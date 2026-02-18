import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler


# =============================================================================
# Dependency Check - runs before other imports to give helpful error messages
# =============================================================================
def _start_error_server(missing_packages: list):
    """Start a minimal HTTP server to display dependency errors in browser."""
    import os
    import signal
    from http.server import BaseHTTPRequestHandler, HTTPServer

    packages_html = "".join(f"<li><code>{p}</code></li>" for p in missing_packages)

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Bambuddy - Setup Required</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a; color: #e2e8f0;
            display: flex; justify-content: center; align-items: center;
            min-height: 100vh; margin: 0; padding: 20px; box-sizing: border-box;
        }}
        .container {{
            background: #1e293b; border-radius: 12px; padding: 40px;
            max-width: 600px; text-align: center; box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        }}
        h1 {{ color: #f87171; margin-bottom: 10px; }}
        h2 {{ color: #94a3b8; font-weight: normal; margin-top: 0; }}
        .packages {{
            background: #0f172a; border-radius: 8px; padding: 20px;
            margin: 20px 0; text-align: left;
        }}
        .packages ul {{ margin: 0; padding-left: 20px; }}
        .packages li {{ color: #fbbf24; margin: 8px 0; }}
        .command {{
            background: #0f172a; border-radius: 8px; padding: 15px 20px;
            margin: 15px 0; font-family: monospace; color: #4ade80;
            text-align: left; overflow-x: auto;
        }}
        .note {{ color: #94a3b8; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Setup Required</h1>
        <h2>Missing Python packages</h2>
        <div class="packages"><ul>{packages_html}</ul></div>
        <p>To fix, run this command on your server:</p>
        <div class="command">pip install -r requirements.txt</div>
        <p>Or if using a virtual environment:</p>
        <div class="command">./venv/bin/pip install -r requirements.txt</div>
        <p class="note">After installing, restart Bambuddy:<br>
        <code>sudo systemctl restart bambuddy</code></p>
    </div>
</body>
</html>"""

    class ErrorHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(503)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())

        def log_message(self, format, *args):
            print(f"[Error Server] {args[0]}")

    port = int(os.environ.get("PORT", 8000))
    print(f"\nStarting error server on http://0.0.0.0:{port}")
    print("Visit this URL in your browser to see the error details.\n")

    server = HTTPServer(("0.0.0.0", port), ErrorHandler)  # nosec B104

    def shutdown(signum, frame):
        print("\nShutting down error server...")
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    server.serve_forever()


def check_dependencies():
    """Check that all required packages are installed."""
    missing = []

    # Map of import name -> package name (for pip install)
    required = {
        "jwt": "PyJWT",
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "sqlalchemy": "sqlalchemy",
        "aiosqlite": "aiosqlite",
        "pydantic": "pydantic",
        "paho.mqtt": "paho-mqtt",
    }

    for module, package in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if missing:
        print("\n" + "=" * 60)
        print("ERROR: Missing required Python packages!")
        print("=" * 60)
        print(f"\nMissing packages: {', '.join(missing)}")
        print("\nTo fix, run:")
        print("  pip install -r requirements.txt")
        print("\nOr if using a virtual environment:")
        print("  ./venv/bin/pip install -r requirements.txt")
        print("=" * 60 + "\n")
        _start_error_server(missing)


check_dependencies()
# =============================================================================

from fastapi import FastAPI

# Import settings first for logging configuration
from backend.app.core.config import APP_VERSION, settings as app_settings

# Configure logging based on settings
# DEBUG=true -> DEBUG level, else use LOG_LEVEL setting
log_level_str = "DEBUG" if app_settings.debug else app_settings.log_level.upper()
log_level = getattr(logging, log_level_str, logging.INFO)
log_format = "%(asctime)s %(levelname)s [%(name)s] %(message)s"

# Create root logger
root_logger = logging.getLogger()
root_logger.setLevel(log_level)

# Console handler - always enabled
console_handler = logging.StreamHandler()
console_handler.setLevel(log_level)
console_handler.setFormatter(logging.Formatter(log_format))
root_logger.addHandler(console_handler)

# File handler - only in production or if explicitly enabled
if app_settings.log_to_file:
    log_file = app_settings.log_dir / "bambuddy.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(log_format))
    root_logger.addHandler(file_handler)
    logging.info("Logging to file: %s", log_file)

# Reduce noise from third-party libraries in production
if not app_settings.debug:
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("paho.mqtt").setLevel(logging.WARNING)

logging.info("Bambuddy starting - debug=%s, log_level=%s", app_settings.debug, log_level_str)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, or_, select

from backend.app.api.routes import (
    ams_history,
    api_keys,
    archives,
    auth,
    camera,
    cloud,
    discovery,
    external_links,
    filaments,
    firmware,
    github_backup,
    groups,
    inventory,
    kprofiles,
    library,
    local_presets,
    maintenance,
    metrics,
    notification_templates,
    notifications,
    pending_uploads,
    print_log,
    print_queue,
    printers,
    projects,
    settings as settings_routes,
    smart_plugs,
    spoolman,
    support,
    system,
    updates,
    users,
    webhook,
    websocket,
)
from backend.app.api.routes.maintenance import _get_printer_maintenance_internal, ensure_default_types
from backend.app.api.routes.support import init_debug_logging
from backend.app.core.database import async_session, init_db
from backend.app.core.websocket import ws_manager
from backend.app.models.smart_plug import SmartPlug
from backend.app.services.archive import ArchiveService
from backend.app.services.bambu_ftp import download_file_async, get_ftp_retry_settings, with_ftp_retry
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.github_backup import github_backup_service
from backend.app.services.homeassistant import homeassistant_service
from backend.app.services.mqtt_relay import mqtt_relay
from backend.app.services.mqtt_smart_plug import mqtt_smart_plug_service
from backend.app.services.notification_service import notification_service
from backend.app.services.print_scheduler import scheduler as print_scheduler
from backend.app.services.printer_manager import (
    init_printer_connections,
    printer_manager,
    printer_state_to_dict,
)
from backend.app.services.smart_plug_manager import smart_plug_manager
from backend.app.services.spoolman import close_spoolman_client, get_spoolman_client, init_spoolman_client
from backend.app.services.spoolman_tracking import (
    cleanup_tracking as _cleanup_spoolman_tracking,
    report_usage as _report_spoolman_usage,
    store_print_data as _store_spoolman_print_data,
)
from backend.app.services.tasmota import tasmota_service

# Track active prints: {(printer_id, filename): archive_id}
_active_prints: dict[tuple[int, str], int] = {}

# Track expected prints from reprint/scheduled (skip auto-archiving for these)
# {(printer_id, filename): archive_id}
_expected_prints: dict[tuple[int, str], int] = {}

# Track starting energy for prints: {archive_id: starting_kwh}
_print_energy_start: dict[int, float] = {}

# Track reprints to add costs on completion: {archive_id}
_reprint_archives: set[int] = set()

# Track AMS mapping for prints: {archive_id: [global_tray_id_per_slot]}
# Used by usage tracker to map 3MF slots to physical AMS trays
_print_ams_mappings: dict[int, list[int]] = {}

# Track progress milestones for notifications: {printer_id: last_milestone_notified}
# Milestones are 25, 50, 75. Value of 0 means no milestone notified yet for current print.
_last_progress_milestone: dict[int, int] = {}

# Track HMS errors that have been notified: {printer_id: set of error codes}
# This prevents sending duplicate notifications for the same error
_notified_hms_errors: dict[int, set[str]] = {}

# Track timelapse file baselines at print start: {printer_id: set of video filenames}
# Used for snapshot-diff detection at print completion
_timelapse_baselines: dict[int, set[str]] = {}

# Track active bed cooldown monitoring tasks: {printer_id: asyncio.Task}
_bed_cooldown_tasks: dict[int, asyncio.Task] = {}


async def _get_plug_energy(plug, db) -> dict | None:
    """Get energy from plug regardless of type (Tasmota, Home Assistant, or MQTT).

    For HA plugs, configures the service with current settings from DB.
    For MQTT plugs, returns data from the subscription service.
    """
    if plug.plug_type == "homeassistant":
        from backend.app.api.routes.settings import get_homeassistant_settings

        ha_settings = await get_homeassistant_settings(db)
        homeassistant_service.configure(ha_settings["ha_url"], ha_settings["ha_token"])
        return await homeassistant_service.get_energy(plug)
    elif plug.plug_type == "mqtt":
        # MQTT plugs report "today" energy, not lifetime total
        # For per-print tracking, we use "today" as the counter (resets at midnight)
        mqtt_data = mqtt_relay.smart_plug_service.get_plug_data(plug.id)
        if mqtt_data:
            return {
                "power": mqtt_data.power,
                "today": mqtt_data.energy,
                "total": mqtt_data.energy,  # Use today as total for per-print calculations
            }
        return None
    else:
        return await tasmota_service.get_energy(plug)


def register_expected_print(printer_id: int, filename: str, archive_id: int, ams_mapping: list[int] | None = None):
    """Register an expected print from reprint/scheduled so we don't create duplicate archives."""
    # Store with multiple filename variations to catch different naming patterns
    _expected_prints[(printer_id, filename)] = archive_id
    # Also store without .3mf extension if present
    if filename.endswith(".3mf"):
        base = filename[:-4]
        _expected_prints[(printer_id, base)] = archive_id
        _expected_prints[(printer_id, f"{base}.gcode")] = archive_id
    # Store AMS mapping for usage tracking at print completion
    if ams_mapping is not None:
        _print_ams_mappings[archive_id] = ams_mapping
    logging.getLogger(__name__).info(
        f"Registered expected print: printer={printer_id}, file={filename}, archive={archive_id}, ams_mapping={ams_mapping}"
    )


_last_status_broadcast: dict[int, str] = {}
_nozzle_count_updated: set[int] = set()  # Track printers where we've updated nozzle_count


async def on_printer_status_change(printer_id: int, state: PrinterState):
    """Handle printer status changes - broadcast via WebSocket."""
    # Only broadcast if something meaningful changed (reduce WebSocket spam)
    # Include rounded temperatures to detect meaningful temp changes (within 1 degree)
    temps = state.temperatures or {}
    nozzle_temp = round(temps.get("nozzle", 0))
    bed_temp = round(temps.get("bed", 0))
    nozzle_2_temp = round(temps.get("nozzle_2", 0)) if "nozzle_2" in temps else ""
    chamber_temp = round(temps.get("chamber", 0)) if "chamber" in temps else ""

    # Auto-detect dual-nozzle printers from MQTT temperature data
    if "nozzle_2" in temps and printer_id not in _nozzle_count_updated:
        _nozzle_count_updated.add(printer_id)
        # Update nozzle_count in database
        async with async_session() as db:
            from backend.app.models.printer import Printer

            result = await db.execute(select(Printer).where(Printer.id == printer_id))
            printer = result.scalar_one_or_none()
            if printer and printer.nozzle_count != 2:
                printer.nozzle_count = 2
                await db.commit()
                logging.getLogger(__name__).info(
                    f"Auto-detected dual-nozzle printer {printer_id}, updated nozzle_count=2"
                )

    # Include target temps for heating phase detection
    bed_target = round(temps.get("bed_target", 0))
    nozzle_target = round(temps.get("nozzle_target", 0))

    # Include tray_now and vt_tray hash so external spool changes trigger broadcasts
    vt_tray_key = hash(str(state.raw_data.get("vt_tray", []))) if state.raw_data else 0
    status_key = (
        f"{state.connected}:{state.state}:{state.progress}:{state.layer_num}:"
        f"{nozzle_temp}:{bed_temp}:{nozzle_2_temp}:{chamber_temp}:"
        f"{state.stg_cur}:{bed_target}:{nozzle_target}:"
        f"{state.cooling_fan_speed}:{state.big_fan1_speed}:{state.big_fan2_speed}:"
        f"{state.chamber_light}:{state.active_extruder}:{state.tray_now}:{vt_tray_key}"
    )

    # MQTT relay - publish status (before dedup check - always publish to MQTT)
    try:
        printer_info = printer_manager.get_printer(printer_id)
        if printer_info:
            await mqtt_relay.on_printer_status(printer_id, state, printer_info.name, printer_info.serial_number)
    except Exception:
        pass  # Don't fail status callback if MQTT fails

    if _last_status_broadcast.get(printer_id) == status_key:
        return  # No change, skip WebSocket broadcast

    _last_status_broadcast[printer_id] = status_key

    # Check for progress milestone notifications (25%, 50%, 75%)
    progress = state.progress or 0
    is_printing = state.state in ("RUNNING", "PRINTING")

    if is_printing and progress > 0:
        # Determine which milestone we've reached
        current_milestone = 0
        if progress >= 75:
            current_milestone = 75
        elif progress >= 50:
            current_milestone = 50
        elif progress >= 25:
            current_milestone = 25

        last_milestone = _last_progress_milestone.get(printer_id, 0)

        # If we've crossed a new milestone, send notification
        if current_milestone > last_milestone:
            _last_progress_milestone[printer_id] = current_milestone
            try:
                async with async_session() as db:
                    from backend.app.models.printer import Printer

                    result = await db.execute(select(Printer).where(Printer.id == printer_id))
                    printer = result.scalar_one_or_none()
                    printer_name = printer.name if printer else f"Printer {printer_id}"
                    filename = state.subtask_name or state.gcode_file or "Unknown"
                    # remaining_time is in minutes, convert to seconds for notification
                    remaining_time_seconds = state.remaining_time * 60 if state.remaining_time else None

                    # Capture camera snapshot for notification image attachment
                    image_data = await _capture_snapshot_for_notification(
                        printer_id, printer, logging.getLogger(__name__)
                    )

                    await notification_service.on_print_progress(
                        printer_id,
                        printer_name,
                        filename,
                        current_milestone,
                        db,
                        remaining_time_seconds,
                        image_data=image_data,
                    )
            except Exception as e:
                logging.getLogger(__name__).warning(f"Progress milestone notification failed: {e}")
    elif progress < 5:
        # Reset milestone tracking when print restarts or new print begins
        _last_progress_milestone[printer_id] = 0

    # Check for new HMS errors and send notifications
    current_hms_errors = getattr(state, "hms_errors", []) or []
    if current_hms_errors:
        # Build set of current error codes (using attr for uniqueness)
        current_error_codes = {f"{e.attr:08x}" for e in current_hms_errors}
        previously_notified = _notified_hms_errors.get(printer_id, set())

        # Find new errors that haven't been notified yet
        new_error_codes = current_error_codes - previously_notified

        # Update tracking immediately to prevent duplicate notifications from concurrent callbacks
        _notified_hms_errors[printer_id] = current_error_codes

        if new_error_codes:
            # Get the actual new errors for the notification
            # Filter to severity >= 2 (skip informational/status messages like H2D sends)
            new_errors = [e for e in current_hms_errors if f"{e.attr:08x}" in new_error_codes and e.severity >= 2]

            try:
                async with async_session() as db:
                    from backend.app.models.printer import Printer

                    result = await db.execute(select(Printer).where(Printer.id == printer_id))
                    printer = result.scalar_one_or_none()
                    printer_name = printer.name if printer else f"Printer {printer_id}"

                    # Format error details for notification
                    # Module 0x07 = AMS/Filament, 0x05 = Nozzle, 0x0C = Motion Controller, etc.
                    module_names = {
                        0x03: "Print/Task",
                        0x05: "Nozzle/Extruder",
                        0x07: "AMS/Filament",
                        0x0C: "Motion Controller",
                        0x12: "Chamber",
                    }

                    from backend.app.services.hms_errors import get_error_description

                    # Capture camera snapshot once for all error notifications
                    error_image_data = await _capture_snapshot_for_notification(
                        printer_id, printer, logging.getLogger(__name__)
                    )

                    for error in new_errors:
                        module_name = module_names.get(error.module, f"Module 0x{error.module:02X}")
                        # Build short code like "0700_8010"
                        # Mask to 16 bits to handle printers that send larger values
                        error_code_int = int(error.code.replace("0x", ""), 16) if error.code else 0
                        error_code_masked = error_code_int & 0xFFFF
                        short_code = f"{(error.attr >> 16) & 0xFFFF:04X}_{error_code_masked:04X}"

                        error_type = f"{module_name} Error"
                        # Look up human-readable description
                        description = get_error_description(short_code)
                        error_detail = description if description else f"Error code: {short_code}"

                        await notification_service.on_printer_error(
                            printer_id, printer_name, error_type, db, error_detail, image_data=error_image_data
                        )

                    logging.getLogger(__name__).info(
                        f"[HMS] Sent notification for {len(new_errors)} new error(s) on printer {printer_id}"
                    )

                    # Also publish to MQTT relay
                    printer_info = printer_manager.get_printer(printer_id)
                    if printer_info:
                        errors_data = [
                            {
                                "code": e.code,
                                "attr": e.attr,
                                "module": e.module,
                                "severity": e.severity,
                            }
                            for e in new_errors
                        ]
                        await mqtt_relay.on_printer_error(
                            printer_id, printer_info.name, printer_info.serial_number, errors_data
                        )

            except Exception as e:
                logging.getLogger(__name__).warning(f"HMS error notification failed: {e}")

    else:
        # No HMS errors - clear tracking so future errors get notified
        if printer_id in _notified_hms_errors:
            _notified_hms_errors.pop(printer_id, None)

    await ws_manager.send_printer_status(
        printer_id,
        printer_state_to_dict(state, printer_id, printer_manager.get_model(printer_id)),
    )


def _is_bambu_uuid(tray_uuid: str) -> bool:
    """Check if a tray UUID looks like a valid Bambu Lab RFID UUID (non-empty, non-zero)."""
    return bool(tray_uuid) and tray_uuid not in ("", "0" * len(tray_uuid))


async def on_ams_change(printer_id: int, ams_data: list):
    """Handle AMS data changes - sync to Spoolman if enabled and auto mode."""
    logger = logging.getLogger(__name__)

    # MQTT relay - publish AMS change
    try:
        printer_info = printer_manager.get_printer(printer_id)
        if printer_info:
            await mqtt_relay.on_ams_change(printer_id, printer_info.name, printer_info.serial_number, ams_data)
    except Exception:
        pass  # Don't fail AMS callback if MQTT fails

    # Broadcast AMS change via WebSocket (bypasses status_key deduplication)
    # This ensures frontend gets immediate updates when AMS slots are configured
    try:
        state = printer_manager.get_status(printer_id)
        if state:
            logger.info("[Printer %s] Broadcasting AMS change via WebSocket", printer_id)
            await ws_manager.send_printer_status(
                printer_id,
                printer_state_to_dict(state, printer_id, printer_manager.get_model(printer_id)),
            )
    except Exception as e:
        logger.warning("Failed to broadcast AMS change for printer %s: %s", printer_id, e)

    from backend.app.utils.color_utils import colors_similar as _colors_similar

    # Auto-unlink spool assignments with stale fingerprints
    try:
        async with async_session() as db:
            from sqlalchemy.orm import selectinload

            from backend.app.api.routes.inventory import _find_tray_in_ams_data
            from backend.app.models.spool_assignment import SpoolAssignment as SA

            result = await db.execute(select(SA).where(SA.printer_id == printer_id).options(selectinload(SA.spool)))
            stale = []
            for assignment in result.scalars().all():
                current_tray = _find_tray_in_ams_data(ams_data, assignment.ams_id, assignment.tray_id)
                if not current_tray:
                    logger.info(
                        "Auto-unlink: spool %d AMS%d-T%d — tray not found in AMS data (slot empty?)",
                        assignment.spool_id,
                        assignment.ams_id,
                        assignment.tray_id,
                    )
                    stale.append(assignment)  # Slot empty
                elif _is_bambu_uuid(current_tray.get("tray_uuid", "")):
                    # A Bambu Lab spool is in this slot — check if it's the same spool
                    # that's currently assigned. If yes, keep the assignment (avoids
                    # unnecessary unlink/re-assign/ams_filament_setting cycle that clears
                    # the printer's filament preset on every startup).
                    tray_uuid = current_tray.get("tray_uuid", "")
                    tag_uid = current_tray.get("tag_uid", "")
                    spool = assignment.spool
                    spool_matches = False
                    if spool:
                        if (spool.tray_uuid and spool.tray_uuid.upper() == tray_uuid.upper()) or (
                            spool.tag_uid
                            and tag_uid
                            and tag_uid != "0000000000000000"
                            and spool.tag_uid.upper() == tag_uid.upper()
                        ):
                            spool_matches = True
                    if spool_matches:
                        # Same BL spool still in slot — keep assignment, update fingerprint if needed
                        cur_color = current_tray.get("tray_color", "")
                        cur_type = current_tray.get("tray_type", "")
                        fp_color = assignment.fingerprint_color or ""
                        fp_type = assignment.fingerprint_type or ""
                        if cur_color.upper() != fp_color.upper() or cur_type.upper() != fp_type.upper():
                            assignment.fingerprint_color = cur_color
                            assignment.fingerprint_type = cur_type
                            logger.debug(
                                "Auto-unlink: spool %d AMS%d-T%d — same BL spool, updated fingerprint",
                                assignment.spool_id,
                                assignment.ams_id,
                                assignment.tray_id,
                            )
                        continue
                    # Different BL spool or unrecognized — unlink so auto-assign can match
                    logger.info(
                        "Auto-unlink: spool %d AMS%d-T%d — different Bambu Lab spool detected (uuid=%s)",
                        assignment.spool_id,
                        assignment.ams_id,
                        assignment.tray_id,
                        tray_uuid,
                    )
                    stale.append(assignment)
                else:
                    cur_color = current_tray.get("tray_color", "")
                    cur_type = current_tray.get("tray_type", "")
                    fp_color = assignment.fingerprint_color or ""
                    fp_type = assignment.fingerprint_type or ""
                    if not _colors_similar(cur_color, fp_color) or cur_type.upper() != fp_type.upper():
                        # Fingerprint mismatch — but check if tray now matches the
                        # assigned spool (e.g. auto-configure changed the tray).
                        spool = assignment.spool
                        if spool:
                            spool_color = (spool.rgba or "FFFFFFFF").upper()
                            spool_type = (spool.material or "").upper()
                            if _colors_similar(cur_color, spool_color) and cur_type.upper() == spool_type:
                                # Tray was reconfigured to match the spool — update fingerprint
                                logger.info(
                                    "Auto-unlink: spool %d AMS%d-T%d — fingerprint mismatch but tray matches spool, updating fp",
                                    assignment.spool_id,
                                    assignment.ams_id,
                                    assignment.tray_id,
                                )
                                assignment.fingerprint_color = cur_color
                                assignment.fingerprint_type = cur_type
                                continue
                        logger.info(
                            "Auto-unlink: spool %d AMS%d-T%d — fingerprint mismatch (cur=%s/%s fp=%s/%s spool=%s/%s)",
                            assignment.spool_id,
                            assignment.ams_id,
                            assignment.tray_id,
                            cur_color,
                            cur_type,
                            fp_color,
                            fp_type,
                            spool.rgba if spool else "?",
                            spool.material if spool else "?",
                        )
                        stale.append(assignment)  # Spool changed
            for a in stale:
                await db.delete(a)
            if stale:
                logger.info("Auto-unlinked %d stale spool assignments for printer %d", len(stale), printer_id)
            # Commit any changes (stale deletions and/or fingerprint updates)
            await db.commit()
    except Exception as e:
        logger.warning("Spool assignment cleanup failed: %s", e)

    # Auto-manage inventory spools from AMS tray data (skip if Spoolman manages AMS)
    try:
        async with async_session() as db:
            from backend.app.api.routes.settings import get_setting
            from backend.app.models.spool_assignment import SpoolAssignment as SA
            from backend.app.services.spool_tag_matcher import (
                auto_assign_spool,
                create_spool_from_tray,
                get_spool_by_tag,
                is_bambu_tag,
                is_valid_tag,
            )

            _spoolman_on = await get_setting(db, "spoolman_enabled")
            if not _spoolman_on or _spoolman_on.lower() != "true":
                for ams_unit in ams_data:
                    if not isinstance(ams_unit, dict):
                        continue
                    ams_id = int(ams_unit.get("id", 0))
                    for tray in ams_unit.get("tray", []):
                        if not isinstance(tray, dict):
                            continue
                        tray_id = int(tray.get("id", 0))
                        tag_uid = tray.get("tag_uid", "")
                        tray_uuid = tray.get("tray_uuid", "")
                        tray_info_idx = tray.get("tray_info_idx", "")
                        if not tray.get("tray_type"):
                            continue  # Empty slot
                        # Check if assignment already exists for this slot
                        existing = await db.execute(
                            select(SA)
                            .options(selectinload(SA.spool))
                            .where(SA.printer_id == printer_id, SA.ams_id == ams_id, SA.tray_id == tray_id)
                        )
                        existing_assignment = existing.scalar_one_or_none()
                        if existing_assignment:
                            # Sync spool weight_used from AMS remain — only INCREASE, never decrease.
                            # The AMS remain% is low-resolution (integer %, i.e. 10g steps for 1kg spool)
                            # and must not overwrite precise values from the usage tracker (3MF/G-code).
                            remain_raw = tray.get("remain")
                            if remain_raw is not None and existing_assignment.spool:
                                try:
                                    remain_val = int(remain_raw)
                                except (TypeError, ValueError):
                                    remain_val = -1
                                if 1 <= remain_val <= 100:
                                    lw = existing_assignment.spool.label_weight or 1000
                                    new_used = round(lw * (100 - remain_val) / 100.0, 1)
                                    current_used = existing_assignment.spool.weight_used or 0
                                    if new_used > current_used + 1:
                                        logger.info(
                                            "Weight sync: spool %d weight_used %s -> %s (remain=%d)",
                                            existing_assignment.spool_id,
                                            current_used,
                                            new_used,
                                            remain_val,
                                        )
                                        existing_assignment.spool.weight_used = new_used
                                        await db.commit()
                            continue

                        if is_bambu_tag(tag_uid, tray_uuid, tray_info_idx):
                            # BL spool with RFID tag: auto-match or auto-create
                            spool = await get_spool_by_tag(db, tag_uid, tray_uuid)
                            if not spool:
                                spool = await create_spool_from_tray(db, tray)
                            await auto_assign_spool(
                                printer_id,
                                ams_id,
                                tray_id,
                                spool,
                                printer_manager,
                                db,
                                tray_info_idx=tray_info_idx,
                            )
                            await db.commit()
                            await ws_manager.broadcast(
                                {
                                    "type": "spool_auto_assigned",
                                    "printer_id": printer_id,
                                    "ams_id": ams_id,
                                    "tray_id": tray_id,
                                    "spool_id": spool.id,
                                }
                            )
                            logger.info(
                                "RFID auto-assigned spool %d to printer %d AMS%d-T%d",
                                spool.id,
                                printer_id,
                                ams_id,
                                tray_id,
                            )
                        elif is_valid_tag(tag_uid, tray_uuid):
                            # Non-BL spool with some tag — let user choose
                            await ws_manager.broadcast(
                                {
                                    "type": "unknown_tag",
                                    "printer_id": printer_id,
                                    "ams_id": ams_id,
                                    "tray_id": tray_id,
                                    "tag_uid": tag_uid,
                                    "tray_uuid": tray_uuid,
                                }
                            )
                        else:
                            # No tag at all — let user choose from inventory
                            await ws_manager.broadcast(
                                {
                                    "type": "unknown_tag",
                                    "printer_id": printer_id,
                                    "ams_id": ams_id,
                                    "tray_id": tray_id,
                                    "tag_uid": "",
                                    "tray_uuid": "",
                                }
                            )
    except Exception as e:
        logger.warning("RFID spool auto-assign failed: %s", e)

    try:
        async with async_session() as db:
            from backend.app.api.routes.settings import get_setting
            from backend.app.models.printer import Printer

            # Check if Spoolman is enabled
            spoolman_enabled = await get_setting(db, "spoolman_enabled")
            if not spoolman_enabled or spoolman_enabled.lower() != "true":
                return

            # Check sync mode
            sync_mode = await get_setting(db, "spoolman_sync_mode")
            if sync_mode and sync_mode != "auto":
                return  # Only sync on auto mode

            # Check if weight sync is disabled
            disable_weight_sync_str = await get_setting(db, "spoolman_disable_weight_sync")
            disable_weight_sync = disable_weight_sync_str and disable_weight_sync_str.lower() == "true"

            # Get Spoolman URL
            spoolman_url = await get_setting(db, "spoolman_url")
            if not spoolman_url:
                return

            # Get or create Spoolman client
            client = await get_spoolman_client()
            if not client:
                client = await init_spoolman_client(spoolman_url)

            # Check if Spoolman is reachable
            if not await client.health_check():
                logger.warning("Spoolman not reachable at %s", spoolman_url)
                return

            # Get printer name for location
            result = await db.execute(select(Printer).where(Printer.id == printer_id))
            printer = result.scalar_one_or_none()
            printer_name = printer.name if printer else f"Printer {printer_id}"

            # OPTIMIZATION: Fetch all spools once before processing trays
            # This eliminates redundant API calls (one per tray) when syncing multiple trays
            logger.debug("[Printer %s] Fetching spools cache for AMS sync...", printer_id)
            try:
                cached_spools = await client.get_spools()
                logger.debug("[Printer %s] Cached %d spools for batch sync", printer_id, len(cached_spools))
            except Exception as e:
                logger.error(
                    "[Printer %s] Failed to fetch spools cache after retries, aborting AMS sync: %s",
                    printer_id,
                    e,
                )
                return

            # Load inventory weights as fallback (when AMS MQTT data lacks remain values)
            from sqlalchemy.orm import selectinload

            from backend.app.models.spool_assignment import SpoolAssignment

            inventory_weights: dict[tuple[int, int], float] = {}
            try:
                assign_result = await db.execute(
                    select(SpoolAssignment)
                    .options(selectinload(SpoolAssignment.spool))
                    .where(SpoolAssignment.printer_id == printer_id)
                )
                for assignment in assign_result.scalars().all():
                    spool = assignment.spool
                    if spool and spool.label_weight > 0:
                        remaining = max(0.0, spool.label_weight - (spool.weight_used or 0))
                        inventory_weights[(assignment.ams_id, assignment.tray_id)] = remaining
            except Exception as e:
                logger.debug("Could not load inventory weights for printer %s: %s", printer_id, e)

            # Sync each AMS tray
            synced = 0
            for ams_unit in ams_data:
                ams_id = int(ams_unit.get("id", 0))
                trays = ams_unit.get("tray", [])

                for tray_data in trays:
                    tray = client.parse_ams_tray(ams_id, tray_data)
                    if not tray:
                        continue  # Empty tray

                    try:
                        inv_remaining = inventory_weights.get((ams_id, tray.tray_id))
                        result = await client.sync_ams_tray(
                            tray,
                            printer_name,
                            disable_weight_sync=disable_weight_sync,
                            cached_spools=cached_spools,
                            inventory_remaining=inv_remaining,
                        )
                        if result:
                            synced += 1
                            # If a new spool was created, add it to the cache
                            # so subsequent trays can find it if they reference the same tag
                            if result.get("id"):
                                # Check if this spool already exists in cache
                                spool_exists = any(s.get("id") == result["id"] for s in cached_spools)
                                if not spool_exists:
                                    cached_spools.append(result)
                                    logger.debug(
                                        "[Printer %s] Added newly created spool %s to cache",
                                        printer_id,
                                        result["id"],
                                    )
                    except Exception as e:
                        logger.error("Error syncing AMS %s tray %s: %s", ams_id, tray.tray_id, e)

            if synced > 0:
                logger.info("Auto-synced %s AMS trays to Spoolman for printer %s", synced, printer_id)

    except Exception as e:
        logging.getLogger(__name__).warning(f"Spoolman AMS sync failed: {e}")


async def _capture_snapshot_for_notification(printer_id: int, printer, logger) -> bytes | None:
    """Capture a camera snapshot for notification image attachment.

    Returns JPEG bytes (max 2.5MB) or None if capture fails or is unavailable.
    Uses: external camera > buffered frame > fresh capture.
    """
    if not printer:
        return None

    try:
        from backend.app.api.routes.settings import get_setting

        async with async_session() as db:
            capture_enabled = await get_setting(db, "capture_finish_photo")

        if capture_enabled is not None and capture_enabled.lower() != "true":
            return None

        # Try external camera first
        if printer.external_camera_enabled and printer.external_camera_url:
            logger.info("[SNAPSHOT] Capturing from external camera for printer %s", printer_id)
            from backend.app.services.external_camera import capture_frame

            frame_data = await capture_frame(printer.external_camera_url, printer.external_camera_type or "mjpeg")
            if frame_data and len(frame_data) <= 2_500_000:
                logger.info("[SNAPSHOT] External camera frame: %s bytes", len(frame_data))
                return frame_data

        # Try buffered frame from active stream
        from backend.app.api.routes.camera import _active_chamber_streams, _active_streams, get_buffered_frame

        active_for_printer = [k for k in _active_streams if k.startswith(f"{printer_id}-")]
        active_chamber = [k for k in _active_chamber_streams if k.startswith(f"{printer_id}-")]
        buffered_frame = get_buffered_frame(printer_id)

        if (active_for_printer or active_chamber) and buffered_frame:
            logger.info("[SNAPSHOT] Using buffered frame for printer %s: %s bytes", printer_id, len(buffered_frame))
            if len(buffered_frame) <= 2_500_000:
                return buffered_frame

        # Fresh capture from printer camera
        logger.info("[SNAPSHOT] Capturing fresh frame for printer %s", printer_id)
        from backend.app.services.camera import capture_camera_frame_bytes

        frame_data = await capture_camera_frame_bytes(
            printer.ip_address, printer.access_code, printer.model, timeout=15
        )
        if frame_data and len(frame_data) <= 2_500_000:
            logger.info("[SNAPSHOT] Fresh camera frame: %s bytes", len(frame_data))
            return frame_data

    except Exception as e:
        logger.warning("[SNAPSHOT] Failed to capture snapshot for printer %s: %s", printer_id, e)

    return None


async def _send_print_start_notification(
    printer_id: int,
    data: dict,
    archive_data: dict | None = None,
    logger=None,
):
    """Helper to send print start notification with optional archive data."""
    if logger is None:
        logger = logging.getLogger(__name__)

    try:
        async with async_session() as db:
            from backend.app.models.printer import Printer

            result = await db.execute(select(Printer).where(Printer.id == printer_id))
            printer = result.scalar_one_or_none()
            printer_name = printer.name if printer else f"Printer {printer_id}"

            # Capture camera snapshot for notification image attachment
            image_data = await _capture_snapshot_for_notification(printer_id, printer, logger)
            if image_data:
                if archive_data is None:
                    archive_data = {}
                archive_data["image_data"] = image_data

            await notification_service.on_print_start(printer_id, printer_name, data, db, archive_data=archive_data)
    except Exception as e:
        logger.warning("Notification on_print_start failed: %s", e)


def _load_objects_from_archive(archive, printer_id: int, logger) -> None:
    """Extract printable objects from an archive's 3MF file and store in printer state."""
    try:
        from backend.app.services.archive import extract_printable_objects_from_3mf

        file_path = app_settings.base_dir / archive.file_path
        if file_path.exists() and str(file_path).endswith(".3mf"):
            with open(file_path, "rb") as f:
                threemf_data = f.read()
            # Extract with positions for UI overlay
            printable_objects, bbox_all = extract_printable_objects_from_3mf(threemf_data, include_positions=True)
            if printable_objects:
                client = printer_manager.get_client(printer_id)
                if client:
                    client.state.printable_objects = printable_objects
                    client.state.printable_objects_bbox_all = bbox_all
                    client.state.skipped_objects = []
                    logger.info("Loaded %s printable objects for printer %s", len(printable_objects), printer_id)
    except Exception as e:
        logger.debug("Failed to extract printable objects from archive: %s", e)


async def on_print_start(printer_id: int, data: dict):
    """Handle print start - archive the 3MF file immediately."""
    logger = logging.getLogger(__name__)

    logger.info("[CALLBACK] on_print_start called for printer %s, data keys: %s", printer_id, list(data.keys()))

    # Cancel any active bed cooldown task for this printer
    existing_task = _bed_cooldown_tasks.pop(printer_id, None)
    if existing_task and not existing_task.done():
        existing_task.cancel()
        logger.info("[BED-COOL] Cancelled bed cooldown monitor for printer %s (new print started)", printer_id)

    # Clear cached cover images so the new print's thumbnail is fetched fresh
    from backend.app.api.routes.printers import clear_cover_cache

    clear_cover_cache(printer_id)

    await ws_manager.send_print_start(printer_id, data)

    # MQTT relay - publish print start
    try:
        printer_info = printer_manager.get_printer(printer_id)
        if printer_info:
            await mqtt_relay.on_print_start(
                printer_id,
                printer_info.name,
                printer_info.serial_number,
                data.get("filename", ""),
                data.get("subtask_name", ""),
            )
    except Exception:
        pass  # Don't fail print start callback if MQTT fails

    # Capture AMS tray remain% for filament consumption tracking (skip if Spoolman handles usage)
    try:
        async with async_session() as db:
            from backend.app.api.routes.settings import get_setting

            _spoolman_on = await get_setting(db, "spoolman_enabled")
        if not _spoolman_on or _spoolman_on.lower() != "true":
            from backend.app.services.usage_tracker import on_print_start as usage_on_print_start

            await usage_on_print_start(printer_id, data, printer_manager)
    except Exception as e:
        logger.warning("Usage tracker on_print_start failed: %s", e)

    # Track if notification was sent (to avoid sending twice)
    notification_sent = False

    # Smart plug automation: turn on plug when print starts
    try:
        async with async_session() as db:
            await smart_plug_manager.on_print_start(printer_id, db)
    except Exception as e:
        logger.warning("Smart plug on_print_start failed: %s", e)

    async with async_session() as db:
        from backend.app.models.printer import Printer
        from backend.app.services.bambu_ftp import list_files_async

        result = await db.execute(select(Printer).where(Printer.id == printer_id))
        printer = result.scalar_one_or_none()

        # Plate detection check - pause if objects detected on build plate
        logger.info(
            f"[PLATE CHECK] printer_id={printer_id}, plate_detection_enabled={printer.plate_detection_enabled if printer else 'NO PRINTER'}"
        )
        if printer and printer.plate_detection_enabled:
            logger.info("[PLATE CHECK] ENTERING plate detection code for printer %s", printer_id)
            try:
                from backend.app.services.plate_detection import check_plate_empty

                # Build ROI tuple from printer settings if available
                roi = None
                if all(
                    [
                        printer.plate_detection_roi_x is not None,
                        printer.plate_detection_roi_y is not None,
                        printer.plate_detection_roi_w is not None,
                        printer.plate_detection_roi_h is not None,
                    ]
                ):
                    roi = (
                        printer.plate_detection_roi_x,
                        printer.plate_detection_roi_y,
                        printer.plate_detection_roi_w,
                        printer.plate_detection_roi_h,
                    )

                # Auto-turn on chamber light if it's off for better detection
                light_was_off = False
                client = printer_manager.get_client(printer_id)
                if client and client.state:
                    light_was_off = not client.state.chamber_light
                    if light_was_off:
                        logger.info("[PLATE CHECK] Turning on chamber light for printer %s", printer_id)
                        client.set_chamber_light(True)
                        # Wait for light to physically turn on and camera to adjust exposure
                        await asyncio.sleep(2.5)

                logger.info("[PLATE CHECK] Running plate detection for printer %s", printer_id)
                plate_result = await check_plate_empty(
                    printer_id=printer_id,
                    ip_address=printer.ip_address,
                    access_code=printer.access_code,
                    model=printer.model,
                    include_debug_image=False,
                    external_camera_url=printer.external_camera_url,
                    external_camera_type=printer.external_camera_type,
                    use_external=printer.external_camera_enabled,
                    roi=roi,
                )

                # Restore chamber light to original state
                if light_was_off and client:
                    logger.info("[PLATE CHECK] Restoring chamber light to off for printer %s", printer_id)
                    client.set_chamber_light(False)

                if not plate_result.needs_calibration and not plate_result.is_empty:
                    # Objects detected - pause the print!
                    logger.warning(
                        f"[PLATE CHECK] Objects detected on plate for printer {printer_id}! "
                        f"Confidence: {plate_result.confidence:.0%}, Diff: {plate_result.difference_percent:.1f}%"
                    )
                    client = printer_manager.get_client(printer_id)
                    if client:
                        client.pause_print()
                        logger.info("[PLATE CHECK] Print paused for printer %s", printer_id)

                    # Send notification about plate not empty
                    await ws_manager.broadcast(
                        {
                            "type": "plate_not_empty",
                            "printer_id": printer_id,
                            "printer_name": printer.name,
                            "message": f"Objects detected on build plate! Print paused. (Diff: {plate_result.difference_percent:.1f}%)",
                        }
                    )

                    # Also send push notification
                    try:
                        await notification_service.on_plate_not_empty(
                            printer_id=printer_id,
                            printer_name=printer.name,
                            db=db,
                            difference_percent=plate_result.difference_percent,
                        )
                    except Exception as notif_err:
                        logger.warning("[PLATE CHECK] Failed to send notification: %s", notif_err)
                else:
                    logger.info("[PLATE CHECK] Plate is empty for printer %s, proceeding with print", printer_id)
            except Exception as plate_err:
                # Don't block print on plate detection errors
                logger.warning("[PLATE CHECK] Plate detection failed for printer %s: %s", printer_id, plate_err)

        if not printer or not printer.auto_archive:
            # Send notification without archive data (auto-archive disabled)
            logger.info(
                f"[CALLBACK] Skipping archive - printer: {printer is not None}, auto_archive: {printer.auto_archive if printer else 'N/A'}"
            )
            if not notification_sent:
                await _send_print_start_notification(printer_id, data, logger=logger)
            return

        # Get the filename and subtask_name
        filename = data.get("filename", "")
        subtask_name = data.get("subtask_name", "")

        logger.info("[CALLBACK] Print start detected - filename: %s, subtask: %s", filename, subtask_name)

        # Skip calibration prints — internal printer files should not be archived
        # Bambu calibration gcode lives under /usr/ (e.g. /usr/etc/print/auto_cali_for_user.gcode)
        if filename and filename.startswith("/usr/"):
            logger.info("[CALLBACK] Skipping archive — internal printer file detected: %s", filename)
            if not notification_sent:
                await _send_print_start_notification(printer_id, data, logger=logger)
            return

        if not filename and not subtask_name:
            # Send notification without archive data (no filename)
            logger.info("[CALLBACK] Skipping archive - no filename or subtask_name")
            if not notification_sent:
                await _send_print_start_notification(printer_id, data, logger=logger)
            return

        # Check if this is an expected print from reprint/scheduled
        # Build list of possible keys to check
        expected_keys = []
        if subtask_name:
            expected_keys.append((printer_id, subtask_name))
            expected_keys.append((printer_id, f"{subtask_name}.3mf"))
            expected_keys.append((printer_id, f"{subtask_name}.gcode.3mf"))
        if filename:
            fname = filename.split("/")[-1] if "/" in filename else filename
            expected_keys.append((printer_id, fname))
            # Strip extensions to match
            base = fname.replace(".gcode", "").replace(".3mf", "")
            expected_keys.append((printer_id, base))
            expected_keys.append((printer_id, f"{base}.3mf"))

        expected_archive_id = None
        for key in expected_keys:
            expected_archive_id = _expected_prints.pop(key, None)
            if expected_archive_id:
                # Clean up other possible keys for this print
                for other_key in expected_keys:
                    _expected_prints.pop(other_key, None)
                break

        if expected_archive_id:
            # This is a reprint/scheduled print - use existing archive, don't create new one
            logger.info("Using expected archive %s for print (skipping duplicate)", expected_archive_id)
            from backend.app.models.archive import PrintArchive

            result = await db.execute(select(PrintArchive).where(PrintArchive.id == expected_archive_id))
            archive = result.scalar_one_or_none()

            if archive:
                # Update archive status to printing
                archive.status = "printing"
                archive.started_at = datetime.now()
                await db.commit()

                # Track as active print
                _active_prints[(printer_id, archive.filename)] = archive.id
                if subtask_name:
                    _active_prints[(printer_id, f"{subtask_name}.3mf")] = archive.id

                # Mark as reprint so we add cost on completion
                _reprint_archives.add(archive.id)
                logger.info("Marked archive %s as reprint for cost addition on completion", archive.id)

                # Set up energy tracking
                try:
                    plug_result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
                    plug = plug_result.scalar_one_or_none()
                    logger.info(
                        f"[ENERGY] Print start - archive {archive.id}, printer {printer_id}, plug found: {plug is not None}"
                    )
                    if plug:
                        energy = await _get_plug_energy(plug, db)
                        logger.info("[ENERGY] Energy response from plug: %s", energy)
                        if energy and energy.get("total") is not None:
                            _print_energy_start[archive.id] = energy["total"]
                            logger.info(
                                f"[ENERGY] Recorded starting energy for archive {archive.id}: {energy['total']} kWh"
                            )
                        else:
                            logger.warning("[ENERGY] No 'total' in energy response for archive %s", archive.id)
                    else:
                        logger.info("[ENERGY] No smart plug found for printer %s", printer_id)
                except Exception as e:
                    logger.warning("Failed to record starting energy: %s", e)

                await ws_manager.send_archive_updated(
                    {
                        "id": archive.id,
                        "status": "printing",
                    }
                )

                # Send notification with archive data (reprint/scheduled)
                if not notification_sent:
                    archive_data = {"print_time_seconds": archive.print_time_seconds}
                    await _send_print_start_notification(printer_id, data, archive_data, logger)

                # Extract printable objects from the archived 3MF file
                _load_objects_from_archive(archive, printer_id, logger)

                # Store Spoolman tracking data for per-filament usage reporting
                try:
                    await _store_spoolman_print_data(printer_id, archive.id, archive.file_path, db, printer_manager)
                except Exception as e:
                    logger.warning("[SPOOLMAN] Failed to store tracking data: %s", e)

            return  # Skip creating a new archive

        # Check if there's already a "printing" archive for this printer/file
        # This prevents duplicates when backend restarts during an active print
        from backend.app.models.archive import PrintArchive

        check_name = subtask_name or filename.split("/")[-1].replace(".gcode", "").replace(".3mf", "")
        existing = await db.execute(
            select(PrintArchive)
            .where(PrintArchive.printer_id == printer_id)
            .where(PrintArchive.status == "printing")
            .where(
                or_(
                    PrintArchive.print_name == check_name,
                    PrintArchive.filename.in_(
                        [
                            f"{check_name}.3mf",
                            f"{check_name}.gcode.3mf",
                        ]
                    ),
                )
            )
            .order_by(PrintArchive.created_at.desc())
            .limit(1)
        )
        existing_archive = existing.scalar_one_or_none()
        if existing_archive:
            # Check if archive is stale (older than 4 hours) - likely a failed/cancelled print
            # that didn't get properly updated
            archive_age = datetime.now(timezone.utc) - existing_archive.created_at.replace(tzinfo=timezone.utc)
            if archive_age.total_seconds() > 4 * 60 * 60:  # 4 hours
                logger.warning(
                    f"Found stale 'printing' archive {existing_archive.id} (age: {archive_age}), "
                    f"marking as cancelled and creating new archive"
                )
                existing_archive.status = "cancelled"
                existing_archive.failure_reason = "Stale - print likely cancelled or failed without status update"
                await db.commit()
                # Fall through to create new archive (don't return)
                _existing_archive = None  # Clear so we don't use stale archive
            else:
                logger.info(
                    f"Skipping duplicate - already have printing archive {existing_archive.id} for {check_name}"
                )
                # Track this as the active print
                _active_prints[(printer_id, existing_archive.filename)] = existing_archive.id
                # Also set up energy tracking if not already tracked
                if existing_archive.id not in _print_energy_start:
                    try:
                        plug_result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
                        plug = plug_result.scalar_one_or_none()
                        if plug:
                            energy = await _get_plug_energy(plug, db)
                            if energy and energy.get("total") is not None:
                                _print_energy_start[existing_archive.id] = energy["total"]
                                logger.info(
                                    f"Recorded starting energy for existing archive {existing_archive.id}: {energy['total']} kWh"
                                )
                    except Exception as e:
                        logger.warning("Failed to record starting energy for existing archive: %s", e)
                # Send notification with archive data (existing archive)
                if not notification_sent:
                    archive_data = {"print_time_seconds": existing_archive.print_time_seconds}
                    await _send_print_start_notification(printer_id, data, archive_data, logger)
                # Extract printable objects from the archived 3MF file
                _load_objects_from_archive(existing_archive, printer_id, logger)
                return

        # Build list of possible 3MF filenames to try
        possible_names = []

        # Bambu printers typically store files as "Name.gcode.3mf"
        # The subtask_name is usually the best source for the filename
        if subtask_name:
            # Try common Bambu naming patterns
            possible_names.append(f"{subtask_name}.gcode.3mf")
            possible_names.append(f"{subtask_name}.3mf")

        # Try original filename with .3mf extension
        if filename:
            # Extract just the filename part, not the full path
            fname = filename.split("/")[-1] if "/" in filename else filename
            if fname.endswith(".3mf"):
                possible_names.append(fname)
            elif fname.endswith(".gcode"):
                base = fname.rsplit(".", 1)[0]
                possible_names.append(f"{base}.gcode.3mf")
                possible_names.append(f"{base}.3mf")
            else:
                possible_names.append(f"{fname}.gcode.3mf")
                possible_names.append(f"{fname}.3mf")

        # Also try with spaces converted to underscores (Bambu Studio may normalize filenames)
        space_variants = []
        for name in possible_names:
            if " " in name:
                space_variants.append(name.replace(" ", "_"))
        possible_names.extend(space_variants)

        # Remove duplicates while preserving order
        seen = set()
        possible_names = [x for x in possible_names if not (x in seen or seen.add(x))]

        logger.info("Trying filenames: %s", possible_names)

        # Try to find and download the 3MF file
        temp_path = None
        downloaded_filename = None

        # Get FTP retry settings
        ftp_retry_enabled, ftp_retry_count, ftp_retry_delay, ftp_timeout = await get_ftp_retry_settings()

        for try_filename in possible_names:
            if not try_filename.endswith(".3mf"):
                continue

            remote_paths = [
                f"/cache/{try_filename}",
                f"/model/{try_filename}",
                f"/data/{try_filename}",
                f"/data/Metadata/{try_filename}",
                f"/{try_filename}",
            ]

            temp_path = app_settings.archive_dir / "temp" / try_filename
            temp_path.parent.mkdir(parents=True, exist_ok=True)

            for remote_path in remote_paths:
                logger.debug("Trying FTP download: %s", remote_path)
                try:
                    if ftp_retry_enabled:
                        downloaded = await with_ftp_retry(
                            download_file_async,
                            printer.ip_address,
                            printer.access_code,
                            remote_path,
                            temp_path,
                            socket_timeout=ftp_timeout,
                            printer_model=printer.model,
                            max_retries=ftp_retry_count,
                            retry_delay=ftp_retry_delay,
                            operation_name=f"Download 3MF from {remote_path}",
                        )
                    else:
                        downloaded = await download_file_async(
                            printer.ip_address,
                            printer.access_code,
                            remote_path,
                            temp_path,
                            socket_timeout=ftp_timeout,
                            printer_model=printer.model,
                        )
                    if downloaded:
                        downloaded_filename = try_filename
                        logger.info("Downloaded: %s", remote_path)
                        break
                except Exception as e:
                    logger.debug("FTP download failed for %s: %s", remote_path, e)

            if downloaded_filename:
                break

        # If still not found, try listing directories to find matching file
        # Different printer models use different directory structures
        if not downloaded_filename and (filename or subtask_name):
            search_term = (subtask_name or filename).lower().replace(".gcode", "").replace(".3mf", "")
            logger.info("Direct FTP download failed, searching directories for '%s'", search_term)
            search_dirs = ["/cache", "/model", "/data", "/data/Metadata", "/"]
            for search_dir in search_dirs:
                if downloaded_filename:
                    break
                try:
                    dir_files = await list_files_async(
                        printer.ip_address, printer.access_code, search_dir, printer_model=printer.model
                    )
                    threemf_files = [f.get("name") for f in dir_files if f.get("name", "").endswith(".3mf")]
                    if threemf_files:
                        logger.info(
                            f"Found {len(threemf_files)} 3MF files in {search_dir}: {threemf_files[:5]}{'...' if len(threemf_files) > 5 else ''}"
                        )
                    for f in dir_files:
                        if f.get("is_directory"):
                            continue
                        fname = f.get("name", "")
                        # Normalize both for comparison (spaces and underscores are equivalent)
                        fname_normalized = fname.lower().replace(" ", "_")
                        search_normalized = search_term.replace(" ", "_")
                        if fname.endswith(".3mf") and search_normalized in fname_normalized:
                            logger.info("Found matching file in %s: %s", search_dir, fname)
                            temp_path = app_settings.archive_dir / "temp" / fname
                            temp_path.parent.mkdir(parents=True, exist_ok=True)
                            if ftp_retry_enabled:
                                downloaded = await with_ftp_retry(
                                    download_file_async,
                                    printer.ip_address,
                                    printer.access_code,
                                    f"{search_dir}/{fname}",
                                    temp_path,
                                    socket_timeout=ftp_timeout,
                                    printer_model=printer.model,
                                    max_retries=ftp_retry_count,
                                    retry_delay=ftp_retry_delay,
                                    operation_name=f"Download 3MF from {search_dir}/{fname}",
                                )
                            else:
                                downloaded = await download_file_async(
                                    printer.ip_address,
                                    printer.access_code,
                                    f"{search_dir}/{fname}",
                                    temp_path,
                                    socket_timeout=ftp_timeout,
                                    printer_model=printer.model,
                                )
                            if downloaded:
                                downloaded_filename = fname
                                logger.info("Found and downloaded from %s: %s", search_dir, fname)
                                break
                except Exception as e:
                    logger.debug("Failed to list %s: %s", search_dir, e)

        if not downloaded_filename or not temp_path:
            logger.warning("Could not find 3MF file for print: %s", filename or subtask_name)
            # Create a fallback archive without 3MF data so the print is still tracked
            # This commonly happens with P1S/A1 printers where FTP has file size limitations
            try:
                from backend.app.models.archive import PrintArchive

                # Derive print name from subtask_name or filename
                print_name = subtask_name or filename
                if print_name:
                    # Clean up the name (remove extensions, path parts)
                    print_name = print_name.split("/")[-1]
                    print_name = print_name.replace(".gcode.3mf", "").replace(".gcode", "").replace(".3mf", "")
                else:
                    print_name = "Unknown Print"

                # Create minimal archive entry
                fallback_archive = PrintArchive(
                    printer_id=printer_id,
                    filename=filename or f"{print_name}.3mf",
                    file_path="",  # Empty - no 3MF file available
                    file_size=0,
                    print_name=print_name,
                    status="printing",
                    started_at=datetime.now(),
                    extra_data={"no_3mf_available": True, "original_subtask": subtask_name, "_print_data": data},
                )

                db.add(fallback_archive)
                await db.commit()
                await db.refresh(fallback_archive)

                logger.info("Created fallback archive %s for %s (no 3MF available)", fallback_archive.id, print_name)

                # Start timelapse session if external camera is enabled
                if printer.external_camera_enabled and printer.external_camera_url:
                    from backend.app.services.layer_timelapse import start_session

                    start_session(
                        printer_id,
                        fallback_archive.id,
                        printer.external_camera_url,
                        printer.external_camera_type or "mjpeg",
                    )
                    logger.info("Started layer timelapse for printer %s, archive %s", printer_id, fallback_archive.id)

                # Track as active print
                _active_prints[(printer_id, fallback_archive.filename)] = fallback_archive.id
                if filename:
                    _active_prints[(printer_id, filename)] = fallback_archive.id
                if subtask_name:
                    _active_prints[(printer_id, f"{subtask_name}.3mf")] = fallback_archive.id
                    _active_prints[(printer_id, subtask_name)] = fallback_archive.id

                # Record starting energy if smart plug available
                try:
                    plug_result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
                    plug = plug_result.scalar_one_or_none()
                    if plug:
                        energy = await _get_plug_energy(plug, db)
                        if energy and energy.get("total") is not None:
                            _print_energy_start[fallback_archive.id] = energy["total"]
                            logger.info(
                                f"[ENERGY] Recorded starting energy for fallback archive {fallback_archive.id}: {energy['total']} kWh"
                            )
                except Exception as e:
                    logger.warning("Failed to record starting energy for fallback: %s", e)

                # Send WebSocket notification
                await ws_manager.send_archive_created(
                    {
                        "id": fallback_archive.id,
                        "printer_id": fallback_archive.printer_id,
                        "filename": fallback_archive.filename,
                        "print_name": fallback_archive.print_name,
                        "status": fallback_archive.status,
                    }
                )

                # MQTT relay - publish archive created
                try:
                    await mqtt_relay.on_archive_created(
                        archive_id=fallback_archive.id,
                        print_name=fallback_archive.print_name,
                        printer_name=printer.name,
                        status=fallback_archive.status,
                    )
                except Exception:
                    pass  # Don't fail if MQTT fails

                # Store Spoolman tracking data (may not work for fallback since no 3MF)
                try:
                    await _store_spoolman_print_data(
                        printer_id, fallback_archive.id, fallback_archive.file_path, db, printer_manager
                    )
                except Exception as e:
                    logger.debug("[SPOOLMAN] Could not store tracking for fallback archive: %s", e)

                # Send notification without archive data (file not found)
                if not notification_sent:
                    await _send_print_start_notification(printer_id, data, logger=logger)
                return
            except Exception as e:
                logger.error("Failed to create fallback archive: %s", e)
                # Send notification without archive data (file not found)
                if not notification_sent:
                    await _send_print_start_notification(printer_id, data, logger=logger)
                return

        try:
            # Archive the file with status "printing"
            service = ArchiveService(db)
            archive = await service.archive_print(
                printer_id=printer_id,
                source_file=temp_path,
                print_data={**data, "status": "printing"},
            )

            if archive:
                # Track this active print (use both original filename and downloaded filename)
                _active_prints[(printer_id, downloaded_filename)] = archive.id
                if filename and filename != downloaded_filename:
                    _active_prints[(printer_id, filename)] = archive.id
                if subtask_name:
                    _active_prints[(printer_id, f"{subtask_name}.3mf")] = archive.id

                logger.info("Created archive %s for %s", archive.id, downloaded_filename)

                # Start timelapse session if external camera is enabled
                if printer.external_camera_enabled and printer.external_camera_url:
                    from backend.app.services.layer_timelapse import start_session

                    start_session(
                        printer_id,
                        archive.id,
                        printer.external_camera_url,
                        printer.external_camera_type or "mjpeg",
                    )
                    logger.info("Started layer timelapse for printer %s, archive %s", printer_id, archive.id)

                # Record starting energy from smart plug if available
                try:
                    plug_result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
                    plug = plug_result.scalar_one_or_none()
                    logger.info(
                        f"[ENERGY] Auto-archive print start - archive {archive.id}, printer {printer_id}, plug found: {plug is not None}"
                    )
                    if plug:
                        energy = await _get_plug_energy(plug, db)
                        logger.info("[ENERGY] Auto-archive energy response: %s", energy)
                        if energy and energy.get("total") is not None:
                            _print_energy_start[archive.id] = energy["total"]
                            logger.info(
                                f"[ENERGY] Recorded starting energy for archive {archive.id}: {energy['total']} kWh"
                            )
                        else:
                            logger.warning("[ENERGY] No 'total' in energy response for archive %s", archive.id)
                    else:
                        logger.info("[ENERGY] No smart plug found for printer %s", printer_id)
                except Exception as e:
                    logger.warning("Failed to record starting energy: %s", e)

                await ws_manager.send_archive_created(
                    {
                        "id": archive.id,
                        "printer_id": archive.printer_id,
                        "filename": archive.filename,
                        "print_name": archive.print_name,
                        "status": archive.status,
                    }
                )

                # MQTT relay - publish archive created
                try:
                    await mqtt_relay.on_archive_created(
                        archive_id=archive.id,
                        print_name=archive.print_name,
                        printer_name=printer.name,
                        status=archive.status,
                    )
                except Exception:
                    pass  # Don't fail if MQTT fails

                # Send notification with archive data (new archive created)
                if not notification_sent:
                    archive_data = {"print_time_seconds": archive.print_time_seconds}
                    await _send_print_start_notification(printer_id, data, archive_data, logger)

                # Extract printable objects for skip object functionality
                try:
                    from backend.app.services.archive import extract_printable_objects_from_3mf

                    with open(temp_path, "rb") as f:
                        threemf_data = f.read()
                    # Extract with positions for UI overlay
                    printable_objects, bbox_all = extract_printable_objects_from_3mf(
                        threemf_data, include_positions=True
                    )
                    if printable_objects:
                        # Store objects in printer state
                        client = printer_manager.get_client(printer_id)
                        if client:
                            client.state.printable_objects = printable_objects
                            client.state.printable_objects_bbox_all = bbox_all
                            client.state.skipped_objects = []  # Reset skipped objects for new print
                            logger.info(
                                "Loaded %s printable objects for printer %s", len(printable_objects), printer_id
                            )
                except Exception as e:
                    logger.debug("Failed to extract printable objects: %s", e)

                # Store Spoolman tracking data for per-filament usage reporting
                try:
                    await _store_spoolman_print_data(printer_id, archive.id, archive.file_path, db, printer_manager)
                except Exception as e:
                    logger.warning("[SPOOLMAN] Failed to store tracking data: %s", e)

                # Capture timelapse file baseline for snapshot-diff on completion
                try:
                    baseline_files, _ = await _list_timelapse_videos(printer)
                    _timelapse_baselines[printer_id] = {f.get("name", "") for f in baseline_files}
                    logger.info(
                        "[TIMELAPSE] Baseline at print start: %s video files for printer %s",
                        len(_timelapse_baselines[printer_id]),
                        printer_id,
                    )
                except Exception as e:
                    logger.warning("[TIMELAPSE] Failed to capture baseline at print start: %s", e)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()


_TIMELAPSE_VIDEO_EXTENSIONS = (".mp4", ".avi")


async def _list_timelapse_videos(printer) -> tuple[list[dict], str | None]:
    """List video files from printer's timelapse directory.

    Finds MP4 (X1/A1 series) and AVI (P1 series) timelapse files.
    Returns (video_files, found_path) where video_files is a list of file dicts
    and found_path is the directory where they were found, or ([], None).
    """
    from backend.app.services.bambu_ftp import list_files_async

    logger = logging.getLogger(__name__)

    for timelapse_path in ["/timelapse", "/timelapse/video", "/record", "/recording"]:
        try:
            found_files = await list_files_async(
                printer.ip_address, printer.access_code, timelapse_path, printer_model=printer.model
            )
            if found_files:
                video_files = [
                    f
                    for f in found_files
                    if not f.get("is_directory") and f.get("name", "").lower().endswith(_TIMELAPSE_VIDEO_EXTENSIONS)
                ]
                if video_files:
                    return video_files, timelapse_path
        except Exception as e:
            logger.debug("[TIMELAPSE] Path %s failed: %s", timelapse_path, e)
            continue

    return [], None


async def _scan_for_timelapse_with_retries(archive_id: int, baseline_names: set[str] | None = None):
    """
    Scan for timelapse with retries using a snapshot-diff approach.

    Instead of picking the "most recent by mtime" (unreliable when the printer
    clock is wrong in LAN-only mode), we snapshot existing MP4 filenames BEFORE
    waiting, then look for any NEW filename that appears after each delay.

    If baseline_names is provided (captured at print start), it is used directly.
    Otherwise falls back to taking a baseline at completion time (best-effort
    for prints started before app restart).

    Falls back to name-matching (print name contained in MP4 filename) if no
    new file appears after all retries.
    """
    from pathlib import Path

    logger = logging.getLogger(__name__)

    # --- Phase 1: Take baseline snapshot of existing timelapse files ---
    try:
        async with async_session() as db:
            from backend.app.models.printer import Printer

            service = ArchiveService(db)
            archive = await service.get_archive(archive_id)

            if not archive:
                logger.warning("[TIMELAPSE] Archive %s not found, aborting", archive_id)
                return
            if archive.timelapse_path:
                logger.info("[TIMELAPSE] Archive %s already has timelapse attached", archive_id)
                return
            if not archive.printer_id:
                logger.warning("[TIMELAPSE] Archive %s has no printer, aborting", archive_id)
                return

            if baseline_names is not None:
                # Use pre-captured baseline from print start (no race condition)
                logger.info(
                    "[TIMELAPSE] Using print-start baseline: %s existing video files for archive %s",
                    len(baseline_names),
                    archive_id,
                )
            else:
                # Fallback: take baseline now (e.g. app restarted mid-print)
                result = await db.execute(select(Printer).where(Printer.id == archive.printer_id))
                printer = result.scalar_one_or_none()
                if not printer:
                    logger.warning("[TIMELAPSE] Printer not found for archive %s, aborting", archive_id)
                    return

                baseline_files, _ = await _list_timelapse_videos(printer)
                baseline_names = {f.get("name", "") for f in baseline_files}
                logger.info(
                    "[TIMELAPSE] Baseline snapshot (fallback): %s existing video files for archive %s",
                    len(baseline_names),
                    archive_id,
                )

            # Derive base_name for name-matching fallback
            base_name = Path(archive.filename).stem if archive.filename else ""
            if base_name.endswith(".gcode"):
                base_name = base_name[:-6]

    except Exception as e:
        logger.warning("[TIMELAPSE] Failed to take baseline snapshot for archive %s: %s", archive_id, e)
        return

    # --- Phase 2: Retry loop — look for NEW files that weren't in baseline ---
    retry_delays = [5, 10, 20, 30]

    for attempt, delay in enumerate(retry_delays, 1):
        logger.info(
            "[TIMELAPSE] Attempt %s/%s: waiting %ss before scanning for archive %s",
            attempt,
            len(retry_delays),
            delay,
            archive_id,
        )
        await asyncio.sleep(delay)

        try:
            async with async_session() as db:
                from backend.app.models.printer import Printer
                from backend.app.services.bambu_ftp import download_file_bytes_async

                service = ArchiveService(db)
                archive = await service.get_archive(archive_id)

                if not archive:
                    logger.warning("[TIMELAPSE] Archive %s not found, stopping retries", archive_id)
                    return
                if archive.timelapse_path:
                    logger.info("[TIMELAPSE] Archive %s already has timelapse attached, stopping retries", archive_id)
                    return

                result = await db.execute(select(Printer).where(Printer.id == archive.printer_id))
                printer = result.scalar_one_or_none()
                if not printer:
                    logger.warning("[TIMELAPSE] Printer not found for archive %s, stopping retries", archive_id)
                    return

                video_files, found_path = await _list_timelapse_videos(printer)

                if not video_files:
                    logger.info("[TIMELAPSE] Attempt %s: No video files found, will retry", attempt)
                    continue

                logger.info("[TIMELAPSE] Attempt %s: Found %s video files in %s", attempt, len(video_files), found_path)
                for f in video_files[:5]:
                    logger.info("[TIMELAPSE]   - %s", f.get("name"))

                # Find files that are NEW (not in baseline snapshot)
                new_files = [f for f in video_files if f.get("name", "") not in baseline_names]

                if new_files:
                    # Pick the first new file (there should typically be exactly one)
                    target = new_files[0]
                    file_name = target.get("name")
                    remote_path = target.get("path") or f"/timelapse/{file_name}"
                    logger.info(
                        "[TIMELAPSE] Attempt %s: New file detected: %s (downloading for archive %s)",
                        attempt,
                        file_name,
                        archive_id,
                    )

                    timelapse_data = await download_file_bytes_async(
                        printer.ip_address, printer.access_code, remote_path, printer_model=printer.model
                    )
                    if timelapse_data:
                        success = await service.attach_timelapse(archive_id, timelapse_data, file_name)
                        if success:
                            logger.info("[TIMELAPSE] Successfully attached timelapse to archive %s", archive_id)
                            await ws_manager.send_archive_updated({"id": archive_id, "timelapse_attached": True})
                            return
                        else:
                            logger.warning("[TIMELAPSE] Failed to attach timelapse to archive %s", archive_id)
                    else:
                        logger.warning("[TIMELAPSE] Attempt %s: Failed to download new file, will retry", attempt)
                else:
                    logger.info("[TIMELAPSE] Attempt %s: No new files since baseline, will retry", attempt)

        except Exception as e:
            logger.warning("[TIMELAPSE] Attempt %s failed with error: %s", attempt, e)

    # --- Phase 3: Fallback — try name matching against all files ---
    if base_name:
        logger.info("[TIMELAPSE] Retries exhausted, trying name-match fallback for '%s'", base_name)
        try:
            async with async_session() as db:
                from backend.app.models.printer import Printer
                from backend.app.services.bambu_ftp import download_file_bytes_async

                service = ArchiveService(db)
                archive = await service.get_archive(archive_id)
                if not archive or archive.timelapse_path:
                    return

                result = await db.execute(select(Printer).where(Printer.id == archive.printer_id))
                printer = result.scalar_one_or_none()
                if not printer:
                    return

                video_files, found_path = await _list_timelapse_videos(printer)
                for f in video_files:
                    fname = f.get("name", "")
                    if base_name.lower() in fname.lower():
                        remote_path = f.get("path") or f"/timelapse/{fname}"
                        logger.info("[TIMELAPSE] Name-match fallback: '%s' matches '%s'", base_name, fname)

                        timelapse_data = await download_file_bytes_async(
                            printer.ip_address, printer.access_code, remote_path, printer_model=printer.model
                        )
                        if timelapse_data:
                            success = await service.attach_timelapse(archive_id, timelapse_data, fname)
                            if success:
                                logger.info(
                                    "[TIMELAPSE] Name-match fallback attached timelapse to archive %s", archive_id
                                )
                                await ws_manager.send_archive_updated({"id": archive_id, "timelapse_attached": True})
                                return
                        break  # Only try the first name match

        except Exception as e:
            logger.warning("[TIMELAPSE] Name-match fallback failed: %s", e)

    logger.warning("[TIMELAPSE] All attempts exhausted for archive %s, giving up", archive_id)


async def on_print_complete(printer_id: int, data: dict):
    """Handle print completion - update the archive status."""
    import time

    logger = logging.getLogger(__name__)
    start_time = time.time()

    def log_timing(section: str):
        elapsed = time.time() - start_time
        logger.info("[TIMING] %s: %.3fs elapsed", section, elapsed)

    logger.info("[CALLBACK] on_print_complete started for printer %s", printer_id)

    try:
        ws_data = {
            "status": data.get("status"),
            "filename": data.get("filename"),
            "subtask_name": data.get("subtask_name"),
            "timelapse_was_active": data.get("timelapse_was_active"),
        }
        await ws_manager.send_print_complete(printer_id, ws_data)
        log_timing("WebSocket send_print_complete")
    except Exception as e:
        logger.warning("[CALLBACK] WebSocket send_print_complete failed: %s", e)

    # Capture user info before clearing (needed for print log entry)
    _print_user_info = printer_manager.get_current_print_user(printer_id)

    # Clear current print user tracking (Issue #206)
    printer_manager.clear_current_print_user(printer_id)

    # MQTT relay - publish print complete
    try:
        printer_info = printer_manager.get_printer(printer_id)
        if printer_info:
            await mqtt_relay.on_print_complete(
                printer_id,
                printer_info.name,
                printer_info.serial_number,
                data.get("filename", ""),
                data.get("subtask_name", ""),
                data.get("status", "completed"),
            )
    except Exception:
        pass  # Don't fail print complete callback if MQTT fails

    filename = data.get("filename", "")
    subtask_name = data.get("subtask_name", "")

    if not filename and not subtask_name:
        logger.warning("Print complete without filename or subtask_name")
        return

    logger.info("Print complete - filename: %s, subtask: %s, status: %s", filename, subtask_name, data.get("status"))

    # Build list of possible keys to try (matching how they were registered in on_print_start)
    possible_keys = []

    # Try subtask_name variations first (most reliable for matching)
    if subtask_name:
        possible_keys.append((printer_id, f"{subtask_name}.3mf"))
        possible_keys.append((printer_id, f"{subtask_name}.gcode.3mf"))
        possible_keys.append((printer_id, subtask_name))

    # Try filename variations
    if filename:
        # Extract just the filename if it's a path
        fname = filename.split("/")[-1] if "/" in filename else filename

        if fname.endswith(".3mf"):
            possible_keys.append((printer_id, fname))
        elif fname.endswith(".gcode"):
            base_name = fname.rsplit(".", 1)[0]
            possible_keys.append((printer_id, f"{base_name}.gcode.3mf"))
            possible_keys.append((printer_id, f"{base_name}.3mf"))
            possible_keys.append((printer_id, fname))
        else:
            possible_keys.append((printer_id, f"{fname}.gcode.3mf"))
            possible_keys.append((printer_id, f"{fname}.3mf"))
            possible_keys.append((printer_id, fname))

        # Also try full path versions
        if filename.endswith(".3mf"):
            possible_keys.append((printer_id, filename))
        elif filename.endswith(".gcode"):
            base_name = filename.rsplit(".", 1)[0]
            possible_keys.append((printer_id, f"{base_name}.3mf"))
            possible_keys.append((printer_id, filename))
        else:
            possible_keys.append((printer_id, f"{filename}.3mf"))
            possible_keys.append((printer_id, filename))

    # Find the archive for this print
    logger.info("Looking for archive in _active_prints, keys to try: %s...", possible_keys[:5])
    logger.info("Current _active_prints: %s", list(_active_prints.keys()))
    archive_id = None
    for key in possible_keys:
        archive_id = _active_prints.pop(key, None)
        if archive_id:
            logger.info("Found archive %s with key %s", archive_id, key)
            # Also clean up any other keys pointing to this archive
            keys_to_remove = [k for k, v in _active_prints.items() if v == archive_id]
            for k in keys_to_remove:
                _active_prints.pop(k, None)
            break

    if not archive_id:
        # Try to find by filename or subtask_name if not tracked (for prints started before app)
        async with async_session() as db:
            from backend.app.models.archive import PrintArchive

            # Try matching by subtask_name (stored as print_name) first
            if subtask_name:
                result = await db.execute(
                    select(PrintArchive)
                    .where(PrintArchive.printer_id == printer_id)
                    .where(PrintArchive.status == "printing")
                    .where(
                        or_(
                            PrintArchive.print_name.ilike(f"%{subtask_name}%"),
                            PrintArchive.filename.ilike(f"%{subtask_name}%"),
                        )
                    )
                    .order_by(PrintArchive.created_at.desc())
                    .limit(1)
                )
                archive = result.scalar_one_or_none()
                if archive:
                    archive_id = archive.id
                    logger.info("Found archive %s by subtask_name match: %s", archive_id, subtask_name)

            # Also try by filename
            if not archive_id and filename:
                result = await db.execute(
                    select(PrintArchive)
                    .where(PrintArchive.printer_id == printer_id)
                    .where(PrintArchive.filename == filename)
                    .where(PrintArchive.status == "printing")
                    .order_by(PrintArchive.created_at.desc())
                    .limit(1)
                )
                archive = result.scalar_one_or_none()
                if archive:
                    archive_id = archive.id

    # Cleanup: delete uploaded file from printer SD card to prevent phantom prints (Issue #374)
    # The print scheduler uploads files to the SD card root (/). Some printers (e.g. P1S)
    # auto-start files found in root on power cycle, causing ghost prints.
    # Must run before the archive_id early-return so it executes even when archiving is disabled.
    try:
        printer_info = printer_manager.get_printer(printer_id)
        if printer_info and subtask_name:
            from backend.app.services.bambu_ftp import delete_file_async

            # Try both .3mf and .gcode extensions — the printer may have either
            for ext in (".3mf", ".gcode"):
                remote_path = f"/{subtask_name}{ext}"
                # Retry up to 3 times — the printer may still lock the filesystem briefly after a print ends
                for attempt in range(1, 4):
                    try:
                        delete_result = await delete_file_async(
                            printer_info.ip_address,
                            printer_info.access_code,
                            remote_path,
                            printer_model=printer_info.model,
                        )
                        if delete_result:
                            logger.info("Deleted %s from printer %s SD card", remote_path, printer_info.name)
                        break  # Success or file doesn't exist — no need to retry
                    except Exception as e:
                        if attempt < 3:
                            logger.debug(
                                "SD card cleanup attempt %d/3 failed for %s: %s, retrying in 2s",
                                attempt,
                                remote_path,
                                e,
                            )
                            await asyncio.sleep(2)
                        else:
                            logger.debug(
                                "SD card cleanup failed after 3 attempts for %s: %s (non-critical)",
                                remote_path,
                                e,
                            )
    except Exception as e:
        logger.debug("SD card file cleanup failed for printer %s: %s (non-critical)", printer_id, e)

    log_timing("SD card cleanup")

    # Update queue item status early — must run before the archive_id early-return
    # so queue items don't get stuck in "printing" when archive lookup fails.
    try:
        async with async_session() as db:
            from backend.app.models.print_queue import PrintQueueItem

            result = await db.execute(
                select(PrintQueueItem)
                .where(PrintQueueItem.printer_id == printer_id)
                .where(PrintQueueItem.status == "printing")
            )
            printing_items = list(result.scalars().all())
            if len(printing_items) > 1:
                logger.warning(
                    "BUG: Multiple queue items in 'printing' status for printer %s: %s",
                    printer_id,
                    [(i.id, i.archive_id, i.library_file_id) for i in printing_items],
                )
            queue_item = printing_items[0] if printing_items else None
            if queue_item:
                queue_status = data.get("status", "completed")
                queue_item.status = queue_status
                queue_item.completed_at = datetime.now()
                await db.commit()
                logger.info("Updated queue item %s status to %s", queue_item.id, queue_status)

                # MQTT relay - publish queue job completed
                try:
                    printer_info = printer_manager.get_printer(printer_id)
                    await mqtt_relay.on_queue_job_completed(
                        job_id=queue_item.id,
                        filename=filename or subtask_name,
                        printer_id=printer_id,
                        printer_name=printer_info.name if printer_info else "Unknown",
                        status=queue_status,
                    )
                except Exception:
                    pass  # Don't fail if MQTT fails

                # Check if queue is now empty and send notification
                try:
                    from sqlalchemy import func as sa_func

                    count_result = await db.execute(
                        select(sa_func.count(PrintQueueItem.id)).where(PrintQueueItem.status == "pending")
                    )
                    pending_count = count_result.scalar() or 0

                    if pending_count == 0:
                        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                        completed_result = await db.execute(
                            select(sa_func.count(PrintQueueItem.id)).where(
                                PrintQueueItem.status.in_(["completed", "failed", "skipped"]),
                                PrintQueueItem.completed_at >= today_start,
                            )
                        )
                        completed_count = completed_result.scalar() or 1

                        await notification_service.on_queue_completed(
                            completed_count=completed_count,
                            db=db,
                        )
                except Exception:
                    pass  # Don't fail if notification fails

                # Handle auto_off_after - power off printer if requested (after cooldown)
                if queue_item.auto_off_after:
                    result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
                    plug = result.scalar_one_or_none()
                    if plug and plug.enabled:
                        logger.info("Auto-off requested for printer %s, waiting for cooldown...", printer_id)

                        async def cooldown_and_poweroff(pid: int, plug_id: int):
                            # Wait for nozzle to cool down
                            await printer_manager.wait_for_cooldown(pid, target_temp=50.0, timeout=600)
                            # Re-fetch plug in new session
                            async with async_session() as new_db:
                                result = await new_db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
                                p = result.scalar_one_or_none()
                                if p and p.enabled:
                                    success = await tasmota_service.turn_off(p)
                                    if success:
                                        logger.info("Powered off printer %s via smart plug '%s'", pid, p.name)
                                    else:
                                        logger.warning("Failed to power off printer %s via smart plug", pid)

                        asyncio.create_task(cooldown_and_poweroff(printer_id, plug.id))
    except Exception as e:
        logging.getLogger(__name__).warning(f"Queue item update failed: {e}")

    log_timing("Queue item update")

    if not archive_id:
        logger.warning("Could not find archive for print complete: filename=%s, subtask=%s", filename, subtask_name)
        return

    log_timing("Archive lookup")

    # Update archive status
    logger.info("[ARCHIVE] Updating archive %s status...", archive_id)
    try:
        async with async_session() as db:
            service = ArchiveService(db)
            status = data.get("status", "completed")

            # Auto-detect failure reason
            failure_reason = None
            if status == "aborted":
                failure_reason = "User cancelled"
                logger.info("[ARCHIVE] Print was aborted by user, setting failure_reason='User cancelled'")
            elif status == "failed":
                # Try to determine failure reason from HMS errors
                hms_errors = data.get("hms_errors", [])
                if hms_errors:
                    logger.info("[ARCHIVE] HMS errors at failure: %s", hms_errors)
                    # Map known HMS error modules to failure reasons
                    # Module 0x07 = Filament, 0x0C = MC (Motion Controller), etc.
                    for err in hms_errors:
                        module = err.get("module", 0)
                        if module == 0x07:  # Filament module
                            failure_reason = "Filament runout"
                            break
                        elif module == 0x0C:  # Motion controller
                            failure_reason = "Layer shift"
                            break
                        elif module == 0x05:  # Nozzle/extruder
                            failure_reason = "Clogged nozzle"
                            break
                    if failure_reason:
                        logger.info("[ARCHIVE] Detected failure_reason from HMS: %s", failure_reason)
                else:
                    logger.info("[ARCHIVE] No HMS errors available to determine failure reason")

            await service.update_archive_status(
                archive_id,
                status=status,
                completed_at=datetime.now() if status in ("completed", "failed", "aborted") else None,
                failure_reason=failure_reason,
            )
            logger.info(
                "[ARCHIVE] Archive %s status updated to %s, failure_reason=%s", archive_id, status, failure_reason
            )

            # Add cost for reprints (first prints have cost set in archive_print())
            if status == "completed" and archive_id in _reprint_archives:
                _reprint_archives.discard(archive_id)
                try:
                    await service.add_reprint_cost(archive_id)
                    logger.info("[ARCHIVE] Added reprint cost for archive %s", archive_id)
                except Exception as e:
                    logger.warning("[ARCHIVE] Failed to add reprint cost for archive %s: %s", archive_id, e)

            await ws_manager.send_archive_updated(
                {
                    "id": archive_id,
                    "status": status,
                }
            )
            logger.info("[ARCHIVE] WebSocket notification sent for archive %s", archive_id)

            # MQTT relay - publish archive updated
            try:
                await mqtt_relay.on_archive_updated(
                    archive_id=archive_id,
                    print_name=filename or subtask_name,
                    status=status,
                )
            except Exception:
                pass  # Don't fail if MQTT fails
    except Exception as e:
        logger.error("[ARCHIVE] Failed to update archive %s status: %s", archive_id, e, exc_info=True)
        # Continue with other operations even if archive update fails

    log_timing("Archive status update")

    # Write independent print log entry (separate table, never touches archives)
    try:
        async with async_session() as db:
            from backend.app.models.archive import PrintArchive
            from backend.app.services.print_log import write_log_entry

            archive = await db.get(PrintArchive, archive_id)
            if archive:
                p_info = printer_manager.get_printer(printer_id)
                await write_log_entry(
                    db,
                    status=data.get("status", "completed"),
                    print_name=archive.print_name,
                    printer_name=p_info.name if p_info else None,
                    printer_id=printer_id,
                    started_at=archive.started_at,
                    completed_at=archive.completed_at,
                    filament_type=archive.filament_type,
                    filament_color=archive.filament_color,
                    filament_used_grams=archive.filament_used_grams,
                    thumbnail_path=archive.thumbnail_path,
                    created_by_username=_print_user_info.get("username") if _print_user_info else None,
                )
                await db.commit()
                logger.info("[PRINT_LOG] Log entry written for archive %s", archive_id)
    except Exception as e:
        logger.warning("[PRINT_LOG] Failed to write log entry for archive %s: %s", archive_id, e)

    log_timing("Print log entry")

    # Track filament consumption from AMS remain% deltas (skip if Spoolman handles usage)
    usage_results: list[dict] = []
    # Prefer ams_mapping captured from MQTT request topic (works for all print sources)
    stored_ams_mapping = data.get("ams_mapping")
    # Fallback to _print_ams_mappings for queue/reprint (set before print starts)
    if not stored_ams_mapping and archive_id:
        stored_ams_mapping = _print_ams_mappings.pop(archive_id, None)
    try:
        async with async_session() as db:
            from backend.app.api.routes.settings import get_setting

            _spoolman_on = await get_setting(db, "spoolman_enabled")
        if not _spoolman_on or _spoolman_on.lower() != "true":
            from backend.app.services.usage_tracker import on_print_complete as usage_on_print_complete

            async with async_session() as db:
                usage_results = await usage_on_print_complete(
                    printer_id,
                    data,
                    printer_manager,
                    db,
                    archive_id=archive_id,
                    ams_mapping=stored_ams_mapping,
                )
                if usage_results:
                    await ws_manager.broadcast(
                        {
                            "type": "spool_usage_logged",
                            "printer_id": printer_id,
                            "usage": usage_results,
                        }
                    )
                    log_timing("Usage tracker")
    except Exception as e:
        logger.warning("Usage tracker on_print_complete failed: %s", e)

    # Report filament usage to Spoolman if print completed successfully
    if data.get("status") == "completed":
        try:
            await _report_spoolman_usage(printer_id, archive_id)
            log_timing("Spoolman usage report")
        except Exception as e:
            logger.warning("Spoolman usage reporting failed: %s", e)
    else:
        # Report partial usage if tracking data exists (only stored when weight sync is disabled)
        try:
            async with async_session() as db:
                await _cleanup_spoolman_tracking(printer_id, archive_id, db)
        except Exception as e:
            logger.debug("[SPOOLMAN] Cleanup failed: %s", e)

    # Run slow operations as background tasks to avoid blocking the event loop
    # These operations can take 5-10+ seconds and would freeze the UI if awaited
    starting_kwh = _print_energy_start.pop(archive_id, None)

    async def _background_energy_calculation():
        """Calculate and save energy usage in background."""
        try:
            logger.info("[ENERGY-BG] Starting energy calculation for archive %s", archive_id)
            async with async_session() as db:
                plug_result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
                plug = plug_result.scalar_one_or_none()

                if plug:
                    energy = await _get_plug_energy(plug, db)
                    logger.info("[ENERGY-BG] Energy response: %s", energy)

                    energy_used = None
                    if starting_kwh is not None and energy and energy.get("total") is not None:
                        ending_kwh = energy["total"]
                        energy_used = round(ending_kwh - starting_kwh, 4)
                        logger.info("[ENERGY-BG] Per-print energy: %s kWh", energy_used)

                    if energy_used is not None and energy_used >= 0:
                        from backend.app.api.routes.settings import get_setting

                        energy_cost_per_kwh = await get_setting(db, "energy_cost_per_kwh")
                        cost_per_kwh = float(energy_cost_per_kwh) if energy_cost_per_kwh else 0.15
                        energy_cost = round(energy_used * cost_per_kwh, 2)

                        from backend.app.models.archive import PrintArchive

                        result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
                        archive = result.scalar_one_or_none()
                        if archive:
                            archive.energy_kwh = energy_used
                            archive.energy_cost = energy_cost
                            await db.commit()
                            logger.info("[ENERGY-BG] Saved: %s kWh, cost=%s", energy_used, energy_cost)
                else:
                    logger.info("[ENERGY-BG] No smart plug for printer %s", printer_id)
        except Exception as e:
            logger.warning("[ENERGY-BG] Failed: %s", e)

    async def _background_finish_photo() -> str | None:
        """Capture finish photo in background. Returns photo filename if captured."""
        try:
            logger.info("[PHOTO-BG] Starting finish photo capture for archive %s", archive_id)

            from backend.app.api.routes.camera import _active_chamber_streams, _active_streams, get_buffered_frame

            async with async_session() as db:
                from backend.app.api.routes.settings import get_setting

                capture_enabled = await get_setting(db, "capture_finish_photo")

                if capture_enabled is None or capture_enabled.lower() == "true":
                    from backend.app.models.printer import Printer

                    result = await db.execute(select(Printer).where(Printer.id == printer_id))
                    printer = result.scalar_one_or_none()

                    if printer and archive_id:
                        from backend.app.models.archive import PrintArchive

                        result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
                        archive = result.scalar_one_or_none()

                        if archive:
                            import uuid
                            from datetime import datetime
                            from pathlib import Path

                            archive_dir = app_settings.base_dir / Path(archive.file_path).parent
                            photo_filename = None

                            # Check for external camera first
                            if printer.external_camera_enabled and printer.external_camera_url:
                                logger.info("[PHOTO-BG] Using external camera")
                                from backend.app.services.external_camera import capture_frame

                                frame_data = await capture_frame(
                                    printer.external_camera_url, printer.external_camera_type or "mjpeg"
                                )
                                if frame_data:
                                    photos_dir = archive_dir / "photos"
                                    photos_dir.mkdir(parents=True, exist_ok=True)
                                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                                    photo_filename = f"finish_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
                                    photo_path = photos_dir / photo_filename
                                    await asyncio.to_thread(photo_path.write_bytes, frame_data)
                                    logger.info("[PHOTO-BG] Saved external camera frame: %s", photo_filename)
                            else:
                                # Check if camera stream is active - use buffered frame to avoid freeze
                                # Check both RTSP streams (_active_streams) and chamber image streams (_active_chamber_streams)
                                active_for_printer = [k for k in _active_streams if k.startswith(f"{printer_id}-")]
                                active_chamber_for_printer = [
                                    k for k in _active_chamber_streams if k.startswith(f"{printer_id}-")
                                ]
                                buffered_frame = get_buffered_frame(printer_id)

                                if (active_for_printer or active_chamber_for_printer) and buffered_frame:
                                    # Use frame from active stream
                                    logger.info("[PHOTO-BG] Using buffered frame from active stream")
                                    photos_dir = archive_dir / "photos"
                                    photos_dir.mkdir(parents=True, exist_ok=True)
                                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                                    photo_filename = f"finish_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
                                    photo_path = photos_dir / photo_filename
                                    await asyncio.to_thread(photo_path.write_bytes, buffered_frame)
                                    logger.info("[PHOTO-BG] Saved buffered frame: %s", photo_filename)
                                else:
                                    # No active stream - capture new frame
                                    from backend.app.services.camera import capture_finish_photo

                                    photo_filename = await capture_finish_photo(
                                        printer_id=printer_id,
                                        ip_address=printer.ip_address,
                                        access_code=printer.access_code,
                                        model=printer.model,
                                        archive_dir=archive_dir,
                                    )

                            if photo_filename:
                                photos = archive.photos or []
                                photos.append(photo_filename)
                                archive.photos = photos
                                await db.commit()
                                logger.info("[PHOTO-BG] Saved: %s", photo_filename)
                                return photo_filename
            return None
        except Exception as e:
            logger.warning("[PHOTO-BG] Failed: %s", e)
            return None

    asyncio.create_task(_background_energy_calculation())
    # Photo capture task - result will be used by notifications
    photo_task = asyncio.create_task(_background_finish_photo())
    log_timing("Background tasks scheduled (energy, photo)")

    # Also run smart plug, notifications, and maintenance as background tasks
    print_status = data.get("status", "completed")

    async def _background_smart_plug():
        """Handle smart plug automation in background."""
        try:
            logger.info("[AUTO-OFF-BG] Starting smart plug automation for printer %s", printer_id)
            async with async_session() as db:
                await smart_plug_manager.on_print_complete(printer_id, print_status, db)
                logger.info("[AUTO-OFF-BG] Completed")
        except Exception as e:
            logger.warning("[AUTO-OFF-BG] Failed: %s", e)

    async def _background_notifications(finish_photo_filename: str | None = None):
        """Send print complete notifications in background."""
        try:
            logger.info(
                "[NOTIFY-BG] Starting notifications for printer %s, photo=%s", printer_id, finish_photo_filename
            )
            async with async_session() as db:
                from backend.app.models.archive import PrintArchive
                from backend.app.models.printer import Printer

                result = await db.execute(select(Printer).where(Printer.id == printer_id))
                printer = result.scalar_one_or_none()
                printer_name = printer.name if printer else f"Printer {printer_id}"

                archive_data = None
                if archive_id:
                    archive_result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
                    archive = archive_result.scalar_one_or_none()
                    if archive:
                        archive_data = {
                            "print_time_seconds": archive.print_time_seconds,
                            "actual_filament_grams": archive.filament_used_grams,
                            "failure_reason": archive.failure_reason,
                        }

                        # Scale filament usage for partial prints
                        if print_status != "completed" and archive.filament_used_grams:
                            progress = data.get("progress") or 0
                            scale = max(0.0, min(progress / 100.0, 1.0))
                            archive_data["actual_filament_grams"] = round(archive.filament_used_grams * scale, 1)
                            archive_data["progress"] = progress

                        # Pass per-slot data from archive.extra_data
                        if archive.extra_data and archive.extra_data.get("filament_slots"):
                            slots = archive.extra_data["filament_slots"]
                            if print_status != "completed":
                                scale = max(0.0, min((data.get("progress") or 0) / 100.0, 1.0))
                                slots = [{**s, "used_g": round(s["used_g"] * scale, 1)} for s in slots]
                            archive_data["filament_slots"] = slots

                        # Pass usage tracker results for AMS slot info in notifications
                        if usage_results:
                            archive_data["usage_results"] = usage_results
                        # Add finish photo URL and image bytes if available
                        if finish_photo_filename:
                            from backend.app.api.routes.settings import get_setting

                            external_url = await get_setting(db, "external_url")
                            if external_url:
                                external_url = external_url.rstrip("/")
                                archive_data["finish_photo_url"] = (
                                    f"{external_url}/api/v1/archives/{archive_id}/photos/{finish_photo_filename}"
                                )
                            else:
                                # Fallback to relative URL (won't work for external services)
                                archive_data["finish_photo_url"] = (
                                    f"/api/v1/archives/{archive_id}/photos/{finish_photo_filename}"
                                )

                            # Read finish photo bytes for image attachment (e.g. Pushover)
                            try:
                                from pathlib import Path

                                photo_path = (
                                    app_settings.base_dir
                                    / Path(archive.file_path).parent
                                    / "photos"
                                    / finish_photo_filename
                                )
                                if photo_path.exists():
                                    photo_bytes = await asyncio.to_thread(photo_path.read_bytes)
                                    if len(photo_bytes) <= 2_500_000:
                                        archive_data["image_data"] = photo_bytes
                                        logger.info("[NOTIFY-BG] Loaded finish photo bytes: %s bytes", len(photo_bytes))
                                    else:
                                        logger.warning(
                                            f"[NOTIFY-BG] Finish photo too large for attachment: "
                                            f"{len(photo_bytes)} bytes"
                                        )
                            except Exception as e:
                                logger.warning("[NOTIFY-BG] Failed to read finish photo bytes: %s", e)

                await notification_service.on_print_complete(
                    printer_id, printer_name, print_status, data, db, archive_data=archive_data
                )
                logger.info("[NOTIFY-BG] Completed")
        except Exception as e:
            logger.warning("[NOTIFY-BG] Failed: %s", e)

    async def _background_maintenance_check():
        """Check for maintenance due in background."""
        if print_status != "completed":
            return
        try:
            logger.info("[MAINT-BG] Starting maintenance check for printer %s", printer_id)
            async with async_session() as db:
                from backend.app.models.printer import Printer

                result = await db.execute(select(Printer).where(Printer.id == printer_id))
                printer = result.scalar_one_or_none()
                printer_name = printer.name if printer else f"Printer {printer_id}"

                await ensure_default_types(db)
                overview = await _get_printer_maintenance_internal(printer_id, db, commit=True)

                items_needing_attention = [
                    {"name": item.maintenance_type_name, "is_due": item.is_due, "is_warning": item.is_warning}
                    for item in overview.maintenance_items
                    if item.enabled and (item.is_due or item.is_warning)
                ]

                if items_needing_attention:
                    await notification_service.on_maintenance_due(printer_id, printer_name, items_needing_attention, db)
                    logger.info("[MAINT-BG] Sent notification: %s items need attention", len(items_needing_attention))

                    # MQTT relay - publish maintenance alerts
                    for item in items_needing_attention:
                        try:
                            await mqtt_relay.on_maintenance_alert(
                                printer_id=printer_id,
                                printer_name=printer_name,
                                maintenance_type=item["name"],
                                current_value=0,  # Not easily available here
                                threshold=0,  # Not easily available here
                            )
                        except Exception:
                            pass  # Don't fail if MQTT fails
                else:
                    logger.info("[MAINT-BG] Completed (no items need attention)")
        except Exception as e:
            logger.warning("[MAINT-BG] Failed: %s", e)

    asyncio.create_task(_background_smart_plug())
    asyncio.create_task(_background_maintenance_check())

    # Notification task waits for photo capture to complete first
    async def _photo_then_notify():
        """Wait for photo capture, then send notification with photo URL."""
        try:
            finish_photo = await photo_task
            logger.info("[PHOTO-NOTIFY] Photo task returned: %s", finish_photo)
            await _background_notifications(finish_photo)
        except Exception as e:
            logger.warning("[PHOTO-NOTIFY] Failed: %s", e)
            # Still try to send notification without photo
            await _background_notifications(None)

    asyncio.create_task(_photo_then_notify())

    # Stitch external camera layer timelapse if session was active
    print_status = data.get("status", "completed")

    async def _background_layer_timelapse():
        """Stitch layer timelapse and attach to archive."""
        from backend.app.services.layer_timelapse import cancel_session, on_print_complete as tl_complete

        try:
            if print_status == "completed":
                logger.info("[LAYER-TL] Stitching layer timelapse for printer %s", printer_id)
                timelapse_path = await tl_complete(printer_id)
                if timelapse_path and archive_id:
                    logger.info("[LAYER-TL] Attaching timelapse %s to archive %s", timelapse_path, archive_id)
                    async with async_session() as db:
                        service = ArchiveService(db)
                        timelapse_data = await asyncio.to_thread(timelapse_path.read_bytes)
                        await service.attach_timelapse(archive_id, timelapse_data, "layer_timelapse.mp4")
                        # Clean up the temp file
                        await asyncio.to_thread(timelapse_path.unlink, missing_ok=True)
                        logger.info("[LAYER-TL] Layer timelapse attached successfully")
                elif timelapse_path:
                    # Timelapse created but no archive - just clean up
                    await asyncio.to_thread(timelapse_path.unlink, missing_ok=True)
            else:
                # Print failed or cancelled - cancel timelapse session
                cancel_session(printer_id)
                logger.info(
                    "[LAYER-TL] Cancelled layer timelapse for printer %s (status: %s)", printer_id, print_status
                )
        except Exception as e:
            logger.warning("[LAYER-TL] Failed: %s", e)
            # Try to cancel session on error
            try:
                cancel_session(printer_id)
            except Exception:
                pass  # Best-effort timelapse session cancellation on error

    asyncio.create_task(_background_layer_timelapse())

    # Start bed cooldown monitor (polls bed temp until it drops below threshold)
    async def _background_bed_cooldown():
        """Monitor bed temperature after print and notify when cooled."""
        try:
            from backend.app.api.routes.settings import get_setting

            # Check threshold setting
            async with async_session() as db:
                threshold_str = await get_setting(db, "bed_cooled_threshold")
            threshold = float(threshold_str) if threshold_str else 35.0

            # Check if any provider has on_bed_cooled enabled (early exit if none)
            async with async_session() as db:
                providers = await notification_service._get_providers_for_event(db, "on_bed_cooled", printer_id)
                if not providers:
                    logger.debug("[BED-COOL] No providers enabled for bed_cooled on printer %s", printer_id)
                    return

            logger.info("[BED-COOL] Monitoring bed temp for printer %s (threshold: %.0f°C)", printer_id, threshold)

            max_polls = 120  # 120 * 15s = 30 min timeout
            for _ in range(max_polls):
                await asyncio.sleep(15)

                # Check if printer is still connected
                status = printer_manager.get_status(printer_id)
                if status is None:
                    logger.info("[BED-COOL] Printer %s disconnected, stopping monitor", printer_id)
                    return

                # Check if a new print started (state == RUNNING)
                if hasattr(status, "state") and status.state == "RUNNING":
                    logger.info("[BED-COOL] New print started on printer %s, stopping monitor", printer_id)
                    return

                # Get bed temperature
                bed_temp = None
                if hasattr(status, "temperatures") and status.temperatures:
                    bed_temp = status.temperatures.get("bed")

                if bed_temp is None:
                    continue

                if bed_temp <= threshold:
                    logger.info(
                        "[BED-COOL] Bed cooled to %.1f°C on printer %s (threshold: %.0f°C)",
                        bed_temp,
                        printer_id,
                        threshold,
                    )
                    printer_info = printer_manager.get_printer(printer_id)
                    p_name = printer_info.name if printer_info else "Unknown"
                    async with async_session() as db:
                        await notification_service.on_bed_cooled(
                            printer_id=printer_id,
                            printer_name=p_name,
                            bed_temp=bed_temp,
                            threshold=threshold,
                            filename=filename or subtask_name or "",
                            db=db,
                        )
                    return

            logger.info("[BED-COOL] Timeout waiting for bed to cool on printer %s", printer_id)
        except asyncio.CancelledError:
            logger.info("[BED-COOL] Bed cooldown monitor cancelled for printer %s", printer_id)
        except Exception as e:
            logger.warning("[BED-COOL] Failed: %s", e)
        finally:
            _bed_cooldown_tasks.pop(printer_id, None)

    # Only start bed cooldown for completed prints
    if data.get("status") == "completed":
        # Cancel any existing task for this printer
        existing_task = _bed_cooldown_tasks.pop(printer_id, None)
        if existing_task and not existing_task.done():
            existing_task.cancel()
        task = asyncio.create_task(_background_bed_cooldown())
        _bed_cooldown_tasks[printer_id] = task

    log_timing("All background tasks scheduled")

    # Auto-scan for timelapse if recording was active during the print
    if archive_id and data.get("timelapse_was_active") and data.get("status") == "completed":
        logger.info("[TIMELAPSE] Timelapse was active during print, scheduling auto-scan for archive %s", archive_id)
        # Schedule timelapse scan as background task with retries
        # The printer needs time to encode the video after print completion
        baseline = _timelapse_baselines.pop(printer_id, None)
        asyncio.create_task(_scan_for_timelapse_with_retries(archive_id, baseline))
        log_timing("Timelapse scan scheduled")

    logger.info("[CALLBACK] on_print_complete finished for printer %s, archive %s", printer_id, archive_id)


# AMS sensor history recording
_ams_history_task: asyncio.Task | None = None
AMS_HISTORY_INTERVAL = 300  # Record every 5 minutes
AMS_HISTORY_RETENTION_DAYS = 30  # Keep data for 30 days
_ams_cleanup_counter = 0  # Track recordings to trigger periodic cleanup
_ams_alarm_cooldown: dict[str, datetime] = {}  # Track alarm cooldowns (printer_id:ams_id:type -> last_alarm_time)
AMS_ALARM_COOLDOWN_MINUTES = 60  # Don't send same alarm more than once per hour


async def record_ams_history():
    """Background task to record AMS humidity and temperature data."""
    logger = logging.getLogger(__name__)

    # Wait a short time for MQTT connections to establish on startup
    await asyncio.sleep(10)

    while True:
        try:
            from backend.app.models.ams_history import AMSSensorHistory
            from backend.app.models.printer import Printer
            from backend.app.models.settings import Settings

            async with async_session() as db:
                # Get all active printers
                result = await db.execute(select(Printer).where(Printer.is_active.is_(True)))
                printers = result.scalars().all()

                # Get alarm thresholds from settings
                humidity_threshold = 60.0  # Default: fair threshold
                temp_threshold = 35.0  # Default: fair threshold
                result = await db.execute(select(Settings).where(Settings.key == "ams_humidity_fair"))
                setting = result.scalar_one_or_none()
                if setting:
                    try:
                        humidity_threshold = float(setting.value)
                    except (ValueError, TypeError):
                        pass  # Keep default threshold if stored value is invalid
                result = await db.execute(select(Settings).where(Settings.key == "ams_temp_fair"))
                setting = result.scalar_one_or_none()
                if setting:
                    try:
                        temp_threshold = float(setting.value)
                    except (ValueError, TypeError):
                        pass  # Keep default threshold if stored value is invalid

                recorded_count = 0
                for printer in printers:
                    # Get current state from printer manager
                    state = printer_manager.get_status(printer.id)
                    if not state or not state.connected or not state.raw_data:
                        continue  # Skip disconnected printers - don't use stale data

                    raw_data = state.raw_data
                    if "ams" not in raw_data or not isinstance(raw_data["ams"], list):
                        continue

                    # Record data for each AMS unit
                    for ams_data in raw_data["ams"]:
                        ams_id = int(ams_data.get("id", 0))

                        # Get humidity (prefer humidity_raw)
                        humidity_raw = ams_data.get("humidity_raw")
                        humidity_idx = ams_data.get("humidity")
                        humidity = None
                        if humidity_raw is not None:
                            try:
                                humidity = float(humidity_raw)
                            except (ValueError, TypeError):
                                pass  # Skip unparseable humidity; will try fallback
                        if humidity is None and humidity_idx is not None:
                            try:
                                humidity = float(humidity_idx)
                            except (ValueError, TypeError):
                                pass  # Skip unparseable humidity index value

                        # Get temperature
                        temperature = None
                        temp_str = ams_data.get("temp")
                        if temp_str is not None:
                            try:
                                temperature = float(temp_str)
                            except (ValueError, TypeError):
                                pass  # Skip unparseable temperature value

                        # Skip if no data
                        if humidity is None and temperature is None:
                            continue

                        # Record the data point
                        history = AMSSensorHistory(
                            printer_id=printer.id,
                            ams_id=ams_id,
                            humidity=humidity,
                            humidity_raw=float(humidity_raw) if humidity_raw else None,
                            temperature=temperature,
                        )
                        db.add(history)
                        recorded_count += 1

                        # Generate AMS label and determine if it's AMS-HT (A, B, C, D or HT-A for AMS-Lite/Hub)
                        is_ams_ht = ams_id >= 128
                        if is_ams_ht:
                            ams_label = f"HT-{chr(65 + (ams_id - 128))}"
                        else:
                            ams_label = f"AMS-{chr(65 + ams_id)}"

                        # Check humidity alarm (only if above threshold)
                        if humidity is not None and humidity > humidity_threshold:
                            cooldown_key = f"{printer.id}:{ams_id}:humidity"
                            last_alarm = _ams_alarm_cooldown.get(cooldown_key)
                            now = datetime.now()
                            if (
                                last_alarm is None
                                or (now - last_alarm).total_seconds() >= AMS_ALARM_COOLDOWN_MINUTES * 60
                            ):
                                _ams_alarm_cooldown[cooldown_key] = now
                                logger.info(
                                    f"Sending humidity alarm for {printer.name} {ams_label}: {humidity}% > {humidity_threshold}%"
                                )
                                try:
                                    # Call different notification method based on AMS type
                                    if is_ams_ht:
                                        await notification_service.on_ams_ht_humidity_high(
                                            printer.id, printer.name, ams_label, humidity, humidity_threshold, db
                                        )
                                    else:
                                        await notification_service.on_ams_humidity_high(
                                            printer.id, printer.name, ams_label, humidity, humidity_threshold, db
                                        )
                                except Exception as e:
                                    logger.warning("Failed to send humidity alarm: %s", e)

                        # Check temperature alarm (only if above threshold)
                        if temperature is not None and temperature > temp_threshold:
                            cooldown_key = f"{printer.id}:{ams_id}:temperature"
                            last_alarm = _ams_alarm_cooldown.get(cooldown_key)
                            now = datetime.now()
                            if (
                                last_alarm is None
                                or (now - last_alarm).total_seconds() >= AMS_ALARM_COOLDOWN_MINUTES * 60
                            ):
                                _ams_alarm_cooldown[cooldown_key] = now
                                logger.info(
                                    f"Sending temperature alarm for {printer.name} {ams_label}: {temperature}°C > {temp_threshold}°C"
                                )
                                try:
                                    # Call different notification method based on AMS type
                                    if is_ams_ht:
                                        await notification_service.on_ams_ht_temperature_high(
                                            printer.id, printer.name, ams_label, temperature, temp_threshold, db
                                        )
                                    else:
                                        await notification_service.on_ams_temperature_high(
                                            printer.id, printer.name, ams_label, temperature, temp_threshold, db
                                        )
                                except Exception as e:
                                    logger.warning("Failed to send temperature alarm: %s", e)

                await db.commit()
                if recorded_count > 0:
                    logger.info("Recorded %s AMS sensor history entries", recorded_count)

                # Periodic cleanup of old data (every ~288 recordings = ~24 hours at 5min interval)
                global _ams_cleanup_counter
                _ams_cleanup_counter += 1
                if _ams_cleanup_counter >= 288:
                    _ams_cleanup_counter = 0
                    # Get retention days from settings
                    from backend.app.models.settings import Settings

                    result = await db.execute(select(Settings).where(Settings.key == "ams_history_retention_days"))
                    setting = result.scalar_one_or_none()
                    retention_days = int(setting.value) if setting else AMS_HISTORY_RETENTION_DAYS

                    cutoff = datetime.now() - timedelta(days=retention_days)
                    result = await db.execute(delete(AMSSensorHistory).where(AMSSensorHistory.recorded_at < cutoff))
                    await db.commit()
                    if result.rowcount > 0:
                        logger.info(
                            f"Cleaned up {result.rowcount} old AMS sensor history entries (older than {retention_days} days)"
                        )

            # Wait until next recording interval
            await asyncio.sleep(AMS_HISTORY_INTERVAL)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("AMS history recording failed: %s", e)
            await asyncio.sleep(60)  # Wait a bit before retrying


def start_ams_history_recording():
    """Start the AMS history recording background task."""
    global _ams_history_task
    if _ams_history_task is None:
        _ams_history_task = asyncio.create_task(record_ams_history())
        logging.getLogger(__name__).info("AMS history recording started")


def stop_ams_history_recording():
    """Stop the AMS history recording background task."""
    global _ams_history_task
    if _ams_history_task:
        _ams_history_task.cancel()
        _ams_history_task = None
        logging.getLogger(__name__).info("AMS history recording stopped")


# Printer runtime tracking
_runtime_tracking_task: asyncio.Task | None = None
RUNTIME_TRACKING_INTERVAL = 30  # Update every 30 seconds


async def track_printer_runtime():
    """Background task to track printer active runtime (RUNNING/PAUSE states)."""
    logger = logging.getLogger(__name__)

    # Wait for MQTT connections to establish on startup
    await asyncio.sleep(15)

    while True:
        try:
            from backend.app.models.printer import Printer

            async with async_session() as db:
                # Get all active printers
                result = await db.execute(select(Printer).where(Printer.is_active.is_(True)))
                printers = result.scalars().all()

                now = datetime.now()
                updated_count = 0

                needs_commit = False

                for printer in printers:
                    # Get current state from printer manager
                    state = printer_manager.get_status(printer.id)
                    if not state:
                        logger.debug("[%s] Runtime tracking: no state available", printer.name)
                        continue
                    if not state.connected:
                        logger.debug("[%s] Runtime tracking: not connected", printer.name)
                        continue

                    # Check if printer is in an active state (RUNNING or PAUSE)
                    if state.state in ("RUNNING", "PAUSE"):
                        # Calculate time since last update
                        if printer.last_runtime_update:
                            elapsed = (now - printer.last_runtime_update).total_seconds()
                            if elapsed > 0:
                                printer.runtime_seconds += int(elapsed)
                                updated_count += 1
                                needs_commit = True
                                logger.debug(
                                    f"[{printer.name}] Runtime tracking: added {int(elapsed)}s, "
                                    f"total={printer.runtime_seconds}s ({printer.runtime_seconds / 3600:.2f}h)"
                                )
                        else:
                            # First time seeing printer active - need to commit to save timestamp
                            needs_commit = True
                            logger.debug("[%s] Runtime tracking: first active detection", printer.name)

                        printer.last_runtime_update = now
                    else:
                        # Printer is idle/offline - clear last_runtime_update
                        if printer.last_runtime_update is not None:
                            logger.debug(
                                f"[{printer.name}] Runtime tracking: state={state.state}, clearing last_runtime_update"
                            )
                            printer.last_runtime_update = None
                            needs_commit = True

                if needs_commit:
                    await db.commit()
                    if updated_count > 0:
                        logger.debug("Updated runtime for %s printer(s)", updated_count)

        except asyncio.CancelledError:
            logger.info("Runtime tracking cancelled")
            break
        except Exception as e:
            logger.warning("Runtime tracking failed: %s", e)

        await asyncio.sleep(RUNTIME_TRACKING_INTERVAL)


def start_runtime_tracking():
    """Start the printer runtime tracking background task."""
    global _runtime_tracking_task
    if _runtime_tracking_task is None:
        _runtime_tracking_task = asyncio.create_task(track_printer_runtime())
        logging.getLogger(__name__).info("Printer runtime tracking started")


def stop_runtime_tracking():
    """Stop the printer runtime tracking background task."""
    global _runtime_tracking_task
    if _runtime_tracking_task:
        _runtime_tracking_task.cancel()
        _runtime_tracking_task = None
        logging.getLogger(__name__).info("Printer runtime tracking stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()

    # Restore debug logging state from previous session
    await init_debug_logging()

    # Set up printer manager callbacks
    loop = asyncio.get_event_loop()
    printer_manager.set_event_loop(loop)
    printer_manager.set_status_change_callback(on_printer_status_change)
    printer_manager.set_print_start_callback(on_print_start)
    printer_manager.set_print_complete_callback(on_print_complete)
    printer_manager.set_ams_change_callback(on_ams_change)

    # Layer change callback for external camera timelapse
    async def on_layer_change(printer_id: int, layer_num: int):
        """Capture timelapse frame on layer change."""
        from backend.app.services.layer_timelapse import on_layer_change as tl_layer_change

        await tl_layer_change(printer_id, layer_num)

    printer_manager.set_layer_change_callback(on_layer_change)

    # Initialize MQTT relay from settings
    async with async_session() as db:
        from backend.app.api.routes.settings import get_setting

        mqtt_settings = {
            "mqtt_enabled": (await get_setting(db, "mqtt_enabled") or "false") == "true",
            "mqtt_broker": await get_setting(db, "mqtt_broker") or "",
            "mqtt_port": int(await get_setting(db, "mqtt_port") or "1883"),
            "mqtt_username": await get_setting(db, "mqtt_username") or "",
            "mqtt_password": await get_setting(db, "mqtt_password") or "",
            "mqtt_topic_prefix": await get_setting(db, "mqtt_topic_prefix") or "bambuddy",
            "mqtt_use_tls": (await get_setting(db, "mqtt_use_tls") or "false") == "true",
        }
        await mqtt_relay.configure(mqtt_settings)

        # Restore MQTT smart plug subscriptions
        if mqtt_settings.get("mqtt_enabled"):
            from backend.app.models.smart_plug import SmartPlug

            result = await db.execute(select(SmartPlug).where(SmartPlug.plug_type == "mqtt"))
            mqtt_plugs = result.scalars().all()
            for plug in mqtt_plugs:
                if plug.mqtt_topic:
                    mqtt_relay.smart_plug_service.subscribe(
                        plug_id=plug.id,
                        topic=plug.mqtt_topic,
                        power_path=plug.mqtt_power_path,
                        energy_path=plug.mqtt_energy_path,
                        state_path=plug.mqtt_state_path,
                        multiplier=plug.mqtt_multiplier or 1.0,
                    )
            if mqtt_plugs:
                logging.info("Restored %s MQTT smart plug subscriptions", len(mqtt_plugs))

    # Connect to all active printers
    async with async_session() as db:
        await init_printer_connections(db)

    # Auto-connect to Spoolman if enabled
    async with async_session() as db:
        from backend.app.api.routes.settings import get_setting

        spoolman_enabled = await get_setting(db, "spoolman_enabled")
        spoolman_url = await get_setting(db, "spoolman_url")

        if spoolman_enabled and spoolman_enabled.lower() == "true" and spoolman_url:
            try:
                client = await init_spoolman_client(spoolman_url)
                if await client.health_check():
                    logging.info("Auto-connected to Spoolman at %s", spoolman_url)
                    # Ensure the 'tag' extra field exists for RFID/UUID storage
                    await client.ensure_tag_extra_field()
                else:
                    logging.warning("Spoolman at %s is not reachable", spoolman_url)
            except Exception as e:
                logging.warning("Failed to auto-connect to Spoolman: %s", e)

    # Start the print scheduler
    asyncio.create_task(print_scheduler.run())

    # Start the smart plug scheduler for time-based on/off
    smart_plug_manager.start_scheduler()

    # Resume any pending auto-offs that were interrupted by restart
    await smart_plug_manager.resume_pending_auto_offs()

    # Start the notification digest scheduler
    notification_service.start_digest_scheduler()

    # Start the GitHub backup scheduler
    await github_backup_service.start_scheduler()

    # Start AMS history recording
    start_ams_history_recording()

    # Start printer runtime tracking
    start_runtime_tracking()

    # Initialize virtual printer manager
    from backend.app.services.virtual_printer import virtual_printer_manager

    virtual_printer_manager.set_session_factory(async_session)

    # Auto-start virtual printer if enabled
    async with async_session() as db:
        from backend.app.api.routes.settings import get_setting

        vp_enabled = await get_setting(db, "virtual_printer_enabled")
        if vp_enabled and vp_enabled.lower() == "true":
            vp_access_code = await get_setting(db, "virtual_printer_access_code") or ""
            vp_mode = await get_setting(db, "virtual_printer_mode") or "immediate"
            vp_model = await get_setting(db, "virtual_printer_model") or ""
            vp_target_printer_id = await get_setting(db, "virtual_printer_target_printer_id")
            vp_remote_iface = await get_setting(db, "virtual_printer_remote_interface_ip") or ""

            # Look up printer IP and serial if in proxy mode
            vp_target_ip = ""
            vp_target_serial = ""
            if vp_mode == "proxy" and vp_target_printer_id:
                from backend.app.models.printer import Printer

                result = await db.execute(select(Printer).where(Printer.id == int(vp_target_printer_id)))
                printer = result.scalar_one_or_none()
                if printer:
                    vp_target_ip = printer.ip_address
                    vp_target_serial = printer.serial_number

            # Proxy mode requires target IP, other modes require access code
            can_start = (vp_mode == "proxy" and vp_target_ip) or (vp_mode != "proxy" and vp_access_code)

            if can_start:
                try:
                    await virtual_printer_manager.configure(
                        enabled=True,
                        access_code=vp_access_code,
                        mode=vp_mode,
                        model=vp_model,
                        target_printer_ip=vp_target_ip,
                        target_printer_serial=vp_target_serial,
                        remote_interface_ip=vp_remote_iface,
                    )
                    if vp_mode == "proxy":
                        logging.info("Virtual printer proxy started (target=%s)", vp_target_ip)
                    else:
                        logging.info("Virtual printer started (model=%s)", vp_model or "default")
                except Exception as e:
                    logging.warning("Failed to start virtual printer: %s", e)

    yield

    # Shutdown
    print_scheduler.stop()
    smart_plug_manager.stop_scheduler()
    notification_service.stop_digest_scheduler()
    github_backup_service.stop_scheduler()
    stop_ams_history_recording()
    stop_runtime_tracking()
    printer_manager.disconnect_all()
    await close_spoolman_client()

    # Stop virtual printer if running
    if virtual_printer_manager.is_enabled:
        await virtual_printer_manager.configure(enabled=False)

    await mqtt_smart_plug_service.disconnect(timeout=2)

    await mqtt_relay.disconnect(timeout=2)


app = FastAPI(
    title=app_settings.app_name,
    description="Archive and manage Bambu Lab 3MF files",
    version=APP_VERSION,
    lifespan=lifespan,
)


# =============================================================================
# Authentication Middleware - Secures ALL API routes by default
# =============================================================================
# Public routes that don't require authentication even when auth is enabled
PUBLIC_API_ROUTES = {
    # Auth routes needed before/during login
    "/api/v1/auth/status",
    "/api/v1/auth/login",
    "/api/v1/auth/setup",  # Needed for initial setup and recovery
    "/api/v1/auth/advanced-auth/status",  # Advanced auth status needed for login page
    "/api/v1/auth/forgot-password",  # Password reset for advanced auth
    # Version check for updates (no sensitive data)
    "/api/v1/updates/version",
    # Metrics endpoint handles its own prometheus_token authentication
    "/api/v1/metrics",
}

# Route prefixes that are public (for routes with dynamic segments)
PUBLIC_API_PREFIXES = [
    # WebSocket connections handle their own auth
    "/api/v1/ws",
]

# Route patterns that are public (read-only display data)
# These are checked with "in path" - needed because browsers load images/videos
# via <img src> and <video src> which don't include Authorization headers
PUBLIC_API_PATTERNS = [
    # Thumbnails
    "/thumbnail",  # /archives/{id}/thumbnail, /library/files/{id}/thumbnail
    "/plate-thumbnail/",  # /archives/{id}/plate-thumbnail/{plate_id}
    # Images and media
    "/photos/",  # /archives/{id}/photos/{filename}
    "/project-image/",  # /archives/{id}/project-image/{path}
    "/qrcode",  # /archives/{id}/qrcode
    "/timelapse",  # /archives/{id}/timelapse (video)
    "/cover",  # /printers/{id}/cover
    "/icon",  # /external-links/{id}/icon
    # Camera (streams loaded via <img> tag)
    "/camera/stream",  # /printers/{id}/camera/stream
    "/camera/snapshot",  # /printers/{id}/camera/snapshot
]


@app.middleware("http")
async def auth_middleware(request, call_next):
    """Enforce authentication on all API routes when auth is enabled.

    This middleware provides defense-in-depth by checking auth at the API gateway level,
    regardless of whether individual routes have auth dependencies.
    """
    from starlette.responses import JSONResponse

    path = request.url.path

    # Only apply to API routes
    if not path.startswith("/api/"):
        return await call_next(request)

    # Allow public routes
    if path in PUBLIC_API_ROUTES:
        return await call_next(request)

    # Allow public prefixes
    for prefix in PUBLIC_API_PREFIXES:
        if path.startswith(prefix):
            return await call_next(request)

    # Allow public patterns (read-only display data like thumbnails)
    for pattern in PUBLIC_API_PATTERNS:
        if pattern in path:
            return await call_next(request)

    # Check if auth is enabled
    try:
        async with async_session() as db:
            from backend.app.core.auth import is_auth_enabled

            auth_enabled = await is_auth_enabled(db)

        if not auth_enabled:
            # Auth disabled, allow all requests
            return await call_next(request)
    except Exception:
        # If we can't check auth status, allow request (fail open for DB issues)
        return await call_next(request)

    # Auth is enabled - require valid token
    auth_header = request.headers.get("Authorization")
    x_api_key = request.headers.get("X-API-Key")

    # Check for API key auth first
    if x_api_key or (auth_header and auth_header.startswith("Bearer bb_")):
        # API key authentication - let the request through to be validated by route handler
        # API keys are validated per-route since they have different permission levels
        return await call_next(request)

    # Check for JWT auth
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate JWT token
    import jwt

    try:
        from backend.app.core.auth import ALGORITHM, SECRET_KEY

        token = auth_header.replace("Bearer ", "")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise ValueError("No username in token")

        # Verify user exists and is active
        async with async_session() as db:
            from backend.app.core.auth import get_user_by_username

            user = await get_user_by_username(db, username)
            if not user or not user.is_active:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "User not found or inactive"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
    except jwt.ExpiredSignatureError:
        return JSONResponse(
            status_code=401,
            content={"detail": "Token has expired"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (jwt.InvalidTokenError, ValueError, Exception):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await call_next(request)


# API routes
app.include_router(auth.router, prefix=app_settings.api_prefix)
app.include_router(users.router, prefix=app_settings.api_prefix)
app.include_router(groups.router, prefix=app_settings.api_prefix)
app.include_router(printers.router, prefix=app_settings.api_prefix)
app.include_router(archives.router, prefix=app_settings.api_prefix)
app.include_router(filaments.router, prefix=app_settings.api_prefix)
app.include_router(inventory.router, prefix=app_settings.api_prefix)
app.include_router(settings_routes.router, prefix=app_settings.api_prefix)
app.include_router(cloud.router, prefix=app_settings.api_prefix)
app.include_router(local_presets.router, prefix=app_settings.api_prefix)
app.include_router(smart_plugs.router, prefix=app_settings.api_prefix)
app.include_router(print_log.router, prefix=app_settings.api_prefix)
app.include_router(print_queue.router, prefix=app_settings.api_prefix)
app.include_router(kprofiles.router, prefix=app_settings.api_prefix)
app.include_router(notifications.router, prefix=app_settings.api_prefix)
app.include_router(notification_templates.router, prefix=app_settings.api_prefix)
app.include_router(spoolman.router, prefix=app_settings.api_prefix)
app.include_router(updates.router, prefix=app_settings.api_prefix)
app.include_router(maintenance.router, prefix=app_settings.api_prefix)
app.include_router(camera.router, prefix=app_settings.api_prefix)
app.include_router(external_links.router, prefix=app_settings.api_prefix)
app.include_router(projects.router, prefix=app_settings.api_prefix)
app.include_router(library.router, prefix=app_settings.api_prefix)
app.include_router(api_keys.router, prefix=app_settings.api_prefix)
app.include_router(webhook.router, prefix=app_settings.api_prefix)
app.include_router(ams_history.router, prefix=app_settings.api_prefix)
app.include_router(system.router, prefix=app_settings.api_prefix)
app.include_router(support.router, prefix=app_settings.api_prefix)
app.include_router(websocket.router, prefix=app_settings.api_prefix)
app.include_router(discovery.router, prefix=app_settings.api_prefix)
app.include_router(pending_uploads.router, prefix=app_settings.api_prefix)
app.include_router(firmware.router, prefix=app_settings.api_prefix)
app.include_router(github_backup.router, prefix=app_settings.api_prefix)
app.include_router(metrics.router, prefix=app_settings.api_prefix)


# Serve static files (React build)
if app_settings.static_dir.exists() and any(app_settings.static_dir.iterdir()):
    app.mount(
        "/assets",
        StaticFiles(directory=app_settings.static_dir / "assets"),
        name="assets",
    )
    if (app_settings.static_dir / "img").exists():
        app.mount(
            "/img",
            StaticFiles(directory=app_settings.static_dir / "img"),
            name="img",
        )
    if (app_settings.static_dir / "icons").exists():
        app.mount(
            "/icons",
            StaticFiles(directory=app_settings.static_dir / "icons"),
            name="icons",
        )


@app.get("/")
async def serve_frontend():
    """Serve the React frontend."""
    index_file = app_settings.static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "message": "Bambuddy API",
        "docs": "/docs",
        "frontend": "Build and place React app in /static directory",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/manifest.json")
async def serve_manifest():
    """Serve PWA manifest."""
    manifest_file = app_settings.static_dir / "manifest.json"
    if manifest_file.exists():
        return FileResponse(manifest_file, media_type="application/manifest+json")
    return {"error": "Manifest not found"}


@app.get("/sw.js")
async def serve_service_worker():
    """Serve service worker."""
    sw_file = app_settings.static_dir / "sw.js"
    if sw_file.exists():
        return FileResponse(sw_file, media_type="application/javascript")
    return {"error": "Service worker not found"}


# Catch-all route for React Router (must be last)
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serve React app for client-side routing."""
    # Don't intercept API routes - raise proper 404 so FastAPI can handle redirects
    if full_path.startswith("api/"):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not found")

    index_file = app_settings.static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)

    return {"error": "Frontend not built"}
