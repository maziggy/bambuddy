"""Tests for the VP MQTT bridge — non-proxy mirror of target printer state to slicer."""

import asyncio
import json
import logging
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.virtual_printer.mqtt_bridge import (
    MQTTBridge,
    _ip_to_uint32_le,
    _resolve_host_interface_for_target,
    _resolve_target_to_ipv4,
)
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

    @pytest.mark.asyncio
    async def test_post_bind_nudge_skipped_when_target_not_connected(self):
        """#1721: the bridge can attach before the real printer's MQTT TLS
        handshake completes. Calling request_status_update on a disconnected
        client logs WARNING (bambu_mqtt.py:3224); on A1 firmware that
        reconnects aggressively, every bind cycle pollutes the support bundle
        with a benign line. The bridge must check state.connected before
        nudging — the next periodic pushall picks up the cache anyway.
        """
        target = _make_paho_client(connected=False)
        bridge = _make_bridge(_make_server(), target)
        await bridge.start()
        target._request_version.assert_not_called()
        target.request_status_update.assert_not_called()
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
    async def test_net_info_ip_rewritten_for_unknown_secondary_interface(self):
        """Regression for #1429: real printers (X1C / H2D Pro) report multiple
        active interfaces (WiFi + Ethernet) — only ONE matches the IP Bambuddy
        tracks. The rewrite must catch every non-zero entry, not just the one
        whose IP equals `_target_ip_uint32_le`, or the slicer's FTP fallback
        path leaks straight to the real printer."""
        server = _make_server(bind_address=VP_IP)
        bridge = _make_bridge(server)
        await bridge.start()

        h2d_le = _ip_to_uint32_le(H2D_IP)
        # A second IP Bambuddy never saw (e.g. printer's ethernet interface
        # while Bambuddy talks over wifi).
        other_le = _ip_to_uint32_le("192.168.99.42")
        vp_le = _ip_to_uint32_le(VP_IP)
        payload = json.dumps(
            {
                "print": {
                    "command": "push_status",
                    "net": {
                        "info": [
                            {"ip": h2d_le, "mask": 0xFFFFFF},
                            {"ip": other_le, "mask": 0xFFFFFF},
                            {"ip": 0, "mask": 0},
                        ]
                    },
                }
            }
        ).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", payload)
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        assert cached["net"]["info"][0]["ip"] == vp_le
        assert cached["net"]["info"][1]["ip"] == vp_le  # secondary interface also rewritten
        assert cached["net"]["info"][2]["ip"] == 0  # placeholder untouched

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_late_arriving_printer_ip_rewrites_existing_cache(self):
        """Regression for #1429: if the printer's `ip_address` is empty at
        first bind (DB row stale, or the client object exists before the
        first SSDP refresh fills it in), the rewrite stays disabled and the
        first cached push poisons the cache with the real-printer IP.
        Once `ip_address` becomes valid, the next refresh tick must (a) arm
        the encoding and (b) sweep the cached `net.info[].ip` so the slicer
        sees the rewritten value on its next pull. Without the sweep the
        sticky-key preservation keeps the poisoned value alive across
        every subsequent incremental push."""
        server = _make_server(bind_address=VP_IP)
        # Bind to a client whose ip_address is empty at start — simulates the
        # late-arrival path.
        target = _make_paho_client(ip="")
        bridge = _make_bridge(server, target)
        await bridge.start()
        assert bridge._target_ip_uint32_le is None  # not yet armed

        h2d_le = _ip_to_uint32_le(H2D_IP)
        vp_le = _ip_to_uint32_le(VP_IP)
        payload = json.dumps(
            {
                "print": {
                    "command": "push_status",
                    "net": {"info": [{"ip": h2d_le, "mask": 0xFFFFFF}]},
                }
            }
        ).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", payload)
        await asyncio.sleep(0.01)

        # First push landed before encoding was armed → cache holds real IP.
        cached = bridge.get_latest_print_state()
        assert cached["net"]["info"][0]["ip"] == h2d_le

        # Printer's IP becomes known. Next refresh tick must self-heal.
        target.ip_address = H2D_IP
        bridge._resolve_client()

        cached = bridge.get_latest_print_state()
        assert cached["net"]["info"][0]["ip"] == vp_le, (
            "cache must be swept once encoding becomes valid; sticky-key "
            "preservation would otherwise keep the poisoned IP forever"
        )
        assert bridge._target_ip_uint32_le == h2d_le

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

    @pytest.mark.asyncio
    async def test_incremental_push_preserves_ams_from_previous_cache(self):
        """Regression for #1371: Bambu firmware sends FULL push_status on
        pushall (with AMS/vt_tray/net/etc.) but typically OMITS those fields
        from 1 Hz incremental push_status updates. Without preserving the
        sticky keys across pushes, the cache forgets AMS info after the first
        incremental update, and BambuStudio (which reads the cache via the
        VP's 1 Hz status push) sees no AMS info until the user power-cycles
        the printer (forcing a fresh pushall).
        """
        server = _make_server()
        bridge = _make_bridge(server)
        await bridge.start()

        # 1. Initial pushall response with full state, AMS included.
        full_push = json.dumps(
            {
                "print": {
                    "command": "push_status",
                    "gcode_state": "IDLE",
                    "wifi_signal": "-50dBm",
                    "ams": {
                        "ams": [
                            {
                                "id": "0",
                                "tray": [
                                    {"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF"},
                                    {"id": "1", "tray_type": "PETG", "tray_color": "00FF00FF"},
                                ],
                            }
                        ],
                        "tray_exist_bits": "3",
                    },
                    "vt_tray": {"id": "254", "tray_type": ""},
                    "lights_report": [{"node": "chamber_light", "mode": "on"}],
                }
            }
        ).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", full_push)
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        assert cached["ams"]["ams"][0]["tray"][0]["tray_type"] == "PLA"
        assert cached["vt_tray"]["id"] == "254"
        assert cached["lights_report"][0]["mode"] == "on"

        # 2. Incremental push with only temp/wifi changes — NO ams field.
        # This is what the printer sends every ~1 s between full pushalls.
        incremental_push = json.dumps(
            {
                "print": {
                    "command": "push_status",
                    "wifi_signal": "-55dBm",
                    "chamber_temper": 26.0,
                }
            }
        ).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", incremental_push)
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        # New fields take effect.
        assert cached["wifi_signal"] == "-55dBm"
        assert cached["chamber_temper"] == 26.0
        # Sticky fields preserved from the previous cache (the #1371 fix).
        assert "ams" in cached, "AMS field must be preserved across incremental pushes (#1371)"
        assert cached["ams"]["ams"][0]["tray"][0]["tray_type"] == "PLA"
        assert cached["ams"]["tray_exist_bits"] == "3"
        assert cached["vt_tray"]["id"] == "254"
        assert cached["lights_report"][0]["mode"] == "on"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_incremental_push_preserves_non_allowlisted_capability_fields(self):
        """Regression for #1622: BambuStudio gates Device-tab UIs (manage
        calibration, AMS-slot filament dropdown, ...) on capability /
        lifecycle fields (cali_version, print_type, mc_print_stage,
        device, ...) it reads off the cached push_status. Before the fix
        these fields were not in the allowlist and drained out of the
        bridge cache on the first 1 Hz incremental tick, so the slicer's
        Device tab would grey out the gated UIs once the cache thinned.
        After the fix the cache accumulates everything the printer has
        ever sent, dropped only when explicitly overwritten.
        """
        server = _make_server()
        bridge = _make_bridge(server)
        await bridge.start()

        full_push = json.dumps(
            {
                "print": {
                    "command": "push_status",
                    "cali_version": 2,
                    "print_type": "idle",
                    "gcode_state": "IDLE",
                    "mc_print_stage": "0",
                    "mc_stage": 0,
                    "device": {"ext_tool": {"info": []}},
                    "cfg": "",
                    "home_flag": 256,
                    "wifi_signal": "-50dBm",
                }
            }
        ).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", full_push)
        await asyncio.sleep(0.01)

        # Incremental push carrying only temps + wifi — none of the
        # capability/lifecycle fields above are mentioned.
        incremental_push = json.dumps(
            {
                "print": {
                    "command": "push_status",
                    "wifi_signal": "-55dBm",
                    "nozzle_temper": 24.5,
                }
            }
        ).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", incremental_push)
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        # Incremental values applied.
        assert cached["wifi_signal"] == "-55dBm"
        assert cached["nozzle_temper"] == 24.5
        # Capability / lifecycle fields preserved from the prior pushall
        # — the symptoms in #1622 (Device-tab UIs disabled) trace to these
        # exact keys missing.
        assert cached["cali_version"] == 2
        assert cached["print_type"] == "idle"
        assert cached["gcode_state"] == "IDLE"
        assert cached["mc_print_stage"] == "0"
        assert cached["mc_stage"] == 0
        assert cached["device"] == {"ext_tool": {"info": []}}
        assert cached["cfg"] == ""
        assert cached["home_flag"] == 256

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_partial_vt_tray_update_overlays_onto_cached_full_dict(self):
        """Regression for #1622 round 5 (reported by @shaddowlink): right after
        the slicer picks a filament for the external spool (vt_tray, ams_id=255),
        Bambu firmware pushes a partial vt_tray carrying just the changed
        fields — typically ``{tray_info_idx, tray_color}`` — and omits the
        ~18 other keys (tray_type, state, k, n, cali_idx, nozzle_temp_min/max,
        tray_uuid, xcam_info, ...) the slicer needs to render the slot.
        Before this fix the per-field accumulate replaced the cached vt_tray
        wholesale (it only carried over prev keys NOT present in new), so the
        next 1 Hz cached-as-base push handed the slicer a stripped vt_tray and
        BambuStudio rendered the external slot as "invalid" until a reload
        triggered a fresh pushall. AMS slots didn't suffer because
        `_merge_ams_dict` already deep-merged them. The fix overlays incoming
        keys onto the previous dict for every top-level dict-shaped field
        (excluding ams, which keeps its own deep merge).
        """
        server = _make_server()
        bridge = _make_bridge(server)
        await bridge.start()

        # 1. Pushall response with the full ~20-field vt_tray dict a real
        # P1S sends to bootstrap the slot.
        full_push = json.dumps(
            {
                "print": {
                    "command": "push_status",
                    "vt_tray": {
                        "id": "254",
                        "tray_info_idx": "Pea5f68f",
                        "tray_type": "PLA",
                        "tray_sub_brands": "",
                        "tray_color": "F72323FF",
                        "tray_weight": "0",
                        "tray_diameter": "0.00",
                        "tray_temp": "0",
                        "tray_time": "0",
                        "bed_temp_type": "0",
                        "bed_temp": "0",
                        "nozzle_temp_max": "240",
                        "nozzle_temp_min": "190",
                        "xcam_info": "000000000000000000000000",
                        "tray_uuid": "00000000000000000000000000000000",
                        "ctype": 0,
                        "remain": -1,
                        "k": 0.01999999955296,
                        "n": 1,
                        "cali_idx": -1,
                        "state": 3,
                    },
                }
            }
        ).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", full_push)
        await asyncio.sleep(0.01)

        # 2. Incremental push carrying just the two fields the slicer's pick
        # changed — exactly the shape the P1S firmware sends after an
        # ams_filament_setting ack. This is what shaddowlink's wire dump
        # captured for the failing case.
        incremental_push = json.dumps(
            {
                "print": {
                    "command": "push_status",
                    "vt_tray": {
                        "tray_info_idx": "Pea5f68f",
                        "tray_color": "76D9F4FF",
                    },
                }
            }
        ).encode()
        bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", incremental_push)
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        vt = cached["vt_tray"]
        # Incoming fields applied.
        assert vt["tray_info_idx"] == "Pea5f68f"
        assert vt["tray_color"] == "76D9F4FF"
        # All other fields preserved from the prior pushall — without these
        # the slicer rendered the slot as invalid.
        assert vt["tray_type"] == "PLA"
        assert vt["state"] == 3
        assert vt["remain"] == -1
        assert vt["k"] == 0.01999999955296
        assert vt["n"] == 1
        assert vt["cali_idx"] == -1
        assert vt["nozzle_temp_min"] == "190"
        assert vt["nozzle_temp_max"] == "240"
        assert vt["tray_uuid"] == "00000000000000000000000000000000"
        assert vt["id"] == "254"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_partial_ams_status_update_preserves_unit_list(self):
        """#1387: Bambu firmware also sends `ams` updates where the key is
        present but the inner `ams` array is missing — e.g. just
        ``{ams_status: 1}`` or a humidity change. Before the deep-merge fix
        the bridge would overwrite the cached AMS with this stripped blob,
        the slicer would read it on the next 1 Hz push, and BambuStudio
        would drop the unit list and fall back to its "no AMS" render
        (only the external spool visible — the reporter's exact symptom).
        Now the partial update only mutates the fields it carries; the
        cached unit list survives.
        """
        server = _make_server()
        bridge = _make_bridge(server)
        await bridge.start()

        # 1. Pushall with full AMS state.
        bridge._on_printer_raw(
            f"device/{H2D_SERIAL}/report",
            json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "ams": {
                            "ams": [
                                {
                                    "id": "0",
                                    "humidity": "1",
                                    "tray": [{"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF"}],
                                }
                            ],
                            "tray_exist_bits": "1",
                            "ams_status": "0",
                        },
                    }
                }
            ).encode(),
        )
        await asyncio.sleep(0.01)

        # 2. Partial AMS update — only `ams_status` and `humidity` changed.
        # No `ams.ams` array, so prev's unit list must be preserved.
        bridge._on_printer_raw(
            f"device/{H2D_SERIAL}/report",
            json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "ams": {"ams_status": "1", "humidity": "2"},
                    }
                }
            ).encode(),
        )
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        # Scalar fields take the new values.
        assert cached["ams"]["ams_status"] == "1"
        assert cached["ams"]["humidity"] == "2"
        # Unit + tray data preserved from the pushall.
        assert cached["ams"]["tray_exist_bits"] == "1"
        assert len(cached["ams"]["ams"]) == 1
        assert cached["ams"]["ams"][0]["tray"][0]["tray_type"] == "PLA"
        assert cached["ams"]["ams"][0]["tray"][0]["tray_color"] == "FF0000FF"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_partial_ams_unit_update_preserves_other_units(self):
        """#1387: when multiple AMS units are configured (e.g. H2D with two
        AMS), an incremental push during a print typically only carries the
        unit / tray that changed state. Naive replacement of `ams.ams` wipes
        the other unit. The bridge merges unit-by-unit by id, preserving
        units the incremental doesn't mention.
        """
        server = _make_server()
        bridge = _make_bridge(server)
        await bridge.start()

        # 1. Pushall with two AMS units configured.
        bridge._on_printer_raw(
            f"device/{H2D_SERIAL}/report",
            json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "ams": {
                            "ams": [
                                {"id": "0", "tray": [{"id": "0", "tray_type": "PLA"}]},
                                {"id": "1", "tray": [{"id": "0", "tray_type": "PETG"}]},
                            ],
                            # bit 0 (AMS 0 slot 0) + bit 4 (AMS 1 slot 0) = 0x11.
                            # `_on_printer_raw` now applies the #1726 bitmask
                            # cleanup to the cached state, so the test fixture
                            # must declare both loaded slots — same shape the
                            # real printer sends.
                            "tray_exist_bits": "11",
                        },
                    }
                }
            ).encode(),
        )
        await asyncio.sleep(0.01)

        # 2. Tray-targeted incremental: unit 0 / tray 0 state changed.
        # Unit 1 is not in the update — must survive.
        bridge._on_printer_raw(
            f"device/{H2D_SERIAL}/report",
            json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "ams": {"ams": [{"id": "0", "tray": [{"id": "0", "state": "11"}]}]},
                    }
                }
            ).encode(),
        )
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        units = {u["id"]: u for u in cached["ams"]["ams"]}
        # Unit 0 keeps its tray_type from the pushall + picks up the new state.
        assert units["0"]["tray"][0]["tray_type"] == "PLA"
        assert units["0"]["tray"][0]["state"] == "11"
        # Unit 1 survives the incremental.
        assert "1" in units
        assert units["1"]["tray"][0]["tray_type"] == "PETG"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_tray_exist_bits_clears_empty_slots_in_slicer_cache(self):
        """#1726 (reported by @needo37): the bridge cache forwards the real
        printer's raw AMS payload to the slicer. Without the empty-slot
        cleanup that bambu_mqtt.py applies to Bambuddy's internal state, the
        cached units carried stale `tray_type` / `tray_color` /
        `tray_info_idx` for slots whose `tray_exist_bits` bit was 0 — and
        BambuStudio's Sync rendered those empty slots as phantom loaded
        filaments. After the fix the bridge runs the same shared
        ``apply_tray_exist_bits`` helper before storing the cache.
        """
        server = _make_server()
        bridge = _make_bridge(server)
        await bridge.start()

        # Pushall: AMS 0 has slots 0/1/2/3; only slots 1, 2, 3 are loaded.
        # Slot 0 carries stale data (RFID/color/material from a previously
        # loaded spool). `tray_exist_bits` = 0xe = 0b1110 → bit 0 unset.
        bridge._on_printer_raw(
            f"device/{H2D_SERIAL}/report",
            json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "ams": {
                            "ams": [
                                {
                                    "id": "0",
                                    "tray": [
                                        {
                                            "id": "0",
                                            "tray_type": "PLA",
                                            "tray_color": "FF0000FF",
                                            "tray_info_idx": "GFL00",
                                            "tag_uid": "1234567890abcdef",
                                            "tray_uuid": "abcdef1234567890abcdef1234567890",
                                            "remain": 75,
                                            "state": "11",
                                        },
                                        {"id": "1", "tray_type": "PETG", "tray_color": "00FF00FF"},
                                        {"id": "2", "tray_type": "ABS", "tray_color": "0000FFFF"},
                                        {"id": "3", "tray_type": "TPU", "tray_color": "FFFF00FF"},
                                    ],
                                }
                            ],
                            "tray_exist_bits": "e",
                        },
                    }
                }
            ).encode(),
        )
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        slot0 = cached["ams"]["ams"][0]["tray"][0]
        # Empty slot: stale per-tray fields wiped, state promoted to 9.
        assert slot0["state"] == 9, "empty slot must be promoted to state=9"
        assert slot0["tray_type"] == ""
        assert slot0["tray_color"] == ""
        assert slot0["tray_info_idx"] == ""
        assert slot0["tag_uid"] == "0000000000000000"
        assert slot0["tray_uuid"] == "00000000000000000000000000000000"
        assert slot0["remain"] == 0
        # Loaded slots preserved.
        assert cached["ams"]["ams"][0]["tray"][1]["tray_type"] == "PETG"
        assert cached["ams"]["ams"][0]["tray"][2]["tray_type"] == "ABS"
        assert cached["ams"]["ams"][0]["tray"][3]["tray_type"] == "TPU"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_tray_exist_bits_shutdown_guard_preserves_cache(self):
        """#765 shutdown guard mirrored at the bridge: when the printer
        powers off it sends all-zero `tray_exist_bits` paired with
        `power_on_flag=False`. Wiping the cache on that pattern would
        propagate phantom empties to every slicer reconnect until the
        printer powers back on and pushes a real state. Skip cleanup
        on the shutdown-shaped payload."""
        server = _make_server()
        bridge = _make_bridge(server)
        await bridge.start()

        # 1. Normal pushall — all four slots loaded.
        bridge._on_printer_raw(
            f"device/{H2D_SERIAL}/report",
            json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "ams": {
                            "ams": [
                                {
                                    "id": "0",
                                    "tray": [
                                        {"id": str(i), "tray_type": "PLA", "tray_color": f"{i:02x}{i:02x}{i:02x}FF"}
                                        for i in range(4)
                                    ],
                                }
                            ],
                            "tray_exist_bits": "f",
                            "power_on_flag": True,
                        },
                    }
                }
            ).encode(),
        )
        await asyncio.sleep(0.01)

        # 2. Shutdown-shaped push: tray_exist_bits=0 + power_on_flag=False.
        bridge._on_printer_raw(
            f"device/{H2D_SERIAL}/report",
            json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "ams": {
                            "tray_exist_bits": "0",
                            "power_on_flag": False,
                        },
                    }
                }
            ).encode(),
        )
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        for i in range(4):
            assert cached["ams"]["ams"][0]["tray"][i]["tray_type"] == "PLA", f"slot {i} must survive the shutdown push"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_tray_exist_bits_skips_ams_ht_units(self):
        """AMS-HT units (id >= 128) use a separate addressing scheme and
        must not be touched by the bitmask cleanup — bit math at
        global_bit = ams_id * 4 + tray_id would overrun normal AMS bits.
        Pin the skip so future AMS-HT support doesn't accidentally wipe
        loaded HT slots.
        """
        server = _make_server()
        bridge = _make_bridge(server)
        await bridge.start()

        bridge._on_printer_raw(
            f"device/{H2D_SERIAL}/report",
            json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "ams": {
                            "ams": [
                                {
                                    "id": "128",
                                    "tray": [
                                        {"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF"},
                                    ],
                                }
                            ],
                            "tray_exist_bits": "0",
                            "power_on_flag": True,
                        },
                    }
                }
            ).encode(),
        )
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        ht_slot = cached["ams"]["ams"][0]["tray"][0]
        # tray_exist_bits="0" alone would normally wipe — but AMS-HT is
        # skipped, so the HT slot keeps its loaded data.
        assert ht_slot["tray_type"] == "PLA"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_partial_ams_tray_update_preserves_other_trays(self):
        """Same shape as the unit-level test but at the tray level. AMS
        unit 0 has four trays; the incremental only mentions tray 0.
        Trays 1-3 must survive intact."""
        server = _make_server()
        bridge = _make_bridge(server)
        await bridge.start()

        bridge._on_printer_raw(
            f"device/{H2D_SERIAL}/report",
            json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "ams": {
                            "ams": [
                                {
                                    "id": "0",
                                    "tray": [
                                        {"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF"},
                                        {"id": "1", "tray_type": "PETG", "tray_color": "00FF00FF"},
                                        {"id": "2", "tray_type": "ABS", "tray_color": "0000FFFF"},
                                        {"id": "3", "tray_type": "TPU", "tray_color": "FFFF00FF"},
                                    ],
                                }
                            ],
                        },
                    }
                }
            ).encode(),
        )
        await asyncio.sleep(0.01)

        bridge._on_printer_raw(
            f"device/{H2D_SERIAL}/report",
            json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "ams": {"ams": [{"id": "0", "tray": [{"id": "0", "state": "11"}]}]},
                    }
                }
            ).encode(),
        )
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        trays = {t["id"]: t for t in cached["ams"]["ams"][0]["tray"]}
        assert trays["0"]["tray_type"] == "PLA"
        assert trays["0"]["state"] == "11"
        # Trays not mentioned in the incremental survive intact.
        assert trays["1"]["tray_type"] == "PETG"
        assert trays["2"]["tray_type"] == "ABS"
        assert trays["3"]["tray_type"] == "TPU"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_incoming_ams_update_replaces_cached_ams(self):
        """Counterpart to the #1371 fix: preservation only kicks in when the
        incoming push OMITS a sticky key. When the printer DOES send a fresh
        `ams` value (e.g. on a pushall, or when AMS state genuinely changes),
        that value must take effect — the preservation must not shadow real
        updates.
        """
        server = _make_server()
        bridge = _make_bridge(server)
        await bridge.start()

        # 1. Initial state: PLA in tray 0.
        bridge._on_printer_raw(
            f"device/{H2D_SERIAL}/report",
            json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "ams": {"ams": [{"id": "0", "tray": [{"id": "0", "tray_type": "PLA"}]}]},
                    }
                }
            ).encode(),
        )
        await asyncio.sleep(0.01)

        # 2. Fresh push with PETG — must replace, not get shadowed by the old PLA.
        bridge._on_printer_raw(
            f"device/{H2D_SERIAL}/report",
            json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "ams": {"ams": [{"id": "0", "tray": [{"id": "0", "tray_type": "PETG"}]}]},
                    }
                }
            ).encode(),
        )
        await asyncio.sleep(0.01)

        cached = bridge.get_latest_print_state()
        assert cached["ams"]["ams"][0]["tray"][0]["tray_type"] == "PETG"

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

        async def _capture(writer, payload, serial="", log_event=True):
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

    @pytest.mark.asyncio
    async def test_live_progress_fields_zeroed_in_cached_branch(self):
        """#1558: when the real target printer is mid-print, the cached
        push_status carries live values for mc_percent / stg_cur / layer_num /
        etc. BambuStudio's Send pre-flight reads any of these as "VP busy"
        even when gcode_state above is forced to IDLE — blocking Send while
        the target prints. The cached branch must override these to the same
        idle values the synthetic stub uses.
        """
        server = _make_server()
        bridge = MagicMock()
        # Real printer mid-print state: gcode_state may be RUNNING upstream,
        # but the VP's own _gcode_state is IDLE (Send is requesting a
        # new upload, the VP isn't running anything).
        bridge.get_latest_print_state.return_value = {
            "command": "push_status",
            "msg": 0,
            "gcode_state": "RUNNING",
            "mc_print_stage": "2",
            "mc_percent": 47,
            "mc_remaining_time": 3600,
            "stg": [1, 2, 3],
            "stg_cur": 14,
            "layer_num": 120,
            "total_layer_num": 250,
            "print_error": 0,
        }
        server.set_bridge(bridge)
        published = self._capture_published(server)

        await server._send_status_report(MagicMock())
        _serial, payload = published[0]
        # Every live-progress field must reflect "idle / VP isn't busy".
        assert payload["print"]["mc_print_stage"] == ""
        assert payload["print"]["mc_percent"] == 0
        assert payload["print"]["mc_remaining_time"] == 0
        assert payload["print"]["stg"] == []
        assert payload["print"]["stg_cur"] == 0
        assert payload["print"]["layer_num"] == 0
        assert payload["print"]["total_layer_num"] == 0
        assert payload["print"]["print_error"] == 0


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

    @pytest.mark.asyncio
    async def test_publish_records_bridge_to_slicer_event_by_default(self, monkeypatch):
        """#1622 round 3: every bridge-synthesised reply (info.get_version answer,
        project_file ack, on-demand pushall response) must show up in the
        cmd.jsonl trace under the ``bridge_to_slicer`` direction so a P1S↔H2D
        diff captures the fingerprint the slicer reads back from us."""
        server = _make_server()
        writer = MagicMock()
        writer.write = lambda data: None
        writer.drain = AsyncMock()

        recorded: list = []
        monkeypatch.setattr(
            "backend.app.services.virtual_printer.mqtt_server.append_event",
            lambda vp_name, direction, topic, payload: recorded.append((vp_name, direction, topic, payload)),
        )

        payload = {"info": {"command": "get_version", "sequence_id": "0"}}
        await server._publish_to_report(writer, payload)

        assert len(recorded) == 1
        vp_name, direction, topic, recorded_payload = recorded[0]
        assert direction == "bridge_to_slicer"
        assert topic.endswith("/report")
        assert recorded_payload == payload

    @pytest.mark.asyncio
    async def test_publish_skips_event_when_log_event_false(self, monkeypatch):
        """The 1Hz periodic-push path passes ``log_event=False`` so dump_wire's
        snapshot stays the canonical record of cache shape and the cmd.jsonl
        isn't flooded with ~60 lines/min per VP."""
        server = _make_server()
        writer = MagicMock()
        writer.write = lambda data: None
        writer.drain = AsyncMock()

        recorded: list = []
        monkeypatch.setattr(
            "backend.app.services.virtual_printer.mqtt_server.append_event",
            lambda *args, **kwargs: recorded.append(args),
        )

        await server._publish_to_report(writer, {"print": {"command": "push_status"}}, log_event=False)

        assert recorded == []


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


class TestHostnameResolution:
    """#1429 follow-up: users who configured the printer by FQDN (common on
    LANs with router-provided DNS like `p1s.fritz.box`) hit `invalid IPv4`
    on the encoder and the rewrite never armed — slicer kept FTPing direct
    to the real printer. The bridge now resolves hostname→IPv4 first."""

    def test_pass_through_for_valid_ipv4(self):
        assert _resolve_target_to_ipv4("192.168.1.50") == "192.168.1.50"

    def test_empty_returns_none(self):
        assert _resolve_target_to_ipv4("") is None
        assert _resolve_target_to_ipv4(None) is None  # type: ignore[arg-type]

    def test_hostname_resolves_via_getaddrinfo(self):
        with patch(
            "backend.app.services.virtual_printer.mqtt_bridge.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("192.168.3.153", 0))],
        ) as mock_gai:
            assert _resolve_target_to_ipv4("p1s.fritz.box") == "192.168.3.153"
        # AF_INET filter prevents an IPv6-only result from being picked,
        # since net.info[*].ip is a uint32 LE that can't carry v6.
        assert mock_gai.call_args.kwargs.get("family") == socket.AF_INET

    def test_dns_failure_returns_none(self):
        with patch(
            "backend.app.services.virtual_printer.mqtt_bridge.socket.getaddrinfo",
            side_effect=OSError("Name or service not known"),
        ):
            assert _resolve_target_to_ipv4("nope.invalid") is None

    def test_fqdn_target_arms_encoding(self, caplog):
        """End-to-end: a client whose `ip_address` is an FQDN should arm
        the bridge once DNS resolves, and the cached rewrite uses the
        resolved IPv4 (not the hostname string) for the `net.info[].ip`
        encoding."""
        server = _make_server(bind_address=VP_IP)
        bridge = _make_bridge(server)
        client = _make_paho_client(ip="p1s.fritz.box")
        bridge._target_client = client
        with (
            patch(
                "backend.app.services.virtual_printer.mqtt_bridge.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", (H2D_IP, 0))],
            ),
            caplog.at_level(logging.INFO, logger="backend.app.services.virtual_printer.mqtt_bridge"),
        ):
            bridge._refresh_ip_encoding()
        assert bridge._target_ip_uint32_le == _ip_to_uint32_le(H2D_IP)
        assert bridge._vp_ip_uint32_le == _ip_to_uint32_le(VP_IP)
        armed = [r for r in caplog.records if "MQTT bridge IP encoding armed" in r.getMessage()]
        assert len(armed) == 1
        # Operator should see configured→resolved in the log line so a
        # bad-DNS regression is immediately legible.
        assert "p1s.fritz.box→192.168.255.133" in armed[0].getMessage()


# ---------------------------------------------------------------------------
# Auto-resolve fallback for default-config (bind_address = "0.0.0.0")
# ---------------------------------------------------------------------------


class TestBindAddressAutoResolve:
    """#1429 residual: VPs created without a dedicated bind IP run on
    `bind_address=0.0.0.0`. The original fix's `_refresh_ip_encoding`
    early-returned on 0.0.0.0, so the rewrite never armed and `net.info[].ip`
    kept leaking the real printer IP. Now the bridge auto-resolves a host
    interface in the printer's subnet and uses that as the VP IP."""

    @pytest.mark.asyncio
    async def test_rewrite_arms_via_auto_resolved_host_ip(self):
        """When bind_address is 0.0.0.0, fall back to the host interface in
        the target printer's subnet and rewrite to that IP."""
        server = _make_server(bind_address="0.0.0.0")  # nosec B104
        bridge = _make_bridge(server)
        with patch(
            "backend.app.services.virtual_printer.mqtt_bridge._resolve_host_interface_for_target",
            return_value=VP_IP,
        ):
            await bridge.start()

            h2d_le = _ip_to_uint32_le(H2D_IP)
            vp_le = _ip_to_uint32_le(VP_IP)
            payload = json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "net": {"info": [{"ip": h2d_le, "mask": 0xFFFFFF}]},
                    }
                }
            ).encode()
            bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", payload)
            await asyncio.sleep(0.01)

            cached = bridge.get_latest_print_state()
            assert cached["net"]["info"][0]["ip"] == vp_le
            assert bridge._vp_ip_uint32_le == vp_le

            await bridge.stop()

    @pytest.mark.asyncio
    async def test_rewrite_disabled_when_no_matching_host_interface(self):
        """If no host interface shares a subnet with the printer, the bridge
        cannot pick a sensible VP IP — leave encoding unarmed and let the
        push through unrewritten (no crash, no wrong rewrite)."""
        server = _make_server(bind_address="")
        bridge = _make_bridge(server)
        with patch(
            "backend.app.services.virtual_printer.mqtt_bridge._resolve_host_interface_for_target",
            return_value=None,
        ):
            await bridge.start()

            h2d_le = _ip_to_uint32_le(H2D_IP)
            payload = json.dumps(
                {
                    "print": {
                        "command": "push_status",
                        "net": {"info": [{"ip": h2d_le, "mask": 0xFFFFFF}]},
                    }
                }
            ).encode()
            bridge._on_printer_raw(f"device/{H2D_SERIAL}/report", payload)
            await asyncio.sleep(0.01)

            assert bridge._vp_ip_uint32_le is None
            assert bridge._target_ip_uint32_le is None

            await bridge.stop()

    @pytest.mark.asyncio
    async def test_explicit_bind_ip_takes_precedence_over_auto_resolve(self):
        """Auto-resolve only kicks in when bind_address is empty/0.0.0.0; an
        explicitly-set bind IP must be used verbatim even if there's also a
        same-subnet host interface."""
        server = _make_server(bind_address=VP_IP)
        bridge = _make_bridge(server)
        # Auto-resolver would have returned a DIFFERENT IP — we must not use it.
        with patch(
            "backend.app.services.virtual_printer.mqtt_bridge._resolve_host_interface_for_target",
            return_value="10.99.99.99",
        ):
            await bridge.start()
            assert bridge._vp_ip_uint32_le == _ip_to_uint32_le(VP_IP)
            await bridge.stop()

    def test_resolve_helper_returns_none_for_unreachable_target(self):
        """The helper itself must be defensive — if `find_interface_for_ip`
        raises or returns None, we get None (no crash)."""
        with patch(
            "backend.app.services.network_utils.find_interface_for_ip",
            return_value=None,
        ):
            assert _resolve_host_interface_for_target("203.0.113.1") is None


class TestNotArmedDiagnosticLogging:
    """#1429 follow-up: every silent early-return in `_refresh_ip_encoding`
    now emits one INFO line explaining WHY the rewrite couldn't arm. Throttled
    to one line per state change so an idle unarmed bridge doesn't spam the
    log every 30s tick. Cleared on arm so a future failure re-emits.
    """

    def test_no_client_logs_once(self, caplog):
        bridge = _make_bridge(_make_server())
        # Force the "no client" path: bridge starts with _target_client=None.
        assert bridge._target_client is None
        with caplog.at_level(logging.INFO, logger="backend.app.services.virtual_printer.mqtt_bridge"):
            bridge._refresh_ip_encoding()
            bridge._refresh_ip_encoding()  # 2nd tick — same reason, must NOT re-log.
            bridge._refresh_ip_encoding()
        not_armed = [r for r in caplog.records if "NOT armed" in r.getMessage()]
        assert len(not_armed) == 1
        assert "target_client is None" in not_armed[0].getMessage()

    def test_missing_target_ip_logs_specific_reason(self, caplog):
        bridge = _make_bridge(_make_server())
        # Manually attach a client with no ip_address (simulates pre-DHCP).
        client = _make_paho_client()
        client.ip_address = ""
        bridge._target_client = client
        with caplog.at_level(logging.INFO, logger="backend.app.services.virtual_printer.mqtt_bridge"):
            bridge._refresh_ip_encoding()
        not_armed = [r for r in caplog.records if "NOT armed" in r.getMessage()]
        assert len(not_armed) == 1
        assert "no ip_address" in not_armed[0].getMessage()

    def test_no_matching_host_interface_logs_specific_reason(self, caplog):
        server = _make_server(bind_address="0.0.0.0")  # nosec B104
        bridge = _make_bridge(server)
        with (
            patch(
                "backend.app.services.virtual_printer.mqtt_bridge._resolve_host_interface_for_target",
                return_value=None,
            ),
            caplog.at_level(logging.INFO, logger="backend.app.services.virtual_printer.mqtt_bridge"),
        ):
            bridge._target_client = _make_paho_client()
            bridge._refresh_ip_encoding()
        not_armed = [r for r in caplog.records if "NOT armed" in r.getMessage()]
        assert len(not_armed) == 1
        msg = not_armed[0].getMessage()
        assert H2D_IP in msg
        assert "no host interface" in msg

    def test_unresolvable_target_logs_reason(self, caplog):
        """When `ip_address` isn't a valid IPv4 *and* doesn't resolve via DNS,
        the bridge must report a single concrete not-armed reason naming the
        configured value — operator can then see exactly what input failed."""
        server = _make_server(bind_address=VP_IP)
        bridge = _make_bridge(server)
        client = _make_paho_client()
        client.ip_address = "not.an.ip"
        bridge._target_client = client
        with (
            patch(
                "backend.app.services.virtual_printer.mqtt_bridge.socket.getaddrinfo",
                side_effect=OSError("nodename nor servname provided"),
            ),
            caplog.at_level(logging.INFO, logger="backend.app.services.virtual_printer.mqtt_bridge"),
        ):
            bridge._refresh_ip_encoding()
        not_armed = [r for r in caplog.records if "NOT armed" in r.getMessage()]
        assert len(not_armed) == 1
        assert "could not resolve printer host 'not.an.ip'" in not_armed[0].getMessage()

    def test_successful_arm_clears_dedup_so_future_failure_relogs(self, caplog):
        """After a successful arm, the dedup must reset so a subsequent
        regression (e.g. printer client unbinds) re-emits the diagnostic
        line instead of being silenced by the previous failure reason."""
        bridge = _make_bridge(_make_server(bind_address=VP_IP))
        bridge._target_client = _make_paho_client()
        with caplog.at_level(logging.INFO, logger="backend.app.services.virtual_printer.mqtt_bridge"):
            bridge._refresh_ip_encoding()  # arms
            assert bridge._not_armed_reason is None
            # Simulate a regression — target_client drops away.
            bridge._target_client = None
            bridge._refresh_ip_encoding()
            bridge._refresh_ip_encoding()  # 2nd same-reason tick must not re-log
        not_armed = [r for r in caplog.records if "NOT armed" in r.getMessage()]
        assert len(not_armed) == 1  # the post-arm failure
        armed = [r for r in caplog.records if "MQTT bridge IP encoding armed" in r.getMessage()]
        assert len(armed) == 1
