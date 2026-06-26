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
from backend.app.services.camera import get_camera_port
from backend.app.services.discovery import is_running_in_docker
from backend.app.services.printer_manager import printer_manager
from backend.app.utils.printer_models import has_external_storage

logger = logging.getLogger(__name__)

# Bambu LAN-mode ports.
PORT_MQTT = 8883  # MQTT over TLS — control + status. Connection-critical.
PORT_FTPS = 990  # FTPS — file upload; required to send prints.
PORT_RTSPS = 322  # RTSPS — camera stream; optional.
PORT_CHAMBER_IMAGE = 6000  # Chamber image protocol — A1/P1 camera stream; optional.

_PORT_PROBE_TIMEOUT = 3.0

# Default seconds the `printer_publishing` check will wait for the first
# report-topic message before declaring fail. Bambu printers in idle publish
# push_status every few seconds; 10s catches healthy bridges with margin while
# staying short enough that the spinner-with-countdown UX stays acceptable.
# The check exits the moment a message arrives, so the typical wall-clock is
# 1–2s, not the full 10. Passed as ``wait_for_publish_seconds`` per call so
# the support-package code path can skip the wait entirely (defaults to 0).
PUBLISH_WAIT_DEFAULT = 10.0
_PUBLISH_POLL_INTERVAL = 0.5


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


def _camera_port_for_printer(printer: Printer | None) -> tuple[int, str]:
    """Return the model-specific camera diagnostic port and display protocol."""
    if not printer:
        return PORT_RTSPS, "RTSPS"
    model = getattr(printer, "model", None)
    if not model:
        return PORT_RTSPS, "RTSPS"
    camera_port = get_camera_port(model)
    if camera_port == PORT_CHAMBER_IMAGE:
        return camera_port, "Chamber Image"
    return camera_port, "RTSPS"


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
    wait_for_publish_seconds: float = 0.0,
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
    camera_port, camera_protocol = _camera_port_for_printer(printer)
    mqtt_ok, ftps_ok, camera_ok = await asyncio.gather(
        _check_port(ip_address, PORT_MQTT),
        _check_port(ip_address, PORT_FTPS),
        _check_port(ip_address, camera_port),
    )
    # MQTT is connection-critical; FTPS/camera only degrade printing/camera.
    checks.append(DiagnosticCheck(id="port_mqtt", status="pass" if mqtt_ok else "fail"))
    checks.append(DiagnosticCheck(id="port_ftps", status="pass" if ftps_ok else "warn"))
    checks.append(
        DiagnosticCheck(
            id="port_rtsps",
            status="pass" if camera_ok else "warn",
            params={"port": camera_port, "protocol": camera_protocol},
        )
    )

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

    # --- External storage (printer-side "Store sent files on external storage") ---
    # Install step 4. The setting has two variants depending on
    # firmware/slicer combo: on newer firmware the toggle lives on the
    # printer (P2S 01.02 / BambuStudio 2.6+), on older versions it's
    # purely a slicer-side preference.
    #
    # For the printer-side variant, `home_flag` bit 11 is pushed on every
    # status report and parsed into state.store_to_sdcard (bambu_mqtt.py
    # line 153). That's the signal here — instant, no FTP I/O.
    #
    # For the slicer-side variant, the printer never hears about it and
    # this check will pass even when the user is missing step 4. That gap
    # is covered separately by the "no_3mf_available" archive-fallback
    # banner. An FTP upload-and-verify probe was tried and rejected — the
    # /cache directory is always writable from Bambuddy regardless of
    # either toggle, so the probe always passes and detects nothing.
    #
    # Skip entirely on models with no external-storage slot at all (A1
    # and A1 Mini). They never set home_flag bit 11, so a naive read of
    # `store_to_sdcard` would fall through to a false `fail` for every
    # A1-series user (#1703).
    state = printer_manager.get_status(printer.id) if printer else None
    model_has_slot = has_external_storage(getattr(printer, "model", None)) if printer else True
    if not model_has_slot or state is None or not state.connected:
        checks.append(DiagnosticCheck(id="external_storage", status="skip"))
    elif getattr(state, "store_to_sdcard", None) is True:
        checks.append(DiagnosticCheck(id="external_storage", status="pass"))
    elif getattr(state, "store_to_sdcard", None) is False:
        checks.append(DiagnosticCheck(id="external_storage", status="fail"))
    else:
        # State exists but the field was never populated — skip rather than
        # report a false fail.
        checks.append(DiagnosticCheck(id="external_storage", status="skip"))

    # --- MQTT credentials / connection ---
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

    # --- Printer is actually publishing on its report topic ---
    # The mqtt_auth check above only proves TCP + TLS + auth + SUBSCRIBE
    # succeed. A printer with a wrong-cased serial — or one that simply isn't
    # publishing for some other reason — still passes mqtt_auth because the
    # broker accepts the subscription regardless. The user-visible symptom in
    # that case is "AMS / K-profiles / custom filaments missing on the slicer
    # side": the VP bridge has nothing cached to mirror because no reports
    # arrived. #1622 surfaced this: bridge keep-alive timeouts paired with
    # the `Connected and subscribed, but the printer has sent zero status
    # reports` warning. The check below turns that warning into a structured
    # diagnostic result the user can act on without grepping container logs.
    #
    # If ``_report_messages_since_connect`` is already > 0, we exit
    # immediately — the bridge has seen reports. If it's 0 and a wait is
    # requested, we poll every PUBLISH_POLL_INTERVAL up to
    # ``wait_for_publish_seconds`` so a fresh reconnect (counter reset to 0)
    # isn't reported as fail before the printer's first idle push lands.
    publishing_params: dict[str, int | float] | None = None
    publishing_status = "skip"
    if printer is not None and state is not None and state.connected:
        client = printer_manager.get_client(printer.id)
        if client is not None:
            wait_budget = max(wait_for_publish_seconds, 0.0)
            if wait_budget > 0:
                # Expose the budget so the UI can render a countdown next to
                # the spinner — the user knows how long this check might take.
                publishing_params = {"max_wait_seconds": wait_budget}
            loop = asyncio.get_running_loop()
            deadline = loop.time() + wait_budget
            while True:
                if client.report_messages_since_connect > 0:
                    publishing_status = "pass"
                    break
                if loop.time() >= deadline:
                    publishing_status = "fail"
                    break
                await asyncio.sleep(_PUBLISH_POLL_INTERVAL)
    checks.append(
        DiagnosticCheck(
            id="printer_publishing",
            status=publishing_status,
            params=publishing_params or {},
        )
    )

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
