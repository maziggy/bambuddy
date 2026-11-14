"""Tests for automatic printer IP rediscovery (SSDP + rate-limited subnet scan)."""

from __future__ import annotations

import asyncio
import datetime
import ssl
import time
from unittest.mock import AsyncMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.services import printer_rediscovery as mod
from backend.app.services.printer_rediscovery import (
    DISCONNECT_GRACE_SECONDS,
    PrinterRediscoveryService,
    build_candidate_scan_hosts,
    extract_ssdp_serial,
    probe_port_8883_serial,
)

pytestmark = pytest.mark.asyncio


def _make_cert_files(tmp_path, common_name: str | None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, common_name)] if common_name else []
    subject = x509.Name(attrs)
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "BBL CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


async def test_extract_ssdp_serial_parses_bambu_and_skips_virtual():
    notify = (
        "NOTIFY * HTTP/1.1\r\n"
        "NT: urn:bambulab-com:device:3dprinter:1\r\n"
        "USN: 01P00A441200123\r\n"
        "DevName.bambu.com: Makerspace P1S\r\n\r\n"
    )
    assert extract_ssdp_serial(notify) == "01P00A441200123"

    virtual_notify = notify.replace("01P00A441200123", "00M09A391800001")
    assert extract_ssdp_serial(virtual_notify) is None
    virtual_notify_3 = notify.replace("01P00A441200123", "01P00A391800003")
    assert extract_ssdp_serial(virtual_notify_3) is None

    other = "NOTIFY * HTTP/1.1\r\nNT: upnp:rootdevice\r\nUSN: uuid:abcd\r\n\r\n"
    assert extract_ssdp_serial(other) is None


async def test_probe_port_8883_serial_reads_leaf_certificate_cn(tmp_path, monkeypatch):
    cert_path, key_path = _make_cert_files(tmp_path, "01P00A441200999")
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    server_ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            await reader.read(16)
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(_handle, "127.0.0.1", 0, ssl=server_ctx)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(mod, "MQTT_PORT", port)
    try:
        serial = await probe_port_8883_serial("127.0.0.1")
        assert serial == "01P00A441200999"
    finally:
        server.close()
        await server.wait_closed()

    # Certificate without a Subject CN must return None
    no_cn_cert, no_cn_key = _make_cert_files(tmp_path, None)
    no_cn_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    no_cn_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    no_cn_ctx.load_cert_chain(certfile=str(no_cn_cert), keyfile=str(no_cn_key))
    no_cn_server = await asyncio.start_server(_handle, "127.0.0.1", 0, ssl=no_cn_ctx)
    monkeypatch.setattr(mod, "MQTT_PORT", no_cn_server.sockets[0].getsockname()[1])
    try:
        assert await probe_port_8883_serial("127.0.0.1") is None
    finally:
        no_cn_server.close()
        await no_cn_server.wait_closed()


async def test_build_candidate_scan_hosts_prioritizes_and_bounds(monkeypatch):
    monkeypatch.setattr(
        mod,
        "get_network_interfaces",
        lambda: [{"name": "wlan0", "ip": "10.20.30.5", "netmask": "255.255.0.0", "subnet": "10.20.0.0/16"}],
    )
    hosts = build_candidate_scan_hosts(
        missing_ips=["10.20.40.12"],
        skip_ips={"10.20.30.99"},
    )
    # /16 is narrowed to the missing printer's /24 first, then local interface's /24
    assert hosts[0] == "10.20.40.1"
    assert "10.20.30.5" not in hosts  # own IP excluded
    assert "10.20.30.99" not in hosts  # connected printer IP excluded
    assert len(hosts) <= 508

    # Multi-homed host (e.g. corporate wlan0 10.206.0.0/17 + makerspace eth0 192.168.68.0/22):
    # missing printer on eth0 must NOT scan wlan0 corporate addresses at all
    monkeypatch.setattr(
        mod,
        "get_network_interfaces",
        lambda: [
            {"name": "eth0", "ip": "192.168.68.81", "netmask": "255.255.252.0", "subnet": "192.168.68.0/22"},
            {"name": "wlan0", "ip": "10.206.50.172", "netmask": "255.255.128.0", "subnet": "10.206.0.0/17"},
        ],
    )
    mesh_hosts = build_candidate_scan_hosts(missing_ips=["192.168.68.66"], skip_ips=set())
    assert all(h.startswith("192.168.") for h in mesh_hosts)
    assert "192.168.71.254" in mesh_hosts
    assert len(mesh_hosts) == 1021


async def test_no_network_activity_when_all_active_printers_connected(test_engine, monkeypatch):
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("backend.app.core.database.async_session", session_factory)

    async with session_factory() as db:
        db.add(
            Printer(
                name="P1S-1",
                serial_number="01P00A111111111",
                ip_address="192.168.1.50",
                access_code="12345678",
                model="P1S",
                is_active=True,
            )
        )
        await db.commit()

    service = PrinterRediscoveryService()
    ssdp_mock = AsyncMock()
    scan_mock = AsyncMock()
    probe_mock = AsyncMock()
    monkeypatch.setattr(service, "_discover_via_ssdp", ssdp_mock)
    monkeypatch.setattr(service, "_scan_subnets_rate_limited", scan_mock)
    monkeypatch.setattr(mod, "probe_port_8883_serial", probe_mock)
    monkeypatch.setattr(mod.printer_manager, "is_connected", lambda _pid: True)

    assert await service.run_once() == 0
    ssdp_mock.assert_not_awaited()
    scan_mock.assert_not_awaited()
    probe_mock.assert_not_awaited()


async def test_ssdp_rediscovery_verifies_credentials_updates_ip_and_skips_subnet_scan(test_engine, monkeypatch):
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("backend.app.core.database.async_session", session_factory)

    async with session_factory() as db:
        printer = Printer(
            name="P1S-1",
            serial_number="01P00A222222222",
            ip_address="192.168.1.50",
            access_code="87654321",
            model="P1S",
            is_active=True,
        )
        db.add(printer)
        await db.commit()
        printer_id = printer.id

    service = PrinterRediscoveryService()
    service._disconnected_since[printer_id] = time.monotonic() - (DISCONNECT_GRACE_SECONDS + 5)

    async def fake_probe(ip: str, **_kwargs):
        if ip == "192.168.1.88":
            return "01P00A222222222"
        return None

    monkeypatch.setattr(mod, "_get_local_ips", lambda: {"192.168.1.2"})
    monkeypatch.setattr(mod, "probe_port_8883_serial", fake_probe)
    monkeypatch.setattr(mod.printer_manager, "is_connected", lambda _pid: False)
    monkeypatch.setattr(
        service,
        "_discover_via_ssdp",
        AsyncMock(return_value={"01P00A222222222": "192.168.1.88"}),
    )
    scan_mock = AsyncMock()
    monkeypatch.setattr(service, "_scan_subnets_rate_limited", scan_mock)
    test_conn_mock = AsyncMock(return_value={"success": True, "state": "IDLE", "model": "P1S", "reason": None})
    connect_mock = AsyncMock(return_value=True)
    ws_mock = AsyncMock()
    monkeypatch.setattr(mod.printer_manager, "test_connection", test_conn_mock)
    monkeypatch.setattr(mod.printer_manager, "connect_printer", connect_mock)
    monkeypatch.setattr(mod.ws_manager, "broadcast", ws_mock)

    with (
        patch("backend.app.api.routes.camera.stop_printer_streams", new=AsyncMock(return_value=0)) as cam_stop,
        patch("backend.app.services.virtual_printer.virtual_printer_manager.sync_from_db", new=AsyncMock()) as vp_sync,
    ):
        updated = await service.run_once()

    assert updated == 1
    test_conn_mock.assert_awaited_once_with(
        ip_address="192.168.1.88",
        serial_number="01P00A222222222",
        access_code="87654321",
    )
    scan_mock.assert_not_awaited()
    connect_mock.assert_awaited_once()
    cam_stop.assert_awaited_once_with(printer_id)
    vp_sync.assert_awaited_once()
    ws_mock.assert_awaited_once_with({"type": "printers_changed", "printer_id": printer_id})

    async with session_factory() as db:
        refreshed = await db.get(Printer, printer_id)
        assert refreshed is not None
        assert refreshed.ip_address == "192.168.1.88"


async def test_rejects_ip_update_when_tls_serial_or_access_code_fails(test_engine, monkeypatch):
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("backend.app.core.database.async_session", session_factory)

    async with session_factory() as db:
        printer = Printer(
            name="P1S-AuthCheck",
            serial_number="01P00A333333333",
            ip_address="192.168.1.50",
            access_code="11223344",
            model="P1S",
            is_active=True,
        )
        db.add(printer)
        db.add(Settings(key="printer_rediscovery_subnet_scan", value="false"))
        await db.commit()
        printer_id = printer.id

    service = PrinterRediscoveryService()
    service._disconnected_since[printer_id] = time.monotonic() - (DISCONNECT_GRACE_SECONDS + 5)
    monkeypatch.setattr(mod.printer_manager, "is_connected", lambda _pid: False)

    # Case 1a: Spoofed SSDP packet pointing to a host whose TLS cert CN is a different serial
    async def spoofed_probe(ip: str, **_kwargs):
        return "01P00AOTHER9999" if ip == "192.168.1.99" else None

    monkeypatch.setattr(mod, "probe_port_8883_serial", spoofed_probe)
    monkeypatch.setattr(service, "_discover_via_ssdp", AsyncMock(return_value={"01P00A333333333": "192.168.1.99"}))
    test_conn_mock = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(mod.printer_manager, "test_connection", test_conn_mock)

    assert await service.run_once() == 0
    # Must never send access_code to a host presenting a mismatched TLS serial
    test_conn_mock.assert_not_awaited()

    # Case 1b: Spoofed SSDP packet pointing to a host whose TLS cert has no CN (None)
    monkeypatch.setattr(mod, "probe_port_8883_serial", AsyncMock(return_value=None))
    assert await service.run_once() == 0
    test_conn_mock.assert_not_awaited()

    # Case 2: TLS serial matches, but MQTT access code is rejected
    async def matching_probe(ip: str, **_kwargs):
        return "01P00A333333333" if ip == "192.168.1.99" else None

    monkeypatch.setattr(mod, "probe_port_8883_serial", matching_probe)
    test_conn_fail = AsyncMock(return_value={"success": False, "reason": "auth_rejected"})
    monkeypatch.setattr(mod.printer_manager, "test_connection", test_conn_fail)

    assert await service.run_once() == 0
    test_conn_fail.assert_awaited_once()

    async with session_factory() as db:
        refreshed = await db.get(Printer, printer_id)
        assert refreshed is not None
        assert refreshed.ip_address == "192.168.1.50"


async def test_subnet_fallback_rate_limit_cooldown_and_early_stop(test_engine, monkeypatch):
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("backend.app.core.database.async_session", session_factory)

    async with session_factory() as db:
        printer = Printer(
            name="P1S-Mesh",
            serial_number="01P00A444444444",
            ip_address="192.168.1.10",
            access_code="99887766",
            model="P1S",
            is_active=True,
        )
        db.add(printer)
        db.add(Settings(key="printer_rediscovery_scan_interval", value="15"))
        await db.commit()
        printer_id = printer.id

    service = PrinterRediscoveryService()
    service._disconnected_since[printer_id] = time.monotonic() - (DISCONNECT_GRACE_SECONDS + 5)

    probed_ips: list[str] = []

    async def fake_probe(ip: str, **_kwargs):
        probed_ips.append(ip)
        if ip == "192.168.1.4":
            return "01P00A444444444"
        return None

    monkeypatch.setattr(mod, "probe_port_8883_serial", fake_probe)
    monkeypatch.setattr(
        mod,
        "build_candidate_scan_hosts",
        lambda _m, _s: [f"192.168.1.{i}" for i in range(1, 30)],
    )
    monkeypatch.setattr(mod, "SCAN_BATCH_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(mod.printer_manager, "is_connected", lambda _pid: False)
    monkeypatch.setattr(service, "_discover_via_ssdp", AsyncMock(return_value={}))
    monkeypatch.setattr(mod.printer_manager, "test_connection", AsyncMock(return_value={"success": False}))

    # First pass runs subnet scan (even if system monotonic clock were small) and stops after first batch of 6
    await service.run_once()
    # 1 initial check of configured IP (192.168.1.10) + first batch of 6 hosts (1..6, finding .4)
    assert len(probed_ips) == 1 + mod.SCAN_CONCURRENCY

    # Immediate second pass must skip subnet scan due to 15-minute cooldown
    probed_ips.clear()
    await service.run_once()
    assert probed_ips == ["192.168.1.10"]


async def test_settings_api_persists_and_validates_rediscovery_fields(async_client):
    resp = await async_client.get("/api/v1/settings/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["printer_rediscovery_enabled"] is True
    assert body["printer_rediscovery_subnet_scan"] is True
    assert body["printer_rediscovery_scan_interval"] == 10

    # Valid update
    put_resp = await async_client.put(
        "/api/v1/settings/",
        json={
            "printer_rediscovery_enabled": False,
            "printer_rediscovery_subnet_scan": False,
            "printer_rediscovery_scan_interval": 20,
        },
    )
    assert put_resp.status_code == 200
    updated = put_resp.json()
    assert updated["printer_rediscovery_enabled"] is False
    assert updated["printer_rediscovery_subnet_scan"] is False
    assert updated["printer_rediscovery_scan_interval"] == 20

    # Out-of-range scan interval (< 5) rejected with 422
    bad_resp = await async_client.put(
        "/api/v1/settings/",
        json={"printer_rediscovery_scan_interval": 1},
    )
    assert bad_resp.status_code == 422

    # Explicit null rejected with 422
    null_resp = await async_client.put(
        "/api/v1/settings/",
        json={"printer_rediscovery_enabled": None},
    )
    assert null_resp.status_code == 422


async def test_dhcp_lease_swap_and_hostname_preservation(test_engine, monkeypatch):
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("backend.app.core.database.async_session", session_factory)

    async with session_factory() as db:
        p_a = Printer(
            name="P1S-A",
            serial_number="01p00aaaaa11111",  # lowercase legacy serial normalized
            ip_address="192.168.1.50",
            access_code="11111111",
            model="P1S",
            is_active=True,
        )
        p_b = Printer(
            name="P1S-B",
            serial_number="01P00ABBBB22222",
            ip_address="192.168.1.51",
            access_code="22222222",
            model="P1S",
            is_active=True,
        )
        p_dns = Printer(
            name="P1S-DNS",
            serial_number="01P00ADNS333333",
            ip_address="p1s-dns.local",
            access_code="33333333",
            model="P1S",
            is_active=True,
        )
        db.add_all([p_a, p_b, p_dns])
        await db.commit()
        id_a, id_b, id_dns = p_a.id, p_b.id, p_dns.id

    service = PrinterRediscoveryService()
    past = time.monotonic() - (DISCONNECT_GRACE_SECONDS + 5)
    service._disconnected_since[id_a] = past
    service._disconnected_since[id_b] = past
    service._disconnected_since[id_dns] = past

    # Router swapped .50 and .51 between P1S-A and P1S-B
    async def swapped_probe(ip: str, **_kwargs):
        if ip == "192.168.1.50":
            return "01P00ABBBB22222"
        if ip == "192.168.1.51":
            return "01P00AAAAA11111"
        return None

    monkeypatch.setattr(mod, "_get_local_ips", lambda: {"192.168.1.2"})
    monkeypatch.setattr(mod, "probe_port_8883_serial", swapped_probe)
    monkeypatch.setattr(mod.printer_manager, "is_connected", lambda _pid: False)
    ssdp_mock = AsyncMock(return_value={})
    scan_mock = AsyncMock(return_value={})
    monkeypatch.setattr(service, "_discover_via_ssdp", ssdp_mock)
    monkeypatch.setattr(service, "_scan_subnets_rate_limited", scan_mock)
    monkeypatch.setattr(mod.printer_manager, "test_connection", AsyncMock(return_value={"success": True}))
    monkeypatch.setattr(mod.printer_manager, "connect_printer", AsyncMock(return_value=True))
    monkeypatch.setattr(mod.ws_manager, "broadcast", AsyncMock())

    with (
        patch("backend.app.api.routes.camera.stop_printer_streams", new=AsyncMock(return_value=0)),
        patch("backend.app.services.virtual_printer.virtual_printer_manager.sync_from_db", new=AsyncMock()),
    ):
        updated = await service.run_once()

    # Both swapped printers updated immediately without SSDP or subnet sweep; DNS printer untouched
    assert updated == 2
    ssdp_mock.assert_not_awaited()
    scan_mock.assert_not_awaited()

    async with session_factory() as db:
        assert (await db.get(Printer, id_a)).ip_address == "192.168.1.51"
        assert (await db.get(Printer, id_b)).ip_address == "192.168.1.50"
        assert (await db.get(Printer, id_dns)).ip_address == "p1s-dns.local"


async def test_ssdp_ignores_local_proxy_virtual_printer_ips(monkeypatch):
    packets = [
        # Local Proxy Virtual Printer answering first with the real physical serial
        (
            b"HTTP/1.1 200 OK\r\nST: urn:bambulab-com:device:3dprinter:1\r\nUSN: 01P00A555555555\r\n\r\n",
            ("192.168.1.20", 2021),
        ),
        # Real physical printer answering slightly later from its new DHCP lease
        (
            b"HTTP/1.1 200 OK\r\nST: urn:bambulab-com:device:3dprinter:1\r\nUSN: 01P00A555555555\r\n\r\n",
            ("192.168.1.77", 2021),
        ),
    ]

    class FakeSocket:
        def recvfrom(self, _bufsize: int):
            if packets:
                return packets.pop(0)
            raise BlockingIOError

        def close(self):
            pass

    service = PrinterRediscoveryService()
    monkeypatch.setattr(service, "_open_ssdp_sockets", lambda _ips: [FakeSocket()])
    monkeypatch.setattr(service, "_send_msearch_relevant_interfaces", lambda _sock, _ips: None)

    found = await service._discover_via_ssdp(
        {"01P00A555555555"},
        1.0,
        ignore_ips={"192.168.1.20"},
    )
    assert found == {"01P00A555555555": "192.168.1.77"}


async def test_virtual_printer_sync_updates_server_mode_camera_proxy_target(test_engine, tmp_path):
    from types import SimpleNamespace

    from backend.app.models.virtual_printer import VirtualPrinter
    from backend.app.services.virtual_printer.manager import VirtualPrinterManager

    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        printer = Printer(
            name="P1S-CamTarget",
            serial_number="01P00A666666666",
            ip_address="192.168.68.99",
            access_code="12345678",
            model="P1S",
            is_active=True,
        )
        db.add(printer)
        await db.flush()
        vp = VirtualPrinter(
            name="Queue VP",
            enabled=True,
            mode="queue",
            model="3DPrinter-X1-Carbon",
            access_code="87654321",
            serial_suffix="391800002",
            target_printer_id=printer.id,
            auto_dispatch=True,
            queue_force_color_match=False,
            save_ams_mapping=False,
            gcode_injection=False,
        )
        db.add(vp)
        await db.commit()
        vp_id = vp.id
        printer_id = printer.id

    mgr = VirtualPrinterManager()
    mgr._session_factory = session_factory
    mgr._base_dir = tmp_path
    rtsp_proxy = SimpleNamespace(target_host="192.168.68.55")
    mgr._instances[vp_id] = SimpleNamespace(
        mode="queue",
        name="Queue VP",
        model="3DPrinter-X1-Carbon",
        access_code="87654321",
        bind_ip="",
        remote_interface_ip="",
        target_printer_id=printer_id,
        auto_dispatch=True,
        queue_force_color_match=False,
        save_ams_mapping=False,
        gcode_injection=False,
        _rtsp_proxy=rtsp_proxy,
    )

    await mgr.sync_from_db()
    assert rtsp_proxy.target_host == "192.168.68.99"
