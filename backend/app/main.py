import asyncio
import json
import logging
import mimetypes as _mimetypes
import os
import posixpath
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, or_, select, text

from backend.app.api.routes import (
    ams_history,
    api_keys,
    archive_purge,
    archives,
    auth,
    bug_report,
    camera,
    camwall,
    cloud,
    discovery,
    external_links,
    filaments,
    firmware,
    github_backup,
    groups,
    inventory,
    kprofiles,
    labels,
    library,
    library_tags,
    library_trash,
    local_backup,
    local_presets,
    maintenance,
    makerworld,
    metrics,
    mfa,
    notification_templates,
    notifications,
    obico,
    orca_cloud,
    pending_uploads,
    pipeline_runs,
    print_log,
    print_queue,
    printer_sensor_history,
    printers,
    projects,
    settings as settings_routes,
    slice_jobs,
    slicer_pipelines,
    slicer_presets,
    smart_plugs,
    sponsor_prompt,
    spoolbuddy,
    spoolman,
    spoolman_inventory,
    support,
    system,
    updates,
    user_notifications,
    users,
    virtual_printers,
    webhook,
    websocket,
)
from backend.app.api.routes.maintenance import _get_printer_maintenance_internal, ensure_default_types
from backend.app.api.routes.support import init_debug_logging
from backend.app.core.config import APP_VERSION, settings as app_settings
from backend.app.core.database import async_session, engine, init_db
from backend.app.core.tasks import spawn_background_task
from backend.app.core.websocket import ws_manager
from backend.app.models.smart_plug import SmartPlug
from backend.app.services.archive import ArchiveService, peek_plate_index_in_3mf, swap_plate_suffix
from backend.app.services.archive_purge import archive_purge_service
from backend.app.services.bambu_ftp import (
    FileNotOnPrinterError,
    cache_3mf_download,
    clear_3mf_cache,
    download_file_async,
    get_cached_3mf,
    get_ftp_retry_settings,
    with_ftp_retry,
)
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.github_backup import github_backup_service
from backend.app.services.homeassistant import homeassistant_service
from backend.app.services.library_trash import library_trash_service
from backend.app.services.local_backup import local_backup_service
from backend.app.services.mqtt_relay import mqtt_relay
from backend.app.services.mqtt_smart_plug import mqtt_smart_plug_service
from backend.app.services.notification_service import notification_service
from backend.app.services.obico_detection import obico_detection_service
from backend.app.services.print_scheduler import scheduler as print_scheduler
from backend.app.services.printer_manager import (
    init_printer_connections,
    parse_plate_id,
    printer_manager,
    printer_state_to_dict,
    resolve_plate_id,
)
from backend.app.services.smart_plug_manager import smart_plug_manager
from backend.app.services.spool_assignment_notifications import (
    notify_missing_spool_assignments_on_print_start,
)
from backend.app.services.spoolman import close_spoolman_client, get_spoolman_client, init_spoolman_client
from backend.app.services.spoolman_tracking import (
    cleanup_tracking as _cleanup_spoolman_tracking,
    report_usage as _report_spoolman_usage,
    store_print_data as _store_spoolman_print_data,
)
from backend.app.services.tasmota import tasmota_service


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


# Import settings first for logging configuration

# Configure logging based on settings
# DEBUG=true -> DEBUG level, else use LOG_LEVEL setting
log_level_str = "DEBUG" if app_settings.debug else app_settings.log_level.upper()
log_level = getattr(logging, log_level_str, logging.INFO)
# Trace ID column ([-] when no request scope is active — startup, MQTT
# callbacks, scheduled tasks not chained from a request — so the column
# stays visually aligned and missing values are obvious in grep). See
# backend/app/core/trace.py for the ContextVar that feeds this slot.
log_format = "%(asctime)s %(levelname)s [%(name)s] [%(trace_id)s] %(message)s"

# Create root logger
root_logger = logging.getLogger()
root_logger.setLevel(log_level)

# Trace-ID injection: this filter populates record.trace_id from the
# per-request ContextVar so the format string above can reference it.
# Attached to each HANDLER (not the root logger) because Python's
# logging semantics only invoke a logger's filters on records that
# *originated* at that logger — records propagated up from child
# loggers (every named logger in the app) never trigger root's filter.
# Putting it on the handlers means every record any handler emits gets
# trace_id injected just before the formatter runs, regardless of which
# logger created the record. Without this, the formatter raises
# KeyError on every child-logger record and the record is silently
# dropped — which is exactly the "logs/bambuddy.log only shows logs
# partially" bug we hit. See backend/app/core/trace.py for the
# ContextVar the filter reads.
from backend.app.core.trace import TraceIDFilter

_trace_id_filter = TraceIDFilter()

# Console handler - always enabled
console_handler = logging.StreamHandler()
console_handler.setLevel(log_level)
console_handler.setFormatter(logging.Formatter(log_format))
console_handler.addFilter(_trace_id_filter)
root_logger.addHandler(console_handler)

# File handler - only in production or if explicitly enabled
if app_settings.log_to_file:
    log_file = app_settings.log_dir / "bambuddy.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=app_settings.log_max_bytes,
        backupCount=app_settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(log_format))
    file_handler.addFilter(_trace_id_filter)
    root_logger.addHandler(file_handler)
    logging.info("Logging to file: %s", log_file)

    # Pipe uvicorn's HTTP access log to bambuddy.log too. Uvicorn ships its
    # access logger with propagate=False by default, so without this attach
    # there is no on-disk record of which endpoint triggered a server-state
    # change — the rogue stop_print mystery on 2026-04-26 was untraceable
    # for exactly this reason. Filtered to write methods only
    # (POST/PUT/PATCH/DELETE) so the high-volume status-poll GETs from the
    # frontend don't churn the rotation window faster than it's useful.
    from backend.app.core.logging_filters import (
        CancelledPoolNoiseFilter,
        WriteRequestsOnlyFilter,
    )

    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.addHandler(file_handler)
    uvicorn_access_logger.addFilter(WriteRequestsOnlyFilter())
    # Uvicorn's access logger has propagate=False (its own default), so the
    # root-attached TraceIDFilter never sees these records. Attach a
    # second instance directly so HTTP access lines carry the same trace
    # ID column as the application logs they correlate with.
    uvicorn_access_logger.addFilter(TraceIDFilter())

    # Drop SQLAlchemy connection-pool log noise that's caused by Starlette's
    # BaseHTTPMiddleware cancelling the inner task scope on client
    # disconnect (#1112). The cancel-safe `get_db` already prevents the
    # underlying transaction leak; this filter only suppresses the residual
    # log records that pre-existing pools still emit during their cleanup.
    logging.getLogger("sqlalchemy.pool").addFilter(CancelledPoolNoiseFilter())

# Reduce noise from third-party libraries in production
if not app_settings.debug:
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("paho.mqtt").setLevel(logging.WARNING)

logging.info("Bambuddy starting - debug=%s, log_level=%s", app_settings.debug, log_level_str)


# Track active prints: {(printer_id, filename): archive_id}
_active_prints: dict[tuple[int, str], int] = {}

# #1721: stage-22 pre-captured finish photo bytes per printer. on_finish_photo_moment
# fires when stg_cur enters 22 ("Filament unloading") at end-of-print — toolhead
# parked, bed not yet dropped — and grabs a single camera frame into this cache.
# `_background_finish_photo` (inside on_print_complete) consumes the cached bytes
# instead of running its own grab-now chain when present, so the finish photo
# captures the better-framed pre-bed-drop moment without us having to force
# timelapse on at dispatch (the #1397 mechanism that caused #1721's per-layer
# nozzle parking on slicer profiles with Timelapse Type = Smooth).
_stage22_finish_frames: dict[int, bytes] = {}

# #1790: per-printer producer-done event. Set by `on_finish_photo_moment` in its
# `finally` block (whether it captured a frame or not). The consumer in
# `_background_finish_photo` waits on it before reading `_stage22_finish_frames`
# so the FINISH-state fallback path — where moment and completion are dispatched
# back-to-back — doesn't race past the producer with an empty pop, and the
# consumer's RTSP fallback can't collide with the producer's still-in-flight RTSP
# grab (Bambu printers allow only one RTSP client at a time).
_stage22_finish_in_flight: dict[int, asyncio.Event] = {}

# #1867: rolling "last in-print camera frame" per printer. Refreshed on
# layer-change while the model is still printing, then consumed by the
# FINISH-state finish-photo path. Firmware that never emits `stg_cur=22`
# (A1 Mini, confirmed) only reaches `on_finish_photo_moment` at the
# gcode_state=FINISH transition — which Bambu reports AFTER the user End
# G-code (e.g. SwapMod plate-swap) has run, so a live grab there captures the
# swapped/empty plate. Banking is layer-driven, so it naturally freezes at the
# final object layer: the End G-code emits no further layer_num increases, so
# the last banked frame is always the finished print before the swap.
_inprint_frame_bank: dict[int, bytes] = {}
# Monotonic timestamp of the last banked frame per printer — throttles banking
# so tall prints don't add a camera grab on every layer.
_inprint_frame_bank_ts: dict[int, float] = {}
# Minimum seconds between banked frames, except the final object layer which
# always refreshes for the best framing.
_INPRINT_BANK_MIN_INTERVAL = 25.0

# Per-printer "connected" edge tracker. Used by `on_printer_status_change`
# to fire `reconcile_stale_active_prints` exactly once per (re)connection
# (#1542 follow-up — power-cycle ghost prints). The value is True after
# the first connected status update for that connection; transitions back
# to False whenever we observe `state.connected = False` so the next
# reconnect re-arms reconciliation. Keyed by printer_id.
_printer_reconciled_since_connect: dict[int, bool] = {}

# Track expected prints from reprint/scheduled (skip auto-archiving for these)
# {(printer_id, filename): archive_id}
_expected_prints: dict[tuple[int, str], int] = {}

# Track AMS mapping for prints: {archive_id: [global_tray_id_per_slot]}
# Used by usage tracker to map 3MF slots to physical AMS trays
_print_ams_mappings: dict[int, list[int]] = {}

# Track plate_id for prints from multi-plate 3MFs: {archive_id: plate_id}
# Used by usage tracker to scope 3MF parsing to the dispatched plate (#1697).
# Populated by direct-Print and queue dispatch paths; queue prints also have a
# redundant queue-item lookup in on_print_start so this dict isn't load-bearing
# for the queue path. Cleared on print completion or TTL eviction.
_print_plate_ids: dict[int, int] = {}

# Track progress milestones for notifications: {printer_id: last_milestone_notified}
# Milestones are 25, 50, 75. Value of 0 means no milestone notified yet for current print.
_last_progress_milestone: dict[int, int] = {}

# Track whether first layer complete notification has been sent for current print
_first_layer_notified: dict[int, bool] = {}

# Track HMS errors that have been notified: {printer_id: set of error codes}
# This prevents sending duplicate notifications for the same error
_notified_hms_errors: dict[int, set[str]] = {}
# Track when HMS errors were last seen: {printer_id: timestamp}
# Used to debounce clearing — prevents flapping errors from re-triggering notifications
_hms_last_seen: dict[int, float] = {}
_HMS_CLEAR_GRACE_SECONDS = 30.0

# Track timelapse file baselines at print start: {printer_id: set of video filenames}
# Used for snapshot-diff detection at print completion
_timelapse_baselines: dict[int, set[str]] = {}

# Track printers waiting for bed to cool after print completion.
# Event-driven: fires when bed_temper arrives via MQTT below threshold.
# {printer_id: {"threshold": float, "filename": str, "registered_at": float}}
_bed_cool_waiters: dict[int, dict] = {}

# Track printers where the user explicitly stopped the print from the queue UI.
# When on_print_complete fires with status "failed" for these printers we treat it
# as "cancelled" (stopped by user) so the correct notification email is sent.
_user_stopped_printers: set[int] = set()

# Offline-notification edge state (#1752): fire `on_printer_offline` exactly
# once when a printer transitions connected → disconnected. `_printer_last_connected`
# holds the previous observation so we only fire on the True → False edge (a
# False → False repeat doesn't notify; an initial False at startup doesn't
# notify either, since there's no prior True). `_printer_offline_notify_tasks`
# holds the per-printer pending asyncio task that fires the notification
# after a debounce window — cancelled if the printer reconnects before the
# window elapses, so transient MQTT blips don't flood the user.
_printer_last_connected: dict[int, bool] = {}
_printer_offline_notify_tasks: dict[int, asyncio.Task] = {}
# Debounce: a printer must stay offline this long before we notify. Sized
# against the staleness path (`bambu_mqtt.py::STALE_RECONNECT_COOLDOWN = 30s`)
# so a single stale-trigger cooldown isn't enough to fire — only a real
# offline that survives one reconnect attempt notifies.
_PRINTER_OFFLINE_NOTIFY_DEBOUNCE_SECONDS = 60.0


# HMS short-code → human-readable failure reason. Used by _dispatch_archive_update
# when status="failed" to label the print's failure_reason in archives.
#
# Earlier code matched on `module` alone (e.g. "any module 0x0C HMS → Layer shift"),
# which is wrong on two counts:
#   1. Real layer-shift codes live in module 0x03 (see Bambu wiki), not 0x0C.
#   2. Module 0x0C is "Motion Controller" — broad category that also covers cameras
#      and visual markers, AND the H2D firmware emits a 0x0C HMS (0C00_001B, not in
#      the public wiki) as part of its user-cancel sequence. Matching on the module
#      alone caused user-cancellations to be archived as "Layer shift" failures.
# We now match by full short code only — anything not in this map leaves
# failure_reason=None rather than guessing.
_HMS_FAILURE_REASONS: dict[str, str] = {
    # Layer shift / step loss
    "0300_4057": "Layer shift",
    "0300_4068": "Layer shift",
    "0300_800C": "Layer shift",
    # Filament runout (printer-side & per-AMS-slot)
    "0300_8004": "Filament runout",
    "0700_8011": "Filament runout",
    "0701_8011": "Filament runout",
    "0702_8011": "Filament runout",
    "0703_8011": "Filament runout",
    "0704_8011": "Filament runout",
    "0705_8011": "Filament runout",
    "0706_8011": "Filament runout",
    "0707_8011": "Filament runout",
    "07FF_8011": "Filament runout",
    # Clogged nozzle / extruder
    "0300_4006": "Clogged nozzle",
    "0300_8016": "Clogged nozzle",
    "0300_801C": "Clogged nozzle",
    "0700_8003": "Clogged nozzle",
    "0700_8007": "Clogged nozzle",
    "0700_8013": "Clogged nozzle",
    "0701_8003": "Clogged nozzle",
    "0701_8007": "Clogged nozzle",
    "0701_8013": "Clogged nozzle",
    "0702_8003": "Clogged nozzle",
}


def _hms_short_code(attr: int, code: int | str) -> str:
    """Build the canonical "MMMM_CCCC" HMS short code from raw attr/code values."""
    if isinstance(code, str):
        code_int = int(code.replace("0x", ""), 16) if code else 0
    else:
        code_int = int(code or 0)
    attr_int = int(attr or 0)
    return f"{(attr_int >> 16) & 0xFFFF:04X}_{code_int & 0xFFFF:04X}"


def derive_failure_reason(status: str, hms_errors: list[dict] | None) -> str | None:
    """Derive a human-readable failure_reason for an archived print.

    Returns "User cancelled" for cancelled/aborted prints; for failed prints,
    returns the first matching reason from _HMS_FAILURE_REASONS, or None when
    no HMS code matches (don't guess — null is honest).
    """
    if status in ("aborted", "cancelled"):
        return "User cancelled"
    if status != "failed":
        return None
    for err in hms_errors or []:
        short_code = _hms_short_code(err.get("attr", 0), err.get("code", 0))
        if short_code in _HMS_FAILURE_REASONS:
            return _HMS_FAILURE_REASONS[short_code]
    return None


# Track created_by_id for expected prints so the user email can be sent even when
# the archive itself doesn't have created_by_id set (e.g. library-file-based prints).
# {(printer_id, filename): created_by_id}
_expected_print_creators: dict[tuple[int, str], int] = {}

# Per-printer lock that serialises the spool-assignment side of on_ams_change
# (auto-unlink stale + auto-assign new) when MQTT bursts deliver multiple AMS
# updates for the same printer in quick succession (~30 ms apart, observed in
# the wild on H2D + dual AMS).
#
# Without this serialisation, two concurrent on_ams_change callbacks each read
# "no assignment for (printer, ams, tray)", each call auto_assign_spool, and
# the second commit hits
#   IntegrityError: duplicate key value violates unique constraint
#                   "spool_assignment_printer_id_ams_id_tray_id_key"
# SQLite's WAL serial-write semantics had been silently swallowing the race
# until optional Postgres support landed (asyncpg allows true concurrent
# transactions and surfaces the constraint violation).
#
# Scope is intentionally narrow: only the two DB-mutating blocks (unlink +
# assign) are inside the lock. The Spoolman sync block further down stays
# concurrent because it's network-bound and idempotent.
_ams_assignment_locks: dict[int, asyncio.Lock] = {}


def _get_ams_assignment_lock(printer_id: int) -> asyncio.Lock:
    """Return the per-printer assignment lock, creating it on first use."""
    lock = _ams_assignment_locks.get(printer_id)
    if lock is None:
        lock = asyncio.Lock()
        _ams_assignment_locks[printer_id] = lock
    return lock


# Per-printer dedup for unknown_tag WS broadcasts. Keyed by
# (ams_id, tray_id) -> (tag_uid, tray_uuid); we only re-broadcast when the
# tag tuple changes for the slot. Cleared when the slot is reported empty
# so remove + reinsert reliably re-prompts the UI.
_unknown_tag_last_broadcast: dict[int, dict[tuple[int, int], tuple[str, str]]] = {}


async def _broadcast_unknown_tag(
    *,
    printer_id: int,
    ams_id: int,
    tray_id: int,
    tag_uid: str,
    tray_uuid: str,
    tray_type: str | None = None,
    tray_color: str | None = None,
    tray_sub_brands: str | None = None,
    tray_count: int | None = None,
) -> None:
    """Broadcast unknown_tag, deduped so repeated MQTT pushes for the same slot+tag don't spam the UI."""
    _logger = logging.getLogger(__name__)
    slot_key = (ams_id, tray_id)
    tag_key = (tag_uid or "", tray_uuid or "")
    per_printer = _unknown_tag_last_broadcast.setdefault(printer_id, {})
    if per_printer.get(slot_key) == tag_key:
        _logger.debug(
            "unknown_tag deduped for printer=%d AMS=%d slot=%d tag=%s",
            printer_id,
            ams_id,
            tray_id,
            tag_key[0][:8] or tag_key[1][:8] or "(none)",
        )
        return
    _logger.info(
        "unknown_tag broadcast: printer=%d AMS=%d slot=%d type=%r color=%r tag=%s",
        printer_id,
        ams_id,
        tray_id,
        tray_type,
        tray_color,
        tag_key[0][:8] or tag_key[1][:8] or "(none)",
    )
    # Broadcast first; only commit the dedup if the WS write succeeds.
    # If broadcast raises, the next MQTT push retries instead of being
    # permanently silenced by a poisoned dedup entry.
    await ws_manager.broadcast(
        {
            "type": "unknown_tag",
            "printer_id": printer_id,
            "ams_id": ams_id,
            "tray_id": tray_id,
            "tag_uid": tag_uid,
            "tray_uuid": tray_uuid,
            "tray_type": tray_type,
            "tray_color": tray_color,
            "tray_sub_brands": tray_sub_brands,
            "tray_count": tray_count,
        }
    )
    per_printer[slot_key] = tag_key


def _clear_unknown_tag_dedup(printer_id: int, ams_id: int, tray_id: int) -> None:
    """Drop the cached last-broadcast tag for a slot (called when slot reports empty or gets matched)."""
    per_printer = _unknown_tag_last_broadcast.get(printer_id)
    if per_printer is None:
        return
    per_printer.pop((ams_id, tray_id), None)


# TTL for expected-print entries: evict registrations older than this to prevent
# unbounded growth when a print is registered but never starts (e.g. printer
# disconnect, app restart, print started from the printer panel).
_EXPECTED_PRINT_TTL_SECONDS: int = 2 * 60 * 60  # 2 hours

# Registration timestamps used for TTL eviction: {(printer_id, filename): monotonic_time}
_expected_print_registered_at: dict[tuple[int, str], float] = {}

# Cleanup loop interval
_EXPECTED_PRINT_CLEANUP_INTERVAL: int = 15 * 60  # 15 minutes
_expected_prints_cleanup_task: asyncio.Task | None = None


async def _get_plug_energy(plug, db) -> dict | None:
    """Get energy from plug regardless of type (Tasmota, Home Assistant, MQTT, or REST).

    For HA plugs, configures the service with current settings from DB.
    For MQTT plugs, returns data from the subscription service.
    For REST plugs, polls the status URL with JSON path extraction.
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
    elif plug.plug_type == "rest":
        from backend.app.services.rest_smart_plug import rest_smart_plug_service

        return await rest_smart_plug_service.get_energy(plug)
    else:
        return await tasmota_service.get_energy(plug)


async def _record_energy_start(archive, printer_id: int, db, *, context: str = "") -> bool:
    """Capture the smart plug lifetime counter on the archive at print start.

    Persists `energy_start_kwh` on the archive row (#941) so per-print energy
    tracking survives a backend restart mid-print. The print-end handler reads
    this value back from the DB and computes the delta against the current
    plug counter.
    """
    _logger = logging.getLogger(__name__)
    try:
        plug_result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
        plug = plug_result.scalar_one_or_none()
        if not plug:
            _logger.info("[ENERGY] No smart plug for printer %s (archive %s)", printer_id, archive.id)
            return False
        energy = await _get_plug_energy(plug, db)
        if not energy or energy.get("total") is None:
            _logger.warning("[ENERGY] No 'total' in energy response for archive %s", archive.id)
            return False
        archive.energy_start_kwh = float(energy["total"])
        await db.commit()
        _logger.info(
            "[ENERGY] Recorded starting energy%s for archive %s: %s kWh",
            f" ({context})" if context else "",
            archive.id,
            energy["total"],
        )
        return True
    except Exception as e:
        _logger.warning("[ENERGY] Failed to record starting energy for archive %s: %s", archive.id, e)
        return False


def register_expected_print(
    printer_id: int,
    filename: str,
    archive_id: int,
    ams_mapping: list[int] | None = None,
    created_by_id: int | None = None,
    plate_id: int | None = None,
):
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
    # Store plate_id for usage tracking when this is a single-plate dispatch from
    # a multi-plate 3MF — without this, the direct-Print path attributes the whole
    # file's filament total to the spool instead of just the printed plate (#1697).
    if plate_id is not None:
        _print_plate_ids[archive_id] = plate_id
    # Store created_by_id so the user start email can be sent even when the archive
    # itself has no created_by_id (e.g. library-file-based queue prints)
    if created_by_id is not None:
        _expected_print_creators[(printer_id, filename)] = created_by_id
        if filename.endswith(".3mf"):
            base = filename[:-4]
            _expected_print_creators[(printer_id, base)] = created_by_id
            _expected_print_creators[(printer_id, f"{base}.gcode")] = created_by_id
    # Record registration time for TTL-based eviction
    _registered_at = time.monotonic()
    _expected_print_registered_at[(printer_id, filename)] = _registered_at
    if filename.endswith(".3mf"):
        base = filename[:-4]
        _expected_print_registered_at[(printer_id, base)] = _registered_at
        _expected_print_registered_at[(printer_id, f"{base}.gcode")] = _registered_at
    logging.getLogger(__name__).info(
        f"Registered expected print: printer={printer_id}, file={filename}, archive={archive_id}, ams_mapping={ams_mapping}, plate_id={plate_id}"
    )


def _compute_run_filament_grams(
    status: str,
    archive_filament_used_grams: float | None,
    progress: float | int | None,
    usage_results: list[dict] | None,
) -> float | None:
    """Per-run filament for PrintLogEntry, partial- and tracker-aware (#1378, #1390).

    Priority for every status:
        1. Sum of tracked spool deltas in ``usage_results`` (AMS-measured
           weight delta — same source that drives "Total Consumed" on the
           Inventory page, so Stats and Inventory totals stay aligned).
        2. For ``completed``: the slicer estimate (no tracker available, fall
           back to the canonical "this print used X" value).
        3. For partial statuses: ``estimate * progress%``.
        4. ``None`` if nothing is known.
    """
    tracked_grams = sum(r.get("weight_used") or 0 for r in (usage_results or []))
    if tracked_grams > 0:
        return round(tracked_grams, 1)

    if status == "completed":
        return archive_filament_used_grams

    if archive_filament_used_grams:
        scale = max(0.0, min(((progress or 0) / 100.0), 1.0))
        if scale > 0:
            return round(archive_filament_used_grams * scale, 1)

    return None


def _get_start_ams_mapping(data: dict, archive_id: int | None) -> list[int] | None:
    """Resolve AMS mapping for print start without consuming stored queue/reprint state."""
    stored_ams_mapping = data.get("ams_mapping")
    if not stored_ams_mapping and archive_id:
        stored_ams_mapping = _print_ams_mappings.get(archive_id)
    return stored_ams_mapping


def _get_start_plate_id(archive_id: int | None) -> int | None:
    """Resolve plate_id for print start without consuming stored direct-Print state.

    Direct-Print of a single plate from a multi-plate 3MF registers plate_id in
    ``_print_plate_ids`` at dispatch time; this lets the spoolman / usage tracker
    read it back at print-start without popping (the entry is popped on print
    completion or TTL eviction, mirroring ``_print_ams_mappings``).
    """
    if archive_id is None:
        return None
    return _print_plate_ids.get(archive_id)


def _partial_progress_scale(progress: int | float | None) -> float:
    """Clamp ``progress / 100`` into [0.0, 1.0] for partial-print scaling.

    Used by every site that multiplies a "would-have-used" slicer estimate
    down to "actually-used" for failed / cancelled / stopped prints. Centralised
    so the three sites in ``_background_notifications`` (and the per-plate
    override helper) can't drift apart on the coercion shape.
    """
    return max(0.0, min((progress or 0) / 100.0, 1.0))


def _scope_notification_archive_data_to_plate(
    archive_data: dict,
    archive_file_path: str | None,
    plate_id: int | None,
    print_status: str,
    progress: int | float | None,
    base_dir: Path,
) -> dict:
    """Override summed-across-plates totals in ``archive_data`` with the values
    for ``plate_id`` so the completion notification reports what was actually
    printed, not the whole project (#1785).

    The 3MF parser at services/archive.py:200-264 sums ``prediction`` and
    ``weight`` across every plate of a multi-plate file (#1593) — correct for
    the archive card's "whole project" headline, wrong for the completion
    notification of a single-plate print. The queue UI already re-reads the
    3MF per-plate at print_queue.py:272-285; this helper mirrors that for the
    notification payload (filament grams, time estimate, per-slot breakdown).

    No-ops when ``plate_id`` is None, the file is missing, or the 3MF carries
    no per-plate values — in every fail case the original ``archive_data`` is
    returned unchanged so the notification still sends.
    """
    if plate_id is None or not archive_file_path:
        return archive_data

    from backend.app.utils.threemf_tools import (
        extract_filament_usage_from_3mf,
        extract_print_time_from_3mf,
    )

    archive_path = base_dir / archive_file_path
    if not archive_path.exists():
        return archive_data

    plate_slots = extract_filament_usage_from_3mf(archive_path, plate_id)
    plate_grams = sum(f.get("used_g", 0) for f in plate_slots)
    plate_time = extract_print_time_from_3mf(archive_path, plate_id)

    scale = 1.0 if print_status == "completed" else _partial_progress_scale(progress)

    if plate_time:
        archive_data["print_time_seconds"] = plate_time

    # Gate both the grams headline AND the per-slot breakdown on the same
    # `plate_grams > 0` signal: if the 3MF carries per-plate filament rows but
    # they all sum to zero (slicer bug / re-slice without estimate), drop back
    # to the project-level grams the archive columns already provide rather
    # than ship a project-level headline next to an all-zero per-plate
    # breakdown.
    if plate_grams > 0:
        archive_data["actual_filament_grams"] = round(plate_grams * scale, 1)
        archive_data["filament_slots"] = [
            {
                "slot_id": s.get("slot_id"),
                "used_g": round((s.get("used_g") or 0) * scale, 1),
                "type": s.get("type", ""),
                "color": s.get("color", ""),
            }
            for s in plate_slots
        ]

    return archive_data


def _extract_filament_data_from_mqtt(data: dict, ams_mapping: list[int] | None = None) -> dict[str, str]:
    """Best-effort filament metadata from the MQTT print-start snapshot.

    Used when the 3MF can't be downloaded (P1S/A1/P2S firmwares lock the
    file during print, see #1533) so the fallback PrintArchive still has
    enough filament info to support the inventory views and AMS-expansion
    planning the operator opens it for. Returns a dict with optional
    ``filament_type`` and ``filament_color`` keys in the same
    comma-separated format the 3MF extractor produces, so the rest of the
    codebase treats the fallback archive identically to a normal one.

    ``ams_mapping`` is the slicer's slot-per-print-filament list captured
    from the MQTT print payload (global tray IDs, possibly -1 for VT-tray
    entries). When supplied, only the slots actually consumed by this
    print contribute. Without it the function falls back to every loaded
    AMS slot — less accurate but still useful.

    Accepts both the raw inner payload (``{"ams": {"ams": [...]}, ...}``)
    that the unit tests pass directly, AND the on_print_start callback
    shape (``{"raw_data": {"ams": {"ams": [...]}, ...}, ...}``) the
    bambu_mqtt service hands to main.py at runtime. The original
    ``_extract_filament_data_from_mqtt(data)`` shipped in #1533 only
    handled the inner shape and silently returned ``{}`` for every real
    print start, leaving fallback archives' filament fields NULL — the
    exact regression the fix was meant to close. Reported with a log
    proving the AMS state was right there at
    ``data["raw_data"]["ams"]["ams"][0]["tray"][0]`` (#1533 follow-up).
    """
    result: dict[str, str] = {}
    # Look at the on_print_start wrapper first, then the inner shape.
    raw_data = (data or {}).get("raw_data")
    ams_root = (raw_data or {}).get("ams") if isinstance(raw_data, dict) else None
    if not isinstance(ams_root, dict):
        ams_root = (data or {}).get("ams") or {}
    ams_units = ams_root.get("ams") if isinstance(ams_root, dict) else None
    if not isinstance(ams_units, list) or not ams_units:
        return result

    # Map global tray id (unit * 4 + tray) → (type, color).
    loaded: dict[int, tuple[str, str]] = {}
    for unit in ams_units:
        if not isinstance(unit, dict):
            continue
        try:
            unit_id = int(unit.get("id", 0))
        except (TypeError, ValueError):
            continue
        for tray in unit.get("tray") or []:
            if not isinstance(tray, dict):
                continue
            try:
                tray_id = int(tray.get("id", 0))
            except (TypeError, ValueError):
                continue
            ttype = (tray.get("tray_type") or "").strip()
            tcolor = (tray.get("tray_color") or "").strip().upper()
            if not ttype:
                continue  # Empty / unloaded slot.
            loaded[unit_id * 4 + tray_id] = (ttype, tcolor)

    if not loaded:
        return result

    if ams_mapping:
        used_ids = [int(x) for x in ams_mapping if isinstance(x, (int, float)) and int(x) >= 0]
        filaments = [loaded[g] for g in used_ids if g in loaded]
        if not filaments:
            return result  # Mapping points entirely at slots we have no data for.
    else:
        filaments = [loaded[g] for g in sorted(loaded.keys())]

    types_joined = ",".join(f[0] for f in filaments)
    colors_joined = ",".join(f[1] for f in filaments if f[1])

    # Column limits per backend/app/models/archive.py: filament_type=50,
    # filament_color=200.
    if types_joined:
        result["filament_type"] = types_joined[:50]
    if colors_joined:
        result["filament_color"] = colors_joined[:200]
    return result


def _maybe_start_layer_timelapse(printer, printer_id: int, archive_id: int) -> bool:
    """Start a layer-timelapse session for *archive_id* when the printer has
    an external camera configured. Returns True if a session was started.

    Three call sites in on_print_start (expected-archive promotion, fallback
    archive creation, fresh-archive creation) used to inline this same
    if-block; the inline copies kept drifting (#1353 fixed only one of them
    on the first pass). Centralising the conditional + call here makes the
    contract testable in isolation and keeps the three sites locked in step.
    """
    if not (printer.external_camera_enabled and printer.external_camera_url):
        return False
    from backend.app.services.layer_timelapse import start_session

    start_session(
        printer_id,
        archive_id,
        printer.external_camera_url,
        printer.external_camera_type or "mjpeg",
        snapshot_url=printer.external_camera_snapshot_url,
    )
    logging.getLogger(__name__).info("Started layer timelapse for printer %s, archive %s", printer_id, archive_id)
    return True


def _format_hms_error_summary(hms_errors: list[dict]) -> str | None:
    """Build a human-readable failure reason from MQTT hms_errors for PrintQueueItem.error_message.

    Each entry has keys: code ('0x4038'), attr (32-bit int), module, severity.
    The short code used for the hms_errors.py lookup table is 'MMMM_EEEE' — module
    from attr bits 16-31, error from the numeric part of code. Falls back to the raw
    short code when no description is on file. Returns None for an empty list so
    callers can leave error_message unset.
    """
    if not hms_errors:
        return None
    from backend.app.services.hms_errors import get_error_description

    parts: list[str] = []
    for err in hms_errors:
        try:
            code_str = str(err.get("code", "")).replace("0x", "")
            error_num = int(code_str, 16) if code_str else 0
            module_num = (int(err.get("attr", 0)) >> 16) & 0xFFFF
            short_code = f"{module_num:04X}_{error_num:04X}"
        except (TypeError, ValueError):
            continue
        description = get_error_description(short_code)
        parts.append(f"[{short_code}] {description}" if description else f"[{short_code}]")
    return "; ".join(parts) if parts else None


async def _bump_library_file_usage_if_completed(db, item, queue_status: str) -> None:
    """Increment LibraryFile.print_count and stamp last_printed_at when a queued
    print completes successfully. Gated to status=='completed': failed, cancelled
    and aborted prints do not count as usage. Caller is responsible for committing
    the session. No-op when the queue item has no linked library file (e.g. reprints
    from an archive). See #1008."""
    if queue_status != "completed" or item.library_file_id is None:
        return
    from backend.app.models.library import LibraryFile

    lib_file = await db.scalar(select(LibraryFile).where(LibraryFile.id == item.library_file_id))
    if lib_file is None:
        return
    lib_file.print_count = (lib_file.print_count or 0) + 1
    lib_file.last_printed_at = datetime.now(timezone.utc)


def mark_printer_stopped_by_user(printer_id: int) -> None:
    """Mark that the active print on this printer was stopped by the user from the queue UI.

    When on_print_complete fires with status 'failed' for a printer in this set we
    reclassify it as 'cancelled' so the correct 'print stopped' notification is sent
    rather than a 'print failed' notification.
    """
    _user_stopped_printers.add(printer_id)
    logging.getLogger(__name__).info("Marked printer %s as user-stopped from queue", printer_id)


_last_status_broadcast: dict[int, str] = {}
# Track printers where we've updated nozzle_count
_nozzle_count_updated: set[int] = set()


async def _maybe_notify_printer_offline(printer_id: int) -> None:
    """Wait the debounce window then fire `on_printer_offline` if the printer
    is still offline.

    Scheduled by `on_printer_status_change` on the connected → disconnected
    edge (#1752). Cancelled by the same handler if the printer reconnects
    before the window elapses, so a single MQTT blip + recovery doesn't
    notify. Both the staleness-detector path (`bambu_mqtt.py::check_staleness`)
    and the smart-plug power-off path (`printer_manager.mark_printer_offline`)
    route through the same status-change callback, so this covers both.
    """
    logger = logging.getLogger(__name__)
    try:
        await asyncio.sleep(_PRINTER_OFFLINE_NOTIFY_DEBOUNCE_SECONDS)
        still_offline = not printer_manager.is_connected(printer_id)
        logger.info(
            "[#1752] Printer %s offline debounce elapsed: still_offline=%s",
            printer_id,
            still_offline,
        )
        if not still_offline:
            return
        async with async_session() as db:
            from backend.app.models.printer import Printer

            result = await db.execute(select(Printer).where(Printer.id == printer_id))
            printer = result.scalar_one_or_none()
            if not printer:
                logger.warning(
                    "[#1752] Printer %s missing from DB at offline-notify time; skipping",
                    printer_id,
                )
                return
            logger.info(
                "[#1752] Dispatching on_printer_offline for printer %s (%s)",
                printer_id,
                printer.name,
            )
            await notification_service.on_printer_offline(printer_id, printer.name, db)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("Printer offline notification failed for printer %s: %s", printer_id, e)
    finally:
        _printer_offline_notify_tasks.pop(printer_id, None)


async def on_printer_status_change(printer_id: int, state: PrinterState):
    """Handle printer status changes - broadcast via WebSocket."""
    # Connected-edge reconciliation (#1542 follow-up). When the printer
    # transitions disconnected → connected — which covers both Bambuddy
    # startup (no prior connection) and a mid-session MQTT reconnect — fire
    # `reconcile_stale_active_prints` exactly once for this connection so
    # any archive still in `status="printing"` that can't actually be
    # running anymore (printer IDLE / different subtask / empty subtask)
    # gets a synthesised PRINT COMPLETE. Without this, a print that
    # finished during a disconnect window + a smart-plug power cycle
    # leaves the .3mf on the SD card and the firmware ghost-replays it on
    # next boot. Reconciliation runs concurrently — it must not block the
    # WebSocket dedup / broadcast logic below, and the connected edge is
    # marked True BEFORE the await so concurrent status updates inside
    # the same connection don't re-trigger reconciliation.
    #
    # Wait for a real push_status before reconciling (#1679): MQTT
    # `_on_connect` broadcasts `state` IMMEDIATELY after the broker accepts
    # the connection, BEFORE `_request_push_all` round-trips. At that
    # instant the `PrinterState` is still on construction defaults — most
    # importantly `state.state == "unknown"` and `state.subtask_name == ""`.
    # If reconcile spawns here, every in-flight archive falls through to
    # the empty-subtask_name trigger and gets synthesised `aborted`, which
    # creates a duplicate archive on the real PRINT COMPLETE and
    # double-counts filament. Gating on `state.state ∉ ("", "unknown")`
    # keeps the #1542 mechanism intact: once the first real push_status
    # updates `state.state` (RUNNING / IDLE / FINISH / …), this handler
    # fires again with the flag still False — reconcile then runs against
    # actual evidence.
    state_known = bool(state.state) and state.state.upper() not in ("", "UNKNOWN")
    if state.connected and state_known and not _printer_reconciled_since_connect.get(printer_id, False):
        _printer_reconciled_since_connect[printer_id] = True
        spawn_background_task(
            reconcile_stale_active_prints(printer_id),
            name=f"reconcile-stale-prints-{printer_id}",
        )
    elif not state.connected and _printer_reconciled_since_connect.get(printer_id, False):
        # Re-arm so the next reconnect triggers reconciliation again.
        _printer_reconciled_since_connect[printer_id] = False

    # Offline-notification edge (#1752): schedule `on_printer_offline` on
    # connected → disconnected. The "back online" channel is already covered
    # by the print-failure notification (firmware reports gcode_state=FAILED
    # on reconnect of an interrupted print), so we don't add a symmetric
    # online event here.
    prev_connected = _printer_last_connected.get(printer_id)
    _printer_last_connected[printer_id] = state.connected
    if prev_connected is True and not state.connected:
        existing = _printer_offline_notify_tasks.get(printer_id)
        if existing is None or existing.done():
            logging.getLogger(__name__).info(
                "[#1752] Printer %s connected→disconnected edge; scheduling offline notification in %.0fs",
                printer_id,
                _PRINTER_OFFLINE_NOTIFY_DEBOUNCE_SECONDS,
            )
            _printer_offline_notify_tasks[printer_id] = asyncio.create_task(
                _maybe_notify_printer_offline(printer_id),
                name=f"printer-offline-notify-{printer_id}",
            )
    elif state.connected:
        pending = _printer_offline_notify_tasks.pop(printer_id, None)
        if pending is not None and not pending.done():
            logging.getLogger(__name__).info(
                "[#1752] Printer %s reconnected before debounce; cancelling pending offline notification",
                printer_id,
            )
            pending.cancel()

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
    # Include AMS dry_time and tray state values so drying/slot changes trigger broadcasts
    ams_dry_key = tuple(a.get("dry_time", 0) for a in (state.raw_data.get("ams") or [])) if state.raw_data else ()
    # Include tray states so load/unload transitions (state 11→10) trigger broadcasts (#784)
    ams_tray_key = (
        tuple(
            (t.get("id"), t.get("tray_type", ""), t.get("state"))
            for a in (state.raw_data.get("ams") or [])
            for t in a.get("tray", [])
        )
        if state.raw_data
        else ()
    )
    status_key = (
        f"{state.connected}:{state.state}:{state.progress}:{state.layer_num}:"
        f"{nozzle_temp}:{bed_temp}:{nozzle_2_temp}:{chamber_temp}:"
        f"{state.stg_cur}:{bed_target}:{nozzle_target}:"
        f"{state.cooling_fan_speed}:{state.big_fan1_speed}:{state.big_fan2_speed}:"
        f"{state.chamber_light}:{state.active_extruder}:{state.tray_now}:{vt_tray_key}:"
        f"{ams_dry_key}:{ams_tray_key}:{state.door_open}:{state.ams_filament_backup}"
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
                from backend.app.models.printer import Printer

                # Read the printer in a short session and release the connection
                # BEFORE the ~15s camera snapshot below — holding it across the grab
                # pinned a pooled connection per milestone, per printer (issue #2572).
                async with async_session() as db:
                    result = await db.execute(select(Printer).where(Printer.id == printer_id))
                    printer = result.scalar_one_or_none()

                printer_name = printer.name if printer else f"Printer {printer_id}"
                filename = state.subtask_name or state.gcode_file or "Unknown"
                # remaining_time is in minutes, convert to seconds for notification
                remaining_time_seconds = state.remaining_time * 60 if state.remaining_time else None

                # Capture camera snapshot for notification image attachment (no DB held).
                image_data = await _capture_snapshot_for_notification(printer_id, printer, logging.getLogger(__name__))

                # Notification send needs a session (provider/template lookups).
                async with async_session() as db:
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
        _first_layer_notified[printer_id] = False

    # HMS error codes that should not trigger notifications even though they
    # have known descriptions (e.g. user-initiated actions, not real errors).
    _HMS_NOTIFICATION_SUPPRESS = {
        "0500_400E",  # Printing was cancelled (user action, not an error)
    }

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
        _hms_last_seen[printer_id] = time.time()

        if new_error_codes:
            # Get the actual new errors for the notification
            # Filter to severity >= 2 (skip informational/status messages like H2D sends)
            new_errors = [e for e in current_hms_errors if f"{e.attr:08x}" in new_error_codes and e.severity >= 2]

            try:
                from backend.app.models.printer import Printer

                # Read the printer in a short session and release the connection
                # BEFORE the ~15s camera snapshot below (issue #2572).
                async with async_session() as db:
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

                # Capture camera snapshot once for all error notifications (no DB held).
                error_image_data = await _capture_snapshot_for_notification(
                    printer_id, printer, logging.getLogger(__name__)
                )

                # Notification sends need a session (provider/template lookups).
                async with async_session() as db:
                    sent_count = 0
                    for error in new_errors:
                        module_name = module_names.get(error.module, f"Module 0x{error.module:02X}")
                        # Build short code like "0700_8010"
                        # Mask to 16 bits to handle printers that send larger values
                        error_code_int = int(error.code.replace("0x", ""), 16) if error.code else 0
                        error_code_masked = error_code_int & 0xFFFF
                        short_code = f"{(error.attr >> 16) & 0xFFFF:04X}_{error_code_masked:04X}"

                        # Only notify for errors with known descriptions — printers
                        # send many undocumented/phantom codes that aren't real errors.
                        description = get_error_description(short_code)
                        if not description or short_code in _HMS_NOTIFICATION_SUPPRESS:
                            continue

                        error_type = f"{module_name} Error"
                        error_detail = description

                        await notification_service.on_printer_error(
                            printer_id, printer_name, error_type, db, error_detail, image_data=error_image_data
                        )
                        sent_count += 1

                    if sent_count:
                        logging.getLogger(__name__).info(
                            f"[HMS] Sent notification for {sent_count} error(s) on printer {printer_id}"
                        )

                # Also publish to MQTT relay (no DB).
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
        # No HMS errors — only clear tracking after a grace period to prevent
        # flapping errors (brief hms:[] gaps) from re-triggering notifications.
        # Some HMS codes (e.g. chamber temp regulation during PETG prints) toggle
        # on/off every few seconds as conditions fluctuate around thresholds.
        if printer_id in _notified_hms_errors:
            last_seen = _hms_last_seen.get(printer_id, 0)
            if time.time() - last_seen >= _HMS_CLEAR_GRACE_SECONDS:
                _notified_hms_errors.pop(printer_id, None)
                _hms_last_seen.pop(printer_id, None)

    await ws_manager.send_printer_status(
        printer_id,
        printer_state_to_dict(
            state,
            printer_id,
            printer_manager.get_model(printer_id),
            printer_manager.get_drying_targets(printer_id),
        ),
    )


def _is_bambu_uuid(tray_uuid: str) -> bool:
    """Check if a tray UUID looks like a valid Bambu Lab RFID UUID (non-empty, non-zero)."""
    return bool(tray_uuid) and tray_uuid not in ("", "0" * len(tray_uuid))


async def on_ams_change(printer_id: int, ams_data: list):
    """Handle AMS data changes - sync to Spoolman if enabled and auto mode."""
    logger = logging.getLogger(__name__)

    # Snapshot BEFORE any await: if a print is active, skip weight sync later.
    # on_print_complete may pop _active_sessions during our awaits (#880).
    from backend.app.services.usage_tracker import _active_sessions

    _print_active = printer_id in _active_sessions

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
                printer_state_to_dict(
                    state,
                    printer_id,
                    printer_manager.get_model(printer_id),
                    printer_manager.get_drying_targets(printer_id),
                ),
            )
    except Exception as e:
        logger.warning("Failed to broadcast AMS change for printer %s: %s", printer_id, e)

    from backend.app.utils.color_utils import colors_similar as _colors_similar

    # Auto-unlink spool assignments with stale fingerprints
    try:
        async with async_session() as db:
            from sqlalchemy.orm import selectinload

            from backend.app.api.routes.inventory import _find_tray_in_ams_data
            from backend.app.models.spool import Spool as _Spool
            from backend.app.models.spool_assignment import SpoolAssignment as SA

            result = await db.execute(
                select(SA)
                .where(SA.printer_id == printer_id)
                .options(selectinload(SA.spool).selectinload(_Spool.k_profiles))
            )
            stale = []
            for assignment in result.scalars().all():
                # External spool assignments (ams_id=255) live in vt_tray, not AMS data
                if assignment.ams_id == 255:
                    ps = printer_manager.get_status(printer_id)
                    vt_tray_raw = ps.raw_data.get("vt_tray", []) if ps else []
                    ext_id = assignment.tray_id + 254  # 0→254, 1→255
                    current_tray = None
                    for vt in vt_tray_raw:
                        if isinstance(vt, dict) and int(vt.get("id", 254)) == ext_id:
                            current_tray = vt
                            break
                    if not current_tray:
                        # vt_tray data may not have arrived yet — keep assignment
                        continue
                else:
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
                    cur_state = current_tray.get("state")
                    fp_color = assignment.fingerprint_color or ""
                    fp_type = assignment.fingerprint_type or ""

                    # SpoolBuddy pre-config replay: fingerprint_type empty means
                    # the slot was empty when the user pre-assigned via SpoolBuddy
                    # (the firmware drops ams_filament_setting on empty slots, so
                    # MQTT was deferred). The moment any filament gets inserted
                    # — Bambu RFID, 3rd-party, or even an existing-but-now-
                    # reconfigured spool — fire the deferred configuration.
                    # The "loaded" signal is state == 11 (Bambu's "filament fed to
                    # extruder" code) OR, on firmwares that don't use the state
                    # enum meaningfully, a non-empty tray_type when state is
                    # NOT one of the firmware's explicit empty signals (9, 10).
                    # state-only was wrong for firmwares that never set 11 — A1
                    # Mini BMCU 01.07.02.00 and P1S Standard AMS 00.00.06.75 both
                    # always report state=3 — so the replay never fired for them
                    # (#1322). The state ∉ {9,10} guard keeps the firmware's
                    # explicit "empty" signals authoritative over any stale
                    # tray_type that might survive the relay's auto-clearing.
                    loaded = cur_state == 11 or (cur_state not in (9, 10) and cur_type.strip())
                    if not fp_type.strip() and loaded and assignment.spool:
                        try:
                            from backend.app.api.routes.inventory import (
                                apply_spool_to_slot_via_mqtt,
                            )

                            await apply_spool_to_slot_via_mqtt(
                                db=db,
                                current_user=None,
                                spool=assignment.spool,
                                printer_id=printer_id,
                                ams_id=assignment.ams_id,
                                tray_id=assignment.tray_id,
                                current_tray_info_idx=current_tray.get("tray_info_idx", ""),
                                current_tray_type=cur_type,
                            )
                            logger.info(
                                "SpoolBuddy pre-config applied on insert: spool %d → printer %d AMS%d-T%d",
                                assignment.spool_id,
                                printer_id,
                                assignment.ams_id,
                                assignment.tray_id,
                            )
                        except Exception:
                            logger.exception(
                                "Pre-config apply failed for spool %d on printer %d AMS%d-T%d",
                                assignment.spool_id,
                                printer_id,
                                assignment.ams_id,
                                assignment.tray_id,
                            )
                        assignment.fingerprint_color = cur_color
                        assignment.fingerprint_type = cur_type
                        continue

                    if not _colors_similar(cur_color, fp_color) or cur_type.upper() != fp_type.upper():
                        # Fingerprint mismatch — but check if tray now matches the
                        # assigned spool (e.g. auto-configure changed the tray).
                        spool = assignment.spool
                        if spool:
                            spool_color = (spool.rgba or "FFFFFFFF").upper()
                            spool_type = (spool.material or "").upper()
                            if _colors_similar(cur_color, spool_color) and cur_type.upper() == spool_type:
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
        logger.warning("Spool assignment cleanup failed: %s", e, exc_info=True)

    # Auto-manage inventory spools from AMS tray data (skip if Spoolman manages AMS).
    # Serialised per-printer via _ams_assignment_locks: MQTT bursts can deliver
    # two AMS pushes ~30 ms apart, and without the lock both callbacks read
    # "no existing assignment" for the same (printer, ams, tray) and race to
    # INSERT, hitting the spool_assignment_printer_id_ams_id_tray_id_key
    # unique constraint on Postgres. SQLite's WAL serialises writes so the
    # bug stayed latent there. See _ams_assignment_locks comment for details.
    try:
        async with _get_ams_assignment_lock(printer_id), async_session() as db:
            from backend.app.api.routes.settings import get_setting
            from backend.app.models.spool import Spool
            from backend.app.models.spool_assignment import SpoolAssignment as SA
            from backend.app.services.spool_tag_matcher import (
                auto_assign_spool,
                create_spool_from_tray,
                find_matching_untagged_spool,
                get_spool_by_tag,
                is_bambu_tag,
                is_valid_tag,
                link_tag_to_inventory_spool,
            )

            _spoolman_on = await get_setting(db, "spoolman_enabled")
            _auto_add_raw = await get_setting(db, "auto_add_unknown_rfid")
            _auto_add_unknown = _auto_add_raw is None or _auto_add_raw.lower() == "true"
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
                            # Slot reported empty — drop any cached unknown-tag
                            # broadcast so reinserting the same spool re-prompts.
                            _clear_unknown_tag_dedup(printer_id, ams_id, tray_id)
                            continue  # Empty slot
                        # Check if assignment already exists for this slot
                        existing = await db.execute(
                            select(SA)
                            .options(selectinload(SA.spool).selectinload(Spool.k_profiles))
                            .where(SA.printer_id == printer_id, SA.ams_id == ams_id, SA.tray_id == tray_id)
                        )
                        existing_assignment = existing.scalar_one_or_none()
                        if existing_assignment:
                            # Sync spool weight_used from AMS remain — only INCREASE, never decrease.
                            # The AMS remain% is low-resolution (integer %, i.e. 10g steps for 1kg spool)
                            # and must not overwrite precise values from the usage tracker (3MF/G-code).
                            # Skip during active prints: the usage tracker handles deduction
                            # precisely via 3MF data on print completion. Without this guard the
                            # AMS remain% SET and the usage tracker ADD both fire from the same
                            # MQTT message, doubling the deduction (#880).
                            if _print_active:
                                continue
                            remain_raw = tray.get("remain")
                            if (
                                remain_raw is not None
                                and existing_assignment.spool
                                and not existing_assignment.spool.weight_locked
                            ):
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

                            # Re-apply stored K-profile when the live tray's
                            # cali_idx drifted from the spool's stored profile.
                            # This catches "reset slot → re-read" and any other
                            # path where the firmware loses the user's K-profile
                            # selection while the SpoolAssignment row persists.
                            # Per the maintainer's rule: any time a spool tag is
                            # identified and matches inventory, the slot must be
                            # configured with the spool's stored settings. Without
                            # this block the existing-assignment branch only ran
                            # weight-sync and let the firmware-default cali_idx win.
                            try:
                                spool = existing_assignment.spool
                                if (
                                    spool is not None
                                    and is_bambu_tag(tag_uid, tray_uuid, tray_info_idx)
                                    and spool.k_profiles
                                ):
                                    state = printer_manager.get_status(printer_id)
                                    nozzle_diameter = "0.4"
                                    if state and state.nozzles:
                                        nd = state.nozzles[0].nozzle_diameter
                                        if nd:
                                            nozzle_diameter = nd
                                    slot_extruder: int | None = None
                                    if state and state.ams_extruder_map:
                                        if ams_id == 255:
                                            slot_extruder = 1 - tray_id
                                        else:
                                            slot_extruder = state.ams_extruder_map.get(str(ams_id))
                                    # Prefer exact extruder match, fall back to
                                    # extruder-agnostic kp for the same printer +
                                    # nozzle. Avoids hard-skipping when the AMS is
                                    # mapped differently than at calibration time.
                                    matching_kp = None
                                    fallback_kp = None
                                    for kp in spool.k_profiles:
                                        if (
                                            kp.printer_id != printer_id
                                            or kp.nozzle_diameter != nozzle_diameter
                                            or kp.cali_idx is None
                                        ):
                                            continue
                                        if (
                                            slot_extruder is not None
                                            and kp.extruder is not None
                                            and kp.extruder == slot_extruder
                                        ):
                                            matching_kp = kp
                                            break
                                        if fallback_kp is None:
                                            fallback_kp = kp
                                    chosen_kp = matching_kp or fallback_kp
                                    if chosen_kp is not None:
                                        live_cali_idx = tray.get("cali_idx")
                                        # Only fire MQTT when the printer's live
                                        # cali_idx differs from the stored value.
                                        # Avoids spamming the broker on every
                                        # MQTT push during steady-state operation.
                                        if live_cali_idx != chosen_kp.cali_idx:
                                            client = printer_manager.get_client(printer_id)
                                            if client:
                                                cali_filament_id = spool.slicer_filament or tray_info_idx or ""
                                                client.extrusion_cali_sel(
                                                    ams_id=ams_id,
                                                    tray_id=tray_id,
                                                    cali_idx=chosen_kp.cali_idx,
                                                    filament_id=cali_filament_id,
                                                    nozzle_diameter=nozzle_diameter,
                                                )
                                                logger.info(
                                                    "Re-applied K-profile cali_idx=%d for spool %d "
                                                    "on printer %d AMS%d-T%d (live=%s drift detected)",
                                                    chosen_kp.cali_idx,
                                                    spool.id,
                                                    printer_id,
                                                    ams_id,
                                                    tray_id,
                                                    live_cali_idx,
                                                )
                            except Exception:
                                logger.exception(
                                    "K-profile re-apply failed for printer %d AMS%d-T%d",
                                    printer_id,
                                    ams_id,
                                    tray_id,
                                )
                            continue

                        if is_bambu_tag(tag_uid, tray_uuid, tray_info_idx):
                            # BL spool with RFID tag: auto-match → inventory match → auto-create
                            spool = await get_spool_by_tag(db, tag_uid, tray_uuid)
                            if not spool:
                                # Try matching an untagged inventory spool (same material/color)
                                spool = await find_matching_untagged_spool(db, tray)
                                if spool:
                                    await link_tag_to_inventory_spool(db, spool, tray)
                                elif _auto_add_unknown:
                                    spool = await create_spool_from_tray(db, tray)
                                else:
                                    # Auto-add disabled: surface the slot so the
                                    # user can add it manually via the UI.
                                    await _broadcast_unknown_tag(
                                        printer_id=printer_id,
                                        ams_id=ams_id,
                                        tray_id=tray_id,
                                        tag_uid=tag_uid,
                                        tray_uuid=tray_uuid,
                                        tray_type=tray.get("tray_type"),
                                        tray_color=tray.get("tray_color"),
                                        tray_sub_brands=tray.get("tray_sub_brands"),
                                        tray_count=len(ams_unit.get("tray", [])),
                                    )
                                    continue
                            # Slot matched (existing tag, untagged inventory
                            # match, or freshly auto-created spool) — drop any
                            # stale dedup so a future tag swap re-prompts.
                            _clear_unknown_tag_dedup(printer_id, ams_id, tray_id)
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
                            await _broadcast_unknown_tag(
                                printer_id=printer_id,
                                ams_id=ams_id,
                                tray_id=tray_id,
                                tag_uid=tag_uid,
                                tray_uuid=tray_uuid,
                                tray_type=tray.get("tray_type"),
                                tray_color=tray.get("tray_color"),
                                tray_sub_brands=tray.get("tray_sub_brands"),
                                tray_count=len(ams_unit.get("tray", [])),
                            )
                        # No-tag slots (generic non-RFID filament) are left alone:
                        # nothing to identify, prompting "+ Add" would just create
                        # ghost spools with empty tags on every confirm.
    except Exception as e:
        logger.warning("RFID spool auto-assign failed: %s", e, exc_info=True)

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

            _auto_add_raw_sm = await get_setting(db, "auto_add_unknown_rfid")
            auto_add_unknown_rfid = _auto_add_raw_sm is None or _auto_add_raw_sm.lower() == "true"

            # `spoolman_disable_weight_sync` is deprecated (#1119) — weight is now
            # always owned by per-print tracking, never by AMS auto-sync. The
            # setting is still read by the settings UI for backwards compat but
            # has no effect on the sync path here.

            # Get Spoolman URL
            spoolman_url = await get_setting(db, "spoolman_url")
            if not spoolman_url:
                return

            # Get or create Spoolman client
            client = await get_spoolman_client()
            if not client:
                try:
                    client = await init_spoolman_client(spoolman_url)
                except ValueError as exc:
                    logger.warning("Spoolman URL %r rejected by SSRF guard: %s", spoolman_url, exc)
                    return

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
            from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

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
                logger.warning("Could not load inventory weights for printer %s: %s", printer_id, e)

            # Load existing Spoolman slot assignments for the no-RFID fallback path
            spoolman_slot_map: dict[tuple[int, int], int] = {}
            try:
                slot_result = await db.execute(
                    select(SpoolmanSlotAssignment).where(SpoolmanSlotAssignment.printer_id == printer_id)
                )
                for slot in slot_result.scalars().all():
                    spoolman_slot_map[(slot.ams_id, slot.tray_id)] = slot.spoolman_spool_id
            except Exception as e:
                logger.warning("Could not load Spoolman slot assignments for printer %s: %s", printer_id, e)

            # Sync each AMS tray and collect slot changes for DB persistence
            synced = 0
            slot_changes: list[tuple[int, int, int]] = []  # (ams_id, tray_id, spoolman_spool_id) to upsert
            empty_slots: list[tuple[int, int]] = []  # (ams_id, tray_id) whose tray is now empty
            for ams_unit in ams_data:
                if not isinstance(ams_unit, dict):
                    continue
                ams_id = int(ams_unit.get("id", 0))
                trays = ams_unit.get("tray", [])

                for tray_data in trays:
                    if not isinstance(tray_data, dict):
                        continue
                    tray_id_raw = int(tray_data.get("id", 0))
                    tray = client.parse_ams_tray(ams_id, tray_data)
                    if not tray:
                        # Empty tray slot — record for local assignment cleanup
                        # and drop any cached unknown-tag broadcast so a
                        # reinserted spool re-prompts.
                        empty_slots.append((ams_id, tray_id_raw))
                        _clear_unknown_tag_dedup(printer_id, ams_id, tray_id_raw)
                        continue

                    spool_tag = (
                        tray.tray_uuid
                        if tray.tray_uuid and tray.tray_uuid != "00000000000000000000000000000000"
                        else tray.tag_uid
                    )

                    # Provide the hint only when no RFID is available
                    hint = spoolman_slot_map.get((ams_id, tray.tray_id)) if not spool_tag else None

                    try:
                        inv_remaining = inventory_weights.get((ams_id, tray.tray_id))
                        result = await client.sync_ams_tray(
                            tray,
                            printer_name,
                            # Per-print tracking is the only weight writer (#1119).
                            # AMS auto-sync still maintains spool metadata / slot
                            # assignments but no longer touches remaining_weight.
                            disable_weight_sync=True,
                            cached_spools=cached_spools,
                            inventory_remaining=inv_remaining,
                            spoolman_spool_id_hint=hint,
                            auto_add_unknown_rfid=auto_add_unknown_rfid,
                        )
                        if result is None and spool_tag and not auto_add_unknown_rfid:
                            # Spoolman skipped auto-create per user setting — surface
                            # the slot so the UI can offer "+ Add to inventory".
                            await _broadcast_unknown_tag(
                                printer_id=printer_id,
                                ams_id=ams_id,
                                tray_id=tray.tray_id,
                                tag_uid=tray.tag_uid or "",
                                tray_uuid=tray.tray_uuid or "",
                                tray_type=tray.tray_type,
                                tray_color=tray.tray_color,
                                tray_sub_brands=tray.tray_sub_brands,
                                tray_count=len(trays),
                            )
                        elif result:
                            _clear_unknown_tag_dedup(printer_id, ams_id, tray.tray_id)
                        if result:
                            synced += 1
                            if result.get("id"):
                                slot_changes.append((ams_id, tray.tray_id, result["id"]))
                                # If a new spool was created, add it to the cache
                                # so subsequent trays can find it if they reference the same tag
                                spool_exists = any(s.get("id") == result["id"] for s in cached_spools)
                                if not spool_exists:
                                    cached_spools.append(result)
                                    logger.debug(
                                        "[Printer %s] Added newly created spool %s to cache",
                                        printer_id,
                                        result["id"],
                                    )
                                # Reconcile slot_preset_mappings (the same row internal
                                # mode keeps in sync via inventory + spool_tag_matcher).
                                # Without this the slot card surfaces the previous spool's
                                # preset name — same bug shape, different inventory mode.
                                from backend.app.services.slot_preset_writer import (
                                    upsert_slot_preset_for_spoolman_spool,
                                )

                                await upsert_slot_preset_for_spoolman_spool(
                                    db=db,
                                    spoolman_spool=result,
                                    tray_info_idx=tray.tray_info_idx or "",
                                    tray_sub_brands=tray.tray_sub_brands or "",
                                    tray_type=tray.tray_type or "",
                                    printer_id=printer_id,
                                    ams_id=ams_id,
                                    tray_id=tray.tray_id,
                                )
                    except Exception as e:
                        logger.error("Error syncing AMS %s tray %s: %s", ams_id, tray.tray_id, e)

            if synced > 0:
                logger.info("Auto-synced %s AMS trays to Spoolman for printer %s", synced, printer_id)

            # Persist slot assignment changes to the local table
            if slot_changes or empty_slots:
                try:
                    for ams_id, tray_id, spool_id in slot_changes:
                        await db.execute(
                            text(
                                "INSERT INTO spoolman_slot_assignments"
                                " (printer_id, ams_id, tray_id, spoolman_spool_id)"
                                " VALUES (:printer_id, :ams_id, :tray_id, :spool_id)"
                                " ON CONFLICT(printer_id, ams_id, tray_id)"
                                " DO UPDATE SET spoolman_spool_id = excluded.spoolman_spool_id"
                            ),
                            {
                                "printer_id": printer_id,
                                "ams_id": ams_id,
                                "tray_id": tray_id,
                                "spool_id": spool_id,
                            },
                        )
                    for ams_id, tray_id in empty_slots:
                        await db.execute(
                            delete(SpoolmanSlotAssignment).where(
                                SpoolmanSlotAssignment.printer_id == printer_id,
                                SpoolmanSlotAssignment.ams_id == ams_id,
                                SpoolmanSlotAssignment.tray_id == tray_id,
                            )
                        )
                    await db.commit()
                except Exception as e:
                    await db.rollback()
                    logger.error("Error persisting Spoolman slot assignments for printer %s: %s", printer_id, e)

    except Exception as e:
        logging.getLogger(__name__).error("Spoolman AMS sync failed for printer %s: %s", printer_id, e)


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

            frame_data = await capture_frame(
                printer.external_camera_url,
                printer.external_camera_type or "mjpeg",
                snapshot_url=printer.external_camera_snapshot_url,
            )
            if frame_data and len(frame_data) <= 2_500_000:
                logger.info("[SNAPSHOT] External camera frame: %s bytes", len(frame_data))
                return _apply_camera_rotation(frame_data, printer, logger)

        # Try buffered frame from active stream
        from backend.app.api.routes.camera import _active_chamber_streams, _active_streams, get_buffered_frame

        active_for_printer = [k for k in _active_streams if k.startswith(f"{printer_id}-")]
        active_chamber = [k for k in _active_chamber_streams if k.startswith(f"{printer_id}-")]
        buffered_frame = get_buffered_frame(printer_id)

        if (active_for_printer or active_chamber) and buffered_frame:
            logger.info("[SNAPSHOT] Using buffered frame for printer %s: %s bytes", printer_id, len(buffered_frame))
            if len(buffered_frame) <= 2_500_000:
                return _apply_camera_rotation(buffered_frame, printer, logger)

        # Fresh capture from printer camera
        logger.info("[SNAPSHOT] Capturing fresh frame for printer %s", printer_id)
        from backend.app.services.camera import capture_camera_frame_bytes

        frame_data = await capture_camera_frame_bytes(
            printer.ip_address, printer.access_code, printer.model, timeout=15
        )
        if frame_data and len(frame_data) <= 2_500_000:
            logger.info("[SNAPSHOT] Fresh camera frame: %s bytes", len(frame_data))
            return _apply_camera_rotation(frame_data, printer, logger)

    except Exception as e:
        logger.warning("[SNAPSHOT] Failed to capture snapshot for printer %s: %s", printer_id, e)

    return None


async def _maybe_bank_inprint_frame(printer_id: int, layer_num: int) -> None:
    """#1867: bank a recent in-print camera frame for the finish photo.

    Called on every layer change. Grabs one frame (throttled) into
    ``_inprint_frame_bank`` so the FINISH-state finish-photo path has a
    pre-swap image on firmware that never emits ``stg_cur=22``. Because it is
    driven by layer_num increases, banking stops the instant printing ends and
    the End G-code (e.g. SwapMod plate swap) runs — no further layer changes
    arrive — so the last banked frame is the finished print, not the swapped
    plate. Best-effort: any failure just leaves the previous banked frame.
    """
    logger = logging.getLogger(__name__)
    client = printer_manager.get_client(printer_id)
    state = client.state if client else None
    if not state or state.state != "RUNNING":
        return
    # Only during actual extrusion — firmware ticks layer_num during the
    # pre-print calibration sequence, whose sub-stages are non-zero.
    if state.mc_print_sub_stage not in (None, 0):
        return

    total = state.total_layers or 0
    is_last_layer = total > 0 and layer_num >= total
    now = time.monotonic()
    last = _inprint_frame_bank_ts.get(printer_id, 0.0)
    if not is_last_layer and (now - last) < _INPRINT_BANK_MIN_INTERVAL:
        return

    try:
        async with async_session() as db:
            from backend.app.models.printer import Printer

            result = await db.execute(select(Printer).where(Printer.id == printer_id))
            printer = result.scalar_one_or_none()
        if not printer:
            return
        # Reuses the notification snapshot path, which honours the
        # `capture_finish_photo` setting (returns None when disabled) so we
        # don't bank frames the user never asked for.
        frame = await _capture_snapshot_for_notification(printer_id, printer, logger)
        if frame:
            _inprint_frame_bank[printer_id] = frame
            _inprint_frame_bank_ts[printer_id] = now
            logger.debug(
                "[FINISH-PHOTO-BANK] banked in-print frame for printer %s at layer %s/%s (%d bytes)",
                printer_id,
                layer_num,
                total,
                len(frame),
            )
    except Exception as e:
        logger.debug("[FINISH-PHOTO-BANK] bank failed for printer %s: %s", printer_id, e)


def _apply_camera_rotation(image_data: bytes, printer, logger) -> bytes:
    """Apply camera rotation to snapshot image if configured."""
    rotation = getattr(printer, "camera_rotation", 0)
    if not rotation or rotation == 0:
        return image_data

    try:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(image_data))
        # PIL rotate is counter-clockwise, so negate for clockwise rotation
        img = img.rotate(-rotation, expand=True)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=90)
        rotated = buf.getvalue()
        logger.info("[SNAPSHOT] Applied %d° rotation: %s → %s bytes", rotation, len(image_data), len(rotated))
        return rotated
    except Exception as e:
        logger.warning("[SNAPSHOT] Failed to apply rotation: %s", e)
        return image_data


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

            # Send user-specific email notification for print start
            if archive_data and archive_data.get("created_by_id"):
                await notification_service.send_user_print_email(
                    event_type="user_print_start",
                    created_by_id=archive_data["created_by_id"],
                    printer_name=printer_name,
                    filename=data.get("subtask_name") or data.get("filename", "Unknown"),
                    db=db,
                )
    except Exception as e:
        logger.warning("Notification on_print_start failed: %s", e)


async def _dispatch_user_print_email(
    status: str,
    created_by_id: int | None,
    printer_name: str,
    filename: str,
    db,
) -> None:
    """Send a user-specific print-completion email based on print status.

    Maps the normalised print status to the correct event type and delegates
    to :meth:`NotificationService.send_user_print_email`.  A single helper
    avoids duplicating the ``if status == "completed" / elif "failed" / elif
    "stopped"`` dispatch block at every call site.

    Does nothing if *created_by_id* is ``None``.
    """
    if created_by_id is None:
        return
    if status == "completed":
        event_type = "user_print_complete"
    elif status == "failed":
        event_type = "user_print_failed"
    elif status in ("stopped", "aborted", "cancelled"):
        event_type = "user_print_stopped"
    else:
        return
    await notification_service.send_user_print_email(
        event_type=event_type,
        created_by_id=created_by_id,
        printer_name=printer_name,
        filename=filename,
        db=db,
    )


def _load_objects_from_archive(archive, printer_id: int, logger) -> None:
    """Extract printable objects from an archive's 3MF file and store in printer state."""
    try:
        from backend.app.services.archive import extract_printable_objects_from_3mf

        client = printer_manager.get_client(printer_id)
        if not client:
            return

        file_path = app_settings.base_dir / archive.file_path
        if file_path.is_file() and str(file_path).endswith(".3mf"):
            with open(file_path, "rb") as f:
                threemf_data = f.read()
            # Extract with positions for UI overlay, scoped to the plate that
            # is printing — resolve_plate_id is the same resolver /cover uses,
            # so the object list can't disagree with the thumbnail it is drawn
            # over (#2522).
            printable_objects, bbox_all = extract_printable_objects_from_3mf(
                threemf_data,
                plate_number=resolve_plate_id(client.state),
                include_positions=True,
            )
            if printable_objects:
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

    # Clear any stale user-stopped flag from previous print cycles
    _user_stopped_printers.discard(printer_id)

    # #1721: drop any leftover pre-captured finish frame from a prior print
    # so a never-consumed cache entry can't bleed into the new print's photo.
    _stage22_finish_frames.pop(printer_id, None)
    # #1867: same for the in-print frame bank — a queued print must not reuse
    # the previous job's banked frame.
    _inprint_frame_bank.pop(printer_id, None)
    _inprint_frame_bank_ts.pop(printer_id, None)

    # Cancel any active bed cooldown waiter for this printer
    if _bed_cool_waiters.pop(printer_id, None):
        logger.info("[BED-COOL] Cancelled bed cooldown waiter for printer %s (new print started)", printer_id)

    # Clear cached cover images so the new print's thumbnail is fetched fresh
    from backend.app.api.routes.printers import clear_cover_cache

    clear_cover_cache(printer_id)

    await ws_manager.send_print_start(printer_id, data)

    # Notify when the print-start AMS mapping references tray slots without spool assignments.
    await notify_missing_spool_assignments_on_print_start(printer_id, data, logger)

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

                await usage_on_print_start(printer_id, data, printer_manager, db=db)
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
            # Release the pooled DB connection before the plate-detection camera
            # work (a 2.5s light-settle sleep + FTP/camera capture). Only the
            # printer SELECT has run so far — nothing to persist — so this commit
            # is a data-noop that ends the read transaction and returns the
            # connection to the pool during the I/O (issue #2572). expire_on_commit
            # =False keeps printer.* readable; on_plate_not_empty (rare) and the
            # archive lookups below re-acquire a fresh connection on next execute.
            await db.commit()
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
                    external_camera_snapshot_url=printer.external_camera_snapshot_url,
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

        if not printer:
            logger.info("[CALLBACK] Skipping archive - printer not found in database")
            if not notification_sent:
                await _send_print_start_notification(printer_id, data, logger=logger)
            return

        if not printer.auto_archive:
            # auto-archive disabled — check if there's an expected print (dispatched
            # by BamBuddy via queue/reprint) that already has an archive to promote.
            # If so, fall through to the expected-print handling below so the archive
            # is tracked in _active_prints and usage tracking works at completion.
            _fn = data.get("filename", "")
            _sn = data.get("subtask_name", "")
            _check_keys: list[tuple[int, str]] = []
            if _sn:
                _check_keys += [
                    (printer_id, _sn),
                    (printer_id, f"{_sn}.3mf"),
                    (printer_id, f"{_sn}.gcode.3mf"),
                ]
            if _fn:
                _base_fn = _fn.split("/")[-1] if "/" in _fn else _fn
                _check_keys.append((printer_id, _base_fn))
                _no_archive_base = _base_fn.replace(".gcode", "").replace(".3mf", "")
                _check_keys += [
                    (printer_id, _no_archive_base),
                    (printer_id, f"{_no_archive_base}.3mf"),
                ]

            _has_expected = any(k in _expected_prints for k in _check_keys)

            if not _has_expected:
                # No expected print — truly external print (started from slicer/touchscreen)
                logger.info("[CALLBACK] Skipping archive - auto_archive: False, no expected print")
                if not notification_sent:
                    _no_archive_creator: int | None = None
                    for _key in _check_keys:
                        _expected_prints.pop(_key, None)
                        _expected_print_registered_at.pop(_key, None)
                        popped_creator = _expected_print_creators.pop(_key, None)
                        if _no_archive_creator is None:
                            _no_archive_creator = popped_creator
                    _creator_data = {"created_by_id": _no_archive_creator} if _no_archive_creator else None
                    await _send_print_start_notification(printer_id, data, _creator_data, logger)
                return
            else:
                logger.info("[CALLBACK] auto_archive disabled but expected print found — promoting archive")

        # Get the filename and subtask_name
        filename = data.get("filename", "")
        subtask_name = data.get("subtask_name", "")

        # MQTT subtask_id uniquely identifies a print job on the printer. When
        # present, it lets us match an archive across a backend restart (#972):
        # same id → same print → resume the existing row instead of cancelling
        # it and recreating from scratch (which loses started_at). Treat "0"
        # and "" as absent — Bambu reports "0" for non-cloud / local prints.
        raw_mqtt = data.get("raw_data") or {}
        subtask_id = raw_mqtt.get("subtask_id")
        if subtask_id is not None:
            subtask_id = str(subtask_id).strip()
            if subtask_id in ("", "0"):
                subtask_id = None

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
            _expected_print_registered_at.pop(key, None)
            if expected_archive_id:
                # Clean up other possible keys for this print
                for other_key in expected_keys:
                    _expected_prints.pop(other_key, None)
                    _expected_print_registered_at.pop(other_key, None)
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
                archive.started_at = datetime.now(timezone.utc)

                # Reprint of an archive reuses the source row. Without resetting
                # ``timelapse_path`` _scan_for_timelapse_with_retries early-returns
                # ("already has timelapse") and _capture_finish_photo_from_timelapse
                # extracts the *original* print's last frame, which then ships in
                # the completion notification (#1707). Clear the path so the
                # scanner runs fresh; also unlink the old video file so reprints
                # don't accumulate orphans in the archive directory. Photos list
                # is left alone — accumulating one finish photo per run is fine.
                stale_timelapse_relpath = archive.timelapse_path
                if stale_timelapse_relpath:
                    archive.timelapse_path = None
                    try:
                        stale_path = app_settings.base_dir / stale_timelapse_relpath
                        if stale_path.is_file():
                            stale_path.unlink()
                            logger.info(
                                "Deleted stale timelapse %s on reprint of archive %s",
                                stale_timelapse_relpath,
                                expected_archive_id,
                            )
                    except OSError as e:
                        logger.warning(
                            "Failed to delete stale timelapse %s on reprint: %s",
                            stale_timelapse_relpath,
                            e,
                        )
                # Persist a restart-stable id so a later restart resumes this
                # archive by subtask_id instead of name-matching + duplicating
                # it (#1485). The printer often hasn't echoed subtask_id back
                # this soon after dispatch, so fall back to the id Bambuddy
                # minted when it sent the print command. Scoped to this
                # expected-print branch on purpose: an expected match means
                # Bambuddy dispatched this exact print in this process, so the
                # client's last-dispatch id genuinely belongs to it — using it
                # for an externally-started print could mis-tag the archive.
                effective_subtask_id = subtask_id
                if not effective_subtask_id:
                    _client = printer_manager.get_client(printer_id)
                    _dispatched = getattr(_client, "last_dispatch_subtask_id", None) if _client else None
                    if _dispatched:
                        effective_subtask_id = str(_dispatched).strip() or None
                # Update on first-set OR on reprint (the queue dispatcher mints
                # a fresh subtask_id per dispatch in bambu_mqtt:3647). Skipping
                # the rewrite for reprints leaves the archive holding the FIRST
                # run's id; if MQTT then reconnects mid-print, the reconciler
                # (#1542) compares the stale stored id against the printer's
                # live id, sees a mismatch, and synthesises a bogus PRINT
                # COMPLETE — exactly the false-positive "Print Stopped" reported
                # in #1807. Inequality check preserves the noop-on-stable-push
                # behaviour the earlier `not archive.subtask_id` guard provided.
                if effective_subtask_id and archive.subtask_id != effective_subtask_id:
                    archive.subtask_id = effective_subtask_id
                # #1403 follow-up: VP-queue archives are created with
                # printer_id=None at queue-add time (we don't know which
                # printer will run the job yet). When the print actually
                # starts on a specific printer the expected-archive lookup
                # used to skip this assignment, leaving printer_id=None
                # forever — which then disables the "Scan for timelapse"
                # button in ArchivesPage (gated on !archive.printer_id).
                if archive.printer_id != printer_id:
                    archive.printer_id = printer_id
                await db.commit()

                # Track as active print
                _active_prints[(printer_id, archive.filename)] = archive.id
                if subtask_name:
                    _active_prints[(printer_id, f"{subtask_name}.3mf")] = archive.id

                # Start timelapse session if external camera is enabled (#1353).
                # Queue / VP-dispatched prints land here in the expected-archive
                # branch and used to skip start_session entirely — frames were
                # never captured and the post-print stitch silently returned None.
                _maybe_start_layer_timelapse(printer, printer_id, archive.id)

                # Inject ams_mapping into usage tracker session — the session was created
                # before expected-print promotion, so it may have ams_mapping=None when
                # the MQTT request topic subscription failed (common on P1S/A1).
                _stored_map = _print_ams_mappings.get(expected_archive_id)
                _stored_plate_id = _print_plate_ids.get(expected_archive_id)
                if _stored_map or _stored_plate_id is not None:
                    try:
                        from backend.app.services.usage_tracker import _active_sessions

                        _ut_session = _active_sessions.get(printer_id)
                        if _ut_session and _stored_map and not _ut_session.ams_mapping:
                            _ut_session.ams_mapping = _stored_map
                            logger.info("[CALLBACK] Injected ams_mapping into usage tracker session: %s", _stored_map)
                        # plate_id injection covers direct-Print of plate N of a multi-plate
                        # 3MF — queue prints already capture it via the on_print_start queue
                        # lookup, but direct-Print never goes through the queue (#1697).
                        if _ut_session and _stored_plate_id is not None and _ut_session.plate_id is None:
                            _ut_session.plate_id = _stored_plate_id
                            logger.info("[CALLBACK] Injected plate_id into usage tracker session: %s", _stored_plate_id)
                    except Exception:
                        pass

                # Set up energy tracking (#941: persist start on archive row)
                await _record_energy_start(archive, printer_id, db, context="expected-print")

                await ws_manager.send_archive_updated(
                    {
                        "id": archive.id,
                        "status": "printing",
                    }
                )

                # Send notification with archive data (reprint/scheduled)
                if not notification_sent:
                    # Use archive's created_by_id; fall back to the creator registered via
                    # register_expected_print (handles library-file-based queue items where
                    # the freshly-created archive has no created_by_id yet).
                    # Pop ALL matching keys so no stale entries remain in the dict.
                    fallback_creator = None
                    for key in expected_keys:
                        popped = _expected_print_creators.pop(key, None)
                        if fallback_creator is None:
                            fallback_creator = popped
                    archive_data = {
                        "print_time_seconds": archive.print_time_seconds,
                        "created_by_id": archive.created_by_id or fallback_creator,
                    }
                    await _send_print_start_notification(printer_id, data, archive_data, logger)

                # Extract printable objects from the archived 3MF file
                _load_objects_from_archive(archive, printer_id, logger)

                # Store Spoolman tracking data for per-filament usage reporting
                try:
                    await _store_spoolman_print_data(
                        printer_id,
                        archive.id,
                        archive.file_path,
                        db,
                        printer_manager,
                        ams_mapping=_get_start_ams_mapping(data, archive.id),
                        plate_id=_get_start_plate_id(archive.id),
                    )
                except Exception as e:
                    logger.warning("[SPOOLMAN] Failed to store tracking data: %s", e)

                # Capture timelapse file baseline for snapshot-diff on completion
                # (mirrors the new-archive branch). Queue / VP-dispatched prints
                # hit this branch — without the baseline the completion-time scan
                # falls into its "take baseline now" fallback, which snapshots
                # AFTER the new MP4 already exists and never matches a diff
                # (#1403 follow-up — see pwostran's 2026-05-18 support bundle).
                await _capture_timelapse_baseline_at_start(printer, printer_id, logger)

            return  # Skip creating a new archive

        # Check if there's already a "printing" archive for this printer/file
        # This prevents duplicates when backend restarts during an active print
        from backend.app.models.archive import PrintArchive

        existing_archive: PrintArchive | None = None

        # Preferred match: subtask_id equality. MQTT reports the same subtask_id
        # across a backend restart for the same print, so this is the most
        # reliable way to reattach. We also accept a previously stale-cancelled
        # archive here so users upgrading mid-print get revived when the row
        # their earlier Bambuddy version wrongly cancelled reappears (#972).
        if subtask_id:
            by_id = await db.execute(
                select(PrintArchive)
                .where(PrintArchive.printer_id == printer_id)
                .where(PrintArchive.subtask_id == subtask_id)
                .where(PrintArchive.status.in_(["printing", "cancelled"]))
                .order_by(PrintArchive.created_at.desc())
                .limit(1)
            )
            candidate = by_id.scalar_one_or_none()
            if candidate and (candidate.status == "printing" or (candidate.failure_reason or "").startswith("Stale")):
                existing_archive = candidate

        # Fallback match: name-based lookup. Kept as-is for prints whose
        # subtask_id is missing ("0" / local / non-cloud prints).
        if existing_archive is None:
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
            # subtask_id match → always resume, regardless of age. Same print,
            # just a backend restart. Revive if it was previously stale-cancelled.
            subtask_match = bool(subtask_id and existing_archive.subtask_id == subtask_id)

            if subtask_match:
                if existing_archive.status == "cancelled":
                    logger.warning(
                        "Reviving stale-cancelled archive %s — matching subtask_id %s confirms same print (#972)",
                        existing_archive.id,
                        subtask_id,
                    )
                    existing_archive.status = "printing"
                    existing_archive.failure_reason = None
                    await db.commit()
                else:
                    logger.info("Resuming archive %s on subtask_id match (%s)", existing_archive.id, subtask_id)
                _active_prints[(printer_id, existing_archive.filename)] = existing_archive.id
                if existing_archive.energy_start_kwh is None:
                    await _record_energy_start(existing_archive, printer_id, db, context="subtask-resume")
                if not notification_sent:
                    archive_data = {
                        "print_time_seconds": existing_archive.print_time_seconds,
                        "created_by_id": existing_archive.created_by_id,
                    }
                    await _send_print_start_notification(printer_id, data, archive_data, logger)
                _load_objects_from_archive(existing_archive, printer_id, logger)
                return

            # Name-match only (no subtask_id to anchor on): decide resume vs.
            # stale from the printer's *current* progress, not wall-clock age.
            # A genuinely long print used to trip a blind 4h cutoff and have its
            # live archive cancelled + duplicated on every backend restart
            # (#1485). If the printer reports real progress, this name-matched
            # 'printing' archive IS that ongoing print — resume it whatever its
            # age. Only treat it as a stale leftover when the printer clearly
            # shows a different, freshly-started print: near-0% progress on an
            # archive far too old to still be at 0%. Unknown progress (printer
            # not connected) never cancels — resuming is the safe default.
            archive_age = datetime.now(timezone.utc) - existing_archive.created_at.replace(tzinfo=timezone.utc)
            live_status = printer_manager.get_status(printer_id)
            live_progress = getattr(live_status, "progress", None) if live_status else None
            looks_stale = (
                live_progress is not None and live_progress < 1.0 and archive_age.total_seconds() > 2 * 60 * 60
            )
            if looks_stale:
                logger.warning(
                    f"Found stale 'printing' archive {existing_archive.id} (age: {archive_age}, "
                    f"printer progress {live_progress:.0f}%) — marking cancelled and creating new archive"
                )
                existing_archive.status = "cancelled"
                existing_archive.failure_reason = "Stale - print likely cancelled or failed without status update"
                await db.commit()
                # Fall through to create new archive (don't return)
            else:
                logger.info(
                    f"Skipping duplicate - already have printing archive {existing_archive.id} for {check_name}"
                )
                # Track this as the active print
                _active_prints[(printer_id, existing_archive.filename)] = existing_archive.id
                # Attach subtask_id retroactively so future restarts can resume.
                # Compare for inequality (not "is empty") to also pick up reprint
                # dispatches that mint a fresh id — see #1807 for the bogus
                # "Print Stopped" the strict-empty guard caused on reconnect.
                if subtask_id and existing_archive.subtask_id != subtask_id:
                    existing_archive.subtask_id = subtask_id
                    await db.commit()
                # Also set up energy tracking if not already tracked (#941: persisted column)
                if existing_archive.energy_start_kwh is None:
                    await _record_energy_start(existing_archive, printer_id, db, context="existing-printing")
                # Send notification with archive data (existing archive)
                if not notification_sent:
                    archive_data = {
                        "print_time_seconds": existing_archive.print_time_seconds,
                        "created_by_id": existing_archive.created_by_id,
                    }
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

        # Release the pooled DB connection before the 3MF FTP download. Reaching
        # here means none of the expected-/existing-archive write branches ran
        # (they all return earlier) — only SELECTs have executed on this path, so
        # this commit persists nothing; it ends the read transaction so the
        # connection returns to the pool during the download. That download tries
        # up to five remote paths per candidate filename with retry/backoff and
        # can run for minutes under FTP contention; holding the session across it
        # pinned one pooled connection idle-in-transaction (issue #2572). No DB
        # work runs during the download — the new-archive writes below re-acquire
        # a fresh connection, and expire_on_commit=False keeps printer.* readable.
        await db.commit()

        # Try to find and download the 3MF file
        temp_path = None
        downloaded_filename = None

        # Cache check: cover endpoint may have already pulled this 3MF during
        # the print (frontend opens the card and shows the thumbnail) — reuse
        # that file instead of re-downloading 36MB over the same FTP link that
        # just served it (#972). The cache keys on a normalized filename so
        # variants like "X", "X.3mf", "X.gcode.3mf" all collapse to one entry.
        for try_filename in possible_names:
            if not try_filename.endswith(".3mf"):
                continue
            cached = get_cached_3mf(printer_id, try_filename)
            if cached:
                logger.info("Reusing cached 3MF from %s (avoided duplicate FTP)", cached)
                temp_path = cached
                downloaded_filename = try_filename
                break

        # Get FTP retry settings
        ftp_retry_enabled, ftp_retry_count, ftp_retry_delay, ftp_timeout = await get_ftp_retry_settings()

        for try_filename in possible_names if not downloaded_filename else []:
            if not try_filename.endswith(".3mf"):
                continue

            # Root (/) is where BambuStudio/OrcaSlicer uploads land on A1/P1-series
            # printers, so try it first — deferring it to last cost #972's reporter
            # ~48 minutes of retries on /cache//model//data//data/Metadata before
            # landing on the path that actually had the file.
            remote_paths = [
                f"/{try_filename}",
                f"/cache/{try_filename}",
                f"/model/{try_filename}",
                f"/data/{try_filename}",
                f"/data/Metadata/{try_filename}",
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
                            timeout=ftp_timeout,
                            socket_timeout=ftp_timeout,
                            printer_model=printer.model,
                            max_retries=ftp_retry_count,
                            retry_delay=ftp_retry_delay,
                            operation_name=f"Download 3MF from {remote_path}",
                            non_retry_exceptions=(FileNotOnPrinterError,),
                        )
                    else:
                        downloaded = await download_file_async(
                            printer.ip_address,
                            printer.access_code,
                            remote_path,
                            temp_path,
                            timeout=ftp_timeout,
                            socket_timeout=ftp_timeout,
                            printer_model=printer.model,
                        )
                    if downloaded:
                        downloaded_filename = try_filename
                        logger.info("Downloaded: %s", remote_path)
                        # Populate shared cache so the cover endpoint (if it
                        # runs next) doesn't refetch the same 36MB over FTP.
                        cache_3mf_download(printer_id, try_filename, temp_path)
                        break
                except FileNotOnPrinterError:
                    # 550 — file isn't at this path. Advance to next candidate
                    # without burning the retry budget.
                    logger.debug("3MF not at %s (550), trying next path", remote_path)
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
                            remote_full_path = posixpath.join(search_dir, fname)
                            if ftp_retry_enabled:
                                downloaded = await with_ftp_retry(
                                    download_file_async,
                                    printer.ip_address,
                                    printer.access_code,
                                    remote_full_path,
                                    temp_path,
                                    timeout=ftp_timeout,
                                    socket_timeout=ftp_timeout,
                                    printer_model=printer.model,
                                    max_retries=ftp_retry_count,
                                    retry_delay=ftp_retry_delay,
                                    operation_name=f"Download 3MF from {remote_full_path}",
                                )
                            else:
                                downloaded = await download_file_async(
                                    printer.ip_address,
                                    printer.access_code,
                                    remote_full_path,
                                    temp_path,
                                    timeout=ftp_timeout,
                                    socket_timeout=ftp_timeout,
                                    printer_model=printer.model,
                                )
                            if downloaded:
                                downloaded_filename = fname
                                logger.info("Found and downloaded from %s: %s", search_dir, fname)
                                cache_3mf_download(printer_id, fname, temp_path)
                                break
                except Exception as e:
                    logger.debug("Failed to list %s: %s", search_dir, e)

        # Validate the downloaded 3MF actually matches the plate that's running
        # (#1204): subtask_name lags across consecutive plates of the same model,
        # so the first FTP candidate (built from subtask_name) can land on the
        # previous plate's still-resident upload. Cross-check the slice_info
        # plate index against the plate parsed from gcode_file (always fresh —
        # it's the field whose change triggered this callback).
        if downloaded_filename and temp_path:
            expected_plate = parse_plate_id(filename)
            actual_plate = peek_plate_index_in_3mf(temp_path) if expected_plate is not None else None
            if expected_plate is not None and actual_plate is not None and actual_plate != expected_plate:
                logger.warning(
                    "[CALLBACK] 3MF plate mismatch: downloaded %s reports plate %s but printer is "
                    "running plate %s — subtask_name=%r appears stale, retrying with corrected name",
                    downloaded_filename,
                    actual_plate,
                    expected_plate,
                    subtask_name,
                )
                corrected_subtask = swap_plate_suffix(subtask_name, expected_plate)
                retry_succeeded = False
                if corrected_subtask and corrected_subtask != subtask_name:
                    for try_filename in (f"{corrected_subtask}.gcode.3mf", f"{corrected_subtask}.3mf"):
                        retry_temp_path = app_settings.archive_dir / "temp" / try_filename
                        retry_temp_path.parent.mkdir(parents=True, exist_ok=True)
                        for remote_path in (
                            f"/{try_filename}",
                            f"/cache/{try_filename}",
                            f"/model/{try_filename}",
                            f"/data/{try_filename}",
                            f"/data/Metadata/{try_filename}",
                        ):
                            try:
                                if ftp_retry_enabled:
                                    downloaded = await with_ftp_retry(
                                        download_file_async,
                                        printer.ip_address,
                                        printer.access_code,
                                        remote_path,
                                        retry_temp_path,
                                        timeout=ftp_timeout,
                                        socket_timeout=ftp_timeout,
                                        printer_model=printer.model,
                                        max_retries=ftp_retry_count,
                                        retry_delay=ftp_retry_delay,
                                        operation_name=f"Re-download 3MF from {remote_path}",
                                        non_retry_exceptions=(FileNotOnPrinterError,),
                                    )
                                else:
                                    downloaded = await download_file_async(
                                        printer.ip_address,
                                        printer.access_code,
                                        remote_path,
                                        retry_temp_path,
                                        timeout=ftp_timeout,
                                        socket_timeout=ftp_timeout,
                                        printer_model=printer.model,
                                    )
                                if downloaded and peek_plate_index_in_3mf(retry_temp_path) == expected_plate:
                                    logger.info(
                                        "[CALLBACK] Re-download succeeded with corrected name %s "
                                        "(plate %s) — replacing wrong file",
                                        try_filename,
                                        expected_plate,
                                    )
                                    try:
                                        temp_path.unlink(missing_ok=True)
                                    except OSError:
                                        pass
                                    temp_path = retry_temp_path
                                    downloaded_filename = try_filename
                                    subtask_name = corrected_subtask
                                    cache_3mf_download(printer_id, try_filename, temp_path)
                                    retry_succeeded = True
                                    break
                                elif downloaded:
                                    # Wrong plate again — discard and keep trying
                                    try:
                                        retry_temp_path.unlink(missing_ok=True)
                                    except OSError:
                                        pass
                            except FileNotOnPrinterError:
                                continue
                            except Exception as e:
                                logger.debug("Re-download failed for %s: %s", remote_path, e)
                        if retry_succeeded:
                            break
                # If the retry didn't find a matching file, drop the wrong 3MF
                # so the no-3MF fallback below creates an archive whose name
                # at least reflects the right plate.
                if not retry_succeeded:
                    logger.warning(
                        "[CALLBACK] Could not re-download correct plate %s — falling back to no-3MF archive",
                        expected_plate,
                    )
                    try:
                        temp_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    temp_path = None
                    downloaded_filename = None
                    # Override the stale subtask_name so the fallback archive's
                    # print_name reflects the correct plate. Prefer the swapped
                    # name when we have one; otherwise let filename win.
                    if corrected_subtask:
                        subtask_name = corrected_subtask
                    else:
                        subtask_name = ""

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

                # Recover estimated print time from MQTT (best-effort for notifications)
                fallback_print_time = None
                mqtt_remaining = data.get("remaining_time")
                if mqtt_remaining and isinstance(mqtt_remaining, (int, float)) and mqtt_remaining > 0:
                    fallback_print_time = int(mqtt_remaining)
                if fallback_print_time is None:
                    mc_remaining = (data.get("raw_data") or {}).get("mc_remaining_time")
                    if mc_remaining and isinstance(mc_remaining, (int, float)) and mc_remaining > 0:
                        fallback_print_time = int(mc_remaining * 60)

                # Best-effort filament metadata from MQTT — see
                # _extract_filament_data_from_mqtt. Without this the fallback
                # archive's filament fields stayed NULL even though the AMS
                # state at print start was sitting right there in `data`.
                # The slicer's ams_mapping (when present) narrows the result
                # to slots actually used by the print (#1533).
                mqtt_filament_meta = _extract_filament_data_from_mqtt(data, _get_start_ams_mapping(data, None))

                # Create minimal archive entry
                fallback_archive = PrintArchive(
                    printer_id=printer_id,
                    filename=filename or f"{print_name}.3mf",
                    file_path="",  # Empty - no 3MF file available
                    file_size=0,
                    print_name=print_name,
                    print_time_seconds=fallback_print_time,
                    status="printing",
                    started_at=datetime.now(timezone.utc),
                    subtask_id=subtask_id,
                    filament_type=mqtt_filament_meta.get("filament_type"),
                    filament_color=mqtt_filament_meta.get("filament_color"),
                    extra_data={"no_3mf_available": True, "original_subtask": subtask_name, "_print_data": data},
                )

                db.add(fallback_archive)
                await db.commit()
                await db.refresh(fallback_archive)

                logger.info("Created fallback archive %s for %s (no 3MF available)", fallback_archive.id, print_name)

                _maybe_start_layer_timelapse(printer, printer_id, fallback_archive.id)

                # Track as active print
                _active_prints[(printer_id, fallback_archive.filename)] = fallback_archive.id
                if filename:
                    _active_prints[(printer_id, filename)] = fallback_archive.id
                if subtask_name:
                    _active_prints[(printer_id, f"{subtask_name}.3mf")] = fallback_archive.id
                    _active_prints[(printer_id, subtask_name)] = fallback_archive.id

                # Record starting energy if smart plug available (#941: persisted column)
                await _record_energy_start(fallback_archive, printer_id, db, context="fallback")

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
                        printer_id,
                        fallback_archive.id,
                        fallback_archive.file_path,
                        db,
                        printer_manager,
                        ams_mapping=_get_start_ams_mapping(data, fallback_archive.id),
                        plate_id=_get_start_plate_id(fallback_archive.id),
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
                subtask_id=subtask_id,
            )

            if archive:
                # Track this active print (use both original filename and downloaded filename)
                _active_prints[(printer_id, downloaded_filename)] = archive.id
                if filename and filename != downloaded_filename:
                    _active_prints[(printer_id, filename)] = archive.id
                if subtask_name:
                    _active_prints[(printer_id, f"{subtask_name}.3mf")] = archive.id

                logger.info("Created archive %s for %s", archive.id, downloaded_filename)

                _maybe_start_layer_timelapse(printer, printer_id, archive.id)

                # Record starting energy from smart plug if available (#941: persisted column)
                await _record_energy_start(archive, printer_id, db, context="auto-archive")

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
                    archive_data = {
                        "print_time_seconds": archive.print_time_seconds,
                        "created_by_id": archive.created_by_id,
                    }
                    await _send_print_start_notification(printer_id, data, archive_data, logger)

                # Extract printable objects for skip object functionality
                try:
                    from backend.app.services.archive import extract_printable_objects_from_3mf

                    client = printer_manager.get_client(printer_id)
                    if client:
                        with open(temp_path, "rb") as f:
                            threemf_data = f.read()
                        # Extract with positions for UI overlay, scoped to the
                        # plate that is printing — an all-plates 3MF carries
                        # every plate's objects (#2522).
                        printable_objects, bbox_all = extract_printable_objects_from_3mf(
                            threemf_data,
                            plate_number=resolve_plate_id(client.state),
                            include_positions=True,
                        )
                        if printable_objects:
                            # Store objects in printer state
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
                    await _store_spoolman_print_data(
                        printer_id,
                        archive.id,
                        archive.file_path,
                        db,
                        printer_manager,
                        ams_mapping=_get_start_ams_mapping(data, archive.id),
                        plate_id=_get_start_plate_id(archive.id),
                    )
                except Exception as e:
                    logger.warning("[SPOOLMAN] Failed to store tracking data: %s", e)

                # Capture timelapse file baseline for snapshot-diff on completion
                await _capture_timelapse_baseline_at_start(printer, printer_id, logger)
        finally:
            # Keep temp_path around until print completes so the cover endpoint
            # can reuse it (#972). Cache eviction in on_print_complete deletes
            # the file. If the cache entry was evicted early (file vanished),
            # clean up any stragglers here to avoid leaking disk on retries.
            cached_now = get_cached_3mf(printer_id, downloaded_filename) if downloaded_filename else None
            if temp_path and temp_path.exists() and cached_now != temp_path:
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


async def _capture_timelapse_baseline_at_start(printer, printer_id: int, logger: logging.Logger) -> None:
    """Snapshot the printer's timelapse directory at print start so the
    completion-time scan can pick the new file by set-difference.

    Must be called from every on_print_start path that proceeds to a real
    print — both the new-archive branch and the expected-archive branch (which
    queue / VP-dispatched prints take). Without a baseline,
    _scan_for_timelapse_with_retries falls into its "take baseline now"
    fallback that runs AFTER the new MP4 has already landed on the SD card,
    so the new file ends up in the "baseline" set and no diff ever matches.

    Bambu printers in LAN-only mode don't sync NTP, so mtime ordering is
    unreliable — the snapshot-diff approach sidesteps that entirely.
    """
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
            from backend.app.models.printer import Printer
            from backend.app.services.bambu_ftp import download_file_bytes_async

            # Read phase: fetch archive + printer in a short session and release
            # the pooled connection BEFORE the FTP list/download below. Holding it
            # across the FTP round-trips left one connection idle-in-transaction per
            # in-flight scan — ×4 retries, per completed print (issue #2572).
            async with async_session() as db:
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

            # I/O phase (no DB connection held): FTP list + download.
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
                    # Write phase: attach in a fresh short-lived session.
                    async with async_session() as db:
                        success = await ArchiveService(db).attach_timelapse(archive_id, timelapse_data, file_name)
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
            from backend.app.models.printer import Printer
            from backend.app.services.bambu_ftp import download_file_bytes_async

            # Read phase: short session, released before the FTP work (issue #2572).
            async with async_session() as db:
                service = ArchiveService(db)
                archive = await service.get_archive(archive_id)
                if not archive or archive.timelapse_path:
                    return

                result = await db.execute(select(Printer).where(Printer.id == archive.printer_id))
                printer = result.scalar_one_or_none()
                if not printer:
                    return

            # I/O phase (no DB connection held): FTP list + download.
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
                        # Write phase: attach in a fresh short-lived session.
                        async with async_session() as db:
                            success = await ArchiveService(db).attach_timelapse(archive_id, timelapse_data, fname)
                        if success:
                            logger.info("[TIMELAPSE] Name-match fallback attached timelapse to archive %s", archive_id)
                            await ws_manager.send_archive_updated({"id": archive_id, "timelapse_attached": True})
                            return
                    break  # Only try the first name match

        except Exception as e:
            logger.warning("[TIMELAPSE] Name-match fallback failed: %s", e)

    logger.warning("[TIMELAPSE] All attempts exhausted for archive %s, giving up", archive_id)


# Defaults for the finish-photo-from-timelapse polling loop (#1397). These are
# module-level so tests can monkeypatch them down to ~0 without timing out.
_FINISH_PHOTO_TIMELAPSE_POLL_INTERVAL_SECONDS: float = 3.0
_FINISH_PHOTO_TIMELAPSE_POLL_TIMEOUT_SECONDS: float = 60.0


async def _capture_finish_photo_from_timelapse(
    archive_id: int,
    archive_dir: Path,
) -> str | None:
    """Wait for the per-print timelapse to land on the archive and extract its
    last frame as the finish photo (#1397).

    Bambu firmware stops timelapse recording after the toolhead parks but
    before the bed-drop end-gcode runs, so the last frame frames the finished
    print correctly. A live camera grab at gcode_state=FINISH captures the
    bed already lowered.

    ``_scan_for_timelapse_with_retries`` runs in parallel and writes
    ``archive.timelapse_path`` when the file lands. This function polls for
    that field. Returns the saved photo filename on success, or None if the
    timelapse never arrives within the timeout / extraction fails / no
    timelapse path was set — in which case the caller falls back to the
    existing live-camera capture chain.
    """
    import uuid

    from backend.app.models.archive import PrintArchive
    from backend.app.services.camera import extract_video_last_frame

    logger = logging.getLogger(__name__)

    deadline = asyncio.get_event_loop().time() + _FINISH_PHOTO_TIMELAPSE_POLL_TIMEOUT_SECONDS
    poll_interval = _FINISH_PHOTO_TIMELAPSE_POLL_INTERVAL_SECONDS

    while True:
        async with async_session() as db:
            result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
            archive = result.scalar_one_or_none()
            timelapse_relpath = archive.timelapse_path if archive else None

        if timelapse_relpath:
            video_path = app_settings.base_dir / timelapse_relpath
            if video_path.exists() and video_path.stat().st_size > 0:
                photos_dir = archive_dir / "photos"
                photos_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"finish_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
                output_path = photos_dir / filename
                if await extract_video_last_frame(video_path, output_path):
                    logger.info(
                        "[PHOTO-BG] Extracted finish photo from timelapse %s for archive %s",
                        video_path.name,
                        archive_id,
                    )
                    return filename
                logger.warning(
                    "[PHOTO-BG] Timelapse %s landed but last-frame extraction failed for archive %s; falling back",
                    video_path.name,
                    archive_id,
                )
                return None

        if asyncio.get_event_loop().time() >= deadline:
            logger.info(
                "[PHOTO-BG] Timelapse for archive %s didn't land within %.0fs; falling back to live camera",
                archive_id,
                _FINISH_PHOTO_TIMELAPSE_POLL_TIMEOUT_SECONDS,
            )
            return None

        await asyncio.sleep(poll_interval)


async def on_print_running_observed(printer_id: int, data: dict):
    """Restart-recovery: capture a fresh timelapse baseline for a print that
    started before Bambuddy came up.

    bambu_mqtt.py suppresses ``on_print_start`` on the first RUNNING push
    after Bambuddy startup (#1304 guard, prevents duplicate archive
    creation). Without that path, ``_capture_timelapse_baseline_at_start``
    never runs and ``_scan_for_timelapse_with_retries`` falls into its
    "take baseline now" fallback at completion time — but by then the
    printer has already uploaded the in-flight MP4, so the baseline
    includes it and no diff ever matches (#1485 follow-up).

    Fires once per session, in lieu of on_print_start when restart-recovery
    kicks in. The printer doesn't upload the timelapse until after PRINT
    COMPLETE, so a baseline captured any time during the print is still
    pre-upload.
    """
    logger = logging.getLogger(__name__)

    # Avoid double-capture: on_print_start may have run earlier in this
    # Bambuddy process if the print started AFTER startup and we crashed
    # later in the same session. (Realistically this can't happen — the
    # MQTT client object would have been recreated — but the cheap guard
    # is correct regardless.)
    if printer_id in _timelapse_baselines:
        logger.debug(
            "[TIMELAPSE] on_print_running_observed: baseline already present for printer %s, skipping",
            printer_id,
        )
        return

    async with async_session() as db:
        from backend.app.models.printer import Printer

        result = await db.execute(select(Printer).where(Printer.id == printer_id))
        printer = result.scalar_one_or_none()
        if not printer:
            logger.warning(
                "[TIMELAPSE] on_print_running_observed: printer %s not found in DB, skipping baseline",
                printer_id,
            )
            return

    await _capture_timelapse_baseline_at_start(printer, printer_id, logger)


def _is_active_archive_stale(archive, state) -> tuple[bool, str]:
    """Return ``(is_stale, reason)`` for an archive in ``status="printing"``
    against the printer's current MQTT state.

    Reconciliation triggers (#1542 follow-up — recovers from missed PRINT
    COMPLETE events, typically a print finishing during an MQTT disconnect
    window followed by a smart-plug power cycle):

      1. Printer state is terminal (IDLE / FINISH / FAILED). The print is
         provably not running anymore — only branch that should fire under
         normal disconnect-then-reconnect timing.
      2. Printer has a different ``subtask_id`` than the archive. Bambu
         firmware mints a fresh ``subtask_id`` for each print, including the
         ghost replay it runs after a power cycle from a leftover SD file —
         so a mismatch unambiguously means the in-DB archive is no longer
         the print on the printer.
      3. Printer is running but ``subtask_name`` is empty. The printer
         doesn't know what it's running; the archive's reference to it is
         already broken.

    Conservative on purpose: PAUSE / PREPARE / SLICING and any RUNNING state
    with matching subtask_id+subtask_name is left alone. The cost of a false
    positive is a duplicate archive on the next real PRINT COMPLETE — the
    reactive handler uses ``_active_prints`` for lookup, which the reconcile
    clears on synthesis, so the real completion creates a fresh row instead
    of overwriting the synthesised one (#1679). The cost of a false negative
    is the ghost-print loop in #1542.

    Pre-push guard (#1679): when ``state.state`` is empty or ``"unknown"``,
    MQTT has connected but the first ``push_status`` response hasn't been
    applied yet — ``PrinterState`` is sitting on its construction defaults.
    The reconcile caller in ``on_printer_status_change`` is already gated
    on a real ``state.state``, so in normal operation this branch is
    unreachable; it's kept as belt-and-braces for future callers and for
    the narrow window where a partial state update could arrive
    (``state.state`` set but ``subtask_name`` not yet populated). Returning
    ``not stale`` on degenerate input is strictly conservative: a real
    stale archive will still be caught by the next push_status arriving
    with terminal state.
    """
    current_state = (state.state or "").upper()
    if current_state in ("", "UNKNOWN"):
        # No real push_status yet — PrinterState defaults are not evidence.
        return False, ""
    if current_state in ("IDLE", "FINISH", "FAILED"):
        return True, f"printer state {current_state}"
    # Below here the printer is in a running / pre-running state (RUNNING /
    # PAUSE / PREPARE / SLICING / etc.) — decide based on subtask identity.
    current_subtask_id = (state.subtask_id or "").strip()
    if archive.subtask_id and current_subtask_id and archive.subtask_id != current_subtask_id:
        return True, f"subtask_id changed ({archive.subtask_id!r} → {current_subtask_id!r})"
    current_subtask_name = (state.subtask_name or "").strip()
    if not current_subtask_name:
        return True, "printer subtask_name empty"
    return False, ""


async def reconcile_stale_active_prints(printer_id: int) -> int:
    """Synthesise ``on_print_complete`` for archives whose print can't be
    running on the printer anymore.

    Called once per MQTT (re)connection (from on_printer_status_change when
    the connected edge flips False → True) and at Bambuddy startup (from
    the FastAPI lifespan). Without this, a print that completes during a
    disconnect window — followed by a smart-plug-driven power cycle — leaves
    the ``.3mf`` on the SD card, the firmware auto-replays it on next boot,
    and Bambuddy fires a fresh PRINT START for the ghost rather than the
    SD cleanup that PRINT COMPLETE was supposed to run. Repeats every
    power cycle until the operator notices (#1542 follow-up). Reconciliation
    closes the loop by faking the missed PRINT COMPLETE — the existing
    cleanup chain handles SD-file deletion, status updates, usage tracking,
    and notifications.

    Synthesised ``status="aborted"`` is the conservative label: we have no
    proof the print finished successfully (and no progress evidence to
    promote to ``"completed"``). The real PRINT COMPLETE callback, if it
    fires later, overwrites the status with the correct value.

    Returns the number of archives reconciled.
    """
    state = printer_manager.get_status(printer_id)
    if not state:
        return 0
    # Don't reconcile while disconnected — we'd be making a decision against
    # stale cached state. The connected → reconcile edge handles this.
    if not state.connected:
        return 0

    from backend.app.models.archive import PrintArchive

    reconciled = 0
    async with async_session() as db:
        result = await db.execute(
            select(PrintArchive).where(
                PrintArchive.printer_id == printer_id,
                PrintArchive.status == "printing",
            )
        )
        active = list(result.scalars().all())

    if not active:
        return 0

    logger = logging.getLogger(__name__)
    for archive in active:
        is_stale, reason = _is_active_archive_stale(archive, state)
        if not is_stale:
            continue
        logger.info(
            "[RECONCILE] Printer %s: synthesising missed PRINT COMPLETE for archive %s (%s) — %s",
            printer_id,
            archive.id,
            archive.filename,
            reason,
        )
        # Synthesised payload: minimal fields the on_print_complete chain
        # needs. `_reconciled` marker lets downstream code distinguish this
        # from a real MQTT-driven completion if it ever needs to (e.g. for
        # metrics / debug logging). raw_data is the live printer state so
        # the usage tracker can compare end-of-print remain% against the
        # captured start values.
        try:
            await on_print_complete(
                printer_id,
                {
                    "status": "aborted",
                    "filename": archive.filename,
                    "subtask_name": archive.print_name or "",
                    "subtask_id": archive.subtask_id or "",
                    "raw_data": state.raw_data or {},
                    "_reconciled": True,
                },
            )
            reconciled += 1
        except Exception as e:
            # Catch-all: a reconciliation failure must not block the
            # printer's normal status flow. The archive stays in
            # ``status="printing"`` and the next reconnect retries.
            logger.warning(
                "[RECONCILE] on_print_complete synthesis failed for archive %s: %s",
                archive.id,
                e,
            )

    return reconciled


async def on_finish_photo_moment(printer_id: int, data: dict):
    """Pre-capture a finish photo when the printer enters stage 22 / FINISH (#1721).

    Fires either at the stage-22 ("Filament unloading") edge — toolhead
    parked, bed not yet dropped, optimal framing — or as a FINISH-state
    fallback for prints that skip stage 22 (cancel, external-spool-only,
    HMS halt, firmware variants). Grabs one frame via the same
    external-camera / RTSP path the post-completion fallback uses, stores
    the JPEG bytes in ``_stage22_finish_frames[printer_id]``, and lets
    ``_background_finish_photo`` consume the cached bytes when it runs.

    Replaces the #1397 "force timelapse on at dispatch" mechanism, which
    caused per-layer nozzle parking on slicer profiles with Timelapse Type
    set to Smooth (#1721). No force-on now means the user's explicit
    timelapse=off in the slicer send dialog is respected.
    """
    logger = logging.getLogger(__name__)
    trigger = data.get("trigger", "unknown")
    timelapse_was_active = bool(data.get("timelapse_was_active"))
    logger.info(
        "[FINISH-PHOTO-MOMENT] printer=%s trigger=%s timelapse_active=%s",
        printer_id,
        trigger,
        timelapse_was_active,
    )

    # If a timelapse is actively recording, skip the pre-capture — the
    # post-completion path will extract the last frame from the recorded
    # video, which still provides the best framing (toolhead parked,
    # before bed drop) without the per-layer parking side effects.
    if timelapse_was_active:
        logger.info(
            "[FINISH-PHOTO-MOMENT] timelapse active for printer %s — skipping pre-capture (last-frame extraction will run post-completion)",
            printer_id,
        )
        return

    # #1790: register the producer-done event BEFORE the first await so the
    # consumer in `_background_finish_photo` — which is dispatched back-to-back
    # with us on the FINISH-state fallback path — sees it as soon as it polls.
    # The `finally` below guarantees `set()` runs on every exit, including
    # early returns and exceptions, so the consumer's bounded wait can't hang.
    producer_done = asyncio.Event()
    _stage22_finish_in_flight[printer_id] = producer_done

    try:
        async with async_session() as db:
            from backend.app.api.routes.settings import get_setting
            from backend.app.models.printer import Printer

            capture_setting = await get_setting(db, "capture_finish_photo")
            if capture_setting is not None and capture_setting.lower() != "true":
                logger.info("[FINISH-PHOTO-MOMENT] capture_finish_photo disabled — skipping pre-capture")
                return

            result = await db.execute(select(Printer).where(Printer.id == printer_id))
            printer = result.scalar_one_or_none()
            if printer is None:
                logger.warning(
                    "[FINISH-PHOTO-MOMENT] printer %s not found in DB",
                    printer_id,
                )
                return

        frame_bytes: bytes | None = None

        # #1867: on the FINISH-state fallback the End G-code (e.g. SwapMod
        # plate-swap) has already run, so a live grab now captures the swapped
        # or empty plate. Prefer the banked in-print frame — the finished
        # print from the last object layer, before the swap. Only for
        # `finish_state`: the `stage_22` and `last_layer` triggers fire before
        # the swap and give cleaner (parked-toolhead) framing via a live grab.
        if trigger == "finish_state":
            banked = _inprint_frame_bank.get(printer_id)
            if banked:
                frame_bytes = banked
                logger.info(
                    "[FINISH-PHOTO-MOMENT] using banked in-print frame (%d bytes) — "
                    "avoids post-swap live grab on stage-22-less firmware",
                    len(banked),
                )

        if frame_bytes is None and printer.external_camera_enabled and printer.external_camera_url:
            from backend.app.services.external_camera import capture_frame

            frame_bytes = await capture_frame(
                printer.external_camera_url,
                printer.external_camera_type or "mjpeg",
                snapshot_url=printer.external_camera_snapshot_url,
            )
            if frame_bytes:
                logger.info(
                    "[FINISH-PHOTO-MOMENT] captured external-camera frame (%d bytes)",
                    len(frame_bytes),
                )
        elif frame_bytes is None:
            from backend.app.api.routes.camera import get_buffered_frame

            buffered = get_buffered_frame(printer_id)
            if buffered:
                frame_bytes = buffered
                logger.info(
                    "[FINISH-PHOTO-MOMENT] used buffered RTSP frame (%d bytes)",
                    len(frame_bytes),
                )
            else:
                from backend.app.services.camera import capture_camera_frame_bytes

                frame_bytes = await capture_camera_frame_bytes(
                    ip_address=printer.ip_address,
                    access_code=printer.access_code,
                    model=printer.model,
                    timeout=15,
                )
                if frame_bytes:
                    logger.info(
                        "[FINISH-PHOTO-MOMENT] captured RTSP frame (%d bytes)",
                        len(frame_bytes),
                    )

        if frame_bytes:
            _stage22_finish_frames[printer_id] = frame_bytes
        else:
            logger.warning(
                "[FINISH-PHOTO-MOMENT] no frame captured for printer %s — post-completion fallback will retry",
                printer_id,
            )
    except Exception as e:
        logger.warning(
            "[FINISH-PHOTO-MOMENT] pre-capture failed for printer %s: %s",
            printer_id,
            e,
        )
    finally:
        # #1790: always unblock the consumer's bounded wait — whether we stored
        # a frame, gave up, or hit an exception. Local ref means cleanup of the
        # dict entry by the consumer doesn't affect signalling.
        producer_done.set()


async def on_print_complete(printer_id: int, data: dict):
    """Handle print completion - update the archive status."""
    import time

    logger = logging.getLogger(__name__)
    start_time = time.time()

    def log_timing(section: str):
        elapsed = time.time() - start_time
        logger.info("[TIMING] %s: %.3fs elapsed", section, elapsed)

    logger.info("[CALLBACK] on_print_complete started for printer %s", printer_id)

    # Drop the 3MF download cache for this printer (#972). The print is over,
    # nothing else legitimately needs the bytes; keeping them would only risk
    # handing a stale file to the next print if it reuses the same name.
    clear_3mf_cache(printer_id)

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

    # If the user explicitly stopped this print from the queue UI the printer will
    # report "failed" or "aborted" via MQTT.  Override that to "cancelled" so the
    # correct "print stopped" notification/email is sent instead of a failure alert.
    _raw_status = data.get("status", "completed")
    if printer_id in _user_stopped_printers and _raw_status in ("failed", "aborted"):
        logger.info(
            "[CALLBACK] Overriding status '%s' -> 'cancelled' for printer %s (print was stopped from queue by user)",
            _raw_status,
            printer_id,
        )
        data = {**data, "status": "cancelled"}
    _user_stopped_printers.discard(printer_id)

    # Raise the plate-clear gate for queued dispatch (#961). Any terminal status
    # may have left material on the bed: a user can cancel ten hours into a
    # twelve-hour print, a printer can self-abort mid-job after a clog, and a
    # touchscreen-stop reports `aborted` rather than `cancelled` because
    # `_user_stopped_printers` is only populated when the user stops via the
    # Bambuddy queue UI. Earlier code raised the flag only for completed/failed,
    # which auto-dispatched the next queued print onto a fouled bed two seconds
    # after a touchscreen-abort (#1171). Persisted to DB so the gate survives
    # Auto Off power cycles and Bambuddy restarts.
    _final_status = data.get("status", "completed")
    if _final_status in ("completed", "failed", "aborted", "cancelled"):
        printer_manager.set_awaiting_plate_clear(printer_id, True)

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

    # Cleanup: delete uploaded file from printer SD card to prevent phantom prints (Issue #374, #1542)
    # The print scheduler uploads files to the SD card root (/). Some printers (e.g. P1S, A1)
    # auto-start files found in root on power cycle, causing ghost prints.
    # Must run before the archive_id early-return so it executes even when archiving is disabled.
    try:
        if subtask_name:
            archive_filename: str | None = None
            async with async_session() as db:
                from backend.app.models.archive import PrintArchive
                from backend.app.models.printer import Printer

                result = await db.execute(select(Printer).where(Printer.id == printer_id))
                printer = result.scalar_one_or_none()
                if archive_id:
                    archive_row = await db.execute(select(PrintArchive.filename).where(PrintArchive.id == archive_id))
                    archive_filename = archive_row.scalar_one_or_none()

            if printer:
                from backend.app.services.bambu_ftp import DeleteResult, delete_file_async
                from backend.app.utils.filename import derive_remote_filename

                # Primary candidate: the exact path the dispatcher uploaded to
                # (derived from archive.filename via the same rule as upload).
                # Without it, a library row that ended up with a doubled
                # .gcode.3mf (#1542) leaves the real file behind because the
                # subtask_name + ext fallbacks below don't match what's on the
                # SD card. Fallbacks remain for archive-less prints (subtask
                # never resolved to an archive) and for older naming variants.
                candidate_paths: list[str] = []
                if archive_filename:
                    candidate_paths.append(f"/{derive_remote_filename(archive_filename)}")
                for ext in (".3mf", ".gcode"):
                    fallback = f"/{subtask_name}{ext}"
                    if fallback not in candidate_paths:
                        candidate_paths.append(fallback)

                # Three outcomes track across all candidates so the final log
                # line reflects what actually happened. The A1 in #1721 always
                # ends here with ``any_not_found=True`` and the others False
                # — its firmware auto-cleans the SD card before our cleanup
                # runs, every candidate FTP-DELE returns 550, and the old
                # code burned 3 retries × 2 s × 3 candidates per print
                # logging a misleading "may linger" WARNING on a successful
                # print.
                any_deleted = False
                any_real_failure = False
                any_not_found = False

                for remote_path in candidate_paths:
                    # Retry only the FAILED case — 550 NOT_FOUND will never
                    # recover by waiting, so a "file isn't here" answer
                    # advances immediately to the next candidate without
                    # consuming the retry budget.
                    for attempt in range(1, 4):
                        try:
                            delete_result = await delete_file_async(
                                printer.ip_address,
                                printer.access_code,
                                remote_path,
                                printer_model=printer.model,
                            )
                        except Exception as e:
                            delete_result = DeleteResult.FAILED
                            logger.warning(
                                "SD card cleanup attempt %d/3 raised for %s: %s",
                                attempt,
                                remote_path,
                                e,
                            )

                        if delete_result == DeleteResult.DELETED:
                            any_deleted = True
                            logger.info("Deleted %s from printer %s SD card", remote_path, printer.name)
                            break
                        if delete_result == DeleteResult.NOT_FOUND:
                            any_not_found = True
                            break  # 550 will not recover; try next candidate
                        # FAILED: real error — retry with backoff, then give up
                        if attempt < 3:
                            await asyncio.sleep(2)
                        else:
                            any_real_failure = True
                            logger.warning(
                                "SD card cleanup failed after 3 attempts for %s "
                                "(network/auth/transient error — file may linger on SD card)",
                                remote_path,
                            )

                if not any_deleted and not any_real_failure and any_not_found:
                    # Every candidate said "not here." Either the printer
                    # firmware swept the SD card itself (common on A1) or the
                    # dispatcher's upload path doesn't match our candidate
                    # rule. Either way: nothing to clean up, no warning.
                    logger.debug(
                        "SD card cleanup: nothing to delete on %s — every candidate returned 550 "
                        "(printer likely self-cleaned)",
                        printer.name,
                    )
    except Exception as e:
        logger.warning("SD card file cleanup failed for printer %s: %s", printer_id, e)

    log_timing("SD card cleanup")

    # Update queue item status early — must run before the archive_id early-return
    # so queue items don't get stuck in "printing" when archive lookup fails.
    # Uses run_with_retry to handle SQLite "database is locked" errors (#897).
    queue_item_id = None
    queue_status = None
    queue_auto_off = False
    try:
        from backend.app.core.database import run_with_retry
        from backend.app.models.print_queue import PrintQueueItem

        async def _update_queue_status(db):
            nonlocal queue_item_id, queue_status, queue_auto_off
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
            item = printing_items[0] if printing_items else None
            if item:
                queue_status = data.get("status", "completed")
                # MQTT sends "aborted" for cancelled prints; normalise to
                # "cancelled" so it matches the queue schema Literal.
                if queue_status == "aborted":
                    queue_status = "cancelled"
                item.status = queue_status
                item.completed_at = datetime.now(timezone.utc)
                if queue_status == "failed" and not item.error_message:
                    item.error_message = _format_hms_error_summary(data.get("hms_errors") or [])

                # Bump usage counters on the source library file so admins can
                # sort by "last printed" and (eventually) auto-purge stale
                # files — #1008.
                await _bump_library_file_usage_if_completed(db, item, queue_status)

                await db.commit()
                queue_item_id = item.id
                queue_auto_off = item.auto_off_after
                logger.info("Updated queue item %s status to %s", item.id, queue_status)

        await run_with_retry(_update_queue_status, label="queue status update")

        # Post-commit side effects (notifications, MQTT relay, auto-off) use
        # their own sessions and have their own error handling — no retry needed.
        if queue_item_id is not None:
            # MQTT relay - publish queue job completed
            try:
                printer_info = printer_manager.get_printer(printer_id)
                await mqtt_relay.on_queue_job_completed(
                    job_id=queue_item_id,
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

                async with async_session() as db:
                    count_result = await db.execute(
                        select(sa_func.count(PrintQueueItem.id)).where(PrintQueueItem.status == "pending")
                    )
                    pending_count = count_result.scalar() or 0

                    if pending_count == 0:
                        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
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

            # Handle auto_off_after - power off printer if the queue item opted
            # in. Delegates to the smart-plug manager so the off honours each
            # plug's configured strategy (time delay or temperature threshold),
            # is cancelled if the printer starts printing again, and never cuts
            # power on a loaded print (#1890). Previously an inline block here
            # hardcoded a 50°C / 600s cooldown wait and powered off on the
            # timeout regardless of print state — cutting a touchscreen reprint.
            if queue_auto_off:
                try:
                    async with async_session() as db:
                        await smart_plug_manager.schedule_off_after_queue_job(printer_id, db)
                except Exception as e:
                    logger.warning("Failed to schedule queue auto-off for printer %s: %s", printer_id, e)
    except Exception as e:
        logging.getLogger(__name__).warning(f"Queue item update failed: {e}")

    log_timing("Queue item update")

    # Register bed cooldown waiter (event-driven via on_bed_temp_update callback).
    # Must run before archive_id early-return so it fires for all prints (including
    # prints started from BambuStudio/touchscreen that have no archive).
    if data.get("status") == "completed":
        try:
            from backend.app.api.routes.settings import get_setting

            async with async_session() as db:
                threshold_str = await get_setting(db, "bed_cooled_threshold")
            threshold = float(threshold_str) if threshold_str else 35.0

            # Check if any provider has on_bed_cooled enabled (skip registration if none)
            async with async_session() as db:
                providers = await notification_service._get_providers_for_event(db, "on_bed_cooled", printer_id)
            if providers:
                _bed_cool_waiters[printer_id] = {
                    "threshold": threshold,
                    "filename": filename or subtask_name or "",
                    "registered_at": time.time(),
                }
                logger.info(
                    "[BED-COOL] Registered waiter for printer %s (threshold: %.0f°C)",
                    printer_id,
                    threshold,
                )
            else:
                logger.debug("[BED-COOL] No providers enabled for bed_cooled on printer %s", printer_id)
        except Exception as e:
            logger.warning("[BED-COOL] Failed to register waiter: %s", e)

    # --- Track filament consumption (must run before archive_id early-return so usage
    # is recorded even when auto-archive is disabled) ---
    usage_results: list[dict] = []
    # Prefer ams_mapping captured from MQTT request topic (works for all print sources)
    stored_ams_mapping = data.get("ams_mapping")
    # Fallback to _print_ams_mappings for queue/reprint (set before print starts)
    if not stored_ams_mapping and archive_id:
        stored_ams_mapping = _print_ams_mappings.pop(archive_id, None)

    # Always drain the plate_id register on completion — the session already
    # consumed it at print-start injection; leaving it would leak into the next
    # print on the same archive_id (rare but possible with reprints) (#1697).
    # Capture the popped value so the completion notification can scope the
    # archive-level (summed-across-plates per #1593) filament + time totals
    # down to the single plate that was actually printed (#1785).
    notify_plate_id: int | None = None
    if archive_id:
        notify_plate_id = _print_plate_ids.pop(archive_id, None)

    # Internal inventory: track AMS remain% deltas (skip if Spoolman handles usage)
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

    # Spoolman: report filament usage (requires archive_id for tracking data lookup)
    if archive_id:
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
                    await _cleanup_spoolman_tracking(
                        printer_id,
                        archive_id,
                        db,
                        last_layer_num=data.get("last_layer_num"),
                        last_progress=data.get("last_progress"),
                    )
            except Exception as e:
                logger.debug("[SPOOLMAN] Cleanup failed: %s", e)

    log_timing("Filament usage tracking")

    if not archive_id:
        logger.warning("Could not find archive for print complete: filename=%s, subtask=%s", filename, subtask_name)

        # Still send print-complete/failed/stopped notifications even without an archive.
        # Try to enrich with queue/library-file data so user-specific emails work too.
        async def _notify_no_archive():
            try:
                async with async_session() as db:
                    from backend.app.models.library import LibraryFile
                    from backend.app.models.print_queue import PrintQueueItem
                    from backend.app.models.printer import Printer

                    result = await db.execute(select(Printer).where(Printer.id == printer_id))
                    printer_obj = result.scalar_one_or_none()
                    p_name = printer_obj.name if printer_obj else f"Printer {printer_id}"

                    # Try to find the most-recent queue item for this printer so we can
                    # recover created_by_id and estimated print time.
                    # NOTE: By the time this task runs the queue item status has already
                    # been updated to a terminal state (completed/failed/cancelled), so
                    # we look for recently-completed items (within the last 5 minutes).
                    no_archive_data: dict | None = None
                    try:
                        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
                        q_result = await db.execute(
                            select(PrintQueueItem)
                            .where(PrintQueueItem.printer_id == printer_id)
                            .where(PrintQueueItem.status.in_(["completed", "failed", "cancelled"]))
                            .where(PrintQueueItem.completed_at >= cutoff)
                            .order_by(PrintQueueItem.completed_at.desc())
                            .limit(1)
                        )
                        queue_item = q_result.scalar_one_or_none()
                        if queue_item:
                            no_archive_data = {"created_by_id": queue_item.created_by_id}
                            # Pull estimated time from library file when available
                            if queue_item.library_file_id:
                                lib_result = await db.execute(
                                    select(LibraryFile).where(LibraryFile.id == queue_item.library_file_id)
                                )
                                lib_file = lib_result.scalar_one_or_none()
                                if lib_file and lib_file.print_time_seconds:
                                    no_archive_data["print_time_seconds"] = lib_file.print_time_seconds
                    except Exception as lookup_err:
                        logger.debug(
                            "[NOTIFY-BG] Could not look up queue item for no-archive notification: %s", lookup_err
                        )

                    # Enrich with usage tracker results (captured in enclosing scope)
                    if usage_results:
                        if no_archive_data is None:
                            no_archive_data = {}
                        total_from_usage = sum(r.get("weight_used", 0) for r in usage_results)
                        if total_from_usage > 0:
                            no_archive_data["actual_filament_grams"] = round(total_from_usage, 1)
                        no_archive_data["usage_results"] = usage_results

                    # Try MQTT remaining_time for print duration when no queue/library data
                    if no_archive_data and not no_archive_data.get("print_time_seconds"):
                        mqtt_remaining = data.get("remaining_time")
                        if mqtt_remaining and isinstance(mqtt_remaining, (int, float)) and mqtt_remaining > 0:
                            no_archive_data["print_time_seconds"] = int(mqtt_remaining)

                    ps = data.get("status", "completed")
                    logger.info(
                        "[NOTIFY-BG] Sending notification without archive: printer=%s, status=%s", printer_id, ps
                    )
                    await notification_service.on_print_complete(
                        printer_id, p_name, ps, data, db, archive_data=no_archive_data
                    )

                    # Send user-specific email if we have a created_by_id
                    if no_archive_data and no_archive_data.get("created_by_id"):
                        raw_filename = data.get("subtask_name") or data.get("filename", "Unknown")
                        await _dispatch_user_print_email(
                            ps,
                            no_archive_data["created_by_id"],
                            p_name,
                            raw_filename,
                            db,
                        )
                    logger.info("[NOTIFY-BG] Completed (no-archive path)")
            except Exception as e:
                logger.warning("[NOTIFY-BG] Failed to send notification without archive: %s", e, exc_info=True)

        spawn_background_task(_notify_no_archive(), name="notify-no-archive")
        return

    log_timing("Archive lookup")

    # Update archive status
    logger.info("[ARCHIVE] Updating archive %s status...", archive_id)
    try:
        async with async_session() as db:
            service = ArchiveService(db)
            status = data.get("status", "completed")

            hms_errors = data.get("hms_errors", []) if status == "failed" else None
            if hms_errors:
                logger.info("[ARCHIVE] HMS errors at failure: %s", hms_errors)
            failure_reason = derive_failure_reason(status, hms_errors)
            if failure_reason:
                logger.info("[ARCHIVE] failure_reason=%r (status=%s)", failure_reason, status)
            elif status == "failed" and hms_errors:
                logger.info("[ARCHIVE] HMS errors present but none matched a known failure-reason short code")

            await service.update_archive_status(
                archive_id,
                status=status,
                completed_at=(
                    datetime.now(timezone.utc) if status in ("completed", "failed", "aborted", "cancelled") else None
                ),
                failure_reason=failure_reason,
            )
            logger.info(
                "[ARCHIVE] Archive %s status updated to %s, failure_reason=%s", archive_id, status, failure_reason
            )

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
                # Back-fill created_by_id on reprint (#730): reprint reuses the
                # source archive row rather than creating a new one, so an
                # archive that was auto-created from a printer-initiated
                # print (created_by_id=NULL) would otherwise stay unattributed
                # forever. When we have a print-session user AND the archive
                # has no attribution yet, credit the current user. Never
                # overwrite an existing attribution — the original uploader
                # keeps ownership.
                _print_user_id = _print_user_info.get("user_id") if _print_user_info else None
                if archive.created_by_id is None and _print_user_id is not None:
                    archive.created_by_id = _print_user_id
                p_info = printer_manager.get_printer(printer_id)
                # Per-run actuals — written to PrintLogEntry so stats reflect
                # what THIS print actually used, not the source archive's
                # first-run values (#1378). Helper handles the partial-print
                # math (failed / cancelled / stopped get scaled to progress
                # or to tracked spool deltas).
                _run_status = data.get("status", "completed")
                _run_grams = _compute_run_filament_grams(
                    _run_status,
                    archive.filament_used_grams,
                    data.get("progress"),
                    usage_results,
                )

                # Per-run cost — prefer usage_results sum. For partial prints
                # we deliberately skip the topup-to-estimate logic in
                # usage_tracker (which assumes the print completed); the raw
                # tracked-spool sum is closer to what THIS run actually cost.
                _run_cost: float | None = None
                if usage_results:
                    _run_cost = sum(r.get("cost") or 0 for r in usage_results) or None
                if _run_cost is None and _run_status == "completed":
                    _run_cost = archive.cost

                await write_log_entry(
                    db,
                    archive_id=archive.id,
                    status=_run_status,
                    print_name=archive.print_name,
                    printer_name=p_info.name if p_info else None,
                    printer_id=printer_id,
                    started_at=archive.started_at,
                    completed_at=archive.completed_at,
                    filament_type=archive.filament_type,
                    filament_color=archive.filament_color,
                    filament_used_grams=_run_grams,
                    cost=_run_cost,
                    failure_reason=archive.failure_reason,
                    thumbnail_path=archive.thumbnail_path,
                    created_by_id=archive.created_by_id,
                    created_by_username=_print_user_info.get("username") if _print_user_info else None,
                )
                await db.commit()
                logger.info("[PRINT_LOG] Log entry written for archive %s", archive_id)
    except Exception as e:
        logger.warning("[PRINT_LOG] Failed to write log entry for archive %s: %s", archive_id, e)

    log_timing("Print log entry")

    # Run slow operations as background tasks to avoid blocking the event loop
    # These operations can take 5-10+ seconds and would freeze the UI if awaited

    async def _background_energy_calculation():
        """Calculate and save energy usage in background.

        Reads the starting kWh from the archive row (#941: persisted so a mid-print
        backend restart no longer loses per-print energy data).
        """
        try:
            logger.info("[ENERGY-BG] Starting energy calculation for archive %s", archive_id)
            async with async_session() as db:
                from backend.app.models.archive import PrintArchive

                archive = await db.get(PrintArchive, archive_id)
                if archive is None:
                    logger.warning("[ENERGY-BG] Archive %s no longer exists", archive_id)
                    return
                starting_kwh = archive.energy_start_kwh
                if starting_kwh is None:
                    logger.info("[ENERGY-BG] No start kWh recorded for archive %s", archive_id)
                    return

                plug_result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
                plug = plug_result.scalar_one_or_none()
                if plug is None:
                    logger.info("[ENERGY-BG] No smart plug for printer %s", printer_id)
                    return

                energy = await _get_plug_energy(plug, db)
                logger.info("[ENERGY-BG] Energy response: %s", energy)
                if not energy or energy.get("total") is None:
                    logger.warning("[ENERGY-BG] No 'total' in energy response")
                    return

                energy_used = round(energy["total"] - starting_kwh, 4)
                logger.info("[ENERGY-BG] Per-print energy: %s kWh", energy_used)
                if energy_used < 0:
                    logger.warning(
                        "[ENERGY-BG] Negative energy delta for archive %s (start=%s, end=%s) — counter reset?",
                        archive_id,
                        starting_kwh,
                        energy["total"],
                    )
                    return

                from backend.app.api.routes.settings import get_setting

                energy_cost_per_kwh = await get_setting(db, "energy_cost_per_kwh")
                cost_per_kwh = float(energy_cost_per_kwh) if energy_cost_per_kwh else 0.15
                energy_cost_value = round(energy_used * cost_per_kwh, 3)

                # First-run-only overwrite of archive.energy_kwh / energy_cost so a
                # reprint doesn't visually clobber the source archive's energy data
                # (#1378). Reprint energy lives in the matching PrintLogEntry below.
                from sqlalchemy import func

                from backend.app.models.print_log import PrintLogEntry

                existing_runs = await db.scalar(
                    select(func.count(PrintLogEntry.id)).where(PrintLogEntry.archive_id == archive_id)
                )
                if (existing_runs or 0) <= 1:
                    # 0 = legacy archive that pre-dates per-run logging; 1 = the row
                    # we just wrote for THIS print. Either way it's the first run.
                    archive.energy_kwh = energy_used
                    archive.energy_cost = energy_cost_value

                # Backfill the latest PrintLogEntry for this archive with energy
                # (write_log_entry above ran before this background task completed,
                # so energy fields are still NULL on that row).
                latest_run = await db.execute(
                    select(PrintLogEntry)
                    .where(PrintLogEntry.archive_id == archive_id)
                    .order_by(PrintLogEntry.id.desc())
                    .limit(1)
                )
                run_row = latest_run.scalar_one_or_none()
                if run_row is not None:
                    run_row.energy_kwh = energy_used
                    run_row.energy_cost = energy_cost_value

                await db.commit()
                logger.info("[ENERGY-BG] Saved: %s kWh, cost=%s", energy_used, energy_cost_value)
        except Exception as e:
            logger.warning("[ENERGY-BG] Failed: %s", e)

    async def _background_finish_photo() -> str | None:
        """Capture finish photo in background. Returns photo filename if captured."""
        try:
            logger.info("[PHOTO-BG] Starting finish photo capture for archive %s", archive_id)

            from backend.app.api.routes.camera import _active_chamber_streams, _active_streams, get_buffered_frame

            # Read phase: settings + printer + archive in a short session, released
            # BEFORE the capture pipeline below. The capture (timelapse last-frame,
            # stage-22 wait, external-camera grab, or a fresh RTSP shot) can take
            # tens of seconds; holding this session across it pinned one pooled
            # connection idle-in-transaction per finishing print (issue #2572).
            async with async_session() as db:
                from backend.app.api.routes.settings import get_setting
                from backend.app.models.archive import PrintArchive
                from backend.app.models.printer import Printer

                capture_enabled = await get_setting(db, "capture_finish_photo")
                if capture_enabled is not None and capture_enabled.lower() != "true":
                    return None
                if not archive_id:
                    return None

                printer = (await db.execute(select(Printer).where(Printer.id == printer_id))).scalar_one_or_none()
                archive = (
                    await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
                ).scalar_one_or_none()

            if not printer or not archive:
                return None

            import uuid
            from datetime import datetime
            from pathlib import Path

            if archive.file_path:
                archive_dir = app_settings.base_dir / Path(archive.file_path).parent
            else:
                logger.warning("[PHOTO-BG] Archive %s has no file_path, using fallback dir", archive_id)
                archive_dir = app_settings.archive_dir / str(archive.id)
            photo_filename = None

            # Prefer the timelapse last-frame source when a timelapse was
            # recording — it captures the moment after the toolhead parks
            # but before the bed drops, which the live-camera grab below
            # would miss (#1397). Skipped for external cameras (those have
            # their own framing and don't see a Bambu timelapse). Only
            # runs when the USER explicitly enabled timelapse for this
            # print — #1721 removed Bambuddy's force-on at dispatch
            # because it caused per-layer nozzle parking on Smooth-mode
            # slicer profiles.
            prefer_timelapse_source = bool(data.get("timelapse_was_active")) and not (
                printer.external_camera_enabled and printer.external_camera_url
            )

            if prefer_timelapse_source:
                photo_filename = await _capture_finish_photo_from_timelapse(
                    archive_id=archive_id,
                    archive_dir=archive_dir,
                )

            # #1721: replacement framing path — on_finish_photo_moment
            # pre-captured a frame at the stage-22 / FINISH edge (toolhead
            # parked, bed not yet dropped) and cached the JPEG bytes in
            # _stage22_finish_frames. Consume them now so the saved photo
            # has the better framing instead of the post-bed-drop angle
            # the live-camera fallback below would give.
            if not photo_filename:
                # #1790: on the FINISH-state fallback path the producer
                # task is dispatched back-to-back with this consumer, so
                # a bare pop would race past with an empty result and
                # the RTSP fallback below would collide with the
                # producer's still-in-flight grab (single-client RTSP
                # on Bambu printers). Wait for the producer to finish
                # or give up before touching the cache.
                in_flight = _stage22_finish_in_flight.pop(printer_id, None)
                if in_flight is not None:
                    try:
                        await asyncio.wait_for(in_flight.wait(), timeout=20.0)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "[PHOTO-BG] timed out waiting for stage-22 producer for printer %s — proceeding to fallback",
                            printer_id,
                        )
                cached_frame = _stage22_finish_frames.pop(printer_id, None)
                if cached_frame:
                    photos_dir = archive_dir / "photos"
                    photos_dir.mkdir(parents=True, exist_ok=True)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    photo_filename = f"finish_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
                    photo_path = photos_dir / photo_filename
                    await asyncio.to_thread(photo_path.write_bytes, cached_frame)
                    logger.info(
                        "[PHOTO-BG] Saved stage-22 pre-captured frame: %s (%d bytes)",
                        photo_filename,
                        len(cached_frame),
                    )

            # Fallback chain: external camera → buffered live frame →
            # fresh RTSP capture. Only runs if the timelapse path above
            # didn't already produce a photo.
            if not photo_filename:
                if printer.external_camera_enabled and printer.external_camera_url:
                    logger.info("[PHOTO-BG] Using external camera")
                    from backend.app.services.external_camera import capture_frame

                    frame_data = await capture_frame(
                        printer.external_camera_url,
                        printer.external_camera_type or "mjpeg",
                        snapshot_url=printer.external_camera_snapshot_url,
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
                    active_chamber_for_printer = [k for k in _active_chamber_streams if k.startswith(f"{printer_id}-")]
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

            # Write phase: attach the photo in a fresh short-lived session.
            if photo_filename:
                async with async_session() as db:
                    from backend.app.models.archive import PrintArchive

                    arch = await db.get(PrintArchive, archive_id)
                    if arch is not None:
                        photos = arch.photos or []
                        photos.append(photo_filename)
                        arch.photos = photos
                        await db.commit()
                logger.info("[PHOTO-BG] Saved: %s", photo_filename)
                return photo_filename
            return None
        except Exception as e:
            logger.warning("[PHOTO-BG] Failed: %s", e)
            return None

    spawn_background_task(_background_energy_calculation(), name="background-energy-calc")
    # Photo capture task - result will be used by notifications
    photo_task = spawn_background_task(_background_finish_photo(), name="background-finish-photo")
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
                        # Actual elapsed time from started_at/completed_at when both are
                        # populated (every terminal status sets completed_at after #1198).
                        # Falls back to None so the notification path can decide whether to
                        # render the slicer estimate as a last resort.
                        actual_time_seconds = None
                        if archive.started_at and archive.completed_at:
                            elapsed = (archive.completed_at - archive.started_at).total_seconds()
                            if elapsed > 0:
                                actual_time_seconds = int(elapsed)

                        archive_data = {
                            "print_time_seconds": archive.print_time_seconds,
                            "actual_time_seconds": actual_time_seconds,
                            "actual_filament_grams": archive.filament_used_grams,
                            "failure_reason": archive.failure_reason,
                            "created_by_id": archive.created_by_id,
                        }

                        # Scale filament usage for partial prints
                        if print_status != "completed" and archive.filament_used_grams:
                            progress = data.get("progress") or 0
                            scale = _partial_progress_scale(progress)
                            archive_data["actual_filament_grams"] = round(archive.filament_used_grams * scale, 1)
                            archive_data["progress"] = progress

                        # Pass per-slot data from archive.extra_data
                        if archive.extra_data and archive.extra_data.get("filament_slots"):
                            slots = archive.extra_data["filament_slots"]
                            if print_status != "completed":
                                scale = _partial_progress_scale(data.get("progress"))
                                slots = [{**s, "used_g": round(s["used_g"] * scale, 1)} for s in slots]
                            archive_data["filament_slots"] = slots

                        # Scope project-summed totals down to the plate that was
                        # actually printed — see _scope_notification_archive_data_to_plate
                        # for the why (#1785).
                        archive_data = _scope_notification_archive_data_to_plate(
                            archive_data,
                            archive.file_path,
                            notify_plate_id,
                            print_status,
                            data.get("progress"),
                            app_settings.base_dir,
                        )

                        # Enrich filament_grams from usage_results when archive has no 3MF data
                        if not archive_data.get("actual_filament_grams") and usage_results:
                            total_from_usage = sum(r.get("weight_used", 0) for r in usage_results)
                            if total_from_usage > 0:
                                archive_data["actual_filament_grams"] = round(total_from_usage, 1)

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

                # Send user-specific email notification
                if archive_data:
                    created_by_id = archive_data.get("created_by_id")
                    raw_filename = data.get("subtask_name") or data.get("filename", "Unknown")
                    await _dispatch_user_print_email(
                        print_status,
                        created_by_id,
                        printer_name,
                        raw_filename,
                        db,
                    )

                logger.info("[NOTIFY-BG] Completed")
        except Exception as e:
            logger.error("[NOTIFY-BG] Failed: %s", e, exc_info=True)

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

    spawn_background_task(_background_smart_plug(), name="background-smart-plug")
    spawn_background_task(_background_maintenance_check(), name="background-maintenance-check")

    # Notification task waits for photo capture to complete first (with timeout).
    # When a timelapse was recording, photo sourcing polls the per-print
    # timelapse for up to 60s (#1397) — extend the budget so the notification
    # carries the correct bed-up photo instead of falling through to the
    # live-cam grab. Adds ~30s of notification latency at worst on slow links.
    photo_wait_timeout = 75 if data.get("timelapse_was_active") else 45

    async def _photo_then_notify():
        """Wait for photo capture, then send notification with photo URL."""
        finish_photo = None
        try:
            finish_photo = await asyncio.wait_for(photo_task, timeout=photo_wait_timeout)
            logger.info("[PHOTO-NOTIFY] Photo task returned: %s", finish_photo)
        except TimeoutError:
            logger.warning(
                "[PHOTO-NOTIFY] Photo capture timed out after %ss, sending notification without photo",
                photo_wait_timeout,
            )
        except Exception as e:
            logger.warning("[PHOTO-NOTIFY] Photo task failed: %s", e)
        try:
            await _background_notifications(finish_photo)
        except Exception as e:
            logger.error("[PHOTO-NOTIFY] Notification sending failed: %s", e, exc_info=True)

    spawn_background_task(_photo_then_notify(), name="photo-then-notify")

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

    spawn_background_task(_background_layer_timelapse(), name="background-layer-timelapse")

    log_timing("All background tasks scheduled")

    # Auto-scan for timelapse if recording was active during the print
    if archive_id and data.get("timelapse_was_active") and data.get("status") == "completed":
        logger.info("[TIMELAPSE] Timelapse was active during print, scheduling auto-scan for archive %s", archive_id)
        # Schedule timelapse scan as background task with retries
        # The printer needs time to encode the video after print completion
        baseline = _timelapse_baselines.pop(printer_id, None)
        spawn_background_task(
            _scan_for_timelapse_with_retries(archive_id, baseline),
            name=f"scan-timelapse-{archive_id}",
        )
        log_timing("Timelapse scan scheduled")

    logger.info("[CALLBACK] on_print_complete finished for printer %s, archive %s", printer_id, archive_id)


# AMS sensor history recording
_ams_history_task: asyncio.Task | None = None
AMS_HISTORY_INTERVAL = 300  # Record every 5 minutes
AMS_HISTORY_RETENTION_DAYS = 30  # Keep data for 30 days
_ams_cleanup_counter = 0  # Track recordings to trigger periodic cleanup
# Track alarm cooldowns (printer_id:ams_id:type -> last_alarm_time)
_ams_alarm_cooldown: dict[str, datetime] = {}
AMS_ALARM_COOLDOWN_MINUTES = 60  # Don't send same alarm more than once per hour


def _ams_has_filament(ams_data: dict) -> bool:
    """True if this AMS unit has at least one tray slot holding filament.

    Bambu firmware reports loaded slots via `tray_exist_bits`, a per-AMS hex
    bitmap (one bit per tray slot — bit set = spool present). Empty AMS units
    still report sensor readings, but those readings are ambient and not
    actionable: no filament to dry, no humidity to push down. #1619 — gate
    humidity/temperature alarms on this check so empty units don't generate
    hourly noise. Sensor history still records regardless so the UI charts
    stay continuous.

    Fallback path inspects the `tray` array's `tray_type` fields for setups
    where `tray_exist_bits` is missing (some early-connection pushall shapes).
    """
    bits = ams_data.get("tray_exist_bits")
    if isinstance(bits, str) and bits.strip():
        try:
            return int(bits, 16) > 0
        except ValueError:
            pass
    trays = ams_data.get("tray")
    if isinstance(trays, list):
        return any(
            isinstance(t, dict) and isinstance(t.get("tray_type"), str) and t["tray_type"].strip() for t in trays
        )
    return False


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

                # Per-filament humidity threshold overrides (#1605) — resolved
                # per-AMS below from the loaded tray types. Reuses the same
                # resolver as the auto-drying scheduler so behavior stays in
                # lockstep across both consumers.
                from backend.app.services.print_scheduler import PrintScheduler

                per_type_humidity_thresholds: dict[str, int] = {}
                result = await db.execute(select(Settings).where(Settings.key == "ams_humidity_thresholds"))
                setting = result.scalar_one_or_none()
                if setting and setting.value:
                    try:
                        raw = json.loads(setting.value)
                        if isinstance(raw, dict):
                            for k, v in raw.items():
                                try:
                                    per_type_humidity_thresholds[str(k).upper() if k != "default" else "default"] = int(
                                        v
                                    )
                                except (TypeError, ValueError):
                                    continue
                    except (ValueError, TypeError):
                        pass  # Invalid JSON → no overrides, fall through to global threshold

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

                        # Skip alarm dispatch for empty AMS units — humidity /
                        # temperature readings are ambient with no filament to
                        # protect, and the hourly notification just becomes
                        # noise. Sensor history was already recorded above so
                        # the UI charts stay continuous (#1619). Per-AMS check
                        # so a multi-AMS setup with one loaded + one empty
                        # still alarms on the loaded unit.
                        if not _ams_has_filament(ams_data):
                            continue

                        # Resolve per-filament humidity threshold for this AMS
                        # unit (#1605). Falls back to the global ams_humidity_fair
                        # when no per-type overrides are configured.
                        trays = ams_data.get("tray", []) or []
                        effective_humidity_threshold = float(
                            PrintScheduler.resolve_humidity_threshold(
                                trays, per_type_humidity_thresholds, int(humidity_threshold)
                            )
                        )

                        # Check humidity alarm (only if above threshold)
                        if humidity is not None and humidity > effective_humidity_threshold:
                            cooldown_key = f"{printer.id}:{ams_id}:humidity"
                            last_alarm = _ams_alarm_cooldown.get(cooldown_key)
                            now = datetime.now(timezone.utc)
                            if (
                                last_alarm is None
                                or (now - last_alarm).total_seconds() >= AMS_ALARM_COOLDOWN_MINUTES * 60
                            ):
                                _ams_alarm_cooldown[cooldown_key] = now
                                logger.info(
                                    f"Sending humidity alarm for {printer.name} {ams_label}: {humidity}% > {effective_humidity_threshold}%"
                                )
                                try:
                                    # Call different notification method based on AMS type
                                    if is_ams_ht:
                                        await notification_service.on_ams_ht_humidity_high(
                                            printer.id,
                                            printer.name,
                                            ams_label,
                                            humidity,
                                            effective_humidity_threshold,
                                            db,
                                        )
                                    else:
                                        await notification_service.on_ams_humidity_high(
                                            printer.id,
                                            printer.name,
                                            ams_label,
                                            humidity,
                                            effective_humidity_threshold,
                                            db,
                                        )
                                except Exception as e:
                                    logger.warning("Failed to send humidity alarm: %s", e)

                        # Check temperature alarm (only if above threshold)
                        if temperature is not None and temperature > temp_threshold:
                            cooldown_key = f"{printer.id}:{ams_id}:temperature"
                            last_alarm = _ams_alarm_cooldown.get(cooldown_key)
                            now = datetime.now(timezone.utc)
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

                    cutoff = datetime.utcnow() - timedelta(days=retention_days)
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


# Printer sensor history recording (nozzle / bed / chamber)
_printer_sensor_history_task: asyncio.Task | None = None
PRINTER_SENSOR_HISTORY_INTERVAL = 60  # Record every minute — heaters move faster than AMS humidity
PRINTER_SENSOR_HISTORY_RETENTION_DAYS = 30
_printer_sensor_cleanup_counter = 0
# Sensor kinds tracked in state.temperatures — these are the normalised keys the
# MQTT parser writes, so we don't need to handle per-model field aliases here
# (nozzle_temper / left_nozzle_temper / right_nozzle_temper / chamber_temper
# are all collapsed by services/bambu_mqtt.py before they reach this loop).
_SENSOR_KINDS = ("nozzle", "nozzle_2", "bed", "chamber")
_SENSOR_TARGET_KEYS = {
    "nozzle": "nozzle_target",
    "nozzle_2": "nozzle_2_target",
    "bed": "bed_target",
    "chamber": "chamber_target",
}


async def record_printer_sensor_history():
    """Background task to record nozzle / bed / chamber readings.

    Pulls from `state.temperatures` (already normalised across all printer
    models by the MQTT parser) rather than re-parsing raw_data, so we get
    free coverage of dual-nozzle H2D, sensor-only X1C chamber, etc.
    """
    logger = logging.getLogger(__name__)

    await asyncio.sleep(10)

    while True:
        try:
            from backend.app.models.printer import Printer
            from backend.app.models.printer_sensor_history import PrinterSensorHistory
            from backend.app.models.settings import Settings

            async with async_session() as db:
                result = await db.execute(select(Printer).where(Printer.is_active.is_(True)))
                printers = result.scalars().all()

                recorded_count = 0
                for printer in printers:
                    state = printer_manager.get_status(printer.id)
                    if not state or not state.connected:
                        continue

                    temps = getattr(state, "temperatures", None) or {}
                    if not isinstance(temps, dict):
                        continue

                    for kind in _SENSOR_KINDS:
                        if kind not in temps:
                            continue
                        try:
                            value = float(temps[kind])
                        except (ValueError, TypeError):
                            continue

                        target_raw = temps.get(_SENSOR_TARGET_KEYS[kind])
                        target_val: float | None = None
                        if target_raw is not None:
                            try:
                                target_val = float(target_raw)
                            except (ValueError, TypeError):
                                target_val = None

                        db.add(
                            PrinterSensorHistory(
                                printer_id=printer.id,
                                sensor_kind=kind,
                                value=value,
                                target=target_val,
                            )
                        )
                        recorded_count += 1

                await db.commit()
                if recorded_count > 0:
                    logger.debug("Recorded %s printer sensor history entries", recorded_count)

                # Periodic cleanup — once every ~24h at this interval.
                global _printer_sensor_cleanup_counter
                _printer_sensor_cleanup_counter += 1
                cleanup_every = max(1, (24 * 60 * 60) // PRINTER_SENSOR_HISTORY_INTERVAL)
                if _printer_sensor_cleanup_counter >= cleanup_every:
                    _printer_sensor_cleanup_counter = 0
                    result = await db.execute(
                        select(Settings).where(Settings.key == "printer_sensor_history_retention_days")
                    )
                    setting = result.scalar_one_or_none()
                    retention_days = int(setting.value) if setting else PRINTER_SENSOR_HISTORY_RETENTION_DAYS

                    cutoff = datetime.utcnow() - timedelta(days=retention_days)
                    cleanup = await db.execute(
                        delete(PrinterSensorHistory).where(PrinterSensorHistory.recorded_at < cutoff)
                    )
                    await db.commit()
                    if cleanup.rowcount > 0:
                        logger.info(
                            "Cleaned up %s old printer sensor history entries (older than %s days)",
                            cleanup.rowcount,
                            retention_days,
                        )

            await asyncio.sleep(PRINTER_SENSOR_HISTORY_INTERVAL)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Printer sensor history recording failed: %s", e)
            await asyncio.sleep(60)


def start_printer_sensor_history_recording():
    global _printer_sensor_history_task
    if _printer_sensor_history_task is None:
        _printer_sensor_history_task = asyncio.create_task(record_printer_sensor_history())
        logging.getLogger(__name__).info("Printer sensor history recording started")


def stop_printer_sensor_history_recording():
    global _printer_sensor_history_task
    if _printer_sensor_history_task:
        _printer_sensor_history_task.cancel()
        _printer_sensor_history_task = None
        logging.getLogger(__name__).info("Printer sensor history recording stopped")


# Printer runtime tracking
_runtime_tracking_task: asyncio.Task | None = None
RUNTIME_TRACKING_INTERVAL = 30  # Update every 30 seconds


async def track_printer_runtime():
    """Background task to track printer active runtime (RUNNING state only).

    PAUSE is intentionally excluded — the runtime counter feeds hours-based
    maintenance intervals (rod lubrication, belt checks, nozzle cleaning)
    which track mechanical wear. Pause time has no motion and no wear, so
    counting it inflates maintenance warnings (#1521).
    """
    logger = logging.getLogger(__name__)

    # Wait for MQTT connections to establish on startup
    await asyncio.sleep(15)

    while True:
        try:
            from backend.app.models.printer import Printer

            # Fetch printer IDs in a short-lived read-only session
            async with async_session() as db:
                result = await db.execute(
                    select(Printer.id, Printer.name, Printer.runtime_seconds, Printer.last_runtime_update).where(
                        Printer.is_active.is_(True)
                    )
                )
                printer_rows = result.all()

            now = datetime.now(timezone.utc)
            updated_count = 0

            # Update each printer in its own short session to minimise write-lock
            # hold time and avoid blocking critical commits like queue status
            # updates (#897).
            for pid, pname, runtime_secs, last_update in printer_rows:
                state = printer_manager.get_status(pid)
                if not state:
                    logger.debug("[%s] Runtime tracking: no state available", pname)
                    continue
                if not state.connected:
                    logger.debug("[%s] Runtime tracking: not connected", pname)
                    continue

                needs_commit = False
                new_runtime = runtime_secs
                new_last_update = last_update

                if state.state == "RUNNING":
                    if last_update:
                        lu = last_update if last_update.tzinfo else last_update.replace(tzinfo=timezone.utc)
                        elapsed = (now - lu).total_seconds()
                        if elapsed > 0:
                            new_runtime = runtime_secs + int(elapsed)
                            updated_count += 1
                            needs_commit = True
                            logger.debug(
                                f"[{pname}] Runtime tracking: added {int(elapsed)}s, "
                                f"total={new_runtime}s ({new_runtime / 3600:.2f}h)"
                            )
                    else:
                        needs_commit = True
                        logger.debug("[%s] Runtime tracking: first active detection", pname)
                    new_last_update = now
                else:
                    if last_update is not None:
                        logger.debug(f"[{pname}] Runtime tracking: state={state.state}, clearing last_runtime_update")
                        new_last_update = None
                        needs_commit = True

                if needs_commit:
                    try:
                        async with async_session() as db:
                            result = await db.execute(select(Printer).where(Printer.id == pid))
                            printer = result.scalar_one_or_none()
                            if printer:
                                printer.runtime_seconds = new_runtime
                                printer.last_runtime_update = new_last_update
                                await db.commit()
                    except Exception as e:
                        logger.warning("[%s] Runtime tracking commit failed: %s", pname, e)

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


# SpoolBuddy device watchdog
_spoolbuddy_watchdog_task: asyncio.Task | None = None
SPOOLBUDDY_WATCHDOG_INTERVAL = 15


async def _spoolbuddy_watchdog_loop():
    """Periodic check for SpoolBuddy devices that have gone offline."""
    from backend.app.api.routes.spoolbuddy import spoolbuddy_watchdog

    while True:
        try:
            await spoolbuddy_watchdog()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.getLogger(__name__).warning("SpoolBuddy watchdog failed: %s", e)
        await asyncio.sleep(SPOOLBUDDY_WATCHDOG_INTERVAL)


def start_spoolbuddy_watchdog():
    global _spoolbuddy_watchdog_task
    if _spoolbuddy_watchdog_task is None:
        _spoolbuddy_watchdog_task = asyncio.create_task(_spoolbuddy_watchdog_loop())
        logging.getLogger(__name__).info("SpoolBuddy watchdog started")


def stop_spoolbuddy_watchdog():
    global _spoolbuddy_watchdog_task
    if _spoolbuddy_watchdog_task:
        _spoolbuddy_watchdog_task.cancel()
        _spoolbuddy_watchdog_task = None
        logging.getLogger(__name__).info("SpoolBuddy watchdog stopped")


# Camera stream orphan cleanup
_camera_cleanup_task: asyncio.Task | None = None
CAMERA_CLEANUP_INTERVAL = 60


async def _camera_cleanup_loop():
    """Periodically clean up orphaned ffmpeg processes."""
    from backend.app.api.routes.camera import cleanup_orphaned_streams

    while True:
        try:
            await cleanup_orphaned_streams()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.getLogger(__name__).warning("Camera stream cleanup failed: %s", e)
        await asyncio.sleep(CAMERA_CLEANUP_INTERVAL)


def start_camera_cleanup():
    global _camera_cleanup_task
    if _camera_cleanup_task is None:
        _camera_cleanup_task = asyncio.create_task(_camera_cleanup_loop())
        logging.getLogger(__name__).info("Camera stream cleanup started")


def stop_camera_cleanup():
    global _camera_cleanup_task
    if _camera_cleanup_task:
        _camera_cleanup_task.cancel()
        _camera_cleanup_task = None
        logging.getLogger(__name__).info("Camera stream cleanup stopped")


# ---------------------------------------------------------------------------
# Expected-print TTL eviction
# ---------------------------------------------------------------------------


def _evict_stale_expected_prints() -> None:
    """Remove entries from _expected_prints / _expected_print_creators that are
    older than _EXPECTED_PRINT_TTL_SECONDS.

    This prevents unbounded growth when a print is registered (via
    register_expected_print) but on_print_start never fires — e.g. because the
    printer disconnects, the app restarts, or the print is started directly from
    the printer panel without going through the queue.
    """
    # Use monotonic time so the TTL is unaffected by system clock adjustments
    # (e.g. NTP sync, DST changes).
    cutoff = time.monotonic() - _EXPECTED_PRINT_TTL_SECONDS
    stale_keys = [k for k, t in _expected_print_registered_at.items() if t < cutoff]
    if not stale_keys:
        return

    evicted_archive_ids: set[int] = set()
    for key in stale_keys:
        archive_id = _expected_prints.pop(key, None)
        if archive_id is not None:
            evicted_archive_ids.add(archive_id)
        _expected_print_creators.pop(key, None)
        _expected_print_registered_at.pop(key, None)

    # Also clean up _print_ams_mappings and _print_plate_ids for archive_ids
    # that have no remaining live keys in _expected_prints (all variants
    # were just evicted).
    live_archive_ids = set(_expected_prints.values())
    for archive_id in evicted_archive_ids:
        if archive_id not in live_archive_ids:
            _print_ams_mappings.pop(archive_id, None)
            _print_plate_ids.pop(archive_id, None)

    logging.getLogger(__name__).info(
        "Evicted %d stale expected-print entries (TTL=%ds)", len(stale_keys), _EXPECTED_PRINT_TTL_SECONDS
    )


async def _expected_prints_cleanup_loop() -> None:
    """Background task: periodically evict stale expected-print entries."""
    while True:
        try:
            _evict_stale_expected_prints()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.getLogger(__name__).warning("Expected prints cleanup failed: %s", e)
        await asyncio.sleep(_EXPECTED_PRINT_CLEANUP_INTERVAL)


def start_expected_prints_cleanup() -> None:
    global _expected_prints_cleanup_task
    if _expected_prints_cleanup_task is None:
        _expected_prints_cleanup_task = asyncio.create_task(_expected_prints_cleanup_loop())
        logging.getLogger(__name__).info("Expected prints cleanup started")


def stop_expected_prints_cleanup() -> None:
    global _expected_prints_cleanup_task
    if _expected_prints_cleanup_task:
        _expected_prints_cleanup_task.cancel()
        _expected_prints_cleanup_task = None
        logging.getLogger(__name__).info("Expected prints cleanup stopped")


# ---------------------------------------------------------------------------
# L-2: Periodic auth-token cleanup (stale TOTP + expired revoked JTIs)
# ---------------------------------------------------------------------------

_auth_cleanup_task: asyncio.Task | None = None
_AUTH_CLEANUP_INTERVAL = 3600  # seconds (hourly)


async def _run_auth_cleanup() -> None:
    """Single cleanup pass: remove stale TOTP records, expired revoked JTIs, and old rate-limit events."""
    from backend.app.core.database import async_session
    from backend.app.models.auth_ephemeral import AuthEphemeralToken, AuthRateLimitEvent
    from backend.app.models.user_totp import UserTOTP

    now = datetime.now(timezone.utc)

    # Remove unconfirmed (is_enabled=False) TOTP records older than 1 hour.
    try:
        async with async_session() as db:
            stale_cutoff = now - timedelta(hours=1)
            result = await db.execute(
                select(UserTOTP).where(
                    UserTOTP.is_enabled.is_(False),
                    UserTOTP.created_at < stale_cutoff,
                )
            )
            stale_records = result.scalars().all()
            if stale_records:
                for rec in stale_records:
                    await db.delete(rec)
                await db.commit()
                logging.info("Auth cleanup: removed %d stale unconfirmed TOTP record(s)", len(stale_records))
    except Exception as e:
        logging.warning("Auth cleanup: failed to purge stale TOTP records: %s", e)

    # Remove expired revoked-JTI entries (they are no longer needed once the
    # original token's exp has passed — the token would be rejected by JWT
    # signature verification regardless).
    try:
        async with async_session() as db:
            await db.execute(
                delete(AuthEphemeralToken).where(
                    AuthEphemeralToken.token_type == "revoked_jti",
                    AuthEphemeralToken.expires_at < now,
                )
            )
            await db.commit()
    except Exception as e:
        logging.warning("Auth cleanup: failed to purge expired revoked JTIs: %s", e)

    # L-R6-B: Purge AuthRateLimitEvent rows older than the lockout window (15 min).
    # Events outside this window can never affect rate-limit decisions — they only
    # consume DB space.  Use the same window constant as the rate limiter so the
    # two are always in sync.
    try:
        from backend.app.api.routes.mfa import LOCKOUT_WINDOW

        async with async_session() as db:
            await db.execute(
                delete(AuthRateLimitEvent).where(
                    AuthRateLimitEvent.occurred_at < now - LOCKOUT_WINDOW,
                )
            )
            await db.commit()
    except Exception as e:
        logging.warning("Auth cleanup: failed to purge stale rate-limit events: %s", e)


async def _auth_cleanup_loop() -> None:
    """Periodic background task: run auth cleanup every hour."""
    while True:
        try:
            await _run_auth_cleanup()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.warning("Auth cleanup loop error: %s", e)
        await asyncio.sleep(_AUTH_CLEANUP_INTERVAL)


def start_auth_cleanup() -> None:
    global _auth_cleanup_task
    if _auth_cleanup_task is None:
        _auth_cleanup_task = asyncio.create_task(_auth_cleanup_loop())
        logging.getLogger(__name__).info("Auth periodic cleanup started")


def stop_auth_cleanup() -> None:
    global _auth_cleanup_task
    if _auth_cleanup_task:
        _auth_cleanup_task.cancel()
        _auth_cleanup_task = None
        logging.getLogger(__name__).info("Auth periodic cleanup stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    # Install Windows-only asyncio Proactor cleanup-RST filter (#1113) before
    # anything else can spawn tasks that might trip it.
    from backend.app.core.asyncio_handlers import install_proactor_reset_filter

    install_proactor_reset_filter()

    await init_db()

    # Register an app-scoped httpx client for Bambu Cloud services so
    # per-request BambuCloudService instances reuse the same connection pool
    # (important for routes like /cloud/filament-info that chain many
    # get_setting_detail calls). The shared client stores no region/token
    # state, so the per-request ownership pattern that fixed the region-bleed
    # bug is preserved.
    import httpx as _httpx

    from backend.app.services.bambu_cloud import set_shared_http_client
    from backend.app.services.makerworld import (
        set_shared_http_client as set_shared_makerworld_http_client,
    )

    _shared_cloud_http_client = _httpx.AsyncClient(timeout=30.0)
    set_shared_http_client(_shared_cloud_http_client)
    # Reuse the same connection pool for MakerWorld — different host, same
    # keep-alive pool saves a TLS handshake per request.
    set_shared_makerworld_http_client(_shared_cloud_http_client)

    # Fix queue items stuck with invalid "aborted" status (should be "cancelled").
    # This can happen when a print was cancelled mid-print on versions before this fix.
    try:
        async with async_session() as db:
            from backend.app.models.print_queue import PrintQueueItem

            result = await db.execute(select(PrintQueueItem).where(PrintQueueItem.status == "aborted"))
            aborted_items = result.scalars().all()
            if aborted_items:
                for item in aborted_items:
                    item.status = "cancelled"
                await db.commit()
                logging.info("Fixed %d queue item(s) with invalid 'aborted' status → 'cancelled'", len(aborted_items))
    except Exception as e:
        logging.warning("Failed to fix aborted queue items: %s", e)

    # Restore debug logging state from previous session
    await init_debug_logging()

    # Set up printer manager callbacks
    loop = asyncio.get_event_loop()
    printer_manager.set_event_loop(loop)
    printer_manager.set_status_change_callback(on_printer_status_change)
    printer_manager.set_print_start_callback(on_print_start)
    printer_manager.set_print_complete_callback(on_print_complete)
    printer_manager.set_print_running_observed_callback(on_print_running_observed)
    printer_manager.set_finish_photo_moment_callback(on_finish_photo_moment)
    printer_manager.set_ams_change_callback(on_ams_change)

    # Rehydrate persisted awaiting-plate-clear gate (#961) so prompts survive restarts
    await printer_manager.load_awaiting_plate_clear_from_db()

    # Layer change callback for external camera timelapse
    async def on_layer_change(printer_id: int, layer_num: int):
        """Capture timelapse frame on layer change + first layer notification."""
        from backend.app.services.layer_timelapse import on_layer_change as tl_layer_change

        await tl_layer_change(printer_id, layer_num)

        # #1867: bank a recent in-print frame so the FINISH-state finish-photo
        # path (firmware that never emits stg_cur=22, e.g. A1 Mini) has a
        # pre-swap image to fall back on instead of a live grab of the swapped
        # plate. Layer-driven, so it freezes at the final object layer.
        await _maybe_bank_inprint_frame(printer_id, layer_num)

        # First layer complete notification (layer_num >= 2 means layer 1 is done).
        # Gate on actual printing state — Bambu firmware ticks layer_num during
        # the pre-print calibration sequence (homing / mesh-level / bed scan /
        # nozzle clean), so a bare layer_num check can fire minutes before the
        # first real extrusion. We require gcode_state == RUNNING and
        # mc_print_sub_stage in (0 = "Printing", None) so calibration sub-stages
        # (1, 9, 14, ...) are excluded. The window widens to [2, 10] because if
        # the layer counter advanced past 2 during PREPARE, the next on_layer_change
        # edge fires later; _first_layer_notified stays clear until we actually send
        # so a deferred re-evaluation can win. See issue #1837.
        if 2 <= layer_num <= 10 and not _first_layer_notified.get(printer_id, False):
            client = printer_manager.get_client(printer_id)
            state = client.state if client else None
            if not state or state.state != "RUNNING":
                return
            if state.mc_print_sub_stage not in (None, 0):
                return
            _first_layer_notified[printer_id] = True
            try:
                async with async_session() as db:
                    from backend.app.models.printer import Printer

                    result = await db.execute(select(Printer).where(Printer.id == printer_id))
                    printer = result.scalar_one_or_none()
                    if not printer:
                        return
                    printer_name = printer.name
                    filename = (state.subtask_name or state.gcode_file or "Unknown") if state else "Unknown"
                    total_layers = state.total_layers if state else 0

                    image_data = await _capture_snapshot_for_notification(
                        printer_id, printer, logging.getLogger(__name__)
                    )
                    await notification_service.on_first_layer_complete(
                        printer_id, printer_name, filename, total_layers, db, image_data=image_data
                    )
            except Exception as e:
                logging.getLogger(__name__).warning("First layer notification failed: %s", e)

    printer_manager.set_layer_change_callback(on_layer_change)

    # Event-driven bed cooldown: fires whenever bed_temper arrives via MQTT
    async def on_bed_temp_update(printer_id: int, bed_temp: float):
        waiter = _bed_cool_waiters.get(printer_id)
        if not waiter:
            return
        threshold = waiter["threshold"]
        if bed_temp > threshold:
            return
        # Bed is at or below threshold — fire notification and remove waiter
        waiter_info = _bed_cool_waiters.pop(printer_id, None)
        if not waiter_info:
            return  # Another callback already handled it
        bed_cool_logger = logging.getLogger(__name__)
        bed_cool_logger.info(
            "[BED-COOL] Bed cooled to %.1f°C on printer %s (threshold: %.0f°C)",
            bed_temp,
            printer_id,
            threshold,
        )
        try:
            printer_info = printer_manager.get_printer(printer_id)
            p_name = printer_info.name if printer_info else "Unknown"
            async with async_session() as db:
                await notification_service.on_bed_cooled(
                    printer_id=printer_id,
                    printer_name=p_name,
                    bed_temp=bed_temp,
                    threshold=threshold,
                    filename=waiter_info["filename"],
                    db=db,
                )
        except Exception as e:
            bed_cool_logger.warning("[BED-COOL] Failed to send notification: %s", e)

    printer_manager.set_bed_temp_update_callback(on_bed_temp_update)

    async def on_drying_complete(printer_id: int, ams_id: int):
        """Smart-plug auto-off-after-drying trigger (#1349).

        Fires once per AMS unit when ``dry_time`` falls from >0 to 0. The
        manager walks all plugs linked to this printer and turns off only
        the ones with ``auto_off_after_drying`` enabled, after their
        per-plug delay. Multiple AMS units finishing close together (e.g. a
        dual-AMS dry that ends within the same MQTT push) call this once
        per unit — the manager's ``_cancel_pending_off`` collapses
        repeated scheduling on the same plug to one timer, so duplicate
        fires are safe.
        """
        try:
            async with async_session() as db:
                await smart_plug_manager.on_drying_complete(printer_id, db)
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Failed to schedule auto-off-after-drying for printer %d (AMS %d): %s",
                printer_id,
                ams_id,
                e,
            )

    printer_manager.set_drying_complete_callback(on_drying_complete)

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
            from backend.app.services.mqtt_smart_plug import subscribe_plug_to_mqtt

            result = await db.execute(select(SmartPlug).where(SmartPlug.plug_type == "mqtt"))
            mqtt_plugs = result.scalars().all()
            restored = 0
            for plug in mqtt_plugs:
                if subscribe_plug_to_mqtt(mqtt_relay.smart_plug_service, plug):
                    restored += 1
            if restored:
                logging.info("Restored %s MQTT smart plug subscriptions", restored)

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
                    field_ok = await client.ensure_tag_extra_field()
                    if not field_ok:
                        logging.error("Spoolman tag extra field registration failed — NFC tag links may not persist")
                    # Register the BambuStudio slicer-preset fields used by the
                    # spool-edit / assign flow. Spoolman rejects PATCHes with
                    # unknown extra keys, so these must exist before any update
                    # that touches them.
                    for field_name in ("bambu_slicer_filament", "bambu_slicer_filament_name"):
                        if not await client.ensure_extra_field(field_name):
                            logging.warning(
                                "Spoolman extra field %r registration failed — "
                                "spool slicer-preset edits will return 502",
                                field_name,
                            )
                else:
                    logging.warning("Spoolman at %s is not reachable", spoolman_url)
            except Exception as e:
                logging.warning("Failed to auto-connect to Spoolman: %s", e)

    # Start the print scheduler
    spawn_background_task(print_scheduler.run(), name="print-scheduler")

    # Start the smart plug scheduler for time-based on/off
    smart_plug_manager.start_scheduler()

    # Resume any pending auto-offs that were interrupted by restart
    await smart_plug_manager.resume_pending_auto_offs()

    # Start the notification digest scheduler
    notification_service.start_digest_scheduler()

    # Start the GitHub backup scheduler
    await github_backup_service.start_scheduler()

    # Start the local backup scheduler
    await local_backup_service.start_scheduler()
    await obico_detection_service.start()

    # Start the library trash sweeper (#1008)
    await library_trash_service.start_scheduler()

    # Start the archive auto-purge sweeper (#1008 follow-up)
    await archive_purge_service.start_scheduler()

    # Start AMS history recording
    start_ams_history_recording()

    # Start printer sensor (nozzle / bed / chamber) history recording
    start_printer_sensor_history_recording()

    # Start printer runtime tracking
    start_runtime_tracking()

    # Start SpoolBuddy device watchdog
    start_spoolbuddy_watchdog()

    # Start camera stream orphan cleanup
    start_camera_cleanup()

    # Start expected-print TTL eviction (prevents memory leak when prints are
    # registered but on_print_start never fires)
    start_expected_prints_cleanup()

    # L-2: Start periodic auth cleanup (stale TOTP + expired revoked JTIs)
    start_auth_cleanup()

    # Event-loop stall watchdog: dumps all thread stacks to stderr if the loop
    # freezes (#1486 — silent "container hangs after adding a printer" reports).
    from backend.app.services.loop_watchdog import start_loop_watchdog

    start_loop_watchdog()

    # Initialize virtual printer manager and sync from DB
    from backend.app.services.virtual_printer import virtual_printer_manager

    virtual_printer_manager.set_session_factory(async_session)
    virtual_printer_manager.set_printer_manager(printer_manager)
    try:
        await virtual_printer_manager.sync_from_db()
        logging.info("Virtual printer manager synced from database")
    except Exception as e:
        logging.warning("Failed to sync virtual printers: %s", e)

    yield

    # Shutdown
    print_scheduler.stop()
    smart_plug_manager.stop_scheduler()
    notification_service.stop_digest_scheduler()
    github_backup_service.stop_scheduler()
    local_backup_service.stop_scheduler()
    library_trash_service.stop_scheduler()
    archive_purge_service.stop_scheduler()
    obico_detection_service.stop()
    stop_ams_history_recording()
    stop_printer_sensor_history_recording()
    stop_runtime_tracking()
    stop_spoolbuddy_watchdog()
    stop_camera_cleanup()
    from backend.app.services.loop_watchdog import stop_loop_watchdog

    stop_loop_watchdog()
    # Tear down all camera fan-out broadcasters (#1089) so subscribers exit
    # cleanly rather than waiting on a queue that nothing will ever fill.
    try:
        from backend.app.services.camera_fanout import shutdown_all_broadcasters

        await shutdown_all_broadcasters()
    except Exception as e:
        logging.warning("Failed to shut down camera broadcasters: %s", e)
    stop_expected_prints_cleanup()
    stop_auth_cleanup()
    printer_manager.disconnect_all()
    await close_spoolman_client()

    # Stop all virtual printer services
    await virtual_printer_manager.stop_all()

    await mqtt_smart_plug_service.disconnect(timeout=2)

    await mqtt_relay.disconnect(timeout=2)

    # Drop the shared Bambu Cloud HTTP client we registered at startup.
    set_shared_http_client(None)
    set_shared_makerworld_http_client(None)
    await _shared_cloud_http_client.aclose()

    # Checkpoint WAL (SQLite only) and close all database connections
    from backend.app.core.db_dialect import is_sqlite

    if is_sqlite():
        try:
            async with engine.begin() as conn:
                await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            logging.info("WAL checkpoint completed")
        except Exception as e:
            logging.warning("WAL checkpoint failed: %s", e)
    await engine.dispose()


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
    # Advanced auth status needed for login page
    "/api/v1/auth/advanced-auth/status",
    "/api/v1/auth/forgot-password",  # Password reset for advanced auth
    "/api/v1/auth/forgot-password/confirm",  # Complete password reset with token (H-6)
    # 2FA routes that are called BEFORE a JWT is issued (pre-auth flow)
    "/api/v1/auth/2fa/verify",  # Exchange pre_auth_token + 2FA code for JWT
    "/api/v1/auth/2fa/email/send",  # Send OTP email (pre_auth_token based)
    # OIDC routes that must be reachable without a JWT
    "/api/v1/auth/oidc/providers",  # Public list of enabled providers
    "/api/v1/auth/oidc/callback",  # Redirect target from OIDC provider
    "/api/v1/auth/oidc/exchange",  # Exchange short-lived OIDC token for JWT
    # Version check for updates (no sensitive data)
    "/api/v1/updates/version",
    # Metrics endpoint handles its own prometheus_token authentication
    "/api/v1/metrics",
    # Appliance bootstrap (#1589 follow-up): the SPA's i18n setup polls
    # this BEFORE a JWT is available to pick up the firstboot wizard's
    # hostname / timezone / locale and the chrony NTP-gate state. The
    # response contains user-set defaults and a public sync flag — no
    # secrets. Without this entry the global auth middleware returns 401
    # before the route handler runs, regardless of the route's own
    # "no auth required" intent.
    "/api/v1/system/appliance",
    # Cam Wall kiosk feed (#2531): a TV or Pi in kiosk mode has no login, so it
    # authenticates with a long-lived ``camwall``-scoped token in the query
    # string — exactly like the camera streams two lists below, and for the same
    # reason (no header to put a JWT in). "Public" here only means the middleware
    # steps aside; the route still runs RequireCamWallTokenIfAuthEnabled, which
    # rejects an absent, expired, revoked, or wrong-scoped token. In particular a
    # plain ``camera_stream`` token does NOT open this door.
    "/api/v1/camwall/printers",
}

# Route prefixes that are public (for routes with dynamic segments)
PUBLIC_API_PREFIXES = [
    # WebSocket connections handle their own auth
    "/api/v1/ws",
    # OIDC authorize redirects — include provider_id in path
    "/api/v1/auth/oidc/authorize/",
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
    # Slicer token-authenticated downloads — protocol handlers (bambustudioopen://,
    # orcaslicer://) cannot send auth headers. These endpoints validate a short-lived
    # download token in the URL path instead.
    "/dl/",  # /archives/{id}/dl/{token}/{filename}, /library/files/{id}/dl/{token}/{filename}
    # Obico ML API fetches JPEG frames by one-shot nonce (issue #172 follow-up).
    # The nonce itself is the credential: 32-byte random, single-use, ~30s TTL.
    "/obico/cached-frame/",  # /obico/cached-frame/{nonce}
]


_security_headers_logger = logging.getLogger("backend.app.main.security_headers")


def _parse_trusted_frame_origins() -> tuple[str, ...]:
    """Parse TRUSTED_FRAME_ORIGINS env var into a validated allowlist (#1191).

    Format: comma-separated list of ``scheme://host[:port]`` origins.

    Used by ``security_headers_middleware`` to relax ``frame-ancestors`` for
    trusted same-LAN deployments (e.g. Home Assistant Webpage panel embedding
    Bambuddy from a different port). Defaults to empty — strict ``'none'``.

    Invalid entries are dropped with a warning rather than failing startup, so
    a typo in one origin doesn't take the whole deployment down.
    """
    raw = os.environ.get("TRUSTED_FRAME_ORIGINS", "").strip()
    if not raw:
        return ()
    valid: list[str] = []
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            parsed = urlparse(candidate)
        except ValueError as e:
            _security_headers_logger.warning("TRUSTED_FRAME_ORIGINS: dropping %r — %s", candidate, e)
            continue
        if parsed.scheme not in ("http", "https"):
            _security_headers_logger.warning("TRUSTED_FRAME_ORIGINS: dropping %r — must be http(s)", candidate)
            continue
        if not parsed.netloc:
            _security_headers_logger.warning("TRUSTED_FRAME_ORIGINS: dropping %r — missing host", candidate)
            continue
        if parsed.path and parsed.path != "/":
            _security_headers_logger.warning("TRUSTED_FRAME_ORIGINS: dropping %r — paths not allowed", candidate)
            continue
        if parsed.query or parsed.fragment:
            _security_headers_logger.warning(
                "TRUSTED_FRAME_ORIGINS: dropping %r — query/fragment not allowed", candidate
            )
            continue
        if "*" in parsed.netloc:
            _security_headers_logger.warning("TRUSTED_FRAME_ORIGINS: dropping %r — wildcards not allowed", candidate)
            continue
        valid.append(f"{parsed.scheme}://{parsed.netloc}")
    if valid:
        _security_headers_logger.info("TRUSTED_FRAME_ORIGINS: %s", ", ".join(valid))
    return tuple(valid)


_TRUSTED_FRAME_ORIGINS: tuple[str, ...] = _parse_trusted_frame_origins()


def _frame_ancestors(default_value: str) -> str:
    """Compose the ``frame-ancestors`` CSP directive (#1191).

    ``default_value`` is the strict directive used when the operator has not
    configured ``TRUSTED_FRAME_ORIGINS`` — typically ``'none'`` (catch-all and
    docs) or ``'self'`` (gcode-viewer, served same-origin). When trusted origins
    are configured, ``'self'`` is always included so same-origin embedding never
    breaks even if an operator forgets to add their own origin to the list.
    """
    if _TRUSTED_FRAME_ORIGINS:
        return "frame-ancestors 'self' " + " ".join(_TRUSTED_FRAME_ORIGINS) + ";"
    return f"frame-ancestors {default_value};"


@app.middleware("http")
async def security_headers_middleware(request, call_next):
    """Add standard HTTP security headers to every response."""
    # Per-request nonce stamped into `script-src` (#1460). On its own this
    # changes nothing for Bambuddy's own pages — index.html has no inline
    # scripts since the SW registration moved to /sw-register.js. The reason
    # it's here is Cloudflare: a CF-fronted deployment has the bot-detection
    # script injected into the HTML on the edge, with a fresh hash on every
    # load (so hashes can't be allowlisted). When CF sees a nonce in our CSP,
    # it clones the same nonce onto its injected <script>, and the inline
    # script passes the policy without us needing 'unsafe-inline'. See
    # https://developers.cloudflare.com/cloudflare-challenges/challenge-types/javascript-detections/#if-you-have-a-content-security-policy-csp
    csp_nonce = secrets.token_urlsafe(16)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    # X-Frame-Options is the legacy cross-origin embedding control. Modern
    # browsers honour CSP frame-ancestors instead, and the legacy
    # `ALLOW-FROM <url>` syntax is deprecated and inconsistent across vendors.
    # When operators have explicitly allowlisted trusted frame origins (#1191
    # — typically Home Assistant on a different port), drop X-Frame-Options
    # and let the CSP-side frame-ancestors directive govern embedding.
    if not _TRUSTED_FRAME_ORIGINS:
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Content-Security-Policy for the React SPA.
    # Notes:
    #   - 'unsafe-inline' for style-src: React and UI libs inject inline styles at runtime.
    #   - connect-src ws:/wss:: MQTT/printer WebSocket connections.
    #   - img-src data: / blob:: base64 thumbnails and Blob-URL timelapse previews.
    #   - media-src blob:: timelapse video player uses Blob URLs.
    #   - font-src data:: some icon fonts are embedded as data URIs.
    if request.url.path.startswith("/gcode-viewer"):
        # The gcode viewer is embedded in an iframe served by this same origin,
        # so frame-ancestors must allow 'self'.  prettygcode.js also uses eval()
        # internally, so script-src needs 'unsafe-eval'.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "connect-src 'self' ws: wss:; "
            "font-src 'self' data:; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-src 'self' http: https:; " + _frame_ancestors("'self'")
        )
    elif request.url.path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
        # FastAPI's built-in Swagger UI / ReDoc pages load assets from
        # cdn.jsdelivr.net and bootstrap with an inline <script>, so the
        # default CSP would render a blank page.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "img-src 'self' data: blob: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
            "connect-src 'self'; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            "worker-src 'self' blob:; "
            "object-src 'none'; "
            "base-uri 'self'; " + _frame_ancestors("'none'")
        )
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{csp_nonce}'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "connect-src 'self' ws: wss:; "
            "font-src 'self' data:; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-src 'self' http: https:; " + _frame_ancestors("'none'")
        )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


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

    # Check if auth is enabled. Fail CLOSED on any exception during the
    # probe — GHSA-6mf4-q26m-47pv: the previous fail-open path here let
    # an attacker who could force a DB exception (e.g. file-descriptor
    # exhaustion via login flood) bypass auth on every protected endpoint.
    try:
        async with async_session() as db:
            from backend.app.core.auth import is_auth_enabled

            auth_enabled = await is_auth_enabled(db)

        if not auth_enabled:
            # Auth disabled, allow all requests
            return await call_next(request)
    except Exception:
        logging.getLogger(__name__).exception("auth_middleware: failing closed on auth-probe error from %s", path)
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication service temporarily unavailable"},
        )

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
        from backend.app.core.auth import (
            ALGORITHM,
            SECRET_KEY,
            _is_token_fresh,
            get_user_by_username,
            is_jti_revoked,
        )

        token = auth_header.replace("Bearer ", "")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise ValueError("No username in token")
        jti = payload.get("jti")
        if not jti:
            raise ValueError("No jti in token")
        iat = payload.get("iat")

        # Reject revoked tokens (defense-in-depth gateway check)
        if await is_jti_revoked(jti):
            return JSONResponse(
                status_code=401,
                content={"detail": "Token has been revoked"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Verify user exists, is active, and token is still fresh (L-R8-A)
        async with async_session() as db:
            user = await get_user_by_username(db, username)
            if not user or not user.is_active:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "User not found or inactive"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if not _is_token_fresh(iat, user):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Token no longer valid"},
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


@app.middleware("http")
async def trace_id_middleware(request, call_next):
    """Stamp every HTTP request with a trace ID and echo it back.

    Decorated AFTER auth_middleware on purpose: Starlette stacks
    @app.middleware decorators LIFO, so the last-decorated runs first
    inbound. Putting the trace stamp last makes it the OUTERMOST layer,
    which means auth-middleware log lines (and every line emitted on the
    way down to and back from the route handler) all carry the same
    trace ID. If we put it before auth, auth's logs would be stamped
    with the *previous* request's ID — useless for correlation.

    Honours an inbound ``X-Trace-Id`` header so callers running their
    own tracing can correlate their span IDs with our log lines, but
    only if the value passes the whitelist gate in
    ``backend.app.core.trace.normalise_inbound_trace_id`` — anything
    rejected (too long, contains control chars, etc.) silently triggers
    a freshly minted server-side ID rather than failing the request.

    The minted (or echoed) ID is set on a ContextVar so that every log
    record emitted during the request — application logs *and* uvicorn's
    access log — carries it via TraceIDFilter, and is also written to
    the ``X-Trace-Id`` response header so clients can pin a server-side
    log search to the exact request they made.
    """
    from backend.app.core.trace import (
        generate_trace_id,
        normalise_inbound_trace_id,
        trace_id_var,
    )

    inbound = normalise_inbound_trace_id(request.headers.get("X-Trace-Id"))
    trace_id = inbound if inbound is not None else generate_trace_id()

    token = trace_id_var.set(trace_id)
    try:
        response = await call_next(request)
    finally:
        # Reset the ContextVar so a record emitted in a totally
        # unrelated background task that just happens to inherit this
        # context doesn't keep referencing this request's ID forever.
        # In practice ContextVar.reset is best-effort under asyncio
        # task-spawn semantics, but the cost is one attribute write so
        # we may as well do it.
        trace_id_var.reset(token)

    response.headers["X-Trace-Id"] = trace_id
    return response


# API routes
app.include_router(auth.router, prefix=app_settings.api_prefix)
app.include_router(mfa.router, prefix=app_settings.api_prefix)
app.include_router(bug_report.router, prefix=app_settings.api_prefix)
app.include_router(users.router, prefix=app_settings.api_prefix)
app.include_router(groups.router, prefix=app_settings.api_prefix)
app.include_router(printers.router, prefix=app_settings.api_prefix)
app.include_router(archives.router, prefix=app_settings.api_prefix)
app.include_router(filaments.router, prefix=app_settings.api_prefix)
app.include_router(inventory.router, prefix=app_settings.api_prefix)
app.include_router(labels.router, prefix=app_settings.api_prefix)
app.include_router(settings_routes.router, prefix=app_settings.api_prefix)
app.include_router(cloud.router, prefix=app_settings.api_prefix)
app.include_router(orca_cloud.router, prefix=app_settings.api_prefix)
app.include_router(local_presets.router, prefix=app_settings.api_prefix)
app.include_router(smart_plugs.router, prefix=app_settings.api_prefix)
app.include_router(print_log.router, prefix=app_settings.api_prefix)
app.include_router(print_queue.router, prefix=app_settings.api_prefix)
app.include_router(kprofiles.router, prefix=app_settings.api_prefix)
app.include_router(notifications.router, prefix=app_settings.api_prefix)
app.include_router(notification_templates.router, prefix=app_settings.api_prefix)
app.include_router(user_notifications.router, prefix=app_settings.api_prefix)
app.include_router(spoolman.router, prefix=app_settings.api_prefix)
app.include_router(spoolman_inventory.router, prefix=app_settings.api_prefix)
app.include_router(updates.router, prefix=app_settings.api_prefix)
app.include_router(sponsor_prompt.router, prefix=app_settings.api_prefix)
app.include_router(maintenance.router, prefix=app_settings.api_prefix)
app.include_router(camera.router, prefix=app_settings.api_prefix)
app.include_router(camwall.router, prefix=app_settings.api_prefix)
app.include_router(external_links.router, prefix=app_settings.api_prefix)
app.include_router(projects.router, prefix=app_settings.api_prefix)
app.include_router(library.router, prefix=app_settings.api_prefix)
app.include_router(library_tags.router, prefix=app_settings.api_prefix)
app.include_router(library_trash.router, prefix=app_settings.api_prefix)
app.include_router(slice_jobs.router, prefix=app_settings.api_prefix)
app.include_router(slicer_pipelines.router, prefix=app_settings.api_prefix)
app.include_router(pipeline_runs.pipeline_run_create_router, prefix=app_settings.api_prefix)
app.include_router(pipeline_runs.pipeline_run_router, prefix=app_settings.api_prefix)
app.include_router(slicer_presets.router, prefix=app_settings.api_prefix)
app.include_router(archive_purge.router, prefix=app_settings.api_prefix)
app.include_router(makerworld.router, prefix=app_settings.api_prefix)
app.include_router(api_keys.router, prefix=app_settings.api_prefix)
app.include_router(webhook.router, prefix=app_settings.api_prefix)
app.include_router(ams_history.router, prefix=app_settings.api_prefix)
app.include_router(printer_sensor_history.router, prefix=app_settings.api_prefix)
app.include_router(system.router, prefix=app_settings.api_prefix)
app.include_router(support.router, prefix=app_settings.api_prefix)
app.include_router(websocket.router, prefix=app_settings.api_prefix)
app.include_router(discovery.router, prefix=app_settings.api_prefix)
app.include_router(pending_uploads.router, prefix=app_settings.api_prefix)
app.include_router(firmware.router, prefix=app_settings.api_prefix)
app.include_router(github_backup.router, prefix=app_settings.api_prefix)
app.include_router(local_backup.router, prefix=app_settings.api_prefix)
app.include_router(obico.router, prefix=app_settings.api_prefix)
app.include_router(metrics.router, prefix=app_settings.api_prefix)
app.include_router(virtual_printers.router, prefix=app_settings.api_prefix)
app.include_router(spoolbuddy.router, prefix=app_settings.api_prefix)


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
    # Self-hosted Inter woff2 files (#1460). Without this mount /fonts/*.woff2
    # falls through to the SPA catch-all and returns index.html, which the
    # browser's font sanitizer rejects ("downloadable font: rejected by
    # sanitizer").
    if (app_settings.static_dir / "fonts").exists():
        app.mount(
            "/fonts",
            StaticFiles(directory=app_settings.static_dir / "fonts"),
            name="fonts",
        )


@app.get("/")
async def serve_frontend():
    """Serve the React frontend."""
    index_file = app_settings.static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file, headers=_HTML_CACHE_HEADERS)
    return {
        "message": "Bambuddy API",
        "docs": "/docs",
        "frontend": "Build and place React app in /static directory",
    }


# index.html must always be revalidated — Vite emits content-hashed JS/CSS
# bundles (e.g. `index-JRaF_JhW.js`), so the JS itself is safe to cache
# forever, but the HTML wrapping it is the only file that knows which hash
# is current. Without explicit cache-control headers Chromium decides
# heuristically (typically 10% of the time since Last-Modified) and on
# long-running kiosks happily serves stale HTML across browser restarts.
# That stale HTML references an old bundle hash, the old bundle is also
# in the disk cache, and the user ends up running pre-update JS forever
# without ever knowing why. ``no-cache`` (revalidate every time, but a
# 304 is cheap) is the correct setting for an SPA's entry HTML.
_HTML_CACHE_HEADERS = {"Cache-Control": "no-cache, must-revalidate"}


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


# GET + HEAD on the three PWA bootstrap routes (#1460). Scanners and a plain
# `curl -I` use HEAD; FastAPI's @app.get only registers GET, so HEAD answers
# with 405 Method Not Allowed and shows up as a "broken manifest" red herring
# in deployment debugging.
@app.api_route("/manifest.json", methods=["GET", "HEAD"])
async def serve_manifest():
    """Serve PWA manifest."""
    manifest_file = app_settings.static_dir / "manifest.json"
    if manifest_file.exists():
        return FileResponse(manifest_file, media_type="application/manifest+json")
    return {"error": "Manifest not found"}


@app.api_route("/sw.js", methods=["GET", "HEAD"])
async def serve_service_worker():
    """Serve service worker."""
    sw_file = app_settings.static_dir / "sw.js"
    if sw_file.exists():
        return FileResponse(
            sw_file,
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return {"error": "Service worker not found"}


@app.api_route("/sw-register.js", methods=["GET", "HEAD"])
async def serve_sw_register():
    """Serve the service-worker registration bootstrap script.

    Served as a real JS file so the strict `script-src 'self'` CSP covers it
    without needing 'unsafe-inline' or per-build hashes on the inline tag.
    """
    reg_file = app_settings.static_dir / "sw-register.js"
    if reg_file.exists():
        return FileResponse(reg_file, media_type="application/javascript")
    return {"error": "sw-register.js not found"}


# ── GCode viewer static files ────────────────────────────────────────────────
# Served via explicit routes so ordering is guaranteed (app.mount() loses
# to the /{full_path:path} catch-all in some Starlette versions).
_gcode_viewer_dir = (app_settings.static_dir.parent / "gcode_viewer").resolve()

# Surface packaging gaps at startup instead of as silent runtime 404s. If the
# directory is missing the explicit @app.get("/gcode-viewer/...") routes below
# return bare HTTPException(404) which renders as {"detail":"Not Found"} in
# the 3D Preview iframe (#1218) — easy to miss in normal operation, easy to
# spot if the operator scans the startup log or a support bundle.
if not (_gcode_viewer_dir / "index.html").is_file():
    logging.getLogger(__name__).error(
        "Embedded GCode viewer assets missing at %s — /gcode-viewer/ will return 404 "
        "and 3D Preview will fail. This indicates a packaging bug; the gcode_viewer/ "
        "directory must be present alongside static/.",
        _gcode_viewer_dir,
    )


def _gcode_viewer_response(rel: str) -> FileResponse:
    from fastapi import HTTPException as _HTTPException

    safe = (_gcode_viewer_dir / rel).resolve()
    if not safe.is_relative_to(_gcode_viewer_dir):
        raise _HTTPException(status_code=403)
    if safe.is_file():
        mt, _ = _mimetypes.guess_type(str(safe))
        return FileResponse(str(safe), media_type=mt or "application/octet-stream")
    raise _HTTPException(status_code=404)


@app.get("/gcode-viewer/")
async def serve_gcode_viewer_index() -> FileResponse:
    """Raw PrettyGCode viewer for the iframe. The bare ``/gcode-viewer``
    (no trailing slash) intentionally falls through to the SPA catch-all so a
    full-page reload re-enters the React layout instead of serving the iframe
    contents standalone."""
    return _gcode_viewer_response("index.html")


@app.get("/gcode-viewer/{file_path:path}")
async def serve_gcode_viewer_file(file_path: str) -> FileResponse:
    return _gcode_viewer_response(file_path)


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
        return FileResponse(index_file, headers=_HTML_CACHE_HEADERS)

    return {"error": "Frontend not built"}
