"""Tests for the auto-drying feature in the print scheduler.

Covers:
- Conservative drying parameter selection (mixed filaments)
- Drying preset loading (user-configured vs defaults)
- Auto-drying lifecycle: start, humidity stop, minimum drying time
- Auto-drying stop conditions: feature disabled, no scheduled items, per-printer
- Sync drying state after restart
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler


class TestConservativeDryingParams:
    """Test _get_conservative_drying_params — picks safest temp/duration for mixed filaments."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    def test_single_filament_pla(self, scheduler):
        """Single PLA tray uses PLA preset."""
        trays = [{"tray_type": "PLA"}]
        presets = PrintScheduler.DEFAULT_DRYING_PRESETS
        result = scheduler._get_conservative_drying_params(trays, "n3f", presets)
        assert result == (45, 12, "PLA")

    def test_mixed_filaments_lowest_temp(self, scheduler):
        """Mixed PLA + ABS: should use PLA's 45°C (lowest), ABS's 12h (longest for n3f)."""
        trays = [{"tray_type": "PLA"}, {"tray_type": "ABS"}]
        presets = PrintScheduler.DEFAULT_DRYING_PRESETS
        result = scheduler._get_conservative_drying_params(trays, "n3f", presets)
        temp, hours, _ = result
        assert temp == 45  # PLA is lowest
        assert hours == 12

    def test_mixed_filaments_longest_duration(self, scheduler):
        """Mixed ABS (8h) + PVA (18h) on n3s: should use longest duration."""
        trays = [{"tray_type": "ABS"}, {"tray_type": "PVA"}]
        presets = PrintScheduler.DEFAULT_DRYING_PRESETS
        result = scheduler._get_conservative_drying_params(trays, "n3s", presets)
        temp, hours, _ = result
        assert temp == 80  # ABS n3s=80, PVA n3s=85 → lowest=80
        assert hours == 18  # ABS n3s_hours=8, PVA n3s_hours=18 → longest=18

    def test_empty_trays_returns_none(self, scheduler):
        """No loaded trays returns None."""
        result = scheduler._get_conservative_drying_params([], "n3f", PrintScheduler.DEFAULT_DRYING_PRESETS)
        assert result is None

    def test_unknown_filament_skipped(self, scheduler):
        """Unknown filament types are ignored."""
        trays = [{"tray_type": "EXOTIC_WOOD"}]
        result = scheduler._get_conservative_drying_params(trays, "n3f", PrintScheduler.DEFAULT_DRYING_PRESETS)
        assert result is None

    def test_filament_type_normalization(self, scheduler):
        """'PLA Basic' should normalize to 'PLA'."""
        trays = [{"tray_type": "PLA Basic"}]
        presets = PrintScheduler.DEFAULT_DRYING_PRESETS
        result = scheduler._get_conservative_drying_params(trays, "n3f", presets)
        assert result is not None
        assert result[0] == 45  # PLA temp

    def test_empty_tray_type_skipped(self, scheduler):
        """Trays with empty tray_type are skipped."""
        trays = [{"tray_type": ""}, {"tray_type": "PETG"}]
        presets = PrintScheduler.DEFAULT_DRYING_PRESETS
        result = scheduler._get_conservative_drying_params(trays, "n3f", presets)
        assert result is not None
        assert result[2] == "PETG"

    def test_n3s_uses_n3s_keys(self, scheduler):
        """AMS-HT (n3s) should use n3s temp and n3s_hours."""
        trays = [{"tray_type": "TPU"}]
        presets = PrintScheduler.DEFAULT_DRYING_PRESETS
        result = scheduler._get_conservative_drying_params(trays, "n3s", presets)
        assert result == (75, 18, "TPU")  # n3s=75, n3s_hours=18

    def test_n3f_uses_n3f_keys(self, scheduler):
        """AMS 2 Pro (n3f) should use n3f temp and n3f_hours."""
        trays = [{"tray_type": "TPU"}]
        presets = PrintScheduler.DEFAULT_DRYING_PRESETS
        result = scheduler._get_conservative_drying_params(trays, "n3f", presets)
        assert result == (65, 12, "TPU")  # n3f=65, n3f_hours=12

    def test_custom_presets(self, scheduler):
        """Custom presets override defaults."""
        trays = [{"tray_type": "PLA"}]
        custom = {"PLA": {"n3f": 50, "n3s": 50, "n3f_hours": 6, "n3s_hours": 6}}
        result = scheduler._get_conservative_drying_params(trays, "n3f", custom)
        assert result == (50, 6, "PLA")


class TestDryingPresets:
    """Test _get_drying_presets — loads user presets from DB or falls back to defaults."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @pytest.mark.asyncio
    async def test_default_presets_when_no_setting(self, scheduler):
        """Returns built-in defaults when no DB setting exists."""
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        presets = await scheduler._get_drying_presets(db)
        assert presets == PrintScheduler.DEFAULT_DRYING_PRESETS

    @pytest.mark.asyncio
    async def test_user_presets_from_db(self, scheduler):
        """Returns user-configured presets when saved in DB."""
        db = AsyncMock()
        setting = MagicMock()
        setting.value = '{"PLA": {"n3f": 50, "n3s": 50, "n3f_hours": 6, "n3s_hours": 6}}'
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = setting
        db.execute = AsyncMock(return_value=result_mock)

        presets = await scheduler._get_drying_presets(db)
        assert presets["PLA"]["n3f"] == 50

    @pytest.mark.asyncio
    async def test_invalid_json_falls_back(self, scheduler):
        """Invalid JSON in DB falls back to defaults."""
        db = AsyncMock()
        setting = MagicMock()
        setting.value = "not valid json{{"
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = setting
        db.execute = AsyncMock(return_value=result_mock)

        presets = await scheduler._get_drying_presets(db)
        assert presets == PrintScheduler.DEFAULT_DRYING_PRESETS

    @pytest.mark.asyncio
    async def test_empty_string_falls_back(self, scheduler):
        """Empty string in DB falls back to defaults."""
        db = AsyncMock()
        setting = MagicMock()
        setting.value = ""
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = setting
        db.execute = AsyncMock(return_value=result_mock)

        presets = await scheduler._get_drying_presets(db)
        assert presets == PrintScheduler.DEFAULT_DRYING_PRESETS


class TestSyncDryingState:
    """Test _sync_drying_state — syncs in-memory state with actual printer status."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_removes_stopped_printers(self, mock_pm, scheduler):
        """Printers that stopped drying are removed from tracking."""
        scheduler._drying_in_progress = {1: time.monotonic()}
        state = MagicMock()
        state.raw_data = {"ams": [{"dry_time": 0}]}
        mock_pm.get_status.return_value = state

        scheduler._sync_drying_state()
        assert 1 not in scheduler._drying_in_progress

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_keeps_active_printers(self, mock_pm, scheduler):
        """Printers still drying remain in tracking."""
        ts = time.monotonic()
        scheduler._drying_in_progress = {1: ts}
        state = MagicMock()
        state.raw_data = {"ams": [{"dry_time": 120}]}
        mock_pm.get_status.return_value = state

        scheduler._sync_drying_state()
        assert scheduler._drying_in_progress[1] == ts

    @patch("backend.app.services.print_scheduler.printer_manager")
    def test_removes_disconnected_printers(self, mock_pm, scheduler):
        """Disconnected printers are removed from tracking."""
        scheduler._drying_in_progress = {1: time.monotonic()}
        mock_pm.get_status.return_value = None

        scheduler._sync_drying_state()
        assert 1 not in scheduler._drying_in_progress


class TestStopDrying:
    """Test _stop_drying — sends stop commands and clears tracking."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_stops_all_ams_units(self, mock_pm, scheduler):
        """Sends stop command to each AMS unit that is drying."""
        scheduler._drying_in_progress = {1: time.monotonic()}
        state = MagicMock()
        state.raw_data = {
            "ams": [
                {"id": 0, "dry_time": 120},
                {"id": 1, "dry_time": 0},
                {"id": 128, "dry_time": 60},
            ]
        }
        mock_pm.get_status.return_value = state

        await scheduler._stop_drying(1)

        # Should send stop to AMS 0 and 128, not AMS 1
        calls = mock_pm.send_drying_command.call_args_list
        assert len(calls) == 2
        assert calls[0].args == (1, 0, 0, 0)
        assert calls[1].args == (1, 128, 0, 0)
        assert 1 not in scheduler._drying_in_progress

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_clears_tracking_when_no_state(self, mock_pm, scheduler):
        """Clears tracking when printer has no state (disconnected)."""
        scheduler._drying_in_progress = {1: time.monotonic()}
        mock_pm.get_status.return_value = None

        await scheduler._stop_drying(1)
        assert 1 not in scheduler._drying_in_progress


class TestMinimumDryingTime:
    """Regression: drying should not stop/restart rapidly when humidity oscillates near threshold."""

    @pytest.fixture
    def scheduler(self):
        s = PrintScheduler()
        s._min_drying_seconds = 1800  # 30 minutes
        return s

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_no_stop_before_minimum_time(self, mock_sd, mock_pm, scheduler):
        """Drying should NOT stop when humidity drops below threshold before 30 min."""
        # Simulate: drying started 5 minutes ago
        scheduler._drying_in_progress = {1: time.monotonic() - 300}

        state = MagicMock()
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "module_type": "n3f",
                    "dry_time": 600,
                    "humidity_raw": "18",
                    "dry_sf_reason": [],
                    "tray": [{"tray_type": "PLA"}],
                }
            ]
        }
        state.firmware_version = "01.09.00.00"
        mock_pm.get_status.return_value = state
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "X1C"

        # Mock _is_printer_idle and DB
        scheduler._is_printer_idle = MagicMock(return_value=True)
        db = AsyncMock()

        # Mock settings: enabled, threshold=21
        settings_returns = {
            "queue_drying_enabled": self._make_setting("true"),
            "ams_humidity_fair": self._make_setting("21"),
            "queue_drying_block": self._make_setting("false"),
            "drying_presets": None,
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns, printer_id=1))

        # Queue item with schedule
        item = MagicMock()
        item.printer_id = 1
        item.scheduled_time = MagicMock()  # Has a schedule
        item.manual_start = False

        await scheduler._check_auto_drying(db, [item], set())

        # Should NOT have sent stop command via humidity check — minimum time not elapsed
        # The only calls should NOT include the humidity-based stop
        for call in mock_pm.send_drying_command.call_args_list:
            # If any stop was called, it should NOT be from the humidity path
            # (humidity path uses keyword args: temp=0, duration=0, mode=0)
            assert call != ((1, 0), {"temp": 0, "duration": 0, "mode": 0}), (
                "Humidity-based stop should not fire before minimum drying time"
            )

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_stops_after_minimum_time(self, mock_sd, mock_pm, scheduler):
        """Drying SHOULD stop when humidity below threshold AND 30 min elapsed."""
        # Simulate: drying started 35 minutes ago
        scheduler._drying_in_progress = {1: time.monotonic() - 2100}

        state = MagicMock()
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "module_type": "n3f",
                    "dry_time": 600,
                    "humidity_raw": "18",
                    "dry_sf_reason": [],
                    "tray": [{"tray_type": "PLA"}],
                }
            ]
        }
        state.firmware_version = "01.09.00.00"
        mock_pm.get_status.return_value = state
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "X1C"

        scheduler._is_printer_idle = MagicMock(return_value=True)
        db = AsyncMock()

        settings_returns = {
            "queue_drying_enabled": self._make_setting("true"),
            "ams_humidity_fair": self._make_setting("21"),
            "queue_drying_block": self._make_setting("false"),
            "drying_presets": None,
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns, printer_id=1))

        item = MagicMock()
        item.printer_id = 1
        item.scheduled_time = MagicMock()
        item.manual_start = False

        await scheduler._check_auto_drying(db, [item], set())

        # Should have sent stop command (humidity-based stop after minimum time)
        mock_pm.send_drying_command.assert_any_call(1, 0, temp=0, duration=0, mode=0)

    @staticmethod
    def _make_setting(value):
        s = MagicMock()
        s.value = value
        return s

    @staticmethod
    def _make_db_side_effect(settings_map, printer_id=1):
        """Create a side_effect for db.execute that returns settings and printers."""

        async def side_effect(stmt):
            result = MagicMock()
            stmt_str = str(stmt)

            # Extract bind parameter values (SQLAlchemy uses :key_1 placeholders)
            try:
                compiled = stmt.compile(compile_kwargs={"literal_binds": False})
                param_values = list(compiled.params.values())
            except Exception:
                param_values = []

            # Match settings queries by checking bind parameter values
            matched = False
            for key, val in settings_map.items():
                if key in param_values:
                    result.scalar_one_or_none.return_value = val
                    matched = True
                    break

            if not matched:
                if "printer" in stmt_str.lower() or "is_active" in stmt_str:
                    printer = MagicMock()
                    printer.id = printer_id
                    printer.is_active = True
                    scalars_mock = MagicMock()
                    scalars_mock.__iter__ = MagicMock(return_value=iter([printer]))
                    result.scalars.return_value = scalars_mock
                else:
                    result.scalar_one_or_none.return_value = None
            return result

        return side_effect


class TestAutoStopOnFeatureDisabled:
    """Regression: disabling auto-drying in settings should stop active drying sessions."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_stops_drying_when_disabled(self, mock_pm, scheduler):
        """Disabling auto-drying should send stop commands to all drying printers."""
        scheduler._drying_in_progress = {1: time.monotonic(), 2: time.monotonic()}

        # Printer 1: drying, Printer 2: drying
        def get_status(pid):
            state = MagicMock()
            state.raw_data = {"ams": [{"id": 0, "dry_time": 120}]}
            return state

        mock_pm.get_status.side_effect = get_status

        db = AsyncMock()
        # queue_drying_enabled = false
        setting = MagicMock()
        setting.value = "false"
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = setting
        db.execute = AsyncMock(return_value=result_mock)

        await scheduler._check_auto_drying(db, [], set())

        # Should have sent stop commands
        assert mock_pm.send_drying_command.call_count == 2
        assert not scheduler._drying_in_progress


class TestAutoStopOnNoScheduledItems:
    """Regression: removing scheduled items should stop auto-drying."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_stops_when_no_scheduled_items(self, mock_pm, scheduler):
        """Auto-drying stops when queue has no scheduled items."""
        scheduler._drying_in_progress = {1: time.monotonic()}

        state = MagicMock()
        state.raw_data = {"ams": [{"id": 0, "dry_time": 120}]}
        mock_pm.get_status.return_value = state

        db = AsyncMock()
        # queue_drying_enabled = true
        enabled_setting = MagicMock()
        enabled_setting.value = "true"

        call_count = [0]

        async def db_execute(stmt):
            call_count[0] += 1
            result = MagicMock()
            result.scalar_one_or_none.return_value = enabled_setting
            return result

        db.execute = AsyncMock(side_effect=db_execute)

        # Manual-start items only (no scheduled_time)
        item = MagicMock()
        item.printer_id = 1
        item.scheduled_time = None
        item.manual_start = True

        await scheduler._check_auto_drying(db, [item], set())

        # Should have stopped drying
        assert mock_pm.send_drying_command.called
        assert not scheduler._drying_in_progress

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_stops_when_empty_queue(self, mock_pm, scheduler):
        """Auto-drying stops when queue is completely empty."""
        scheduler._drying_in_progress = {1: time.monotonic()}

        state = MagicMock()
        state.raw_data = {"ams": [{"id": 0, "dry_time": 120}]}
        mock_pm.get_status.return_value = state

        db = AsyncMock()
        enabled_setting = MagicMock()
        enabled_setting.value = "true"
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = enabled_setting
        db.execute = AsyncMock(return_value=result_mock)

        await scheduler._check_auto_drying(db, [], set())

        assert mock_pm.send_drying_command.called
        assert not scheduler._drying_in_progress


class TestDryingTrackingTimestamps:
    """Test that _drying_in_progress uses timestamps, not booleans."""

    def test_initial_state_empty(self):
        """Fresh scheduler has no drying tracked."""
        scheduler = PrintScheduler()
        assert scheduler._drying_in_progress == {}

    def test_timestamp_is_monotonic(self):
        """Tracked values should be monotonic timestamps."""
        scheduler = PrintScheduler()
        before = time.monotonic()
        scheduler._drying_in_progress[1] = time.monotonic()
        after = time.monotonic()
        assert before <= scheduler._drying_in_progress[1] <= after

    def test_timestamp_is_truthy(self):
        """Timestamps are truthy for .get() checks (backward compat with bool pattern)."""
        scheduler = PrintScheduler()
        scheduler._drying_in_progress[1] = time.monotonic()
        assert scheduler._drying_in_progress.get(1)
        assert not scheduler._drying_in_progress.get(999)
