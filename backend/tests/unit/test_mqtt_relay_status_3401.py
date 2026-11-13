"""The relay's printer status topic carries what the REST status does (#3401).

Door, active extruder, tray, AMS, external spool and HMS errors were only
available by polling REST. The broker also gets a Last Will, so a crash does
not leave the retained "online" standing, and a printer dropping is published
even straight after a regular update.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.bambu_mqtt import HMSError, PrinterState
from backend.app.services.mqtt_relay import MQTTRelayService


@pytest.fixture
def relay():
    service = MQTTRelayService()
    service.enabled = True
    service.connected = True
    service.topic_prefix = "bambuddy"
    service._publish = MagicMock()
    return service


def _state(**kwargs) -> PrinterState:
    return PrinterState(connected=True, state="RUNNING", **kwargs)


def _published(relay) -> dict:
    topic, payload = relay._publish.call_args.args[:2]
    assert topic == "bambuddy/printers/SERIAL1/status"
    return payload


async def _send(relay, state, printer_id=1):
    await relay.on_printer_status(printer_id, state, "H2D", "SERIAL1")


class TestStatusPayload:
    async def test_door_extruder_and_tray_are_published(self, relay):
        await _send(relay, _state(door_open=True, active_extruder=1, tray_now=5))

        payload = _published(relay)
        assert payload["door_open"] is True
        assert payload["active_extruder"] == 1
        assert payload["tray_now"] == 5

    async def test_hms_errors_are_published_like_the_rest_status(self, relay):
        error = HMSError(code="0x0700_8010", attr=117473280, module=7, severity=2)
        await _send(relay, _state(hms_errors=[error]))

        errors = _published(relay)["hms_errors"]
        assert [e["code"] for e in errors] == ["0x0700_8010"]
        assert errors[0]["severity"] == 2
        assert "description" in errors[0]

    async def test_ams_and_external_spool_are_published(self, relay):
        raw = {
            "ams": [
                {
                    "id": 0,
                    "humidity": 4,
                    "temp": 25.0,
                    "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF", "tray_sub_brands": "PLA Basic"}],
                }
            ],
            "vt_tray": [{"id": 254, "tray_type": "ABS", "tray_color": "000000FF"}],
        }
        await _send(relay, _state(raw_data=raw))

        payload = _published(relay)
        assert payload["ams"][0]["tray"][0]["tray_type"] == "PLA"
        assert payload["vt_tray"][0]["tray_type"] == "ABS"

    async def test_a_printer_without_an_ams_publishes_null_and_empty_lists(self, relay):
        await _send(relay, _state())

        payload = _published(relay)
        assert payload["ams"] is None
        assert payload["vt_tray"] == []
        assert payload["hms_errors"] == []

    async def test_the_payload_stays_json_serialisable(self, relay):
        error = HMSError(code="0x0700_8010", attr=1, module=7, severity=2)
        await _send(relay, _state(hms_errors=[error], door_open=False, active_extruder=0, tray_now=255))

        json.dumps(_published(relay), default=str)

    async def test_existing_fields_are_unchanged(self, relay):
        await _send(relay, _state(progress=42.0))

        payload = _published(relay)
        assert payload["state"] == "RUNNING"
        assert payload["progress"] == 42.0
        assert payload["awaiting_plate_clear"] is False


class TestThrottle:
    async def test_a_second_update_within_a_second_is_dropped(self, relay):
        await _send(relay, _state())
        await _send(relay, _state(progress=50.0))

        assert relay._publish.call_count == 1

    async def test_a_printer_dropping_is_published_straight_after_an_update(self, relay):
        await _send(relay, _state())
        await _send(relay, PrinterState(connected=False, state="unknown"))

        assert relay._publish.call_count == 2
        assert _published(relay)["connected"] is False

    async def test_a_printer_coming_back_is_published_straight_after(self, relay):
        await _send(relay, PrinterState(connected=False))
        await _send(relay, _state())

        assert relay._publish.call_count == 2
        assert _published(relay)["connected"] is True

    async def test_each_printer_is_throttled_on_its_own(self, relay):
        await _send(relay, _state(), printer_id=1)
        await _send(relay, _state(), printer_id=2)

        assert relay._publish.call_count == 2


class TestLastWill:
    async def test_connect_sets_a_retained_offline_will_before_connecting(self):
        service = MQTTRelayService()
        service.topic_prefix = "home/bb"
        client = MagicMock()
        order = []
        client.will_set.side_effect = lambda *a, **k: order.append("will")
        client.connect_async.side_effect = lambda *a, **k: order.append("connect")

        with (
            patch("backend.app.services.mqtt_relay.mqtt.Client", return_value=client),
            patch("backend.app.services.mqtt_relay.asyncio.sleep", new=AsyncMock()),
        ):
            await service._connect("broker.local", 1883, "", "", False)

        client.will_set.assert_called_once()
        args, kwargs = client.will_set.call_args
        assert args[0] == "home/bb/status"
        assert json.loads(args[1]) == {"status": "offline"}
        assert kwargs == {"qos": 1, "retain": True}
        assert order == ["will", "connect"]
