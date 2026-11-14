"""BIQU Panda Breath over MQTT (#1466).

The payloads are the shape firmware 1.0.4 declares in its Home Assistant
discovery messages: one JSON state document per device, "ON"/"OFF" switches,
the three working modes as text, and the bound printer's serial number.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.panda_breath import PandaBreathService

STATE = {
    "chamber_temp": 41.5,
    "target_temp": 50,
    "work_on": "ON",
    "mode": "auto mode",
    "filament_drying_mode": "pla",
    "drying_running": "OFF",
    "drying_remaining_min": 0,
    "printer_sn": "01P00A123456789",
    "printer_bind": "bind",
    "filter_temp": 30,
}


def _service() -> PandaBreathService:
    service = PandaBreathService()
    service.enabled = True
    service.connected = True
    return service


@pytest.fixture
def printers():
    """printer_manager knows printer 7 by its serial number, printer 8 by another."""
    serials = {7: "01P00A123456789", 8: "01P00A999999999"}
    pm = MagicMock()
    pm.printer_id_for_serial.side_effect = lambda s: next(
        (pid for pid, sn in serials.items() if sn.upper() == (s or "").strip().upper()), None
    )
    pm.get_printer.side_effect = lambda pid: (
        SimpleNamespace(serial_number=serials[pid], name=f"P{pid}") if pid in serials else None
    )
    with patch("backend.app.services.printer_manager.printer_manager", pm):
        yield pm


class TestApplyMessage:
    def test_state_document_is_read(self, printers):
        service = _service()
        changed = service.apply_message("panda_breath/AABBCC/state", json.dumps(STATE))

        assert changed == {7}
        assert service.state_for_printer(7) == {
            "device_id": "AABBCC",
            "online": True,
            "chamber_temp": 41.5,
            "target_temp": 50.0,
            "work_on": True,
            "mode": "auto mode",
            "filament_drying_mode": "pla",
            "drying_running": False,
            "drying_remaining_min": 0.0,
        }

    def test_same_values_again_change_nothing(self, printers):
        service = _service()
        service.apply_message("panda_breath/AABBCC/state", json.dumps(STATE))
        assert service.apply_message("panda_breath/AABBCC/state", json.dumps(STATE)) == set()

    def test_partial_document_keeps_the_other_values(self, printers):
        service = _service()
        service.apply_message("panda_breath/AABBCC/state", json.dumps(STATE))
        service.apply_message("panda_breath/AABBCC/state", json.dumps({"work_on": "OFF"}))

        state = service.state_for_printer(7)
        assert state["work_on"] is False
        assert state["chamber_temp"] == 41.5

    def test_availability_marks_offline(self, printers):
        service = _service()
        service.apply_message("panda_breath/AABBCC/state", json.dumps(STATE))
        assert service.apply_message("panda_breath/AABBCC/availability", "offline") == {7}
        assert service.state_for_printer(7)["online"] is False

    def test_rebinding_updates_both_printers(self, printers):
        """Moved to another printer: the old card loses it, the new one gains it."""
        service = _service()
        service.apply_message("panda_breath/AABBCC/state", json.dumps(STATE))
        changed = service.apply_message("panda_breath/AABBCC/state", json.dumps({"printer_sn": "01P00A999999999"}))

        assert changed == {7, 8}
        assert service.state_for_printer(7) is None
        assert service.state_for_printer(8)["device_id"] == "AABBCC"

    @pytest.mark.parametrize(
        ("topic", "payload"),
        [
            ("panda_breath/AABBCC/state", "not json"),
            ("panda_breath/AABBCC/state", "[1, 2]"),
            ("panda_breath/AABBCC/command", json.dumps({"work_on": "ON"})),
            ("other/AABBCC/state", json.dumps(STATE)),
            ("panda_breath/state", json.dumps(STATE)),
        ],
    )
    def test_other_messages_are_ignored(self, printers, topic, payload):
        service = _service()
        assert service.apply_message(topic, payload) == set()
        assert service.devices == {}

    def test_unbound_device_reaches_no_printer(self, printers):
        service = _service()
        state = {**STATE, "printer_sn": ""}
        assert service.apply_message("panda_breath/AABBCC/state", json.dumps(state)) == set()
        assert service.state_for_printer(7) is None
        assert service.get_status({})["devices"][0]["bound"] is False

    def test_serial_matches_regardless_of_case(self, printers):
        service = _service()
        service.apply_message("panda_breath/AABBCC/state", json.dumps({**STATE, "printer_sn": " 01p00a123456789 "}))
        assert service.state_for_printer(7)["device_id"] == "AABBCC"

    def test_disabled_service_shows_nothing(self, printers):
        service = _service()
        service.apply_message("panda_breath/AABBCC/state", json.dumps(STATE))
        service.enabled = False
        assert service.state_for_printer(7) is None
        # The command route and the status of an offline printer look up by serial.
        assert service.device_for_serial("01P00A123456789") is None
        assert service.state_for_serial("01P00A123456789") is None


class TestStatus:
    def test_lists_devices_with_their_printer(self, printers):
        service = _service()
        service.apply_message("panda_breath/AABBCC/state", json.dumps(STATE))
        status = service.get_status({"01P00A123456789": (7, "P7")})

        assert status["enabled"] is True
        [row] = status["devices"]
        assert row["device_id"] == "AABBCC"
        assert row["printer_id"] == 7
        assert row["printer_name"] == "P7"
        assert "printer_sn" not in row

    def test_printer_the_caller_may_not_see_is_not_named(self, printers):
        service = _service()
        service.apply_message("panda_breath/AABBCC/state", json.dumps(STATE))
        [row] = service.get_status({"01P00A999999999": (8, "P8")})["devices"]

        assert row["bound"] is True
        assert row["printer_id"] is None
        assert row["printer_name"] is None


class TestLostBroker:
    def test_devices_count_as_offline_without_the_broker(self, printers):
        """Nothing reaches the device then, so its last values are stale."""
        service = _service()
        service.apply_message("panda_breath/AABBCC/state", json.dumps(STATE))
        service._on_disconnect(None, None, 0)

        assert service.state_for_printer(7)["online"] is False
        printers.schedule_status_broadcast.assert_called_with(7)

    @pytest.mark.asyncio
    async def test_switching_off_refreshes_the_cards(self, printers):
        service = _service()
        service.apply_message("panda_breath/AABBCC/state", json.dumps(STATE))
        printers.schedule_status_broadcast.reset_mock()

        await service.configure({"mqtt_enabled": True, "panda_breath_enabled": False, "mqtt_broker": "b"})

        printers.schedule_status_broadcast.assert_called_once_with(7)
        assert service.state_for_printer(7) is None


class TestConfigure:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "settings",
        [
            {"mqtt_enabled": False, "panda_breath_enabled": True, "mqtt_broker": "b"},
            {"mqtt_enabled": True, "panda_breath_enabled": False, "mqtt_broker": "b"},
            {"mqtt_enabled": True, "panda_breath_enabled": True, "mqtt_broker": ""},
        ],
    )
    async def test_off_unless_mqtt_and_panda_breath_are_on(self, printers, settings):
        service = _service()
        service.apply_message("panda_breath/AABBCC/state", json.dumps(STATE))
        with patch.object(service, "_connect") as connect:
            await service.configure(settings)

        connect.assert_not_called()
        assert service.enabled is False
        assert service.devices == {}

    @pytest.mark.asyncio
    async def test_on_connects_to_the_relay_broker(self):
        service = PandaBreathService()
        with patch.object(service, "_connect") as connect:
            await service.configure(
                {"mqtt_enabled": True, "panda_breath_enabled": True, "mqtt_broker": "broker", "mqtt_port": 1884}
            )

        connect.assert_called_once()
        assert service.enabled is True
        assert (service._broker, service._port) == ("broker", 1884)

    def test_subscribes_to_state_and_availability(self):
        service = PandaBreathService()
        client = MagicMock()
        service._on_connect(client, None, {}, 0)

        topics = [c.args[0] for c in client.subscribe.call_args_list]
        assert topics == ["panda_breath/+/state", "panda_breath/+/availability"]


class TestSendCommand:
    def test_publishes_one_value_on_the_device_command_topic(self):
        service = _service()
        service.client = MagicMock()
        service.client.publish.return_value = MagicMock(rc=0)
        service.connected = True

        assert service.send_command("AABBCC", "mode", "filament drying") is True
        service.client.publish.assert_called_once_with(
            "panda_breath/AABBCC/command", json.dumps({"mode": "filament drying"}), qos=1, retain=False
        )

    def test_not_connected_sends_nothing(self):
        service = _service()
        service.client = MagicMock()
        service.connected = False

        assert service.send_command("AABBCC", "work_on", "OFF") is False
        service.client.publish.assert_not_called()


class TestStatusBroadcast:
    def test_a_change_pushes_the_printer_status(self, printers):
        service = _service()
        msg = MagicMock(topic="panda_breath/AABBCC/state", payload=json.dumps(STATE).encode())
        service._on_message(None, None, msg)

        printers.schedule_status_broadcast.assert_called_once_with(7)


class TestWebSocketStatus:
    def test_printer_status_payload_carries_the_device(self):
        """The card is fed over the WebSocket; without the key there a merge
        would keep a device that has gone away."""
        from backend.app.services.bambu_mqtt import PrinterState
        from backend.app.services.printer_manager import printer_state_to_dict

        with patch("backend.app.services.printer_manager.panda_breath_service") as service:
            service.state_for_printer.return_value = {"device_id": "AABBCC"}
            assert printer_state_to_dict(PrinterState(), 7)["panda_breath"] == {"device_id": "AABBCC"}
            service.state_for_printer.return_value = None
            assert printer_state_to_dict(PrinterState(), 7)["panda_breath"] is None
        service.state_for_printer.assert_called_with(7)
