"""Unit tests for cost tracking in usage_tracker.py.

Tests cost calculation scenarios:
- Spool-specific cost_per_kg
- Default fallback cost from settings
- Spools without cost (None)
- Completed prints
- Failed/partial prints
- Cost aggregation to archives
"""

import os
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.usage_tracker import (
    PrintSession,
    _active_sessions,
    _track_from_3mf,
    on_print_complete,
)


def _make_spool(spool_id=1, label_weight=1000, weight_used=0, cost_per_kg=None):
    """Create a mock Spool object with cost fields."""
    spool = MagicMock()
    spool.id = spool_id
    spool.label_weight = label_weight
    spool.weight_used = weight_used
    spool.cost_per_kg = cost_per_kg
    spool.last_used = None
    spool.material = "PLA"
    return spool


def _make_assignment(spool_id=1, printer_id=1, ams_id=0, tray_id=0):
    """Create a mock SpoolAssignment object."""
    assignment = MagicMock()
    assignment.spool_id = spool_id
    assignment.printer_id = printer_id
    assignment.ams_id = ams_id
    assignment.tray_id = tray_id
    return assignment


def _make_archive(archive_id=1, file_path=None):
    """Create a mock PrintArchive object with a temp file, and register cleanup."""
    if file_path is None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".3mf", prefix="test_print_") as tmp:
            file_path = tmp.name
        # Register cleanup for this file after the test
        import pytest

        frame = None
        try:
            raise Exception
        except Exception:
            import sys

            frame = sys._getframe(1)
        request = frame.f_locals.get("request")
        if request is not None:

            def cleanup():
                try:
                    os.remove(file_path)
                except Exception:
                    pass

            request.addfinalizer(cleanup)
    archive = MagicMock()
    archive.id = archive_id
    archive.file_path = file_path
    return archive


@pytest.fixture(autouse=True)
def cleanup_temp_archives():
    yield
    # Cleanup any temp .3mf files created by _make_archive
    import glob

    for f in glob.glob("test_print_*.3mf"):
        try:
            os.remove(f)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def cleanup_test_print_gcode():
    yield
    import os

    path = "archives/test/test_print.gcode.3mf"
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


@pytest.fixture
def archive_factory_temp():
    import tempfile

    def _factory(*args, **kwargs):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".3mf", prefix="test_print_", dir="archives/test") as tmp:
            kwargs["file_path"] = tmp.name
        return kwargs["file_path"]

    yield _factory
    # Cleanup
    import glob
    import os

    for f in glob.glob("archives/test/test_print_*.3mf"):
        try:
            os.remove(f)
        except Exception:
            pass


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


class TestCostCalculation:
    """Tests for cost calculation in usage tracking."""

    @pytest.fixture(autouse=True)
    def _clear_sessions(self):
        _active_sessions.clear()
        yield
        _active_sessions.clear()

    @pytest.mark.asyncio
    async def test_cost_with_spool_specific_cost_per_kg(self):
        """Cost is calculated using spool-specific cost_per_kg when available."""
        # Spool with cost_per_kg = 25.00 USD/kg
        spool = _make_spool(spool_id=1, label_weight=1000, cost_per_kg=25.0)
        assignment = _make_assignment(spool_id=1)
        archive = _make_archive(archive_id=10)

        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Test",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 80},
            tray_now_at_start=0,
        )

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]},
            progress=100,
            layer_num=50,
            tray_now=0,
        )

        # db returns: archive, queue_item(None), assignment, spool
        db = _mock_db_sequential([archive, None, assignment, spool])

        # 20g used from 3MF
        filament_usage = [{"slot_id": 1, "used_g": 20.0, "type": "PLA", "color": "#FF0000"}]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.api.routes.settings.get_setting", return_value="15.0"),  # default cost
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

        assert len(results) == 1
        assert results[0]["spool_id"] == 1
        assert results[0]["weight_used"] == 20.0
        # Cost = 20g / 1000 * 25.0 = 0.50
        assert results[0]["cost"] == 0.50

    @pytest.mark.asyncio
    async def test_cost_with_default_fallback(self):
        """Cost uses default_filament_cost from settings when spool cost is None."""
        # Spool without cost_per_kg
        spool = _make_spool(spool_id=1, label_weight=1000, cost_per_kg=None)
        assignment = _make_assignment(spool_id=1)
        archive = _make_archive(archive_id=10)

        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Test",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 80},
            tray_now_at_start=0,
        )

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]},
            progress=100,
            layer_num=50,
            tray_now=0,
        )

        # db returns: archive, queue_item(None), assignment, spool
        db = _mock_db_sequential([archive, None, assignment, spool])

        # 30g used from 3MF
        filament_usage = [{"slot_id": 1, "used_g": 30.0, "type": "PLA", "color": "#FF0000"}]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.api.routes.settings.get_setting", return_value="15.0"),  # default: 15.0/kg
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

        assert len(results) == 1
        assert results[0]["spool_id"] == 1
        assert results[0]["weight_used"] == 30.0
        # Cost = 30g / 1000 * 15.0 = 0.45
        assert results[0]["cost"] == 0.45

    @pytest.mark.asyncio
    async def test_cost_zero_when_default_cost_is_zero(self):
        """Cost is None when both spool cost and default cost are 0."""
        # Spool without cost_per_kg
        spool = _make_spool(spool_id=1, label_weight=1000, cost_per_kg=None)
        assignment = _make_assignment(spool_id=1)
        archive = _make_archive(archive_id=10)

        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Test",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 80},
            tray_now_at_start=0,
        )

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]},
            progress=100,
            layer_num=50,
            tray_now=0,
        )

        # db returns: archive, queue_item(None), assignment, spool
        db = _mock_db_sequential([archive, None, assignment, spool])

        filament_usage = [{"slot_id": 1, "used_g": 10.0, "type": "PLA", "color": "#FF0000"}]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.api.routes.settings.get_setting", return_value="0.0"),  # no default cost
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

        assert len(results) == 1
        assert results[0]["cost"] is None

    @pytest.mark.asyncio
    async def test_cost_for_failed_print_uses_actual_usage(self):
        """Failed print at 50% progress calculates cost from actual usage."""
        spool = _make_spool(spool_id=1, label_weight=1000, cost_per_kg=20.0)
        assignment = _make_assignment(spool_id=1)
        archive = _make_archive(archive_id=10)

        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Test",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 80},
            tray_now_at_start=0,
        )

        # Failed at 50% progress
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]},
            progress=50,
            layer_num=25,
            tray_now=0,
        )

        # db returns: archive, queue_item(None), assignment, spool
        db = _mock_db_sequential([archive, None, assignment, spool])

        # 40g total, but only 50% used
        filament_usage = [{"slot_id": 1, "used_g": 40.0, "type": "PLA", "color": "#FF0000"}]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.api.routes.settings.get_setting", return_value="15.0"),
            patch(
                "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
                return_value=filament_usage,
            ),
            patch(
                "backend.app.utils.threemf_tools.extract_layer_filament_usage_from_3mf",
                return_value=None,  # No layer data, use linear scaling
            ),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await on_print_complete(
                printer_id=1,
                data={"status": "failed", "last_progress": 50.0},
                printer_manager=printer_manager,
                db=db,
                archive_id=10,
            )

        assert len(results) == 1
        # 50% of 40g = 20g
        assert results[0]["weight_used"] == 20.0
        # Cost = 20g / 1000 * 20.0 = 0.40
        assert results[0]["cost"] == 0.40

    @pytest.mark.asyncio
    async def test_cost_with_ams_fallback_tracking(self):
        """AMS fallback tracking also calculates cost correctly."""
        spool = _make_spool(spool_id=2, label_weight=1000, cost_per_kg=30.0)
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

        # db returns assignment then spool (no archive, AMS fallback path)
        db = _mock_db_sequential([assignment, spool])

        with patch("backend.app.api.routes.settings.get_setting", return_value="15.0"):
            results = await on_print_complete(
                printer_id=1,
                data={"status": "completed"},
                printer_manager=printer_manager,
                db=db,
                archive_id=None,  # No archive = AMS fallback
            )

        assert len(results) == 1
        assert results[0]["spool_id"] == 2
        # 10% of 1000g = 100g
        assert results[0]["weight_used"] == 100.0
        # Cost = 100g / 1000 * 30.0 = 3.00
        assert results[0]["cost"] == 3.0

    @pytest.mark.asyncio
    async def test_multi_filament_cost_aggregation(self):
        """Multiple spools in one print have their costs tracked separately."""
        spool1 = _make_spool(spool_id=1, label_weight=1000, cost_per_kg=20.0)
        spool2 = _make_spool(spool_id=2, label_weight=1000, cost_per_kg=25.0)
        assignment1 = _make_assignment(spool_id=1, ams_id=0, tray_id=0)
        assignment2 = _make_assignment(spool_id=2, ams_id=0, tray_id=1)
        archive = _make_archive(archive_id=10)

        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Test",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 80, (0, 1): 90},
            tray_now_at_start=0,
        )

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 70}, {"id": 1, "remain": 80}]}]},
            progress=100,
            layer_num=50,
            tray_now=0,
        )

        # Mock slot-to-tray mapping: slot 1 -> tray 0, slot 2 -> tray 1
        ams_mapping = [0, 1]

        # db returns: archive, assignment1, spool1, assignment2, spool2
        # ams_mapping is provided, so no queue item lookup is performed
        db = _mock_db_sequential([archive, assignment1, spool1, assignment2, spool2])

        # Two filaments used
        filament_usage = [
            {"slot_id": 1, "used_g": 15.0, "type": "PLA", "color": "#FF0000"},
            {"slot_id": 2, "used_g": 25.0, "type": "PLA", "color": "#00FF00"},
        ]

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.api.routes.settings.get_setting", return_value="15.0"),
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
                ams_mapping=ams_mapping,
            )

        assert len(results) == 2

        # First spool: 15g at 20/kg = 0.30
        spool1_result = next(r for r in results if r["spool_id"] == 1)
        assert spool1_result["weight_used"] == 15.0
        assert spool1_result["cost"] == 0.30

        # Second spool: 25g at 25/kg = 0.625, rounded to 0.62
        spool2_result = next(r for r in results if r["spool_id"] == 2)
        assert spool2_result["weight_used"] == 25.0
        assert spool2_result["cost"] == 0.62


class TestCostAggregation:
    """Tests for cost aggregation to PrintArchive."""

    @pytest.mark.asyncio
    async def test_costs_summed_in_archive(self):
        """Multiple spool costs are summed when aggregated to archive."""
        # This test would need to mock the full main.py flow
        # For now, we verify the results dict structure includes cost
        results = [
            {"spool_id": 1, "weight_used": 20.0, "cost": 0.50},
            {"spool_id": 2, "weight_used": 30.0, "cost": 0.75},
        ]

        # Simulate aggregation logic from main.py
        total_cost = sum(r.get("cost", 0) or 0 for r in results)
        assert total_cost == 1.25

    @pytest.mark.asyncio
    async def test_null_costs_handled_in_aggregation(self):
        """None costs don't break aggregation."""
        results = [
            {"spool_id": 1, "weight_used": 20.0, "cost": 0.50},
            {"spool_id": 2, "weight_used": 30.0, "cost": None},  # No cost
            {"spool_id": 3, "weight_used": 10.0, "cost": 0.25},
        ]

        # Aggregation should handle None gracefully
        total_cost = sum(r.get("cost", 0) or 0 for r in results)
        assert total_cost == 0.75  # Only spools 1 and 3

    @pytest.mark.asyncio
    async def test_cost_with_archive_id(self):
        """Test cost aggregation using archive_id (3MF path)."""
        spool_new = _make_spool(spool_id=1, label_weight=1000, cost_per_kg=25.0)
        assignment_new = _make_assignment(spool_id=1)
        archive_new = _make_archive(archive_id=20)
        filament_usage_new = [{"slot_id": 1, "used_g": 20.0, "type": "PLA", "color": "#FF0000"}]

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]},
            progress=100,
            layer_num=50,
            tray_now=0,
        )

        db = _mock_db_sequential([archive_new, None, assignment_new, spool_new])

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.api.routes.settings.get_setting", return_value="15.0"),
            patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=filament_usage_new),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results_new = await on_print_complete(
                printer_id=1,
                data={"status": "completed"},
                printer_manager=printer_manager,
                db=db,
                archive_id=20,
            )

        assert len(results_new) == 1
        assert results_new[0]["spool_id"] == 1
        assert results_new[0]["cost"] == 0.50  # 20g / 1000 * 25.0

    @pytest.mark.asyncio
    async def test_cost_with_print_name_ams_fallback(self):
        """Test cost aggregation using print_name (AMS fallback, legacy path)."""
        spool_old = _make_spool(spool_id=2, label_weight=1000, cost_per_kg=15.0)
        assignment_old = _make_assignment(spool_id=2, ams_id=0, tray_id=0)
        legacy_print_name = "LegacyPrint"

        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name=legacy_print_name,
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 80},
            tray_now_at_start=0,
        )

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]},
            progress=100,
            layer_num=50,
            tray_now=0,
        )

        db = _mock_db_sequential([assignment_old, spool_old])

        with (
            patch("backend.app.core.config.settings") as mock_settings,
            patch("backend.app.api.routes.settings.get_setting", return_value="15.0"),
            patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=None),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results_old = await on_print_complete(
                printer_id=1,
                data={"status": "completed", "subtask_name": legacy_print_name, "filename": legacy_print_name},
                printer_manager=printer_manager,
                db=db,
                archive_id=None,
            )

        assert len(results_old) == 1
        assert results_old[0]["spool_id"] == 2
        assert results_old[0]["cost"] == 1.5  # 100g / 1000 * 15.0
