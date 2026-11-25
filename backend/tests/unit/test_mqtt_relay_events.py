"""MQTT relay events reach the broker.

Five relay methods were written and never called, one of them for a topic the
wiki documents (``smart_plugs/energy``). The first test makes that impossible to
repeat: every public ``on_*`` method of the relay has a caller. The rest cover
the energy publisher; the printer online/offline and filament-low events are
tested where they are produced (test_printer_offline_notification.py,
test_filament_low_producer_2913.py).
"""

import ast
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.models.smart_plug import SmartPlug
from backend.app.services.mqtt_relay import MQTTRelayService
from backend.app.services.smart_plug_manager import SmartPlugManager

APP = Path(__file__).resolve().parents[2] / "app"


def test_every_relay_event_has_a_caller():
    relay_source = (APP / "services" / "mqtt_relay.py").read_text(encoding="utf-8")
    service = next(
        node
        for node in ast.parse(relay_source).body
        if isinstance(node, ast.ClassDef) and node.name == MQTTRelayService.__name__
    )
    events = [
        node.name
        for node in service.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("on_")
    ]
    callers = "\n".join(path.read_text(encoding="utf-8") for path in APP.rglob("*.py") if path.name != "mqtt_relay.py")
    uncalled = [name for name in events if not re.search(rf"\.{name}\(", callers)]
    assert events
    assert uncalled == []


async def _plug(db_session, **kwargs) -> SmartPlug:
    fields = {"name": "Printer plug", "ip_address": "192.168.1.50"}
    fields.update(kwargs)
    plug = SmartPlug(**fields)
    db_session.add(plug)
    await db_session.commit()
    return plug


@pytest.fixture
def relay():
    with patch("backend.app.services.smart_plug_manager.mqtt_relay") as relay:
        relay.enabled = True
        relay.connected = True
        relay.on_smart_plug_energy = AsyncMock()
        yield relay


@pytest.mark.asyncio
async def test_each_enabled_plug_publishes_its_energy(db_session, relay):
    plug = await _plug(db_session)
    await _plug(db_session, name="Off", enabled=False)
    read = AsyncMock(return_value={"power": 120.5, "today": 1.25, "total": 310.0})

    with patch("backend.app.services.smart_plug_manager.get_plug_energy", read):
        await SmartPlugManager()._publish_energy_to_relay(db_session)

    relay.on_smart_plug_energy.assert_awaited_once_with(plug.id, "Printer plug", 120.5, 1.25, 310.0)


@pytest.mark.asyncio
async def test_a_plug_that_cannot_be_read_publishes_nothing(db_session, relay):
    await _plug(db_session)
    with patch("backend.app.services.smart_plug_manager.get_plug_energy", AsyncMock(return_value=None)):
        await SmartPlugManager()._publish_energy_to_relay(db_session)

    relay.on_smart_plug_energy.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_failing_plug_does_not_stop_the_others(db_session, relay):
    await _plug(db_session, name="Broken")
    good = await _plug(db_session, name="Good")

    async def read(plug, _db):
        if plug.name == "Broken":
            raise OSError("unreachable")
        return {"power": 5.0, "today": 0.1, "total": 2.0}

    with patch("backend.app.services.smart_plug_manager.get_plug_energy", side_effect=read):
        await SmartPlugManager()._publish_energy_to_relay(db_session)

    relay.on_smart_plug_energy.assert_awaited_once_with(good.id, "Good", 5.0, 0.1, 2.0)


@pytest.mark.parametrize(("enabled", "connected"), [(False, False), (True, False)])
@pytest.mark.asyncio
async def test_no_plug_is_read_while_the_relay_is_off(db_session, relay, enabled, connected):
    """The loop runs on every install; only relay users pay for the reads."""
    await _plug(db_session)
    relay.enabled = enabled
    relay.connected = connected
    read = AsyncMock(return_value={"power": 1.0, "today": 0.0, "total": 0.0})

    with patch("backend.app.services.smart_plug_manager.get_plug_energy", read):
        await SmartPlugManager()._publish_energy_to_relay(db_session)

    read.assert_not_awaited()
    relay.on_smart_plug_energy.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_mqtt_plug_that_stopped_reporting_publishes_nothing(db_session, relay):
    """An MQTT plug's values are the last message received; once the plug goes
    quiet they are stale, and publishing them every minute would report a dead
    plug as drawing power."""
    await _plug(db_session, name="Quiet", plug_type="mqtt", ip_address=None)
    live = await _plug(db_session, name="Live", plug_type="mqtt", ip_address=None)
    relay.smart_plug_service.is_reachable.side_effect = lambda plug_id: plug_id == live.id
    read = AsyncMock(return_value={"power": 50.0, "today": 0.5, "total": 0.5})

    with patch("backend.app.services.smart_plug_manager.get_plug_energy", read):
        await SmartPlugManager()._publish_energy_to_relay(db_session)

    relay.on_smart_plug_energy.assert_awaited_once_with(live.id, "Live", 50.0, 0.5, 0.5)


# -- smart_plugs/on and /off: every switch, not only the manual one -------------

# The plug backends themselves (a REST toggle is a turn_on or turn_off).
PLUG_BACKENDS = {"tasmota.py", "homeassistant.py", "rest_smart_plug.py", "mqtt_smart_plug.py"}
# Runs a Home Assistant script through its entity's turn_on: no plug changes state.
NOT_A_SWITCH = {("services/smart_plug_manager.py", "run_linked_scripts")}


def _called_names(node: ast.AST) -> set[str]:
    names = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            if isinstance(call.func, ast.Attribute):
                names.add(call.func.attr)
            elif isinstance(call.func, ast.Name):
                names.add(call.func.id)
    return names


def test_every_function_that_switches_a_plug_tells_the_relay():
    """smart_plugs/on and /off used to come only from the manual switch; auto-on,
    auto-off, schedules, the queue and failure detection switched plugs silently."""
    switching, missing = [], []
    for path in APP.rglob("*.py"):
        if path.name in PLUG_BACKENDS:
            continue
        rel = path.relative_to(APP).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or (rel, node.name) in NOT_A_SWITCH:
                continue
            names = _called_names(node)
            if names & {"turn_on", "turn_off", "toggle"}:
                switching.append(f"{rel}:{node.name}")
                if "publish_plug_state" not in names:
                    missing.append(f"{rel}:{node.name}")
    assert switching
    assert missing == []


@pytest.fixture
def session_for(db_session):
    """database.async_session() handing out the test session."""

    class _Session:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    with patch("backend.app.core.database.async_session", lambda: _Session()):
        yield


@pytest.mark.asyncio
async def test_publish_plug_state_names_the_printer_from_the_database(db_session, session_for):
    """Not from the connection cache: that keeps a renamed printer's old name
    and has none for an inactive printer."""
    from backend.app.models.printer import Printer
    from backend.app.services.smart_plug_manager import publish_plug_state

    printer = Printer(name="Workshop", serial_number="SN1", ip_address="192.0.2.1", access_code="x", is_active=False)
    db_session.add(printer)
    await db_session.commit()

    with patch("backend.app.services.smart_plug_manager.mqtt_relay") as relay:
        relay.on_smart_plug_state = AsyncMock()
        await publish_plug_state(3, "X1C plug", printer.id, on=False)
        await publish_plug_state(4, "Lamp", None, on=True)

    assert relay.on_smart_plug_state.await_args_list[0].kwargs == {
        "plug_id": 3,
        "plug_name": "X1C plug",
        "state": "off",
        "printer_id": printer.id,
        "printer_name": "Workshop",
    }
    assert relay.on_smart_plug_state.await_args_list[1].kwargs["printer_name"] is None
    assert relay.on_smart_plug_state.await_args_list[1].kwargs["state"] == "on"


@pytest.mark.asyncio
async def test_a_home_assistant_script_is_not_a_plug_switching(session_for):
    """Running a script through its entity changes no power state."""
    from backend.app.services.smart_plug_manager import publish_plug_state

    with patch("backend.app.services.smart_plug_manager.mqtt_relay") as relay:
        relay.on_smart_plug_state = AsyncMock()
        await publish_plug_state(5, "Lights script", None, on=True, ha_entity_id="script.printer_lights")
        await publish_plug_state(6, "Switch", None, on=True, ha_entity_id="switch.printer")

    relay.on_smart_plug_state.assert_awaited_once()
    assert relay.on_smart_plug_state.await_args.kwargs["plug_id"] == 6


@pytest.mark.asyncio
async def test_publish_plug_state_never_raises():
    """Switching the plug worked; a broker problem must not turn that into an error."""
    from backend.app.services.smart_plug_manager import publish_plug_state

    with patch("backend.app.services.smart_plug_manager.mqtt_relay") as relay:
        relay.on_smart_plug_state = AsyncMock(side_effect=RuntimeError("broker down"))
        await publish_plug_state(3, "X1C plug", None, on=True)


@pytest.mark.asyncio
async def test_the_delayed_auto_off_publishes_with_the_plug_name():
    manager = SmartPlugManager()
    service = AsyncMock()
    service.turn_off = AsyncMock(return_value=True)
    with (
        patch("backend.app.services.smart_plug_manager.printer_manager") as pm,
        patch("backend.app.services.smart_plug_manager.publish_plug_state", new_callable=AsyncMock) as publish,
        patch.object(manager, "get_service_for_plug", AsyncMock(return_value=service)),
        patch.object(manager, "_mark_auto_off_executed", new_callable=AsyncMock),
    ):
        pm.is_print_active.return_value = False
        await manager._delayed_off(
            3, "tasmota", "1.2.3.4", None, None, None, printer_id=1, delay_seconds=0, plug_name="X1C plug"
        )

    publish.assert_awaited_once_with(3, "X1C plug", 1, on=False, ha_entity_id=None)


# -- maintenance/alert carries the real values -----------------------------------


def test_a_print_hours_item_reports_hours_since_and_the_interval():
    from types import SimpleNamespace

    from backend.app.api.routes.maintenance import maintenance_alert_values

    item = SimpleNamespace(
        interval_type="hours", interval_hours=200.0, hours_since_maintenance=212.5, days_since_maintenance=None
    )
    assert maintenance_alert_values(item) == (212.5, 200.0, "hours")


def test_a_time_based_item_reports_days_since_and_the_interval():
    from types import SimpleNamespace

    from backend.app.api.routes.maintenance import maintenance_alert_values

    item = SimpleNamespace(
        interval_type="days", interval_hours=30.0, hours_since_maintenance=40.0, days_since_maintenance=31.2
    )
    assert maintenance_alert_values(item) == (31.2, 30.0, "days")
