"""Connection diagnostic for Bambu printers.

Runs the checks a maintainer performs by hand when triaging a
"printer won't connect / won't print" report — port reachability, LAN
developer mode, container network mode, subnet match, and MQTT credentials —
so users can self-diagnose setup problems instead of opening an issue.

See the 2026-05-21 issue-triage analysis: ~1/3 of closed issues were
user-side setup errors clustered on exactly these causes.
"""

import asyncio
import ipaddress
import logging
import os
import socket
import ssl
import subprocess
import sys
from pathlib import Path

from backend.app.models.printer import Printer
from backend.app.schemas.printer import DiagnosticCheck, PrinterDiagnosticResult
from backend.app.services.bambu_ftp import find_remote_file_async
from backend.app.services.bambu_mqtt import CONNECT_ERROR_AUTH_REJECTED
from backend.app.services.camera import get_camera_port
from backend.app.services.discovery import OCI_RUNTIMES, detect_container_runtime
from backend.app.services.ftp_profiles import get_ftp_profile
from backend.app.services.network_utils import find_local_ipv4_network
from backend.app.services.print_storage import (
    REASON_INTERNAL_STORAGE,
    StorageVerdict,
    ftp_probe_paths,
    last_print_storage_verdict,
)
from backend.app.services.printer_manager import printer_manager
from backend.app.utils.printer_models import has_external_storage, has_remote_storage_toggle

logger = logging.getLogger(__name__)

# Bambu LAN-mode ports.
PORT_MQTT = 8883  # MQTT over TLS — control + status. Connection-critical.
PORT_FTPS = 990  # FTPS — file upload; required to send prints.
PORT_RTSPS = 322  # RTSPS — camera stream; optional.
PORT_CHAMBER_IMAGE = 6000  # Chamber image protocol — A1/P1 camera stream; optional.

_PORT_PROBE_TIMEOUT = 3.0

# Cap for the storage probe (#2856). One connection and a handful of directory
# listings, so a healthy printer answers in well under a second. Kept short on
# purpose: this check sits inside the support bundle's 15s-per-printer budget,
# and in the interactive run it is spinner time the user is watching.
_STORAGE_PROBE_TIMEOUT = 6.0

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


async def _last_print_file_is_reachable(
    printer: Printer | None,
    verdict: StorageVerdict,
    *,
    ftps_ok: bool,
) -> bool:
    """Did the last print's file turn up on external storage after all? (#2856)

    ``verdict`` is read off the dispatch URL, which says where the printer
    *put* the file, not whether port 990 can serve it: an H2D with a card in
    the slot reports ``brtc://emmc/<name>`` and then hands the same file over
    from ``/cache`` without complaint. Warning that the file is out of reach
    while that user's archives are quietly complete would send them chasing a
    setting that is already right.

    So check before saying it -- one connection, one listing per candidate
    directory, no transfer, and only for the check that is about to warn.
    "Could not check" returns False and leaves the warning standing, which is
    the safe direction: it is a warn, not a fail.
    """
    if not ftps_ok or not verdict.probe_filename or printer is None:
        return False
    # Attribute reads inside the guard too: this also runs from the support
    # bundle, where the row may outlive its session, and a detached-instance
    # error there is "could not check", not a broken diagnostic.
    ip_address = None
    try:
        ip_address = getattr(printer, "ip_address", None)
        access_code = getattr(printer, "access_code", None)
        if not ip_address or not access_code:
            return False
        found = await find_remote_file_async(
            ip_address,
            access_code,
            ftp_probe_paths(verdict.probe_filename),
            timeout=_STORAGE_PROBE_TIMEOUT,
            socket_timeout=_STORAGE_PROBE_TIMEOUT,
            printer_model=getattr(printer, "model", None),
        )
    except Exception as e:
        logger.debug("Could not probe %s for %s: %s", ip_address, verdict.probe_filename, e)
        return False
    if found:
        logger.debug("Last print file is on external storage at %s despite %s", found, verdict.reason)
    return found is not None


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


# Interfaces a container engine creates on the *host*. Seeing one of them
# means we are in the host's network namespace.
_HOST_INFRA_PREFIXES = ("docker", "br-", "veth", "virbr", "podman", "cni-", "cni_")


def _has_native_interface() -> bool:
    """True if some interface here was created in this network namespace.

    A NAT-networked container is handed one end of a veth pair per attached
    network, and a veth's ``iflink`` points at its peer's index in the *other*
    namespace, so it never equals its own ``ifindex``. An interface where the
    two agree was made here — a physical NIC, a bridge, a VLAN — which a
    container with its own namespace does not get.

    tun/tap devices are skipped: a container can legitimately run its own
    WireGuard or Tailscale client, and that tun would otherwise read as
    evidence of a namespace it is not evidence of.
    """
    try:
        entries = [(idx, name) for idx, name in socket.if_nameindex() if name != "lo"]
    except Exception:
        return False

    for index, name in entries:
        # Never user input: the kernel's own interface table, and never a path.
        iface = Path("/sys/class/net") / name  # SEC-PATH-OK: name from socket.if_nameindex()
        if (iface / "tun_flags").exists():
            continue
        try:
            ifindex = (iface / "ifindex").read_text().strip()
            iflink = (iface / "iflink").read_text().strip()
        except (OSError, ValueError):
            continue
        # sysfs is tagged by network namespace, but a container given a bind
        # mount of the host's /sys sees the host's interfaces under names that
        # may collide with its own. Reading a different interface's numbers
        # would be reading another namespace's answer, so require that the
        # entry found here is the one the kernel just named.
        if ifindex != str(index):
            continue
        if ifindex == iflink:
            return True
    return False


def _detect_container_network_mode(runtime: str | None) -> str | None:
    """Return "host", "bridge", or None when it genuinely cannot be told.

    The first rule is the original Docker one and is kept exactly: a Docker
    *host* always has a docker0, so a container that can see it shares the
    host's namespace. It says nothing about Podman, which on a host running
    no bridge containers creates no such interface at all — which is how a
    host-networked Podman container came to be told it was on bridge
    networking (#3092).

    The second rule is the general form of the same idea and is what answers
    for Podman. The third is the fallback the first rule always implied: an
    OCI container that can see neither is isolated, which is what bridge
    networking means.
    """
    try:
        for _idx, name in socket.if_nameindex():
            if name.startswith(_HOST_INFRA_PREFIXES):
                return "host"
    except Exception:
        pass
    if _has_native_interface():
        return "host"
    if runtime in OCI_RUNTIMES:
        return "bridge"
    return None


def _host_source_ip(destination_ip: str) -> str | None:
    """The local IPv4 address Bambuddy would send from toward ``destination_ip``.

    Asking about the printer's own address rather than a fixed far-away one
    matters on any host with more than one NIC: the source for a route to the
    internet is simply not the source for a route to the printer, and
    comparing the printer against the wrong interface is a warning about
    nothing (#3092).

    Literals only. ``connect()`` on a name would resolve it, and this runs on
    the event loop; ``_same_subnet`` rejects names anyway, so nothing is lost.
    """
    try:
        if ipaddress.ip_address(destination_ip).version != 4:
            return None
    except ValueError:
        return None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # No packets are sent; this just picks the routing-table source IP.
            s.connect((destination_ip, 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        # Fail soft: this is a diagnostic, and an unroutable address or an
        # exhausted fd table must leave the check skipped, not 500 the page.
        return None


def _same_subnet(printer_ip: str, host_ip: str) -> bool | None:
    """Is ``printer_ip`` inside the network configured on Bambuddy's ``host_ip``?

    None means undeterminable — a name instead of an IPv4 literal, or no
    local interface claiming ``host_ip``.

    An address does not carry its prefix, and this used to supply ``/24`` for
    both sides. That is the most common LAN and not the only one: on the
    reporter's ``192.168.96.0/22`` it declared a printer four hundred
    addresses away to be on a different network and told him to go configure
    routing between two halves of one subnet (#3092). The prefix is read off
    the interface that owns the source address instead.
    """
    try:
        printer_addr = ipaddress.ip_address(printer_ip)
        host_addr = ipaddress.ip_address(host_ip)
    except ValueError:
        return None
    if printer_addr.version != 4 or host_addr.version != 4:
        return None

    network = find_local_ipv4_network(str(host_addr))
    if network is None:
        return None
    return printer_addr in network


# macOS attributes Local Network permission (TCC) to a process's code
# signature, and judges a launchd-spawned process on its own instead of
# letting it inherit the grant of the Terminal that started it. Homebrew's
# Python is unsigned on Intel, so there is no identity for a grant to attach
# to: every connection to a LAN address is dropped, with no error the
# application can log and no permission prompt. All three printer ports read
# as unreachable while the subnet check passes (#3114).
_CODESIGN = "/usr/bin/codesign"
# Reading a local file's signature takes milliseconds, so this is a guard
# rather than a budget -- and it is deliberately short. The support bundle
# gives each printer 15s total (_PER_DIAGNOSTIC_TIMEOUT_SECONDS) and drops
# the whole connection diagnostic on overrun, so a codesign that hangs (the
# stub that offers to install the command line tools is the plausible way)
# must not be able to cost the bundle the rest of its checks.
_CODESIGN_TIMEOUT = 2.0


def _base_interpreter_path() -> str:
    """The interpreter macOS judges, as both the probe and the message see it.

    ``sys._base_executable`` rather than ``sys.executable``: inside a venv the
    latter is a symlink in the venv's own bin directory, and what macOS judges
    is the real interpreter it resolves to. Resolved once, here, so the path
    reported to the user is the same one whose signature was read.
    """
    return os.path.realpath(getattr(sys, "_base_executable", None) or sys.executable)


def _interpreter_is_signed() -> bool | None:
    """Does the interpreter Bambuddy runs under carry a code signature?

    None when it cannot be told: no usable ``codesign`` because the Xcode
    command line tools are absent, or the probe failed some other way. That
    is deliberately not folded into False. The advice for "no identity" names
    a repair that rewrites a file inside the user's Python installation, and
    offering that on a guess is worse than giving the generic answer.

    On an Apple Silicon Homebrew install the interpreter resolves to the
    framework's ``bin/pythonX.Y`` (measured, inside and outside a venv alike)
    -- not the ``Python.app`` stub, which is a separate binary in the same
    framework. The reporter's TCC log names the same ``bin/pythonX.Y`` on
    Intel.
    """
    executable = _base_interpreter_path()
    if not executable:
        return None
    try:
        result = subprocess.run(
            [_CODESIGN, "-d", executable],
            capture_output=True,
            text=True,
            timeout=_CODESIGN_TIMEOUT,
        )
    except Exception:
        # Fail soft, as everywhere else in this module: a diagnostic that
        # raises is worse than one that declines to answer.
        logger.debug("codesign probe failed", exc_info=True)
        return None
    if result.returncode == 0:
        return True
    # codesign writes this to stderr and exits non-zero. It is the one
    # outcome that separates "no identity at all" from "the probe never ran".
    if "not signed at all" in result.stderr:
        return False
    return None


async def run_connection_diagnostic(
    ip_address: str,
    *,
    printer: Printer | None = None,
    serial_number: str | None = None,
    access_code: str | None = None,
    wait_for_publish_seconds: float = 0.0,
    progress: dict | None = None,
) -> PrinterDiagnosticResult:
    """Run connection checks for a printer.

    Works for an existing saved printer (pass ``printer``) and for the
    pre-save Add-Printer flow (pass ``serial_number`` + ``access_code``).

    Each check carries a stable ``id`` and a ``status`` of
    pass / fail / warn / skip; the frontend renders the human-readable
    title and fix text (localized) keyed on that id + status.

    ``progress``, when given, is kept current while the checks run: its
    ``checks`` key is the list of finished checks and ``stage`` names the step
    in flight. The support bundle cancels a run that overruns its budget, and
    this is how it can still say which step hung and keep what finished --
    a bare "timed out" for every printer is all #3164's bundle could show.
    """
    checks: list[DiagnosticCheck] = []
    if progress is None:
        progress = {}
    progress["checks"] = checks

    # --- Port reachability (probed in parallel) ---
    progress["stage"] = "ports"
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

    # --- macOS Local Network permission ---
    progress["stage"] = "macos_local_network"
    # Appended on macOS only. Everywhere else there is nothing to say, and a
    # permanently dimmed "skipped" row would be noise for the users who make
    # up nearly all of them.
    #
    # Both outcomes are reported as warn rather than fail, and only when the
    # control port is already unreachable -- so this can never be the check
    # that turns an otherwise healthy result red. A printer that is simply
    # switched off produces the same all-ports-dead pattern, which is why the
    # signature probe, not the pattern, is what earns the specific advice.
    if sys.platform == "darwin":
        if mqtt_ok:
            # The control port answered, so LAN access demonstrably works.
            checks.append(DiagnosticCheck(id="macos_local_network", status="pass"))
        else:
            signed = await asyncio.to_thread(_interpreter_is_signed)
            if signed is False:
                checks.append(
                    DiagnosticCheck(
                        id="macos_local_network",
                        status="warn",
                        params={"reason": "unsigned", "executable": _base_interpreter_path()},
                    )
                )
            else:
                # Signed, or undeterminable. An ad-hoc signature -- which is
                # what every arm64 binary carries, because the linker adds one
                # -- identifies itself by a hash of the binary, so a Python
                # upgrade presents macOS with a new application and leaves the
                # old grant behind. That is repairable in System Settings,
                # unlike the unsigned case, so point there instead.
                checks.append(DiagnosticCheck(id="macos_local_network", status="warn", params={"reason": "permission"}))

    # --- Container network mode ---
    progress["stage"] = "network_mode"
    # Not Docker-only: Podman runs Bambuddy in exactly the same two shapes and
    # its users were told "Not running in Docker", which reads as "you are on
    # bare metal" and sent them looking for the problem somewhere else (#3092).
    runtime = detect_container_runtime()
    network_mode: str | None = None
    if runtime is None:
        checks.append(DiagnosticCheck(id="network_mode", status="skip"))
    elif runtime not in OCI_RUNTIMES:
        # An LXC/LXD system container is bridged onto the LAN like a small VM.
        # There is no network mode to recommend, so don't imply there is one.
        checks.append(
            DiagnosticCheck(id="network_mode", status="skip", params={"reason": "system_container", "runtime": runtime})
        )
    else:
        network_mode = _detect_container_network_mode(runtime)
        if network_mode is None:
            checks.append(
                DiagnosticCheck(id="network_mode", status="skip", params={"reason": "unknown", "runtime": runtime})
            )
        else:
            checks.append(
                DiagnosticCheck(
                    id="network_mode",
                    status="pass" if network_mode == "host" else "warn",
                    params={"mode": network_mode, "runtime": runtime},
                )
            )

    # --- Subnet match ---
    progress["stage"] = "subnet"
    # Skipped in bridge mode: the container IP is the bridge IP, not the host's,
    # so the comparison is meaningless and the network_mode check already covers it.
    if network_mode == "bridge":
        checks.append(DiagnosticCheck(id="subnet", status="skip"))
    else:
        host_ip = _host_source_ip(ip_address)
        # Off the loop: resolving the prefix shells out to `ip -j addr show`.
        same = await asyncio.to_thread(_same_subnet, ip_address, host_ip) if host_ip else None
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
    progress["stage"] = "external_storage"
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
    elif not (last_verdict := last_print_storage_verdict(state)).reachable and not await _last_print_file_is_reachable(
        printer, last_verdict, ftps_ok=ftps_state == "ok"
    ):
        # The toggle is on, a card is in, the printer said the last print's file
        # is on internal storage — and a probe confirmed it really is out of
        # reach. That is what H2-series, P2S and X2D firmware does, and no setting
        # here changes it (#2762 tracks reading that storage). A pass here would
        # be a lie; a fail would be unresolvable.
        #
        # The verdict's own reason, not a fixed one: a print started from the
        # printer's screen reaches this branch too, and it never involved a
        # slicer, so the advice attached to REASON_INTERNAL_STORAGE would name a
        # dialog its operator never opened (#1820).
        checks.append(
            DiagnosticCheck(
                id="external_storage",
                status="warn",
                params={"reason": last_verdict.reason or REASON_INTERNAL_STORAGE},
            )
        )
    elif not last_verdict.reachable:
        # Reached only when the probe above found the file: the printer named
        # internal storage and served it over FTPS anyway. What this check is
        # for is whether Bambuddy can read the print file, and it demonstrably
        # can — so pass, whatever the toggle happens to say (#2856).
        checks.append(DiagnosticCheck(id="external_storage", status="pass"))
    elif store_to_sdcard is True:
        checks.append(DiagnosticCheck(id="external_storage", status="pass"))
    elif store_to_sdcard is False:
        checks.append(DiagnosticCheck(id="external_storage", status="fail"))
    else:
        # State exists but the field was never populated — skip rather than
        # report a false fail.
        checks.append(DiagnosticCheck(id="external_storage", status="skip"))

    # --- MQTT credentials / connection ---
    progress["stage"] = "mqtt_auth"
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
    progress["stage"] = "developer_mode"
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
    progress["stage"] = "printer_publishing"
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
