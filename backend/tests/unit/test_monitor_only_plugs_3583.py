"""#3583: monitor-only MQTT plugs are never switched by automation.

Bambuddy subscribes to an MQTT plug and never publishes, so it can be neither
turned on nor off. Its auto on / auto off default to on and the UI hides both,
yet every automation path tried to switch it -- through the Tasmota service,
with no IP. Linked beside a plug that can switch, it could take the queue's
power-on and hold the job, and every auto-off logged a failed turn-off.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.smart_plug import SmartPlug
from backend.app.services.print_scheduler import PrintScheduler
from backend.app.services.smart_plug_manager import SmartPlugManager, can_turn_off, is_monitor_only
from backend.tests.unit.test_scheduler_class_target_smart_plug_2786 import (  # noqa: F401 - fixture
    _add_item,
    _get_item,
    _run,
    queue_db,
)


def _plug(plug_id, plug_type="tasmota", **kw):
    fields = {
        "id": plug_id,
        "name": f"{plug_type} {plug_id}",
        "plug_type": plug_type,
        "ip_address": None if plug_type == "mqtt" else f"10.0.1.{plug_id}",
        "username": None,
        "password": None,
        "ha_entity_id": None,
        "enabled": True,
        "auto_on": True,
        "auto_off": True,
        "auto_off_after_drying": True,
        "off_delay_after_drying_minutes": 10,
        "off_delay_mode": "time",
        "off_delay_minutes": 5,
        "off_temp_threshold": 70,
        "printer_id": 1,
        "controls_printer_power": True,
        "auto_off_executed": False,
        "auto_off_pending": False,
        "auto_off_pending_since": None,
        "schedule_enabled": True,
        "schedule_on_time": "08:00",
        "schedule_off_time": "22:00",
        "last_state": "OFF",
    }
    fields.update(kw)
    plug = MagicMock()
    for key, value in fields.items():
        setattr(plug, key, value)
    return plug


class TestCapability:
    def test_mqtt_is_monitor_only(self):
        assert is_monitor_only(SimpleNamespace(plug_type="mqtt"))
        assert not is_monitor_only(SimpleNamespace(plug_type="rest"))

    @pytest.mark.parametrize(
        ("plug_type", "entity", "expected"),
        [
            ("tasmota", None, True),
            ("rest", None, True),
            ("homeassistant", "switch.printer", True),
            ("homeassistant", "script.printer_off", False),
            ("mqtt", None, False),
        ],
    )
    def test_can_turn_off(self, plug_type, entity, expected):
        assert can_turn_off(SimpleNamespace(plug_type=plug_type, ha_entity_id=entity)) is expected

    def test_auto_on_plugs_leave_out_mqtt(self):
        mqtt, rest = _plug(1, "mqtt"), _plug(2, "rest")

        assert PrintScheduler._auto_on_plugs([mqtt, rest]) == [rest]


class TestPrintCycle:
    @pytest.mark.asyncio
    async def test_print_start_does_not_switch_on_an_mqtt_plug(self):
        manager = SmartPlugManager()
        mqtt, tasmota = _plug(1, "mqtt"), _plug(2)
        with (
            patch.object(manager, "_get_plugs_for_printer", AsyncMock(return_value=[mqtt, tasmota])),
            patch("backend.app.services.smart_plug_manager.tasmota_service") as service,
        ):
            service.turn_on = AsyncMock(return_value=True)
            await manager.on_print_start(printer_id=1, db=AsyncMock())

        service.turn_on.assert_awaited_once_with(tasmota)

    @pytest.mark.asyncio
    async def test_print_start_still_runs_a_linked_script(self):
        """Turning on is narrower than switching: a script is run on purpose."""
        manager = SmartPlugManager()
        script = _plug(3, "homeassistant", ha_entity_id="script.printer_on")
        with (
            patch.object(manager, "_get_plugs_for_printer", AsyncMock(return_value=[script])),
            patch.object(manager, "_configure_ha_service", AsyncMock()),
            patch("backend.app.services.smart_plug_manager.homeassistant_service") as service,
        ):
            service.turn_on = AsyncMock(return_value=True)
            await manager.on_print_start(printer_id=1, db=AsyncMock())

        service.turn_on.assert_awaited_once_with(script)

    @pytest.mark.asyncio
    async def test_print_end_schedules_no_off_for_an_mqtt_plug(self):
        manager = SmartPlugManager()
        mqtt, tasmota = _plug(1, "mqtt"), _plug(2)
        with (
            patch.object(manager, "_get_plugs_for_printer", AsyncMock(return_value=[mqtt, tasmota])),
            patch.object(manager, "_schedule_delayed_off") as schedule,
        ):
            await manager.on_print_complete(printer_id=1, status="completed", db=AsyncMock())

        assert [c.args[0] for c in schedule.call_args_list] == [tasmota]

    @pytest.mark.asyncio
    async def test_auto_off_after_a_queue_job_skips_an_mqtt_plug(self):
        manager = SmartPlugManager()
        mqtt, tasmota = _plug(1, "mqtt"), _plug(2)
        with (
            patch.object(manager, "_get_plugs_for_printer", AsyncMock(return_value=[mqtt, tasmota])),
            patch.object(manager, "_schedule_off_per_mode") as schedule,
        ):
            await manager.schedule_off_after_queue_job(printer_id=1, db=AsyncMock())

        assert [c.args[0] for c in schedule.call_args_list] == [tasmota]

    @pytest.mark.asyncio
    async def test_drying_auto_off_skips_an_mqtt_plug(self):
        manager = SmartPlugManager()
        mqtt, tasmota = _plug(1, "mqtt"), _plug(2)
        with (
            patch.object(manager, "_get_plugs_for_printer", AsyncMock(return_value=[mqtt, tasmota])),
            patch.object(manager, "_schedule_delayed_off") as schedule,
        ):
            await manager.on_drying_complete(printer_id=1, db=AsyncMock())

        assert [c.args[0] for c in schedule.call_args_list] == [tasmota]


def _session_returning(plugs):
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = plugs
    db.execute = AsyncMock(return_value=result)
    session = MagicMock()
    session.return_value.__aenter__ = AsyncMock(return_value=db)
    session.return_value.__aexit__ = AsyncMock()
    return session, db


class TestSchedulesAndRestart:
    @pytest.mark.asyncio
    async def test_a_schedule_does_not_switch_an_mqtt_plug(self):
        manager = SmartPlugManager()
        mqtt = _plug(1, "mqtt")
        session, _db = _session_returning([mqtt])
        now = MagicMock()
        now.strftime.return_value = "08:00"
        with (
            patch("backend.app.services.smart_plug_manager.datetime") as clock,
            patch("backend.app.core.database.async_session", session),
            patch("backend.app.services.smart_plug_manager.tasmota_service") as service,
        ):
            clock.now.return_value = now
            clock.utcnow.return_value = datetime.now(timezone.utc)
            service.turn_on = AsyncMock(return_value=True)
            await manager._check_schedules()

        service.turn_on.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_pending_off_left_on_an_mqtt_plug_is_cleared(self):
        manager = SmartPlugManager()
        mqtt = _plug(1, "mqtt", auto_off_pending=True, auto_off_pending_since=datetime.now(timezone.utc))
        session, db = _session_returning([mqtt])
        with (
            patch("backend.app.core.database.async_session", session),
            patch.object(manager, "_schedule_delayed_off") as delayed,
            patch.object(manager, "_schedule_temp_based_off") as temp,
            patch("backend.app.services.smart_plug_manager.tasmota_service") as service,
        ):
            service.turn_off = AsyncMock(return_value=True)
            await manager.resume_pending_auto_offs()

        service.turn_off.assert_not_awaited()
        delayed.assert_not_called()
        temp.assert_not_called()
        assert mqtt.auto_off_pending is False
        db.commit.assert_awaited()


class TestObicoPowerOff:
    @pytest.mark.asyncio
    async def test_failure_power_off_skips_an_mqtt_plug(self):
        from backend.app.services import obico_actions

        mqtt, tasmota = _plug(1, "mqtt"), _plug(2)
        session, _db = _session_returning([])
        with (
            patch("backend.app.services.obico_actions.async_session", session),
            patch(
                "backend.app.services.smart_plug_manager.smart_plug_manager._get_plugs_for_printer",
                AsyncMock(return_value=[mqtt, tasmota]),
            ),
            patch("backend.app.services.smart_plug_manager.tasmota_service") as service,
            patch("backend.app.services.smart_plug_manager.publish_plug_state", AsyncMock()),
            patch(
                "backend.app.services.smart_plug_manager.run_linked_scripts_after_automatic_switch",
                AsyncMock(),
            ),
        ):
            service.turn_off = AsyncMock(return_value=True)
            await obico_actions._turn_off_linked_plugs(1)

        service.turn_off.assert_awaited_once_with(tasmota)


async def _add_typed_plug(ctx, printer_id, plug_type, *, controls_printer_power=True):
    async with ctx.session_maker() as db:
        fields = {"ip_address": f"10.0.1.{printer_id}"} if plug_type != "mqtt" else {"mqtt_topic": "tele/p/SENSOR"}
        if plug_type == "rest":
            fields = {"rest_on_url": "http://plug.example/on", "rest_off_url": "http://plug.example/off"}
        plug = SmartPlug(
            name=f"{plug_type} {printer_id}",
            plug_type=plug_type,
            printer_id=printer_id,
            enabled=True,
            auto_on=True,
            controls_printer_power=controls_printer_power,
            **fields,
        )
        db.add(plug)
        await db.commit()
        return plug.id


def _powered_plug_types(power_on):
    return [call.args[0].plug_type for call in power_on.await_args_list]


class TestQueuePowerOn:
    @pytest.mark.asyncio
    async def test_a_job_on_a_printer_powers_on_with_the_plug_that_can_switch(self, queue_db):
        """The bug: the MQTT plug came first and was picked, and the job held."""
        await _add_typed_plug(queue_db, 1, "mqtt")
        await _add_typed_plug(queue_db, 1, "rest")
        await _add_item(queue_db, printer_id=1)

        power_on = await _run(queue_db, PrintScheduler(), power_on=AsyncMock(return_value=False))

        assert _powered_plug_types(power_on) == ["rest"]

    @pytest.mark.asyncio
    async def test_an_any_model_job_wakes_with_the_plug_that_can_switch(self, queue_db):
        await _add_typed_plug(queue_db, 1, "mqtt")
        await _add_typed_plug(queue_db, 1, "rest")
        await _add_item(queue_db, target_model="X1C")

        power_on = await _run(queue_db, PrintScheduler(), power_on=AsyncMock(return_value=True))

        assert _powered_plug_types(power_on) == ["rest"]

    @pytest.mark.asyncio
    async def test_a_printer_with_only_an_mqtt_plug_is_not_woken(self, queue_db):
        """And says why: it has no plug that can switch it on."""
        await _add_typed_plug(queue_db, 1, "mqtt")
        await _add_typed_plug(queue_db, 2, "mqtt")
        item_id = await _add_item(queue_db, target_model="X1C")

        power_on = await _run(queue_db, PrintScheduler(), power_on=AsyncMock(return_value=True))

        power_on.assert_not_awaited()
        item = await _get_item(queue_db, item_id)
        assert "no Auto On smart plug" in (item.waiting_reason or "")
