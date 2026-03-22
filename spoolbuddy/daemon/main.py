#!/usr/bin/env python3
"""SpoolBuddy daemon — reads NFC tags and scale, pushes events to Bambuddy backend."""

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# Add scripts/ to sys.path so hardware drivers (read_tag, scale_diag) are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from . import __version__
from .api_client import APIClient
from .config import Config
from .display_control import DisplayControl
from .nfc_reader import NFCReader, NFCState
from .scale_reader import ScaleReader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("spoolbuddy")


def _spoolbuddy_env_path() -> Path:
    # installer writes this at <install>/spoolbuddy/.env; allow override for custom setups/tests
    override = os.environ.get("SPOOLBUDDY_ENV_FILE", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / ".env"


def _set_env_value(path: Path, key: str, value: str):
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    updated = False
    new_lines: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f"{key}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _schedule_service_restart():
    # Restart getty and spoolbuddy shortly after responding, so this process can finish current cycle.
    subprocess.Popen(
        [
            "sh",
            "-c",
            "sleep 1; systemctl restart getty@tty1; systemctl restart spoolbuddy",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _get_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


async def nfc_poll_loop(config: Config, api: APIClient, shared: dict):
    """Continuous NFC polling loop — runs in asyncio with blocking reads offloaded."""
    display: DisplayControl = shared["display"]

    try:
        while True:
            if shared.get("nfc_scan_paused", False):
                await asyncio.sleep(config.nfc_poll_interval)
                continue

            nfc: NFCReader | None = shared.get("nfc")
            if not nfc or not nfc.ok:
                await asyncio.sleep(config.nfc_poll_interval)
                continue

            event_type, event_data = await asyncio.to_thread(nfc.poll)

            if event_type == "tag_detected":
                display.wake()
                await api.tag_scanned(
                    device_id=config.device_id,
                    tag_uid=event_data["tag_uid"],
                    tray_uuid=event_data.get("tray_uuid"),
                    sak=event_data.get("sak"),
                    tag_type=event_data.get("tag_type"),
                )
            elif event_type == "tag_removed":
                await api.tag_removed(
                    device_id=config.device_id,
                    tag_uid=event_data["tag_uid"],
                )

            # Check for pending write command
            pending = shared.get("pending_write")
            if pending and nfc.state == NFCState.TAG_PRESENT:
                if nfc.current_sak == 0x00:
                    logger.info("Executing pending tag write for spool %d", pending["spool_id"])
                    success, msg = await asyncio.to_thread(nfc.write_ntag, pending["ndef_data"])
                    await api.write_tag_result(
                        device_id=config.device_id,
                        spool_id=pending["spool_id"],
                        tag_uid=nfc.current_uid or "",
                        success=success,
                        message=msg,
                    )
                    shared.pop("pending_write", None)
                else:
                    # Fail fast when a non-NTAG is presented during write mode.
                    # Without this, UI can appear stuck on "waiting for SpoolBuddy".
                    sak = nfc.current_sak
                    await api.write_tag_result(
                        device_id=config.device_id,
                        spool_id=pending["spool_id"],
                        tag_uid=nfc.current_uid or "",
                        success=False,
                        message=f"Incompatible tag type (SAK=0x{sak:02X}). Place an NTAG tag to write.",
                    )
                    logger.warning(
                        "Write aborted for spool %d: incompatible tag type SAK=0x%02X",
                        pending["spool_id"],
                        sak,
                    )
                    shared.pop("pending_write", None)

            await asyncio.sleep(config.nfc_poll_interval)
    finally:
        nfc: NFCReader | None = shared.get("nfc")
        if nfc:
            nfc.close()


async def scale_poll_loop(config: Config, api: APIClient, shared: dict):
    """Continuous scale reading loop — reads at 100ms, reports at 1s intervals."""
    scale: ScaleReader = shared["scale"]
    display: DisplayControl = shared["display"]
    if not scale.ok:
        logger.warning("Scale not available, skipping scale polling")
        return

    last_report = 0.0
    last_reported_grams: float | None = None
    REPORT_THRESHOLD = 2.0  # Only report if weight changed by more than this (grams)
    try:
        while True:
            result = await asyncio.to_thread(scale.read)

            if result is not None:
                grams, stable, raw_adc = result
                now = time.monotonic()

                if now - last_report >= config.scale_report_interval:
                    # Only send when weight changed meaningfully
                    weight_changed = last_reported_grams is None or abs(grams - last_reported_grams) >= REPORT_THRESHOLD

                    if weight_changed:
                        display.wake()
                        await api.scale_reading(
                            device_id=config.device_id,
                            weight_grams=grams,
                            stable=stable,
                            raw_adc=raw_adc,
                        )
                        last_reported_grams = grams
                    last_report = now

            await asyncio.sleep(config.scale_read_interval)
    finally:
        scale.close()


async def _perform_update(config: Config, api: APIClient):
    """Pull latest code from git, install deps, then exit for systemd restart."""
    # Determine repo root (install path) — daemon runs from <repo>/spoolbuddy/
    repo_root = Path(__file__).resolve().parent.parent.parent

    await api.report_update_status(config.device_id, "updating", "Fetching latest code...")

    git_path = shutil.which("git") or "/usr/bin/git"
    git_config = ["-c", f"safe.directory={repo_root}"]

    # git fetch origin main
    proc = await asyncio.create_subprocess_exec(
        git_path,
        *git_config,
        "fetch",
        "origin",
        "main",
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = f"git fetch failed: {stderr.decode()[:200]}"
        logger.error(msg)
        await api.report_update_status(config.device_id, "error", msg)
        return

    await api.report_update_status(config.device_id, "updating", "Applying update...")

    # git reset --hard origin/main
    proc = await asyncio.create_subprocess_exec(
        git_path,
        *git_config,
        "reset",
        "--hard",
        "origin/main",
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = f"git reset failed: {stderr.decode()[:200]}"
        logger.error(msg)
        await api.report_update_status(config.device_id, "error", msg)
        return

    await api.report_update_status(config.device_id, "updating", "Installing dependencies...")

    # pip install daemon deps (use the venv pip)
    venv_pip = repo_root / "spoolbuddy" / "venv" / "bin" / "pip"
    pip_packages = ["spidev", "gpiod", "smbus2", "httpx"]

    if venv_pip.exists():
        proc = await asyncio.create_subprocess_exec(
            str(venv_pip),
            "install",
            "--upgrade",
            *pip_packages,
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            *pip_packages,
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    await proc.communicate()
    if proc.returncode != 0:
        logger.warning("pip install returned non-zero (continuing anyway)")

    await api.report_update_status(config.device_id, "complete", "Update complete, restarting...")
    logger.info("Update complete, exiting for systemd restart")

    # Exit cleanly — systemd Restart=always will bring us back with the new code
    sys.exit(0)


async def heartbeat_loop(config: Config, api: APIClient, start_time: float, shared: dict):
    """Periodic heartbeat to keep device registered and pick up commands."""
    display: DisplayControl = shared["display"]
    ip = _get_ip()

    while True:
        await asyncio.sleep(config.heartbeat_interval)

        nfc = shared.get("nfc")
        scale = shared.get("scale")
        uptime = int(time.monotonic() - start_time)
        result = await api.heartbeat(
            device_id=config.device_id,
            nfc_ok=nfc.ok if nfc else False,
            scale_ok=scale.ok if scale else False,
            uptime_s=uptime,
            ip_address=ip,
            firmware_version=__version__,
            nfc_reader_type=nfc.reader_type if nfc else None,
            nfc_connection=nfc.connection if nfc else None,
            backend_url=config.backend_url,
        )

        if result:
            cmd = result.get("pending_command")
            if cmd == "update":
                logger.info("Update command received, starting update...")
                try:
                    await _perform_update(config, api)
                except Exception as e:
                    logger.error("Update failed: %s", e)
                    await api.report_update_status(config.device_id, "error", str(e)[:255])
                continue
            elif cmd == "tare":
                scale = shared.get("scale")
                if scale and scale.ok:
                    new_offset = await asyncio.to_thread(scale.tare)
                    logger.info("Tare executed: offset=%d", new_offset)
                    await api.update_tare(config.device_id, new_offset)
                    config.tare_offset = new_offset
                else:
                    logger.warning("Tare command received but scale not available")
                # Skip calibration sync — this heartbeat response predates the tare
                continue
            elif cmd == "apply_system_config":
                payload = result.get("pending_system_payload") or {}
                backend_url = str(payload.get("backend_url", "")).strip()
                api_key = str(payload.get("api_key", "")).strip()

                if not backend_url or not api_key:
                    await api.system_command_result(
                        config.device_id,
                        "apply_system_config",
                        False,
                        "Missing backend_url or api_key payload",
                    )
                    continue

                try:
                    env_path = _spoolbuddy_env_path()
                    await asyncio.to_thread(_set_env_value, env_path, "SPOOLBUDDY_BACKEND_URL", backend_url)
                    await asyncio.to_thread(_set_env_value, env_path, "SPOOLBUDDY_API_KEY", api_key)

                    await api.system_command_result(
                        config.device_id,
                        "apply_system_config",
                        True,
                        f"Updated {env_path}",
                    )

                    logger.info("Applied system config update; scheduling service restart")
                    await asyncio.to_thread(_schedule_service_restart)
                except Exception as e:
                    logger.exception("Failed to apply system config")
                    await api.system_command_result(
                        config.device_id,
                        "apply_system_config",
                        False,
                        str(e),
                    )
                continue
            elif cmd == "restart_services":
                try:
                    await api.system_command_result(
                        config.device_id,
                        "restart_services",
                        True,
                        "Restart scheduled",
                    )
                    logger.info("Restart command received; scheduling service restart")
                    await asyncio.to_thread(_schedule_service_restart)
                except Exception as e:
                    logger.exception("Failed to schedule service restart")
                    await api.system_command_result(
                        config.device_id,
                        "restart_services",
                        False,
                        str(e),
                    )
                continue
            elif cmd in ("run_nfc_diag", "run_scale_diag", "run_read_tag_diag"):
                if cmd == "run_scale_diag":
                    diagnostic = "scale"
                    script_name = "scale_diag.py"
                elif cmd == "run_read_tag_diag":
                    diagnostic = "read_tag"
                    script_name = "read_tag.py"
                else:
                    diagnostic = "nfc"
                    script_name = "pn5180_diag.py"
                script_path = Path(__file__).resolve().parent.parent / "scripts" / script_name

                if diagnostic in ("nfc", "read_tag"):
                    logger.info("Pausing NFC continuous scan for diagnostic")
                    shared["nfc_scan_paused"] = True
                    nfc_for_diag = shared.get("nfc")
                    if nfc_for_diag:
                        await asyncio.to_thread(nfc_for_diag.close)
                        shared["nfc"] = None

                logger.info("Running %s diagnostic via %s", diagnostic, script_path)
                try:
                    proc = await asyncio.to_thread(
                        subprocess.run,
                        [sys.executable, str(script_path)],
                        capture_output=True,
                        text=True,
                        timeout=45,
                    )
                    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
                    await api.diagnostic_result(
                        config.device_id,
                        diagnostic,
                        proc.returncode == 0,
                        output,
                        proc.returncode,
                    )
                except subprocess.TimeoutExpired:
                    await api.diagnostic_result(
                        config.device_id,
                        diagnostic,
                        False,
                        "Diagnostic timed out after 45 seconds",
                        -1,
                    )
                except Exception as e:
                    await api.diagnostic_result(
                        config.device_id,
                        diagnostic,
                        False,
                        f"Diagnostic execution failed: {e}",
                        -1,
                    )
                finally:
                    if diagnostic in ("nfc", "read_tag"):
                        logger.info("Reinitializing NFC continuous scan after diagnostic")
                        shared["nfc"] = NFCReader()
                        shared["nfc_scan_paused"] = False
                continue
            elif cmd == "write_tag":
                write_payload = result.get("pending_write_payload")
                if write_payload:
                    shared["pending_write"] = {
                        "spool_id": write_payload["spool_id"],
                        "ndef_data": bytes.fromhex(write_payload["ndef_data_hex"]),
                    }
                    logger.info("Write tag command received for spool %d", write_payload["spool_id"])

            tare = result.get("tare_offset", config.tare_offset)
            cal = result.get("calibration_factor", config.calibration_factor)
            if tare != config.tare_offset or cal != config.calibration_factor:
                config.tare_offset = tare
                config.calibration_factor = cal
                scale = shared.get("scale")
                if scale:
                    scale.update_calibration(tare, cal)
                logger.info("Calibration updated from backend: tare=%d, factor=%.6f", tare, cal)

            # Apply display settings from backend
            brightness = result.get("display_brightness")
            blank_timeout = result.get("display_blank_timeout")
            if brightness is not None:
                display.set_brightness(brightness)
            if blank_timeout is not None:
                display.set_blank_timeout(blank_timeout)

        display.tick()


async def main():
    config = Config.load()
    logger.info(
        "SpoolBuddy daemon v%s starting (device=%s, backend=%s)", __version__, config.device_id, config.backend_url
    )

    api = APIClient(config.backend_url, config.api_key)
    ip = _get_ip()
    start_time = time.monotonic()

    # Initialize hardware before registration so we can report capabilities
    nfc = NFCReader()
    scale = ScaleReader(
        tare_offset=config.tare_offset,
        calibration_factor=config.calibration_factor,
    )
    display = DisplayControl()

    # Register with backend (retries until success)
    reg = await api.register_device(
        device_id=config.device_id,
        hostname=config.hostname,
        ip_address=ip,
        firmware_version=__version__,
        has_nfc=True,
        has_scale=True,
        tare_offset=config.tare_offset,
        calibration_factor=config.calibration_factor,
        nfc_reader_type=nfc.reader_type,
        nfc_connection=nfc.connection,
        backend_url=config.backend_url,
        has_backlight=display.has_backlight,
    )

    # Use server-side calibration if available
    if reg:
        config.tare_offset = reg.get("tare_offset", config.tare_offset)
        config.calibration_factor = reg.get("calibration_factor", config.calibration_factor)
        scale.update_calibration(config.tare_offset, config.calibration_factor)

    logger.info("Device registered, starting poll loops")

    shared: dict = {"nfc": nfc, "scale": scale, "display": display, "nfc_scan_paused": False}
    try:
        await asyncio.gather(
            nfc_poll_loop(config, api, shared),
            scale_poll_loop(config, api, shared),
            heartbeat_loop(config, api, start_time, shared),
        )
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())
