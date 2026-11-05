"""A printer command is never sent again on a later connection (#3454).

When the connection to a printer dropped and paho reconnected the same client,
paho re-sent every QoS 1 publish still in its outbox. Bambu's broker doesn't
reliably PUBACK (#1164), so the outbox held commands from hours ago: a `stop`
from 09:25 cancelled the print running at 13:29, and a `project_file` raised
"Device is busy" (0500_4004). The client now empties the outbox in on_connect,
which paho calls before it re-sends.

These tests drive a real paho client through its own reconnect() and CONNACK
handling, with only the socket stubbed, so a paho upgrade that changes either
fails here instead of bringing the replay back.
"""

import json
import struct
import time
from unittest.mock import MagicMock, patch

import paho.mqtt.client as mqtt
import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.app.utils.paho_teardown import retire_paho_client

STOP = json.dumps({"print": {"command": "stop", "sequence_id": "1"}})
PROJECT_FILE = json.dumps({"print": {"command": "project_file", "sequence_id": "2"}})


def _paho() -> mqtt.Client:
    return mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="bambuddy_test_3454",
        protocol=mqtt.MQTTv311,
    )


def _queue_unacked(client: mqtt.Client, topic: str, *payloads: str) -> None:
    """Commands sent on the old connection whose PUBACK never came."""
    for payload in payloads:
        client.publish(topic, payload, qos=1)  # no socket: stays in the outbox
    for message in client._out_messages.values():
        message.state = mqtt.mqtt_ms_wait_for_puback
        client._inflight_messages += 1


def _reconnect(client: mqtt.Client) -> list[str]:
    """paho's own reconnect() of the same client, then its CONNACK handling,
    with only the socket stubbed; returns the payloads put on the wire."""
    sent: list[str] = []

    def send_publish(mid, topic, payload=b"", *args, **kwargs):
        sent.append(payload.decode() if isinstance(payload, bytes) else payload)
        return mqtt.MQTTErrorCode.MQTT_ERR_SUCCESS

    sock = MagicMock()
    sock.send.side_effect = len  # every write goes out whole
    if not client._host:
        client.connect_async("192.0.2.1", 8883)
    with (
        patch.object(client, "_create_socket", return_value=sock),
        patch.object(client, "_send_connect", return_value=mqtt.MQTTErrorCode.MQTT_ERR_SUCCESS),
        patch.object(client, "_send_publish", side_effect=send_publish),
    ):
        assert client.reconnect() == mqtt.MQTTErrorCode.MQTT_ERR_SUCCESS
        client._in_packet = {"remaining_length": 2, "packet": struct.pack("!BB", 0, 0)}
        client._handle_connack()
    return sent


@pytest.fixture
def printer():
    printer = BambuMQTTClient(
        ip_address="192.168.1.100", serial_number="SN3454", access_code="12345678", model="A1 mini"
    )
    paho = _paho()
    paho.on_connect = printer._on_connect
    printer._client = paho
    return printer


def _commands(sent: list[str]) -> list[str]:
    return [json.loads(p).get("print", json.loads(p).get("pushing", {})).get("command") for p in sent]


def test_paho_re_sends_the_old_outbox_after_on_connect():
    # paho's own behaviour, which the fix relies on: the outbox survives a
    # reconnect and goes out again, only after on_connect has run, and the
    # in-flight count has been reset to 0 by then.
    client = _paho()
    seen_by_on_connect: list[tuple[int, int]] = []
    client.on_connect = lambda c, *a: seen_by_on_connect.append((len(c._out_messages), c._inflight_messages))
    _queue_unacked(client, "device/SN3454/request", STOP, PROJECT_FILE)

    sent = _reconnect(client)

    assert seen_by_on_connect == [(2, 0)]
    assert sent == [STOP, PROJECT_FILE]


def test_a_stale_stop_and_project_file_are_not_sent_again(printer):
    _queue_unacked(printer._client, printer.topic_publish, STOP, PROJECT_FILE)

    sent = _reconnect(printer._client)

    assert "stop" not in _commands(sent)
    assert "project_file" not in _commands(sent)
    assert printer.state.connected is True


def test_commands_the_new_connection_sends_are_kept(printer):
    _queue_unacked(printer._client, printer.topic_publish, STOP)

    sent = _reconnect(printer._client)

    # on_connect asks for a full status push on the new connection.
    assert "pushall" in _commands(sent)
    assert sent.count(STOP) == 0
    # The outbox holds only the new connection's commands, each counted once
    # as in flight, so paho's ceiling isn't eaten by dropped ones.
    outbox = list(printer._client._out_messages.values())
    assert outbox
    assert STOP not in [m.payload.decode() for m in outbox]
    assert printer._client._inflight_messages == len(outbox)


def test_the_virtual_printer_bridges_commands_are_dropped_too(printer):
    # Slicer commands forwarded by a virtual printer go through publish_raw.
    printer.publish_raw(printer.topic_publish, PROJECT_FILE)
    for message in printer._client._out_messages.values():
        message.state = mqtt.mqtt_ms_wait_for_puback

    assert PROJECT_FILE not in _reconnect(printer._client)


def test_dropping_is_logged_with_the_count(printer, caplog):
    _queue_unacked(printer._client, printer.topic_publish, STOP, PROJECT_FILE)

    with caplog.at_level("INFO", logger="backend.app.services.bambu_mqtt"):
        _reconnect(printer._client)

    assert "Dropped 2 command(s) queued on the previous MQTT connection" in caplog.text


def test_paho_without_those_internals_warns(printer, caplog):
    class OtherPaho:
        def subscribe(self, *a, **k):
            return (mqtt.MQTT_ERR_SUCCESS, 1)

        def publish(self, *a, **k):
            return mqtt.MQTTMessageInfo(1)

    with caplog.at_level("WARNING", logger="backend.app.services.bambu_mqtt"):
        printer._drop_previous_connection_commands(OtherPaho())

    assert "Cannot clear paho's outbox on reconnect" in caplog.text


def test_a_retired_client_keeps_no_outbox_to_replay():
    # A client retired while stuck in its TLS handshake can still finish it
    # and connect; its on_connect is gone, so the outbox must be too.
    client = _paho()
    _queue_unacked(client, "device/SN3454/request", STOP)
    with patch.object(client, "loop_stop"), patch.object(client, "disconnect"):
        retire_paho_client(client, "SN3454")
        for _ in range(200):
            if not client._out_messages:
                break
            time.sleep(0.01)

    assert STOP not in _reconnect(client)
