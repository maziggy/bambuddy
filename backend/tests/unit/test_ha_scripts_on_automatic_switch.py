"""HA "Run when printer turns on/off" scripts follow automatic switching too.

They ran only when someone switched the printer's plug by hand. Auto-off
(delayed, by temperature, resumed after a restart), the plug schedule and AI
failure detection switched the printer without them. Print start already runs
the "on" scripts itself, as linked plugs with auto-on, so it is not routed
through here; nor is the queue's power-on, which print start follows.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import smart_plug_manager as spm
from backend.app.services.smart_plug_manager import SmartPlugManager


@pytest.fixture(autouse=True)
def _fresh_run_memory():
    spm._recent_automatic_script_runs.clear()
    yield
    spm._recent_automatic_script_runs.clear()


def _plug(**fields):
    base = {
        "id": 1,
        "name": "Plug",
        "plug_type": "tasmota",
        "ha_entity_id": None,
        "printer_id": 7,
        "controls_printer_power": True,
        "auto_on": False,
        "auto_off": False,
        "enabled": True,
    }
    base.update(fields)
    return SimpleNamespace(**base)


def _script(name, **fields):
    return _plug(name=name, plug_type="homeassistant", ha_entity_id=f"script.{name}", **fields)


class TestWhichScriptsRun:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("state", "ran"), [("ON", ["lights_on"]), ("OFF", ["fan_off"])])
    async def test_by_the_direction_of_the_switch(self, state, ran):
        plugs = [_plug(), _script("lights_on", auto_on=True), _script("fan_off", auto_off=True)]
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: plugs)))
        service = MagicMock(turn_on=AsyncMock(return_value=True))

        with patch.object(spm.smart_plug_manager, "get_service_for_plug", AsyncMock(return_value=service)):
            await spm.run_linked_scripts(7, state, db)

        assert [call.args[0].name for call in service.turn_on.await_args_list] == ran


class TestOnlyThePrintersPowerPlug:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("plug", "printer_id", "runs"),
        [
            (_plug(), 7, True),
            (_plug(controls_printer_power=False), 7, False),  # an accessory, e.g. a lamp
            (_script("x"), 7, False),
            (_plug(), None, False),
        ],
    )
    async def test_after_an_automatic_switch(self, plug, printer_id, runs):
        session = MagicMock()
        session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        session.return_value.__aexit__ = AsyncMock(return_value=False)
        run = AsyncMock()

        with (
            patch("backend.app.core.database.async_session", session),
            patch.object(spm, "run_linked_scripts", run),
        ):
            await spm.run_linked_scripts_after_automatic_switch(printer_id, plug, "OFF")

        assert run.await_count == (1 if runs else 0)


@pytest.fixture
def scripts():
    run = AsyncMock()
    with patch.object(spm, "run_linked_scripts_after_automatic_switch", run):
        yield run


def _service():
    return MagicMock(turn_on=AsyncMock(return_value=True), turn_off=AsyncMock(return_value=True))


class TestEveryAutomaticOff:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("feeds_printer", [True, False])
    async def test_the_delayed_auto_off(self, scripts, feeds_printer):
        manager = SmartPlugManager()
        with (
            patch.object(spm, "printer_manager") as pm,
            patch.object(spm, "publish_plug_state", AsyncMock()),
            patch.object(manager, "get_service_for_plug", AsyncMock(return_value=_service())),
            patch.object(manager, "_mark_auto_off_executed", AsyncMock()),
        ):
            pm.is_print_active.return_value = False
            await manager._delayed_off(
                3, "tasmota", "1.2.3.4", None, None, None, 7, 0, controls_printer_power=feeds_printer
            )

        printer_id, plug, state = scripts.await_args.args
        assert (printer_id, state, plug.controls_printer_power) == (7, "OFF", feeds_printer)

    @pytest.mark.asyncio
    async def test_the_temperature_auto_off(self, scripts):
        manager = SmartPlugManager()
        with (
            patch.object(spm, "printer_manager") as pm,
            patch.object(spm, "publish_plug_state", AsyncMock()),
            patch.object(spm.asyncio, "sleep", AsyncMock()),
            patch.object(manager, "get_service_for_plug", AsyncMock(return_value=_service())),
            patch.object(manager, "_mark_auto_off_executed", AsyncMock()),
        ):
            pm.get_status.return_value = MagicMock(state="FINISH", temperatures={"nozzle": 30})
            pm.is_print_active.return_value = False
            await manager._temp_based_off(3, "tasmota", "1.2.3.4", None, None, None, printer_id=7, temp_threshold=55)

        assert scripts.await_args.args[0] == 7 and scripts.await_args.args[2] == "OFF"

    @pytest.mark.asyncio
    async def test_a_resumed_auto_off(self, scripts):
        manager = SmartPlugManager()
        plug = MagicMock(
            id=1, printer_id=7, off_delay_mode="time", controls_printer_power=True, auto_off_pending_since=None
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [plug])))
        session = MagicMock()
        session.return_value.__aenter__ = AsyncMock(return_value=db)
        session.return_value.__aexit__ = AsyncMock(return_value=False)
        with (
            patch("backend.app.core.database.async_session", session),
            patch.object(spm, "printer_manager") as pm,
            patch.object(spm, "publish_plug_state", AsyncMock()),
            patch.object(manager, "get_service_for_plug", AsyncMock(return_value=_service())),
            patch.object(manager, "_mark_auto_off_executed", AsyncMock()),
        ):
            pm.is_print_active.return_value = False
            await manager.resume_pending_auto_offs()

        scripts.assert_awaited_once_with(7, plug, "OFF")


class TestTheSchedule:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("now", "state"), [("08:00", "ON"), ("22:00", "OFF")])
    async def test_a_scheduled_switch(self, scripts, now, state):
        manager = SmartPlugManager()
        plug = MagicMock(
            id=1, printer_id=7, schedule_on_time="08:00", schedule_off_time="22:00", controls_printer_power=True
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [plug])))
        session = MagicMock()
        session.return_value.__aenter__ = AsyncMock(return_value=db)
        session.return_value.__aexit__ = AsyncMock(return_value=False)
        with (
            patch("backend.app.core.database.async_session", session),
            patch.object(spm, "datetime") as clock,
            patch.object(spm, "printer_manager"),
            patch.object(spm, "publish_plug_state", AsyncMock()),
            patch.object(manager, "get_service_for_plug", AsyncMock(return_value=_service())),
        ):
            clock.now.return_value = MagicMock(strftime=MagicMock(return_value=now))
            await manager._check_schedules()

        scripts.assert_awaited_once_with(7, plug, state)


class TestFailureDetection:
    @pytest.mark.asyncio
    async def test_scripts_run_once_and_are_not_turned_off(self, scripts):
        """Two plugs feed the printer; the linked script is run, not switched."""
        from backend.app.services import obico_actions

        mains = [_plug(id=1), _plug(id=2)]
        script = _script("fan_off", auto_off=True, id=3)
        service = _service()
        session = MagicMock()
        session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        session.return_value.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(obico_actions, "async_session", session),
            patch.object(spm.smart_plug_manager, "_get_plugs_for_printer", AsyncMock(return_value=[*mains, script])),
            patch.object(spm.smart_plug_manager, "get_service_for_plug", AsyncMock(return_value=service)),
            patch.object(spm, "publish_plug_state", AsyncMock()),
        ):
            await obico_actions._turn_off_linked_plugs(7)

        assert [call.args[0].id for call in service.turn_off.await_args_list] == [1, 2]
        assert scripts.await_count == 1
        assert scripts.await_args.args[0] == 7 and scripts.await_args.args[2] == "OFF"


class TestReviewCases:
    @pytest.mark.asyncio
    async def test_a_script_whose_automation_is_off_does_not_run(self):
        plugs = [_script("paused", auto_off=True, enabled=False), _script("live", auto_off=True)]
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: plugs)))
        service = MagicMock(turn_on=AsyncMock(return_value=True))

        with patch.object(spm.smart_plug_manager, "get_service_for_plug", AsyncMock(return_value=service)):
            await spm.run_linked_scripts(7, "OFF", db)

        assert [call.args[0].name for call in service.turn_on.await_args_list] == ["live"]

    @pytest.mark.asyncio
    async def test_two_plugs_switching_the_printer_run_the_scripts_once(self):
        """Two plugs feed one printer and both auto-off after a print."""
        session = MagicMock()
        session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        session.return_value.__aexit__ = AsyncMock(return_value=False)
        run = AsyncMock()

        with (
            patch("backend.app.core.database.async_session", session),
            patch.object(spm, "run_linked_scripts", run),
        ):
            await spm.run_linked_scripts_after_automatic_switch(7, _plug(id=1), "OFF")
            await spm.run_linked_scripts_after_automatic_switch(7, _plug(id=2), "OFF")
            await spm.run_linked_scripts_after_automatic_switch(7, _plug(id=1), "ON")

        assert [call.args[1] for call in run.await_args_list] == ["OFF", "ON"]

    @pytest.mark.asyncio
    async def test_failure_detection_that_switched_no_power_plug_runs_nothing(self, scripts):
        from backend.app.services import obico_actions

        lamp = _plug(id=4, controls_printer_power=False)
        session = MagicMock()
        session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        session.return_value.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(obico_actions, "async_session", session),
            patch.object(spm.smart_plug_manager, "_get_plugs_for_printer", AsyncMock(return_value=[lamp])),
            patch.object(spm.smart_plug_manager, "get_service_for_plug", AsyncMock(return_value=_service())),
            patch.object(spm, "publish_plug_state", AsyncMock()),
        ):
            await obico_actions._turn_off_linked_plugs(7)

        scripts.assert_not_awaited()
