"""Print scheduler service - processes the print queue."""

import asyncio
import json
import logging
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import defusedxml.ElementTree as ET
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.database import async_session
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.smart_plug import SmartPlug
from backend.app.services.bambu_ftp import delete_file_async, get_ftp_retry_settings, upload_file_async, with_ftp_retry
from backend.app.services.notification_service import notification_service
from backend.app.services.printer_manager import printer_manager, supports_drying
from backend.app.services.smart_plug_manager import smart_plug_manager
from backend.app.utils.printer_models import normalize_printer_model
from backend.app.utils.threemf_tools import extract_nozzle_mapping_from_3mf

logger = logging.getLogger(__name__)

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
        self._min_drying_seconds = 1800  # 30 minutes minimum before humidity re-check can stop drying
        # Track which printers are currently auto-drying (printer_id -> start timestamp)
        self._drying_in_progress: dict[int, float] = {}

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
            # Get all pending items, ordered by printer and position
            result = await db.execute(
                select(PrintQueueItem)
                .where(PrintQueueItem.status == "pending")
                .order_by(PrintQueueItem.printer_id, PrintQueueItem.position)
            )
            items = list(result.scalars().all())

            if not items:
                # No pending items — still check auto-drying on idle printers
                await self._check_auto_drying(db, [], set())
                return

            logger.info(
                "Queue check: found %d pending items: %s",
                len(items),
                [(i.id, i.printer_id, i.archive_id, i.library_file_id) for i in items],
            )

            # Track busy printers to avoid assigning multiple items to same printer
            busy_printers: set[int] = set()

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
                    printer_idle = self._is_printer_idle(item.printer_id)
                    printer_connected = printer_manager.is_connected(item.printer_id)

                    # If printer not connected, try to power on via smart plug
                    if not printer_connected:
                        plug = await self._get_smart_plug(db, item.printer_id)
                        if plug and plug.auto_on and plug.enabled:
                            logger.info("Printer %s offline, attempting to power on via smart plug", item.printer_id)
                            powered_on = await self._power_on_and_wait(plug, item.printer_id, db)
                            if powered_on:
                                printer_connected = True
                                printer_idle = self._is_printer_idle(item.printer_id)
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
                                printer_idle = self._is_printer_idle(item.printer_id)
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

                    # Start the print
                    await self._start_print(db, item)
                    busy_printers.add(item.printer_id)

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
                    )

                    # Update waiting_reason if changed and send notification when first waiting
                    if item.waiting_reason != waiting_reason:
                        was_waiting = item.waiting_reason is not None
                        item.waiting_reason = waiting_reason
                        await db.commit()

                        # Send waiting notification only when transitioning to waiting state
                        if waiting_reason and not was_waiting:
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

                        await self._start_print(db, item)
                        busy_printers.add(printer_id)

            # Log summary of skip reasons (helps diagnose why queue items aren't starting)
            if skip_reasons:
                logger.info("Queue skip summary: %s", skip_reasons)
            if busy_printers:
                # Log why each printer was busy (first time it was checked)
                for pid in busy_printers:
                    state = printer_manager.get_status(pid)
                    connected = printer_manager.is_connected(pid)
                    plate_cleared = printer_manager.is_plate_cleared(pid)
                    state_name = state.state if state else "NO_STATUS"
                    logger.info(
                        "Queue: printer %d not available — connected=%s, state=%s, plate_cleared=%s",
                        pid,
                        connected,
                        state_name,
                        plate_cleared,
                    )

            # Auto-drying: start drying on idle printers that have no pending queue items
            await self._check_auto_drying(db, items, busy_printers)

    async def _find_idle_printer_for_model(
        self,
        db: AsyncSession,
        model: str,
        exclude_ids: set[int],
        required_filament_types: list[str] | None = None,
        target_location: str | None = None,
        filament_overrides: list[dict] | None = None,
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
            is_idle = self._is_printer_idle(printer.id) if is_connected else False

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

        # Get filament requirements from source file
        filament_reqs = await self._get_filament_requirements(db, item)
        if not filament_reqs:
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

        # Compute mapping: match required filaments to available slots
        return self._match_filaments_to_slots(filament_reqs, loaded_filaments)

    async def _get_filament_requirements(self, db: AsyncSession, item: PrintQueueItem) -> list[dict] | None:
        """Extract filament requirements from the source 3MF file.

        Args:
            db: Database session
            item: Queue item with archive_id or library_file_id

        Returns:
            List of filament requirement dicts with slot_id, type, color, used_grams
        """
        file_path: Path | None = None

        if item.archive_id:
            result = await db.execute(select(PrintArchive).where(PrintArchive.id == item.archive_id))
            archive = result.scalar_one_or_none()
            if archive:
                file_path = settings.base_dir / archive.file_path
        elif item.library_file_id:
            result = await db.execute(select(LibraryFile).where(LibraryFile.id == item.library_file_id))
            library_file = result.scalar_one_or_none()
            if library_file:
                lib_path = Path(library_file.file_path)
                file_path = lib_path if lib_path.is_absolute() else settings.base_dir / library_file.file_path

        if not file_path or not file_path.exists():
            return None

        filaments = []
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                if "Metadata/slice_info.config" not in zf.namelist():
                    return None

                content = zf.read("Metadata/slice_info.config").decode()
                root = ET.fromstring(content)

                # Check if plate_id is specified - use that plate's filaments
                plate_id = item.plate_id
                if plate_id:
                    for plate_elem in root.findall("./plate"):
                        plate_index = None
                        for meta in plate_elem.findall("metadata"):
                            if meta.get("key") == "index":
                                plate_index = int(meta.get("value", "0"))
                                break
                        if plate_index == plate_id:
                            for filament_elem in plate_elem.findall("./filament"):
                                filament_id = filament_elem.get("id")
                                filament_type = filament_elem.get("type", "")
                                filament_color = filament_elem.get("color", "")
                                # tray_info_idx identifies the specific spool selected when slicing
                                tray_info_idx = filament_elem.get("tray_info_idx", "")
                                used_g = filament_elem.get("used_g", "0")
                                try:
                                    used_grams = float(used_g)
                                    if used_grams > 0 and filament_id:
                                        filaments.append(
                                            {
                                                "slot_id": int(filament_id),
                                                "type": filament_type,
                                                "color": filament_color,
                                                "tray_info_idx": tray_info_idx,
                                                "used_grams": round(used_grams, 1),
                                            }
                                        )
                                except (ValueError, TypeError):
                                    pass  # Skip filament entry with unparseable usage data
                            break
                else:
                    # No plate_id - extract all filaments with used_g > 0
                    for filament_elem in root.findall("./filament"):
                        filament_id = filament_elem.get("id")
                        filament_type = filament_elem.get("type", "")
                        filament_color = filament_elem.get("color", "")
                        # tray_info_idx identifies the specific spool selected when slicing
                        tray_info_idx = filament_elem.get("tray_info_idx", "")
                        used_g = filament_elem.get("used_g", "0")
                        try:
                            used_grams = float(used_g)
                            if used_grams > 0 and filament_id:
                                filaments.append(
                                    {
                                        "slot_id": int(filament_id),
                                        "type": filament_type,
                                        "color": filament_color,
                                        "tray_info_idx": tray_info_idx,
                                        "used_grams": round(used_grams, 1),
                                    }
                                )
                        except (ValueError, TypeError):
                            pass  # Skip filament entry with unparseable usage data

                filaments.sort(key=lambda x: x["slot_id"])

                # Enrich with nozzle mapping for dual-nozzle printers
                nozzle_mapping = extract_nozzle_mapping_from_3mf(zf)
                if nozzle_mapping:
                    for filament in filaments:
                        filament["nozzle_id"] = nozzle_mapping.get(filament["slot_id"])
        except Exception as e:
            logger.warning("Failed to parse filament requirements: %s", e)
            return None

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

    def _match_filaments_to_slots(self, required: list[dict], loaded: list[dict]) -> list[int] | None:
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
            req_nozzle_id = req.get("nozzle_id")
            if req_nozzle_id is not None:
                available = [f for f in available if f.get("extruder_id") == req_nozzle_id]

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

    def _is_printer_idle(self, printer_id: int) -> bool:
        """Check if a printer is connected and idle."""
        if not printer_manager.is_connected(printer_id):
            logger.debug("Printer %d: not connected", printer_id)
            return False

        state = printer_manager.get_status(printer_id)
        if not state:
            logger.debug("Printer %d: no status available", printer_id)
            return False

        # IDLE = ready for next print
        # FINISH/FAILED = ready only if user confirmed plate is cleared
        idle = state.state == "IDLE" or (
            state.state in ("FINISH", "FAILED") and printer_manager.is_plate_cleared(printer_id)
        )
        if not idle:
            logger.debug(
                "Printer %d: not idle — state=%s, plate_cleared=%s",
                printer_id,
                state.state,
                printer_manager.is_plate_cleared(printer_id),
            )
        return idle

    async def _get_bool_setting(self, db: AsyncSession, key: str, default: bool = False) -> bool:
        """Read a boolean setting from the database."""
        result = await db.execute(select(Settings).where(Settings.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            return setting.value.lower() == "true"
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

    async def _check_auto_drying(self, db: AsyncSession, queue_items: list[PrintQueueItem], busy_printers: set[int]):
        """Start drying on idle printers based on humidity.

        Two modes (can both be enabled):
        - queue_drying_enabled: Dry between scheduled queue prints
        - ambient_drying_enabled: Dry any idle printer when humidity is high, regardless of queue
        """
        queue_drying_enabled = await self._get_bool_setting(db, "queue_drying_enabled")
        ambient_drying_enabled = await self._get_bool_setting(db, "ambient_drying_enabled")
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
        if not ambient_drying_enabled and not printers_with_scheduled:
            for pid in list(self._drying_in_progress):
                logger.info("Auto-drying: printer %d — stopping, no scheduled prints in queue", pid)
                await self._stop_drying(pid)
            return

        # Get humidity threshold
        result = await db.execute(select(Settings).where(Settings.key == "ams_humidity_fair"))
        setting = result.scalar_one_or_none()
        humidity_threshold = int(setting.value) if setting else 60

        # Get drying presets
        presets = await self._get_drying_presets(db)

        # Determine if drying should be skipped for printers with pending items
        block_for_drying = await self._get_bool_setting(db, "queue_drying_block")

        # Get all active printers
        all_printers = await db.execute(select(Printer).where(Printer.is_active.is_(True)))
        for printer in all_printers.scalars():
            pid = printer.id
            if pid in busy_printers:
                logger.debug("Auto-drying: printer %d skipped — busy", pid)
                continue
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
            if not self._is_printer_idle(pid):
                logger.debug("Auto-drying: printer %d skipped — not idle", pid)
                continue

            # Check if this printer supports drying
            state = printer_manager.get_status(pid)
            if not state:
                logger.debug("Auto-drying: printer %d skipped — no state", pid)
                continue
            model = printer_manager.get_model(pid)
            firmware = state.firmware_version
            if not supports_drying(model, firmware):
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
                # Already drying — check if humidity dropped below threshold (with minimum drying time)
                if dry_time > 0:
                    if pid not in self._drying_in_progress:
                        # Drying we didn't start (manual or from before restart) — track but don't stop
                        self._drying_in_progress[pid] = time.monotonic()
                    started_at = self._drying_in_progress[pid]
                    elapsed = time.monotonic() - started_at
                    if humidity is not None and humidity <= humidity_threshold and elapsed >= self._min_drying_seconds:
                        logger.info(
                            "Auto-drying: printer %d AMS %d — humidity %d%% <= threshold %d%% after %dm, stopping drying",
                            pid,
                            ams_id,
                            humidity,
                            humidity_threshold,
                            int(elapsed / 60),
                        )
                        printer_manager.send_drying_command(pid, ams_id, temp=0, duration=0, mode=0)
                    else:
                        logger.debug(
                            "Auto-drying: printer %d AMS %d — drying (%dm left, humidity %s%%, elapsed %dm/%dm min)",
                            pid,
                            ams_id,
                            dry_time,
                            humidity,
                            int(elapsed / 60),
                            self._min_drying_seconds // 60,
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
                trays = ams_data.get("tray", [])
                params = self._get_conservative_drying_params(trays, module_type, presets)
                if not params:
                    logger.debug(
                        "Auto-drying: printer %d AMS %d skipped — no drying-eligible filaments in trays", pid, ams_id
                    )
                    continue

                temp, duration_hours, filament_type = params

                # Start drying
                logger.info(
                    "Auto-drying: printer %d AMS %d — humidity %d%% > threshold %d%%, "
                    "starting %s drying at %d°C for %dh",
                    pid,
                    ams_id,
                    humidity,
                    humidity_threshold,
                    filament_type,
                    temp,
                    duration_hours,
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

    async def _get_smart_plug(self, db: AsyncSession, printer_id: int) -> SmartPlug | None:
        """Get the smart plug associated with a printer."""
        result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
        return result.scalar_one_or_none()

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
        """Check if the previous print on this printer succeeded."""
        # Find the most recent completed queue item for this printer
        result = await db.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.printer_id == item.printer_id)
            .where(PrintQueueItem.id != item.id)
            .where(PrintQueueItem.status.in_(["completed", "failed", "skipped", "aborted"]))
            .order_by(PrintQueueItem.completed_at.desc())
            .limit(1)
        )
        prev_item = result.scalar_one_or_none()

        # If no previous item, assume success (first in queue)
        if not prev_item:
            return True

        return prev_item.status == "completed"

    async def _power_off_if_needed(self, db: AsyncSession, item: PrintQueueItem):
        """Power off printer if auto_off_after is enabled (waits for cooldown)."""
        if not item.auto_off_after:
            return

        plug = await self._get_smart_plug(db, item.printer_id)
        if plug and plug.enabled:
            logger.info("Auto-off: Waiting for printer %s to cool down before power off...", item.printer_id)
            # Wait for cooldown (up to 10 minutes)
            await printer_manager.wait_for_cooldown(item.printer_id, target_temp=50.0, timeout=600)
            logger.info("Auto-off: Powering off printer %s", item.printer_id)
            service = await smart_plug_manager.get_service_for_plug(plug, db)
            await service.turn_off(plug)

    async def _get_job_name(self, db: AsyncSession, item: PrintQueueItem) -> str:
        """Get a human-readable name for a queue item."""
        if item.archive_id:
            result = await db.execute(select(PrintArchive).where(PrintArchive.id == item.archive_id))
            archive = result.scalar_one_or_none()
            if archive:
                return archive.filename.replace(".gcode.3mf", "").replace(".3mf", "")
        if item.library_file_id:
            result = await db.execute(select(LibraryFile).where(LibraryFile.id == item.library_file_id))
            library_file = result.scalar_one_or_none()
            if library_file:
                return library_file.filename.replace(".gcode.3mf", "").replace(".3mf", "")
        return f"Job #{item.id}"

    async def _get_printer(self, db: AsyncSession, printer_id: int) -> Printer | None:
        """Get printer by ID."""
        result = await db.execute(select(Printer).where(Printer.id == printer_id))
        return result.scalar_one_or_none()

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

        # Determine source: archive or library file
        archive = None
        library_file = None
        file_path = None
        filename = None

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
            result = await db.execute(select(LibraryFile).where(LibraryFile.id == item.library_file_id))
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
            try:
                from backend.app.services.archive import ArchiveService

                archive_service = ArchiveService(db)
                archive = await archive_service.archive_print(
                    printer_id=item.printer_id,
                    source_file=file_path,
                    original_filename=filename,
                )
                if archive:
                    item.archive_id = archive.id
                    await db.flush()
                    logger.info(
                        "Queue item %s: Created archive %s from library file %s",
                        item.id,
                        archive.id,
                        item.library_file_id,
                    )
            except Exception as e:
                logger.warning("Queue item %s: Failed to create archive from library file: %s", item.id, e)

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

        # Upload file to printer via FTP
        # Use a clean filename to avoid issues with double extensions like .gcode.3mf
        base_name = filename
        if base_name.endswith(".gcode.3mf"):
            base_name = base_name[:-10]  # Remove .gcode.3mf
        elif base_name.endswith(".3mf"):
            base_name = base_name[:-4]  # Remove .3mf
        remote_filename = f"{base_name}.3mf"
        # Upload to root directory (not /cache/) - the start_print command references
        # files by name only (ftp://{filename}), so they must be in the root
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
                )
        except Exception as e:
            uploaded = False
            logger.error("Queue item %s: FTP error: %s (type: %s)", item.id, e, type(e).__name__)

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

            register_expected_print(item.printer_id, remote_filename, archive.id, ams_mapping=ams_mapping)

        # IMPORTANT: Set status to "printing" BEFORE sending the print command.
        # This prevents phantom reprints if the backend crashes/restarts after the
        # print command is sent but before the status update is committed.
        # If we crash after this commit but before start_print(), the item will be
        # in "printing" status without actually printing - but that's safer than
        # accidentally reprinting the same file hours later.
        item.status = "printing"
        item.started_at = datetime.now(timezone.utc)
        await db.commit()

        # Consume the plate-cleared flag now that we're starting a print
        printer_manager.consume_plate_cleared(item.printer_id)
        logger.info("Queue item %s: Status set to 'printing', sending print command...", item.id)

        # Start the print with AMS mapping, plate_id and print options
        started = printer_manager.start_print(
            item.printer_id,
            remote_filename,
            plate_id=item.plate_id or 1,
            ams_mapping=ams_mapping,
            bed_levelling=item.bed_levelling,
            flow_cali=item.flow_cali,
            vibration_cali=item.vibration_cali,
            layer_inspect=item.layer_inspect,
            timelapse=item.timelapse,
            use_ams=item.use_ams,
        )

        if started:
            logger.info("Queue item %s: Print started successfully - %s", item.id, filename)

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

            await self._power_off_if_needed(db, item)


# Global scheduler instance
scheduler = PrintScheduler()
