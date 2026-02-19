"""Unit tests for usage_tracker.py — 3MF-primary filament tracking.

Tests the unified tracking logic: 3MF slicer estimates as primary path,
AMS remain% delta as fallback, per-layer gcode for partial prints,
slot-to-tray mapping resolution, and notification variable formatting.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.usage_tracker import (
    PrintSession,
    _active_sessions,
    _decode_mqtt_mapping,
    _match_slots_by_color,
    _track_from_3mf,
    on_print_complete,
    on_print_start,
)


def _make_spool(spool_id=1, label_weight=1000, weight_used=0, tag_uid=None, tray_uuid=None):
    """Create a mock Spool object."""
    spool = MagicMock()
    spool.id = spool_id
    spool.label_weight = label_weight
    spool.weight_used = weight_used
    spool.tag_uid = tag_uid
    spool.tray_uuid = tray_uuid
    spool.last_used = None
    return spool


def _make_assignment(spool_id=1, printer_id=1, ams_id=0, tray_id=0):
    """Create a mock SpoolAssignment object."""
    assignment = MagicMock()
    assignment.spool_id = spool_id
    assignment.printer_id = printer_id
    assignment.ams_id = ams_id
    assignment.tray_id = tray_id
    return assignment


def _make_archive(archive_id=1, file_path="archives/1/test.3mf", extra_data=None):
    """Create a mock PrintArchive object."""
    archive = MagicMock()
    archive.id = archive_id
    archive.file_path = file_path
    archive.extra_data = extra_data
    return archive


def _make_queue_item(ams_mapping=None, status="printing"):
    """Create a mock PrintQueueItem object."""
    item = MagicMock()
    item.ams_mapping = ams_mapping
    item.status = status
    return item


def _mock_db_execute(*return_values):
    """Create a mock db with execute() that returns values in sequence."""
    db = AsyncMock()
    results = []
    for val in return_values:
        result = MagicMock()
        result.scalar_one_or_none.return_value = val
        results.append(result)
    db.execute = AsyncMock(side_effect=results)
    return db


def _mock_db_sequential(responses):
    """Create mock db that returns responses in order."""
    db = AsyncMock()
    call_count = [0]

    async def mock_execute(*args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        result = MagicMock()
        if idx < len(responses):
            result.scalar_one_or_none.return_value = responses[idx]
        else:
            result.scalar_one_or_none.return_value = None
        return result

    db.execute = mock_execute
    return db


class TestOnPrintStart:
    """Tests for on_print_start()."""

    @pytest.fixture(autouse=True)
    def _clear_sessions(self):
        _active_sessions.clear()
        yield
        _active_sessions.clear()

    @pytest.mark.asyncio
    async def test_captures_remain_data(self):
        """Captures AMS remain% at print start."""
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 80}, {"id": 1, "remain": 50}]}]},
            tray_now=5,
        )

        await on_print_start(1, {"subtask_name": "Benchy"}, printer_manager)

        assert 1 in _active_sessions
        session = _active_sessions[1]
        assert session.print_name == "Benchy"
        assert session.tray_remain_start == {(0, 0): 80, (0, 1): 50}

    @pytest.mark.asyncio
    async def test_captures_tray_now_at_start(self):
        """Captures tray_now at print start for later use in usage tracking."""
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 80}]}]},
            tray_now=9,
        )

        await on_print_start(1, {"subtask_name": "Test"}, printer_manager)

        assert _active_sessions[1].tray_now_at_start == 9

    @pytest.mark.asyncio
    async def test_tray_now_at_start_255_when_unloaded(self):
        """Captures tray_now=255 when printer has no filament loaded at start."""
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 80}]}]},
            tray_now=255,
        )

        await on_print_start(1, {"subtask_name": "Test"}, printer_manager)

        assert _active_sessions[1].tray_now_at_start == 255

    @pytest.mark.asyncio
    async def test_creates_session_without_remain(self):
        """Creates session even without valid remain data (for 3MF tracking)."""
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": -1}]}]},
            tray_now=255,
        )

        await on_print_start(1, {"subtask_name": "Test"}, printer_manager)

        assert 1 in _active_sessions
        assert _active_sessions[1].tray_remain_start == {}


class TestOnPrintComplete:
    """Tests for on_print_complete() — path ordering and interaction."""

    @pytest.fixture(autouse=True)
    def _clear_sessions(self):
        _active_sessions.clear()
        yield
        _active_sessions.clear()

    @pytest.mark.asyncio
    async def test_bl_spool_uses_3mf(self):
        """BL spool (with tag_uid) is tracked via 3MF, not just AMS delta."""
        spool = _make_spool(spool_id=1, tag_uid="AABB1122", label_weight=1000)
        assignment = _make_assignment(spool_id=1, printer_id=1, ams_id=0, tray_id=0)
        archive = _make_archive(archive_id=10)

        # Setup: session with AMS remain data
        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Benchy",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 80},
        )

        # Mock printer state: tray_now=0 (AMS0-T0), single filament
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]},
            progress=100,
            layer_num=50,
            tray_now=0,
        )

        # db returns: archive, queue_item(None), assignment, spool
        db = _mock_db_sequential([archive, None, assignment, spool])

        filament_usage = [{"slot_id": 1, "used_g": 15.0, "type": "PLA", "color": "#FF0000"}]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await on_print_complete(
                printer_id=1,
                data={"status": "completed"},
                printer_manager=printer_manager,
                db=db,
                archive_id=10,
            )

        # 3MF path should handle it (BL guard removed)
        assert len(results) >= 1
        assert results[0]["spool_id"] == 1
        assert results[0]["weight_used"] == 15.0

    @pytest.mark.asyncio
    async def test_ams_delta_fallback_no_archive(self):
        """AMS delta tracks consumption when archive_id is None."""
        spool = _make_spool(spool_id=2, label_weight=1000)
        assignment = _make_assignment(spool_id=2)

        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Test",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 80},
        )

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]},
            tray_now=0,
            last_loaded_tray=-1,
        )

        # db returns assignment then spool
        db = _mock_db_sequential([assignment, spool])

        results = await on_print_complete(
            printer_id=1,
            data={"status": "completed"},
            printer_manager=printer_manager,
            db=db,
            archive_id=None,
        )

        assert len(results) == 1
        assert results[0]["spool_id"] == 2
        # 10% of 1000g = 100g
        assert results[0]["weight_used"] == 100.0
        assert results[0]["percent_used"] == 10

    @pytest.mark.asyncio
    async def test_no_double_tracking(self):
        """When 3MF handles a tray, AMS delta skips it."""
        spool = _make_spool(spool_id=1, label_weight=1000)
        assignment = _make_assignment(spool_id=1)
        archive = _make_archive(archive_id=10)

        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Benchy",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 80},
        )

        # tray_now=0 matches the single filament slot
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]},
            progress=100,
            layer_num=50,
            tray_now=0,
        )

        # db returns: archive, queue_item(None), assignment, spool
        db = _mock_db_sequential([archive, None, assignment, spool])

        filament_usage = [{"slot_id": 1, "used_g": 15.0, "type": "PLA", "color": "#FF0000"}]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await on_print_complete(
                printer_id=1,
                data={"status": "completed"},
                printer_manager=printer_manager,
                db=db,
                archive_id=10,
            )

        # Only 1 result (3MF), NOT 2 (3MF + AMS delta)
        assert len(results) == 1
        assert results[0]["weight_used"] == 15.0


class TestTrackFrom3mf:
    """Tests for _track_from_3mf() — per-layer, linear scaling, and slot mapping."""

    @pytest.mark.asyncio
    async def test_linear_fallback_for_partial_print(self):
        """Falls back to linear scaling when gcode layer data unavailable."""
        spool = _make_spool(spool_id=1, label_weight=1000)
        assignment = _make_assignment(spool_id=1)
        archive = _make_archive(archive_id=10)

        # db: archive, queue_item(None), assignment, spool
        db = _mock_db_sequential([archive, None, assignment, spool])

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            progress=50,
            layer_num=25,
            tray_now=0,
        )

        filament_usage = [{"slot_id": 1, "used_g": 20.0, "type": "PLA", "color": ""}]
        handled_trays: set[tuple[int, int]] = set()

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
            patch(
                "backend.app.utils.threemf_tools.extract_layer_filament_usage_from_3mf",
                return_value=None,  # No layer data available
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=10,
                status="failed",
                print_name="Benchy",
                handled_trays=handled_trays,
                printer_manager=printer_manager,
                db=db,
            )

        assert len(results) == 1
        # 50% of 20g = 10g
        assert results[0]["weight_used"] == 10.0
        # Tray should be marked as handled
        assert (0, 0) in handled_trays

    @pytest.mark.asyncio
    async def test_per_layer_partial_print(self):
        """Failed print at layer N uses gcode cumulative data."""
        spool = _make_spool(spool_id=1, label_weight=1000)
        assignment = _make_assignment(spool_id=1)
        archive = _make_archive(archive_id=10)

        # db: archive, queue_item(None), assignment, spool
        db = _mock_db_sequential([archive, None, assignment, spool])

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            progress=50,
            layer_num=25,
            tray_now=0,
        )

        filament_usage = [{"slot_id": 1, "used_g": 20.0, "type": "PLA", "color": ""}]
        # Per-layer data: at layer 25, filament 0 used 5000mm
        layer_data = {10: {0: 2000.0}, 25: {0: 5000.0}, 50: {0: 10000.0}}
        filament_props = {1: {"density": 1.24, "diameter": 1.75}}
        handled_trays: set[tuple[int, int]] = set()

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
            patch(
                "backend.app.utils.threemf_tools.extract_layer_filament_usage_from_3mf",
                return_value=layer_data,
            ),
            patch(
                "backend.app.utils.threemf_tools.get_cumulative_usage_at_layer",
                return_value={0: 5000.0},
            ),
            patch(
                "backend.app.utils.threemf_tools.extract_filament_properties_from_3mf",
                return_value=filament_props,
            ),
            patch(
                "backend.app.utils.threemf_tools.mm_to_grams",
                return_value=12.0,  # 5000mm at 1.75mm/1.24g/cm3
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=10,
                status="failed",
                print_name="Benchy",
                handled_trays=handled_trays,
                printer_manager=printer_manager,
                db=db,
            )

        assert len(results) == 1
        # Should use per-layer grams (12.0g), not linear scale (10.0g)
        assert results[0]["weight_used"] == 12.0

    @pytest.mark.asyncio
    async def test_completed_print_uses_full_weight(self):
        """Completed print uses full 3MF weight (scale=1.0)."""
        spool = _make_spool(spool_id=1, label_weight=1000)
        assignment = _make_assignment(spool_id=1)
        archive = _make_archive(archive_id=10)

        # db: archive, queue_item(None), assignment, spool
        db = _mock_db_sequential([archive, None, assignment, spool])

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            progress=100,
            layer_num=50,
            tray_now=0,
        )

        filament_usage = [{"slot_id": 1, "used_g": 20.0, "type": "PLA", "color": ""}]
        handled_trays: set[tuple[int, int]] = set()

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=10,
                status="completed",
                print_name="Benchy",
                handled_trays=handled_trays,
                printer_manager=printer_manager,
                db=db,
            )

        assert len(results) == 1
        assert results[0]["weight_used"] == 20.0

    @pytest.mark.asyncio
    async def test_tray_now_override_for_single_filament(self):
        """Single-filament non-queue print uses tray_now instead of slot_id mapping."""
        # Spool 2 is at AMS1-T3 (global_tray_id=7)
        spool = _make_spool(spool_id=2, label_weight=1000)
        assignment = _make_assignment(spool_id=2, ams_id=1, tray_id=3)
        archive = _make_archive(archive_id=10)

        # db: archive, queue_item(None), assignment, spool
        db = _mock_db_sequential([archive, None, assignment, spool])

        # tray_now=7 = (ams_id=1, tray_id=3), the ACTUAL tray used
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            progress=100,
            layer_num=50,
            tray_now=7,
        )

        # 3MF has slot_id=12 (would default-map to ams_id=2, tray_id=3 — WRONG)
        filament_usage = [{"slot_id": 12, "used_g": 10.6, "type": "PLA", "color": "#FF0000"}]
        handled_trays: set[tuple[int, int]] = set()

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=10,
                status="completed",
                print_name="Test",
                handled_trays=handled_trays,
                printer_manager=printer_manager,
                db=db,
            )

        assert len(results) == 1
        assert results[0]["spool_id"] == 2
        assert results[0]["ams_id"] == 1
        assert results[0]["tray_id"] == 3
        assert results[0]["weight_used"] == 10.6
        assert (1, 3) in handled_trays

    @pytest.mark.asyncio
    async def test_queue_ams_mapping_overrides_default(self):
        """Queue item ams_mapping overrides default slot_id mapping."""
        # Spool at AMS1-T3 (global_tray_id=7)
        spool = _make_spool(spool_id=5, label_weight=1000)
        assignment = _make_assignment(spool_id=5, ams_id=1, tray_id=3)
        archive = _make_archive(archive_id=20)
        # Queue item maps slot 1 → global tray 7 (ams_id=1, tray_id=3)
        queue_item = _make_queue_item(ams_mapping="[7, -1, -1, -1]")

        # db: archive, queue_item, assignment, spool
        db = _mock_db_sequential([archive, queue_item, assignment, spool])

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            progress=100,
            layer_num=50,
            tray_now=7,
        )

        filament_usage = [{"slot_id": 1, "used_g": 25.0, "type": "PETG", "color": ""}]
        handled_trays: set[tuple[int, int]] = set()

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=20,
                status="completed",
                print_name="Queue Print",
                handled_trays=handled_trays,
                printer_manager=printer_manager,
                db=db,
            )

        assert len(results) == 1
        assert results[0]["spool_id"] == 5
        assert results[0]["ams_id"] == 1
        assert results[0]["tray_id"] == 3
        assert results[0]["weight_used"] == 25.0

    @pytest.mark.asyncio
    async def test_multi_filament_uses_queue_mapping(self):
        """Multi-filament queue prints use ams_mapping for each slot."""
        spool_a = _make_spool(spool_id=1, label_weight=1000)
        spool_b = _make_spool(spool_id=2, label_weight=1000)
        assign_a = _make_assignment(spool_id=1, ams_id=0, tray_id=0)
        assign_b = _make_assignment(spool_id=2, ams_id=1, tray_id=2)
        archive = _make_archive(archive_id=30)
        # slot 1 → tray 0 (AMS0-T0), slot 2 → tray 6 (AMS1-T2)
        queue_item = _make_queue_item(ams_mapping="[0, 6]")

        # db: archive, queue_item, assign_a, spool_a, assign_b, spool_b
        db = _mock_db_sequential([archive, queue_item, assign_a, spool_a, assign_b, spool_b])

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            progress=100,
            layer_num=50,
            tray_now=6,
        )

        filament_usage = [
            {"slot_id": 1, "used_g": 10.0, "type": "PLA", "color": ""},
            {"slot_id": 2, "used_g": 5.0, "type": "PETG", "color": ""},
        ]
        handled_trays: set[tuple[int, int]] = set()

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=30,
                status="completed",
                print_name="Multi",
                handled_trays=handled_trays,
                printer_manager=printer_manager,
                db=db,
            )

        assert len(results) == 2
        assert results[0]["spool_id"] == 1
        assert results[0]["ams_id"] == 0
        assert results[0]["tray_id"] == 0
        assert results[0]["weight_used"] == 10.0
        assert results[1]["spool_id"] == 2
        assert results[1]["ams_id"] == 1
        assert results[1]["tray_id"] == 2
        assert results[1]["weight_used"] == 5.0

    @pytest.mark.asyncio
    async def test_no_tray_now_override_for_multi_filament(self):
        """Multi-filament non-queue prints fall back to default mapping, not tray_now."""
        spool = _make_spool(spool_id=1, label_weight=1000)
        assignment = _make_assignment(spool_id=1, ams_id=0, tray_id=0)
        archive = _make_archive(archive_id=10)

        # db: archive, queue_item(None), assignment, spool (2nd slot has no assignment)
        db = _mock_db_sequential([archive, None, assignment, spool, None])

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            progress=100,
            layer_num=50,
            tray_now=4,  # tray_now won't be used
        )

        # Two filament slots with usage
        filament_usage = [
            {"slot_id": 1, "used_g": 10.0, "type": "PLA", "color": ""},
            {"slot_id": 2, "used_g": 5.0, "type": "PETG", "color": ""},
        ]
        handled_trays: set[tuple[int, int]] = set()

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=10,
                status="completed",
                print_name="Test",
                handled_trays=handled_trays,
                printer_manager=printer_manager,
                db=db,
            )

        # Should use default mapping (slot 1 → tray 0, slot 2 → tray 1)
        assert len(results) == 1  # Only slot 1 has assignment
        assert results[0]["ams_id"] == 0
        assert results[0]["tray_id"] == 0

    @pytest.mark.asyncio
    async def test_stored_ams_mapping_overrides_all(self):
        """Stored ams_mapping from print command takes priority over queue and tray_now."""
        # Spool at AMS2-T1 (global_tray_id=9)
        spool = _make_spool(spool_id=10, label_weight=1000)
        assignment = _make_assignment(spool_id=10, ams_id=2, tray_id=1)
        archive = _make_archive(archive_id=50)

        # db: archive, assignment, spool (no queue lookup when ams_mapping provided)
        db = _mock_db_sequential([archive, assignment, spool])

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            progress=100,
            layer_num=50,
            tray_now=0,  # Different from mapped tray — should be ignored
            last_loaded_tray=0,
        )

        filament_usage = [{"slot_id": 2, "used_g": 1.57, "type": "PLA", "color": "#FFFFFF"}]
        handled_trays: set[tuple[int, int]] = set()

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            # ams_mapping: slot 2 (index 1) -> tray 9 (AMS2-T1)
            results = await _track_from_3mf(
                printer_id=1,
                archive_id=50,
                status="completed",
                print_name="Test",
                handled_trays=handled_trays,
                printer_manager=printer_manager,
                db=db,
                ams_mapping=[-1, 9],
            )

        assert len(results) == 1
        assert results[0]["spool_id"] == 10
        assert results[0]["ams_id"] == 2
        assert results[0]["tray_id"] == 1
        assert results[0]["weight_used"] == 1.6  # rounded

    @pytest.mark.asyncio
    async def test_last_loaded_tray_fallback(self):
        """Falls back to last_loaded_tray when tray_now_at_start and current tray_now are both 255."""
        # Spool at AMS2-T1 (global_tray_id=9)
        spool = _make_spool(spool_id=11, label_weight=1000)
        assignment = _make_assignment(spool_id=11, ams_id=2, tray_id=1)
        archive = _make_archive(archive_id=60)

        # db: archive, queue_item(None), assignment, spool
        db = _mock_db_sequential([archive, None, assignment, spool])

        # H2D scenario: tray_now=255 at completion, but last_loaded_tray=9
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            progress=100,
            layer_num=50,
            tray_now=255,
            last_loaded_tray=9,
        )

        filament_usage = [{"slot_id": 6, "used_g": 1.52, "type": "PLA", "color": "#7CC4D5"}]
        handled_trays: set[tuple[int, int]] = set()

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=60,
                status="completed",
                print_name="Cube",
                handled_trays=handled_trays,
                printer_manager=printer_manager,
                db=db,
                tray_now_at_start=255,  # H2D: 255 at start too
            )

        assert len(results) == 1
        assert results[0]["spool_id"] == 11
        assert results[0]["ams_id"] == 2
        assert results[0]["tray_id"] == 1

    @pytest.mark.asyncio
    async def test_tray_now_at_start_preferred_over_last_loaded(self):
        """tray_now_at_start is used before last_loaded_tray fallback."""
        spool = _make_spool(spool_id=3, label_weight=1000)
        assignment = _make_assignment(spool_id=3, ams_id=1, tray_id=1)
        archive = _make_archive(archive_id=70)

        db = _mock_db_sequential([archive, None, assignment, spool])

        # tray_now_at_start=5 (valid), last_loaded_tray=9 (different) — should use 5
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            progress=100,
            layer_num=50,
            tray_now=255,
            last_loaded_tray=9,
        )

        filament_usage = [{"slot_id": 1, "used_g": 5.0, "type": "PLA", "color": ""}]
        handled_trays: set[tuple[int, int]] = set()

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=70,
                status="completed",
                print_name="Test",
                handled_trays=handled_trays,
                printer_manager=printer_manager,
                db=db,
                tray_now_at_start=5,  # AMS1-T1
            )

        assert len(results) == 1
        assert results[0]["ams_id"] == 1
        assert results[0]["tray_id"] == 1


class TestDecodeMqttMapping:
    """Tests for _decode_mqtt_mapping() — snow-encoded MQTT mapping to global tray IDs."""

    def test_none_input(self):
        assert _decode_mqtt_mapping(None) is None

    def test_empty_list(self):
        assert _decode_mqtt_mapping([]) is None

    def test_all_unmapped(self):
        """All 65535 values → None (no valid mappings)."""
        assert _decode_mqtt_mapping([65535, 65535, 65535]) is None

    def test_single_ams_slots(self):
        """AMS 0 slots: snow values 0-3 → global tray IDs 0-3."""
        assert _decode_mqtt_mapping([0, 1, 2, 3]) == [0, 1, 2, 3]

    def test_multi_ams_slots(self):
        """AMS 1 (hw_id=1): snow 256=AMS1-T0, 257=AMS1-T1 → global 4, 5."""
        assert _decode_mqtt_mapping([256, 257]) == [4, 5]

    def test_ams_ht_slot(self):
        """AMS-HT (hw_id=128): snow 32768 → global 128."""
        assert _decode_mqtt_mapping([32768]) == [128]

    def test_external_spool(self):
        """External spool: ams_hw_id=254, slot=0 → global 254."""
        # snow = 254 * 256 + 0 = 65024
        assert _decode_mqtt_mapping([65024]) == [254]

    def test_mixed_with_unmapped(self):
        """Mix of valid and unmapped (65535) values."""
        result = _decode_mqtt_mapping([1, 65535, 0])
        assert result == [1, -1, 0]

    def test_h2c_real_mapping(self):
        """Real H2C mapping from MQTT logs: [1, 0, 65535*4, 32768]."""
        mapping = [1, 0, 65535, 65535, 65535, 65535, 32768]
        result = _decode_mqtt_mapping(mapping)
        assert result == [1, 0, -1, -1, -1, -1, 128]

    def test_non_int_values_treated_as_unmapped(self):
        """Non-integer values in the mapping are treated as unmapped."""
        assert _decode_mqtt_mapping(["foo", 0]) == [-1, 0]


class TestMatchSlotsByColor:
    """Tests for _match_slots_by_color() — color-based filament slot to AMS tray matching."""

    def _ams(self, trays):
        """Build AMS data from list of (ams_id, tray_id, color_hex, tray_type) tuples."""
        units: dict[int, list] = {}
        for ams_id, tray_id, color, tray_type in trays:
            units.setdefault(ams_id, []).append({"id": tray_id, "tray_color": color, "tray_type": tray_type})
        return [{"id": aid, "tray": t} for aid, t in units.items()]

    def _usage(self, slots):
        """Build filament_usage from list of (slot_id, color_hex) tuples."""
        return [{"slot_id": sid, "used_g": 10.0, "type": "PLA", "color": color} for sid, color in slots]

    def test_none_inputs(self):
        assert _match_slots_by_color(None, None) is None
        assert _match_slots_by_color([], None) is None
        assert _match_slots_by_color(None, {"ams": []}) is None

    def test_empty_ams(self):
        usage = self._usage([(1, "#FF0000")])
        assert _match_slots_by_color(usage, {"ams": []}) is None

    def test_single_slot_single_tray(self):
        """One 3MF slot matches one AMS tray by color."""
        ams = self._ams([(0, 0, "FF0000FF", "PLA")])
        usage = self._usage([(1, "#FF0000")])
        assert _match_slots_by_color(usage, {"ams": ams}) == [0]

    def test_a1_mini_three_colors(self):
        """A1 Mini: 3 slots match 3 distinct AMS trays."""
        ams = self._ams(
            [
                (0, 0, "FF0000FF", "PLA"),  # Red
                (0, 1, "00FF00FF", "PLA"),  # Green
                (0, 2, "0000FFFF", "PLA"),  # Blue
            ]
        )
        usage = self._usage([(1, "#FF0000"), (2, "#00FF00"), (3, "#0000FF")])
        assert _match_slots_by_color(usage, {"ams": ams}) == [0, 1, 2]

    def test_dual_ams_p2s_like(self):
        """P2S with dual AMS: slots from second AMS unit."""
        ams = self._ams(
            [
                (0, 0, "AAAAAAFF", "PLA"),
                (0, 1, "BBBBBBFF", "PLA"),
                (1, 0, "CC0000FF", "PETG"),  # global_id=4
                (1, 1, "00CC00FF", "PETG"),  # global_id=5
            ]
        )
        usage = self._usage([(1, "#CC0000"), (2, "#00CC00")])
        assert _match_slots_by_color(usage, {"ams": ams}) == [4, 5]

    def test_ams_ht_global_id(self):
        """AMS-HT (ams_id >= 128) uses raw ams_id as global tray ID."""
        ams = self._ams(
            [
                (0, 0, "FF0000FF", "PLA"),
                (128, 0, "0000FFFF", "PLA"),  # AMS-HT → global_id=128
            ]
        )
        usage = self._usage([(1, "#FF0000"), (2, "#0000FF")])
        assert _match_slots_by_color(usage, {"ams": ams}) == [0, 128]

    def test_ambiguous_same_color_returns_none(self):
        """Two trays with the same color → ambiguous → None."""
        ams = self._ams(
            [
                (0, 0, "FF0000FF", "PLA"),
                (0, 1, "FF0000FF", "PLA"),  # Same red
            ]
        )
        usage = self._usage([(1, "#FF0000")])
        assert _match_slots_by_color(usage, {"ams": ams}) is None

    def test_no_matching_color_returns_none(self):
        """3MF slot color not found in any AMS tray → None."""
        ams = self._ams([(0, 0, "00FF00FF", "PLA")])
        usage = self._usage([(1, "#FF0000")])  # Red, but AMS has green
        assert _match_slots_by_color(usage, {"ams": ams}) is None

    def test_color_normalization_strips_alpha(self):
        """AMS colors (RRGGBBAA) and 3MF colors (#RRGGBB) match after normalization."""
        ams = self._ams([(0, 0, "AABBCC80", "PLA")])  # 8-char with alpha
        usage = self._usage([(1, "#AABBCC")])  # 6-char with #
        assert _match_slots_by_color(usage, {"ams": ams}) == [0]

    def test_case_insensitive(self):
        """Color matching is case-insensitive."""
        ams = self._ams([(0, 0, "aaBBccFF", "PLA")])
        usage = self._usage([(1, "#AAbbCC")])
        assert _match_slots_by_color(usage, {"ams": ams}) == [0]

    def test_empty_tray_color_skipped(self):
        """Trays with empty color are skipped (not matched)."""
        ams = self._ams(
            [
                (0, 0, "", "PLA"),
                (0, 1, "FF0000FF", "PLA"),
            ]
        )
        usage = self._usage([(1, "#FF0000")])
        assert _match_slots_by_color(usage, {"ams": ams}) == [1]

    def test_empty_tray_type_skipped(self):
        """Trays with empty tray_type are skipped (unloaded slot)."""
        ams = self._ams(
            [
                (0, 0, "FF0000FF", ""),  # Empty slot
                (0, 1, "FF0000FF", "PLA"),  # Loaded slot
            ]
        )
        usage = self._usage([(1, "#FF0000")])
        assert _match_slots_by_color(usage, {"ams": ams}) == [1]

    def test_short_slot_color_returns_none(self):
        """3MF slot with color < 6 chars → can't match → None."""
        ams = self._ams([(0, 0, "FF0000FF", "PLA")])
        usage = [{"slot_id": 1, "used_g": 10.0, "type": "PLA", "color": "#FFF"}]
        assert _match_slots_by_color(usage, {"ams": ams}) is None

    def test_slot_id_zero_skipped(self):
        """Slots with slot_id=0 are skipped."""
        ams = self._ams([(0, 0, "FF0000FF", "PLA")])
        usage = [{"slot_id": 0, "used_g": 10.0, "type": "PLA", "color": "#FF0000"}]
        assert _match_slots_by_color(usage, {"ams": ams}) is None

    def test_ams_data_as_list(self):
        """Handles ams_raw as a plain list (some printer models)."""
        ams_list = [{"id": 0, "tray": [{"id": 0, "tray_color": "FF0000FF", "tray_type": "PLA"}]}]
        usage = self._usage([(1, "#FF0000")])
        assert _match_slots_by_color(usage, ams_list) == [0]

    def test_same_color_two_trays_disambiguated_by_usage(self):
        """Two trays same color, two slots same color → unique assignment via used_trays tracking."""
        ams = self._ams(
            [
                (0, 0, "FF0000FF", "PLA"),
                (0, 1, "FF0000FF", "PLA"),
            ]
        )
        # Two slots both wanting red — first gets tray 0, second gets tray 1? No.
        # When first slot takes the only available, second has 1 left → should work
        usage = self._usage([(1, "#FF0000"), (2, "#FF0000")])
        # First slot: candidates=[0,1], available=[0,1], len!=1 → None
        assert _match_slots_by_color(usage, {"ams": ams}) is None

    def test_dict_wrapper_with_ams_key(self):
        """Standard dict format with 'ams' key."""
        ams_data = {"ams": [{"id": 0, "tray": [{"id": 0, "tray_color": "00FF00FF", "tray_type": "PLA"}]}]}
        usage = self._usage([(1, "#00FF00")])
        assert _match_slots_by_color(usage, ams_data) == [0]


class TestMqttMappingIntegration:
    """Integration tests: MQTT mapping field used in _track_from_3mf."""

    @pytest.mark.asyncio
    async def test_h2c_multi_filament_uses_mqtt_mapping(self):
        """H2C: 3 filaments resolved via MQTT mapping field (no ams_mapping, no queue)."""
        # AMS0-T1 (White PLA), AMS0-T0 (Black PLA), AMS128-T0 (Red PLA)
        spool_white = _make_spool(spool_id=1, label_weight=1000)
        spool_black = _make_spool(spool_id=2, label_weight=1000)
        spool_red = _make_spool(spool_id=3, label_weight=1000)
        assign_white = _make_assignment(spool_id=1, ams_id=0, tray_id=1)
        assign_black = _make_assignment(spool_id=2, ams_id=0, tray_id=0)
        assign_red = _make_assignment(spool_id=3, ams_id=128, tray_id=0)
        archive = _make_archive(archive_id=12)

        # db: archive, then 3 pairs of (assignment, spool)
        # No queue lookup because MQTT mapping is found first
        db = _mock_db_sequential(
            [
                archive,
                assign_white,
                spool_white,
                assign_black,
                spool_black,
                assign_red,
                spool_red,
            ]
        )

        # MQTT mapping: slot0→AMS0-T1(1), slot1→AMS0-T0(0), slots2-5→unmapped, slot6→AMS128-T0(32768)
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"mapping": [1, 0, 65535, 65535, 65535, 65535, 32768]},
            progress=100,
            layer_num=50,
            tray_now=255,
        )

        # 3MF slots 1, 2, 7 (1-based) → indices 0, 1, 6 in mapping
        filament_usage = [
            {"slot_id": 1, "used_g": 21.16, "type": "PLA", "color": "#FFFFFF"},
            {"slot_id": 2, "used_g": 24.22, "type": "PLA", "color": "#000000"},
            {"slot_id": 7, "used_g": 18.47, "type": "PLA", "color": "#F72323"},
        ]
        handled_trays: set[tuple[int, int]] = set()

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=12,
                status="completed",
                print_name="Cube + Cube + Cube",
                handled_trays=handled_trays,
                printer_manager=printer_manager,
                db=db,
            )

        assert len(results) == 3

        # slot_id=1 → mapping[0]=1 → AMS0-T1 (White PLA)
        assert results[0]["spool_id"] == 1
        assert results[0]["ams_id"] == 0
        assert results[0]["tray_id"] == 1
        assert results[0]["weight_used"] == 21.2

        # slot_id=2 → mapping[1]=0 → AMS0-T0 (Black PLA)
        assert results[1]["spool_id"] == 2
        assert results[1]["ams_id"] == 0
        assert results[1]["tray_id"] == 0
        assert results[1]["weight_used"] == 24.2

        # slot_id=7 → mapping[6]=32768 → AMS128-T0 (Red PLA)
        assert results[2]["spool_id"] == 3
        assert results[2]["ams_id"] == 128
        assert results[2]["tray_id"] == 0
        assert results[2]["weight_used"] == 18.5

    @pytest.mark.asyncio
    async def test_print_cmd_mapping_takes_priority_over_mqtt(self):
        """ams_mapping from print command is used even when MQTT mapping exists."""
        spool = _make_spool(spool_id=1, label_weight=1000)
        assignment = _make_assignment(spool_id=1, ams_id=0, tray_id=2)
        archive = _make_archive(archive_id=10)

        # db: archive, assignment, spool (no queue lookup when ams_mapping provided)
        db = _mock_db_sequential([archive, assignment, spool])

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"mapping": [0, 65535]},  # MQTT says slot 0 → AMS0-T0
            progress=100,
            layer_num=50,
            tray_now=255,
        )

        filament_usage = [{"slot_id": 1, "used_g": 10.0, "type": "PLA", "color": ""}]
        handled_trays: set[tuple[int, int]] = set()

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=10,
                status="completed",
                print_name="Test",
                handled_trays=handled_trays,
                printer_manager=printer_manager,
                db=db,
                ams_mapping=[2],  # Print cmd says slot 0 → AMS0-T2 (overrides MQTT)
            )

        assert len(results) == 1
        assert results[0]["ams_id"] == 0
        assert results[0]["tray_id"] == 2  # From print_cmd mapping, not MQTT


class TestNotificationVariables:
    """Tests for filament_details formatting in notifications."""

    def test_filament_details_single_slot(self):
        """Single slot produces 'PLA: 15.2g' format."""
        slots = [{"type": "PLA", "used_g": 15.2, "slot_id": 1, "color": "#FF0000"}]
        parts = []
        for slot in slots:
            ftype = slot.get("type", "Unknown") or "Unknown"
            used = slot.get("used_g", 0)
            parts.append(f"{ftype}: {used:.1f}g")
        result = " | ".join(parts)
        assert result == "PLA: 15.2g"

    def test_filament_details_multi_slot(self):
        """Multiple slots produce 'PLA: 10.0g | PETG: 5.0g' format."""
        slots = [
            {"type": "PLA", "used_g": 10.0, "slot_id": 1, "color": ""},
            {"type": "PETG", "used_g": 5.0, "slot_id": 2, "color": ""},
        ]
        parts = []
        for slot in slots:
            ftype = slot.get("type", "Unknown") or "Unknown"
            used = slot.get("used_g", 0)
            parts.append(f"{ftype}: {used:.1f}g")
        result = " | ".join(parts)
        assert result == "PLA: 10.0g | PETG: 5.0g"

    def test_filament_details_empty_type(self):
        """Empty type defaults to 'Unknown'."""
        slots = [{"type": "", "used_g": 5.0, "slot_id": 1, "color": ""}]
        parts = []
        for slot in slots:
            ftype = slot.get("type", "Unknown") or "Unknown"
            used = slot.get("used_g", 0)
            parts.append(f"{ftype}: {used:.1f}g")
        result = " | ".join(parts)
        assert result == "Unknown: 5.0g"

    def test_filament_grams_scaled_for_partial(self):
        """filament_grams is scaled by progress for partial prints."""
        filament_used_grams = 20.0
        progress = 50
        scale = max(0.0, min(progress / 100.0, 1.0))
        scaled = round(filament_used_grams * scale, 1)
        assert scaled == 10.0

    def test_filament_grams_zero_progress(self):
        """Progress=0 at cancellation gives 0.0g."""
        filament_used_grams = 20.0
        progress = 0
        scale = max(0.0, min(progress / 100.0, 1.0))
        scaled = round(filament_used_grams * scale, 1)
        assert scaled == 0.0

    def test_slot_scaling_for_partial(self):
        """Per-slot usage is scaled linearly for partial prints."""
        slots = [
            {"type": "PLA", "used_g": 20.0, "slot_id": 1, "color": ""},
            {"type": "PETG", "used_g": 10.0, "slot_id": 2, "color": ""},
        ]
        progress = 30
        scale = max(0.0, min(progress / 100.0, 1.0))
        scaled_slots = [{**s, "used_g": round(s["used_g"] * scale, 1)} for s in slots]
        assert scaled_slots[0]["used_g"] == 6.0
        assert scaled_slots[1]["used_g"] == 3.0
