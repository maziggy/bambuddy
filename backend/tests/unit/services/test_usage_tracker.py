"""Unit tests for the filament usage tracker.

Tests 3MF-primary tracking (Path 1) and AMS remain% delta fallback
(Path 2) for spools not covered by 3MF data.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.usage_tracker import (
    PrintSession,
    _active_sessions,
    _track_from_3mf,
    on_print_complete,
    on_print_start,
)


def _make_spool(*, id=1, label_weight=1000, weight_used=0, tag_uid=None, tray_uuid=None):
    """Create a mock Spool object."""
    spool = MagicMock()
    spool.id = id
    spool.label_weight = label_weight
    spool.weight_used = weight_used
    spool.tag_uid = tag_uid
    spool.tray_uuid = tray_uuid
    spool.last_used = None
    return spool


def _make_assignment(*, spool_id=1, printer_id=1, ams_id=0, tray_id=0):
    """Create a mock SpoolAssignment object."""
    assignment = MagicMock()
    assignment.spool_id = spool_id
    assignment.printer_id = printer_id
    assignment.ams_id = ams_id
    assignment.tray_id = tray_id
    return assignment


def _make_printer_state(ams_data, progress=0, layer_num=0, tray_now=255):
    """Create a mock printer state with AMS data."""
    state = MagicMock()
    state.raw_data = {"ams": ams_data}
    state.progress = progress
    state.layer_num = layer_num
    state.tray_now = tray_now
    return state


def _make_printer_manager(state=None):
    """Create a mock printer manager."""
    pm = MagicMock()
    pm.get_status.return_value = state
    return pm


class TestOnPrintStart:
    """Tests for on_print_start — capturing AMS remain%."""

    @pytest.fixture(autouse=True)
    def _clear_sessions(self):
        _active_sessions.clear()
        yield
        _active_sessions.clear()

    @pytest.mark.asyncio
    async def test_creates_session_with_valid_remain(self):
        """Session created with remain% data for trays reporting 0-100."""
        ams_data = [{"id": 0, "tray": [{"id": 0, "remain": 80}]}]
        pm = _make_printer_manager(_make_printer_state(ams_data))

        await on_print_start(1, {"subtask_name": "test_print"}, pm)

        assert 1 in _active_sessions
        session = _active_sessions[1]
        assert session.print_name == "test_print"
        assert session.tray_remain_start == {(0, 0): 80}

    @pytest.mark.asyncio
    async def test_creates_session_even_without_valid_remain(self):
        """Session still created when remain=-1 (for 3MF fallback path)."""
        ams_data = [{"id": 0, "tray": [{"id": 0, "remain": -1}]}]
        pm = _make_printer_manager(_make_printer_state(ams_data))

        await on_print_start(1, {"subtask_name": "test_print"}, pm)

        assert 1 in _active_sessions
        session = _active_sessions[1]
        assert session.tray_remain_start == {}  # Empty, no valid remain

    @pytest.mark.asyncio
    async def test_skips_without_ams_data(self):
        """No session created when no AMS data available."""
        state = MagicMock()
        state.raw_data = {"ams": []}
        pm = _make_printer_manager(state)

        await on_print_start(1, {"subtask_name": "test"}, pm)

        assert 1 not in _active_sessions


class TestOnPrintCompleteAMSDelta:
    """Tests for Path 1: AMS remain% delta tracking."""

    @pytest.fixture(autouse=True)
    def _clear_sessions(self):
        _active_sessions.clear()
        yield
        _active_sessions.clear()

    @pytest.mark.asyncio
    async def test_computes_delta_and_updates_spool(self):
        """Spool weight_used updated by remain% delta * label_weight."""
        # Set up session with start remain = 80%
        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="test",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 80},
        )

        # Current remain = 70% → 10% consumed → 100g on 1000g spool
        ams_data = [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]
        pm = _make_printer_manager(_make_printer_state(ams_data))

        spool = _make_spool(label_weight=1000, weight_used=50)
        assignment = _make_assignment()

        db = AsyncMock()
        # First execute → assignment, second → spool
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=assignment)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        results = await on_print_complete(1, {"status": "completed"}, pm, db)

        assert len(results) == 1
        assert results[0]["weight_used"] == 100.0
        assert results[0]["percent_used"] == 10
        # weight_used should be old (50) + delta (100)
        assert spool.weight_used == 150.0
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_negative_delta(self):
        """No tracking when remain increased (spool refilled)."""
        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="test",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 50},
        )

        # Remain went UP: 50 → 80 (refilled)
        ams_data = [{"id": 0, "tray": [{"id": 0, "remain": 80}]}]
        pm = _make_printer_manager(_make_printer_state(ams_data))
        db = AsyncMock()

        results = await on_print_complete(1, {"status": "completed"}, pm, db)

        assert results == []
        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_session_falls_through_to_3mf(self):
        """When no session exists, AMS delta path skipped (3MF may still run)."""
        pm = _make_printer_manager()
        db = AsyncMock()

        results = await on_print_complete(1, {"status": "completed"}, pm, db)

        assert results == []


class TestTrackFrom3MF:
    """Tests for Path 2: 3MF per-filament fallback tracking."""

    @pytest.mark.asyncio
    async def test_updates_non_bl_spool_from_3mf(self):
        """Non-BL spool gets weight_used from 3MF used_g for completed print."""
        spool = _make_spool(id=5, label_weight=1000, weight_used=100)
        assignment = _make_assignment(spool_id=5)
        archive = MagicMock()
        archive.file_path = "archives/test.3mf"

        db = AsyncMock()
        # archive, queue_item(None), assignment, spool
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=archive)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=assignment)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        pm = _make_printer_manager(_make_printer_state([], tray_now=0))
        filament_usage = [{"slot_id": 1, "used_g": 25.5, "type": "PLA", "color": "#FF0000"}]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=filament_usage),
        ):
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=10,
                status="completed",
                print_name="test_print",
                handled_trays=set(),
                printer_manager=pm,
                db=db,
            )

        assert len(results) == 1
        assert results[0]["spool_id"] == 5
        assert results[0]["weight_used"] == 25.5
        # weight_used = old (100) + 3MF (25.5)
        assert spool.weight_used == 125.5

    @pytest.mark.asyncio
    async def test_scales_by_progress_for_failed_print(self):
        """Failed print scales 3MF estimate by progress percentage."""
        spool = _make_spool(id=1, label_weight=1000, weight_used=0)
        assignment = _make_assignment()
        archive = MagicMock()
        archive.file_path = "archives/test.3mf"

        db = AsyncMock()
        # archive, queue_item(None), assignment, spool
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=archive)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=assignment)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        # Print failed at 50% progress → 50g consumed from 100g estimate
        pm = _make_printer_manager(_make_printer_state([], progress=50, tray_now=0))
        filament_usage = [{"slot_id": 1, "used_g": 100.0, "type": "PLA", "color": ""}]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=filament_usage),
        ):
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=10,
                status="failed",
                print_name="test",
                handled_trays=set(),
                printer_manager=pm,
                db=db,
            )

        assert len(results) == 1
        assert results[0]["weight_used"] == 50.0
        assert spool.weight_used == 50.0

    @pytest.mark.asyncio
    async def test_tracks_bl_spools_via_3mf(self):
        """BL spools (with tag_uid) ARE now tracked via 3MF (unified tracking)."""
        spool = _make_spool(tag_uid="ABCD1234", tray_uuid="A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4")
        assignment = _make_assignment()
        archive = MagicMock()
        archive.file_path = "archives/test.3mf"

        db = AsyncMock()
        # archive, queue_item(None), assignment, spool
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=archive)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=assignment)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        pm = _make_printer_manager(_make_printer_state([], tray_now=0))
        filament_usage = [{"slot_id": 1, "used_g": 50.0, "type": "PLA", "color": ""}]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=filament_usage),
        ):
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=10,
                status="completed",
                print_name="test",
                handled_trays=set(),
                printer_manager=pm,
                db=db,
            )

        assert len(results) == 1
        assert results[0]["spool_id"] == 1
        assert results[0]["weight_used"] == 50.0

    @pytest.mark.asyncio
    async def test_skips_already_handled_trays(self):
        """Trays handled by AMS remain% delta are not double-tracked via 3MF."""
        archive = MagicMock()
        archive.file_path = "archives/test.3mf"

        db = AsyncMock()
        # archive, queue_item(None)
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=archive)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            ]
        )

        pm = _make_printer_manager(_make_printer_state([], tray_now=0))
        filament_usage = [{"slot_id": 1, "used_g": 50.0, "type": "PLA", "color": ""}]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=filament_usage),
        ):
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=10,
                status="completed",
                print_name="test",
                handled_trays={(0, 0)},  # slot_id=1 → ams_id=0, tray_id=0
                printer_manager=pm,
                db=db,
            )

        assert results == []

    @pytest.mark.asyncio
    async def test_slot_to_tray_mapping(self):
        """3MF slot_id maps correctly to (ams_id, tray_id) via tray_now."""
        # tray_now=4 → ams_id=1, tray_id=0 (single filament uses tray_now)
        spool = _make_spool(id=9)
        assignment = _make_assignment(spool_id=9, ams_id=1, tray_id=0)
        archive = MagicMock()
        archive.file_path = "archives/test.3mf"

        db = AsyncMock()
        # archive, queue_item(None), assignment, spool
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=archive)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=assignment)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        pm = _make_printer_manager(_make_printer_state([], tray_now=4))
        filament_usage = [{"slot_id": 5, "used_g": 30.0, "type": "PETG", "color": ""}]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=filament_usage),
        ):
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=10,
                status="completed",
                print_name="test",
                handled_trays=set(),
                printer_manager=pm,
                db=db,
            )

        assert len(results) == 1
        assert results[0]["ams_id"] == 1
        assert results[0]["tray_id"] == 0


class TestSpoolAssignmentSnapshot:
    """Tests for spool assignment snapshotting at print start (#459).

    When a spool runs empty mid-print, on_ams_change deletes the SpoolAssignment.
    The snapshot captured at print start ensures usage is still attributed correctly.
    """

    @pytest.fixture(autouse=True)
    def _clear_sessions(self):
        _active_sessions.clear()
        yield
        _active_sessions.clear()

    @pytest.mark.asyncio
    async def test_on_print_start_snapshots_assignments_with_db(self):
        """on_print_start captures spool assignments when db is provided."""
        ams_data = [{"id": 0, "tray": [{"id": 0, "remain": 80}, {"id": 1, "remain": 60}]}]
        pm = _make_printer_manager(_make_printer_state(ams_data, tray_now=0))

        assignment_0 = _make_assignment(spool_id=10, printer_id=1, ams_id=0, tray_id=0)
        assignment_1 = _make_assignment(spool_id=20, printer_id=1, ams_id=0, tray_id=1)

        db = AsyncMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [assignment_0, assignment_1]
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=result_mock)

        await on_print_start(1, {"subtask_name": "Benchy"}, pm, db=db)

        session = _active_sessions[1]
        assert session.spool_assignments == {(0, 0): 10, (0, 1): 20}

    @pytest.mark.asyncio
    async def test_on_print_start_empty_snapshot_without_db(self):
        """on_print_start creates empty snapshot when no db provided."""
        ams_data = [{"id": 0, "tray": [{"id": 0, "remain": 80}]}]
        pm = _make_printer_manager(_make_printer_state(ams_data, tray_now=0))

        await on_print_start(1, {"subtask_name": "Benchy"}, pm)

        session = _active_sessions[1]
        assert session.spool_assignments == {}

    @pytest.mark.asyncio
    async def test_3mf_uses_snapshot_instead_of_live_query(self):
        """_track_from_3mf uses snapshot spool_id without querying SpoolAssignment."""
        spool = _make_spool(id=42, label_weight=1000)
        archive = MagicMock()
        archive.file_path = "archives/test.3mf"

        # db: archive, queue_item(None), spool — NO assignment query needed
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=archive)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        pm = _make_printer_manager(_make_printer_state([], tray_now=0))
        filament_usage = [{"slot_id": 1, "used_g": 15.0, "type": "PLA", "color": "#FF0000"}]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=filament_usage),
        ):
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=10,
                status="completed",
                print_name="Test",
                handled_trays=set(),
                printer_manager=pm,
                db=db,
                spool_assignments={(0, 0): 42},
            )

        assert len(results) == 1
        assert results[0]["spool_id"] == 42
        assert results[0]["weight_used"] == 15.0

    @pytest.mark.asyncio
    async def test_3mf_falls_back_to_live_query_without_snapshot(self):
        """_track_from_3mf queries SpoolAssignment when no snapshot exists."""
        spool = _make_spool(id=5, label_weight=1000)
        assignment = _make_assignment(spool_id=5)
        archive = MagicMock()
        archive.file_path = "archives/test.3mf"

        # db: archive, queue_item(None), assignment, spool
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=archive)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=assignment)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        pm = _make_printer_manager(_make_printer_state([], tray_now=0))
        filament_usage = [{"slot_id": 1, "used_g": 10.0, "type": "PLA", "color": "#FF0000"}]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=filament_usage),
        ):
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=10,
                status="completed",
                print_name="Test",
                handled_trays=set(),
                printer_manager=pm,
                db=db,
                spool_assignments=None,
            )

        assert len(results) == 1
        assert results[0]["spool_id"] == 5

    @pytest.mark.asyncio
    async def test_ams_delta_uses_snapshot_over_live_query(self):
        """AMS remain% fallback uses snapshot spool_id instead of live query."""
        spool = _make_spool(id=77, label_weight=1000)

        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Benchy",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 80},
            spool_assignments={(0, 0): 77},
        )

        # Current remain = 70% → 10% delta → 100g
        ams_data = [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]
        pm = _make_printer_manager(_make_printer_state(ams_data))

        # db only returns spool (NO assignment query)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        results = await on_print_complete(
            printer_id=1,
            data={"status": "completed"},
            printer_manager=pm,
            db=db,
            archive_id=None,
        )

        assert len(results) == 1
        assert results[0]["spool_id"] == 77
        assert results[0]["weight_used"] == 100.0

    @pytest.mark.asyncio
    async def test_ams_delta_falls_back_to_live_query_without_snapshot(self):
        """AMS remain% fallback queries SpoolAssignment when snapshot is empty."""
        spool = _make_spool(id=33, label_weight=1000)
        assignment = _make_assignment(spool_id=33)

        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Benchy",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 80},
            spool_assignments={},  # Empty snapshot (pre-upgrade session)
        )

        ams_data = [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]
        pm = _make_printer_manager(_make_printer_state(ams_data))

        # db returns assignment then spool
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=assignment)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        results = await on_print_complete(
            printer_id=1,
            data={"status": "completed"},
            printer_manager=pm,
            db=db,
            archive_id=None,
        )

        assert len(results) == 1
        assert results[0]["spool_id"] == 33

    @pytest.mark.asyncio
    async def test_snapshot_survives_mid_print_unlink(self):
        """Core bug scenario: snapshot provides spool_id after mid-print unlink.

        Simulates the #459 scenario: spool runs empty mid-print, on_ams_change
        deletes the SpoolAssignment, but the snapshot from print start still
        has the spool_id so usage is correctly attributed at print completion.
        """
        spool = _make_spool(id=8, label_weight=1000, weight_used=50)
        archive = MagicMock()
        archive.file_path = "archives/big_print.3mf"

        # Session was created at print start WITH snapshot
        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Big Print",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 90},
            spool_assignments={(0, 0): 8},  # Snapshot from print start
        )

        pm = _make_printer_manager(
            _make_printer_state(
                [{"id": 0, "tray": [{"id": 0, "remain": 75}]}],
                tray_now=0,
            )
        )

        filament_usage = [{"slot_id": 1, "used_g": 14.2, "type": "PLA", "color": "#FF0000"}]

        # db: archive, queue_item(None), spool
        # NOTE: No assignment in db — it was deleted by on_ams_change mid-print!
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=archive)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=filament_usage),
        ):
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await on_print_complete(
                printer_id=1,
                data={"status": "completed"},
                printer_manager=pm,
                db=db,
                archive_id=100,
            )

        # Usage should be tracked despite assignment being deleted mid-print
        assert len(results) >= 1
        assert results[0]["spool_id"] == 8
        assert results[0]["weight_used"] == 14.2
        # Spool weight should be updated: 50 + 14.2 = 64.2
        assert spool.weight_used == 64.2
