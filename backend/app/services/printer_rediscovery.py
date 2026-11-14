"""Automatic IP rediscovery for disconnected Bambu Lab printers.

When active printers go offline (for example, after mesh routers reassign DHCP
leases), this service looks for them on the local network:
1. SSDP multicast/broadcast listen + M-SEARCH on UDP port 2021.
2. Fallback rate-limited TCP/TLS sweep on port 8883 (reading the printer serial
   from the leaf certificate Common Name).

Before updating a printer's IP address in the database, Bambuddy verifies both
its serial number and its configured access code via MQTT ``test_connection``.
Nothing runs when all active printers are connected.
"""

import asyncio
import contextlib
import ipaddress
import logging
import re
import socket
import ssl
import struct
import time
from dataclasses import dataclass

from cryptography import x509
from cryptography.x509.oid import NameOID
from sqlalchemy import select

from backend.app.core import database
from backend.app.core.websocket import ws_manager
from backend.app.models.printer import Printer
from backend.app.services.discovery import (
    BAMBU_SEARCH_TARGET,
    SSDP_ADDR,
    SSDP_MSEARCH,
    SSDP_PORT,
    VIRTUAL_PRINTER_SERIAL_SUFFIX,
)
from backend.app.services.network_utils import get_all_interface_ips, get_network_interfaces
from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

MQTT_PORT = 8883
CHECK_INTERVAL_SECONDS = 30.0
DISCONNECT_GRACE_SECONDS = 15.0
SSDP_LISTEN_SECONDS = 5.0
TCP_CONNECT_TIMEOUT = 0.8
TLS_HANDSHAKE_TIMEOUT = 5.0
SCAN_CONCURRENCY = 6
SCAN_BATCH_DELAY_SECONDS = 0.15
DEFAULT_SCAN_INTERVAL_MINUTES = 10
MIN_SCAN_INTERVAL_MINUTES = 5
MAX_SCAN_INTERVAL_MINUTES = 60
MAX_SUBNET_HOSTS = 1024


@dataclass(frozen=True, slots=True)
class _MissingPrinter:
    id: int
    name: str
    serial_number: str
    ip_address: str
    access_code: str


_TLS_PROBE_CONTEXT: ssl.SSLContext | None = None
_VP_SERIAL_RE = re.compile(r"39180\d{4}$")


def _is_virtual_printer_serial(serial: str) -> bool:
    return serial.endswith(VIRTUAL_PRINTER_SERIAL_SUFFIX) or bool(_VP_SERIAL_RE.search(serial))


def _get_tls_probe_context() -> ssl.SSLContext:
    """Return a cached client SSLContext without loading OS trust stores."""
    global _TLS_PROBE_CONTEXT
    if _TLS_PROBE_CONTEXT is None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        with contextlib.suppress(ssl.SSLError):
            # Bambu printers use plain-RSA AES-GCM suites stripped by hardened OpenSSL 3 defaults
            ctx.set_ciphers("DEFAULT:AES256-GCM-SHA384:AES128-GCM-SHA256")
        _TLS_PROBE_CONTEXT = ctx
    return _TLS_PROBE_CONTEXT


def _normalize_serial(value: str) -> str:
    return value.strip().upper()


def extract_ssdp_serial(response: str) -> str | None:
    """Extract an uppercase Bambu printer serial number from an SSDP packet, or None."""
    if BAMBU_SEARCH_TARGET not in response and "bambulab" not in response.lower():
        return None
    usn_match = re.search(r"USN:\s*(?:uuid:)?([^\s\r\n]+)", response, re.IGNORECASE)
    if not usn_match:
        return None
    serial = _normalize_serial(usn_match.group(1))
    if not serial or _is_virtual_printer_serial(serial):
        return None
    return serial


async def probe_port_8883_serial(
    ip: str,
    *,
    connect_timeout: float = TCP_CONNECT_TIMEOUT,
    tls_timeout: float = TLS_HANDSHAKE_TIMEOUT,
) -> str | None:
    """Connect to ``ip:8883``, upgrade to TLS, and return the uppercase leaf certificate CN.

    Bambu Lab printers set their leaf certificate's Common Name to their serial
    number. Separating the fast TCP connect timeout from the TLS handshake
    timeout keeps dead hosts cheap while giving ESP32 brokers (P1P/P1S/A1) full
    headroom to finish TLS negotiation on the same socket.
    """
    writer: asyncio.StreamWriter | None = None
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, MQTT_PORT),
            timeout=connect_timeout,
        )
    except Exception:
        return None

    try:
        await writer.start_tls(_get_tls_probe_context(), ssl_handshake_timeout=tls_timeout)
        ssl_obj = writer.get_extra_info("ssl_object")
        der_cert = ssl_obj.getpeercert(binary_form=True) if ssl_obj is not None else None
        if not der_cert:
            return None
        cert = x509.load_der_x509_certificate(der_cert)
        attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if not attrs:
            return None
        raw_cn = attrs[0].value
        cn = _normalize_serial(raw_cn.decode("utf-8", errors="ignore") if isinstance(raw_cn, bytes) else raw_cn)
        return cn or None
    except Exception:
        return None
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
        except Exception:
            pass


def _get_local_ips() -> set[str]:
    """Return all IPv4 addresses owned by this host (including VP secondary aliases)."""
    return {entry["ip"] for entry in get_all_interface_ips(include_excluded=True) if entry.get("ip")}


def _is_scannable_private_ipv4(ip_str: str) -> ipaddress.IPv4Address | None:
    try:
        addr = ipaddress.IPv4Address(ip_str)
    except ValueError:
        return None
    if not addr.is_private or addr.is_loopback or addr.is_link_local:
        return None
    return addr


def _select_relevant_interfaces(missing_ips: list[str]) -> list[tuple[ipaddress.IPv4Address, ipaddress.IPv4Network]]:
    """Return scannable local interfaces, scoped to those matching ``missing_ips`` when possible.

    On multi-homed hosts (for example, a Raspberry Pi bridging an enterprise
    Wi-Fi network on ``wlan0`` and a makerspace printer router on ``eth0``),
    this avoids probing the enterprise interface when all missing printers
    belong to ``eth0``'s subnet.
    """
    all_ifaces: list[tuple[ipaddress.IPv4Address, ipaddress.IPv4Network]] = []
    for iface in get_network_interfaces():
        iface_ip = iface.get("ip") or ""
        addr = _is_scannable_private_ipv4(iface_ip)
        if addr is None:
            continue
        subnet_str = iface.get("subnet") or f"{addr}/24"
        try:
            net = ipaddress.IPv4Network(subnet_str, strict=False)
        except ValueError:
            net = ipaddress.IPv4Network(f"{addr}/24", strict=False)
        all_ifaces.append((addr, net))

    missing_addrs = [addr for ip in missing_ips if (addr := _is_scannable_private_ipv4(ip)) is not None]
    if missing_addrs and all(any(m in net for _, net in all_ifaces) for m in missing_addrs):
        return [(addr, net) for addr, net in all_ifaces if any(m in net for m in missing_addrs)]
    return all_ifaces


def build_candidate_scan_hosts(missing_ips: list[str], skip_ips: set[str]) -> list[str]:
    """Build a prioritized, deduplicated list of private LAN hosts to probe.

    Scans the ``/24`` slices of the missing printers' previous IPs and relevant
    local interfaces first, followed by wider ``/22``–``/23`` interface ranges
    (such as mesh router defaults like ``192.168.68.0/22``). Networks wider
    than ``/22`` are narrowed to ``/24`` slices so Bambuddy never sweeps
    thousands of addresses on a ``/16``.
    """
    excluded = set(skip_ips)
    for iface in get_network_interfaces():
        if iface.get("ip"):
            excluded.add(iface["ip"])

    priority_networks: list[ipaddress.IPv4Network] = []
    wider_networks: list[ipaddress.IPv4Network] = []

    for ip_str in missing_ips:
        addr = _is_scannable_private_ipv4(ip_str)
        if addr is not None:
            priority_networks.append(ipaddress.IPv4Network(f"{addr}/24", strict=False))

    for addr, net in _select_relevant_interfaces(missing_ips):
        if net.prefixlen >= 24:
            priority_networks.append(net)
        else:
            priority_networks.append(ipaddress.IPv4Network(f"{addr}/24", strict=False))
            if net.num_addresses - 2 <= MAX_SUBNET_HOSTS:
                wider_networks.append(net)

    seen: set[str] = set()
    hosts: list[str] = []
    for net in (*priority_networks, *wider_networks):
        for host in net.hosts():
            host_str = str(host)
            if host_str in excluded or host_str in seen:
                continue
            seen.add(host_str)
            hosts.append(host_str)
            if len(hosts) >= MAX_SUBNET_HOSTS:
                return hosts
    return hosts


class PrinterRediscoveryService:
    """Background service that rediscovers disconnected active printers."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._wake_event = asyncio.Event()
        self._disconnected_since: dict[int, float] = {}
        self._last_subnet_scan_at: float | None = None

    def forget_printer(self, printer_id: int) -> None:
        """Drop per-printer state when a printer is deleted."""
        self._disconnected_since.pop(printer_id, None)

    def on_settings_changed(self, *, reset_scan_cooldown: bool = False) -> None:
        """Wake the background loop when rediscovery settings change."""
        if reset_scan_cooldown:
            self._last_subnet_scan_at = None
        self._wake_event.set()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._wake_event.clear()
        self._task = asyncio.create_task(self._loop(), name="printer-rediscovery")
        logger.info("Printer rediscovery service started")

    async def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._disconnected_since.clear()
        logger.info("Printer rediscovery service stopped")

    async def _wait(self, seconds: float) -> None:
        self._wake_event.clear()
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _loop(self) -> None:
        # Allow startup MQTT handshakes to settle before checking offline state.
        await self._wait(DISCONNECT_GRACE_SECONDS)
        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Printer rediscovery sweep failed: %s", exc)
            if not self._running:
                break
            await self._wait(CHECK_INTERVAL_SECONDS)

    async def _load_config_and_candidates(
        self,
    ) -> tuple[bool, bool, int, list[_MissingPrinter], set[str]]:
        """Read settings and snapshot active disconnected printers without holding DB open."""
        from backend.app.api.routes.settings import get_setting, setting_is_true

        async with database.async_session() as db:
            enabled_raw = await get_setting(db, "printer_rediscovery_enabled")
            enabled = True if not enabled_raw else setting_is_true(enabled_raw)
            if not enabled:
                self._disconnected_since.clear()
                return False, False, DEFAULT_SCAN_INTERVAL_MINUTES, [], set()

            subnet_raw = await get_setting(db, "printer_rediscovery_subnet_scan")
            subnet_enabled = True if not subnet_raw else setting_is_true(subnet_raw)

            interval_raw = await get_setting(db, "printer_rediscovery_scan_interval")
            try:
                interval_min = int(interval_raw) if interval_raw else DEFAULT_SCAN_INTERVAL_MINUTES
            except ValueError:
                interval_min = DEFAULT_SCAN_INTERVAL_MINUTES
            interval_min = max(MIN_SCAN_INTERVAL_MINUTES, min(MAX_SCAN_INTERVAL_MINUTES, interval_min))

            rows = (await db.execute(select(Printer).where(Printer.is_active.is_(True)))).scalars().all()
            raw_printers = [
                (p.id, p.name, p.serial_number or "", p.ip_address or "", p.access_code or "") for p in rows
            ]

        now = time.monotonic()
        connected_ips: set[str] = set()
        candidates_all: list[_MissingPrinter] = []
        for pid, name, raw_serial, ip_addr, access_code in raw_printers:
            is_literal_ipv4 = _is_scannable_private_ipv4(ip_addr) is not None
            if printer_manager.is_connected(pid):
                self._disconnected_since.pop(pid, None)
                if is_literal_ipv4:
                    connected_ips.add(ip_addr)
                continue
            # Never overwrite DNS/mDNS hostnames (e.g. printer.local) with raw DHCP IPs
            norm_serial = _normalize_serial(raw_serial)
            if not norm_serial or not access_code or not is_literal_ipv4:
                self._disconnected_since.pop(pid, None)
                continue
            candidates_all.append(
                _MissingPrinter(
                    id=pid,
                    name=name,
                    serial_number=norm_serial,
                    ip_address=ip_addr,
                    access_code=access_code,
                )
            )

        candidate_ids = {p.id for p in candidates_all}
        for tracked_id in list(self._disconnected_since):
            if tracked_id not in candidate_ids:
                self._disconnected_since.pop(tracked_id, None)

        missing: list[_MissingPrinter] = []
        for p in candidates_all:
            since = self._disconnected_since.setdefault(p.id, now)
            if now - since >= DISCONNECT_GRACE_SECONDS:
                missing.append(p)

        return enabled, subnet_enabled, interval_min, missing, connected_ips

    async def run_once(self) -> int:
        """Execute one rediscovery pass if any active printers are missing.

        Returns the number of printers whose IP addresses were updated.
        """
        enabled, subnet_enabled, interval_min, candidates, connected_ips = await self._load_config_and_candidates()
        if not enabled or not candidates:
            return 0

        # Skip printers that still answer with their own serial at their configured IP
        # (their MQTT session may be reconnecting, so their IP has not moved).
        # Also record any other Bambu serial seen at a candidate's old IP so direct
        # DHCP lease swaps between printers resolve immediately.
        missing_by_serial: dict[str, _MissingPrinter] = {}
        observed_at_old_ips: dict[str, str] = {}
        for printer in candidates:
            cert_serial = await probe_port_8883_serial(printer.ip_address)
            if cert_serial == printer.serial_number:
                logger.debug(
                    "Printer %s (%s) still answers at %s; skipping IP rediscovery",
                    printer.name,
                    printer.serial_number,
                    printer.ip_address,
                )
                continue
            if cert_serial and not _is_virtual_printer_serial(cert_serial):
                observed_at_old_ips[cert_serial] = printer.ip_address
            missing_by_serial[printer.serial_number] = printer

        if not missing_by_serial:
            return 0

        local_ips = await asyncio.to_thread(_get_local_ips)
        updated = 0

        # Fast path: direct DHCP lease swap observed during initial candidate checks
        for serial, swapped_ip in observed_at_old_ips.items():
            if swapped_ip in local_ips:
                continue
            printer = missing_by_serial.get(serial)
            if printer is None:
                continue
            if await self._verify_and_update_printer(printer, swapped_ip, verify_tls_serial=False):
                updated += 1
                connected_ips.add(swapped_ip)
                missing_by_serial.pop(serial, None)

        if not missing_by_serial:
            return updated

        # Approach 1: SSDP listen + M-SEARCH (ignoring Bambuddy's own primary/alias VP IPs)
        ssdp_matches = await self._discover_via_ssdp(
            set(missing_by_serial.keys()),
            SSDP_LISTEN_SECONDS,
            ignore_ips=local_ips,
            missing_ips=[p.ip_address for p in missing_by_serial.values()],
        )
        for serial, new_ip in ssdp_matches.items():
            printer = missing_by_serial.get(serial)
            if printer is None:
                continue
            if await self._verify_and_update_printer(printer, new_ip, verify_tls_serial=True):
                updated += 1
                connected_ips.add(new_ip)
                missing_by_serial.pop(serial, None)

        if not missing_by_serial or not subnet_enabled:
            return updated

        # Approach 2: Rate-limited fallback subnet scan
        now = time.monotonic()
        cooldown_seconds = interval_min * 60.0
        if self._last_subnet_scan_at is not None and (now - self._last_subnet_scan_at) < cooldown_seconds:
            logger.debug(
                "Skipping fallback subnet scan (%d missing printer(s)); %.0fs remaining in cooldown",
                len(missing_by_serial),
                cooldown_seconds - (now - self._last_subnet_scan_at),
            )
            return updated

        self._last_subnet_scan_at = now
        scan_matches = await self._scan_subnets_rate_limited(
            target_serials=set(missing_by_serial.keys()),
            missing_ips=[p.ip_address for p in missing_by_serial.values()],
            skip_ips=connected_ips | local_ips,
        )
        for serial, new_ip in scan_matches.items():
            printer = missing_by_serial.get(serial)
            if printer is None:
                continue
            if await self._verify_and_update_printer(printer, new_ip, verify_tls_serial=False):
                updated += 1

        return updated

    def _open_ssdp_sockets(self, missing_ips: list[str]) -> list[socket.socket]:
        """Open SSDP UDP sockets for both port-2021 NOTIFY broadcasts and unicast M-SEARCH replies.

        When Virtual Printers bind ``(iface_ip, 2021)`` on Linux, unicast replies
        addressed to port 2021 go to the more specific VP socket, whereas broadcast
        ``NOTIFY`` packets reach ``("", 2021)`` and unicast ``M-SEARCH`` replies
        reach a separate ephemeral socket ``("", 0)``. Multicast group membership
        is joined only on ``_select_relevant_interfaces(missing_ips)`` so unrelated
        enterprise NICs never emit IGMP Join/Leave traffic.
        """
        relevant = _select_relevant_interfaces(missing_ips)
        sockets: list[socket.socket] = []
        for bind_port in (SSDP_PORT, 0):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if bind_port == SSDP_PORT:
                    with contextlib.suppress(AttributeError, OSError):
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                sock.setblocking(False)
                sock.bind(("", bind_port))
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

                if bind_port == SSDP_PORT:
                    if relevant:
                        for addr, _net in relevant:
                            with contextlib.suppress(OSError):
                                mreq_iface = struct.pack(
                                    "4s4s", socket.inet_aton(SSDP_ADDR), socket.inet_aton(str(addr))
                                )
                                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq_iface)
                    else:
                        with contextlib.suppress(OSError):
                            mreq = struct.pack("4sl", socket.inet_aton(SSDP_ADDR), socket.INADDR_ANY)
                            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                sockets.append(sock)
            except OSError:
                with contextlib.suppress(OSError):
                    sock.close()
                if bind_port == 0 and not sockets:
                    raise
        return sockets

    def _send_msearch_relevant_interfaces(self, sock: socket.socket, missing_ips: list[str]) -> None:
        payload = SSDP_MSEARCH.encode()
        sent_any = False
        for addr, _net in _select_relevant_interfaces(missing_ips):
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(str(addr)))
                sock.sendto(payload, (SSDP_ADDR, SSDP_PORT))
                sent_any = True
            except OSError:
                pass
        if not sent_any:
            with contextlib.suppress(OSError):
                sock.sendto(payload, (SSDP_ADDR, SSDP_PORT))

    async def _discover_via_ssdp(
        self,
        target_serials: set[str],
        duration: float,
        *,
        ignore_ips: set[str] | None = None,
        missing_ips: list[str] | None = None,
    ) -> dict[str, str]:
        """Listen for SSDP announcements/replies matching ``target_serials``."""
        found: dict[str, str] = {}
        if not target_serials:
            return found

        ignored = ignore_ips or set()
        hint_ips = missing_ips or []
        sockets: list[socket.socket] = []
        try:
            sockets = self._open_ssdp_sockets(hint_ips)
            msearch_sock = sockets[-1]
            self._send_msearch_relevant_interfaces(msearch_sock, hint_ips)
            loop = asyncio.get_running_loop()
            start = loop.time()
            last_send = start

            while (loop.time() - start) < duration and not (target_serials <= found.keys()):
                for sock in sockets:
                    while True:
                        try:
                            data, addr = sock.recvfrom(4096)
                        except (BlockingIOError, OSError):
                            break
                        sender_ip = addr[0]
                        if sender_ip in ignored or _is_scannable_private_ipv4(sender_ip) is None:
                            continue
                        serial = extract_ssdp_serial(data.decode("utf-8", errors="ignore"))
                        if serial and serial in target_serials:
                            found[serial] = sender_ip

                if target_serials <= found.keys():
                    break

                now = loop.time()
                if now - last_send >= 2.5:
                    self._send_msearch_relevant_interfaces(msearch_sock, hint_ips)
                    last_send = now

                await asyncio.sleep(0.1)
        except OSError as exc:
            logger.debug("SSDP rediscovery socket error: %s", exc)
        finally:
            for sock in sockets:
                with contextlib.suppress(OSError):
                    sock.close()
        return found

    async def _probe_host_for_serial(self, ip: str, target_serials: set[str]) -> tuple[str, str] | None:
        serial = await probe_port_8883_serial(ip)
        if serial and not _is_virtual_printer_serial(serial) and serial in target_serials:
            return serial, ip
        return None

    async def _scan_subnets_rate_limited(
        self,
        *,
        target_serials: set[str],
        missing_ips: list[str],
        skip_ips: set[str],
    ) -> dict[str, str]:
        """Gently probe local LAN hosts on port 8883 with low concurrency and batch pacing."""
        hosts = build_candidate_scan_hosts(missing_ips, skip_ips)
        if not hosts or not target_serials:
            return {}

        logger.info(
            "Starting rate-limited subnet rediscovery for %d missing printer(s) across %d host(s)",
            len(target_serials),
            len(hosts),
        )
        found: dict[str, str] = {}
        remaining = set(target_serials)

        for idx in range(0, len(hosts), SCAN_CONCURRENCY):
            if not remaining:
                break
            batch = hosts[idx : idx + SCAN_CONCURRENCY]
            results = await asyncio.gather(
                *(self._probe_host_for_serial(ip, remaining) for ip in batch),
                return_exceptions=True,
            )
            for res in results:
                if isinstance(res, tuple):
                    serial, ip = res
                    found[serial] = ip
                    remaining.discard(serial)

            if remaining and (idx + SCAN_CONCURRENCY) < len(hosts):
                await asyncio.sleep(SCAN_BATCH_DELAY_SECONDS)

        logger.info("Rate-limited subnet rediscovery finished (matched %d printer(s))", len(found))
        return found

    async def _verify_and_update_printer(
        self,
        candidate: _MissingPrinter,
        new_ip: str,
        *,
        verify_tls_serial: bool,
    ) -> bool:
        """Verify serial + access code on ``new_ip``, persist the new IP, and reconnect."""
        if not new_ip or new_ip == candidate.ip_address:
            return False

        if verify_tls_serial:
            tls_serial = await probe_port_8883_serial(new_ip)
            if tls_serial != candidate.serial_number:
                logger.warning(
                    "Ignoring SSDP match for %s at %s: port %d TLS serial verification failed (%s)",
                    candidate.serial_number,
                    new_ip,
                    MQTT_PORT,
                    tls_serial or "unavailable",
                )
                return False

        probe = await printer_manager.test_connection(
            ip_address=new_ip,
            serial_number=candidate.serial_number,
            access_code=candidate.access_code,
        )
        if not probe.get("success"):
            logger.warning(
                "Candidate IP %s matched serial %s for printer %s, but access code verification failed (%s)",
                new_ip,
                candidate.serial_number,
                candidate.name,
                probe.get("reason") or "connection failed",
            )
            return False

        async with database.async_session() as db:
            printer = await db.get(Printer, candidate.id)
            if (
                printer is None
                or not printer.is_active
                or _normalize_serial(printer.serial_number or "") != candidate.serial_number
                or printer_manager.is_connected(printer.id)
                or printer.ip_address == new_ip
            ):
                return False

            old_ip = printer.ip_address
            printer.ip_address = new_ip
            await db.commit()
            await db.refresh(printer)

        self._disconnected_since.pop(candidate.id, None)
        logger.info(
            "Rediscovered printer %s (%s): updated IP %s -> %s",
            printer.name,
            printer.serial_number,
            old_ip,
            new_ip,
        )

        # Reconnect the physical MQTT client first (connect_printer disconnects any stale client)
        await printer_manager.connect_printer(printer)

        from backend.app.api.routes.camera import stop_printer_streams
        from backend.app.services.virtual_printer import virtual_printer_manager

        try:
            await stop_printer_streams(printer.id)
        except Exception as exc:
            logger.warning("Failed to reset camera streams after IP rediscovery for printer %s: %s", printer.id, exc)

        try:
            await virtual_printer_manager.sync_from_db()
        except Exception as exc:
            logger.error("Failed to sync virtual printers after IP rediscovery: %s", exc)

        try:
            await ws_manager.broadcast({"type": "printers_changed", "printer_id": printer.id})
        except Exception as exc:
            logger.debug("Failed to broadcast printers_changed after IP rediscovery: %s", exc)

        return True


printer_rediscovery_service = PrinterRediscoveryService()
