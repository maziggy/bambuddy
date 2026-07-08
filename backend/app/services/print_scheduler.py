"""Print scheduler service - processes the print queue."""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.config import settings
from backend.app.core.database import async_session, run_with_retry
from backend.app.core.tasks import spawn_background_task
from backend.app.core.websocket import ws_manager
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.smart_plug import SmartPlug
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.bambu_ftp import (
    cache_3mf_download,
    delete_file_async,
    get_ftp_retry_settings,
    upload_file_async,
    with_ftp_retry,
)
from backend.app.services.filament_deficit import compute_deficit_for_queue_item
from backend.app.services.notification_service import notification_service
from backend.app.services.printer_manager import (
    printer_manager,
    supports_airduct,
    supports_chamber_heater,
    supports_chamber_temp,
    supports_drying,
    supports_drying_while_printing,
)
from backend.app.services.smart_plug_manager import smart_plug_manager
from backend.app.utils.filename import derive_remote_filename
from backend.app.utils.printer_models import normalize_printer_model

logger = logging.getLogger(__name__)

# Dispatch-toast progress throttling (#1625 follow-up). Mirrors the legacy
# background_dispatch.py upload_progress_callback (200 ms time gate + 256 KB
# byte gate) from before the scheduler unification. Time gate keeps small
# files from going silent (a single 8 KB chunk fires once and that's it);
# byte gate caps the broadcast rate on slow LAN where 200 ms covers many
# chunks. uploaded >= total always emits so the bar closes cleanly even on
# sub-200 ms files.
_DISPATCH_PROGRESS_BYTE_STEP = 256 * 1024
_DISPATCH_PROGRESS_MIN_INTERVAL_SECS = 0.2


class _UploadProgressBridge:
    """Thread-safe bridge from ``upload_file_async`` to the WS broadcaster.

    ``upload_file_async`` runs the FTP transfer in an executor thread and
    invokes its ``progress_callback`` from that thread, so the callback
    body cannot ``await`` directly. This bridge captures the asyncio loop
    at construction (on the scheduler thread) and uses
    ``run_coroutine_threadsafe`` to hop back. The byte/time throttle
    matches the legacy background_dispatch.py path 1:1 so the toast feels
    identical to the pre-#1625 experience.

    Failures inside the emit are swallowed — progress is a UX nicety, the
    upload itself must not fail because of a WS hiccup.
    """

    def __init__(self, user_id: int | None, queue_item_id: int):
        self._user_id = user_id
        self._queue_item_id = queue_item_id
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._last_emit_bytes = 0
        self._last_emit_monotonic = 0.0
        self._has_emitted = False

    def __call__(self, bytes_transferred: int, total_bytes: int) -> None:
        if self._loop is None or total_bytes <= 0:
            return
        now = time.monotonic()
        # Mirrors legacy bg-dispatch: emit if first call OR upload complete
        # OR 200 ms elapsed OR ≥256 KB transferred since last emit. Two of
        # the four matter most: first-call so the user sees something even
        # for sub-chunk-size files; uploaded >= total so the bar locks at
        # 100% even when the throttle would otherwise eat it.
        should_emit = (
            not self._has_emitted
            or bytes_transferred >= total_bytes
            or now - self._last_emit_monotonic >= _DISPATCH_PROGRESS_MIN_INTERVAL_SECS
            or bytes_transferred - self._last_emit_bytes >= _DISPATCH_PROGRESS_BYTE_STEP
        )
        if not should_emit:
            return
        self._has_emitted = True
        self._last_emit_bytes = bytes_transferred
        self._last_emit_monotonic = now
        try:
            asyncio.run_coroutine_threadsafe(
                ws_manager.send_queue_item_upload_progress(
                    user_id=self._user_id,
                    queue_item_id=self._queue_item_id,
                    bytes_transferred=bytes_transferred,
                    total_bytes=total_bytes,
                ),
                self._loop,
            )
        except Exception:
            pass  # progress is best-effort, never block the upload


# Bambu firmware states that mean the project_file has actually been accepted
# and the printer is now processing / running / paused mid-print. Used by the
# dispatch watchdog (#1370): a transition into one of these states means the
# print landed, anything else (e.g. FINISH -> IDLE after the user dismisses
# a post-print prompt) is NOT a valid "command landed" signal even though the
# state value did change. SLICING is included because some firmwares park
# briefly in SLICING between PREPARE and RUNNING while parsing the g-code.
_ACTIVE_PRINT_STATES: frozenset[str] = frozenset({"PREPARE", "SLICING", "RUNNING", "PAUSE"})

# Filament type equivalence groups — types within the same group are
# interchangeable on the printer side (Bambu Lab firmware treats them as compatible).
_FILAMENT_TYPE_GROUPS: list[list[str]] = [
    ["PA-CF", "PA12-CF", "PAHT-CF"],
]
_FILAMENT_EQUIV_MAP: dict[str, str] = {}
for _group in _FILAMENT_TYPE_GROUPS:
    _canonical = _group[0].upper()
    for _t in _group:
        _FILAMENT_EQUIV_MAP[_t.upper()] = _canonical


def _canonical_filament_type(ftype: str) -> str:
    """Return canonical type for equivalence matching."""
    upper = ftype.upper()
    return _FILAMENT_EQUIV_MAP.get(upper, upper)


def _installed_nozzle_diameters(status) -> list[float]:
    """Parse the installed nozzle diameters from a PrinterState (#1899).

    Returns the diameters the printer actually reports (e.g. [0.4] single-nozzle,
    [0.4, 0.6] dual-nozzle), skipping the empty-string defaults that populate a
    NozzleInfo before MQTT fills it in. An empty list means "the printer hasn't
    told us its nozzle hardware" — callers must treat that as unknown, not as a
    mismatch, so we never block a print on missing data.
    """
    diameters: list[float] = []
    for nozzle in getattr(status, "nozzles", None) or []:
        raw = getattr(nozzle, "nozzle_diameter", "") or ""
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            diameters.append(value)
    return diameters


def _nozzle_mismatch_message(sliced_nozzle: float | None, installed: list[float]) -> str | None:
    """Return an actionable error message when the sliced nozzle can't be
    printed on any installed nozzle, else None (#1899).

    Fail-safe: returns None whenever we lack the data to judge — no sliced
    diameter, or the printer reported no nozzles — so a print is only ever
    blocked on a POSITIVE mismatch. On dual-nozzle printers a match against
    EITHER installed nozzle passes (a 0.6 slice is fine if one hotend is 0.6).
    The 0.05 tolerance absorbs float noise while staying well inside the 0.2
    gap between adjacent nozzle sizes (0.2/0.4/0.6/0.8).
    """
    if not sliced_nozzle or not installed:
        return None
    if any(abs(d - sliced_nozzle) < 0.05 for d in installed):
        return None
    installed_str = " / ".join(f"{d:g}mm" for d in installed)
    return (
        f"File sliced for a {sliced_nozzle:g}mm nozzle, but the printer has "
        f"{installed_str} installed. Re-slice for the installed nozzle, or "
        f"install the matching nozzle before printing."
    )


class PrintScheduler:
    """Background scheduler that processes the print queue."""

    # Built-in drying presets per filament type (from BambuStudio filament profiles)
    # Format: { n3f_temp, n3s_temp, n3f_hours, n3s_hours }
    DEFAULT_DRYING_PRESETS: dict[str, dict[str, int]] = {
        "PLA": {"n3f": 45, "n3s": 45, "n3f_hours": 12, "n3s_hours": 12},
        "PETG": {"n3f": 65, "n3s": 65, "n3f_hours": 12, "n3s_hours": 12},
        "TPU": {"n3f": 65, "n3s": 75, "n3f_hours": 12, "n3s_hours": 18},
        "ABS": {"n3f": 65, "n3s": 80, "n3f_hours": 12, "n3s_hours": 8},
        "ASA": {"n3f": 65, "n3s": 80, "n3f_hours": 12, "n3s_hours": 8},
        "PA": {"n3f": 65, "n3s": 85, "n3f_hours": 12, "n3s_hours": 12},
        "PC": {"n3f": 65, "n3s": 80, "n3f_hours": 12, "n3s_hours": 8},
        "PVA": {"n3f": 65, "n3s": 85, "n3f_hours": 12, "n3s_hours": 18},
    }

    def __init__(self):
        self._running = False
        self._check_interval = 30  # seconds
        self._power_on_wait_time = 180  # seconds to wait for printer after power on (3 min)
        self._power_on_check_interval = 10  # seconds between connection checks
        # Track which printers are currently auto-drying (printer_id -> start timestamp)
        self._drying_in_progress: dict[int, float] = {}
        # Defensive in-memory dispatch hold (#1157): a printer that just received
        # a project_file command must not get a second dispatch until either it
        # transitions out of pre_state OR the hard timeout expires. The H2D Pro
        # can take 80–210 s to flip FINISH→PREPARE after project_file, and
        # during that window the DB busy_printers seed is empirically unreliable
        # (multi-plate batches double-/triple-dispatched onto the same printer
        # 30 s apart). Keyed by printer_id; cleared by the watchdog on success
        # or revert.
        # printer_id -> (monotonic_started_at, pre_state, pre_subtask_id)
        self._dispatch_holds: dict[int, tuple[float, str, str | None]] = {}
        # Minimum cooldown between dispatches to the same printer (covers the
        # H2D's project_file digestion window).
        self._dispatch_min_cooldown = 60.0
        # Hard timeout — drop the hold even if we never observed a transition,
        # so a lost MQTT session can't lock a printer out of the queue forever.
        # Matches the watchdog timeout (90 s) plus a safety margin so the
        # watchdog runs first on the unhappy path.
        self._dispatch_max_hold = 180.0

    async def run(self):
        """Main loop - check queue every interval."""
        self._running = True
        logger.info("Print scheduler started")

        while self._running:
            try:
                await self.check_queue()
            except Exception as e:
                logger.error("Scheduler error: %s", e)

            await asyncio.sleep(self._check_interval)

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        logger.info("Print scheduler stopped")

    async def check_queue(self):
        """Check for prints ready to start."""
        async with async_session() as db:
            # Check if shortest-job-first scheduling is enabled
            sjf_enabled = await self._get_bool_setting(db, "queue_shortest_first")

            # Get all pending items, ordered by printer and position (or SJF order)
            if sjf_enabled:
                # SJF: group by printer (and target_model for model-based jobs),
                # then items already jumped get top priority (starvation guard),
                # then sort by print_time ascending. Items with no print time go last.
                result = await db.execute(
                    select(PrintQueueItem)
                    .where(PrintQueueItem.status == "pending")
                    .order_by(
                        PrintQueueItem.printer_id,
                        PrintQueueItem.target_model,
                        PrintQueueItem.been_jumped.desc(),
                        PrintQueueItem.print_time_seconds.asc().nullslast(),
                        PrintQueueItem.position,
                    )
                )
            else:
                result = await db.execute(
                    select(PrintQueueItem)
                    .where(PrintQueueItem.status == "pending")
                    .order_by(PrintQueueItem.printer_id, PrintQueueItem.position)
                )
            items = list(result.scalars().all())

            # Read plate-clear setting once per queue check. Default MUST be
            # False to match the schema (SettingsSchema.require_plate_clear
            # defaults False) and the frontend (toggle + card badge both treat a
            # missing value as off). When no settings row exists, a True default
            # here re-enabled the plate-clear gate the UI showed as disabled,
            # blocking dispatch to FINISH-state printers forever with no UI path
            # to clear it (#1865).
            require_plate_clear = await self._get_bool_setting(db, "require_plate_clear", default=False)

            if not items:
                # No pending items — still check auto-drying on idle printers
                await self._check_auto_drying(db, [], set(), require_plate_clear=require_plate_clear)
                return

            logger.info(
                "Queue check: found %d pending items: %s",
                len(items),
                [(i.id, i.printer_id, i.archive_id, i.library_file_id) for i in items],
            )

            # Seed busy_printers with printers that already have an item in 'printing'
            # status. _is_printer_idle() alone is not sufficient as a dispatch gate —
            # on H2D / P1 series the MQTT state transition from IDLE to RUNNING can
            # lag several seconds behind the print command, so the next check_queue
            # tick still sees IDLE and would double-dispatch onto the same printer.
            # Without this guard, two pending items targeting the same printer
            # (e.g. a batch with quantity>1) both end up in 'printing' status —
            # surfaced via the "BUG: Multiple queue items" warning in on_print_complete.
            busy_result = await db.execute(
                select(PrintQueueItem.printer_id)
                .where(PrintQueueItem.status == "printing")
                .where(PrintQueueItem.printer_id.is_not(None))
            )
            busy_printers: set[int] = {pid for (pid,) in busy_result.all() if pid is not None}

            # Defense-in-depth (#1157): augment busy_printers with any printer
            # still in its post-dispatch hold window. Empirically, the DB seed
            # above can miss in-flight items in a multi-plate batch — same-file
            # plates were being dispatched 30 s apart while the H2D was still
            # digesting the first project_file. The hold is keyed in-memory and
            # released by the watchdog on the success path, so it adds a layer
            # that doesn't depend on DB row visibility or completion-callback
            # timing.
            for held_printer_id in list(self._dispatch_holds.keys()):
                if self._printer_in_dispatch_hold(held_printer_id):
                    busy_printers.add(held_printer_id)

            # Log skip reasons once per queue check (not per item)
            skip_reasons: dict[str, int] = {}

            for item in items:
                # Check scheduled time first (scheduled_time is stored in UTC from ISO string)
                if item.scheduled_time:
                    sched = item.scheduled_time
                    if sched.tzinfo is None:
                        sched = sched.replace(tzinfo=timezone.utc)
                    if sched > datetime.now(timezone.utc):
                        skip_reasons["scheduled_future"] = skip_reasons.get("scheduled_future", 0) + 1
                        continue

                # Skip items that require manual start
                if item.manual_start:
                    skip_reasons["manual_start"] = skip_reasons.get("manual_start", 0) + 1
                    continue

                if item.printer_id:
                    # Specific printer assignment (existing behavior)
                    if item.printer_id in busy_printers:
                        continue

                    # Check if printer is idle
                    printer_idle = self._is_printer_idle(item.printer_id, require_plate_clear)
                    printer_connected = printer_manager.is_connected(item.printer_id)

                    # If printer not connected, try to power on via smart plug
                    if not printer_connected:
                        plugs = await self._get_smart_plugs(db, item.printer_id)
                        auto_on_plugs = [p for p in plugs if p.auto_on and p.enabled]
                        if auto_on_plugs:
                            logger.info("Printer %s offline, attempting to power on via smart plug(s)", item.printer_id)
                            # Power on using the first auto_on plug (the printer power plug)
                            powered_on = await self._power_on_and_wait(auto_on_plugs[0], item.printer_id, db)
                            if powered_on:
                                # Also turn on any remaining auto_on plugs (e.g., filter)
                                for extra_plug in auto_on_plugs[1:]:
                                    try:
                                        service = await smart_plug_manager.get_service_for_plug(extra_plug, db)
                                        await service.turn_on(extra_plug)
                                        logger.info(
                                            "Also powered on plug '%s' for printer %s", extra_plug.name, item.printer_id
                                        )
                                    except Exception as e:
                                        logger.warning("Failed to power on extra plug '%s': %s", extra_plug.name, e)
                                printer_connected = True
                                printer_idle = self._is_printer_idle(item.printer_id, require_plate_clear)
                            else:
                                logger.warning("Could not power on printer %s via smart plug", item.printer_id)
                                busy_printers.add(item.printer_id)
                                continue
                        else:
                            # No plug or auto_on disabled
                            busy_printers.add(item.printer_id)
                            continue

                    # Check if printer is idle (busy with another print)
                    if not printer_idle:
                        # If printer is drying (not truly busy), handle based on queue_drying_block
                        if self._drying_in_progress.get(item.printer_id):
                            block_for_drying = await self._get_bool_setting(db, "queue_drying_block")
                            if block_for_drying:
                                # Drying blocks queue — skip this printer
                                busy_printers.add(item.printer_id)
                                continue
                            else:
                                # Print takes priority — stop drying
                                await self._stop_drying(item.printer_id)
                                # Re-check idle after stopping drying
                                printer_idle = self._is_printer_idle(item.printer_id, require_plate_clear)
                                if not printer_idle:
                                    busy_printers.add(item.printer_id)
                                    continue
                        else:
                            busy_printers.add(item.printer_id)
                            continue

                    # Check condition (previous print success)
                    if item.require_previous_success:
                        if not await self._check_previous_success(db, item):
                            item.status = "skipped"
                            item.error_message = "Previous print failed or was aborted"
                            item.completed_at = datetime.now(timezone.utc)
                            await db.commit()
                            logger.info("Skipped queue item %s - previous print failed", item.id)

                            # Send notification
                            job_name = await self._get_job_name(db, item)
                            printer = await self._get_printer(db, item.printer_id)
                            await notification_service.on_queue_job_skipped(
                                job_name=job_name,
                                printer_id=item.printer_id,
                                printer_name=printer.name if printer else "Unknown",
                                reason="Previous print failed or was aborted",
                                db=db,
                            )
                            continue

                    # Compute AMS mapping if not already set
                    if not item.ams_mapping:
                        computed_mapping = await self._compute_ams_mapping_for_printer(db, item.printer_id, item)
                        if computed_mapping:
                            item.ams_mapping = json.dumps(computed_mapping)
                            logger.info(
                                f"Queue item {item.id}: Computed AMS mapping for printer {item.printer_id}: {computed_mapping}"
                            )
                            await db.commit()

                    # Filament-deficit pre-dispatch check (#1496). If the
                    # assigned spool can't satisfy any required slot grams,
                    # promote the item to manual_start so the user must
                    # acknowledge via the ▶ button (which re-checks live).
                    if await self._block_on_filament_deficit(db, item):
                        continue

                    # Start the print
                    await self._start_print(db, item)
                    busy_printers.add(item.printer_id)

                    # SJF starvation guard: mark items that were jumped
                    if sjf_enabled and item.print_time_seconds is not None:
                        for other in items:
                            if (
                                other.id != item.id
                                and other.status == "pending"
                                and other.printer_id == item.printer_id
                                and not other.been_jumped
                                and other.position < item.position
                                and (
                                    other.print_time_seconds is None
                                    or other.print_time_seconds > item.print_time_seconds
                                )
                            ):
                                other.been_jumped = True
                        await db.commit()

                elif item.target_model:
                    # Model-based assignment - find any idle printer of matching model
                    # Parse required filament types if present
                    required_types = None
                    if item.required_filament_types:
                        try:
                            required_types = json.loads(item.required_filament_types)
                        except json.JSONDecodeError:
                            pass  # Ignore malformed filament types; treat as no constraint

                    # Parse filament overrides if present
                    filament_overrides = None
                    if item.filament_overrides:
                        try:
                            filament_overrides = json.loads(item.filament_overrides)
                        except json.JSONDecodeError:
                            pass

                    # If overrides exist, use override types for validation instead
                    effective_types = required_types
                    if filament_overrides:
                        override_types = sorted({o["type"] for o in filament_overrides if "type" in o})
                        if override_types:
                            # Merge: keep original types for non-overridden slots, add override types
                            effective_types = sorted(set(required_types or []) | set(override_types))

                    printer_id, waiting_reason = await self._find_idle_printer_for_model(
                        db,
                        item.target_model,
                        busy_printers,
                        effective_types,
                        item.target_location,
                        filament_overrides=filament_overrides,
                        require_plate_clear=require_plate_clear,
                    )

                    # Update waiting_reason if changed and send notification when first waiting
                    if item.waiting_reason != waiting_reason:
                        was_waiting = item.waiting_reason is not None
                        item.waiting_reason = waiting_reason
                        await db.commit()

                        # Send waiting notification only when transitioning to waiting state
                        # and the reason requires user action (not just "all printers busy")
                        if waiting_reason and not was_waiting and not self._is_busy_only(waiting_reason):
                            job_name = await self._get_job_name(db, item)
                            await notification_service.on_queue_job_waiting(
                                job_name=job_name,
                                target_model=item.target_model,
                                waiting_reason=waiting_reason,
                                db=db,
                            )

                    if printer_id:
                        # Check condition (previous print success) before assigning
                        if item.require_previous_success:
                            if not await self._check_previous_success(db, item):
                                item.status = "skipped"
                                item.error_message = "Previous print failed or was aborted"
                                item.completed_at = datetime.now(timezone.utc)
                                await db.commit()
                                logger.info("Skipped queue item %s - previous print failed", item.id)

                                # Send notification
                                job_name = await self._get_job_name(db, item)
                                printer = await self._get_printer(db, printer_id)
                                await notification_service.on_queue_job_skipped(
                                    job_name=job_name,
                                    printer_id=printer_id,
                                    printer_name=printer.name if printer else "Unknown",
                                    reason="Previous print failed or was aborted",
                                    db=db,
                                )
                                continue

                        # Assign printer and start - clear waiting reason
                        item.printer_id = printer_id
                        item.waiting_reason = None
                        logger.info("Model-based assignment: queue item %s assigned to printer %s", item.id, printer_id)

                        # Send assignment notification
                        job_name = await self._get_job_name(db, item)
                        printer = await self._get_printer(db, printer_id)
                        await notification_service.on_queue_job_assigned(
                            job_name=job_name,
                            printer_id=printer_id,
                            printer_name=printer.name if printer else "Unknown",
                            target_model=item.target_model,
                            db=db,
                        )

                        # Compute AMS mapping for the assigned printer if not already set
                        # This is critical for model-based jobs where mapping wasn't computed upfront
                        if not item.ams_mapping:
                            computed_mapping = await self._compute_ams_mapping_for_printer(db, printer_id, item)
                            if computed_mapping:
                                item.ams_mapping = json.dumps(computed_mapping)
                                logger.info(
                                    f"Queue item {item.id}: Computed AMS mapping for printer {printer_id}: {computed_mapping}"
                                )
                                await db.commit()

                        # Filament-deficit pre-dispatch check (#1496).
                        if await self._block_on_filament_deficit(db, item):
                            continue

                        await self._start_print(db, item)
                        busy_printers.add(printer_id)

                        # SJF starvation guard: mark model-based items that were jumped
                        if sjf_enabled and item.print_time_seconds is not None:
                            for other in items:
                                if (
                                    other.id != item.id
                                    and other.status == "pending"
                                    and other.printer_id is None
                                    and other.target_model
                                    and other.target_model.upper() == item.target_model.upper()
                                    and not other.been_jumped
                                    and other.position < item.position
                                    and (
                                        other.print_time_seconds is None
                                        or other.print_time_seconds > item.print_time_seconds
                                    )
                                ):
                                    other.been_jumped = True
                            await db.commit()

            # Log summary of skip reasons (helps diagnose why queue items aren't starting)
            if skip_reasons:
                logger.info("Queue skip summary: %s", skip_reasons)
            if busy_printers:
                # Log why each printer was busy (first time it was checked)
                for pid in busy_printers:
                    state = printer_manager.get_status(pid)
                    connected = printer_manager.is_connected(pid)
                    awaiting = printer_manager.is_awaiting_plate_clear(pid)
                    state_name = state.state if state else "NO_STATUS"
                    logger.info(
                        "Queue: printer %d not available — connected=%s, state=%s, awaiting_plate_clear=%s",
                        pid,
                        connected,
                        state_name,
                        awaiting,
                    )

            # Auto-drying: start drying on idle printers that have no pending queue items
            await self._check_auto_drying(db, items, busy_printers, require_plate_clear=require_plate_clear)

    async def _find_idle_printer_for_model(
        self,
        db: AsyncSession,
        model: str,
        exclude_ids: set[int],
        required_filament_types: list[str] | None = None,
        target_location: str | None = None,
        filament_overrides: list[dict] | None = None,
        require_plate_clear: bool = True,
    ) -> tuple[int | None, str | None]:
        """Find an idle, connected printer matching the model with compatible filaments.

        Args:
            db: Database session
            model: Printer model to match (e.g., "X1C", "P1S")
            exclude_ids: Printer IDs to exclude (already busy)
            required_filament_types: Optional list of filament types needed (e.g., ["PLA", "PETG"])
                                     If provided, only printers with all required types loaded will match.
            target_location: Optional location filter. If provided, only printers in this location are considered.
            filament_overrides: Optional list of override dicts. Each entry may include
                                 ``force_color_match: true`` to require an exact type+color match
                                 on the printer for that slot. Without the flag the existing
                                 colour-preference logic applies.

        Returns:
            Tuple of (printer_id, waiting_reason):
            - (printer_id, None) if a matching printer was found
            - (None, reason) if no printer is available, with explanation
        """
        # Normalize model name and use case-insensitive matching
        normalized_model = normalize_printer_model(model) or model
        query = (
            select(Printer)
            .where(func.lower(Printer.model) == normalized_model.lower())
            .where(Printer.is_active == True)  # noqa: E712
        )

        # Add location filter if specified
        if target_location:
            query = query.where(Printer.location == target_location)

        result = await db.execute(query)
        printers = list(result.scalars().all())

        location_suffix = f" in {target_location}" if target_location else ""
        if not printers:
            return None, f"No active {normalized_model} printers{location_suffix} configured"

        # Separate force-matched overrides from preference-only overrides
        force_overrides = [o for o in (filament_overrides or []) if o.get("force_color_match")]
        pref_overrides = [o for o in (filament_overrides or []) if not o.get("force_color_match")]

        # Track reasons for skipping printers
        printers_busy = []
        printers_offline = []
        printers_missing_filament: list[tuple[str, list[str]]] = []
        candidates: list[tuple[int, int]] = []  # (printer_id, color_match_count)

        for printer in printers:
            if printer.id in exclude_ids:
                # Printer is already claimed by another job in this scheduling run.
                # For force-color jobs, still check if the color would match — if not,
                # report it as a color mismatch rather than plain "Busy" so the user
                # knows the job needs a filament change, not just to wait for availability.
                if force_overrides and not pref_overrides:
                    missing_colors = self._get_missing_force_color_slots(printer.id, force_overrides)
                    if missing_colors:
                        printers_missing_filament.append((printer.name, missing_colors))
                        continue
                printers_busy.append(printer.name)
                continue

            is_connected = printer_manager.is_connected(printer.id)
            is_idle = self._is_printer_idle(printer.id, require_plate_clear) if is_connected else False

            if not is_connected:
                printers_offline.append(printer.name)
                continue

            if not is_idle:
                # Printer is currently printing.  For force-color jobs, check whether the
                # loaded color would satisfy the requirement — if not, surface it as a
                # color-mismatch reason rather than plain "Busy" so the user understands
                # that the job is waiting for a filament change, not just printer availability.
                if force_overrides and not pref_overrides:
                    missing_colors = self._get_missing_force_color_slots(printer.id, force_overrides)
                    if missing_colors:
                        printers_missing_filament.append((printer.name, missing_colors))
                        logger.debug(
                            "Printer %s (%s) is busy but also has wrong force-color: %s",
                            printer.id,
                            printer.name,
                            missing_colors,
                        )
                        continue
                printers_busy.append(printer.name)
                continue

            # Validate filament compatibility if required types are specified
            if required_filament_types:
                missing = self._get_missing_filament_types(printer.id, required_filament_types)
                if missing:
                    # When force_overrides are present, enrich missing entries with color info
                    # so the "Waiting on" message includes "TYPE (color)" instead of just "TYPE"
                    if force_overrides:
                        force_color_map = {
                            (o.get("type") or "").upper(): o.get("color_name") or o.get("color", "?")
                            for o in force_overrides
                        }
                        missing_enriched = [
                            f"{t} ({force_color_map[t_upper]})" if (t_upper := t.upper()) in force_color_map else t
                            for t in missing
                        ]
                        printers_missing_filament.append((printer.name, missing_enriched))
                    else:
                        printers_missing_filament.append((printer.name, missing))
                    logger.debug("Skipping printer %s (%s) - missing filaments: %s", printer.id, printer.name, missing)
                    continue

            # Force color match: ALL flagged slots must have an exact type+color match
            if force_overrides:
                missing_colors = self._get_missing_force_color_slots(printer.id, force_overrides)
                if missing_colors:
                    printers_missing_filament.append((printer.name, missing_colors))
                    logger.debug(
                        "Skipping printer %s (%s) - missing force-matched colors: %s",
                        printer.id,
                        printer.name,
                        missing_colors,
                    )
                    continue

            # If preference-only overrides exist, rank by color matches (existing behaviour)
            if pref_overrides:
                color_matches = self._count_override_color_matches(printer.id, pref_overrides)
                if color_matches > 0:
                    candidates.append((printer.id, color_matches))
                else:
                    override_colors = [f"{o.get('type', '?')} ({o.get('color', '?')})" for o in pref_overrides]
                    printers_missing_filament.append((printer.name, override_colors))
                    logger.debug("Skipping printer %s (%s) - no matching override colors", printer.id, printer.name)
                    continue
            elif force_overrides:
                # Passed all force checks — immediately eligible (no preference ordering needed)
                return printer.id, None
            else:
                # No overrides at all - take first available (existing behavior)
                return printer.id, None

        # If we have candidates from preference override matching, pick the one with most color matches
        if candidates:
            candidates.sort(key=lambda c: c[1], reverse=True)
            return candidates[0][0], None

        # Build waiting reason from what we found
        reasons = []
        if printers_missing_filament:
            # Filament/color mismatch is most actionable - show first
            if force_overrides and not pref_overrides:
                # All mismatches are force-color failures — use descriptive message only;
                # but only if there are no busy printers that DO have the matching color.
                # If a printer has the right color but is busy, surface "Busy" instead so
                # the user knows the job will start automatically once that printer is free.
                if not printers_busy:
                    all_missing = sorted({c for _, cols in printers_missing_filament for c in cols})
                    return None, f"No matching material/color. Waiting on {', '.join(all_missing)}"
                # else: fall through — printers_busy will be appended below
            else:
                names_and_missing = [
                    f"{name} (needs {', '.join(missing)})" for name, missing in printers_missing_filament
                ]
                reasons.append(f"Waiting for filament: {'; '.join(names_and_missing)}")
        if printers_busy:
            reasons.append(f"Busy: {', '.join(printers_busy)}")
        if printers_offline:
            reasons.append(f"Offline: {', '.join(printers_offline)}")

        return None, " | ".join(reasons) if reasons else f"No available {model} printers{location_suffix}"

    @staticmethod
    def _is_busy_only(waiting_reason: str) -> bool:
        """Check if the waiting reason only contains 'Busy' entries.

        When all matching printers are simply busy printing, the queued job
        will start automatically once a printer finishes — no user action
        is required, so we skip the notification.
        """
        parts = [p.strip() for p in waiting_reason.split(" | ")]
        return all(p.startswith("Busy:") for p in parts)

    def _get_missing_force_color_slots(self, printer_id: int, force_overrides: list[dict]) -> list[str]:
        """Return descriptive strings for force_color_match slots not satisfied by the printer.

        Each entry in ``force_overrides`` must have ``type`` and ``color`` fields and is expected
        to carry ``force_color_match: True``.  The printer must have **every** such slot loaded
        with an exact type+color match.

        Returns:
            List of ``"TYPE (color)"`` strings for unmatched slots (empty list means all match).
        """
        status = printer_manager.get_status(printer_id)
        if not status:
            return [f"{o.get('type', '?')} ({o.get('color_name') or o.get('color', '?')})" for o in force_overrides]

        # Build set of loaded type+colour pairs from AMS and external spool
        loaded: set[tuple[str, str]] = set()
        for ams_unit in status.raw_data.get("ams", []):
            for tray in ams_unit.get("tray", []):
                tray_type = tray.get("tray_type")
                tray_color = tray.get("tray_color", "")
                if tray_type:
                    color_norm = tray_color.replace("#", "").lower()[:6]
                    loaded.add((_canonical_filament_type(tray_type), color_norm))
        for vt in status.raw_data.get("vt_tray") or []:
            vt_type = vt.get("tray_type")
            if vt_type:
                color_norm = (vt.get("tray_color", "") or "").replace("#", "").lower()[:6]
                loaded.add((_canonical_filament_type(vt_type), color_norm))

        missing = []
        for o in force_overrides:
            o_type = _canonical_filament_type(o.get("type") or "")
            o_color = (o.get("color") or "").replace("#", "").lower()[:6]
            if (o_type, o_color) not in loaded:
                color_label = o.get("color_name") or o.get("color", "?")
                missing.append(f"{o_type} ({color_label})")
        return missing

    def _get_missing_filament_types(self, printer_id: int, required_types: list[str]) -> list[str]:
        """Get the list of required filament types that are not loaded on the printer.

        Args:
            printer_id: The printer ID
            required_types: List of filament types needed (e.g., ["PLA", "PETG"])

        Returns:
            List of missing filament types (empty if all are loaded)
        """
        status = printer_manager.get_status(printer_id)
        if not status:
            return required_types  # Can't determine, assume all missing

        # Collect all filament types loaded on this printer (AMS units + external spool)
        # Use canonical types so equivalence groups (e.g. PA-CF/PA12-CF/PAHT-CF) match.
        loaded_types: set[str] = set()

        # Check AMS units (stored in raw_data["ams"])
        ams_data = status.raw_data.get("ams", [])
        if ams_data:
            for ams_unit in ams_data:
                for tray in ams_unit.get("tray", []):
                    tray_type = tray.get("tray_type")
                    if tray_type:
                        loaded_types.add(_canonical_filament_type(tray_type))

        # Check external spool(s) (virtual tray, stored in raw_data["vt_tray"] as list)
        for vt in status.raw_data.get("vt_tray") or []:
            vt_type = vt.get("tray_type")
            if vt_type:
                loaded_types.add(_canonical_filament_type(vt_type))

        # Find which required types are missing (using canonical type for equivalence)
        missing = []
        for req_type in required_types:
            if _canonical_filament_type(req_type) not in loaded_types:
                missing.append(req_type)

        return missing

    def _count_override_color_matches(self, printer_id: int, overrides: list[dict]) -> int:
        """Count how many filament overrides have an exact color match on the printer.

        Used to prefer printers that already have the desired override colors loaded.
        """
        status = printer_manager.get_status(printer_id)
        if not status:
            return 0

        # Collect loaded filaments' type+color pairs
        loaded: set[tuple[str, str]] = set()
        for ams_unit in status.raw_data.get("ams", []):
            for tray in ams_unit.get("tray", []):
                tray_type = tray.get("tray_type")
                tray_color = tray.get("tray_color", "")
                if tray_type:
                    color_norm = tray_color.replace("#", "").lower()[:6]
                    loaded.add((tray_type.upper(), color_norm))
        for vt in status.raw_data.get("vt_tray") or []:
            vt_type = vt.get("tray_type")
            if vt_type:
                color_norm = (vt.get("tray_color", "") or "").replace("#", "").lower()[:6]
                loaded.add((vt_type.upper(), color_norm))

        matches = 0
        for o in overrides:
            o_type = (o.get("type") or "").upper()
            o_color = (o.get("color") or "").replace("#", "").lower()[:6]
            if (o_type, o_color) in loaded:
                matches += 1
        return matches

    async def _compute_ams_mapping_for_printer(
        self, db: AsyncSession, printer_id: int, item: PrintQueueItem
    ) -> list[int] | None:
        """Compute AMS mapping for a printer based on filament requirements.

        Called when a queue item has no ams_mapping set — either for model-based
        items after printer assignment, or printer-specific items (e.g. from VP).

        Args:
            db: Database session
            printer_id: The assigned printer ID
            item: The queue item (contains archive_id or library_file_id)

        Returns:
            AMS mapping array or None if no mapping needed/possible
        """
        # Get printer status
        status = printer_manager.get_status(printer_id)
        if not status:
            logger.warning("Cannot compute AMS mapping: printer %s status unavailable", printer_id)
            return None

        # Filament Track Switch (FTS): when installed it routes any AMS slot to
        # either extruder, so the per-nozzle hard filter below must NOT apply.
        # Otherwise a print on one nozzle can't use a spool physically loaded in
        # an AMS on the *other* nozzle, and the matcher falls through to a
        # same-type wrong-colour spool on the target nozzle — the H2C + FTS
        # wrong-filament bug (#2186). Mirrors the frontend skip added for #1162.
        fts_installed = bool(getattr(getattr(status, "fila_switch", None), "installed", False))

        # Get filament requirements from source file
        filament_reqs = await self._get_filament_requirements(db, item)
        if not filament_reqs:
            # When the 3MF can't be read but force-color overrides are present, build a
            # direct mapping from the overrides so the printer uses the correct AMS slot.
            if item.filament_overrides:
                try:
                    overrides = json.loads(item.filament_overrides)
                    force_overrides = [o for o in overrides if o.get("force_color_match")]
                    if force_overrides:
                        logger.info(
                            "Queue item %s: No filament reqs from 3MF; building AMS mapping from %d "
                            "force-color override(s)",
                            item.id,
                            len(force_overrides),
                        )
                        return self._build_override_direct_mapping(force_overrides, status)
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    logger.warning("Queue item %s: Force-color fallback mapping failed: %s", item.id, e)
            logger.debug("No filament requirements found for queue item %s", item.id)
            return None

        # Apply filament overrides if present
        if item.filament_overrides:
            try:
                overrides = json.loads(item.filament_overrides)
                override_map = {o["slot_id"]: o for o in overrides}
                for req in filament_reqs:
                    if req["slot_id"] in override_map:
                        override = override_map[req["slot_id"]]
                        req["type"] = override["type"]
                        req["color"] = override["color"]
                        # Clear tray_info_idx so matching uses type+color instead of
                        # the original 3MF's tray_info_idx (which would match the old filament)
                        req["tray_info_idx"] = ""
                        logger.debug(
                            "Queue item %s: Override slot %d -> %s %s",
                            item.id,
                            req["slot_id"],
                            override["type"],
                            override["color"],
                        )
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("Failed to apply filament overrides for queue item %s: %s", item.id, e)

        # Build loaded filaments from printer status
        loaded_filaments = self._build_loaded_filaments(status)
        if not loaded_filaments:
            logger.debug("No filaments loaded on printer %s", printer_id)
            return None

        # Check if user prefers lowest remaining filament when multiple spools match
        prefer_lowest = await self._get_bool_setting(db, "prefer_lowest_filament")

        # Gate prefer_lowest on the printer's AMS Filament Backup state (#1766).
        # Without backup, the printer will not switch to a second spool when the
        # picked one runs out — so sorting toward the lowest leaves the print
        # at risk of running dry mid-job. None (unknown / A1 family) preserves
        # today's behaviour intentionally.
        if prefer_lowest and status.ams_filament_backup is False:
            logger.info("[prefer-lowest] skipped (AMS Backup OFF on printer %s)", printer_id)
            prefer_lowest = False

        # When the preference is on, surface Bambuddy's inventory-side
        # remaining for each slot that's bound to a tracked spool, so the
        # sort beats the MQTT-only blind spot (#1508). Skip the lookup
        # entirely when the preference is off — no behaviour change for
        # users who haven't opted in.
        inventory_remain_overrides: dict[int, float] | None = None
        if prefer_lowest:
            inventory_remain_overrides = await self._build_inventory_remain_overrides(db, printer_id, loaded_filaments)

        # Compute mapping: match required filaments to available slots
        return self._match_filaments_to_slots(
            filament_reqs, loaded_filaments, prefer_lowest, inventory_remain_overrides, fts_installed
        )

    def _build_override_direct_mapping(self, force_overrides: list[dict], status) -> list[int] | None:
        """Build an AMS mapping directly from force-color overrides without a 3MF.

        Used when ``_get_filament_requirements`` returns nothing (e.g. the 3MF's
        slice_info is missing or unreadable) but ``force_color_match`` overrides
        are present. Each override's ``slot_id``, ``type``, and ``color`` are
        treated as the filament requirement for that slot and matched against the
        current AMS state of the printer.

        Returns the same format as ``_match_filaments_to_slots``, or None when
        the AMS has no loaded filaments.
        """
        loaded = self._build_loaded_filaments(status)
        if not loaded:
            return None

        reqs = [
            {
                "slot_id": o["slot_id"],
                "type": o.get("type", ""),
                "color": o.get("color", ""),
                "tray_info_idx": "",
            }
            for o in force_overrides
        ]
        return self._match_filaments_to_slots(reqs, loaded)

    async def _get_filament_requirements(self, db: AsyncSession, item: PrintQueueItem) -> list[dict] | None:
        """Resolve the queue item's source 3MF and parse the per-slot
        filament requirements out of it. Thin DB-resolver wrapper around
        ``filament_requirements.extract_filament_requirements`` so the VP
        queue-mode write path (#1188) can reuse the same parser at upload
        time.
        """
        from backend.app.services.filament_requirements import extract_filament_requirements

        file_path: Path | None = None
        if item.archive_id:
            result = await db.execute(select(PrintArchive).where(PrintArchive.id == item.archive_id))
            archive = result.scalar_one_or_none()
            if archive:
                file_path = settings.base_dir / archive.file_path
        elif item.library_file_id:
            result = await db.execute(LibraryFile.active().where(LibraryFile.id == item.library_file_id))
            library_file = result.scalar_one_or_none()
            if library_file:
                lib_path = Path(library_file.file_path)
                file_path = lib_path if lib_path.is_absolute() else settings.base_dir / library_file.file_path

        if not file_path or not file_path.exists():
            return None

        filaments = extract_filament_requirements(file_path, plate_id=item.plate_id)
        return filaments if filaments else None

    def _build_loaded_filaments(self, status) -> list[dict]:
        """Build list of loaded filaments from printer status.

        Args:
            status: PrinterState from printer_manager

        Returns:
            List of loaded filament dicts with type, color, ams_id, tray_id, global_tray_id
        """
        filaments = []

        # Get ams_extruder_map for dual-nozzle printers (H2D, H2D Pro)
        ams_extruder_map = status.raw_data.get("ams_extruder_map", {})

        # Parse AMS units from raw_data
        ams_data = status.raw_data.get("ams", [])
        for ams_unit in ams_data:
            ams_id = int(ams_unit.get("id", 0))
            trays = ams_unit.get("tray", [])
            is_ht = len(trays) == 1  # AMS-HT has single tray

            for tray in trays:
                tray_type = tray.get("tray_type")
                if tray_type:
                    tray_id = int(tray.get("id", 0))
                    tray_color = tray.get("tray_color", "")
                    # tray_info_idx identifies the specific spool (e.g., "GFA00", "P4d64437")
                    tray_info_idx = tray.get("tray_info_idx", "")
                    # Normalize color: remove alpha, add hash
                    color = self._normalize_color(tray_color)
                    # Calculate global tray ID
                    # AMS-HT units have IDs starting at 128 with a single tray
                    global_tray_id = ams_id if ams_id >= 128 else ams_id * 4 + tray_id

                    filaments.append(
                        {
                            "type": tray_type,
                            "color": color,
                            "tray_info_idx": tray_info_idx,
                            "ams_id": ams_id,
                            "tray_id": tray_id,
                            "is_ht": is_ht,
                            "is_external": False,
                            "global_tray_id": global_tray_id,
                            "extruder_id": ams_extruder_map.get(str(ams_id)),
                            "remain": tray.get("remain", -1),
                        }
                    )

        # Check external spool(s) (vt_tray is a list)
        for idx, vt in enumerate(status.raw_data.get("vt_tray") or []):
            if vt.get("tray_type"):
                color = self._normalize_color(vt.get("tray_color", ""))
                tray_id = int(vt.get("id", 254))
                filaments.append(
                    {
                        "type": vt["tray_type"],
                        "color": color,
                        "tray_info_idx": vt.get("tray_info_idx", ""),
                        "ams_id": -1,
                        "tray_id": idx,
                        "is_ht": False,
                        "is_external": True,
                        "global_tray_id": tray_id,
                        "extruder_id": (255 - tray_id) if ams_extruder_map else None,
                        "remain": vt.get("remain", -1),
                    }
                )

        return filaments

    def _normalize_color(self, color: str | None) -> str:
        """Normalize color to #RRGGBB format."""
        if not color:
            return "#808080"
        hex_color = color.replace("#", "")[:6]
        return f"#{hex_color}"

    def _normalize_color_for_compare(self, color: str | None) -> str:
        """Normalize color for comparison (lowercase, no hash)."""
        if not color:
            return ""
        return color.replace("#", "").lower()[:6]

    def _colors_are_similar(self, color1: str | None, color2: str | None, threshold: int = 40) -> bool:
        """Check if two colors are visually similar within a threshold."""
        hex1 = self._normalize_color_for_compare(color1)
        hex2 = self._normalize_color_for_compare(color2)
        if not hex1 or not hex2 or len(hex1) < 6 or len(hex2) < 6:
            return False

        try:
            r1 = int(hex1[0:2], 16)
            g1 = int(hex1[2:4], 16)
            b1 = int(hex1[4:6], 16)
            r2 = int(hex2[0:2], 16)
            g2 = int(hex2[2:4], 16)
            b2 = int(hex2[4:6], 16)
            return abs(r1 - r2) <= threshold and abs(g1 - g2) <= threshold and abs(b1 - b2) <= threshold
        except ValueError:
            return False

    async def _build_inventory_remain_overrides(
        self, db: AsyncSession, printer_id: int, loaded: list[dict]
    ) -> dict[int, float]:
        """Return ``{global_tray_id: remaining_grams}`` for AMS slots the user
        has bound to an inventory spool — Bambuddy-side or Spoolman-side.

        The MQTT ``remain`` field on a tray is the printer firmware's
        RFID-decremented value, which has two limitations the "Prefer Lowest
        Remaining Filament" feature has been ignoring (#1508):

        - it's only meaningful for Bambu RFID spools; everything else reports
          ``-1`` (then clamped to a sentinel), so multiple non-RFID trays
          compare equal and the sort collapses to AMS-slot order — the user
          who's curating inventory weights gets the lower-slot pick instead
          of the lower-remaining pick;
        - even when set, it's the *printer's* counter, not Bambuddy's
          ``label_weight - weight_used`` (internal mode) or Spoolman's
          ``remaining_weight`` (Spoolman mode) — the two diverge any time the
          user re-spools, swaps cardboard, or runs a print outside Bambuddy.

        When the user has bound a spool to a slot, their own inventory
        tracking is authoritative; this helper surfaces that value so the
        sort can prefer it. Slots without a binding are absent from the
        returned map — the caller then falls back to MQTT ``remain`` for
        those, preserving the pre-#1508 behaviour for un-tracked spools.

        Returns an empty map on any failure (no inventory bindings, DB
        error, Spoolman unreachable). A best-effort lookup; "Prefer Lowest"
        is a preference, not a guarantee.
        """
        if not loaded:
            return {}
        # External / virtual-tray slots are tracked separately from AMS — skip
        # them so a VT-loaded spool doesn't accidentally inherit a tracked
        # AMS binding (the tables use ams_id 254/255 for VT, but the cross
        # match is fiddly and out of scope for this fix).
        tracked_slots = [(f["ams_id"], f["tray_id"], f["global_tray_id"]) for f in loaded if not f.get("is_external")]
        if not tracked_slots:
            return {}

        is_spoolman = await self._is_spoolman_mode(db)
        overrides: dict[int, float] = {}

        if is_spoolman:
            result = await db.execute(
                select(SpoolmanSlotAssignment).where(SpoolmanSlotAssignment.printer_id == printer_id)
            )
            assignments = list(result.scalars().all())
            by_slot = {(a.ams_id, a.tray_id): a.spoolman_spool_id for a in assignments}
            from backend.app.services.filament_deficit import _spoolman_remaining_grams

            for ams_id, tray_id, gtid in tracked_slots:
                spoolman_id = by_slot.get((ams_id, tray_id))
                if spoolman_id is None:
                    continue
                grams = await _spoolman_remaining_grams(spoolman_id)
                if grams is not None:
                    overrides[gtid] = grams
            return overrides

        # Internal inventory mode (default). selectinload matches the pattern
        # used elsewhere (inventory.py, spoolman.py routes) — a single query
        # plus an eager-loaded relationship rather than an explicit join, so
        # the row-attribute shape is exactly what those routes already rely on.
        result = await db.execute(
            select(SpoolAssignment)
            .options(selectinload(SpoolAssignment.spool))
            .where(SpoolAssignment.printer_id == printer_id)
        )
        assignments = list(result.scalars().all())
        by_slot = {(a.ams_id, a.tray_id): a.spool for a in assignments}
        for ams_id, tray_id, gtid in tracked_slots:
            spool = by_slot.get((ams_id, tray_id))
            if spool is None:
                continue
            label = float(spool.label_weight or 0)
            used = float(spool.weight_used or 0)
            overrides[gtid] = max(0.0, label - used)
        return overrides

    @staticmethod
    async def _is_spoolman_mode(db: AsyncSession) -> bool:
        """Mirror of ``filament_deficit._is_spoolman_mode`` — kept private
        here to avoid making this module import-dependent on that private
        helper's signature."""
        try:
            from backend.app.api.routes.settings import get_setting

            v = await get_setting(db, "spoolman_enabled")
            return bool(v) and v.lower() == "true"
        except Exception:
            return False

    @staticmethod
    def _slot_priority(ams_id: int | None, tray_id: int | None) -> int:
        """Deterministic slot-position tie-breaker for the prefer-lowest sort.

        Three bands, matched to the emission order in ``_build_loaded_filaments``
        so a tied sort produces the same physical-position order the pre-#1508
        stable sort did (preserves the regression-free baseline):

        - Regular AMS (``ams_id`` 0..7): ``ams_id * 4 + tray_id`` → 0..31
        - AMS-HT (``ams_id`` >= 128, single tray): ``1000 + (ams_id - 128) * 4``
        - External / VT (``ams_id`` < 0, or ``None``): ``10_000``

        Banding ensures regular AMS < AMS-HT < external on ties, regardless of
        what the raw ``ams_id`` happens to be (in particular, ``ams_id = -1``
        for VT must NOT sort to a negative number or it would beat AMS slot 0).
        """
        if ams_id is None or ams_id < 0:
            return 10_000
        if ams_id >= 128:
            return 1_000 + (ams_id - 128) * 4 + (tray_id or 0)
        return ams_id * 4 + (tray_id or 0)

    @staticmethod
    def _prefer_lowest_sort_key(f: dict, overrides: dict[int, float] | None) -> tuple[int, float, int]:
        """Sort key for the "Prefer Lowest Remaining Filament" preference.

        Two-tier ordering: inventory-tracked spools always sort BEFORE
        non-tracked spools (the user has told us they care about these
        specifically), then ascending by remaining within each tier, then
        ascending by AMS slot position as the deterministic tie-breaker.

        Tiers are flagged by the first tuple element (0 = inventory-tracked,
        1 = MQTT-only / unknown). Cross-tier value comparisons never run
        because the tier flag dominates — which is what lets us mix grams
        (inventory) and percent (MQTT) without a unit conversion.

        Within the MQTT tier ``remain = -1`` (unknown) is mapped to 101 so
        spools the printer DOES know something about sort ahead of those
        it knows nothing about — preserves pre-#1508 behaviour for the
        no-inventory-binding case.

        Slot tie-breaker via ``_slot_priority`` so regular AMS < AMS-HT <
        external on ties, matching the legacy emission-order stable sort.
        """
        gtid = f.get("global_tray_id")
        slot_order = PrintScheduler._slot_priority(f.get("ams_id"), f.get("tray_id"))
        if overrides and gtid in overrides:
            return (0, overrides[gtid], slot_order)
        remain = f.get("remain", -1)
        return (1, float(remain) if remain is not None and remain >= 0 else 101.0, slot_order)

    def _match_filaments_to_slots(
        self,
        required: list[dict],
        loaded: list[dict],
        prefer_lowest: bool = False,
        inventory_remain_overrides: dict[int, float] | None = None,
        fts_installed: bool = False,
    ) -> list[int] | None:
        """Match required filaments to loaded filaments and build AMS mapping.

        Priority: unique tray_info_idx match > exact color match > similar color match > type-only match

        The tray_info_idx is a filament type identifier stored in the 3MF file when the user
        slices (e.g., "GFA00" for generic PLA, "P4d64437" for custom presets). If the same
        tray_info_idx appears in only ONE available tray, we use that tray. If multiple trays
        have the same tray_info_idx (e.g., two spools of generic PLA), we fall back to color
        matching among those trays.

        Args:
            required: List of required filaments with slot_id, type, color, tray_info_idx
            loaded: List of loaded filaments with type, color, tray_info_idx, global_tray_id

        Returns:
            AMS mapping array (position = slot_id - 1, value = global_tray_id or -1)
        """
        if not required:
            return None

        # Track used trays to avoid duplicate assignment
        used_tray_ids: set[int] = set()
        comparisons = []

        for req in required:
            req_type = (req.get("type") or "").upper()
            req_color = req.get("color", "")
            req_tray_info_idx = req.get("tray_info_idx", "")

            # Find best match: unique tray_info_idx > exact color > similar color > type-only
            idx_match = None
            exact_match = None
            similar_match = None
            type_only_match = None

            # Get available trays (not already used)
            available = [f for f in loaded if f["global_tray_id"] not in used_tray_ids]

            # Nozzle-aware filtering: restrict to trays on the correct nozzle.
            # Hard filter — cross-nozzle assignment causes print failures
            # ("position of left hotend is abnormal"), so never fall back.
            # Skipped when an FTS is installed: it routes any AMS slot to either
            # extruder, so restricting to one nozzle would wrongly exclude the
            # correct spool sitting in the other nozzle's AMS (#2186).
            req_nozzle_id = req.get("nozzle_id")
            if req_nozzle_id is not None and not fts_installed:
                available = [f for f in available if f.get("extruder_id") == req_nozzle_id]

            # Sort by remaining filament (ascending) so lowest-remain spool wins .find().
            # Inventory-tracked spools sort before MQTT-only ones (#1508); see
            # _prefer_lowest_sort_key for the full rationale.
            if prefer_lowest:
                available.sort(key=lambda f: self._prefer_lowest_sort_key(f, inventory_remain_overrides))
                # INFO-level decision trace for "Prefer Lowest Filament" #1766.
                # One line per filament req so a bug report can be diagnosed
                # without enabling debug logging: shows what the matcher saw
                # (req shape + sorted candidate trays with their remain values
                # and any inventory override that was applied). Mirrored by
                # the picked-match log at the bottom of the loop.
                logger.info(
                    "[prefer-lowest] req slot=%s type=%r color=%r tii=%r nozzle=%s; available (sorted lowest-first): %s",
                    req.get("slot_id"),
                    req_type,
                    req_color,
                    req_tray_info_idx,
                    req_nozzle_id,
                    [
                        {
                            "gtid": f.get("global_tray_id"),
                            "type": f.get("type"),
                            "color": f.get("color"),
                            "tii": f.get("tray_info_idx"),
                            "remain": f.get("remain"),
                            "inv_g": (
                                inventory_remain_overrides.get(f.get("global_tray_id"))
                                if inventory_remain_overrides
                                else None
                            ),
                        }
                        for f in available
                    ],
                )

            # Check if tray_info_idx is unique among available trays
            if req_tray_info_idx:
                idx_matches = [f for f in available if f.get("tray_info_idx") == req_tray_info_idx]
                if len(idx_matches) == 1:
                    # Unique tray_info_idx - use it as definitive match
                    idx_match = idx_matches[0]
                    logger.debug(
                        f"Matched filament slot {req.get('slot_id')} by unique tray_info_idx={req_tray_info_idx} "
                        f"-> tray {idx_match['global_tray_id']}"
                    )
                elif len(idx_matches) > 1:
                    # Multiple trays with same tray_info_idx - use color matching among them
                    logger.debug(
                        f"Non-unique tray_info_idx={req_tray_info_idx} found in {len(idx_matches)} trays, "
                        f"using color matching among trays: {[f['global_tray_id'] for f in idx_matches]}"
                    )
                    if prefer_lowest:
                        idx_matches.sort(key=lambda f: self._prefer_lowest_sort_key(f, inventory_remain_overrides))
                    # Use color matching within this subset
                    for f in idx_matches:
                        f_color = f.get("color", "")
                        if self._normalize_color_for_compare(f_color) == self._normalize_color_for_compare(req_color):
                            if not exact_match:
                                exact_match = f
                        elif self._colors_are_similar(f_color, req_color):
                            if not similar_match:
                                similar_match = f
                        elif not type_only_match:
                            type_only_match = f

            # If no idx_match yet, do standard type/color matching on all available trays
            if not idx_match and not exact_match and not similar_match and not type_only_match:
                for f in available:
                    f_type = (f.get("type") or "").upper()
                    if _canonical_filament_type(f_type) != _canonical_filament_type(req_type):
                        continue

                    # Type matches - check color
                    f_color = f.get("color", "")
                    if self._normalize_color_for_compare(f_color) == self._normalize_color_for_compare(req_color):
                        if not exact_match:
                            exact_match = f
                    elif self._colors_are_similar(f_color, req_color):
                        if not similar_match:
                            similar_match = f
                    elif not type_only_match:
                        type_only_match = f

            match = idx_match or exact_match or similar_match or type_only_match
            if match:
                used_tray_ids.add(match["global_tray_id"])
                comparisons.append({"slot_id": req.get("slot_id", 0), "global_tray_id": match["global_tray_id"]})
            else:
                comparisons.append({"slot_id": req.get("slot_id", 0), "global_tray_id": -1})
            if prefer_lowest:
                # Pair with the "available (sorted)" log above so the reporter
                # bundle shows BOTH what the matcher saw AND which match bucket
                # won — fast triage when "Prefer Lowest Filament" picks the
                # wrong slot (#1766).
                if match:
                    bucket = (
                        "idx"
                        if idx_match is not None
                        else "exact_color"
                        if exact_match is not None
                        else "similar_color"
                        if similar_match is not None
                        else "type_only"
                    )
                    logger.info(
                        "[prefer-lowest] picked gtid=%s via %s for req slot=%s",
                        match["global_tray_id"],
                        bucket,
                        req.get("slot_id"),
                    )
                else:
                    logger.info(
                        "[prefer-lowest] NO MATCH for req slot=%s (type=%r color=%r tii=%r)",
                        req.get("slot_id"),
                        req_type,
                        req_color,
                        req_tray_info_idx,
                    )

        # Build mapping array
        if not comparisons:
            return None

        max_slot_id = max(c["slot_id"] for c in comparisons)
        if max_slot_id <= 0:
            return None

        mapping = [-1] * max_slot_id
        for c in comparisons:
            slot_id = c["slot_id"]
            if slot_id and slot_id > 0:
                mapping[slot_id - 1] = c["global_tray_id"]

        return mapping

    def _mark_printer_dispatched(
        self,
        printer_id: int,
        pre_state: str | None,
        pre_subtask_id: str | None,
    ) -> None:
        """Record that a print command was just sent to ``printer_id``.

        Held until either the watchdog observes a state/subtask transition
        (success path) or the hard timeout expires. See ``_dispatch_holds``.
        """
        if not pre_state:
            # No pre_state means we can't detect a transition — fall back to a
            # pure time-based hold using empty string as a sentinel that won't
            # match any real printer state.
            pre_state = ""
        self._dispatch_holds[printer_id] = (time.monotonic(), pre_state, pre_subtask_id)

    def _release_dispatch_hold(self, printer_id: int) -> None:
        """Drop the dispatch hold for ``printer_id`` (called by the watchdog)."""
        self._dispatch_holds.pop(printer_id, None)

    def _printer_in_dispatch_hold(self, printer_id: int) -> bool:
        """True if ``printer_id`` is still inside its post-dispatch hold window.

        Returns False (and clears the hold) once any of these are true:
          - hard timeout (``_dispatch_max_hold``) has elapsed
          - the printer has transitioned out of pre_state and we're past the
            minimum cooldown
          - the printer's subtask_id has advanced past pre_subtask_id and we're
            past the minimum cooldown
        Otherwise the printer is held — caller should treat it as busy.
        """
        entry = self._dispatch_holds.get(printer_id)
        if not entry:
            return False
        started_at, pre_state, pre_subtask_id = entry
        elapsed = time.monotonic() - started_at

        if elapsed >= self._dispatch_max_hold:
            self._dispatch_holds.pop(printer_id, None)
            return False

        # Without a pre_state we can't detect a transition — fall back to the
        # min cooldown alone, then drop the hold.
        if not pre_state:
            if elapsed >= self._dispatch_min_cooldown:
                self._dispatch_holds.pop(printer_id, None)
                return False
            return True

        status = printer_manager.get_status(printer_id)
        current_state = getattr(status, "state", None) if status else None
        current_subtask_id = getattr(status, "subtask_id", None) if status else None
        transitioned = (current_state is not None and current_state != pre_state) or (
            pre_subtask_id is not None and current_subtask_id is not None and current_subtask_id != pre_subtask_id
        )

        if transitioned and elapsed >= self._dispatch_min_cooldown:
            self._dispatch_holds.pop(printer_id, None)
            return False

        return True

    def _is_printer_idle(self, printer_id: int, require_plate_clear: bool = True) -> bool:
        """Check if a printer is connected and idle."""
        if not printer_manager.is_connected(printer_id):
            logger.debug("Printer %d: not connected", printer_id)
            return False

        state = printer_manager.get_status(printer_id)
        if not state:
            logger.debug("Printer %d: no status available", printer_id)
            return False

        # Plate-clear gate: if the printer finished/failed a previous print and the user
        # hasn't acknowledged the plate was cleared, the queue must not dispatch the next
        # job — even if the printer currently reports IDLE. After Auto Off cycles the
        # printer, it boots back into IDLE with no memory of the previous finish; without
        # the persisted awaiting flag we'd bypass the confirmation prompt (#961).
        if require_plate_clear and printer_manager.is_awaiting_plate_clear(printer_id):
            logger.debug(
                "Printer %d: not idle — awaiting plate-clear acknowledgment (state=%s)",
                printer_id,
                state.state,
            )
            return False

        idle = state.state in ("IDLE", "FINISH", "FAILED")
        if not idle:
            logger.debug("Printer %d: not idle — state=%s", printer_id, state.state)
        return idle

    async def _get_setting(self, db: AsyncSession, key: str) -> str | None:
        """Read a setting value from the database."""
        result = await db.execute(select(Settings).where(Settings.key == key))
        setting = result.scalar_one_or_none()
        return setting.value if setting else None

    async def _get_bool_setting(self, db: AsyncSession, key: str, default: bool = False) -> bool:
        """Read a boolean setting from the database."""
        result = await db.execute(select(Settings).where(Settings.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            return setting.value.lower() == "true"
        return default

    async def _get_int_setting(self, db: AsyncSession, key: str, default: int) -> int:
        """Read an int setting; falls back to default on missing/unparseable rows."""
        result = await db.execute(select(Settings).where(Settings.key == key))
        setting = result.scalar_one_or_none()
        if setting and setting.value:
            try:
                return int(setting.value)
            except ValueError:
                pass
        return default

    async def _get_drying_presets(self, db: AsyncSession) -> dict[str, dict[str, int]]:
        """Get drying presets (user-configured or built-in defaults)."""
        result = await db.execute(select(Settings).where(Settings.key == "drying_presets"))
        setting = result.scalar_one_or_none()
        if setting and setting.value:
            try:
                presets = json.loads(setting.value)
                if isinstance(presets, dict) and presets:
                    return presets
            except json.JSONDecodeError:
                pass
        return self.DEFAULT_DRYING_PRESETS

    async def _get_humidity_thresholds(self, db: AsyncSession) -> dict[str, int]:
        """Per-filament humidity thresholds (#1605).

        Returns the user-configured overrides map keyed by normalized filament
        type (uppercase base, e.g. ``PLA``, ``ASA``) plus a ``default`` key for
        unknown / unmapped types. Empty / unset → empty dict, in which case
        callers fall back to ``ams_humidity_fair``.
        """
        result = await db.execute(select(Settings).where(Settings.key == "ams_humidity_thresholds"))
        setting = result.scalar_one_or_none()
        if not setting or not setting.value:
            return {}
        try:
            data = json.loads(setting.value)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, int] = {}
        for key, value in data.items():
            try:
                out[str(key).upper() if key != "default" else "default"] = int(value)
            except (TypeError, ValueError):
                continue
        return out

    @staticmethod
    def resolve_humidity_threshold(trays: list[dict], thresholds: dict[str, int], fallback: int) -> int:
        """Resolve the effective humidity threshold for an AMS unit (#1605).

        For mixed filament types loaded into one AMS, returns the most
        restrictive (lowest) threshold across all loaded tray types — matches
        the conservative-params strategy already used for drying temp/hours.
        Empty / unloaded trays contribute no constraint. Unknown types use the
        ``default`` key, falling through to ``fallback`` (= ``ams_humidity_fair``)
        when no per-type map is configured at all.
        """
        default = thresholds.get("default", fallback)
        if not thresholds:
            return fallback
        candidates: list[int] = []
        for tray in trays:
            tray_type = str(tray.get("tray_type") or "").strip()
            if not tray_type:
                continue
            base_type = tray_type.split()[0].upper()
            candidates.append(thresholds.get(base_type, default))
        if not candidates:
            return default
        return min(candidates)

    def _get_conservative_drying_params(
        self, trays: list[dict], module_type: str, presets: dict[str, dict[str, int]]
    ) -> tuple[int, int, str] | None:
        """Get the most conservative drying params for mixed filament types in an AMS unit.

        Returns (temp, duration_hours, filament_type) or None if no drying-eligible filaments.
        """
        temp_key = module_type if module_type in ("n3f", "n3s") else "n3f"
        hours_key = f"{temp_key}_hours"

        min_temp = None
        max_hours = None
        filament_type = ""

        for tray in trays:
            tray_type = tray.get("tray_type", "")
            if not tray_type:
                continue
            # Normalize filament type for preset lookup (e.g., "PLA Basic" -> "PLA")
            base_type = tray_type.split()[0].upper()
            preset = presets.get(base_type)
            if not preset:
                continue

            temp = preset.get(temp_key, 55)
            hours = preset.get(hours_key, 12)

            # Conservative: lowest temp, longest duration
            if min_temp is None or temp < min_temp:
                min_temp = temp
            if max_hours is None or hours > max_hours:
                max_hours = hours
            if not filament_type:
                filament_type = base_type

        if min_temp is None:
            return None
        return (min_temp, max_hours or 12, filament_type)

    async def _check_auto_drying(
        self,
        db: AsyncSession,
        queue_items: list[PrintQueueItem],
        busy_printers: set[int],
        *,
        require_plate_clear: bool = True,
    ):
        """Start drying on idle printers based on humidity.

        Three modes (can all be enabled independently):
        - queue_drying_enabled: Dry between scheduled queue prints
        - ambient_drying_enabled: Dry any idle printer when humidity is high, regardless of queue
        - print_drying_enabled: Also evaluate printers that are currently printing,
          when model+firmware supports "Print While Drying" (gated by
          supports_drying_while_printing). Drying temperature is capped at
          max(40, preset_temp - 5) to protect spools mid-print.
        """
        queue_drying_enabled = await self._get_bool_setting(db, "queue_drying_enabled")
        ambient_drying_enabled = await self._get_bool_setting(db, "ambient_drying_enabled")
        print_drying_enabled = await self._get_bool_setting(db, "print_drying_enabled")
        if not queue_drying_enabled and not ambient_drying_enabled:
            # Stop active drying on all printers if both features disabled
            if self._drying_in_progress:
                for pid in list(self._drying_in_progress):
                    logger.info("Auto-drying: printer %d — stopping, auto-drying disabled", pid)
                    await self._stop_drying(pid)
            return

        # Update drying state from printer status (handles backend restart)
        self._sync_drying_state()

        # Find printers with scheduled items (for queue drying mode)
        printers_with_scheduled: set[int] = set()
        printers_with_items: set[int] = set()
        for item in queue_items:
            if item.printer_id:
                printers_with_items.add(item.printer_id)
                if item.scheduled_time and not item.manual_start:
                    printers_with_scheduled.add(item.printer_id)

        # If only queue mode is on and no printers have scheduled items, stop drying
        # (but skip this short-circuit when print_drying_enabled is on — busy printers
        # may still be eligible for mid-print drying regardless of queue state).
        if not ambient_drying_enabled and not printers_with_scheduled and not print_drying_enabled:
            for pid in list(self._drying_in_progress):
                logger.info("Auto-drying: printer %d — stopping, no scheduled prints in queue", pid)
                await self._stop_drying(pid)
            return

        # Get humidity threshold (global fallback)
        result = await db.execute(select(Settings).where(Settings.key == "ams_humidity_fair"))
        setting = result.scalar_one_or_none()
        global_humidity_threshold = int(setting.value) if setting else 60

        # Per-filament humidity threshold overrides (#1605). Empty → fall back
        # to the global threshold for every AMS unit.
        per_type_thresholds = await self._get_humidity_thresholds(db)

        # Get drying presets
        presets = await self._get_drying_presets(db)

        # Determine if drying should be skipped for printers with pending items
        block_for_drying = await self._get_bool_setting(db, "queue_drying_block")

        # Get all active printers
        all_printers = await db.execute(select(Printer).where(Printer.is_active.is_(True)))
        for printer in all_printers.scalars():
            pid = printer.id

            # Resolve model+firmware up front — needed to decide whether this printer
            # qualifies for mid-print drying (busy printer on capable hardware).
            state = printer_manager.get_status(pid)
            if not state:
                logger.debug("Auto-drying: printer %d skipped — no state", pid)
                continue
            model = printer_manager.get_model(pid)
            firmware = state.firmware_version

            mid_print = (
                pid in busy_printers and print_drying_enabled and supports_drying_while_printing(model, firmware)
            )

            if pid in busy_printers and not mid_print:
                logger.debug("Auto-drying: printer %d skipped — busy", pid)
                continue

            if not mid_print:
                # In queue-only mode, only dry printers that have scheduled prints
                if not ambient_drying_enabled and pid not in printers_with_scheduled:
                    if self._drying_in_progress.get(pid):
                        logger.info("Auto-drying: printer %d — stopping, no scheduled prints for this printer", pid)
                        await self._stop_drying(pid)
                    logger.debug("Auto-drying: printer %d skipped — no scheduled prints", pid)
                    continue
                # When block mode is on, don't START new drying on printers with pending items.
                # But allow already-drying printers through so humidity auto-stop logic still runs.
                if block_for_drying and pid in printers_with_items and not self._drying_in_progress.get(pid):
                    logger.debug("Auto-drying: printer %d skipped — has pending items (block mode)", pid)
                    continue
            if not printer_manager.is_connected(pid):
                logger.debug("Auto-drying: printer %d skipped — not connected", pid)
                continue
            if not mid_print and not self._is_printer_idle(pid, require_plate_clear):
                logger.debug("Auto-drying: printer %d skipped — not idle", pid)
                continue

            # Check drying capability. For mid-print path, supports_drying_while_printing
            # was already verified when computing mid_print above.
            if not mid_print and not supports_drying(model, firmware):
                logger.debug("Auto-drying: printer %d skipped — model %s does not support drying", pid, model)
                continue

            # Check each AMS unit from raw_data
            ams_list = state.raw_data.get("ams", [])
            logger.debug("Auto-drying: printer %d — checking %d AMS units", pid, len(ams_list))
            for ams_data in ams_list:
                module_type = str(ams_data.get("module_type") or "")
                ams_id = int(ams_data.get("id", 0))
                # Only n3f/n3s support drying
                if module_type not in ("n3f", "n3s"):
                    logger.debug("Auto-drying: printer %d AMS %d skipped — module_type=%s", pid, ams_id, module_type)
                    continue

                # Resolve per-filament humidity threshold for this AMS unit (#1605).
                # Most-restrictive of all loaded tray types; falls back to the
                # global threshold when no overrides are configured.
                trays = ams_data.get("tray", []) or []
                humidity_threshold = self.resolve_humidity_threshold(
                    trays, per_type_thresholds, global_humidity_threshold
                )

                dry_time = int(ams_data.get("dry_time") or 0)

                # Read humidity — prefer humidity_raw (actual %) over humidity (index 1-5)
                humidity = None
                h_raw = ams_data.get("humidity_raw")
                if h_raw is not None:
                    try:
                        humidity = int(h_raw)
                    except (ValueError, TypeError):
                        pass
                if humidity is None:
                    h_idx = ams_data.get("humidity")
                    if h_idx is not None:
                        try:
                            humidity = int(h_idx)
                        except (ValueError, TypeError):
                            pass
                # Already drying — let it run to its configured duration (#1892).
                #
                # We deliberately do NOT stop drying from a humidity re-check here.
                # Relative humidity drops steeply in heated air, so the AMS sensor
                # reads ~15-20% within minutes of the dryer starting even while the
                # filament is still saturated. A humidity-based early-stop therefore
                # always fires at the minimum-time floor, truncating both user-started
                # manual cycles and Bambuddy's own preset-duration dries to ~30 min.
                # The firmware stops when the configured duration elapses; scheduling
                # stops (print takes priority, queue no longer needs drying) are
                # handled separately via _stop_drying().
                if dry_time > 0:
                    if pid not in self._drying_in_progress:
                        # Drying we didn't start (manual or from before restart) —
                        # track it so scheduling stops still apply; never auto-stop it.
                        self._drying_in_progress[pid] = time.monotonic()
                    logger.debug(
                        "Auto-drying: printer %d AMS %d — drying (%dm left, humidity %s%%), letting it run",
                        pid,
                        ams_id,
                        dry_time,
                        humidity,
                    )
                    continue

                # Humidity below threshold — no need to start drying
                if humidity is None or humidity <= humidity_threshold:
                    logger.debug(
                        "Auto-drying: printer %d AMS %d skipped — humidity %s <= threshold %d",
                        pid,
                        ams_id,
                        humidity,
                        humidity_threshold,
                    )
                    continue

                # Check cannot-dry reasons (power constraints etc.)
                sf_reasons = ams_data.get("dry_sf_reason", [])
                if sf_reasons:
                    logger.debug(
                        "Auto-drying: printer %d AMS %d skipped — cannot dry reasons: %s",
                        pid,
                        ams_id,
                        sf_reasons,
                    )
                    continue

                # Get conservative drying params for mixed filaments
                params = self._get_conservative_drying_params(trays, module_type, presets)
                if not params:
                    logger.debug(
                        "Auto-drying: printer %d AMS %d skipped — no drying-eligible filaments in trays", pid, ams_id
                    )
                    continue

                temp, duration_hours, filament_type = params

                # Mid-print drying: cap drying temperature to protect spools (Bambu warns
                # "drying temperature must not exceed the filament's softening temperature"
                # for Print While Drying). Floor at 40 degC — below that the dryer is
                # ineffective and firmware will reject anyway.
                if mid_print:
                    temp = max(40, temp - 5)

                # Start drying
                logger.info(
                    "Auto-drying: printer %d AMS %d — humidity %d%% > threshold %d%%, "
                    "starting %s drying at %d°C for %dh%s",
                    pid,
                    ams_id,
                    humidity,
                    humidity_threshold,
                    filament_type,
                    temp,
                    duration_hours,
                    " (mid-print)" if mid_print else "",
                )
                success = printer_manager.send_drying_command(
                    pid, ams_id, temp, duration_hours, mode=1, filament=filament_type
                )
                if success:
                    self._drying_in_progress[pid] = time.monotonic()

    def _sync_drying_state(self):
        """Sync in-memory drying state with actual printer status.

        Handles backend restart — if a printer is drying but we don't know about it,
        update our state. If we think it's drying but it's not, clear it.
        """
        to_remove = []
        for pid in self._drying_in_progress:
            state = printer_manager.get_status(pid)
            if not state:
                to_remove.append(pid)
                continue
            # Check if any AMS unit is still drying
            ams_list = state.raw_data.get("ams", [])
            any_drying = any(int(a.get("dry_time") or 0) > 0 for a in ams_list)
            if not any_drying:
                to_remove.append(pid)
        for pid in to_remove:
            self._drying_in_progress.pop(pid, None)

    async def _stop_drying(self, printer_id: int):
        """Stop all active drying on a printer (print takes priority)."""
        state = printer_manager.get_status(printer_id)
        if not state:
            self._drying_in_progress.pop(printer_id, None)
            return

        ams_list = state.raw_data.get("ams", [])
        for ams_data in ams_list:
            dry_time = int(ams_data.get("dry_time") or 0)
            if dry_time > 0:
                ams_id = int(ams_data.get("id", 0))
                logger.info(
                    "Auto-drying: stopping drying on printer %d AMS %d — print takes priority",
                    printer_id,
                    ams_id,
                )
                printer_manager.send_drying_command(printer_id, ams_id, 0, 0, mode=0)
        self._drying_in_progress.pop(printer_id, None)

    async def _get_smart_plugs(self, db: AsyncSession, printer_id: int) -> list[SmartPlug]:
        """Get all smart plugs associated with a printer."""
        result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
        return list(result.scalars().all())

    # Bundled defaults for preheat_filament_targets (#1468). Values are the
    # chamber-temperature recommendations BambuStudio ships for the matching
    # filament profile; users can override via Settings → Workflow → Preheat
    # card. "default" applies when a loaded tray's normalised type isn't in
    # the map (rare — Bambu RFID-tagged spools always carry a known type).
    DEFAULT_PREHEAT_FILAMENT_TARGETS: dict[str, int] = {
        "PLA": 0,
        "PETG": 0,
        "PETG-CF": 40,
        "ABS": 45,
        "ASA": 45,
        "PA": 50,
        "PA-CF": 55,
        "PC": 50,
        "PC-FR": 50,
        "TPU": 0,
        "PVA": 0,
        "default": 0,
    }

    async def _get_preheat_filament_targets(self, db: AsyncSession) -> dict[str, int]:
        """Parse the user-configured filament→chamber-target map, falling back
        to DEFAULT_PREHEAT_FILAMENT_TARGETS on missing / malformed JSON. Keys
        are uppercased and the 'default' fallback is always present in the
        returned dict so the resolution loop can index it unconditionally."""
        raw = await self._get_setting(db, "preheat_filament_targets")
        if not raw:
            return dict(self.DEFAULT_PREHEAT_FILAMENT_TARGETS)
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("not an object")
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("preheat_filament_targets unparseable, using defaults: %s", exc)
            return dict(self.DEFAULT_PREHEAT_FILAMENT_TARGETS)
        # Coerce values to int; drop unparseable rows so a stray string
        # doesn't crash the loop.
        out: dict[str, int] = {}
        for key, value in parsed.items():
            try:
                out[str(key).upper()] = int(value)
            except (TypeError, ValueError):
                continue
        if "DEFAULT" not in out:
            out["DEFAULT"] = self.DEFAULT_PREHEAT_FILAMENT_TARGETS["default"]
        return out

    @staticmethod
    def _normalize_filament_type(tray_type: str) -> str:
        """Reduce the printer's tray_type to a preset-lookup key. Mirrors the
        existing drying-preset normalisation (split-at-space, upper-case) so
        the two maps share vocabulary — "PLA Basic" → "PLA", "PA-CF" stays
        "PA-CF" (no space to split on)."""
        return tray_type.split()[0].upper() if tray_type else ""

    def _derive_chamber_target(
        self,
        printer: Printer,
        targets: dict[str, int],
    ) -> int:
        """Look up the chamber target for each loaded AMS tray and return the
        max. Returns 0 when no AMS data is available (e.g. external-spool
        prints) or when every loaded slot maps to 0 — the chamber phase then
        short-circuits in the main loop.

        Reads from `printer_manager.get_status(...).raw_data['ams']`, which is
        the same source the dispatcher uses for AMS slot mapping. Empty / RFID-
        less slots have empty `tray_type` and contribute nothing."""
        state = printer_manager.get_status(printer.id)
        if state is None:
            return 0
        ams_list = (state.raw_data or {}).get("ams") if state.raw_data else None
        # Older Bambu firmware nests AMS as {"ams": {"ams": [...]}} — try both.
        if isinstance(ams_list, dict):
            ams_list = ams_list.get("ams") or []
        if not isinstance(ams_list, list):
            return 0
        best = 0
        for ams in ams_list:
            for tray in (ams.get("tray") or []) if isinstance(ams, dict) else []:
                normalised = self._normalize_filament_type(tray.get("tray_type") or "")
                if not normalised:
                    continue
                target = targets.get(normalised, targets.get("DEFAULT", 0))
                if target > best:
                    best = target
        return best

    async def _preheat_and_soak(
        self,
        db: AsyncSession,
        item: PrintQueueItem,
        printer: Printer,
        archive: PrintArchive | None,
    ) -> None:
        """Run the per-printer preheat + heat-soak stage before FTP upload (#1468).

        Resolution order:
          1. `item.preheat_override` — 'off' skips entirely; 'inherit' falls back
             to the global `preheat_enabled` setting; 'on' forces the stage on
             even if the global is off.
          2. Chamber target — `item.preheat_chamber_target_override` if non-null;
             else max of `preheat_filament_targets[normalize(t.tray_type)]`
             across loaded AMS slots; else 0 (skips chamber phase, keeps bed
             phase + soak timer).
          3. Three hardware tiers branch the wait loop:
             - Chamber heater (H2C/H2D/H2DPro/H2S/X2D/X1E via supports_chamber_heater):
               send M141 to the resolved target, then wait for the chamber sensor
               to reach it (or the max-wait timeout to elapse).
             - Chamber sensor only (X1C/P2S via supports_chamber_temp ∧ ¬supports_chamber_heater):
               no M141; the bed is the only heat source, so we wait for the chamber
               sensor to rise via bed radiation OR fall through on timeout.
             - No chamber sensor (P1S/P1P/A1/A1 Mini): no way to verify chamber
               temperature; the function just heats the bed and holds for the
               configured soak duration.

        The bed target comes from the archive's parsed metadata
        (`bed_temperature`); if missing the preheat stage logs and returns
        without dispatching anything, rather than guessing at a default that
        might wreck filament setup.

        Failures are logged but never re-raised — preheat is best-effort. A
        printer that goes offline mid-soak, a refused gcode command, or a
        missing temperature reading must not turn into a failed queue item; the
        normal upload + start path runs immediately after this method returns.
        """
        override = (getattr(item, "preheat_override", None) or "inherit").lower()
        if override == "off":
            return
        if override == "inherit":
            enabled = await self._get_bool_setting(db, "preheat_enabled", default=False)
            if not enabled:
                return
        # override == "on" forces the stage on regardless of the global setting.

        max_wait = await self._get_int_setting(db, "preheat_max_wait_seconds", default=900)
        soak_seconds = await self._get_int_setting(db, "preheat_soak_seconds", default=300)

        # Chamber target resolution:
        #   1. Explicit per-item override beats everything (user knows best).
        #   2. Otherwise derive from loaded AMS filament types via the per-
        #      filament target map. PLA-only print derives 0 → chamber phase
        #      auto-skips without the user touching anything.
        explicit_target = getattr(item, "preheat_chamber_target_override", None)
        if explicit_target is not None and explicit_target > 0:
            chamber_target = int(explicit_target)
            chamber_source = "item-override"
        elif explicit_target == 0:
            chamber_target = 0  # explicit 0 means "no chamber, even if filament wants it"
            chamber_source = "item-override-zero"
        else:
            targets = await self._get_preheat_filament_targets(db)
            chamber_target = self._derive_chamber_target(printer, targets)
            chamber_source = "filament-map"

        bed_target = int(archive.bed_temperature) if archive and archive.bed_temperature else 0
        if bed_target <= 0:
            logger.info(
                "Queue item %s: preheat skipped — archive has no bed_temperature metadata",
                item.id,
            )
            return

        client = printer_manager.get_client(printer.id)
        if client is None:
            logger.warning("Queue item %s: preheat skipped — printer client unavailable", item.id)
            return

        model = printer.model or ""
        has_heater = supports_chamber_heater(model)
        has_sensor = supports_chamber_temp(model)
        do_chamber = chamber_target > 0 and (has_heater or has_sensor)

        logger.info(
            "Queue item %s: preheat starting — bed=%d°C chamber_target=%d°C (source=%s override=%s "
            "model=%s has_heater=%s has_sensor=%s) max_wait=%ds soak=%ds",
            item.id,
            bed_target,
            chamber_target if do_chamber else 0,
            chamber_source,
            override,
            model,
            has_heater,
            has_sensor,
            max_wait,
            soak_seconds,
        )

        # Dispatch heaters. set_bed_temperature / set_chamber_temperature already
        # cache the target locally so the polling reads below see consistent
        # state (firmware MQTT echoes lag by ~1s).
        try:
            client.set_bed_temperature(bed_target)
        except Exception as exc:
            logger.warning("Queue item %s: preheat bed M140 failed: %s", item.id, exc)
            return

        # Airduct mode (#1468 follow-up). Models with the cooling/heating flap
        # (H2C/H2D/H2D Pro/H2S/X2D/P2S) keep the flap whatever the user last
        # left it on, regardless of M141. Default cooling actively vents the
        # chamber, so a `chamber_target > 0` print with the flap stuck in
        # cooling never converges — the heater fights the open exhaust. We
        # flip the flap BEFORE M141 to "heating" when the preheat wants
        # chamber heat, and back to "cooling" when it doesn't (PLA-only print
        # on an H2D that was previously running ABS would otherwise stay in
        # heating mode and overheat PLA). The current-state read keeps the
        # command idempotent — no MQTT chatter when the flap is already where
        # we want it.
        if supports_airduct(model):
            desired_airduct = "heating" if chamber_target > 0 else "cooling"
            desired_id = 1 if desired_airduct == "heating" else 0
            current_state = printer_manager.get_status(printer.id)
            current_airduct = getattr(current_state, "airduct_mode", None) if current_state else None
            if current_airduct != desired_id:
                try:
                    client.set_airduct_mode(desired_airduct)
                except Exception as exc:
                    logger.warning(
                        "Queue item %s: preheat airduct %s mode failed: %s",
                        item.id,
                        desired_airduct,
                        exc,
                    )

        if do_chamber and has_heater:
            try:
                client.set_chamber_temperature(chamber_target)
            except Exception as exc:
                logger.warning("Queue item %s: preheat chamber M141 failed: %s", item.id, exc)

        # Wait for convergence. Bed warm-up is fast (~5 min from cold); chamber
        # via M141 takes a few minutes; chamber via bed radiation can take 20+.
        # Poll every 3s — frequent enough for responsive logging without
        # spamming the MQTT state stream. The "converged" predicate is:
        #   bed reached target (within 2°C tolerance for floating-point + heater hysteresis),
        #   AND
        #   chamber phase satisfied (no chamber phase, no sensor, or sensor reached target).
        BED_TOLERANCE = 2.0
        CHAMBER_TOLERANCE = 2.0
        POLL_INTERVAL = 3.0
        deadline = asyncio.get_event_loop().time() + max_wait

        while True:
            state = printer_manager.get_status(printer.id)
            if state is None:
                logger.warning("Queue item %s: preheat lost state during wait", item.id)
                break

            temps = state.temperatures or {}
            bed_now = float(temps.get("bed", 0) or 0)
            chamber_now = float(temps.get("chamber", 0) or 0)
            bed_ok = bed_now >= bed_target - BED_TOLERANCE

            if not do_chamber:
                chamber_ok = True  # phase disabled or model has neither sensor nor heater
            elif not has_sensor:
                chamber_ok = True  # P1S etc — can't read, rely on soak timer only
            else:
                chamber_ok = chamber_now >= chamber_target - CHAMBER_TOLERANCE

            if bed_ok and chamber_ok:
                logger.info(
                    "Queue item %s: preheat target reached (bed=%.1f chamber=%.1f) — entering soak",
                    item.id,
                    bed_now,
                    chamber_now,
                )
                break

            if asyncio.get_event_loop().time() >= deadline:
                logger.info(
                    "Queue item %s: preheat max_wait reached (bed=%.1f/%d chamber=%.1f/%d) — falling through to soak",
                    item.id,
                    bed_now,
                    bed_target,
                    chamber_now,
                    chamber_target if do_chamber else 0,
                )
                break

            await asyncio.sleep(POLL_INTERVAL)

        if soak_seconds > 0:
            logger.info("Queue item %s: preheat soak — holding for %ds", item.id, soak_seconds)
            await asyncio.sleep(soak_seconds)

        logger.info("Queue item %s: preheat complete — proceeding to upload", item.id)

    async def _power_on_and_wait(self, plug: SmartPlug, printer_id: int, db: AsyncSession) -> bool:
        """Turn on smart plug and wait for printer to connect.

        Returns True if printer connected successfully within timeout.
        """
        # Get the appropriate service for the plug type (Tasmota or Home Assistant)
        service = await smart_plug_manager.get_service_for_plug(plug, db)

        # Check current plug state
        status = await service.get_status(plug)
        if not status.get("reachable"):
            logger.warning("Smart plug '%s' is not reachable", plug.name)
            return False

        # Turn on if not already on
        if status.get("state") != "ON":
            success = await service.turn_on(plug)
            if not success:
                logger.warning("Failed to turn on smart plug '%s'", plug.name)
                return False
            logger.info("Powered on smart plug '%s' for printer %s", plug.name, printer_id)

        # Get printer from database for connection
        result = await db.execute(select(Printer).where(Printer.id == printer_id))
        printer = result.scalar_one_or_none()
        if not printer:
            logger.error("Printer %s not found in database", printer_id)
            return False

        # Wait for printer to boot (give it some time before trying to connect)
        logger.info("Waiting 30s for printer %s to boot...", printer_id)
        await asyncio.sleep(30)

        # Try to connect to the printer periodically
        elapsed = 30  # Already waited 30s
        while elapsed < self._power_on_wait_time:
            # Try to connect
            logger.info("Attempting to connect to printer %s...", printer_id)
            try:
                connected = await printer_manager.connect_printer(printer)
                if connected:
                    logger.info("Printer %s connected after %ss", printer_id, elapsed)
                    # Give it a moment to stabilize and get status
                    await asyncio.sleep(5)
                    return True
            except Exception as e:
                logger.debug("Connection attempt failed: %s", e)

            await asyncio.sleep(self._power_on_check_interval)
            elapsed += self._power_on_check_interval
            logger.debug("Waiting for printer %s to connect... (%ss)", printer_id, elapsed)

        logger.warning("Printer %s did not connect within %ss after power on", printer_id, self._power_on_wait_time)
        return False

    async def _check_previous_success(self, db: AsyncSession, item: PrintQueueItem) -> bool:
        """Check if the previous print on this printer succeeded.

        A user-cancelled predecessor is treated as neutral — `cancelled` is a
        deliberate action, not a failure, so subsequent items should still
        dispatch (#1667). `skipped` is excluded from the lookback entirely:
        a skip isn't an actual print attempt, so it must not gate downstream
        items — counting it as a failed predecessor was the cascade bug that
        let a single cancellation block 18 items over 3 days for the reporter.
        Only `failed` and `aborted` — real print-attempt failures — block.

        Failures with `gate_acknowledged=True` (set by the per-printer Resume
        action — #1818) are also excluded from the lookback so the user can
        clear the gate after fixing the physical issue without having to
        re-queue every downstream job.
        """
        result = await db.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.printer_id == item.printer_id)
            .where(PrintQueueItem.id != item.id)
            .where(PrintQueueItem.status.in_(["completed", "failed", "cancelled", "aborted"]))
            .where(PrintQueueItem.gate_acknowledged == False)  # noqa: E712
            .order_by(PrintQueueItem.completed_at.desc())
            .limit(1)
        )
        prev_item = result.scalar_one_or_none()

        # If no previous item, assume success (first in queue)
        if not prev_item:
            return True

        return prev_item.status in ("completed", "cancelled")

    async def _power_off_if_needed(self, db: AsyncSession, item: PrintQueueItem):
        """Schedule power-off if the queue item enabled auto_off_after.

        Delegates to the smart-plug manager so the off honours each plug's
        configured strategy (time delay or temperature threshold), is cancelled
        if the printer starts printing again, and never cuts power on a loaded
        print (#1890). Previously this hardcoded a 50°C / 600s cooldown wait and
        powered off on the timeout regardless of print state.
        """
        if not item.auto_off_after:
            return
        try:
            await smart_plug_manager.schedule_off_after_queue_job(item.printer_id, db)
        except Exception as e:
            logger.warning("Auto-off: Failed to schedule power-off for printer %s: %s", item.printer_id, e)

    async def _get_job_name(self, db: AsyncSession, item: PrintQueueItem) -> str:
        """Get a human-readable name for a queue item."""
        if item.archive_id:
            result = await db.execute(select(PrintArchive).where(PrintArchive.id == item.archive_id))
            archive = result.scalar_one_or_none()
            if archive:
                return archive.filename.replace(".gcode.3mf", "").replace(".3mf", "")
        if item.library_file_id:
            result = await db.execute(LibraryFile.active().where(LibraryFile.id == item.library_file_id))
            library_file = result.scalar_one_or_none()
            if library_file:
                return library_file.filename.replace(".gcode.3mf", "").replace(".3mf", "")
        return f"Job #{item.id}"

    async def _get_printer(self, db: AsyncSession, printer_id: int) -> Printer | None:
        """Get printer by ID."""
        result = await db.execute(select(Printer).where(Printer.id == printer_id))
        return result.scalar_one_or_none()

    async def _block_on_filament_deficit(
        self,
        db: AsyncSession,
        item: PrintQueueItem,
    ) -> bool:
        """Promote the item to manual_start when the assigned spool is short (#1496).

        Returns True when this dispatch attempt was blocked, False when the
        item is clear to start. A previously-flagged item whose spool has
        since been swapped to one with enough material clears the flag here
        so the next scheduler tick dispatches it.
        """
        # User has explicitly acknowledged the deficit ("Print Anyway") —
        # don't re-flag, don't even compute. Without this short-circuit the
        # scheduler bounces between "user said anyway" (route clears
        # manual_start) and "scheduler re-blocked" (this method re-flags it
        # on identical spool state) (#1698-followup).
        if item.skip_filament_check:
            # #1762 diagnostic: surface the short-circuit at INFO so a
            # future "Print Anyway didn't work" report (e.g. issue #1762
            # comment 3) has actionable evidence in the support bundle
            # without needing DEBUG enabled.
            logger.info(
                "Queue item %s honouring user's Print Anyway acknowledgement — skipping deficit check",
                item.id,
            )
            return False

        try:
            deficit = await compute_deficit_for_queue_item(db, item)
        except Exception as e:
            # Never let a flaky deficit check wedge the queue — log and let
            # dispatch proceed. The PrintModal-side check still runs on the
            # manual paths.
            logger.warning("Filament deficit check failed for item %s: %s", item.id, e)
            return False

        if deficit:
            item.filament_short = True
            item.manual_start = True
            await db.commit()
            job_name = await self._get_job_name(db, item)
            printer = await self._get_printer(db, item.printer_id) if item.printer_id else None
            logger.info(
                "Queue item %s blocked on filament deficit (%d slot(s)) — promoted to manual_start",
                item.id,
                len(deficit),
            )
            try:
                await notification_service.on_queue_job_waiting(
                    job_name=job_name,
                    target_model=(printer.model if printer else "") or "",
                    waiting_reason="filament_short",
                    db=db,
                )
            except Exception as e:
                logger.debug("filament_short notification failed for item %s: %s", item.id, e)
            return True

        # No deficit — clear any stale flag from a previous tick.
        if item.filament_short:
            item.filament_short = False
            await db.commit()
        return False

    async def _propagate_owner_to_printer_manager(self, db: AsyncSession, item: PrintQueueItem) -> None:
        """Hand the queue item's owner to printer_manager so the
        print-complete callback can credit the user in PrintLogEntry (#1670).

        No-ops when the item has no `created_by_id` or the referenced user
        row is missing (e.g. user deleted between queue-add and dispatch —
        in that case the print log row falls back to the existing un-credited
        behaviour rather than crashing the dispatch).
        """
        if not item.created_by_id:
            return
        from backend.app.models.user import User

        owner = await db.get(User, item.created_by_id)
        if owner:
            printer_manager.set_current_print_user(item.printer_id, owner.id, owner.username)

    async def _start_print(self, db: AsyncSession, item: PrintQueueItem):
        """Upload file and start print for a queue item.

        Supports two sources:
        - archive_id: Print from an existing archive
        - library_file_id: Print from a library file (file manager)
        """
        logger.info("Starting queue item %s", item.id)

        # Get printer first (needed for both paths)
        result = await db.execute(select(Printer).where(Printer.id == item.printer_id))
        printer = result.scalar_one_or_none()
        if not printer:
            item.status = "failed"
            item.error_message = "Printer not found"
            item.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error("Queue item %s: Printer %s not found", item.id, item.printer_id)
            await self._power_off_if_needed(db, item)
            return

        # Check printer is connected
        if not printer_manager.is_connected(item.printer_id):
            item.status = "failed"
            item.error_message = "Printer not connected"
            item.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error("Queue item %s: Printer %s not connected", item.id, item.printer_id)
            await self._power_off_if_needed(db, item)
            return

        # Cancel-while-dispatching race (#1853): the scheduler's snapshot of
        # `items` was taken at the top of check_queue, but the user can /cancel
        # any pending row in the gap before we reach this point. Re-read the
        # row and bail out cleanly instead of starting an FTP upload for a row
        # that's already cancelled. The atomic CAS at the pending→printing
        # transition (below, before start_print) is the load-bearing guard;
        # this is the early-exit optimisation that avoids wasted FTP I/O.
        await db.refresh(item)
        if item.status != "pending":
            logger.info(
                "Queue item %s no longer pending (status=%s) — aborting dispatch",
                item.id,
                item.status,
            )
            return

        # Determine source: archive or library file
        archive = None
        library_file = None
        file_path = None
        filename = None
        cleanup_disk_paths: list[Path] = []

        if item.archive_id:
            # Print from archive
            result = await db.execute(select(PrintArchive).where(PrintArchive.id == item.archive_id))
            archive = result.scalar_one_or_none()
            if not archive:
                item.status = "failed"
                item.error_message = "Archive not found"
                item.completed_at = datetime.now(timezone.utc)
                await db.commit()
                logger.error("Queue item %s: Archive %s not found", item.id, item.archive_id)
                await self._power_off_if_needed(db, item)
                return

            file_path = settings.base_dir / archive.file_path
            filename = archive.filename

        elif item.library_file_id:
            # Print from library file (file manager)
            result = await db.execute(LibraryFile.active().where(LibraryFile.id == item.library_file_id))
            library_file = result.scalar_one_or_none()
            if not library_file:
                item.status = "failed"
                item.error_message = "Library file not found"
                item.completed_at = datetime.now(timezone.utc)
                await db.commit()
                logger.error("Queue item %s: Library file %s not found", item.id, item.library_file_id)
                await self._power_off_if_needed(db, item)
                return
            # Library files store absolute paths
            lib_path = Path(library_file.file_path)
            file_path = lib_path if lib_path.is_absolute() else settings.base_dir / library_file.file_path
            filename = library_file.filename

            # Create archive from library file so usage tracking has access to the 3MF
            queue_item_id = item.id
            try:
                from backend.app.services.archive import ArchiveService

                archive_service = ArchiveService(db)
                archive = await archive_service.archive_print(
                    printer_id=item.printer_id,
                    source_file=file_path,
                    original_filename=filename,
                    created_by_id=item.created_by_id,
                    project_id=item.project_id,
                )
                if archive:
                    item.archive_id = archive.id
                    if item.cleanup_library_after_dispatch and not library_file.is_external:
                        item.library_file_id = None
                        cleanup_disk_paths.append(file_path)
                        if library_file.thumbnail_path:
                            thumb_path = Path(library_file.thumbnail_path)
                            if not thumb_path.is_absolute():
                                thumb_path = settings.base_dir / library_file.thumbnail_path
                            cleanup_disk_paths.append(thumb_path)
                        await db.delete(library_file)
                        file_path = settings.base_dir / archive.file_path
                        filename = archive.filename
                    # Commit, not flush — flush opens the SQLite write
                    # transaction (item.archive_id update + library_file
                    # delete) and would hold the WAL writer lock through the
                    # FTP upload below, causing "database is locked" cascades
                    # for sensor history + concurrent cancels (#1853).
                    await db.commit()
                    logger.info(
                        "Queue item %s: Created archive %s from library file %s",
                        item.id,
                        archive.id,
                        item.library_file_id,
                    )
            except Exception as e:
                logger.warning(
                    "Queue item %s: Failed to create archive from library file: %s",
                    queue_item_id,
                    e,
                    exc_info=True,
                )
                await db.rollback()
                item = await db.get(PrintQueueItem, queue_item_id)
                if item:
                    item.status = "failed"
                    item.error_message = "Failed to create archive from library file"
                    item.completed_at = datetime.now(timezone.utc)
                    await db.commit()
                    await self._power_off_if_needed(db, item)
                return

            if not archive:
                item.status = "failed"
                item.error_message = "Failed to create archive from library file"
                item.completed_at = datetime.now(timezone.utc)
                await db.commit()
                logger.error("Queue item %s: Archive creation from library file returned no archive", item.id)
                await self._power_off_if_needed(db, item)
                return

        else:
            # Neither archive nor library file specified
            item.status = "failed"
            item.error_message = "No source file specified"
            item.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error("Queue item %s: No archive_id or library_file_id specified", item.id)
            await self._power_off_if_needed(db, item)
            return

        # Check file exists on disk
        if not file_path.exists():
            item.status = "failed"
            item.error_message = "Source file not found on disk"
            item.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error("Queue item %s: File not found: %s", item.id, file_path)
            await self._power_off_if_needed(db, item)
            return

        # Nozzle-diameter mismatch guard (#1899). A file sliced for one nozzle
        # size dispatched to a printer with a different nozzle installed is
        # rejected by the firmware with a cryptic HMS ("Failed to get AMS mapping
        # table" 0700_8012, or "nozzle diameter … not consistent" 0500_4038) that
        # gives the user no idea what went wrong. Catch it here, before we spend
        # time preheating and uploading, and fail with an actionable message.
        # Fail-safe by construction: only a POSITIVE mismatch blocks — when the
        # slice carries no nozzle diameter (archive.nozzle_diameter is None) or
        # the printer hasn't reported its nozzles yet, we fall through and let the
        # print proceed exactly as before. On dual-nozzle printers (H2D) a match
        # against EITHER installed nozzle passes, so a 0.6 slice is fine as long
        # as one of the two hotends is a 0.6.
        sliced_nozzle = archive.nozzle_diameter if archive else None
        if sliced_nozzle:
            installed = _installed_nozzle_diameters(printer_manager.get_status(item.printer_id))
            mismatch_msg = _nozzle_mismatch_message(sliced_nozzle, installed)
            if mismatch_msg:
                item.status = "failed"
                item.error_message = mismatch_msg
                item.completed_at = datetime.now(timezone.utc)
                await db.commit()
                logger.warning("Queue item %s: nozzle mismatch — %s", item.id, mismatch_msg)
                await notification_service.on_queue_job_failed(
                    job_name=filename.replace(".gcode.3mf", "").replace(".3mf", ""),
                    printer_id=printer.id,
                    printer_name=printer.name,
                    reason=mismatch_msg,
                    db=db,
                )
                try:
                    await ws_manager.send_queue_item_failed(
                        user_id=item.created_by_id,
                        queue_item_id=item.id,
                        printer_id=item.printer_id,
                        reason="nozzle_mismatch",
                    )
                except Exception:
                    pass
                await self._power_off_if_needed(db, item)
                return

        # Preheat / heat-soak (#1468) — fires before upload so the printer's
        # bed (and chamber, if applicable) is at temperature when the firmware
        # starts the actual print routine. Best-effort: any failure logs and
        # falls through to the normal upload+start path rather than turning a
        # configuration issue into a failed queue item.
        await self._preheat_and_soak(db, item, printer, archive)

        # G-code injection for auto-print systems (#422)
        injected_path = None
        if item.gcode_injection:
            try:
                snippets_raw = await self._get_setting(db, "gcode_snippets")
                if snippets_raw:
                    snippets = json.loads(snippets_raw)
                    model_snippets = snippets.get(printer.model, {})
                    start_gc = (model_snippets.get("start_gcode") or "").strip()
                    end_gc = (model_snippets.get("end_gcode") or "").strip()
                    if start_gc or end_gc:
                        from backend.app.utils.threemf_tools import inject_gcode_into_3mf

                        injected_path = inject_gcode_into_3mf(
                            file_path, item.plate_id or 1, start_gc or None, end_gc or None
                        )
                        if injected_path:
                            file_path = injected_path
                            logger.info("Queue item %s: G-code injected for model %s", item.id, printer.model)
                        else:
                            logger.warning(
                                "Queue item %s: G-code injection returned no result, using original", item.id
                            )
            except Exception as e:
                logger.warning("Queue item %s: G-code injection failed, using original: %s", item.id, e)

        # Upload to root directory (not /cache/) - the start_print command references
        # files by name only (ftp://{filename}), so they must be in the root
        remote_filename = derive_remote_filename(filename)
        remote_path = f"/{remote_filename}"

        # Get FTP retry settings
        ftp_retry_enabled, ftp_retry_count, ftp_retry_delay, ftp_timeout = await get_ftp_retry_settings()

        logger.info(
            f"Queue item {item.id}: FTP upload starting - printer={printer.name} ({printer.model}), "
            f"ip={printer.ip_address}, file={remote_filename}, local_path={file_path}, "
            f"retry_enabled={ftp_retry_enabled}, retry_count={ftp_retry_count}, timeout={ftp_timeout}"
        )

        # Delete existing file if present (avoids 553 error on overwrite)
        try:
            logger.debug("Queue item %s: Deleting existing file %s if present...", item.id, remote_path)
            delete_result = await delete_file_async(
                printer.ip_address,
                printer.access_code,
                remote_path,
                socket_timeout=ftp_timeout,
                printer_model=printer.model,
            )
            logger.debug("Queue item %s: Delete result: %s", item.id, delete_result)
        except Exception as e:
            logger.debug("Queue item %s: Delete failed (may not exist): %s", item.id, e)

        # Dispatch toast — announce the upload start with the total byte
        # count so the frontend can render an honest progress bar.
        toast_uid = item.created_by_id
        toast_file_name = filename.replace(".gcode.3mf", "").replace(".3mf", "")
        try:
            total_bytes = file_path.stat().st_size
        except OSError:
            total_bytes = 0
        try:
            await ws_manager.send_queue_item_uploading(
                user_id=toast_uid,
                queue_item_id=item.id,
                printer_id=item.printer_id,
                printer_name=printer.name,
                file_name=toast_file_name,
                total_bytes=total_bytes,
            )
        except Exception:
            pass  # toast is best-effort

        progress_bridge = _UploadProgressBridge(toast_uid, item.id)

        try:
            if ftp_retry_enabled:
                uploaded = await with_ftp_retry(
                    upload_file_async,
                    printer.ip_address,
                    printer.access_code,
                    file_path,
                    remote_path,
                    socket_timeout=ftp_timeout,
                    printer_model=printer.model,
                    progress_callback=progress_bridge,
                    max_retries=ftp_retry_count,
                    retry_delay=ftp_retry_delay,
                    operation_name=f"Upload print to {printer.name}",
                )
            else:
                uploaded = await upload_file_async(
                    printer.ip_address,
                    printer.access_code,
                    file_path,
                    remote_path,
                    socket_timeout=ftp_timeout,
                    printer_model=printer.model,
                    progress_callback=progress_bridge,
                )
        except Exception as e:
            uploaded = False
            logger.error("Queue item %s: FTP error: %s (type: %s)", item.id, e, type(e).__name__)

        # Clean up injected temp file after upload attempt
        if injected_path and injected_path.exists():
            injected_path.unlink(missing_ok=True)

        if not uploaded:
            error_msg = (
                "Failed to upload file to printer. Check if SD card is inserted and properly formatted (FAT32/exFAT). "
                "See server logs for detailed diagnostics."
            )
            item.status = "failed"
            item.error_message = error_msg
            item.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error(
                f"Queue item {item.id}: FTP upload failed - printer={printer.name}, model={printer.model}, "
                f"ip={printer.ip_address}. Check logs above for storage diagnostics and specific error codes."
            )

            # Send failure notification
            await notification_service.on_queue_job_failed(
                job_name=filename.replace(".gcode.3mf", "").replace(".3mf", ""),
                printer_id=printer.id,
                printer_name=printer.name,
                reason="Failed to upload file to printer",
                db=db,
            )
            try:
                await ws_manager.send_queue_item_failed(
                    user_id=toast_uid,
                    queue_item_id=item.id,
                    printer_id=item.printer_id,
                    reason="upload_failed",
                )
            except Exception:
                pass
            await self._power_off_if_needed(db, item)
            return

        # Parse AMS mapping if stored
        ams_mapping = None
        if item.ams_mapping:
            try:
                ams_mapping = json.loads(item.ams_mapping)
            except json.JSONDecodeError:
                logger.warning("Queue item %s: Invalid AMS mapping JSON, ignoring", item.id)

        # Register as expected print so we don't create a duplicate archive
        # Only applicable for archive-based prints
        if archive:
            from backend.app.main import register_expected_print

            register_expected_print(
                item.printer_id,
                remote_filename,
                archive.id,
                ams_mapping=ams_mapping,
                created_by_id=item.created_by_id,
                plate_id=item.plate_id,
            )

        # Propagate the queue item's owner into printer_manager so the
        # print-complete callback can credit the user in the PrintLogEntry
        # (#1670). `created_by_id` is set either at queue-add time (UI-added
        # items) or when the user clicks the manual-start button.
        await self._propagate_owner_to_printer_manager(db, item)

        # IMPORTANT: Set status to "printing" BEFORE sending the print command.
        # This prevents phantom reprints if the backend crashes/restarts after the
        # print command is sent but before the status update is committed.
        # If we crash after this commit but before start_print(), the item will be
        # in "printing" status without actually printing - but that's safer than
        # accidentally reprinting the same file hours later.
        #
        # Atomic CAS (#1853): a user pressing /cancel mid-dispatch (between the
        # initial pending read at the top of check_queue and this point) flips
        # the row to "cancelled" in a separate session. Without the WHERE
        # status='pending' clause, the unconditional update here would silently
        # overwrite that cancellation and we'd ship the MQTT start_print below
        # — printer obeys, user sees "I pressed cancel and the print started".
        # rowcount==0 means the user won the race; bail out, best-effort delete
        # the file we just uploaded, do NOT send start_print.
        now_utc = datetime.now(timezone.utc)
        cas = await db.execute(
            update(PrintQueueItem)
            .where(PrintQueueItem.id == item.id)
            .where(PrintQueueItem.status == "pending")
            .values(status="printing", started_at=now_utc)
        )
        await db.commit()
        if cas.rowcount == 0:
            logger.info(
                "Queue item %s no longer pending at print-command time "
                "(cancelled or removed mid-dispatch) — aborting before MQTT send (#1853)",
                item.id,
            )
            try:
                await delete_file_async(
                    printer.ip_address,
                    printer.access_code,
                    remote_path,
                    socket_timeout=ftp_timeout,
                    printer_model=printer.model,
                )
            except Exception as cleanup_err:
                logger.debug(
                    "Queue item %s: best-effort cleanup of uploaded file failed: %s",
                    item.id,
                    cleanup_err,
                )
            try:
                await ws_manager.send_queue_item_failed(
                    user_id=toast_uid,
                    queue_item_id=item.id,
                    printer_id=item.printer_id,
                    reason="cancelled_mid_dispatch",
                )
            except Exception:
                pass
            return
        # Sync the in-memory item so subsequent code that reads item.status /
        # item.started_at sees the values we just persisted.
        item.status = "printing"
        item.started_at = now_utc

        for cleanup_path in cleanup_disk_paths:
            try:
                if cleanup_path.exists():
                    cleanup_path.unlink()
            except OSError as cleanup_err:
                logger.warning(
                    "TRANSIENT_LIBRARY_FILE_ORPHAN %s",
                    json.dumps(
                        {
                            "queue_item_id": item.id,
                            "path": str(cleanup_path),
                            "error": str(cleanup_err),
                        },
                        sort_keys=True,
                    ),
                )

        # Clear the awaiting-plate-clear flag now that we're starting a new print
        printer_manager.set_awaiting_plate_clear(item.printer_id, False)
        logger.info("Queue item %s: Status set to 'printing', sending print command...", item.id)

        # Capture state before dispatch so the watchdog can detect whether the
        # printer actually transitioned (#967). Also capture subtask_id so the
        # watchdog can recognise "command landed but state hasn't flipped yet"
        # on slow H2D transitions (#1078).
        pre_status = printer_manager.get_status(item.printer_id)
        pre_state = getattr(pre_status, "state", None) if pre_status else None
        pre_subtask_id = getattr(pre_status, "subtask_id", None) if pre_status else None
        pre_gcode_file = getattr(pre_status, "gcode_file", None) if pre_status else None

        # #1721: respect the user's explicit timelapse choice. The #1397
        # force-on at dispatch was removed because it caused per-layer nozzle
        # parking on slicer profiles with Timelapse Type = Smooth. Finish-photo
        # capture is now driven by the stg_cur=22 transition in bambu_mqtt.py
        # ("Filament unloading", toolhead parked, bed not yet dropped) with a
        # FINISH-state fallback — no need to force a video.
        effective_timelapse = bool(item.timelapse)

        # Start the print with AMS mapping, plate_id and print options.
        # nozzle_mapping rides through verbatim — JSON string captured from
        # Bambu Studio's project_file on VP intake (#1780); the MQTT layer
        # parses + injects it only for dual-nozzle models so a null on every
        # other model is a transparent pass-through.
        started = printer_manager.start_print(
            item.printer_id,
            remote_filename,
            plate_id=item.plate_id or 1,
            ams_mapping=ams_mapping,
            bed_levelling=item.bed_levelling,
            flow_cali=item.flow_cali,
            vibration_cali=item.vibration_cali,
            layer_inspect=item.layer_inspect,
            timelapse=effective_timelapse,
            use_ams=item.use_ams,
            nozzle_offset_cali=item.nozzle_offset_cali,
            nozzle_mapping=item.nozzle_mapping,
        )

        if started:
            logger.info("Queue item %s: Print started successfully - %s", item.id, filename)
            # No dispatch-toast event here: the legacy bg-dispatch path kept
            # status='processing' from upload start until the printer acked
            # (or timed out). The frontend derives "Awaiting printer…" purely
            # from upload_progress_pct >= 99.9; an explicit 'dispatched' WS
            # event would push the status chip out of 'PROCESSING' prematurely
            # — which is exactly what the screenshot at #1625-followup
            # complained about.

            # Register the local 3MF in the cover-cache so /cover skips FTP
            # (#1166 follow-up). file_path was resolved earlier from either the
            # archive or the library file row.
            if file_path is not None:
                cache_3mf_download(item.printer_id, remote_filename, file_path)

            # Hold the printer against further dispatches until the watchdog
            # confirms the printer transitioned (or until the hard timeout).
            # Prevents multi-plate batches from triple-dispatching onto the
            # same H2D Pro while it digests the first project_file (#1157).
            self._mark_printer_dispatched(item.printer_id, pre_state, pre_subtask_id)

            # Watchdog: if the printer never transitions out of pre_state AND
            # never advances subtask_id, the MQTT publish was accepted locally but
            # didn't reach the printer (half-broken session — same shape as
            # #887/#936). Revert the queue item so the next dispatch can pick it
            # up instead of leaving it stuck in "printing" (#967). subtask_id
            # check avoids false reverts on slow H2D FINISH→PREPARE transitions
            # that would otherwise cause the item to re-dispatch as a reprint
            # of the just-finished job (#1078).
            if pre_state:
                spawn_background_task(
                    self._watchdog_print_start(
                        item.id,
                        item.printer_id,
                        pre_state,
                        pre_subtask_id,
                        pre_gcode_file,
                        created_by_id=toast_uid,
                    ),
                    name=f"watchdog-print-start-{item.id}",
                )

            # Get estimated time for notification
            estimated_time = None
            if archive and archive.print_time_seconds:
                estimated_time = archive.print_time_seconds
            elif library_file and library_file.print_time_seconds:
                estimated_time = library_file.print_time_seconds

            # Send job started notification
            await notification_service.on_queue_job_started(
                job_name=filename.replace(".gcode.3mf", "").replace(".3mf", ""),
                printer_id=printer.id,
                printer_name=printer.name,
                db=db,
                estimated_time=estimated_time,
            )

            # MQTT relay - publish queue job started
            try:
                from backend.app.services.mqtt_relay import mqtt_relay

                await mqtt_relay.on_queue_job_started(
                    job_id=item.id,
                    filename=filename,
                    printer_id=printer.id,
                    printer_name=printer.name,
                    printer_serial=printer.serial_number,
                )
            except Exception:
                pass  # Don't fail if MQTT fails
        else:
            # Clean up uploaded file from SD card to prevent phantom prints
            try:
                await delete_file_async(
                    printer.ip_address,
                    printer.access_code,
                    remote_path,
                    printer_model=printer.model,
                )
            except Exception:
                pass  # Best-effort — don't fail the error handler

            # Print command failed - revert status
            item.status = "failed"
            item.error_message = "Failed to send print command to printer"
            item.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error(
                f"Queue item {item.id}: Failed to start print on {printer.name} ({printer.model}) - "
                f"printer_manager.start_print() returned False. "
                f"This may indicate: printer not connected, MQTT error, unsupported model configuration, or firmware issue. "
                f"Check printer status and backend logs for details."
            )

            # Send failure notification
            await notification_service.on_queue_job_failed(
                job_name=filename.replace(".gcode.3mf", "").replace(".3mf", ""),
                printer_id=printer.id,
                printer_name=printer.name,
                reason="Failed to send print command to printer - check printer connection and status",
                db=db,
            )
            try:
                await ws_manager.send_queue_item_failed(
                    user_id=toast_uid,
                    queue_item_id=item.id,
                    printer_id=item.printer_id,
                    reason="start_command_failed",
                )
            except Exception:
                pass

            await self._power_off_if_needed(db, item)

    @staticmethod
    async def _watchdog_print_start(
        queue_item_id: int,
        printer_id: int,
        pre_state: str,
        pre_subtask_id: str | None = None,
        pre_gcode_file: str | None = None,
        timeout: float = 90.0,
        phase_b_timeout: float = 180.0,
        poll_interval: float = 3.0,
        created_by_id: int | None = None,
    ) -> None:
        """Revert a queue item if the printer never acknowledges the start command.

        Bambuddy optimistically marks the queue item as "printing" right after the
        MQTT project_file publish succeeds locally. The watchdog runs in two phases:

        Phase A (up to ``timeout``): wait for either an active-state transition
        or a ``subtask_id`` advance past ``pre_subtask_id``. State alone is the
        primary signal; subtask_id advance handles the H2D case where state can
        sit at FINISH for ~50 s after the printer accepted ``project_file``
        before flipping to PREPARE (#1078). If neither happens, the MQTT publish
        was lost on a half-broken session (#887/#936) — revert and force
        reconnect (the #967 recovery path).

        Phase B (up to ``phase_b_timeout``, only if Phase A exited on subtask_id
        alone): keep watching for the active-state transition. subtask_id alone
        proves the file landed but not that the printer started — and a printer
        that accepts the command but stays at IDLE/FINISH indefinitely (e.g.
        cloud+LAN re-auth dance after a power cycle on old firmware, #1678)
        used to leave the queue item stuck in 'printing' forever because the
        old watchdog returned success as soon as subtask_id advanced. If Phase
        B times out, revert the queue item so the user can retry without
        restarting Bambuddy. Skip ``force_reconnect`` here: the file landed and
        a forced reconnect mid-parse triggers 0500_4003 (#1150).

        Phase A timeout raised from 45 s → 90 s as belt-and-braces for slow
        transitions that also don't emit an early subtask_id tick.
        """
        last_status = None
        landed_on_subtask = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            status = printer_manager.get_status(printer_id)
            if not status:
                # Printer disconnected — don't mess with the DB. Drop the
                # in-memory dispatch hold too so a fresh dispatch can retry
                # once the printer comes back; the hard timeout would
                # otherwise hold the printer unnecessarily.
                scheduler._release_dispatch_hold(printer_id)
                return
            last_status = status
            if status.state in _ACTIVE_PRINT_STATES:
                # Printer is actively processing the job — release the
                # post-dispatch hold so the next pending item for this printer
                # can be evaluated normally. We do NOT accept arbitrary state
                # transitions: a printer going FINISH -> IDLE (user dismissed
                # the post-print prompt without accepting our project_file)
                # would otherwise look like "command landed" and leave the
                # queue item stuck in 'printing' forever (#1370).
                scheduler._release_dispatch_hold(printer_id)
                try:
                    await ws_manager.send_queue_item_acked(
                        user_id=created_by_id,
                        queue_item_id=queue_item_id,
                        printer_id=printer_id,
                    )
                except Exception:
                    pass
                return
            if pre_subtask_id is not None and status.subtask_id is not None and status.subtask_id != pre_subtask_id:
                # Phase A exit — printer accepted the file (subtask_id flipped
                # to our submission id). Don't return yet: the printer may
                # have accepted the command but never actually start (e.g.
                # cloud+LAN re-auth dance after a power cycle, #1678). Phase
                # B watches for the active-state transition.
                landed_on_subtask = True
                break

        if landed_on_subtask:
            phase_b_deadline = time.monotonic() + phase_b_timeout
            while time.monotonic() < phase_b_deadline:
                await asyncio.sleep(poll_interval)
                status = printer_manager.get_status(printer_id)
                if not status:
                    scheduler._release_dispatch_hold(printer_id)
                    return
                last_status = status
                if status.state in _ACTIVE_PRINT_STATES:
                    scheduler._release_dispatch_hold(printer_id)
                    try:
                        await ws_manager.send_queue_item_acked(
                            user_id=created_by_id,
                            queue_item_id=queue_item_id,
                            printer_id=printer_id,
                        )
                    except Exception:
                        pass
                    return

        # No active-state transition. Revert the item so the scheduler can retry.
        # Drop the in-memory hold so the retry isn't blocked by it.
        scheduler._release_dispatch_hold(printer_id)

        # Three outcomes from the revert attempt, each routed differently:
        #   "reverted":          row flipped from printing -> pending, run recovery
        #   "already_moved_on":  item.status != 'printing' (completed/cancelled by
        #                        on_print_complete or user). Skip recovery entirely
        #                        — the print clearly landed somewhere even if the
        #                        watchdog didn't see the active-state transition.
        #   "revert_failed":     SQLite contention exhausted retries. Still run
        #                        recovery so the MQTT session gets a fresh client_id
        #                        on the half-broken-session path.
        async def _do_revert(db):
            item = await db.get(PrintQueueItem, queue_item_id)
            if not item or item.status != "printing":
                return "already_moved_on"
            item.status = "pending"
            item.started_at = None
            await db.commit()
            return "reverted"

        try:
            revert_outcome = await run_with_retry(_do_revert, label=f"watchdog revert item={queue_item_id}")
        except Exception as e:
            logger.warning(
                "Queue item %s: failed to revert to 'pending' (printer %d): %s — "
                "scheduler may keep treating this item as in-flight",
                queue_item_id,
                printer_id,
                e,
            )
            revert_outcome = "revert_failed"

        if revert_outcome == "already_moved_on":
            # Preserves the pre-#1370 early-return: if on_print_complete (or any
            # other path) already moved the item past 'printing', don't run the
            # MQTT session-recovery logic below — a forced reconnect on a healthy
            # session breaks ongoing prints on the same printer.
            return

        total_timeout = timeout + (phase_b_timeout if landed_on_subtask else 0.0)
        if revert_outcome == "reverted":
            if landed_on_subtask:
                logger.warning(
                    "Queue item %s: printer %d accepted project_file (subtask_id "
                    "advanced) but never transitioned to an active state within "
                    "%.0fs — printer wedged post-acceptance; reverted to 'pending' "
                    "for retry (#1678)",
                    queue_item_id,
                    printer_id,
                    total_timeout,
                )
            else:
                logger.warning(
                    "Queue item %s: printer %d did not respond to print command within "
                    "%.0fs (state still %s, subtask_id still %s) — reverted to 'pending' "
                    "for retry (#967)",
                    queue_item_id,
                    printer_id,
                    timeout,
                    pre_state,
                    pre_subtask_id,
                )

        # Phase B was entered iff subtask_id advanced, which means the
        # project_file landed on the printer. A forced reconnect at this point
        # would interrupt the printer's parse and trigger 0500_4003 (#1150) —
        # skip the recovery entirely.
        if landed_on_subtask:
            return

        # Phase A timeout path: if the printer's gcode_file changed since
        # pre-dispatch, the project_file command landed and the printer is
        # parsing — a forced reconnect mid-parse triggers 0500_4003 (#1150).
        # If gcode_file is unchanged, the publish was silently swallowed
        # (#887/#936) and force_reconnect recovery is what we want.
        client = printer_manager.get_client(printer_id)
        current_gcode_file = getattr(last_status, "gcode_file", None) if last_status else None
        publish_landed = current_gcode_file is not None and current_gcode_file != pre_gcode_file
        if publish_landed:
            logger.warning(
                "Queue item %s: gcode_file changed to %r (was %r) — printer "
                "received the command and is parsing slowly. Skipping forced "
                "MQTT reconnect to avoid 0500_4003 mid-parse (#1150).",
                queue_item_id,
                current_gcode_file,
                pre_gcode_file,
            )
        elif client and hasattr(client, "force_reconnect_stale_session"):
            client.force_reconnect_stale_session(
                f"queue print command unacknowledged after {timeout:.0f}s "
                f"(state still {pre_state}, gcode_file {current_gcode_file!r})"
            )


# Global scheduler instance
scheduler = PrintScheduler()
