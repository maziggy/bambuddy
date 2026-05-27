"""Tests for Virtual Printer MQTT server."""

import ast
import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.virtual_printer.mqtt_server import SimpleMQTTServer


class TestMQTTServerNoGlobalState:
    """Ensure MQTT server doesn't set global asyncio state."""

    def test_no_global_exception_handler(self):
        """MQTT server must not call set_exception_handler().

        set_exception_handler() is global to the event loop. When multiple
        VP instances run, each would overwrite the previous handler,
        causing lost error context and spurious 'Unhandled exception in
        client_connected_cb' messages.
        """
        source = inspect.getsource(SimpleMQTTServer)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "set_exception_handler":
                raise AssertionError(
                    "SimpleMQTTServer must not call set_exception_handler(). "
                    "It overwrites the global asyncio exception handler, "
                    "breaking multi-VP setups."
                )


def _make_server(serial: str = "01P00A391800001") -> SimpleMQTTServer:
    """Build a SimpleMQTTServer with dummy cert paths (start() is never called)."""
    return SimpleMQTTServer(
        serial=serial,
        access_code="deadbeef",
        cert_path=Path("/tmp/unused.crt"),  # nosec B108
        key_path=Path("/tmp/unused.key"),  # nosec B108
        model="C12",
    )


class TestExtractSerialFromTopic:
    """_extract_serial_from_topic should pull the serial out of device topics."""

    @pytest.mark.parametrize(
        "topic,expected",
        [
            ("device/01P00A391800001/request", "01P00A391800001"),
            ("device/09400A391800003/report", "09400A391800003"),
            ("device/00M00A391800004/request/subpath", "00M00A391800004"),
        ],
    )
    def test_valid_topics(self, topic, expected):
        assert SimpleMQTTServer._extract_serial_from_topic(topic) == expected

    @pytest.mark.parametrize(
        "topic",
        [
            "",
            "device/",
            "device//request",  # empty serial
            "notdevice/01P00A/request",
            "random",
        ],
    )
    def test_invalid_topics(self, topic):
        assert SimpleMQTTServer._extract_serial_from_topic(topic) is None


def _build_publish_payload(topic: str, message: dict) -> bytes:
    """Build the MQTT PUBLISH packet *payload* (past the fixed header byte)."""
    topic_bytes = topic.encode("utf-8")
    message_bytes = json.dumps(message).encode("utf-8")
    return len(topic_bytes).to_bytes(2, "big") + topic_bytes + message_bytes


class TestPublishHandlerAdaptiveSerial:
    """#927: `_handle_publish` must accept any `device/*/request` topic from an
    authenticated client and use the topic's serial for all responses."""

    def test_handle_publish_accepts_mismatched_serial(self):
        """Prior behavior silently dropped publishes whose topic serial didn't
        equal self.serial. After the fix the handler must run and learn the
        client's serial.
        """
        server = _make_server(serial="01P00A391800001")  # synthetic VP serial
        server._client_serials["test-client"] = server.serial  # simulate post-CONNECT

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        # Slicer publishes with a *different* serial — the exact bug from #927.
        topic = "device/01P00AABCDEFGHI/request"
        payload = _build_publish_payload(topic, {"info": {"command": "get_version", "sequence_id": "42"}})

        asyncio.run(server._handle_publish(0x30, payload, writer, "test-client"))

        # Learned the client's serial.
        assert server._client_serials["test-client"] == "01P00AABCDEFGHI"

        # Wrote at least one packet to the slicer (the version response).
        assert writer.write.called
        all_bytes = b"".join(call.args[0] for call in writer.write.call_args_list)
        # Response topic must contain the *client's* serial, not self.serial.
        assert b"device/01P00AABCDEFGHI/report" in all_bytes
        assert b"device/01P00A391800001/report" not in all_bytes
        # Response body carries get_version with the client's serial as sn.
        assert b'"command": "get_version"' in all_bytes
        assert b'"sn": "01P00AABCDEFGHI"' in all_bytes

    def test_handle_publish_ignores_non_request_topics(self):
        server = _make_server()
        server._client_serials["c1"] = server.serial
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        payload = _build_publish_payload(
            "device/01P00AABCDEFGHI/report",  # /report, not /request
            {"pushing": {"command": "pushall"}},
        )
        asyncio.run(server._handle_publish(0x30, payload, writer, "c1"))

        assert not writer.write.called  # no response
        # Client serial unchanged
        assert server._client_serials["c1"] == server.serial

    def test_handle_publish_pushall_uses_client_serial(self):
        """pushall → status_report must be sent on the client's subscribed topic."""
        server = _make_server(serial="01P00A391800001")
        server._client_serials["c1"] = server.serial

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        payload = _build_publish_payload(
            "device/CUSTOMSERIAL123/request",
            {"pushing": {"command": "pushall", "sequence_id": "1"}},
        )
        asyncio.run(server._handle_publish(0x30, payload, writer, "c1"))

        all_bytes = b"".join(call.args[0] for call in writer.write.call_args_list)
        assert b"device/CUSTOMSERIAL123/report" in all_bytes
        assert b'"command": "push_status"' in all_bytes
        assert server._client_serials["c1"] == "CUSTOMSERIAL123"

    def test_handle_publish_tolerates_null_terminated_payload(self):
        """#927: OrcaSlicer on Linux appends the C-string \\0 to MQTT payloads.
        The handler must still parse and respond rather than silently dropping."""
        server = _make_server(serial="01P00A391800001")
        server._client_serials["c1"] = server.serial

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        topic = "device/01P00A391800001/request"
        topic_bytes = topic.encode("utf-8")
        # Real-world bytes captured from EdwardChamberlain's support log: the
        # JSON ends with an extra \x00 that strict json.loads rejects.
        message_bytes = b'{"pushing":{"command":"pushall","sequence_id":"7"}}\x00'
        payload = len(topic_bytes).to_bytes(2, "big") + topic_bytes + message_bytes

        asyncio.run(server._handle_publish(0x30, payload, writer, "c1"))

        all_bytes = b"".join(call.args[0] for call in writer.write.call_args_list)
        assert b"device/01P00A391800001/report" in all_bytes
        assert b'"command": "push_status"' in all_bytes


class TestClientSerialLifecycle:
    """_client_serials must be cleaned up on disconnect/stop to avoid leaks."""

    def test_stop_clears_client_serials(self):
        server = _make_server()
        server._client_serials["a"] = "X"
        server._client_serials["b"] = "Y"
        # stop() is async but we only need to cover the clear() path; run a minimal version
        asyncio.run(server.stop())
        assert server._client_serials == {}
