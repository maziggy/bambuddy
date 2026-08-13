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

from backend.app.services.print_scheduler import (
    AUTO_DRY_MAX_UNPRODUCTIVE_CYCLES,
    AUTO_DRY_REARM_COOLDOWN_SECONDS,
    PrintScheduler,
)


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


class TestPlateHoldDoesNotGateDrying:
    """#2801 — an unacknowledged plate must not stop the AMS heating.

    Plate-clear answers "is the bed ready for the next job". It says nothing
    about whether filament may be dried, and the gap between a finished print
    and the acknowledgment is exactly when drying is most useful: the printer
    is free and nobody is waiting on it. Leaving the plate unacknowledged is
    also how people hold the queue by hand.

    Before this, such a printer landed in the dispatch set, was read as
    "currently printing", took the mid-print path -- capped temperature,
    (mid-print) in the log -- and bypassed the very gate that was meant to
    hold it, while the queue loop tore the cycle down once a tick.
    """

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @staticmethod
    def _finished_printer_state():
        state = MagicMock()
        state.state = "FINISH"
        state.firmware_version = "01.03.00.00"
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "module_type": "n3f",
                    "dry_time": 0,
                    "humidity_raw": "75",
                    "dry_sf_reason": [],
                    "tray": [{"tray_type": "PLA"}],
                }
            ]
        }
        return state

    def _db(self):
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=TestAmbientDrying._make_db_side_effect(
                {
                    "queue_drying_enabled": TestAmbientDrying._make_setting("false"),
                    "ambient_drying_enabled": TestAmbientDrying._make_setting("true"),
                    "print_drying_enabled": TestAmbientDrying._make_setting("true"),
                    "ams_humidity_fair": TestAmbientDrying._make_setting("60"),
                    "queue_drying_block": TestAmbientDrying._make_setting("false"),
                    "drying_presets": None,
                }
            )
        )
        return db

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_finished_printer_with_dirty_plate_dries_at_full_temperature(self, mock_sd, mock_pm, scheduler):
        mock_pm.get_status.return_value = self._finished_printer_state()
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "P2S"
        mock_pm.send_drying_command.return_value = True
        scheduler._is_printer_idle = MagicMock(return_value=True)

        await scheduler._check_auto_drying(self._db(), [], set())

        # 45 degC is the uncapped PLA preset: mid-print would have sent 40.
        mock_pm.send_drying_command.assert_called_once_with(1, 0, 45, 12, mode=1, filament="PLA")

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_idleness_is_judged_without_the_plate_gate(self, mock_sd, mock_pm, scheduler):
        mock_pm.get_status.return_value = self._finished_printer_state()
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "P2S"
        mock_pm.send_drying_command.return_value = True
        scheduler._is_printer_idle = MagicMock(return_value=True)

        await scheduler._check_auto_drying(self._db(), [], set())

        scheduler._is_printer_idle.assert_called_with(1, require_plate_clear=False)

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_a_printer_about_to_print_is_still_left_alone(self, mock_sd, mock_pm, scheduler):
        """The narrow set keeps its job: an imminent print must not be dried into."""
        mock_pm.get_status.return_value = self._finished_printer_state()
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "P2S"
        mock_pm.send_drying_command.return_value = True
        scheduler._is_printer_idle = MagicMock(return_value=True)

        await scheduler._check_auto_drying(self._db(), [], {1})

        assert not mock_pm.send_drying_command.called


class TestStopDrying:
    """Test _stop_drying — sends stop commands and clears tracking."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_stops_all_ams_units(self, mock_pm, scheduler):
        """Sends stop command to each auto-armed AMS unit that is drying."""
        scheduler._drying_in_progress = {1: time.monotonic()}
        scheduler._auto_dry_units[(1, 0)] = {"ended_at": None}
        scheduler._auto_dry_units[(1, 128)] = {"ended_at": None}
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
    async def test_leaves_cycles_bambuddy_did_not_start(self, mock_pm, scheduler):
        """A hand-started dry on another unit survives (#2801).

        One auto-dried unit used to be enough to stop every AMS on the
        printer reporting dry_time > 0, which took the user's own cycle with
        it. The entry gate only ever knew about cycles Bambuddy began; the
        action now matches.
        """
        scheduler._drying_in_progress = {1: time.monotonic()}
        scheduler._auto_dry_units[(1, 0)] = {"ended_at": None}
        state = MagicMock()
        state.raw_data = {"ams": [{"id": 0, "dry_time": 120}, {"id": 1, "dry_time": 600}]}
        mock_pm.get_status.return_value = state

        await scheduler._stop_drying(1)

        calls = mock_pm.send_drying_command.call_args_list
        assert [c.args[1] for c in calls] == [0]

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_stops_nothing_it_cannot_prove_it_started(self, mock_pm, scheduler):
        """After a restart Bambuddy cannot tell its own cycle from a manual one.

        _sync_drying_state prunes but never adopts, for exactly this reason, so
        a cycle armed before the restart is left running rather than risking a
        stop on somebody's manual dry. Tracking is still cleared.
        """
        scheduler._drying_in_progress = {1: time.monotonic()}
        state = MagicMock()
        state.raw_data = {"ams": [{"id": 0, "dry_time": 120}]}
        mock_pm.get_status.return_value = state

        await scheduler._stop_drying(1)

        assert not mock_pm.send_drying_command.called
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
    """Regression #1892: a running drying cycle must never be stopped by a humidity re-check.

    Relative humidity reads low in heated air (the AMS sensor sees ~15-20% within
    minutes of the dryer starting even while the filament is still saturated), so a
    humidity-based auto-stop would truncate every cycle — manual or Bambuddy-started —
    to the old minimum-time floor. Drying is now left to run to its configured
    duration; the firmware stops it when the duration elapses.
    """

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_no_stop_before_minimum_time(self, mock_sd, mock_pm, scheduler):
        """Drying should NOT stop when humidity drops below threshold shortly after start."""
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
    async def test_no_stop_after_long_elapsed_time(self, mock_sd, mock_pm, scheduler):
        """#1892: drying must NOT stop even long after start with low humidity — let it run."""
        # Simulate: drying started 35 minutes ago, humidity reads low (heated air)
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

        # Must NOT send a humidity-based stop — drying is left to run to its duration
        for call in mock_pm.send_drying_command.call_args_list:
            assert call != ((1, 0), {"temp": 0, "duration": 0, "mode": 0}), (
                "Humidity re-check must never stop a running drying cycle (#1892)"
            )

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
        scheduler._auto_dry_units[(1, 0)] = {"ended_at": None}
        scheduler._auto_dry_units[(2, 0)] = {"ended_at": None}

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

    @staticmethod
    def _make_setting(value):
        s = MagicMock()
        s.value = value
        return s

    @staticmethod
    def _make_db_side_effect(settings_map):
        """Create a side_effect for db.execute that returns settings by key."""

        async def side_effect(stmt):
            result = MagicMock()
            try:
                compiled = stmt.compile(compile_kwargs={"literal_binds": False})
                param_values = list(compiled.params.values())
            except Exception:
                param_values = []

            for key, val in settings_map.items():
                if key in param_values:
                    result.scalar_one_or_none.return_value = val
                    return result

            result.scalar_one_or_none.return_value = None
            return result

        return side_effect

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_stops_when_no_scheduled_items(self, mock_pm, scheduler):
        """Auto-drying stops when queue has no scheduled items (queue mode only)."""
        scheduler._drying_in_progress = {1: time.monotonic()}
        scheduler._auto_dry_units[(1, 0)] = {"ended_at": None}

        state = MagicMock()
        state.raw_data = {"ams": [{"id": 0, "dry_time": 120}]}
        mock_pm.get_status.return_value = state

        db = AsyncMock()
        settings_returns = {
            "queue_drying_enabled": self._make_setting("true"),
            "ambient_drying_enabled": self._make_setting("false"),
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

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
        """Auto-drying stops when queue is completely empty (queue mode only)."""
        scheduler._drying_in_progress = {1: time.monotonic()}
        scheduler._auto_dry_units[(1, 0)] = {"ended_at": None}

        state = MagicMock()
        state.raw_data = {"ams": [{"id": 0, "dry_time": 120}]}
        mock_pm.get_status.return_value = state

        db = AsyncMock()
        settings_returns = {
            "queue_drying_enabled": self._make_setting("true"),
            "ambient_drying_enabled": self._make_setting("false"),
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

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


class _DryingTestBase:
    """Shared helpers for auto-drying integration tests."""

    @staticmethod
    def _make_setting(value):
        s = MagicMock()
        s.value = value
        return s

    @staticmethod
    def _make_db_side_effect(settings_map, printer_ids=None):
        """Create a side_effect for db.execute that returns settings by key and printers."""
        if printer_ids is None:
            printer_ids = [1]

        async def side_effect(stmt):
            result = MagicMock()
            stmt_str = str(stmt)

            try:
                compiled = stmt.compile(compile_kwargs={"literal_binds": False})
                param_values = list(compiled.params.values())
            except Exception:
                param_values = []

            for key, val in settings_map.items():
                if key in param_values:
                    result.scalar_one_or_none.return_value = val
                    return result

            if "printer" in stmt_str.lower() or "is_active" in stmt_str:
                printers = []
                for pid in printer_ids:
                    p = MagicMock()
                    p.id = pid
                    p.is_active = True
                    printers.append(p)
                scalars_mock = MagicMock()
                scalars_mock.__iter__ = MagicMock(return_value=iter(printers))
                result.scalars.return_value = scalars_mock
            else:
                result.scalar_one_or_none.return_value = None
            return result

        return side_effect


class TestAmbientDrying(_DryingTestBase):
    """Tests for ambient drying mode — drying based on humidity regardless of queue state."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_ambient_dries_idle_printer_without_queue(self, mock_sd, mock_pm, scheduler):
        """Ambient mode starts drying on idle printers even with no queue items."""
        state = MagicMock()
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "module_type": "n3f",
                    "dry_time": 0,
                    "humidity_raw": "75",
                    "dry_sf_reason": [],
                    "tray": [{"tray_type": "PLA"}],
                }
            ]
        }
        state.firmware_version = "01.09.00.00"
        mock_pm.get_status.return_value = state
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "X1C"
        mock_pm.send_drying_command.return_value = True

        scheduler._is_printer_idle = MagicMock(return_value=True)
        db = AsyncMock()

        settings_returns = {
            "queue_drying_enabled": self._make_setting("false"),
            "ambient_drying_enabled": self._make_setting("true"),
            "ams_humidity_fair": self._make_setting("60"),
            "queue_drying_block": self._make_setting("false"),
            "drying_presets": None,
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

        # Empty queue — ambient mode should still dry
        await scheduler._check_auto_drying(db, [], set())

        mock_pm.send_drying_command.assert_called_once_with(1, 0, 45, 12, mode=1, filament="PLA")
        assert 1 in scheduler._drying_in_progress

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_ambient_does_not_dry_below_threshold(self, mock_sd, mock_pm, scheduler):
        """Ambient mode does NOT dry when humidity is below threshold."""
        state = MagicMock()
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "module_type": "n3f",
                    "dry_time": 0,
                    "humidity_raw": "40",
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
            "queue_drying_enabled": self._make_setting("false"),
            "ambient_drying_enabled": self._make_setting("true"),
            "ams_humidity_fair": self._make_setting("60"),
            "queue_drying_block": self._make_setting("false"),
            "drying_presets": None,
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

        await scheduler._check_auto_drying(db, [], set())

        mock_pm.send_drying_command.assert_not_called()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_ambient_off_stops_drying_without_queue(self, mock_pm, scheduler):
        """Disabling ambient drying stops drying on printers without queue items."""
        scheduler._drying_in_progress = {1: time.monotonic()}
        scheduler._auto_dry_units[(1, 0)] = {"ended_at": None}

        state = MagicMock()
        state.raw_data = {"ams": [{"id": 0, "dry_time": 120}]}
        mock_pm.get_status.return_value = state

        db = AsyncMock()
        settings_returns = {
            "queue_drying_enabled": self._make_setting("false"),
            "ambient_drying_enabled": self._make_setting("false"),
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

        await scheduler._check_auto_drying(db, [], set())

        assert mock_pm.send_drying_command.called
        assert not scheduler._drying_in_progress

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_ambient_continues_when_queue_empty(self, mock_sd, mock_pm, scheduler):
        """Ambient drying continues even when queue has no scheduled items (unlike queue mode)."""
        scheduler._drying_in_progress = {1: time.monotonic() - 100}

        state = MagicMock()
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "module_type": "n3f",
                    "dry_time": 600,
                    "humidity_raw": "75",
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
            "queue_drying_enabled": self._make_setting("false"),
            "ambient_drying_enabled": self._make_setting("true"),
            "ams_humidity_fair": self._make_setting("60"),
            "queue_drying_block": self._make_setting("false"),
            "drying_presets": None,
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

        await scheduler._check_auto_drying(db, [], set())

        # Should NOT have sent stop — humidity still high, drying continues
        for call in mock_pm.send_drying_command.call_args_list:
            assert call.kwargs.get("mode") != 0, "Should not stop drying in ambient mode with high humidity"
        assert 1 in scheduler._drying_in_progress

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_queue_only_does_not_dry_without_scheduled_items(self, mock_sd, mock_pm, scheduler):
        """Queue mode alone does NOT dry printers that have no scheduled queue items."""
        state = MagicMock()
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "module_type": "n3f",
                    "dry_time": 0,
                    "humidity_raw": "75",
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
            "ambient_drying_enabled": self._make_setting("false"),
            "ams_humidity_fair": self._make_setting("60"),
            "queue_drying_block": self._make_setting("false"),
            "drying_presets": None,
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

        # No queue items at all
        await scheduler._check_auto_drying(db, [], set())

        mock_pm.send_drying_command.assert_not_called()


class TestBlockForDryingBugFix(_DryingTestBase):
    """Regression: block mode gates NEW drying starts but must leave running dries alone (#1892)."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_block_mode_leaves_active_drying_running(self, mock_sd, mock_pm, scheduler):
        """#1892: a printer already drying in block mode must not be stopped by a humidity re-check."""
        # Drying started 35 minutes ago
        scheduler._drying_in_progress = {1: time.monotonic() - 2100}

        state = MagicMock()
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "module_type": "n3f",
                    "dry_time": 600,
                    "humidity_raw": "30",  # Below threshold
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
            "ambient_drying_enabled": self._make_setting("false"),
            "ams_humidity_fair": self._make_setting("60"),
            "queue_drying_block": self._make_setting("true"),
            "drying_presets": None,
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

        # Queue item exists for this printer (triggers block mode gate)
        item = MagicMock()
        item.printer_id = 1
        item.scheduled_time = MagicMock()
        item.manual_start = False

        await scheduler._check_auto_drying(db, [item], set())

        # Must NOT stop the running dry — block mode gates new starts, not active cycles
        for call in mock_pm.send_drying_command.call_args_list:
            assert call != ((1, 0), {"temp": 0, "duration": 0, "mode": 0}), (
                "Block mode must not stop an already-running drying cycle (#1892)"
            )

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_block_mode_prevents_new_drying_start(self, mock_sd, mock_pm, scheduler):
        """Block mode should still prevent starting NEW drying on printers with pending items."""
        state = MagicMock()
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "module_type": "n3f",
                    "dry_time": 0,
                    "humidity_raw": "75",
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
            "ambient_drying_enabled": self._make_setting("false"),
            "ams_humidity_fair": self._make_setting("60"),
            "queue_drying_block": self._make_setting("true"),
            "drying_presets": None,
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

        item = MagicMock()
        item.printer_id = 1
        item.scheduled_time = MagicMock()
        item.manual_start = False

        await scheduler._check_auto_drying(db, [item], set())

        # Should NOT start drying — block mode with pending items
        mock_pm.send_drying_command.assert_not_called()


class TestResolveHumidityThreshold:
    """Per-filament humidity threshold resolver (#1605).

    Resolves the trigger threshold for an AMS unit from the loaded tray types.
    Mixed loads use the lowest (most restrictive) value. Empty / unloaded trays
    contribute no constraint; falls back to the global ``ams_humidity_fair``
    when no per-type overrides are configured.
    """

    def test_no_overrides_falls_back_to_global(self):
        """Empty overrides map → caller's global fallback is used verbatim."""
        result = PrintScheduler.resolve_humidity_threshold([{"tray_type": "PLA"}], {}, 60)
        assert result == 60

    def test_single_known_type_uses_override(self):
        """Single PLA tray with override = 50 returns 50."""
        result = PrintScheduler.resolve_humidity_threshold(
            [{"tray_type": "PLA Basic"}],
            {"default": 60, "PLA": 50},
            60,
        )
        assert result == 50

    def test_mixed_load_picks_lowest(self):
        """Mixed PLA (60) + Nylon (20) → most restrictive = 20."""
        result = PrintScheduler.resolve_humidity_threshold(
            [{"tray_type": "PLA Basic"}, {"tray_type": "PA Glass"}],
            {"default": 60, "PLA": 60, "PA": 20},
            60,
        )
        assert result == 20

    def test_unknown_type_uses_default_key(self):
        """Tray type not in the map falls back to the 'default' key, not the
        caller fallback. Lets the user tune unknown-filament behavior."""
        result = PrintScheduler.resolve_humidity_threshold(
            [{"tray_type": "EXOTIC_WOOD"}],
            {"default": 40, "PLA": 60},
            999,
        )
        assert result == 40

    def test_empty_tray_slots_skipped(self):
        """Empty tray_type strings (unloaded slots) contribute no constraint."""
        result = PrintScheduler.resolve_humidity_threshold(
            [{"tray_type": ""}, {"tray_type": "PLA"}],
            {"default": 30, "PLA": 50},
            60,
        )
        assert result == 50

    def test_all_empty_trays_uses_default_key(self):
        """No loaded trays at all → falls back to default key (or fallback if
        no overrides). Matches the empty-AMS behavior of the existing alarm
        site so an empty AMS still alarms at the user's default rate."""
        result = PrintScheduler.resolve_humidity_threshold(
            [{"tray_type": ""}, {}],
            {"default": 30, "PLA": 50},
            60,
        )
        assert result == 30

    def test_filament_name_normalized(self):
        """Tray types like 'PLA Basic', 'pla basic' all normalize to 'PLA'."""
        result = PrintScheduler.resolve_humidity_threshold(
            [{"tray_type": "pla basic"}],
            {"default": 60, "PLA": 25},
            60,
        )
        assert result == 25

    def test_no_tray_type_field_skipped(self):
        """Missing tray_type field is treated as empty (unloaded)."""
        result = PrintScheduler.resolve_humidity_threshold(
            [{}, {"tray_type": "ASA"}],
            {"default": 60, "ASA": 30},
            60,
        )
        assert result == 30


class TestGetHumidityThresholds:
    """The DB-loading helper for ``ams_humidity_thresholds`` (#1605)."""

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @pytest.mark.asyncio
    async def test_missing_setting_returns_empty(self, scheduler):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        result = await scheduler._get_humidity_thresholds(db)
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_value_returns_empty(self, scheduler):
        db = AsyncMock()
        setting = MagicMock(value="")
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=setting)))
        result = await scheduler._get_humidity_thresholds(db)
        assert result == {}

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty(self, scheduler):
        db = AsyncMock()
        setting = MagicMock(value="not json{")
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=setting)))
        result = await scheduler._get_humidity_thresholds(db)
        assert result == {}

    @pytest.mark.asyncio
    async def test_valid_json_normalizes_keys(self, scheduler):
        """Filament-type keys uppercase; 'default' preserved."""
        db = AsyncMock()
        setting = MagicMock(value='{"default": 60, "pla": 50, "ASA": 30, "garbage": "x"}')
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=setting)))
        result = await scheduler._get_humidity_thresholds(db)
        assert result == {"default": 60, "PLA": 50, "ASA": 30}


class TestMidPrintDrying(_DryingTestBase):
    """Tests for the print_drying_enabled path — drying that runs CONCURRENTLY
    with an active print on capable hardware (H2D / H2C / H2S / P2S / X2D / X1C /
    A2L / H2D Pro on recent firmware). Distinct from idle drying.

    Verifies:
      - With the toggle ON and capable hardware, a printer in the busy set is
        still evaluated and drying fires at the capped temperature.
      - The temperature cap is max(40, preset_temp - 5) — protects spools.
      - With the toggle OFF, the existing busy-printer skip still applies.
      - With the toggle ON but unsupported firmware, the busy-printer skip
        still applies (gated by supports_drying_while_printing).
    """

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @staticmethod
    def _ams_unit(humidity: str = "75"):
        return {
            "id": 0,
            "module_type": "n3f",
            "dry_time": 0,
            "humidity_raw": humidity,
            "dry_sf_reason": [],
            "tray": [{"tray_type": "PLA"}],
        }

    def _state(self, firmware: str):
        state = MagicMock()
        state.raw_data = {"ams": [self._ams_unit()]}
        state.firmware_version = firmware
        return state

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_running_printer_dries_when_enabled_and_capable(self, mock_pm, scheduler):
        """Toggle ON + capable hardware: running printer dries at capped temp."""
        state = self._state("01.03.00.00")
        state.state = "RUNNING"
        mock_pm.get_status.return_value = state
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "H2D"
        mock_pm.send_drying_command.return_value = True

        scheduler._is_printer_idle = MagicMock(return_value=False)
        db = AsyncMock()
        settings_returns = {
            "queue_drying_enabled": self._make_setting("true"),
            "ambient_drying_enabled": self._make_setting("false"),
            "print_drying_enabled": self._make_setting("true"),
            "ams_humidity_fair": self._make_setting("60"),
            "queue_drying_block": self._make_setting("false"),
            "drying_presets": None,
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

        # Actually printing (RUNNING), so the mid-print path applies
        await scheduler._check_auto_drying(db, [], {1})

        # PLA preset is 45 degC for n3f; mid-print cap is max(40, 45-5) = 40
        mock_pm.send_drying_command.assert_called_once_with(1, 0, 40, 12, mode=1, filament="PLA")
        assert 1 in scheduler._drying_in_progress

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_temp_cap_applied_above_floor(self, mock_pm, scheduler):
        """Higher-temp filament (PETG n3f=65) caps to 60, not floor."""
        state = MagicMock()
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "module_type": "n3f",
                    "dry_time": 0,
                    "humidity_raw": "75",
                    "dry_sf_reason": [],
                    "tray": [{"tray_type": "PETG"}],
                }
            ]
        }
        state.firmware_version = "01.03.00.00"
        state.state = "RUNNING"
        mock_pm.get_status.return_value = state
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "H2D"
        mock_pm.send_drying_command.return_value = True

        scheduler._is_printer_idle = MagicMock(return_value=False)
        db = AsyncMock()
        settings_returns = {
            "queue_drying_enabled": self._make_setting("true"),
            "ambient_drying_enabled": self._make_setting("false"),
            "print_drying_enabled": self._make_setting("true"),
            "ams_humidity_fair": self._make_setting("60"),
            "queue_drying_block": self._make_setting("false"),
            "drying_presets": None,
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

        await scheduler._check_auto_drying(db, [], {1})

        # PETG preset 65 -> max(40, 65-5) = 60
        mock_pm.send_drying_command.assert_called_once_with(1, 0, 60, 12, mode=1, filament="PETG")

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_running_printer_skipped_when_toggle_off(self, mock_sd, mock_pm, scheduler):
        """Toggle OFF: running printer is skipped even on capable hardware."""
        mock_pm.get_status.return_value = self._state("01.03.00.00")
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "H2D"

        scheduler._is_printer_idle = MagicMock(return_value=False)
        db = AsyncMock()
        settings_returns = {
            "queue_drying_enabled": self._make_setting("true"),
            "ambient_drying_enabled": self._make_setting("false"),
            "print_drying_enabled": self._make_setting("false"),
            "ams_humidity_fair": self._make_setting("60"),
            "queue_drying_block": self._make_setting("false"),
            "drying_presets": None,
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

        await scheduler._check_auto_drying(db, [], {1})

        mock_pm.send_drying_command.assert_not_called()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_running_printer_skipped_when_firmware_too_old(self, mock_pm, scheduler):
        """Toggle ON but firmware below matrix threshold: skip."""
        # H2D matrix minimum is 01.03.00.00; this is below
        mock_pm.get_status.return_value = self._state("01.02.30.00")
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "H2D"

        scheduler._is_printer_idle = MagicMock(return_value=False)
        db = AsyncMock()
        settings_returns = {
            "queue_drying_enabled": self._make_setting("true"),
            "ambient_drying_enabled": self._make_setting("false"),
            "print_drying_enabled": self._make_setting("true"),
            "ams_humidity_fair": self._make_setting("60"),
            "queue_drying_block": self._make_setting("false"),
            "drying_presets": None,
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

        await scheduler._check_auto_drying(db, [], {1})

        mock_pm.send_drying_command.assert_not_called()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    async def test_running_printer_skipped_when_model_excluded(self, mock_pm, scheduler):
        """Toggle ON, recent firmware, but excluded model (A1): skip."""
        mock_pm.get_status.return_value = self._state("99.99.99.99")
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "A1"

        scheduler._is_printer_idle = MagicMock(return_value=False)
        db = AsyncMock()
        settings_returns = {
            "queue_drying_enabled": self._make_setting("true"),
            "ambient_drying_enabled": self._make_setting("false"),
            "print_drying_enabled": self._make_setting("true"),
            "ams_humidity_fair": self._make_setting("60"),
            "queue_drying_block": self._make_setting("false"),
            "drying_presets": None,
        }
        db.execute = AsyncMock(side_effect=self._make_db_side_effect(settings_returns))

        await scheduler._check_auto_drying(db, [], {1})

        mock_pm.send_drying_command.assert_not_called()


class TestAutoDryRearmGuards(_DryingTestBase):
    """The re-arm loop from #2770.

    An AMS reads higher humidity while it is warm than once it has cooled, so a
    threshold set inside that band is never satisfied at the moment a cycle
    ends. The firmware is separately free to end a cycle early when it decides
    the filament is dry. Together those produced five 12-hour cycles armed in
    four hours on the reporter's H2D, one of them six seconds after the previous
    ended. These tests pin the two guards that break the loop and, just as
    importantly, that neither guard ever stops a cycle that is running.
    """

    THRESHOLD = "14"
    ABOVE = 16
    BELOW = 10

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @staticmethod
    def _ams_state(dry_time, humidity):
        state = MagicMock()
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "module_type": "n3f",
                    "dry_time": dry_time,
                    "humidity_raw": str(humidity),
                    "dry_sf_reason": [],
                    "tray": [{"tray_type": "PLA"}],
                }
            ]
        }
        state.firmware_version = "01.09.00.00"
        return state

    def _db(self):
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=self._make_db_side_effect(
                {
                    "queue_drying_enabled": self._make_setting("false"),
                    "ambient_drying_enabled": self._make_setting("true"),
                    "ams_humidity_fair": self._make_setting(self.THRESHOLD),
                    "queue_drying_block": self._make_setting("false"),
                    "drying_presets": None,
                }
            )
        )
        return db

    async def _pass(self, scheduler, mock_pm, db, dry_time, humidity):
        """One 30-second scheduler pass with the AMS in the given state."""
        mock_pm.get_status.return_value = self._ams_state(dry_time, humidity)
        await scheduler._check_auto_drying(db, [], set())

    async def _unproductive_cycle(self, scheduler, mock_pm, db):
        """Arm a cycle, watch it run, then see it end with humidity still high."""
        await self._pass(scheduler, mock_pm, db, 0, self.ABOVE)
        await self._pass(scheduler, mock_pm, db, 720, self.ABOVE)
        await self._pass(scheduler, mock_pm, db, 0, self.ABOVE)

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_does_not_rearm_immediately_after_a_cycle_ends(self, mock_sd, mock_pm, scheduler):
        """The six-second re-arm: one cycle ends, the next pass must not start another."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "H2D"
        mock_pm.send_drying_command.return_value = True
        scheduler._is_printer_idle = MagicMock(return_value=True)
        db = self._db()

        await self._unproductive_cycle(scheduler, mock_pm, db)
        await self._pass(scheduler, mock_pm, db, 0, self.ABOVE)

        assert mock_pm.send_drying_command.call_count == 1

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.notification_service")
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_suspends_after_repeated_unproductive_cycles(self, mock_sd, mock_pm, mock_notify, scheduler):
        """Past the cooldown the loop would resume, so the counter has to stop it."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "H2D"
        mock_pm.send_drying_command.return_value = True
        mock_notify.on_ams_drying_suspended = AsyncMock()
        scheduler._is_printer_idle = MagicMock(return_value=True)
        db = self._db()

        for _ in range(AUTO_DRY_MAX_UNPRODUCTIVE_CYCLES):
            await self._unproductive_cycle(scheduler, mock_pm, db)
            # Age the last cycle out of its cooldown so the next arm is allowed.
            scheduler._auto_dry_units[(1, 0)]["ended_at"] -= AUTO_DRY_REARM_COOLDOWN_SECONDS + 1

        assert mock_pm.send_drying_command.call_count == AUTO_DRY_MAX_UNPRODUCTIVE_CYCLES

        await self._pass(scheduler, mock_pm, db, 0, self.ABOVE)

        assert mock_pm.send_drying_command.call_count == AUTO_DRY_MAX_UNPRODUCTIVE_CYCLES
        assert scheduler._auto_dry_units[(1, 0)]["suspended"] is True
        mock_notify.on_ams_drying_suspended.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.notification_service")
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_suspension_notifies_once_and_stays_put(self, mock_sd, mock_pm, mock_notify, scheduler):
        """A suspension is a state, not a repeating alarm."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "H2D"
        mock_pm.send_drying_command.return_value = True
        mock_notify.on_ams_drying_suspended = AsyncMock()
        scheduler._is_printer_idle = MagicMock(return_value=True)
        db = self._db()
        scheduler._auto_dry_units[(1, 0)] = {
            "unproductive": AUTO_DRY_MAX_UNPRODUCTIVE_CYCLES,
            "suspended": False,
            "ended_at": None,
        }

        for _ in range(4):
            await self._pass(scheduler, mock_pm, db, 0, self.ABOVE)

        mock_pm.send_drying_command.assert_not_called()
        assert mock_notify.on_ams_drying_suspended.await_count == 1

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.notification_service")
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_humidity_dropping_lifts_the_suspension(self, mock_sd, mock_pm, mock_notify, scheduler):
        """The reading coming down is the evidence that drying works after all."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "H2D"
        mock_pm.send_drying_command.return_value = True
        mock_notify.on_ams_drying_suspended = AsyncMock()
        scheduler._is_printer_idle = MagicMock(return_value=True)
        db = self._db()
        scheduler._auto_dry_units[(1, 0)] = {
            "unproductive": AUTO_DRY_MAX_UNPRODUCTIVE_CYCLES,
            "suspended": True,
            "ended_at": None,
        }

        await self._pass(scheduler, mock_pm, db, 0, self.BELOW)
        # The judgement is cleared, but the re-arm clock is kept (#2801).
        assert not scheduler._auto_dry_units[(1, 0)].get("suspended")
        assert not scheduler._auto_dry_units[(1, 0)].get("unproductive")

        await self._pass(scheduler, mock_pm, db, 0, self.ABOVE)
        mock_pm.send_drying_command.assert_called_once_with(1, 0, 45, 12, mode=1, filament="PLA")

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_manual_cycle_is_neither_counted_nor_interrupted(self, mock_sd, mock_pm, scheduler):
        """A dry the user started by hand carries no history and is never stopped."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "H2D"
        mock_pm.send_drying_command.return_value = True
        scheduler._is_printer_idle = MagicMock(return_value=True)
        db = self._db()

        await self._pass(scheduler, mock_pm, db, 720, self.ABOVE)
        await self._pass(scheduler, mock_pm, db, 600, self.ABOVE)

        mock_pm.send_drying_command.assert_not_called()
        assert (1, 0) not in scheduler._auto_dry_units

        # It ends; Bambuddy is free to arm its own cycle, with a clean slate.
        await self._pass(scheduler, mock_pm, db, 0, self.ABOVE)
        mock_pm.send_drying_command.assert_called_once_with(1, 0, 45, 12, mode=1, filament="PLA")
        assert scheduler._auto_dry_units[(1, 0)]["unproductive"] == 0

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_history_is_dropped_when_the_printer_goes_away(self, mock_sd, mock_pm, scheduler):
        """A printer deleted and re-added must not inherit a suspension."""
        mock_pm.get_status.return_value = None
        scheduler._auto_dry_units[(1, 0)] = {"unproductive": 5, "suspended": True, "ended_at": None}

        scheduler._sync_drying_state()

        assert scheduler._auto_dry_units == {}


class TestAutoDryStoppedByBambuddy(_DryingTestBase):
    """A cycle Bambuddy itself cut short must not count against the unit."""

    THRESHOLD = "14"
    ABOVE = 16

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @staticmethod
    def _ams_state(dry_time, humidity):
        state = MagicMock()
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "module_type": "n3f",
                    "dry_time": dry_time,
                    "humidity_raw": str(humidity),
                    "dry_sf_reason": [],
                    "tray": [{"tray_type": "PLA"}],
                }
            ]
        }
        state.firmware_version = "01.09.00.00"
        return state

    def _db(self):
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=self._make_db_side_effect(
                {
                    "queue_drying_enabled": self._make_setting("false"),
                    "ambient_drying_enabled": self._make_setting("true"),
                    "ams_humidity_fair": self._make_setting(self.THRESHOLD),
                    "queue_drying_block": self._make_setting("false"),
                    "drying_presets": None,
                }
            )
        )
        return db

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_print_takes_priority_stop_is_not_an_unproductive_cycle(self, mock_sd, mock_pm, scheduler):
        """The queue stopping a dry so a print can start is Bambuddy's own doing.

        Counting it would suspend auto-drying on any printer that dries between
        jobs often enough -- exactly the install queue-drying exists for.
        """
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "H2D"
        mock_pm.send_drying_command.return_value = True
        scheduler._is_printer_idle = MagicMock(return_value=True)
        db = self._db()

        mock_pm.get_status.return_value = self._ams_state(0, self.ABOVE)
        await scheduler._check_auto_drying(db, [], set())
        mock_pm.get_status.return_value = self._ams_state(720, self.ABOVE)
        await scheduler._check_auto_drying(db, [], set())

        # A print is ready: check_queue stops drying on this printer.
        await scheduler._stop_drying(1)

        mock_pm.get_status.return_value = self._ams_state(0, self.ABOVE)
        await scheduler._check_auto_drying(db, [], set())

        assert scheduler._auto_dry_units[(1, 0)]["unproductive"] == 0

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_manual_stop_button_is_not_an_unproductive_cycle(self, mock_sd, mock_pm, scheduler):
        """The Stop button goes straight to printer_manager, bypassing the
        scheduler, so the route has to tell the scheduler to forget the cycle."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "H2D"
        mock_pm.send_drying_command.return_value = True
        scheduler._is_printer_idle = MagicMock(return_value=True)
        db = self._db()

        mock_pm.get_status.return_value = self._ams_state(0, self.ABOVE)
        await scheduler._check_auto_drying(db, [], set())
        mock_pm.get_status.return_value = self._ams_state(720, self.ABOVE)
        await scheduler._check_auto_drying(db, [], set())

        scheduler.forget_auto_dry_cycle(1, 0)

        mock_pm.get_status.return_value = self._ams_state(0, self.ABOVE)
        await scheduler._check_auto_drying(db, [], set())

        assert scheduler._auto_dry_units[(1, 0)]["unproductive"] == 0


class TestAutoDryProgressKeepsItGoing(_DryingTestBase):
    """Suspension must not punish a spool that is genuinely drying, just slowly."""

    THRESHOLD = "25"

    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @staticmethod
    def _ams_state(dry_time, humidity):
        state = MagicMock()
        state.raw_data = {
            "ams": [
                {
                    "id": 0,
                    "module_type": "n3f",
                    "dry_time": dry_time,
                    "humidity_raw": str(humidity),
                    "dry_sf_reason": [],
                    "tray": [{"tray_type": "PLA"}],
                }
            ]
        }
        state.firmware_version = "01.09.00.00"
        return state

    def _db(self):
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=self._make_db_side_effect(
                {
                    "queue_drying_enabled": self._make_setting("false"),
                    "ambient_drying_enabled": self._make_setting("true"),
                    "ams_humidity_fair": self._make_setting(self.THRESHOLD),
                    "queue_drying_block": self._make_setting("false"),
                    "drying_presets": None,
                }
            )
        )
        return db

    async def _cycle(self, scheduler, mock_pm, db, end_humidity):
        """Arm a cycle, run it, and end it at the given reading."""
        mock_pm.get_status.return_value = self._ams_state(0, end_humidity)
        await scheduler._check_auto_drying(db, [], set())
        mock_pm.get_status.return_value = self._ams_state(720, end_humidity)
        await scheduler._check_auto_drying(db, [], set())
        mock_pm.get_status.return_value = self._ams_state(0, end_humidity)
        await scheduler._check_auto_drying(db, [], set())
        entry = scheduler._auto_dry_units.get((1, 0))
        if entry is not None and isinstance(entry.get("ended_at"), float):
            entry["ended_at"] -= AUTO_DRY_REARM_COOLDOWN_SECONDS + 1

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.notification_service")
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_a_reading_that_keeps_falling_is_never_suspended(self, mock_sd, mock_pm, mock_notify, scheduler):
        """40 -> 37 -> 35 -> 33 with a 25% threshold: nowhere near it yet, but
        every cycle is working. A humid workshop must not lose auto-drying."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "H2D"
        mock_pm.send_drying_command.return_value = True
        mock_notify.on_ams_drying_suspended = AsyncMock()
        scheduler._is_printer_idle = MagicMock(return_value=True)
        db = self._db()

        for reading in (40, 37, 35, 33, 31, 29):
            await self._cycle(scheduler, mock_pm, db, reading)

        assert scheduler._auto_dry_units[(1, 0)]["suspended"] is False
        mock_notify.on_ams_drying_suspended.assert_not_awaited()
        assert mock_pm.send_drying_command.call_count == 6

    @pytest.mark.asyncio
    @patch("backend.app.services.print_scheduler.notification_service")
    @patch("backend.app.services.print_scheduler.printer_manager")
    @patch("backend.app.services.print_scheduler.supports_drying", return_value=True)
    async def test_a_reading_oscillating_by_one_point_still_suspends(self, mock_sd, mock_pm, mock_notify, scheduler):
        """A plateau with sensor noise — 31, 30, 31, 30 — is not progress.
        Comparing against the previous cycle rather than the best-so-far would
        read every other cycle as an improvement and loop indefinitely."""
        mock_pm.is_connected.return_value = True
        mock_pm.get_model.return_value = "H2D"
        mock_pm.send_drying_command.return_value = True
        mock_notify.on_ams_drying_suspended = AsyncMock()
        scheduler._is_printer_idle = MagicMock(return_value=True)
        db = self._db()

        for reading in (31, 30, 31, 30, 31, 30):
            await self._cycle(scheduler, mock_pm, db, reading)

        assert scheduler._auto_dry_units[(1, 0)]["suspended"] is True
        mock_notify.on_ams_drying_suspended.assert_awaited_once()
