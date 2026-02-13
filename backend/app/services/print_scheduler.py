"""Print scheduler service - processes the print queue."""

import asyncio
import json
import logging
import zipfile
from datetime import datetime
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
from backend.app.models.smart_plug import SmartPlug
from backend.app.services.bambu_ftp import delete_file_async, get_ftp_retry_settings, upload_file_async, with_ftp_retry
from backend.app.services.notification_service import notification_service
from backend.app.services.printer_manager import printer_manager
from backend.app.services.smart_plug_manager import smart_plug_manager
from backend.app.utils.printer_models import normalize_printer_model

logger = logging.getLogger(__name__)


class PrintScheduler:
    """Background scheduler that processes the print queue."""

    def __init__(self):
        self._running = False
        self._check_interval = 30  # seconds
        self._power_on_wait_time = 180  # seconds to wait for printer after power on (3 min)
        self._power_on_check_interval = 10  # seconds between connection checks

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
                return

            # Track busy printers to avoid assigning multiple items to same printer
            busy_printers: set[int] = set()

            for item in items:
                # Check scheduled time first (scheduled_time is stored in UTC from ISO string)
                if item.scheduled_time and item.scheduled_time > datetime.utcnow():
                    continue

                # Skip items that require manual start
                if item.manual_start:
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
                        busy_printers.add(item.printer_id)
                        continue

                    # Check condition (previous print success)
                    if item.require_previous_success:
                        if not await self._check_previous_success(db, item):
                            item.status = "skipped"
                            item.error_message = "Previous print failed or was aborted"
                            item.completed_at = datetime.now()
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

                    printer_id, waiting_reason = await self._find_idle_printer_for_model(
                        db, item.target_model, busy_printers, required_types, item.target_location
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
                                item.completed_at = datetime.now()
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

    async def _find_idle_printer_for_model(
        self,
        db: AsyncSession,
        model: str,
        exclude_ids: set[int],
        required_filament_types: list[str] | None = None,
        target_location: str | None = None,
    ) -> tuple[int | None, str | None]:
        """Find an idle, connected printer matching the model with compatible filaments.

        Args:
            db: Database session
            model: Printer model to match (e.g., "X1C", "P1S")
            exclude_ids: Printer IDs to exclude (already busy)
            required_filament_types: Optional list of filament types needed (e.g., ["PLA", "PETG"])
                                     If provided, only printers with all required types loaded will match.
            target_location: Optional location filter. If provided, only printers in this location are considered.

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

        # Track reasons for skipping printers
        printers_busy = []
        printers_offline = []
        printers_missing_filament = []

        for printer in printers:
            if printer.id in exclude_ids:
                printers_busy.append(printer.name)
                continue

            is_connected = printer_manager.is_connected(printer.id)
            is_idle = self._is_printer_idle(printer.id) if is_connected else False

            if not is_connected:
                printers_offline.append(printer.name)
                continue

            if not is_idle:
                printers_busy.append(printer.name)
                continue

            # Validate filament compatibility if required types are specified
            if required_filament_types:
                missing = self._get_missing_filament_types(printer.id, required_filament_types)
                if missing:
                    printers_missing_filament.append((printer.name, missing))
                    logger.debug("Skipping printer %s (%s) - missing filaments: %s", printer.id, printer.name, missing)
                    continue

            # Found a matching printer - clear waiting reason
            return printer.id, None

        # Build waiting reason from what we found
        reasons = []
        if printers_missing_filament:
            # Filament mismatch is most actionable - show first
            names_and_missing = [f"{name} (needs {', '.join(missing)})" for name, missing in printers_missing_filament]
            reasons.append(f"Waiting for filament: {'; '.join(names_and_missing)}")
        if printers_busy:
            reasons.append(f"Busy: {', '.join(printers_busy)}")
        if printers_offline:
            reasons.append(f"Offline: {', '.join(printers_offline)}")

        return None, " | ".join(reasons) if reasons else f"No available {model} printers{location_suffix}"

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
        loaded_types: set[str] = set()

        # Check AMS units (stored in raw_data["ams"])
        ams_data = status.raw_data.get("ams", [])
        if ams_data:
            for ams_unit in ams_data:
                for tray in ams_unit.get("tray", []):
                    tray_type = tray.get("tray_type")
                    if tray_type:
                        loaded_types.add(tray_type.upper())

        # Check external spool (virtual tray, stored in raw_data["vt_tray"])
        vt_tray = status.raw_data.get("vt_tray")
        if vt_tray:
            vt_type = vt_tray.get("tray_type")
            if vt_type:
                loaded_types.add(vt_type.upper())

        # Find which required types are missing (case-insensitive comparison)
        missing = []
        for req_type in required_types:
            if req_type.upper() not in loaded_types:
                missing.append(req_type)

        return missing

    async def _compute_ams_mapping_for_printer(
        self, db: AsyncSession, printer_id: int, item: PrintQueueItem
    ) -> list[int] | None:
        """Compute AMS mapping for a printer based on filament requirements.

        This is called for model-based queue items after a printer is assigned,
        to compute the correct AMS slot mapping for that specific printer's hardware.

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

        # Parse AMS units from raw_data
        ams_data = status.raw_data.get("ams", [])
        for ams_unit in ams_data:
            ams_id = ams_unit.get("id", 0)
            trays = ams_unit.get("tray", [])
            is_ht = len(trays) == 1  # AMS-HT has single tray

            for tray in trays:
                tray_type = tray.get("tray_type")
                if tray_type:
                    tray_id = tray.get("id", 0)
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
                        }
                    )

        # Check external spool (vt_tray)
        vt_tray = status.raw_data.get("vt_tray")
        if vt_tray and vt_tray.get("tray_type"):
            color = self._normalize_color(vt_tray.get("tray_color", ""))
            filaments.append(
                {
                    "type": vt_tray["tray_type"],
                    "color": color,
                    "tray_info_idx": vt_tray.get("tray_info_idx", ""),
                    "ams_id": -1,
                    "tray_id": 0,
                    "is_ht": False,
                    "is_external": True,
                    "global_tray_id": 254,
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
                    if f_type != req_type:
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
            return False

        state = printer_manager.get_status(printer_id)
        if not state:
            return False

        # IDLE = ready for next print
        # FINISH/FAILED = ready only if user confirmed plate is cleared
        return state.state == "IDLE" or (
            state.state in ("FINISH", "FAILED") and printer_manager.is_plate_cleared(printer_id)
        )

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
            item.completed_at = datetime.utcnow()
            await db.commit()
            logger.error("Queue item %s: Printer %s not found", item.id, item.printer_id)
            await self._power_off_if_needed(db, item)
            return

        # Check printer is connected
        if not printer_manager.is_connected(item.printer_id):
            item.status = "failed"
            item.error_message = "Printer not connected"
            item.completed_at = datetime.utcnow()
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
                item.completed_at = datetime.utcnow()
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
                item.completed_at = datetime.utcnow()
                await db.commit()
                logger.error("Queue item %s: Library file %s not found", item.id, item.library_file_id)
                await self._power_off_if_needed(db, item)
                return
            # Library files store absolute paths
            from pathlib import Path

            lib_path = Path(library_file.file_path)
            file_path = lib_path if lib_path.is_absolute() else settings.base_dir / library_file.file_path
            filename = library_file.filename

        else:
            # Neither archive nor library file specified
            item.status = "failed"
            item.error_message = "No source file specified"
            item.completed_at = datetime.utcnow()
            await db.commit()
            logger.error("Queue item %s: No archive_id or library_file_id specified", item.id)
            await self._power_off_if_needed(db, item)
            return

        # Check file exists on disk
        if not file_path.exists():
            item.status = "failed"
            item.error_message = "Source file not found on disk"
            item.completed_at = datetime.utcnow()
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
            item.completed_at = datetime.utcnow()
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

        # Register as expected print so we don't create a duplicate archive
        # Only applicable for archive-based prints
        if archive:
            from backend.app.main import register_expected_print

            register_expected_print(item.printer_id, remote_filename, archive.id)

        # Parse AMS mapping if stored
        ams_mapping = None
        if item.ams_mapping:
            try:
                ams_mapping = json.loads(item.ams_mapping)
            except json.JSONDecodeError:
                logger.warning("Queue item %s: Invalid AMS mapping JSON, ignoring", item.id)

        # IMPORTANT: Set status to "printing" BEFORE sending the print command.
        # This prevents phantom reprints if the backend crashes/restarts after the
        # print command is sent but before the status update is committed.
        # If we crash after this commit but before start_print(), the item will be
        # in "printing" status without actually printing - but that's safer than
        # accidentally reprinting the same file hours later.
        item.status = "printing"
        item.started_at = datetime.utcnow()
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
            # Print command failed - revert status
            item.status = "failed"
            item.error_message = "Failed to send print command to printer"
            item.completed_at = datetime.utcnow()
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
