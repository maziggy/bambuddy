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
import ssl

from backend.app.models.printer import Printer
from backend.app.schemas.printer import DiagnosticCheck, PrinterDiagnosticResult
from backend.app.services.bambu_mqtt import CONNECT_ERROR_AUTH_REJECTED
from backend.app.services.camera import get_camera_port
from backend.app.services.discovery import is_running_in_docker
from backend.app.services.ftp_profiles import get_ftp_profile
from backend.app.services.print_storage import REASON_INTERNAL_STORAGE, last_print_storage_verdict
from backend.app.services.printer_manager import printer_manager
from backend.app.utils.printer_models import has_external_storage, has_remote_storage_toggle

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


# Public alias. The connection watchdog probes the MQTT port before rebuilding a
# client, so it can tell "the printer is switched off" (leave it alone, paho will
# keep retrying) from "the printer is answering but our session is dead" (#2732).
check_port = _check_port


async def _check_ftps_tls(ip: str, model: str | None, timeout: float = _PORT_PROBE_TIMEOUT) -> str:
    """Probe port 990 the way the FTP client does, and say how far it got.

    Returns ``"ok"``, ``"closed"`` (nothing accepted the TCP connection) or
    ``"no_tls"`` (the port accepted the connection but the TLS handshake did
    not complete).

    A plain TCP probe cannot tell the last two apart, which is exactly how
    #2780 hid: the reporter's diagnostic reported port 990 as reachable and
    green while every real transfer died in the handshake with
    ``WRONG_VERSION_NUMBER``, so archives quietly arrived empty with nothing
    on screen to explain it.

    The context mirrors :class:`~backend.app.services.bambu_ftp.ImplicitFTP_TLS`
    -- including the model's TLS cap -- so a pass here means the FTP client
    would also get through. Handshake only; no login is attempted, so this
    stays valid for the pre-save Add-Printer flow where no access code exists
    yet.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if get_ftp_profile(model).cap_tls_v1_2:
        context.maximum_version = ssl.TLSVersion.TLSv1_2

    writer = None
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, PORT_FTPS, ssl=context),
            timeout=timeout,
        )
        return "ok"
    except ssl.SSLError:
        # The socket was accepted and then failed to negotiate TLS. Reaching
        # here at all proves something is listening, so this is never "port
        # blocked" -- it is the printer's file service in a state no retry
        # gets past.
        return "no_tls"
    except Exception:
        return "closed"
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


def _auth_reason_params(reason: str | None) -> dict:
    """Map a client's CONNACK-refusal slug onto the check's `params.reason`.

    The frontend renders `diagnostic.check.<id>.<status>_<reason>` when a reason
    is present and falls back to the plain per-status text otherwise, so an
    unknown or absent slug degrades to today's generic wording rather than a
    missing string. Only `auth_rejected` currently carries its own message:
    that is the one case where the printer positively told us the credentials
    were wrong, as opposed to us merely observing that we are not connected.
    """
    if reason == CONNECT_ERROR_AUTH_REJECTED:
        return {"reason": CONNECT_ERROR_AUTH_REJECTED}
    return {}


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
    mqtt_ok, ftps_state, camera_ok = await asyncio.gather(
        _check_port(ip_address, PORT_MQTT),
        _check_ftps_tls(ip_address, getattr(printer, "model", None) if printer else None),
        _check_port(ip_address, camera_port),
    )
    # MQTT is connection-critical; FTPS/camera only degrade printing/camera.
    checks.append(DiagnosticCheck(id="port_mqtt", status="pass" if mqtt_ok else "fail"))
    # "no_tls" gets its own message: the port is open, so the usual advice
    # (unblock port 990) is wrong and only a printer restart helps (#2780).
    checks.append(
        DiagnosticCheck(
            id="port_ftps",
            status="pass" if ftps_state == "ok" else "warn",
            params={} if ftps_state != "no_tls" else {"reason": "no_tls"},
        )
    )
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
    #
    # Some models (P1-series) DO have a slot but no reachable control to turn
    # the option on: the Bambu Studio toggle only appears when the printer
    # publishes `support_save_remote_print_file_to_storage`, which current
    # P1 firmware never does, and the P1S/P1P have no screen. For those,
    # `store_to_sdcard` is stuck False with no way to fix it — report `skip`
    # (with a reason the UI explains) instead of a permanently-red `fail`
    # (#2524).
    state = printer_manager.get_status(printer.id) if printer else None
    model = getattr(printer, "model", None) if printer else None
    model_has_slot = has_external_storage(model) if printer else True
    store_to_sdcard = getattr(state, "store_to_sdcard", None) if state else None
    if not model_has_slot or state is None or not state.connected:
        checks.append(DiagnosticCheck(id="external_storage", status="skip"))
    elif store_to_sdcard is False and not has_remote_storage_toggle(model):
        # Slot present but no way to enable it on this firmware — don't nag
        # with an unresolvable fail; explain why via the reason param.
        #
        # Ahead of the empty-slot check below on purpose (#2524 over #2780):
        # on a P1-series the toggle cannot be switched on at all, so telling
        # the operator to insert a card would promise a fix that inserting a
        # card does not deliver.
        checks.append(
            DiagnosticCheck(
                id="external_storage",
                status="skip",
                params={"reason": "unsupported_model"},
            )
        )
    elif getattr(state, "sdcard_reported", False) and not getattr(state, "sdcard", False):
        # The toggle can be on and still achieve nothing with an empty slot,
        # and that combination used to report a clean pass — #2780's H2C had
        # `store_to_sdcard` set and `sdcard` False for three solid weeks while
        # every one of its archives came out blank. Report the empty slot,
        # which is the part the operator can actually act on.
        checks.append(
            DiagnosticCheck(
                id="external_storage",
                status="fail",
                params={"reason": "no_media"},
            )
        )
    elif not last_print_storage_verdict(state).reachable:
        # The toggle is on, a card is in, and the printer still put the last
        # print on internal storage — which is what H2-series and P2S firmware
        # does, and no setting here changes it (#2762 tracks reading that
        # storage). A pass here would be a lie; a fail would be unresolvable.
        checks.append(
            DiagnosticCheck(
                id="external_storage",
                status="warn",
                params={"reason": REASON_INTERNAL_STORAGE},
            )
        )
    elif store_to_sdcard is True:
        checks.append(DiagnosticCheck(id="external_storage", status="pass"))
    elif store_to_sdcard is False:
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
            checks.append(
                DiagnosticCheck(
                    id="mqtt_auth",
                    status="pass" if result.get("success") else "fail",
                    params=_auth_reason_params(result.get("reason")),
                )
            )
        except Exception:
            logger.debug("test_connection failed during diagnostic", exc_info=True)
            checks.append(DiagnosticCheck(id="mqtt_auth", status="fail"))
    elif state is not None:
        # Existing printer: trust the live MQTT state rather than opening a
        # second connection (Bambu printers tolerate few concurrent sessions).
        # `connected == False` alone does not say *why* — the live client keeps
        # the last CONNACK refusal, so a rejected access code can be reported as
        # such instead of as a generic failure the user has to guess at (#2698).
        client = printer_manager.get_client(printer.id) if printer else None
        checks.append(
            DiagnosticCheck(
                id="mqtt_auth",
                status="pass" if state.connected else "fail",
                params={} if state.connected else _auth_reason_params(getattr(client, "last_connect_error", None)),
            )
        )
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
