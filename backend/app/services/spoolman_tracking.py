"""Spoolman per-filament usage tracking for active prints.

Captures AMS tray state and G-code data at print start, then reports
per-filament usage to the correct Spoolman spools at print completion.
Supports accurate partial usage reporting for failed/cancelled prints.
"""

import json
import logging

from sqlalchemy import delete, select

from backend.app.core.config import settings as app_settings
from backend.app.core.database import async_session
from backend.app.services.spoolman import get_spoolman_client, init_spoolman_client

logger = logging.getLogger(__name__)

# Zero UUID used by Bambu printers for empty/unset tray_uuid
_ZERO_UUID = "00000000000000000000000000000000"
_ZERO_TAG_UID = "0000000000000000"


def _is_non_zero_identifier(value: str) -> bool:
    """Return True when identifier is non-empty and not all zeros."""
    if not value:
        return False
    return set(value) != {"0"}


def _to_fixed_hex(value: int, width: int) -> str:
    """Mirror frontend toFixedHex(): uppercase, zero-padded, fixed width."""
    safe = max(0, int(value))
    return format(safe, "X").zfill(width)[-width:]


def _hash_serial_to_hex32(serial: str) -> str:
    """Mirror frontend hashSerialToHex32() exactly (32-bit FNV-1a)."""
    input_str = (serial or "").strip().upper()
    hash_value = 0x811C9DC5
    for char in input_str:
        hash_value ^= ord(char)
        hash_value = (hash_value * 0x01000193) & 0xFFFFFFFF
    return format(hash_value, "X").zfill(8)


def _global_tray_id_to_ams_slot(global_tray_id: int) -> tuple[int, int]:
    """Convert global tray id to (ams_id, tray_id) tuple for fallback tag generation."""
    # External spool slots use IDs 254/255 and map to ams_id=255 tray_id=0/1.
    if global_tray_id >= 254:
        return 255, max(0, global_tray_id - 254)
    # AMS-HT units are addressed by ams_id directly and have a single tray.
    if global_tray_id >= 128:
        return global_tray_id, 0
    # Standard AMS units: four trays each.
    return global_tray_id // 4, global_tray_id % 4


def _get_fallback_spool_tag(printer_serial: str, global_tray_id: int) -> str:
    """Mirror frontend getFallbackSpoolTag(serial, amsId, trayId) exactly."""
    if not printer_serial:
        return ""
    ams_id, tray_id = _global_tray_id_to_ams_slot(global_tray_id)
    return f"{_hash_serial_to_hex32(printer_serial)}{_to_fixed_hex(ams_id, 4)}{_to_fixed_hex(tray_id, 4)}"


def _resolve_spool_tag(tray_info: dict, printer_serial: str = "", global_tray_id: int | None = None) -> str:
    """Get the best spool identifier from tray info (prefer tray_uuid over tag_uid).

    Returns empty string if no usable identifier is found.
    """
    tray_uuid = str(tray_info.get("tray_uuid", "") or "")
    tag_uid = str(tray_info.get("tag_uid", "") or "")

    if tray_uuid and tray_uuid != _ZERO_UUID and _is_non_zero_identifier(tray_uuid):
        return tray_uuid
    if tag_uid and tag_uid != _ZERO_TAG_UID and _is_non_zero_identifier(tag_uid):
        return tag_uid
    if global_tray_id is not None:
        return _get_fallback_spool_tag(printer_serial, global_tray_id)
    return ""


async def _get_printer_serial(printer_id: int) -> str:
    """Get printer serial for deterministic fallback tag generation."""
    from backend.app.models.printer import Printer
    from backend.app.services.printer_manager import printer_manager

    printer_info = printer_manager.get_printer(printer_id)
    if printer_info and printer_info.serial_number:
        return printer_info.serial_number

    async with async_session() as db:
        result = await db.execute(select(Printer.serial_number).where(Printer.id == printer_id))
        serial_number = result.scalar_one_or_none()
        return serial_number or ""


def _resolve_global_tray_id(slot_id: int, slot_to_tray: list | None, ams_trays: dict | None = None) -> int:
    """Map a 1-based slot_id to a global_tray_id using optional custom mapping.

    Custom mapping: slot_to_tray[slot_id - 1] is used when >= 0.
    Position-based default: uses sorted ams_trays keys so external spools (ID 254/255)
    naturally follow standard AMS trays, matching the slicer's slot numbering.
    A value of -1 in the custom mapping means unmapped (uses position-based default).
    Final fallback: slot_id - 1 (legacy, works for pure AMS without external spools).
    """
    if slot_to_tray and slot_id <= len(slot_to_tray):
        mapped_tray = slot_to_tray[slot_id - 1]
        if mapped_tray >= 0:
            return mapped_tray
    # Position-based default: sort available tray IDs so external spools (254/255)
    # come after standard AMS trays, matching the slicer's slot assignment order.
    if ams_trays:
        sorted_tray_ids = sorted(ams_trays.keys())
        if slot_id <= len(sorted_tray_ids):
            return sorted_tray_ids[slot_id - 1]
    return slot_id - 1


def build_ams_tray_lookup(raw_data: dict) -> dict[int, dict]:
    """Build lookup of global_tray_id -> tray info from printer state.

    Returns: {0: {"tray_uuid": "...", "tag_uid": "...", "tray_type": "..."}, ...}
    """
    lookup = {}
    ams_data = raw_data.get("ams", [])
    for ams_unit in ams_data:
        ams_id = int(ams_unit.get("id", 0))
        for tray in ams_unit.get("tray", []):
            tray_id = int(tray.get("id", 0))
            # AMS-HT units have IDs starting at 128 with a single tray
            global_tray_id = ams_id if ams_id >= 128 else ams_id * 4 + tray_id
            lookup[global_tray_id] = {
                "tray_uuid": tray.get("tray_uuid", ""),
                "tag_uid": tray.get("tag_uid", ""),
                "tray_type": tray.get("tray_type", ""),
            }

    # External spool(s) (vt_tray is a list, global_tray_id from each entry's "id")
    for vt in raw_data.get("vt_tray") or []:
        if vt.get("tray_type"):
            tray_id = int(vt.get("id", 254))
            lookup[tray_id] = {
                "tray_uuid": vt.get("tray_uuid", ""),
                "tag_uid": vt.get("tag_uid", ""),
                "tray_type": vt.get("tray_type", ""),
            }

    return lookup


async def store_print_data(
    printer_id: int,
    archive_id: int,
    file_path: str,
    db,
    printer_manager,
    ams_mapping: list[int] | None = None,
):
    """Store Spoolman tracking data at print start (persisted to database).

    Only stores data when Spoolman is enabled and AMS weight sync is disabled
    (i.e., we're using per-usage tracking instead of AMS percentage estimates).
    """
    from backend.app.api.routes.settings import get_setting
    from backend.app.models.active_print_spoolman import ActivePrintSpoolman
    from backend.app.models.print_queue import PrintQueueItem
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
    disable_weight_sync_str = await get_setting(db, "spoolman_disable_weight_sync")
    disable_weight_sync = disable_weight_sync_str and disable_weight_sync_str.lower() == "true"
    if not disable_weight_sync:
        logger.debug("[SPOOLMAN] Weight sync enabled, skipping per-usage tracking data storage")
        return

    # Get 3MF file path
    full_path = app_settings.base_dir / file_path
    if not full_path.exists():
        logger.debug("[SPOOLMAN] 3MF file not found: %s", full_path)
        return

    # Extract per-filament usage from 3MF (total usage per slot)
    filament_usage = extract_filament_usage_from_3mf(full_path)
    if not filament_usage:
        logger.debug("[SPOOLMAN] No filament usage data in 3MF for archive %s", archive_id)
        return

    # Get current AMS tray state
    state = printer_manager.get_status(printer_id)
    ams_trays = {}
    if state and state.raw_data:
        ams_trays = build_ams_tray_lookup(state.raw_data)

    # Prefer the explicit mapping captured from the print command, then fall back
    # to any queue mapping stored for scheduled/reprint jobs.
    slot_to_tray = ams_mapping if ams_mapping is not None else None
    if not slot_to_tray:
        queue_result = await db.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.archive_id == archive_id)
            .where(PrintQueueItem.status == "printing")
        )
        queue_item = queue_result.scalar_one_or_none()
        if queue_item and queue_item.ams_mapping:
            try:
                slot_to_tray = json.loads(queue_item.ams_mapping)
            except json.JSONDecodeError:
                pass  # Ignore malformed AMS mapping; fall back to default slot assignment

    # Parse G-code for per-layer filament usage (for accurate partial usage tracking)
    layer_usage = extract_layer_filament_usage_from_3mf(full_path)
    layer_usage_json = None
    if layer_usage:
        # Convert int keys to string for JSON serialization
        layer_usage_json = {str(k): v for k, v in layer_usage.items()}
        logger.debug("[SPOOLMAN] Parsed %s layers from G-code", len(layer_usage))

    # Extract filament properties (density, diameter) for mm -> grams conversion
    filament_properties = extract_filament_properties_from_3mf(full_path)

    # Delete any existing row for this printer/archive (shouldn't exist, but just in case)
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

    logger.info("[SPOOLMAN] Stored tracking data for print: printer=%s, archive=%s", printer_id, archive_id)
    logger.debug("[SPOOLMAN] Filament usage: %s", filament_usage)
    logger.debug("[SPOOLMAN] AMS trays: %s", list(ams_trays.keys()))
    if slot_to_tray:
        logger.debug("[SPOOLMAN] Custom slot mapping: %s", slot_to_tray)
    if layer_usage_json:
        logger.debug("[SPOOLMAN] Layer usage data available for partial tracking")


async def cleanup_tracking(
    printer_id: int,
    archive_id: int,
    db,
    last_layer_num: int | None = None,
    last_progress: int | None = None,
):
    """Report partial usage and clean up Spoolman tracking data for failed/aborted prints."""
    from backend.app.models.active_print_spoolman import ActivePrintSpoolman

    # Get tracking data first (needed for partial usage reporting)
    result = await db.execute(
        select(ActivePrintSpoolman)
        .where(ActivePrintSpoolman.printer_id == printer_id)
        .where(ActivePrintSpoolman.archive_id == archive_id)
    )
    tracking = result.scalar_one_or_none()

    if not tracking:
        logger.debug("[SPOOLMAN] No tracking data to clean up for printer=%s, archive=%s", printer_id, archive_id)
        return

    # Try to report partial usage before cleanup
    try:
        await _report_partial_usage(
            printer_id,
            tracking,
            last_layer_num=last_layer_num,
            last_progress=last_progress,
        )
    except Exception as e:
        logger.warning("[SPOOLMAN] Partial usage report failed: %s", e)

    # Delete tracking data
    await db.execute(
        delete(ActivePrintSpoolman)
        .where(ActivePrintSpoolman.printer_id == printer_id)
        .where(ActivePrintSpoolman.archive_id == archive_id)
    )
    await db.commit()
    logger.debug("[SPOOLMAN] Cleaned up tracking data for printer=%s, archive=%s", printer_id, archive_id)


async def _get_spoolman_client_with_fallback():
    """Get Spoolman client, initializing from settings if needed.

    Returns (client, is_healthy) tuple. Client may be None.
    """
    client = await get_spoolman_client()
    if not client:
        async with async_session() as db:
            from backend.app.api.routes.settings import get_setting

            spoolman_url = await get_setting(db, "spoolman_url")
            if spoolman_url:
                try:
                    client = await init_spoolman_client(spoolman_url)
                except ValueError as exc:
                    logger.warning("Spoolman URL %r rejected by SSRF guard: %s", spoolman_url, exc)
                    return None

    if not client:
        return None
    if not await client.health_check():
        logger.warning("Spoolman health check failed; skipping usage reporting")
        return None

    return client


async def _report_spool_usage_for_slots(
    client,
    filament_usage_items: list[tuple[int, float]],
    ams_trays: dict[int, dict],
    slot_to_tray: list | None,
    method_label: str,
    printer_serial: str = "",
) -> int:
    """Report usage to Spoolman for a list of (slot_id, grams) pairs.

    Returns number of spools successfully updated.
    """
    spools_updated = 0
    for slot_id, grams_used in filament_usage_items:
        if grams_used <= 0:
            continue

        global_tray_id = _resolve_global_tray_id(slot_id, slot_to_tray, ams_trays)
        tray_info = ams_trays.get(global_tray_id)
        if not tray_info:
            logger.debug("[SPOOLMAN] Slot %s: no tray at global_tray_id %s", slot_id, global_tray_id)
            continue

        is_external = global_tray_id >= 254
        tray_type = tray_info.get("tray_type", "")
        logger.debug(
            "[SPOOLMAN] Slot %s resolved to global_tray_id %s (tray_type=%s, external=%s)",
            slot_id,
            global_tray_id,
            tray_type or "unknown",
            is_external,
        )

        spool_tag = _resolve_spool_tag(tray_info, printer_serial, global_tray_id)
        if not spool_tag:
            logger.debug("[SPOOLMAN] Slot %s: no identifier for tray %s", slot_id, global_tray_id)
            continue

        spool = await client.find_spool_by_tag(spool_tag)
        if not spool:
            logger.debug("[SPOOLMAN] Slot %s: no spool for tag %s...", slot_id, spool_tag[:16])
            continue

        result = await client.use_spool(spool["id"], grams_used)
        if result:
            logger.info("[SPOOLMAN] %s: slot %s: %sg -> spool %s", method_label, slot_id, grams_used, spool["id"])
            spools_updated += 1

    return spools_updated


async def _report_partial_usage(
    printer_id: int,
    tracking,
    last_layer_num: int | None = None,
    last_progress: int | None = None,
):
    """Report partial filament usage based on actual G-code layer data.

    Uses per-layer cumulative extrusion from G-code parsing for accurate
    multi-material tracking. Falls back to linear interpolation if G-code
    data is unavailable.
    """
    from backend.app.services.printer_manager import printer_manager
    from backend.app.utils.threemf_tools import get_cumulative_usage_at_layer, mm_to_grams

    async with async_session() as db:
        from backend.app.api.routes.settings import get_setting

        # Check if partial usage reporting is enabled (default: true)
        report_partial = await get_setting(db, "spoolman_report_partial_usage")
        if report_partial and report_partial.lower() == "false":
            logger.debug("[SPOOLMAN] Partial usage reporting disabled by setting")
            return

        # Check if Spoolman is enabled
        spoolman_enabled = await get_setting(db, "spoolman_enabled")
        if not spoolman_enabled or spoolman_enabled.lower() != "true":
            return

    # Get current printer state for layer progress.
    # On failed/aborted prints the firmware may already reset to IDLE with layer=0,
    # so we fall back to completion-time hints captured from MQTT.
    state = printer_manager.get_status(printer_id)
    current_layer = state.layer_num if state else None
    total_layers = state.total_layers if state else None

    if (not current_layer or current_layer <= 0) and last_layer_num and last_layer_num > 0:
        current_layer = last_layer_num
        logger.debug("[SPOOLMAN] Using captured last_layer_num=%s for partial usage", current_layer)

    progress_ratio_from_event = None
    if last_progress is not None:
        try:
            progress_ratio_from_event = min(max(float(last_progress), 0.0), 100.0) / 100.0
        except (TypeError, ValueError):
            progress_ratio_from_event = None

    if (not current_layer or current_layer <= 0) and progress_ratio_from_event and total_layers and total_layers > 0:
        current_layer = max(1, int(round(total_layers * progress_ratio_from_event)))
        logger.debug(
            "[SPOOLMAN] Estimated layer from last_progress=%s%% and total_layers=%s -> %s",
            last_progress,
            total_layers,
            current_layer,
        )

    if not current_layer or current_layer <= 0:
        logger.debug(
            "[SPOOLMAN] No progress to report (layer 0/unknown, last_layer_num=%s, last_progress=%s)",
            last_layer_num,
            last_progress,
        )
        return

    logger.info("[SPOOLMAN] Reporting partial usage at layer %s/%s", current_layer, total_layers or "?")

    # Get tracking data
    layer_usage = tracking.layer_usage
    filament_properties = tracking.filament_properties or {}
    filament_usage = tracking.filament_usage or []
    ams_trays = {int(k): v for k, v in (tracking.ams_trays or {}).items()}
    slot_to_tray = tracking.slot_to_tray
    printer_serial = await _get_printer_serial(printer_id)

    client = await _get_spoolman_client_with_fallback()
    if not client:
        logger.warning("[SPOOLMAN] Not reachable for partial usage reporting")
        return

    # Try to use accurate G-code parsed data
    if layer_usage:
        layer_usage_int = {
            int(layer): {int(fid): mm for fid, mm in filaments.items()} for layer, filaments in layer_usage.items()
        }
        usage_mm = get_cumulative_usage_at_layer(layer_usage_int, current_layer)

        if usage_mm:
            logger.info("[SPOOLMAN] Using G-code parsed data for layer %s", current_layer)

            # Build (slot_id, grams) list using Spoolman densities with 3MF fallback
            usage_items = []
            for filament_id, mm_used in usage_mm.items():
                slot_id = filament_id + 1  # filament_id is 0-based, slot_id is 1-based

                # Get density from Spoolman (most accurate), fall back to 3MF, then PLA default
                global_tray_id = _resolve_global_tray_id(slot_id, slot_to_tray, ams_trays)
                tray_info = ams_trays.get(global_tray_id)
                density = None
                diameter = 1.75

                if tray_info:
                    spool_tag = _resolve_spool_tag(tray_info, printer_serial, global_tray_id)
                    if spool_tag:
                        spool = await client.find_spool_by_tag(spool_tag)
                        if spool:
                            filament_data = spool.get("filament", {})
                            density = filament_data.get("density")
                            diameter = filament_data.get("diameter", 1.75)

                if not density:
                    props = filament_properties.get(str(slot_id), filament_properties.get(slot_id, {}))
                    density = props.get("density", 1.24)
                    logger.debug("[SPOOLMAN] Using fallback density %s for slot %s", density, slot_id)

                grams_used = round(mm_to_grams(mm_used, diameter, density), 2)
                usage_items.append((slot_id, grams_used))

            spools_updated = await _report_spool_usage_for_slots(
                client, usage_items, ams_trays, slot_to_tray, "Partial (G-code)", printer_serial
            )
            if spools_updated > 0:
                logger.info("[SPOOLMAN] Reported partial usage to %s spool(s) using G-code data", spools_updated)
            return

    # Fallback: linear interpolation (if no G-code data available)
    progress_ratio = None
    if total_layers and total_layers > 0:
        progress_ratio = min(current_layer / total_layers, 1.0)
    elif progress_ratio_from_event is not None:
        progress_ratio = progress_ratio_from_event

    if progress_ratio is None:
        logger.debug(
            "[SPOOLMAN] Cannot use linear fallback: total_layers=%s, last_progress=%s",
            total_layers,
            last_progress,
        )
        return

    logger.info("[SPOOLMAN] Falling back to linear interpolation (%s)", progress_ratio)

    usage_items = []
    for usage in filament_usage:
        slot_id = usage.get("slot_id", 0)
        total_used_g = usage.get("used_g", 0)
        if total_used_g > 0:
            partial_used_g = round(total_used_g * progress_ratio, 2)
            usage_items.append((slot_id, partial_used_g))

    spools_updated = await _report_spool_usage_for_slots(
        client, usage_items, ams_trays, slot_to_tray, "Partial (linear)", printer_serial
    )
    if spools_updated > 0:
        logger.info("[SPOOLMAN] Reported partial usage to %s spool(s) using linear interpolation", spools_updated)


async def report_usage(printer_id: int, archive_id: int):
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
            logger.info("[SPOOLMAN] No tracking data for print (printer=%s, archive=%s)", printer_id, archive_id)
            return

        filament_usage = tracking.filament_usage or []
        ams_trays = {int(k): v for k, v in (tracking.ams_trays or {}).items()}
        slot_to_tray = tracking.slot_to_tray
        printer_serial = await _get_printer_serial(printer_id)

        # Delete tracking row (we're done with it)
        await db.delete(tracking)
        await db.commit()

        if not filament_usage:
            logger.debug("[SPOOLMAN] No filament usage data for archive %s", archive_id)
            return

        # Check if Spoolman is enabled
        spoolman_enabled = await get_setting(db, "spoolman_enabled")
        if not spoolman_enabled or spoolman_enabled.lower() != "true":
            return

        client = await _get_spoolman_client_with_fallback()
        if not client:
            logger.warning("[SPOOLMAN] Not reachable for usage reporting")
            return

        logger.info("[SPOOLMAN] Reporting per-filament usage for archive %s", archive_id)

        usage_items = [(u.get("slot_id", 0), u.get("used_g", 0)) for u in filament_usage]
        spools_updated = await _report_spool_usage_for_slots(
            client, usage_items, ams_trays, slot_to_tray, f"Archive {archive_id}", printer_serial
        )

        if spools_updated == 0:
            logger.info("[SPOOLMAN] Archive %s: no spools updated", archive_id)
        else:
            logger.info("[SPOOLMAN] Archive %s: updated %s spool(s)", archive_id, spools_updated)
