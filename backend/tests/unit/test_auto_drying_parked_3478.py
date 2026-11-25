"""Auto-drying and a countdown the firmware never runs (#3478).

An AMS HT took a drying command mid-print and left the heater off: dry_time
sat at 720 with dry_status Off for a week. The MQTT layer flags that as
dry_countdown_stalled, but auto-drying treated any dry_time > 0 as a running
cycle, so the unit was never dried again. Now a parked timer is not drying: a
cycle Bambuddy armed that parks is closed out and counted on its own (it says
nothing about humidity), and two in a row suspend the unit with a message that
says the printer did not heat.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import (
    AUTO_DRY_MAX_NEVER_HEATED_CYCLES,
    AUTO_DRY_REARM_COOLDOWN_SECONDS,
    PrintScheduler,
)

UNIT = (1, 128)
WET = 75
DRY = 50


def _state(dry_time: int, stalled: bool, humidity: int = WET):
    state = MagicMock()
    state.raw_data = {
        "ams": [
            {
                "id": 128,
                "module_type": "n3s",
                "dry_time": dry_time,
                "dry_status": 0,
                "dry_countdown_stalled": stalled,
                "humidity_raw": str(humidity),
                "dry_sf_reason": [],
                "tray": [{"tray_type": "PLA"}],
            }
        ]
    }
    state.firmware_version = "01.09.00.00"
    return state


def _setting(value):
    s = MagicMock()
    s.value = value
    return s


def _db():
    settings = {
        "queue_drying_enabled": _setting("false"),
        "ambient_drying_enabled": _setting("true"),
        "ams_humidity_fair": _setting("60"),
        "queue_drying_block": _setting("false"),
    }

    async def execute(stmt):
        result = MagicMock()
        try:
            params = list(stmt.compile().params.values())
        except Exception:
            params = []
        for key, val in settings.items():
            if key in params:
                result.scalar_one_or_none.return_value = val
                return result
        if "printer" in str(stmt).lower():
            printer = MagicMock()
            printer.id = 1
            printer.is_active = True
            scalars = MagicMock()
            scalars.__iter__ = MagicMock(return_value=iter([printer]))
            result.scalars.return_value = scalars
        else:
            result.scalar_one_or_none.return_value = None
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute)
    return db


@pytest.fixture
def env():
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.notification_service") as notify,
        patch("backend.app.services.print_scheduler.supports_drying", return_value=True),
    ):
        pm.is_connected.return_value = True
        pm.get_model.return_value = "P2S"
        pm.send_drying_command.return_value = True
        notify.on_ams_drying_suspended = AsyncMock()
        scheduler = PrintScheduler()
        scheduler._is_printer_idle = MagicMock(return_value=True)
        db = _db()

        async def run(dry_time, stalled, humidity=WET):
            pm.get_status.return_value = _state(dry_time, stalled, humidity)
            await scheduler._check_auto_drying(db, [], set())

        yield scheduler, pm, notify, run


def _age_out_cooldown(scheduler):
    scheduler._auto_dry_units[UNIT]["ended_at"] -= AUTO_DRY_REARM_COOLDOWN_SECONDS + 1


async def _parked_cycle(run):
    """Arm, see the command taken, then see the stall detector flag it."""
    await run(0, False)
    await run(720, False)
    await run(720, True)


class TestParkedTimerIsNotDrying:
    @pytest.mark.asyncio
    async def test_a_parked_timer_bambuddy_did_not_start_gets_a_start(self, env):
        """The reporter's unit: parked at 720, humidity above the threshold."""
        scheduler, pm, _notify, run = env
        await run(720, True)
        pm.send_drying_command.assert_called_once_with(1, 128, 45, 12, mode=1, filament="PLA")
        assert scheduler._auto_dry_units[UNIT]["running"] is True

    @pytest.mark.asyncio
    async def test_a_timer_not_yet_called_parked_still_runs(self, env):
        _scheduler, pm, _notify, run = env
        await run(720, False)
        pm.send_drying_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_parked_cycle_is_closed_counted_apart_and_cools_off(self, env):
        scheduler, pm, _notify, run = env
        await _parked_cycle(run)

        unit = scheduler._auto_dry_units[UNIT]
        assert "running" not in unit
        assert unit["never_heated"] == 1
        assert unit.get("unproductive", 0) == 0
        assert "best_end_humidity" not in unit
        assert isinstance(unit["ended_at"], float)

        await run(720, True)
        assert pm.send_drying_command.call_count == 1

    @pytest.mark.asyncio
    async def test_parked_cycles_suspend_with_the_not_heating_message(self, env):
        scheduler, pm, notify, run = env
        for _ in range(AUTO_DRY_MAX_NEVER_HEATED_CYCLES - 1):
            await _parked_cycle(run)
            _age_out_cooldown(scheduler)
        # The next start goes out on top of the parked timer and parks again.
        await run(720, True)
        await run(720, True)

        assert pm.send_drying_command.call_count == AUTO_DRY_MAX_NEVER_HEATED_CYCLES
        unit = scheduler._auto_dry_units[UNIT]
        assert unit["suspended"] is True
        notify.on_ams_drying_suspended.assert_awaited_once()
        assert notify.on_ams_drying_suspended.await_args.kwargs["never_heated"] is True

        _age_out_cooldown(scheduler)
        await run(720, True)
        assert pm.send_drying_command.call_count == AUTO_DRY_MAX_NEVER_HEATED_CYCLES

    @pytest.mark.asyncio
    async def test_a_cycle_that_runs_resets_the_parked_count(self, env):
        scheduler, pm, notify, run = env
        await _parked_cycle(run)
        _age_out_cooldown(scheduler)
        # This one counts down and finishes.
        await run(720, True)
        await run(719, False)
        await run(0, False)

        assert "never_heated" not in scheduler._auto_dry_units[UNIT]
        notify.on_ams_drying_suspended.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dry_humidity_lifts_a_not_heating_suspension(self, env):
        scheduler, _pm, _notify, run = env
        scheduler._auto_dry_units[UNIT] = {"suspended": True, "never_heated": 2, "ended_at": None}
        await run(0, False, humidity=DRY)
        unit = scheduler._auto_dry_units[UNIT]
        assert not unit.get("suspended")
        assert "never_heated" not in unit


class TestSyncDryingState:
    def test_a_printer_with_only_a_parked_timer_is_not_drying(self):
        scheduler = PrintScheduler()
        scheduler._drying_in_progress[1] = time.monotonic()
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = _state(720, True)
            scheduler._sync_drying_state()
        assert 1 not in scheduler._drying_in_progress

    def test_a_running_timer_keeps_the_printer_drying(self):
        scheduler = PrintScheduler()
        scheduler._drying_in_progress[1] = time.monotonic()
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.get_status.return_value = _state(700, False)
            scheduler._sync_drying_state()
        assert 1 in scheduler._drying_in_progress


class TestNotification:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("never_heated", "template"),
        [(False, "ams_drying_suspended"), (True, "ams_drying_not_heating")],
    )
    async def test_picks_the_template_on_the_same_switch(self, never_heated, template):
        from backend.app.services.notification_service import NotificationService

        service = NotificationService()
        with (
            patch.object(service, "_get_providers_for_event", new_callable=AsyncMock) as get,
            patch.object(service, "_send_to_providers", new_callable=AsyncMock),
            patch.object(service, "_build_message_from_template", new_callable=AsyncMock) as build,
        ):
            get.return_value = [MagicMock()]
            build.return_value = ("t", "m")
            await service.on_ams_drying_suspended(
                1, "P2S", "HT-A", 75.0, 60.0, 2, AsyncMock(), never_heated=never_heated
            )
        assert get.await_args.args[1] == "on_ams_drying_suspended"
        assert build.await_args.args[1] == template
