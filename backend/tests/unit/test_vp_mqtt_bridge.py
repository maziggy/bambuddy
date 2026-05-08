"""Tests for the VP MQTT bridge — non-proxy mirror of target printer state to slicer."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.virtual_printer.mqtt_bridge import MQTTBridge, _ip_to_uint32_le
from backend.app.services.virtual_printer.mqtt_server import SimpleMQTTServer

H2D_SERIAL = "0948BB540200427"
VP_SERIAL = "09400A391800003"
H2D_IP = "192.168.255.133"
VP_IP = "192.168.255.16"


def _make_server(serial: str = VP_SERIAL, bind_address: str = VP_IP) -> SimpleMQTTServer:
    return SimpleMQTTServer(
        serial=serial,
        access_code="deadbeef",
        cert_path=Path("/tmp/unused.crt"),  # nosec B108
        key_path=Path("/tmp/unused.key"),  # nosec B108
        model="O1D",
        bind_address=bind_address,
    )


def _make_paho_client(
    serial: str = H2D_SERIAL,
    ip: str = H2D_IP,
    *,
    connected: bool = True,
) -> MagicMock:
    """Build a mock BambuMQTTClient that satisfies MQTTBridge's interface."""
    client = MagicMock()
    client.serial_number = serial
    client.ip_address = ip
    client.state = MagicMock()
    client.state.connected = connected
    client.publish_raw = MagicMock(return_value=True)
    client._raw_handlers: list = []

    def _register(handler):
        client._raw_handlers.append(handler)

    def _unregister(handler):
        if handler in client._raw_handlers:
            client._raw_handlers.remove(handler)

    client.register_raw_message_handler.side_effect = _register
    client.unregister_raw_message_handler.side_effect = _unregister
    # No-op for _request_version / request_status_update so the post-bind nudge doesn't crash.
    client._request_version = MagicMock()
    client.request_status_update = MagicMock()
    return client


def _make_printer_manager(client) -> MagicMock:
    pm = MagicMock()
    pm.get_client = MagicMock(return_value=client)
    return pm


def _make_bridge(server: SimpleMQTTServer, target: MagicMock | None = None) -> MQTTBridge:
    target = target if target is not None else _make_paho_client()
    pm = _make_printer_manager(target)
    return MQTTBridge(
        vp_id=1,
        vp_name="vp1",
        vp_serial=VP_SERIAL,
        target_printer_id=42,
        mqtt_server=server,
        printer_manager=pm,
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestBridgeLifecycle:
    @pytest.mark.asyncio
    async def test_start_registers_handler_on_target_client(self):
        target = _make_paho_client()
        bridge = _make_bridge(_make_server(), target)
        await bridge.start()
        assert len(target._raw_handlers) == 1
        assert bridge.is_active is True
        await bridge.stop()
        assert len(target._raw_handlers) == 0

    @pytest.mark.asyncio
    async def test_start_with_no_target_client_does_not_crash(self):
        pm = MagicMock()
        pm.get_client = MagicMock(return_value=None)
        bridge = MQTTBridge(
            vp_id=1,
            vp_name="vp1",
            vp_serial=VP_SERIAL,
            target_printer_id=42,
            mqtt_server=_make_server(),
            printer_manager=pm,
        )
        await bridge.start()
        assert bridge.is_active is False
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_resolve_rebinds_when_paho_client_replaced(self):
        """BambuMQTTClient is destroyed and recreated on connect_printer; bridge must rebind."""
        old_client = _make_paho_client(serial="REAL_OLD")
        new_client = _make_paho_client(serial="REAL_NEW")
        pm = _make_printer_manager(old_client)
        bridge = MQTTBridge(
            vp_id=1,
            vp_name="vp1",
            vp_serial=VP_SERIAL,
            target_printer_id=42,
            mqtt_server=_make_server(),
            printer_manager=pm,
        )
        await bridge.start()
        assert len(old_client._raw_handlers) == 1
        assert bridge._target_serial == "REAL_OLD"

        pm.get_client.return_value = new_client
        bridge._resolve_client()
        assert len(old_client._raw_handlers) == 0
        assert len(new_client._raw_handlers) == 1
        assert bridge._target_serial == "REAL_NEW"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_post_bind_nudge_requests_version_and_status(self):
        target = _make_paho_client()
        bridge = _make_bridge(_make_server(), target)
        await bridge.start()
        target._request_version.assert_called_once()
        target.request_status_update.assert_called_once()
        await bridge.stop()


# ---------------------------------------------------------------------------
# Caching: push_status
# ---------------------------------------------------------------------------


class TestPushStatusCache:
    """push_status snapshots feed `_send_status_report` via the cache, not a fan-out."""

    @pytest.mark.asyncio
    async def test_push_status_is_cached_not_fanned_out(self):
        server = _make_server()
        server.push_raw_to_clients = AsyncMock()
        bridge = _make_bridge(server)
        await bridge.start()

        payload = json.dumps({"print": {"command": "push_status", "ams": {"ams": []}, "gcode_state": "IDLE"}}).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", payload)
        await asyncio.sleep(0.01)

        server.push_raw_to_clients.assert_not_awaited()
        cached = bridge.get_latest_print_state()
        assert cached is not None
        assert cached["command"] == "push_status"
        assert cached["gcode_state"] == "IDLE"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_serial_rewritten_in_cached_push(self):
        server = _make_server()
        bridge = _make_bridge(server)
        await bridge.start()

        payload = json.dumps(
            {
                "print": {
                    "command": "push_status",
                    "upgrade_state": {"sn": H2D_SERIAL, "status": "IDLE"},
                }
            }
        ).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", payload)
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        assert cached["upgrade_state"]["sn"] == VP_SERIAL

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_net_info_ip_rewritten_to_vp_ip(self):
        """BambuStudio reads `net.info[].ip` (LE uint32) for the FTP destination —
        must be rewritten to the VP's bind IP or the slicer bypasses the VP."""
        server = _make_server(bind_address=VP_IP)
        bridge = _make_bridge(server)
        await bridge.start()

        h2d_le = _ip_to_uint32_le(H2D_IP)
        vp_le = _ip_to_uint32_le(VP_IP)
        payload = json.dumps(
            {
                "print": {
                    "command": "push_status",
                    "net": {"info": [{"ip": h2d_le, "mask": 0xFFFFFF}, {"ip": 0, "mask": 0}]},
                }
            }
        ).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", payload)
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        assert cached["net"]["info"][0]["ip"] == vp_le
        assert cached["net"]["info"][1]["ip"] == 0  # untouched

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_request_topic_message_is_ignored(self):
        server = _make_server()
        bridge = _make_bridge(server)
        await bridge.start()

        payload = json.dumps({"print": {"command": "push_status"}}).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/request", payload)
        await asyncio.sleep(0.01)

        assert bridge.get_latest_print_state() is None
        await bridge.stop()


# ---------------------------------------------------------------------------
# Caching: get_version response
# ---------------------------------------------------------------------------


class TestVersionCache:
    @pytest.mark.asyncio
    async def test_get_version_response_caches_modules(self):
        server = _make_server()
        bridge = _make_bridge(server)
        await bridge.start()

        payload = json.dumps(
            {
                "info": {
                    "command": "get_version",
                    "module": [
                        {"name": "ota", "sn": H2D_SERIAL, "sw_ver": "01.03.00.00"},
                        {"name": "n3f/0", "sn": "AMS_HW_1", "sw_ver": "04.00.21.87"},
                    ],
                }
            }
        ).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", payload)
        await asyncio.sleep(0.01)

        modules = bridge.get_latest_version_modules()
        assert modules is not None
        assert len(modules) == 2
        # Device-level sn rewritten; AMS-hardware sn left alone.
        assert modules[0]["sn"] == VP_SERIAL
        assert modules[1]["sn"] == "AMS_HW_1"

        await bridge.stop()


# ---------------------------------------------------------------------------
# Selective fan-out (everything that's not push_status / get_version)
# ---------------------------------------------------------------------------


class TestCommandResponseFanout:
    @pytest.mark.asyncio
    async def test_extrusion_cali_get_response_is_fanned_out(self):
        """Slicer's extrusion_cali_get goes to the printer; the printer's response
        must reach the slicer or BambuStudio's pre-flight blocks Send."""
        server = _make_server()
        server.push_raw_to_clients = AsyncMock()
        bridge = _make_bridge(server)
        await bridge.start()

        body = json.dumps({"print": {"command": "extrusion_cali_get", "filaments": []}}).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", body)
        await asyncio.sleep(0.01)

        server.push_raw_to_clients.assert_awaited_once()
        topic, _payload = server.push_raw_to_clients.await_args.args
        assert topic == f"device/{VP_SERIAL}/report"

        await bridge.stop()


# ---------------------------------------------------------------------------
# Forwarding: slicer → printer
# ---------------------------------------------------------------------------


class TestForwardToPrinter:
    @pytest.mark.asyncio
    async def test_forward_publishes_to_real_serial_request_topic(self):
        target = _make_paho_client()
        bridge = _make_bridge(_make_server(), target)
        await bridge.start()

        ok = bridge.forward_to_printer({"print": {"command": "stop"}})
        assert ok is True
        target.publish_raw.assert_called_once()
        topic, payload = target.publish_raw.call_args.args
        assert topic == f"device/{H2D_SERIAL}/request"
        assert json.loads(payload) == {"print": {"command": "stop"}}

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_forward_returns_false_when_not_bound(self):
        pm = MagicMock()
        pm.get_client = MagicMock(return_value=None)
        bridge = MQTTBridge(
            vp_id=1,
            vp_name="vp1",
            vp_serial=VP_SERIAL,
            target_printer_id=42,
            mqtt_server=_make_server(),
            printer_manager=pm,
        )
        await bridge.start()
        assert bridge.forward_to_printer({"print": {"command": "stop"}}) is False
        await bridge.stop()


# ---------------------------------------------------------------------------
# SimpleMQTTServer status response: cached-as-base
# ---------------------------------------------------------------------------


class TestStatusReportCachedAsBase:
    """`_send_status_report` sends near-byte-identical real data when bridge cache exists."""

    def _capture_published(self, server: SimpleMQTTServer):
        """Wrap _publish_to_report to capture (topic, payload_dict)."""
        published: list = []

        async def _capture(writer, payload, serial=""):
            published.append((serial or server.serial, payload))

        server._publish_to_report = _capture  # type: ignore[assignment]
        return published

    @pytest.mark.asyncio
    async def test_uses_real_cache_when_bridge_active(self):
        server = _make_server()
        bridge = MagicMock()
        bridge.get_latest_print_state.return_value = {
            "command": "push_status",
            "msg": 0,
            "ams": {"ams": [{"id": "0"}]},
            "device": {"extruder": {"info": [{"id": 0}, {"id": 1}]}},
            "nozzle_diameter": "0.4",
            "nozzle_type": "HH01",  # real H2D value, not synthetic 'hardened_steel'
        }
        server.set_bridge(bridge)
        published = self._capture_published(server)

        await server._send_status_report(MagicMock())
        assert len(published) == 1
        _serial, payload = published[0]
        # AMS / device / nozzle_type all from cache
        assert payload["print"]["nozzle_type"] == "HH01"
        assert payload["print"]["device"]["extruder"]["info"][1]["id"] == 1
        # Protocol fields under our control
        assert payload["print"]["command"] == "push_status"
        assert payload["print"]["gcode_state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_falls_back_to_synthetic_when_no_cache(self):
        server = _make_server()
        bridge = MagicMock()
        bridge.get_latest_print_state.return_value = None
        server.set_bridge(bridge)
        published = self._capture_published(server)

        await server._send_status_report(MagicMock())
        assert len(published) == 1
        _serial, payload = published[0]
        # Synthetic baseline has stub fields like nozzle_type='hardened_steel'
        # and a `storage` field that the real H2D doesn't push.
        assert payload["print"]["nozzle_type"] == "hardened_steel"
        assert "storage" in payload["print"]

    @pytest.mark.asyncio
    async def test_storage_indicators_overlaid_for_send_preflight(self):
        """#1228: P1S/A1-class firmware doesn't always include the SD/storage
        fields BambuStudio's "Send" pre-flight reads. Without these the
        slicer rejects with 'storage needs to be inserted' before even
        attempting FTP. The cached-as-base path now overlays them so the
        pre-flight passes regardless of what the real printer reports.
        """
        server = _make_server()
        bridge = MagicMock()
        # Real P1S push without SD card inserted: home_flag has other bits set
        # but the SD bit (0x100) is clear; sdcard is False; no storage field.
        bridge.get_latest_print_state.return_value = {
            "command": "push_status",
            "msg": 0,
            "home_flag": 0x42,
            "sdcard": False,
        }
        server.set_bridge(bridge)
        published = self._capture_published(server)

        await server._send_status_report(MagicMock())
        _serial, payload = published[0]
        # SD bit ORed onto whatever was there — other bits preserved.
        assert payload["print"]["home_flag"] & 0x100 == 0x100
        assert payload["print"]["home_flag"] & 0x42 == 0x42
        # Force-set so a False from the printer doesn't trip the pre-flight.
        assert payload["print"]["sdcard"] is True
        # storage was missing — the overlay must inject a non-empty default.
        assert "storage" in payload["print"]
        assert payload["print"]["storage"]["free"] > 0
        assert payload["print"]["storage"]["total"] > 0

    @pytest.mark.asyncio
    async def test_storage_indicators_preserve_real_storage_when_present(self):
        """When the real printer DOES report a storage block, pass it through
        unchanged (the overlay only fills in the missing field, not overrides).
        """
        server = _make_server()
        bridge = MagicMock()
        real_storage = {"free": 12345, "total": 67890}
        bridge.get_latest_print_state.return_value = {
            "command": "push_status",
            "msg": 0,
            "home_flag": 0x100,  # SD bit already set on the real printer
            "sdcard": True,
            "storage": real_storage,
        }
        server.set_bridge(bridge)
        published = self._capture_published(server)

        await server._send_status_report(MagicMock())
        _serial, payload = published[0]
        # SD bit OR is idempotent — already-set bit stays set.
        assert payload["print"]["home_flag"] == 0x100
        assert payload["print"]["sdcard"] is True
        # Real values pass through, NOT the synthetic defaults.
        assert payload["print"]["storage"] == real_storage

    @pytest.mark.asyncio
    async def test_overrides_protocol_fields_even_when_cache_present(self):
        """Cached value's gcode_state must NOT win over our local upload-state-machine value."""
        server = _make_server()
        server._gcode_state = "PREPARE"
        server._current_file = "foo.3mf"
        bridge = MagicMock()
        bridge.get_latest_print_state.return_value = {
            "command": "push_status",
            "gcode_state": "IDLE",  # printer is idle; we are mid-FTP-upload
            "gcode_file": "",
            "gcode_file_prepare_percent": "0",
        }
        server.set_bridge(bridge)
        published = self._capture_published(server)

        await server._send_status_report(MagicMock())
        _serial, payload = published[0]
        assert payload["print"]["gcode_state"] == "PREPARE"
        assert payload["print"]["gcode_file"] == "foo.3mf"


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------


class TestWireFormat:
    """BambuStudio's Send pre-flight rejects compact JSON — must match real printer's
    indented format (32K bytes for an idle H2D vs 14K compact)."""

    @pytest.mark.asyncio
    async def test_publish_uses_indent_4_json_format(self):
        server = _make_server()
        captured: list = []

        async def _capture_drain():
            pass

        writer = MagicMock()
        writer.write = lambda data: captured.append(data)
        writer.drain = AsyncMock()

        await server._publish_to_report(writer, {"print": {"command": "push_status", "ams": {}}})

        body = b"".join(captured)
        assert b'\n    "print"' in body, "publish_to_report must use indent=4 JSON"


# ---------------------------------------------------------------------------
# Routing: _handle_publish
# ---------------------------------------------------------------------------


class TestPublishRouting:
    """Slicer-issued commands: project_file/gcode_file handled locally, everything
    else forwarded to the real printer."""

    def _build_publish_payload(self, topic: str, body: bytes) -> bytes:
        topic_bytes = topic.encode("utf-8")
        return bytes([len(topic_bytes) >> 8, len(topic_bytes) & 0xFF]) + topic_bytes + body

    def _attach_active_bridge(self, server: SimpleMQTTServer) -> MagicMock:
        bridge = MagicMock()
        bridge.is_active = True
        bridge.forward_to_printer = MagicMock(return_value=True)
        server.set_bridge(bridge)
        return bridge

    @pytest.mark.asyncio
    async def test_project_file_handled_locally_not_forwarded(self):
        server = _make_server()
        bridge = self._attach_active_bridge(server)
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        body = json.dumps({"print": {"command": "project_file", "subtask_name": "f", "sequence_id": "1"}}).encode()
        payload = self._build_publish_payload(f"device/{VP_SERIAL}/request", body)

        with patch.object(server, "_send_print_response", new=AsyncMock()) as mock_resp:
            await server._handle_publish(0x30, payload, writer, "client1")

        bridge.forward_to_printer.assert_not_called()
        mock_resp.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_gcode_file_handled_locally_not_forwarded(self):
        server = _make_server()
        bridge = self._attach_active_bridge(server)
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        body = json.dumps({"print": {"command": "gcode_file", "subtask_name": "f.gcode", "sequence_id": "1"}}).encode()
        payload = self._build_publish_payload(f"device/{VP_SERIAL}/request", body)

        with patch.object(server, "_send_print_response", new=AsyncMock()):
            await server._handle_publish(0x30, payload, writer, "client1")

        bridge.forward_to_printer.assert_not_called()

    @pytest.mark.asyncio
    async def test_pushall_handled_locally_not_forwarded(self):
        server = _make_server()
        bridge = self._attach_active_bridge(server)
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        body = json.dumps({"pushing": {"command": "pushall", "sequence_id": "0"}}).encode()
        payload = self._build_publish_payload(f"device/{VP_SERIAL}/request", body)

        with patch.object(server, "_send_status_report", new=AsyncMock()) as mock_status:
            await server._handle_publish(0x30, payload, writer, "client1")

        # Synthetic answer fires (fast, low latency); no forwarding (the
        # cache already mirrors what the printer would respond with).
        bridge.forward_to_printer.assert_not_called()
        mock_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_version_handled_locally_not_forwarded(self):
        server = _make_server()
        bridge = self._attach_active_bridge(server)
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        body = json.dumps({"info": {"command": "get_version", "sequence_id": "1"}}).encode()
        payload = self._build_publish_payload(f"device/{VP_SERIAL}/request", body)

        with patch.object(server, "_send_version_response", new=AsyncMock()) as mock_ver:
            await server._handle_publish(0x30, payload, writer, "client1")

        bridge.forward_to_printer.assert_not_called()
        mock_ver.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_extrusion_cali_get_is_forwarded(self):
        """extrusion_cali_get fetches per-filament k-profiles — must reach the printer."""
        server = _make_server()
        bridge = self._attach_active_bridge(server)
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        body = json.dumps(
            {
                "print": {
                    "command": "extrusion_cali_get",
                    "filament_id": "",
                    "nozzle_diameter": "0.4",
                    "sequence_id": "5",
                }
            }
        ).encode()
        payload = self._build_publish_payload(f"device/{VP_SERIAL}/request", body)

        await server._handle_publish(0x30, payload, writer, "client1")

        bridge.forward_to_printer.assert_called_once()
        forwarded = bridge.forward_to_printer.call_args.args[0]
        assert forwarded["print"]["command"] == "extrusion_cali_get"

    @pytest.mark.asyncio
    async def test_print_stop_is_forwarded(self):
        server = _make_server()
        bridge = self._attach_active_bridge(server)
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        body = json.dumps({"print": {"command": "stop", "sequence_id": "5"}}).encode()
        payload = self._build_publish_payload(f"device/{VP_SERIAL}/request", body)

        await server._handle_publish(0x30, payload, writer, "client1")

        bridge.forward_to_printer.assert_called_once()


# ---------------------------------------------------------------------------
# IP encoding helper
# ---------------------------------------------------------------------------


class TestIpEncoding:
    def test_le_uint32_matches_real_h2d_capture(self):
        # 192.168.255.133 captured from real H2D's net.info[0].ip = 2248124608
        assert _ip_to_uint32_le("192.168.255.133") == 2248124608

    def test_vp_ip_round_trip(self):
        assert _ip_to_uint32_le("192.168.255.16") == 285190336

    def test_invalid_ip_raises(self):
        with pytest.raises(ValueError):
            _ip_to_uint32_le("not.an.ip.actually")
