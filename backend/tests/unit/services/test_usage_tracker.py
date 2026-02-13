"""Unit tests for the filament usage tracker.

Tests both AMS remain% delta tracking (Path 1) and 3MF per-filament
fallback tracking (Path 2) for non-BL spools.
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


def _make_printer_state(ams_data, progress=0):
    """Create a mock printer state with AMS data."""
    state = MagicMock()
    state.raw_data = {"ams": ams_data}
    state.progress = progress
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
        # First execute → archive, second → assignment, third → spool
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=archive)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=assignment)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        pm = _make_printer_manager()
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
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=archive)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=assignment)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        # Print failed at 50% progress → 50g consumed from 100g estimate
        pm = _make_printer_manager(_make_printer_state([], progress=50))
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
    async def test_skips_bl_spools(self):
        """BL spools (with tag_uid) are NOT tracked via 3MF — they use AMS remain%."""
        spool = _make_spool(tag_uid="ABCD1234", tray_uuid="A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4")
        assignment = _make_assignment()
        archive = MagicMock()
        archive.file_path = "archives/test.3mf"

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=archive)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=assignment)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        pm = _make_printer_manager()
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

        assert results == []

    @pytest.mark.asyncio
    async def test_skips_already_handled_trays(self):
        """Trays handled by AMS remain% delta are not double-tracked via 3MF."""
        archive = MagicMock()
        archive.file_path = "archives/test.3mf"

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=archive)),
            ]
        )

        pm = _make_printer_manager()
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
        """3MF slot_id maps correctly to (ams_id, tray_id)."""
        # slot 5 → global_tray_id 4 → ams_id=1, tray_id=0
        spool = _make_spool(id=9)
        assignment = _make_assignment(spool_id=9, ams_id=1, tray_id=0)
        archive = MagicMock()
        archive.file_path = "archives/test.3mf"

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=archive)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=assignment)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=spool)),
            ]
        )

        pm = _make_printer_manager()
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
