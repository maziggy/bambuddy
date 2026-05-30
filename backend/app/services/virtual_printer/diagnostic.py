"""Setup diagnostic for a virtual printer.

A virtual printer fails for the user in ways a real printer never does: the
bind IP no longer exists after a host/network change, a service silently
failed to bind its port, the access code was never set, the slicer was never
told to trust the CA. The manager swallows per-service start errors
(``run_with_logging`` in ``start_server``), so a service object can exist
while nothing is actually listening — the only reliable signal is probing the
bind IP's ports from the outside, which is what this does.

Each check carries a stable ``id`` and a ``status`` of pass / fail / warn /
skip; the frontend renders the localized title and fix text keyed on that
id + status.
"""

import asyncio
import logging

from backend.app.models.virtual_printer import VirtualPrinter
from backend.app.schemas.printer import DiagnosticCheck
from backend.app.schemas.virtual_printer import VPDiagnosticResult

logger = logging.getLogger(__name__)

# Server-mode listening ports — see virtual_printer/manager.py start_server().
PORT_FTPS = 990  # implicit FTPS — slicer file upload
PORT_MQTT = 8883  # MQTT over TLS — control + status
PORT_BIND = 3002  # bind/detect (TLS) — slicer discovery handshake
PORT_BIND_PLAIN = 3000  # bind/detect (plain) — legacy / some slicer models

_PORT_PROBE_TIMEOUT = 2.0


async def _check_port(ip: str, port: int, timeout: float = _PORT_PROBE_TIMEOUT) -> bool:
    """Test TCP connectivity to ip:port. Returns True if something is listening."""
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


async def run_vp_diagnostic(vp: VirtualPrinter, instance) -> VPDiagnosticResult:
    """Run setup checks for a virtual printer.

    Args:
        vp: The virtual printer DB row.
        instance: The running ``VirtualPrinterInstance`` from the manager, or
            ``None`` if the VP is not currently instantiated.
    """
    checks: list[DiagnosticCheck] = []
    is_proxy = vp.mode == "proxy"
    running = bool(instance and instance.is_running)

    # --- VP enabled ---
    checks.append(DiagnosticCheck(id="enabled", status="pass" if vp.enabled else "fail"))

    # --- Instance running ---
    if not vp.enabled:
        checks.append(DiagnosticCheck(id="running", status="skip"))
    else:
        checks.append(DiagnosticCheck(id="running", status="pass" if running else "fail"))

    # --- Bind interface still exists ---
    # A bind IP picked weeks ago can vanish after a Docker restart or a router
    # handing out a different lease — the VP then binds nothing and is invisible.
    if not vp.bind_ip:
        checks.append(DiagnosticCheck(id="bind_interface", status="fail"))
    else:
        from backend.app.services.network_utils import find_interface_for_ip

        iface = find_interface_for_ip(vp.bind_ip)
        checks.append(
            DiagnosticCheck(
                id="bind_interface",
                status="pass" if iface else "fail",
                params={"bind_ip": vp.bind_ip},
            )
        )

    # --- Access code (non-proxy modes only) ---
    if is_proxy:
        checks.append(DiagnosticCheck(id="access_code", status="skip"))
    else:
        checks.append(DiagnosticCheck(id="access_code", status="pass" if vp.access_code else "fail"))

    # --- Target printer (proxy mode only) ---
    if not is_proxy:
        checks.append(DiagnosticCheck(id="target_printer", status="skip"))
    elif not vp.target_printer_id:
        checks.append(DiagnosticCheck(id="target_printer", status="fail"))
    else:
        from backend.app.services.printer_manager import printer_manager

        state = printer_manager.get_status(vp.target_printer_id)
        online = bool(state and state.connected)
        # A configured-but-offline target degrades proxying but isn't a setup
        # error on the VP's side — warn rather than fail.
        checks.append(DiagnosticCheck(id="target_printer", status="pass" if online else "warn"))

    # --- Service ports actually listening on the bind IP ---
    # The decisive check: a service object can exist while its socket never
    # bound (port already in use, permission denied) because start errors are
    # logged and swallowed. Probe the bind IP directly.
    bind_ip = vp.bind_ip
    if not running or not bind_ip:
        for cid, port in (("port_ftps", PORT_FTPS), ("port_mqtt", PORT_MQTT), ("port_bind", PORT_BIND)):
            checks.append(DiagnosticCheck(id=cid, status="skip", params={"port": port}))
    elif is_proxy:
        # Proxy mode listens on dynamic ports reported by the proxy manager,
        # and runs no bind/detect server.
        proxy_status = instance.get_status().get("proxy", {})
        ftp_port = proxy_status.get("ftp_port")
        mqtt_port = proxy_status.get("mqtt_port")
        ftp_ok = await _check_port(bind_ip, ftp_port) if ftp_port else False
        mqtt_ok = await _check_port(bind_ip, mqtt_port) if mqtt_port else False
        checks.append(
            DiagnosticCheck(
                id="port_ftps",
                status="pass" if ftp_ok else "fail",
                params={"port": ftp_port or PORT_FTPS},
            )
        )
        checks.append(
            DiagnosticCheck(
                id="port_mqtt",
                status="pass" if mqtt_ok else "fail",
                params={"port": mqtt_port or PORT_MQTT},
            )
        )
        checks.append(DiagnosticCheck(id="port_bind", status="skip", params={"port": PORT_BIND}))
    else:
        # The non-proxy bind server listens on BOTH 3000 (plain) and 3002
        # (TLS) per bind_server.py BIND_PORTS — slicers pick either path.
        # Probing only 3002 missed half-dead VPs where one listener failed
        # to start and the other succeeded; report port_bind as pass only
        # when both probes succeed.
        ftp_ok, mqtt_ok, bind_tls_ok, bind_plain_ok = await asyncio.gather(
            _check_port(bind_ip, PORT_FTPS),
            _check_port(bind_ip, PORT_MQTT),
            _check_port(bind_ip, PORT_BIND),
            _check_port(bind_ip, PORT_BIND_PLAIN),
        )
        checks.append(DiagnosticCheck(id="port_ftps", status="pass" if ftp_ok else "fail", params={"port": PORT_FTPS}))
        checks.append(DiagnosticCheck(id="port_mqtt", status="pass" if mqtt_ok else "fail", params={"port": PORT_MQTT}))
        checks.append(
            DiagnosticCheck(
                id="port_bind",
                status="pass" if (bind_tls_ok and bind_plain_ok) else "fail",
                params={"port": PORT_BIND, "port_plain": PORT_BIND_PLAIN},
            )
        )

    # --- TLS certificate ---
    # When running, the cert chain must exist on disk for the slicer's TLS
    # handshake to succeed. This is a pass/fail on the file; the localized
    # detail text reminds the user to import the CA into the slicer.
    if not running:
        checks.append(DiagnosticCheck(id="certificate", status="skip"))
    else:
        cert_ok = bool(instance and instance.cert_path.exists())
        checks.append(DiagnosticCheck(id="certificate", status="pass" if cert_ok else "fail"))

    statuses = {c.status for c in checks}
    if "fail" in statuses:
        overall = "problems"
    elif "warn" in statuses:
        overall = "warnings"
    else:
        overall = "ok"

    return VPDiagnosticResult(
        vp_id=vp.id,
        vp_name=vp.name,
        mode=vp.mode,
        overall=overall,
        checks=checks,
    )
