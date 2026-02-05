import asyncio
import json
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

    server = HTTPServer(("0.0.0.0", port), ErrorHandler)

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
    logging.info(f"Logging to file: {log_file}")

# Reduce noise from third-party libraries in production
if not app_settings.debug:
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

logging.info(f"Bambuddy starting - debug={app_settings.debug}, log_level={log_level_str}")
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
    kprofiles,
    library,
    maintenance,
    metrics,
    notification_templates,
    notifications,
    pending_uploads,
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
from backend.app.services.notification_service import notification_service
from backend.app.services.print_scheduler import scheduler as print_scheduler
from backend.app.services.printer_manager import (
    init_printer_connections,
    printer_manager,
    printer_state_to_dict,
)
from backend.app.services.smart_plug_manager import smart_plug_manager
from backend.app.services.spoolman import close_spoolman_client, get_spoolman_client, init_spoolman_client
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

# Track progress milestones for notifications: {printer_id: last_milestone_notified}
# Milestones are 25, 50, 75. Value of 0 means no milestone notified yet for current print.
_last_progress_milestone: dict[int, int] = {}

# Track HMS errors that have been notified: {printer_id: set of error codes}
# This prevents sending duplicate notifications for the same error
_notified_hms_errors: dict[int, set[str]] = {}


async def _get_plug_energy(plug, db) -> dict | None:
    """Get energy from plug regardless of type (Tasmota, Home Assistant, or MQTT).

    For HA plugs, configures the service with current settings from DB.
    For MQTT plugs, returns data from the subscription service.
    """
    if plug.plug_type == "homeassistant":
        from backend.app.api.routes.settings import get_setting

        ha_url = await get_setting(db, "ha_url") or ""
        ha_token = await get_setting(db, "ha_token") or ""
        homeassistant_service.configure(ha_url, ha_token)
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


def register_expected_print(printer_id: int, filename: str, archive_id: int):
    """Register an expected print from reprint/scheduled so we don't create duplicate archives."""
    # Store with multiple filename variations to catch different naming patterns
    _expected_prints[(printer_id, filename)] = archive_id
    # Also store without .3mf extension if present
    if filename.endswith(".3mf"):
        base = filename[:-4]
        _expected_prints[(printer_id, base)] = archive_id
        _expected_prints[(printer_id, f"{base}.gcode")] = archive_id
    logging.getLogger(__name__).info(
        f"Registered expected print: printer={printer_id}, file={filename}, archive={archive_id}"
    )


_last_status_broadcast: dict[int, str] = {}
_nozzle_count_updated: set[int] = set()  # Track printers where we've updated nozzle_count


def _build_ams_tray_lookup(raw_data: dict) -> dict[int, dict]:
    """Build lookup of global_tray_id -> tray info from printer state.

    Returns: {0: {"tray_uuid": "...", "tag_uid": "..."}, 1: {...}, ...}
    """
    lookup = {}
    ams_data = raw_data.get("ams", [])
    for ams_unit in ams_data:
        ams_id = ams_unit.get("id", 0)
        for tray in ams_unit.get("tray", []):
            tray_id = tray.get("id", 0)
            global_tray_id = ams_id * 4 + tray_id
            lookup[global_tray_id] = {
                "tray_uuid": tray.get("tray_uuid", ""),
                "tag_uid": tray.get("tag_uid", ""),
                "tray_type": tray.get("tray_type", ""),
            }

    # External spool (global_tray_id = 254)
    vt_tray = raw_data.get("vt_tray")
    if vt_tray and vt_tray.get("tray_type"):
        lookup[254] = {
            "tray_uuid": vt_tray.get("tray_uuid", ""),
            "tag_uid": vt_tray.get("tag_uid", ""),
            "tray_type": vt_tray.get("tray_type", ""),
        }

    return lookup


async def _store_spoolman_print_data(printer_id: int, archive_id: int, file_path: str, db, logger):
    """Store Spoolman tracking data at print start (persisted to database)."""
    from backend.app.api.routes.settings import get_setting
    from backend.app.models.active_print_spoolman import ActivePrintSpoolman
    from backend.app.utils.threemf_tools import (
        extract_filament_properties_from_3mf,
        extract_filament_usage_from_3mf,
        extract_layer_filament_usage_from_3mf,
    )

    # Check if Spoolman is enabled
    spoolman_enabled = await get_setting(db, "spoolman_enabled")
    if not spoolman_enabled or spoolman_enabled.lower() != "true":
        return

    # Only store tracking data if "Disable AMS Weight Sync" is enabled
    # (otherwise we're using AMS percentage-based estimates, not per-usage tracking)
    disable_weight_sync_str = await get_setting(db, "spoolman_disable_weight_sync")
    disable_weight_sync = disable_weight_sync_str and disable_weight_sync_str.lower() == "true"
    if not disable_weight_sync:
        logger.debug("[SPOOLMAN] Weight sync enabled, skipping per-usage tracking data storage")
        return

    # Get 3MF file path
    full_path = app_settings.base_dir / file_path
    if not full_path.exists():
        logger.debug(f"[SPOOLMAN] 3MF file not found: {full_path}")
        return

    # Extract per-filament usage from 3MF (total usage per slot)
    filament_usage = extract_filament_usage_from_3mf(full_path)
    if not filament_usage:
        logger.debug(f"[SPOOLMAN] No filament usage data in 3MF for archive {archive_id}")
        return

    # Get current AMS tray state
    state = printer_manager.get_status(printer_id)
    ams_trays = {}
    if state and state.raw_data:
        ams_trays = _build_ams_tray_lookup(state.raw_data)

    # Get custom slot-to-tray mapping from queue item (if this is a queued print)
    slot_to_tray = None
    from backend.app.models.print_queue import PrintQueueItem

    queue_result = await db.execute(
        select(PrintQueueItem).where(PrintQueueItem.archive_id == archive_id).where(PrintQueueItem.status == "printing")
    )
    queue_item = queue_result.scalar_one_or_none()
    if queue_item and queue_item.ams_mapping:
        try:
            slot_to_tray = json.loads(queue_item.ams_mapping)
        except json.JSONDecodeError:
            pass

    # Parse G-code for per-layer filament usage (for accurate partial usage tracking)
    layer_usage = extract_layer_filament_usage_from_3mf(full_path)
    layer_usage_json = None
    if layer_usage:
        # Convert int keys to string for JSON serialization
        layer_usage_json = {str(k): v for k, v in layer_usage.items()}
        logger.debug(f"[SPOOLMAN] Parsed {len(layer_usage)} layers from G-code")

    # Extract filament properties (density, diameter) for mm -> grams conversion
    filament_properties = extract_filament_properties_from_3mf(full_path)

    # Delete any existing row for this printer/archive (shouldn't exist, but just in case)
    from sqlalchemy import delete

    await db.execute(
        delete(ActivePrintSpoolman)
        .where(ActivePrintSpoolman.printer_id == printer_id)
        .where(ActivePrintSpoolman.archive_id == archive_id)
    )

    # Insert new tracking data
    tracking = ActivePrintSpoolman(
        printer_id=printer_id,
        archive_id=archive_id,
        filament_usage=filament_usage,
        ams_trays=ams_trays,
        slot_to_tray=slot_to_tray,
        layer_usage=layer_usage_json,
        filament_properties=filament_properties,
    )
    db.add(tracking)
    await db.commit()

    logger.info(f"[SPOOLMAN] Stored tracking data for print: printer={printer_id}, archive={archive_id}")
    logger.debug(f"[SPOOLMAN] Filament usage: {filament_usage}")
    logger.debug(f"[SPOOLMAN] AMS trays: {list(ams_trays.keys())}")
    if slot_to_tray:
        logger.debug(f"[SPOOLMAN] Custom slot mapping: {slot_to_tray}")
    if layer_usage_json:
        logger.debug("[SPOOLMAN] Layer usage data available for partial tracking")


async def _cleanup_spoolman_tracking(printer_id: int, archive_id: int, db, logger):
    """Report partial usage and clean up Spoolman tracking data for failed/aborted prints."""
    from sqlalchemy import delete, select

    from backend.app.models.active_print_spoolman import ActivePrintSpoolman

    # Get tracking data first (needed for partial usage reporting)
    result = await db.execute(
        select(ActivePrintSpoolman)
        .where(ActivePrintSpoolman.printer_id == printer_id)
        .where(ActivePrintSpoolman.archive_id == archive_id)
    )
    tracking = result.scalar_one_or_none()

    if not tracking:
        logger.debug(f"[SPOOLMAN] No tracking data to clean up for printer={printer_id}, archive={archive_id}")
        return

    # Try to report partial usage before cleanup
    try:
        await _report_partial_spoolman_usage(printer_id, tracking, logger)
    except Exception as e:
        logger.warning(f"[SPOOLMAN] Partial usage report failed: {e}")

    # Delete tracking data
    await db.execute(
        delete(ActivePrintSpoolman)
        .where(ActivePrintSpoolman.printer_id == printer_id)
        .where(ActivePrintSpoolman.archive_id == archive_id)
    )
    await db.commit()
    logger.debug(f"[SPOOLMAN] Cleaned up tracking data for printer={printer_id}, archive={archive_id}")


async def _report_partial_spoolman_usage(printer_id: int, tracking, logger):
    """Report partial filament usage based on actual G-code layer data.

    Uses per-layer cumulative extrusion from G-code parsing for accurate
    multi-material tracking. Falls back to linear interpolation if G-code
    data is unavailable.

    This is called when a print fails or is cancelled.
    """
    from backend.app.api.routes.settings import get_setting
    from backend.app.utils.threemf_tools import get_cumulative_usage_at_layer, mm_to_grams

    async with async_session() as db:
        # Check if partial usage reporting is enabled (default: true)
        report_partial = await get_setting(db, "spoolman_report_partial_usage")
        if report_partial and report_partial.lower() == "false":
            logger.debug("[SPOOLMAN] Partial usage reporting disabled by setting")
            return

        # Check if Spoolman is enabled
        spoolman_enabled = await get_setting(db, "spoolman_enabled")
        if not spoolman_enabled or spoolman_enabled.lower() != "true":
            return

    # Get current printer state for layer progress
    state = printer_manager.get_status(printer_id)
    if not state:
        logger.debug("[SPOOLMAN] No printer state available for partial usage")
        return

    current_layer = state.layer_num
    total_layers = state.total_layers

    # Need current layer to calculate usage
    if not current_layer or current_layer <= 0:
        logger.debug("[SPOOLMAN] No progress to report (layer 0 or unknown)")
        return

    logger.info(f"[SPOOLMAN] Reporting partial usage at layer {current_layer}/{total_layers or '?'}")

    # Get tracking data
    layer_usage = tracking.layer_usage
    filament_properties = tracking.filament_properties or {}
    filament_usage = tracking.filament_usage or []
    ams_trays = tracking.ams_trays or {}
    slot_to_tray = tracking.slot_to_tray

    # Convert ams_trays keys from string to int (JSON serialization)
    ams_trays = {int(k): v for k, v in ams_trays.items()}

    async with async_session() as db:
        from backend.app.api.routes.settings import get_setting

        # Get Spoolman client
        client = await get_spoolman_client()
        if not client:
            spoolman_url = await get_setting(db, "spoolman_url")
            if spoolman_url:
                client = await init_spoolman_client(spoolman_url)

        if not client or not await client.health_check():
            logger.warning("[SPOOLMAN] Not reachable for partial usage reporting")
            return

        spools_updated = 0

        # Try to use accurate G-code parsed data
        if layer_usage:
            # Convert string keys back to int (JSON serialization issue)
            # Both outer (layer) and inner (filament_id) keys need conversion
            layer_usage_int = {
                int(layer): {int(fid): mm for fid, mm in filaments.items()} for layer, filaments in layer_usage.items()
            }

            # Get cumulative usage at current layer
            usage_mm = get_cumulative_usage_at_layer(layer_usage_int, current_layer)

            if usage_mm:
                logger.info(f"[SPOOLMAN] Using G-code parsed data for layer {current_layer}")

                # Process each filament's usage
                for filament_id, mm_used in usage_mm.items():
                    # filament_id is 0-based from G-code, slot_id is 1-based
                    slot_id = filament_id + 1

                    # Determine which tray was used for this slot
                    global_tray_id = slot_id - 1  # Default: slot 1 -> tray 0
                    if slot_to_tray and slot_id <= len(slot_to_tray):
                        mapped_tray = slot_to_tray[slot_id - 1]
                        if mapped_tray >= 0:
                            global_tray_id = mapped_tray

                    tray_info = ams_trays.get(global_tray_id)
                    if not tray_info:
                        logger.debug(f"[SPOOLMAN] Slot {slot_id}: no tray at global_tray_id {global_tray_id}")
                        continue

                    # Get spool identifier (prefer tray_uuid over tag_uid)
                    tray_uuid = tray_info.get("tray_uuid", "")
                    tag_uid = tray_info.get("tag_uid", "")
                    spool_tag = tray_uuid if tray_uuid and tray_uuid != "00000000000000000000000000000000" else tag_uid

                    if not spool_tag:
                        logger.debug(f"[SPOOLMAN] Slot {slot_id}: no identifier for tray {global_tray_id}")
                        continue

                    # Find the spool in Spoolman
                    spool = await client.find_spool_by_tag(spool_tag)
                    if not spool:
                        logger.debug(f"[SPOOLMAN] Slot {slot_id}: no spool for tag {spool_tag[:16]}...")
                        continue

                    # Get density from Spoolman's filament data (most accurate)
                    # Falls back to 3MF properties, then to default PLA density
                    filament_data = spool.get("filament", {})
                    density = filament_data.get("density")
                    diameter = filament_data.get("diameter", 1.75)

                    if not density:
                        # Fallback to 3MF properties
                        props = filament_properties.get(str(slot_id), filament_properties.get(slot_id, {}))
                        density = props.get("density", 1.24)
                        logger.debug(f"[SPOOLMAN] Using fallback density {density} for slot {slot_id}")

                    # Convert mm to grams using Spoolman's filament density
                    grams_used = round(mm_to_grams(mm_used, diameter, density), 2)

                    if grams_used <= 0:
                        continue

                    # Report usage to Spoolman
                    result = await client.use_spool(spool["id"], grams_used)
                    if result:
                        logger.info(
                            f"[SPOOLMAN] Partial (G-code): slot {slot_id}: {grams_used}g ({mm_used:.1f}mm, d={density}) -> spool {spool['id']}"
                        )
                        spools_updated += 1

                if spools_updated > 0:
                    logger.info(f"[SPOOLMAN] Reported partial usage to {spools_updated} spool(s) using G-code data")
                return

        # Fallback: linear interpolation (if no G-code data available)
        if not total_layers or total_layers <= 0:
            logger.debug(f"[SPOOLMAN] Cannot use linear fallback: total_layers={total_layers}")
            return

        progress_ratio = min(current_layer / total_layers, 1.0)
        logger.info(f"[SPOOLMAN] Falling back to linear interpolation ({progress_ratio:.1%})")

        for usage in filament_usage:
            slot_id = usage.get("slot_id", 0)
            total_used_g = usage.get("used_g", 0)

            if total_used_g <= 0:
                continue

            # Calculate partial usage using linear interpolation
            partial_used_g = round(total_used_g * progress_ratio, 2)

            if partial_used_g <= 0:
                continue

            # Determine which tray was used for this slot
            global_tray_id = slot_id - 1
            if slot_to_tray and slot_id <= len(slot_to_tray):
                mapped_tray = slot_to_tray[slot_id - 1]
                if mapped_tray >= 0:
                    global_tray_id = mapped_tray

            tray_info = ams_trays.get(global_tray_id)
            if not tray_info:
                continue

            # Get spool identifier
            tray_uuid = tray_info.get("tray_uuid", "")
            tag_uid = tray_info.get("tag_uid", "")
            spool_tag = tray_uuid if tray_uuid and tray_uuid != "00000000000000000000000000000000" else tag_uid

            if not spool_tag:
                continue

            # Find and update spool
            spool = await client.find_spool_by_tag(spool_tag)
            if spool:
                result = await client.use_spool(spool["id"], partial_used_g)
                if result:
                    logger.info(
                        f"[SPOOLMAN] Partial (linear): slot {slot_id}: {partial_used_g}g/{total_used_g}g -> spool {spool['id']}"
                    )
                    spools_updated += 1

        if spools_updated > 0:
            logger.info(f"[SPOOLMAN] Reported partial usage to {spools_updated} spool(s) using linear interpolation")


async def _report_spoolman_usage(printer_id: int, archive_id: int, logger):
    """Report filament usage to Spoolman after print completion.

    Uses per-filament usage data captured at print start to report
    usage to the correct spools.
    """
    async with async_session() as db:
        from backend.app.api.routes.settings import get_setting
        from backend.app.models.active_print_spoolman import ActivePrintSpoolman

        # Get tracking data stored at print start
        result = await db.execute(
            select(ActivePrintSpoolman)
            .where(ActivePrintSpoolman.printer_id == printer_id)
            .where(ActivePrintSpoolman.archive_id == archive_id)
        )
        tracking = result.scalar_one_or_none()

        if not tracking:
            logger.info(f"[SPOOLMAN] No tracking data for print (printer={printer_id}, archive={archive_id})")
            return

        filament_usage = tracking.filament_usage or []
        ams_trays = tracking.ams_trays or {}
        slot_to_tray = tracking.slot_to_tray

        # Delete tracking row (we're done with it)
        await db.delete(tracking)
        await db.commit()

        if not filament_usage:
            logger.debug(f"[SPOOLMAN] No filament usage data for archive {archive_id}")
            return

        # Check if Spoolman is enabled
        spoolman_enabled = await get_setting(db, "spoolman_enabled")
        if not spoolman_enabled or spoolman_enabled.lower() != "true":
            return

        # Get Spoolman client
        client = await get_spoolman_client()
        if not client:
            spoolman_url = await get_setting(db, "spoolman_url")
            if spoolman_url:
                client = await init_spoolman_client(spoolman_url)

        if not client or not await client.health_check():
            logger.warning("[SPOOLMAN] Not reachable for usage reporting")
            return

        logger.info(f"[SPOOLMAN] Reporting per-filament usage for archive {archive_id}")

        # Convert ams_trays keys from string to int (JSON serialization converts int keys to strings)
        ams_trays = {int(k): v for k, v in ams_trays.items()}

        spools_updated = 0
        for usage in filament_usage:
            slot_id = usage.get("slot_id", 0)
            used_g = usage.get("used_g", 0)

            if used_g <= 0:
                continue

            # Determine which tray was used for this slot
            # Default: slot_id 1 -> global_tray_id 0, etc.
            global_tray_id = slot_id - 1

            # Apply custom mapping if provided
            if slot_to_tray and slot_id <= len(slot_to_tray):
                mapped_tray = slot_to_tray[slot_id - 1]
                if mapped_tray >= 0:  # -1 = unmapped
                    global_tray_id = mapped_tray

            tray_info = ams_trays.get(global_tray_id)
            if not tray_info:
                logger.debug(f"[SPOOLMAN] Slot {slot_id}: no tray at global_tray_id {global_tray_id}")
                continue

            # Get spool identifier (prefer tray_uuid over tag_uid)
            tray_uuid = tray_info.get("tray_uuid", "")
            tag_uid = tray_info.get("tag_uid", "")
            spool_tag = tray_uuid if tray_uuid and tray_uuid != "00000000000000000000000000000000" else tag_uid

            if not spool_tag:
                logger.debug(f"[SPOOLMAN] Slot {slot_id}: no identifier for tray {global_tray_id}")
                continue

            # Find and update spool
            spool = await client.find_spool_by_tag(spool_tag)
            if spool:
                spool_result = await client.use_spool(spool["id"], used_g)
                if spool_result:
                    logger.info(f"[SPOOLMAN] Slot {slot_id}: {used_g}g -> spool {spool['id']} (tray {global_tray_id})")
                    spools_updated += 1
            else:
                logger.debug(f"[SPOOLMAN] Slot {slot_id}: no spool for tag {spool_tag[:16]}...")

        if spools_updated == 0:
            logger.info(f"[SPOOLMAN] Archive {archive_id}: no spools updated")
        else:
            logger.info(f"[SPOOLMAN] Archive {archive_id}: updated {spools_updated} spool(s)")


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

    status_key = (
        f"{state.connected}:{state.state}:{state.progress}:{state.layer_num}:"
        f"{nozzle_temp}:{bed_temp}:{nozzle_2_temp}:{chamber_temp}:"
        f"{state.stg_cur}:{bed_target}:{nozzle_target}:"
        f"{state.cooling_fan_speed}:{state.big_fan1_speed}:{state.big_fan2_speed}:"
        f"{state.chamber_light}:{state.active_extruder}"
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

            # Update tracking with all current errors
            _notified_hms_errors[printer_id] = current_error_codes
    else:
        # No HMS errors - clear tracking so future errors get notified
        if printer_id in _notified_hms_errors:
            _notified_hms_errors.pop(printer_id, None)

    await ws_manager.send_printer_status(
        printer_id,
        printer_state_to_dict(state, printer_id, printer_manager.get_model(printer_id)),
    )


async def on_ams_change(printer_id: int, ams_data: list):
    """Handle AMS data changes - sync to Spoolman if enabled and auto mode."""
    import logging

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
            logger.info(f"[Printer {printer_id}] Broadcasting AMS change via WebSocket")
            await ws_manager.send_printer_status(
                printer_id,
                printer_state_to_dict(state, printer_id, printer_manager.get_model(printer_id)),
            )
    except Exception as e:
        logger.warning(f"Failed to broadcast AMS change for printer {printer_id}: {e}")

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
                logger.warning(f"Spoolman not reachable at {spoolman_url}")
                return

            # Get printer name for location
            result = await db.execute(select(Printer).where(Printer.id == printer_id))
            printer = result.scalar_one_or_none()
            printer_name = printer.name if printer else f"Printer {printer_id}"

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
                        result = await client.sync_ams_tray(tray, printer_name, disable_weight_sync=disable_weight_sync)
                        if result:
                            synced += 1
                    except Exception as e:
                        logger.error(f"Error syncing AMS {ams_id} tray {tray.tray_id}: {e}")

            if synced > 0:
                logger.info(f"Auto-synced {synced} AMS trays to Spoolman for printer {printer_id}")

    except Exception as e:
        import logging

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
            logger.info(f"[SNAPSHOT] Capturing from external camera for printer {printer_id}")
            from backend.app.services.external_camera import capture_frame

            frame_data = await capture_frame(printer.external_camera_url, printer.external_camera_type or "mjpeg")
            if frame_data and len(frame_data) <= 2_500_000:
                logger.info(f"[SNAPSHOT] External camera frame: {len(frame_data)} bytes")
                return frame_data

        # Try buffered frame from active stream
        from backend.app.api.routes.camera import _active_chamber_streams, _active_streams, get_buffered_frame

        active_for_printer = [k for k in _active_streams if k.startswith(f"{printer_id}-")]
        active_chamber = [k for k in _active_chamber_streams if k.startswith(f"{printer_id}-")]
        buffered_frame = get_buffered_frame(printer_id)

        if (active_for_printer or active_chamber) and buffered_frame:
            logger.info(f"[SNAPSHOT] Using buffered frame for printer {printer_id}: {len(buffered_frame)} bytes")
            if len(buffered_frame) <= 2_500_000:
                return buffered_frame

        # Fresh capture from printer camera
        logger.info(f"[SNAPSHOT] Capturing fresh frame for printer {printer_id}")
        from backend.app.services.camera import capture_camera_frame_bytes

        frame_data = await capture_camera_frame_bytes(
            printer.ip_address, printer.access_code, printer.model, timeout=15
        )
        if frame_data and len(frame_data) <= 2_500_000:
            logger.info(f"[SNAPSHOT] Fresh camera frame: {len(frame_data)} bytes")
            return frame_data

    except Exception as e:
        logger.warning(f"[SNAPSHOT] Failed to capture snapshot for printer {printer_id}: {e}")

    return None


async def _send_print_start_notification(
    printer_id: int,
    data: dict,
    archive_data: dict | None = None,
    logger=None,
):
    """Helper to send print start notification with optional archive data."""
    if logger is None:
        import logging

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
        logger.warning(f"Notification on_print_start failed: {e}")


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
                    logger.info(f"Loaded {len(printable_objects)} printable objects for printer {printer_id}")
    except Exception as e:
        logger.debug(f"Failed to extract printable objects from archive: {e}")


async def on_print_start(printer_id: int, data: dict):
    """Handle print start - archive the 3MF file immediately."""
    import logging

    logger = logging.getLogger(__name__)

    logger.info(f"[CALLBACK] on_print_start called for printer {printer_id}, data keys: {list(data.keys())}")

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

    # Track if notification was sent (to avoid sending twice)
    notification_sent = False

    # Smart plug automation: turn on plug when print starts
    try:
        async with async_session() as db:
            await smart_plug_manager.on_print_start(printer_id, db)
    except Exception as e:
        logger.warning(f"Smart plug on_print_start failed: {e}")

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
            logger.info(f"[PLATE CHECK] ENTERING plate detection code for printer {printer_id}")
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
                        logger.info(f"[PLATE CHECK] Turning on chamber light for printer {printer_id}")
                        client.set_chamber_light(True)
                        # Wait for light to physically turn on and camera to adjust exposure
                        await asyncio.sleep(2.5)

                logger.info(f"[PLATE CHECK] Running plate detection for printer {printer_id}")
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
                    logger.info(f"[PLATE CHECK] Restoring chamber light to off for printer {printer_id}")
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
                        logger.info(f"[PLATE CHECK] Print paused for printer {printer_id}")

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
                        logger.warning(f"[PLATE CHECK] Failed to send notification: {notif_err}")
                else:
                    logger.info(f"[PLATE CHECK] Plate is empty for printer {printer_id}, proceeding with print")
            except Exception as plate_err:
                # Don't block print on plate detection errors
                logger.warning(f"[PLATE CHECK] Plate detection failed for printer {printer_id}: {plate_err}")

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

        logger.info(f"[CALLBACK] Print start detected - filename: {filename}, subtask: {subtask_name}")

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
            logger.info(f"Using expected archive {expected_archive_id} for print (skipping duplicate)")
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
                logger.info(f"Marked archive {archive.id} as reprint for cost addition on completion")

                # Set up energy tracking
                try:
                    plug_result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
                    plug = plug_result.scalar_one_or_none()
                    logger.info(
                        f"[ENERGY] Print start - archive {archive.id}, printer {printer_id}, plug found: {plug is not None}"
                    )
                    if plug:
                        energy = await _get_plug_energy(plug, db)
                        logger.info(f"[ENERGY] Energy response from plug: {energy}")
                        if energy and energy.get("total") is not None:
                            _print_energy_start[archive.id] = energy["total"]
                            logger.info(
                                f"[ENERGY] Recorded starting energy for archive {archive.id}: {energy['total']} kWh"
                            )
                        else:
                            logger.warning(f"[ENERGY] No 'total' in energy response for archive {archive.id}")
                    else:
                        logger.info(f"[ENERGY] No smart plug found for printer {printer_id}")
                except Exception as e:
                    logger.warning(f"Failed to record starting energy: {e}")

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
                    await _store_spoolman_print_data(printer_id, archive.id, archive.file_path, db, logger)
                except Exception as e:
                    logger.warning(f"[SPOOLMAN] Failed to store tracking data: {e}")

            return  # Skip creating a new archive

        # Check if there's already a "printing" archive for this printer/file
        # This prevents duplicates when backend restarts during an active print
        from backend.app.models.archive import PrintArchive

        check_name = subtask_name or filename.split("/")[-1].replace(".gcode", "").replace(".3mf", "")
        existing = await db.execute(
            select(PrintArchive)
            .where(PrintArchive.printer_id == printer_id)
            .where(PrintArchive.status == "printing")
            .where(PrintArchive.print_name.ilike(f"%{check_name}%"))
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
                existing_archive = None  # Clear so we don't use stale archive
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
                        logger.warning(f"Failed to record starting energy for existing archive: {e}")
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

        logger.info(f"Trying filenames: {possible_names}")

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
                logger.debug(f"Trying FTP download: {remote_path}")
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
                        logger.info(f"Downloaded: {remote_path}")
                        break
                except Exception as e:
                    logger.debug(f"FTP download failed for {remote_path}: {e}")

            if downloaded_filename:
                break

        # If still not found, try listing directories to find matching file
        # Different printer models use different directory structures
        if not downloaded_filename and (filename or subtask_name):
            search_term = (subtask_name or filename).lower().replace(".gcode", "").replace(".3mf", "")
            logger.info(f"Direct FTP download failed, searching directories for '{search_term}'")
            search_dirs = ["/cache", "/model", "/data", "/data/Metadata", "/"]
            for search_dir in search_dirs:
                if downloaded_filename:
                    break
                try:
                    dir_files = await list_files_async(printer.ip_address, printer.access_code, search_dir)
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
                            logger.info(f"Found matching file in {search_dir}: {fname}")
                            temp_path = app_settings.archive_dir / "temp" / fname
                            temp_path.parent.mkdir(parents=True, exist_ok=True)
                            if ftp_retry_enabled:
                                downloaded = await with_ftp_retry(
                                    download_file_async,
                                    printer.ip_address,
                                    printer.access_code,
                                    f"{search_dir}/{fname}",
                                    temp_path,
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
                                )
                            if downloaded:
                                downloaded_filename = fname
                                logger.info(f"Found and downloaded from {search_dir}: {fname}")
                                break
                except Exception as e:
                    logger.debug(f"Failed to list {search_dir}: {e}")

        if not downloaded_filename or not temp_path:
            logger.warning(f"Could not find 3MF file for print: {filename or subtask_name}")
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

                logger.info(f"Created fallback archive {fallback_archive.id} for {print_name} (no 3MF available)")

                # Start timelapse session if external camera is enabled
                if printer.external_camera_enabled and printer.external_camera_url:
                    from backend.app.services.layer_timelapse import start_session

                    start_session(
                        printer_id,
                        fallback_archive.id,
                        printer.external_camera_url,
                        printer.external_camera_type or "mjpeg",
                    )
                    logger.info(f"Started layer timelapse for printer {printer_id}, archive {fallback_archive.id}")

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
                    logger.warning(f"Failed to record starting energy for fallback: {e}")

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
                        printer_id, fallback_archive.id, fallback_archive.file_path, db, logger
                    )
                except Exception as e:
                    logger.debug(f"[SPOOLMAN] Could not store tracking for fallback archive: {e}")

                # Send notification without archive data (file not found)
                if not notification_sent:
                    await _send_print_start_notification(printer_id, data, logger=logger)
                return
            except Exception as e:
                logger.error(f"Failed to create fallback archive: {e}")
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

                logger.info(f"Created archive {archive.id} for {downloaded_filename}")

                # Start timelapse session if external camera is enabled
                if printer.external_camera_enabled and printer.external_camera_url:
                    from backend.app.services.layer_timelapse import start_session

                    start_session(
                        printer_id,
                        archive.id,
                        printer.external_camera_url,
                        printer.external_camera_type or "mjpeg",
                    )
                    logger.info(f"Started layer timelapse for printer {printer_id}, archive {archive.id}")

                # Record starting energy from smart plug if available
                try:
                    plug_result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
                    plug = plug_result.scalar_one_or_none()
                    logger.info(
                        f"[ENERGY] Auto-archive print start - archive {archive.id}, printer {printer_id}, plug found: {plug is not None}"
                    )
                    if plug:
                        energy = await _get_plug_energy(plug, db)
                        logger.info(f"[ENERGY] Auto-archive energy response: {energy}")
                        if energy and energy.get("total") is not None:
                            _print_energy_start[archive.id] = energy["total"]
                            logger.info(
                                f"[ENERGY] Recorded starting energy for archive {archive.id}: {energy['total']} kWh"
                            )
                        else:
                            logger.warning(f"[ENERGY] No 'total' in energy response for archive {archive.id}")
                    else:
                        logger.info(f"[ENERGY] No smart plug found for printer {printer_id}")
                except Exception as e:
                    logger.warning(f"Failed to record starting energy: {e}")

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
                    notification_sent = True

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
                            logger.info(f"Loaded {len(printable_objects)} printable objects for printer {printer_id}")
                except Exception as e:
                    logger.debug(f"Failed to extract printable objects: {e}")

                # Store Spoolman tracking data for per-filament usage reporting
                try:
                    await _store_spoolman_print_data(printer_id, archive.id, archive.file_path, db, logger)
                except Exception as e:
                    logger.warning(f"[SPOOLMAN] Failed to store tracking data: {e}")
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()


async def _scan_for_timelapse_with_retries(archive_id: int):
    """
    Scan for timelapse with retries.

    The printer encodes the timelapse quickly after print completion.
    We just need a short delay then grab the most recent file.

    Since we KNOW timelapse was active (from MQTT ipcam data), the most recent
    file in /timelapse is our target. Retries handle FTP connection issues.
    """
    import logging

    logger = logging.getLogger(__name__)

    # Short delays - printer usually finishes encoding within seconds
    retry_delays = [5, 10, 20]

    for attempt, delay in enumerate(retry_delays, 1):
        logger.info(
            f"[TIMELAPSE] Attempt {attempt}/{len(retry_delays)}: waiting {delay}s before scanning for archive {archive_id}"
        )
        await asyncio.sleep(delay)

        try:
            async with async_session() as db:
                from backend.app.models.printer import Printer
                from backend.app.services.bambu_ftp import download_file_bytes_async, list_files_async

                # Get archive (ArchiveService from module-level import)
                service = ArchiveService(db)
                archive = await service.get_archive(archive_id)

                if not archive:
                    logger.warning(f"[TIMELAPSE] Archive {archive_id} not found, stopping retries")
                    return
                if archive.timelapse_path:
                    logger.info(f"[TIMELAPSE] Archive {archive_id} already has timelapse attached, stopping retries")
                    return
                if not archive.printer_id:
                    logger.warning(f"[TIMELAPSE] Archive {archive_id} has no printer, stopping retries")
                    return

                # Get printer
                result = await db.execute(select(Printer).where(Printer.id == archive.printer_id))
                printer = result.scalar_one_or_none()

                if not printer:
                    logger.warning(f"[TIMELAPSE] Printer not found for archive {archive_id}, stopping retries")
                    return

                # Scan timelapse directory on printer
                # H2D may store in different locations than X1C
                files = []
                found_path = None
                for timelapse_path in ["/timelapse", "/timelapse/video", "/record", "/recording"]:
                    try:
                        found_files = await list_files_async(printer.ip_address, printer.access_code, timelapse_path)
                        if found_files:
                            files = found_files
                            found_path = timelapse_path
                            logger.info(f"[TIMELAPSE] Attempt {attempt}: Found {len(files)} files in {timelapse_path}")
                            break
                    except Exception as e:
                        logger.debug(f"[TIMELAPSE] Path {timelapse_path} failed: {e}")
                        continue

                if not files:
                    logger.info(f"[TIMELAPSE] Attempt {attempt}: No timelapse files found on printer, will retry")
                    continue

                mp4_files = [f for f in files if not f.get("is_directory") and f.get("name", "").endswith(".mp4")]

                # Log ALL mp4 files found for debugging
                logger.info(f"[TIMELAPSE] Attempt {attempt}: Found {len(mp4_files)} MP4 files in {found_path}")
                for f in mp4_files[:5]:  # Log first 5
                    logger.info(f"[TIMELAPSE]   - {f.get('name')}, mtime={f.get('mtime')}")

                if not mp4_files:
                    logger.info(f"[TIMELAPSE] Attempt {attempt}: No MP4 files found, will retry")
                    continue

                # Sort by mtime descending to get most recent file
                mp4_files_with_mtime = [f for f in mp4_files if f.get("mtime")]
                if not mp4_files_with_mtime:
                    logger.info(f"[TIMELAPSE] Attempt {attempt}: No MP4 files with mtime found, will retry")
                    continue

                mp4_files_with_mtime.sort(key=lambda x: x.get("mtime"), reverse=True)
                most_recent = mp4_files_with_mtime[0]

                file_name = most_recent.get("name")
                logger.info(f"[TIMELAPSE] Attempt {attempt}: Most recent file: {file_name}")

                # Since we KNOW timelapse was active (from MQTT), just grab the most recent file
                remote_path = most_recent.get("path") or f"/timelapse/{file_name}"
                logger.info(f"[TIMELAPSE] Downloading {file_name} for archive {archive_id}")
                timelapse_data = await download_file_bytes_async(printer.ip_address, printer.access_code, remote_path)

                if timelapse_data:
                    success = await service.attach_timelapse(archive_id, timelapse_data, file_name)
                    if success:
                        logger.info(f"[TIMELAPSE] Successfully attached timelapse to archive {archive_id}")
                        await ws_manager.send_archive_updated({"id": archive_id, "timelapse_attached": True})
                        return  # Success!
                    else:
                        logger.warning(f"[TIMELAPSE] Failed to attach timelapse to archive {archive_id}")
                else:
                    logger.warning(f"[TIMELAPSE] Attempt {attempt}: Failed to download, will retry")

        except Exception as e:
            logger.warning(f"[TIMELAPSE] Attempt {attempt} failed with error: {e}")

    logger.warning(f"[TIMELAPSE] All {len(retry_delays)} attempts exhausted for archive {archive_id}, giving up")


async def on_print_complete(printer_id: int, data: dict):
    """Handle print completion - update the archive status."""
    import logging
    import time

    logger = logging.getLogger(__name__)
    start_time = time.time()

    def log_timing(section: str):
        elapsed = time.time() - start_time
        logger.info(f"[TIMING] {section}: {elapsed:.3f}s elapsed")

    logger.info(f"[CALLBACK] on_print_complete started for printer {printer_id}")

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
        logger.warning(f"[CALLBACK] WebSocket send_print_complete failed: {e}")

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

    logger.info(f"Print complete - filename: {filename}, subtask: {subtask_name}, status: {data.get('status')}")

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
    logger.info(f"Looking for archive in _active_prints, keys to try: {possible_keys[:5]}...")
    logger.info(f"Current _active_prints: {list(_active_prints.keys())}")
    archive_id = None
    for key in possible_keys:
        archive_id = _active_prints.pop(key, None)
        if archive_id:
            logger.info(f"Found archive {archive_id} with key {key}")
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
                    logger.info(f"Found archive {archive_id} by subtask_name match: {subtask_name}")

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

    if not archive_id:
        logger.warning(f"Could not find archive for print complete: filename={filename}, subtask={subtask_name}")
        return

    log_timing("Archive lookup")

    # Update archive status
    logger.info(f"[ARCHIVE] Updating archive {archive_id} status...")
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
                    logger.info(f"[ARCHIVE] HMS errors at failure: {hms_errors}")
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
                        logger.info(f"[ARCHIVE] Detected failure_reason from HMS: {failure_reason}")
                else:
                    logger.info("[ARCHIVE] No HMS errors available to determine failure reason")

            await service.update_archive_status(
                archive_id,
                status=status,
                completed_at=datetime.now() if status in ("completed", "failed", "aborted") else None,
                failure_reason=failure_reason,
            )
            logger.info(f"[ARCHIVE] Archive {archive_id} status updated to {status}, failure_reason={failure_reason}")

            # Add cost for reprints (first prints have cost set in archive_print())
            if status == "completed" and archive_id in _reprint_archives:
                _reprint_archives.discard(archive_id)
                try:
                    await service.add_reprint_cost(archive_id)
                    logger.info(f"[ARCHIVE] Added reprint cost for archive {archive_id}")
                except Exception as e:
                    logger.warning(f"[ARCHIVE] Failed to add reprint cost for archive {archive_id}: {e}")

            await ws_manager.send_archive_updated(
                {
                    "id": archive_id,
                    "status": status,
                }
            )
            logger.info(f"[ARCHIVE] WebSocket notification sent for archive {archive_id}")

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
        logger.error(f"[ARCHIVE] Failed to update archive {archive_id} status: {e}", exc_info=True)
        # Continue with other operations even if archive update fails

    log_timing("Archive status update")

    # Report filament usage to Spoolman if print completed successfully
    if data.get("status") == "completed":
        try:
            await _report_spoolman_usage(printer_id, archive_id, logger)
            log_timing("Spoolman usage report")
        except Exception as e:
            logger.warning(f"Spoolman usage reporting failed: {e}")
    else:
        # Report partial usage if tracking data exists (only stored when weight sync is disabled)
        try:
            async with async_session() as db:
                await _cleanup_spoolman_tracking(printer_id, archive_id, db, logger)
        except Exception as e:
            logger.debug(f"[SPOOLMAN] Cleanup failed: {e}")

    # Run slow operations as background tasks to avoid blocking the event loop
    # These operations can take 5-10+ seconds and would freeze the UI if awaited
    starting_kwh = _print_energy_start.pop(archive_id, None)

    async def _background_energy_calculation():
        """Calculate and save energy usage in background."""
        try:
            logger.info(f"[ENERGY-BG] Starting energy calculation for archive {archive_id}")
            async with async_session() as db:
                plug_result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
                plug = plug_result.scalar_one_or_none()

                if plug:
                    energy = await _get_plug_energy(plug, db)
                    logger.info(f"[ENERGY-BG] Energy response: {energy}")

                    energy_used = None
                    if starting_kwh is not None and energy and energy.get("total") is not None:
                        ending_kwh = energy["total"]
                        energy_used = round(ending_kwh - starting_kwh, 4)
                        logger.info(f"[ENERGY-BG] Per-print energy: {energy_used} kWh")

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
                            logger.info(f"[ENERGY-BG] Saved: {energy_used} kWh, cost={energy_cost}")
                else:
                    logger.info(f"[ENERGY-BG] No smart plug for printer {printer_id}")
        except Exception as e:
            logger.warning(f"[ENERGY-BG] Failed: {e}")

    async def _background_finish_photo() -> str | None:
        """Capture finish photo in background. Returns photo filename if captured."""
        try:
            logger.info(f"[PHOTO-BG] Starting finish photo capture for archive {archive_id}")

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
                                    logger.info(f"[PHOTO-BG] Saved external camera frame: {photo_filename}")
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
                                    logger.info(f"[PHOTO-BG] Saved buffered frame: {photo_filename}")
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
                                logger.info(f"[PHOTO-BG] Saved: {photo_filename}")
                                return photo_filename
            return None
        except Exception as e:
            logger.warning(f"[PHOTO-BG] Failed: {e}")
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
            logger.info(f"[AUTO-OFF-BG] Starting smart plug automation for printer {printer_id}")
            async with async_session() as db:
                await smart_plug_manager.on_print_complete(printer_id, print_status, db)
                logger.info("[AUTO-OFF-BG] Completed")
        except Exception as e:
            logger.warning(f"[AUTO-OFF-BG] Failed: {e}")

    async def _background_notifications(finish_photo_filename: str | None = None):
        """Send print complete notifications in background."""
        try:
            logger.info(f"[NOTIFY-BG] Starting notifications for printer {printer_id}, photo={finish_photo_filename}")
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
                                        logger.info(f"[NOTIFY-BG] Loaded finish photo bytes: {len(photo_bytes)} bytes")
                                    else:
                                        logger.warning(
                                            f"[NOTIFY-BG] Finish photo too large for attachment: "
                                            f"{len(photo_bytes)} bytes"
                                        )
                            except Exception as e:
                                logger.warning(f"[NOTIFY-BG] Failed to read finish photo bytes: {e}")

                await notification_service.on_print_complete(
                    printer_id, printer_name, print_status, data, db, archive_data=archive_data
                )
                logger.info("[NOTIFY-BG] Completed")
        except Exception as e:
            logger.warning(f"[NOTIFY-BG] Failed: {e}")

    async def _background_maintenance_check():
        """Check for maintenance due in background."""
        if print_status != "completed":
            return
        try:
            logger.info(f"[MAINT-BG] Starting maintenance check for printer {printer_id}")
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
                    logger.info(f"[MAINT-BG] Sent notification: {len(items_needing_attention)} items need attention")

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
            logger.warning(f"[MAINT-BG] Failed: {e}")

    asyncio.create_task(_background_smart_plug())
    asyncio.create_task(_background_maintenance_check())

    # Notification task waits for photo capture to complete first
    async def _photo_then_notify():
        """Wait for photo capture, then send notification with photo URL."""
        try:
            finish_photo = await photo_task
            logger.info(f"[PHOTO-NOTIFY] Photo task returned: {finish_photo}")
            await _background_notifications(finish_photo)
        except Exception as e:
            logger.warning(f"[PHOTO-NOTIFY] Failed: {e}")
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
                logger.info(f"[LAYER-TL] Stitching layer timelapse for printer {printer_id}")
                timelapse_path = await tl_complete(printer_id)
                if timelapse_path and archive_id:
                    logger.info(f"[LAYER-TL] Attaching timelapse {timelapse_path} to archive {archive_id}")
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
                logger.info(f"[LAYER-TL] Cancelled layer timelapse for printer {printer_id} (status: {print_status})")
        except Exception as e:
            logger.warning(f"[LAYER-TL] Failed: {e}")
            # Try to cancel session on error
            try:
                cancel_session(printer_id)
            except Exception:
                pass

    asyncio.create_task(_background_layer_timelapse())
    log_timing("All background tasks scheduled")

    # Auto-scan for timelapse if recording was active during the print
    if archive_id and data.get("timelapse_was_active") and data.get("status") == "completed":
        logger.info(f"[TIMELAPSE] Timelapse was active during print, scheduling auto-scan for archive {archive_id}")
        # Schedule timelapse scan as background task with retries
        # The printer needs time to encode the video after print completion
        asyncio.create_task(_scan_for_timelapse_with_retries(archive_id))
        log_timing("Timelapse scan scheduled")

    # Update queue item if this was a scheduled print
    try:
        async with async_session() as db:
            from backend.app.models.print_queue import PrintQueueItem
            # Note: SmartPlug is already imported at module level (line 56)
            # Do NOT import it here as it would shadow the module-level import
            # and cause "cannot access local variable" errors earlier in this function

            result = await db.execute(
                select(PrintQueueItem)
                .where(PrintQueueItem.printer_id == printer_id)
                .where(PrintQueueItem.status == "printing")
            )
            queue_item = result.scalar_one_or_none()
            if queue_item:
                status = data.get("status", "completed")
                queue_item.status = status
                queue_item.completed_at = datetime.now()
                await db.commit()
                logger.info(f"Updated queue item {queue_item.id} status to {status}")

                # MQTT relay - publish queue job completed
                try:
                    printer_info = printer_manager.get_printer(printer_id)
                    await mqtt_relay.on_queue_job_completed(
                        job_id=queue_item.id,
                        filename=filename or subtask_name,
                        printer_id=printer_id,
                        printer_name=printer_info.name if printer_info else "Unknown",
                        status=status,
                    )
                except Exception:
                    pass  # Don't fail if MQTT fails

                # Check if queue is now empty and send notification
                try:
                    from sqlalchemy import func

                    # Count remaining pending items
                    count_result = await db.execute(
                        select(func.count(PrintQueueItem.id)).where(PrintQueueItem.status == "pending")
                    )
                    pending_count = count_result.scalar() or 0

                    if pending_count == 0:
                        # Count how many completed today (rough approximation)
                        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                        completed_result = await db.execute(
                            select(func.count(PrintQueueItem.id)).where(
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
                        logger.info(f"Auto-off requested for printer {printer_id}, waiting for cooldown...")

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
                                        logger.info(f"Powered off printer {pid} via smart plug '{p.name}'")
                                    else:
                                        logger.warning(f"Failed to power off printer {pid} via smart plug")

                        asyncio.create_task(cooldown_and_poweroff(printer_id, plug.id))
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(f"Queue item update failed: {e}")

    log_timing("Queue item update")
    logger.info(f"[CALLBACK] on_print_complete finished for printer {printer_id}, archive {archive_id}")


# AMS sensor history recording
_ams_history_task: asyncio.Task | None = None
AMS_HISTORY_INTERVAL = 300  # Record every 5 minutes
AMS_HISTORY_RETENTION_DAYS = 30  # Keep data for 30 days
_ams_cleanup_counter = 0  # Track recordings to trigger periodic cleanup
_ams_alarm_cooldown: dict[str, datetime] = {}  # Track alarm cooldowns (printer_id:ams_id:type -> last_alarm_time)
AMS_ALARM_COOLDOWN_MINUTES = 60  # Don't send same alarm more than once per hour


async def record_ams_history():
    """Background task to record AMS humidity and temperature data."""
    import logging

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
                        pass
                result = await db.execute(select(Settings).where(Settings.key == "ams_temp_fair"))
                setting = result.scalar_one_or_none()
                if setting:
                    try:
                        temp_threshold = float(setting.value)
                    except (ValueError, TypeError):
                        pass

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
                                pass
                        if humidity is None and humidity_idx is not None:
                            try:
                                humidity = float(humidity_idx)
                            except (ValueError, TypeError):
                                pass

                        # Get temperature
                        temperature = None
                        temp_str = ams_data.get("temp")
                        if temp_str is not None:
                            try:
                                temperature = float(temp_str)
                            except (ValueError, TypeError):
                                pass

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
                                    logger.warning(f"Failed to send humidity alarm: {e}")

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
                                    logger.warning(f"Failed to send temperature alarm: {e}")

                await db.commit()
                if recorded_count > 0:
                    logger.info(f"Recorded {recorded_count} AMS sensor history entries")

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
            logger.warning(f"AMS history recording failed: {e}")
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
    import logging

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
                        logger.debug(f"[{printer.name}] Runtime tracking: no state available")
                        continue
                    if not state.connected:
                        logger.debug(f"[{printer.name}] Runtime tracking: not connected")
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
                            logger.debug(f"[{printer.name}] Runtime tracking: first active detection")

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
                        logger.debug(f"Updated runtime for {updated_count} printer(s)")

        except asyncio.CancelledError:
            logger.info("Runtime tracking cancelled")
            break
        except Exception as e:
            logger.warning(f"Runtime tracking failed: {e}")

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
                logging.info(f"Restored {len(mqtt_plugs)} MQTT smart plug subscriptions")

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
                    logging.info(f"Auto-connected to Spoolman at {spoolman_url}")
                    # Ensure the 'tag' extra field exists for RFID/UUID storage
                    await client.ensure_tag_extra_field()
                else:
                    logging.warning(f"Spoolman at {spoolman_url} is not reachable")
            except Exception as e:
                logging.warning(f"Failed to auto-connect to Spoolman: {e}")

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
                        logging.info(f"Virtual printer proxy started (target={vp_target_ip})")
                    else:
                        logging.info(f"Virtual printer started (model={vp_model or 'default'})")
                except Exception as e:
                    logging.warning(f"Failed to start virtual printer: {e}")

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
app.include_router(settings_routes.router, prefix=app_settings.api_prefix)
app.include_router(cloud.router, prefix=app_settings.api_prefix)
app.include_router(smart_plugs.router, prefix=app_settings.api_prefix)
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
