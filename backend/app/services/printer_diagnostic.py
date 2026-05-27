"""Connection diagnostic for Bambu printers.

Runs the checks a maintainer performs by hand when triaging a
"printer won't connect / won't print" report — port reachability, LAN
developer mode, Docker network mode, subnet match, and MQTT credentials —
so users can self-diagnose setup problems instead of opening an issue.

See the 2026-05-21 issue-triage analysis: ~1/3 of closed issues were
user-side setup errors clustered on exactly these causes.
"""

import asyncio
import ipaddress
import logging
import socket

from backend.app.models.printer import Printer
from backend.app.schemas.printer import DiagnosticCheck, PrinterDiagnosticResult
from backend.app.services.discovery import is_running_in_docker
from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

# Bambu LAN-mode ports.
PORT_MQTT = 8883  # MQTT over TLS — control + status. Connection-critical.
PORT_FTPS = 990  # FTPS — file upload; required to send prints.
PORT_RTSPS = 322  # RTSPS — camera stream; optional.

_PORT_PROBE_TIMEOUT = 3.0


async def _check_port(ip: str, port: int, timeout: float = _PORT_PROBE_TIMEOUT) -> bool:
    """Test TCP connectivity to ip:port. Returns True if reachable."""
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _detect_docker_network_mode() -> str:
    """Detect Docker network mode.

    In host mode the container shares the host network namespace, so Docker
    infrastructure interfaces (docker0, br-*, veth*) are visible. In bridge
    mode the container only sees its own eth0.
    """
    try:
        for _idx, name in socket.if_nameindex():
            if name.startswith(("docker", "br-", "veth", "virbr")):
                return "host"
    except Exception:
        pass
    return "bridge"


def _get_host_ip() -> str | None:
    """Best-effort IPv4 address the Bambuddy host routes from."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # No packets are sent; this just picks the routing-table source IP.
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return None


def _same_subnet(ip_a: str, ip_b: str) -> bool | None:
    """True/False if both are IPv4 literals in the same /24; None if undeterminable."""
    try:
        addr_a = ipaddress.ip_address(ip_a)
        addr_b = ipaddress.ip_address(ip_b)
    except ValueError:
        return None
    if addr_a.version != 4 or addr_b.version != 4:
        return None
    net_a = ipaddress.ip_network(f"{addr_a}/24", strict=False)
    net_b = ipaddress.ip_network(f"{addr_b}/24", strict=False)
    return net_a == net_b


async def run_connection_diagnostic(
    ip_address: str,
    *,
    printer: Printer | None = None,
    serial_number: str | None = None,
    access_code: str | None = None,
) -> PrinterDiagnosticResult:
    """Run connection checks for a printer.

    Works for an existing saved printer (pass ``printer``) and for the
    pre-save Add-Printer flow (pass ``serial_number`` + ``access_code``).

    Each check carries a stable ``id`` and a ``status`` of
    pass / fail / warn / skip; the frontend renders the human-readable
    title and fix text (localized) keyed on that id + status.
    """
    checks: list[DiagnosticCheck] = []

    # --- Port reachability (probed in parallel) ---
    mqtt_ok, ftps_ok, rtsps_ok = await asyncio.gather(
        _check_port(ip_address, PORT_MQTT),
        _check_port(ip_address, PORT_FTPS),
        _check_port(ip_address, PORT_RTSPS),
    )
    # MQTT is connection-critical; FTPS/RTSPS only degrade printing/camera.
    checks.append(DiagnosticCheck(id="port_mqtt", status="pass" if mqtt_ok else "fail"))
    checks.append(DiagnosticCheck(id="port_ftps", status="pass" if ftps_ok else "warn"))
    checks.append(DiagnosticCheck(id="port_rtsps", status="pass" if rtsps_ok else "warn"))

    # --- Docker network mode ---
    network_mode: str | None = None
    if is_running_in_docker():
        network_mode = _detect_docker_network_mode()
        checks.append(
            DiagnosticCheck(
                id="network_mode",
                status="pass" if network_mode == "host" else "warn",
                params={"mode": network_mode},
            )
        )
    else:
        checks.append(DiagnosticCheck(id="network_mode", status="skip"))

    # --- Subnet match ---
    # Skipped in bridge mode: the container IP is the bridge IP, not the host's,
    # so the comparison is meaningless and the network_mode check already covers it.
    if network_mode == "bridge":
        checks.append(DiagnosticCheck(id="subnet", status="skip"))
    else:
        host_ip = _get_host_ip()
        same = _same_subnet(ip_address, host_ip) if host_ip else None
        if same is None:
            checks.append(DiagnosticCheck(id="subnet", status="skip"))
        else:
            checks.append(
                DiagnosticCheck(
                    id="subnet",
                    status="pass" if same else "warn",
                    params={"printer_ip": ip_address, "host_ip": host_ip},
                )
            )

    # --- MQTT credentials / connection ---
    state = printer_manager.get_status(printer.id) if printer else None
    if not mqtt_ok:
        # Can't reach the broker at all — the port check already reported it.
        checks.append(DiagnosticCheck(id="mqtt_auth", status="skip"))
    elif serial_number and access_code:
        # Pre-add flow: actively probe with the credentials the user entered.
        try:
            result = await printer_manager.test_connection(
                ip_address=ip_address,
                serial_number=serial_number,
                access_code=access_code,
            )
            checks.append(DiagnosticCheck(id="mqtt_auth", status="pass" if result.get("success") else "fail"))
        except Exception:
            logger.debug("test_connection failed during diagnostic", exc_info=True)
            checks.append(DiagnosticCheck(id="mqtt_auth", status="fail"))
    elif state is not None:
        # Existing printer: trust the live MQTT state rather than opening a
        # second connection (Bambu printers tolerate few concurrent sessions).
        checks.append(DiagnosticCheck(id="mqtt_auth", status="pass" if state.connected else "fail"))
    else:
        checks.append(DiagnosticCheck(id="mqtt_auth", status="skip"))

    # --- LAN developer mode (only readable over a live MQTT connection) ---
    if state is not None and state.connected:
        if state.developer_mode is True:
            dev_status = "pass"
        elif state.developer_mode is False:
            dev_status = "fail"
        else:
            dev_status = "skip"
        checks.append(DiagnosticCheck(id="developer_mode", status=dev_status))
    else:
        checks.append(DiagnosticCheck(id="developer_mode", status="skip"))

    statuses = {c.status for c in checks}
    if "fail" in statuses:
        overall = "problems"
    elif "warn" in statuses:
        overall = "warnings"
    else:
        overall = "ok"

    return PrinterDiagnosticResult(
        printer_id=printer.id if printer else None,
        ip_address=ip_address,
        overall=overall,
        checks=checks,
    )
