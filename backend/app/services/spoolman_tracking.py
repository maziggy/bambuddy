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


def _resolve_spool_tag(tray_info: dict) -> str:
    """Get the best spool identifier from tray info (prefer tray_uuid over tag_uid).

    Returns empty string if no usable identifier is found.
    """
    tray_uuid = tray_info.get("tray_uuid", "")
    tag_uid = tray_info.get("tag_uid", "")
    if tray_uuid and tray_uuid != _ZERO_UUID:
        return tray_uuid
    return tag_uid


def _resolve_global_tray_id(slot_id: int, slot_to_tray: list | None) -> int:
    """Map a 1-based slot_id to a global_tray_id using optional custom mapping.

    Default mapping: slot 1 -> tray 0, slot 2 -> tray 1, etc.
    Custom mapping (from print queue): slot_to_tray[slot_id - 1] overrides default.
    A value of -1 in custom mapping means unmapped (uses default).
    """
    global_tray_id = slot_id - 1
    if slot_to_tray and slot_id <= len(slot_to_tray):
        mapped_tray = slot_to_tray[slot_id - 1]
        if mapped_tray >= 0:
            global_tray_id = mapped_tray
    return global_tray_id


def build_ams_tray_lookup(raw_data: dict) -> dict[int, dict]:
    """Build lookup of global_tray_id -> tray info from printer state.

    Returns: {0: {"tray_uuid": "...", "tag_uid": "...", "tray_type": "..."}, ...}
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


async def store_print_data(printer_id: int, archive_id: int, file_path: str, db, printer_manager):
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
        ams_trays = build_ams_tray_lookup(state.raw_data)

    # Get custom slot-to-tray mapping from queue item (if this is a queued print)
    slot_to_tray = None
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


async def cleanup_tracking(printer_id: int, archive_id: int, db):
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
        logger.debug(f"[SPOOLMAN] No tracking data to clean up for printer={printer_id}, archive={archive_id}")
        return

    # Try to report partial usage before cleanup
    try:
        await _report_partial_usage(printer_id, tracking)
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
                client = await init_spoolman_client(spoolman_url)

    if not client or not await client.health_check():
        return None

    return client


async def _report_spool_usage_for_slots(
    client,
    filament_usage_items: list[tuple[int, float]],
    ams_trays: dict[int, dict],
    slot_to_tray: list | None,
    method_label: str,
) -> int:
    """Report usage to Spoolman for a list of (slot_id, grams) pairs.

    Returns number of spools successfully updated.
    """
    spools_updated = 0
    for slot_id, grams_used in filament_usage_items:
        if grams_used <= 0:
            continue

        global_tray_id = _resolve_global_tray_id(slot_id, slot_to_tray)
        tray_info = ams_trays.get(global_tray_id)
        if not tray_info:
            logger.debug(f"[SPOOLMAN] Slot {slot_id}: no tray at global_tray_id {global_tray_id}")
            continue

        spool_tag = _resolve_spool_tag(tray_info)
        if not spool_tag:
            logger.debug(f"[SPOOLMAN] Slot {slot_id}: no identifier for tray {global_tray_id}")
            continue

        spool = await client.find_spool_by_tag(spool_tag)
        if not spool:
            logger.debug(f"[SPOOLMAN] Slot {slot_id}: no spool for tag {spool_tag[:16]}...")
            continue

        result = await client.use_spool(spool["id"], grams_used)
        if result:
            logger.info(f"[SPOOLMAN] {method_label}: slot {slot_id}: {grams_used}g -> spool {spool['id']}")
            spools_updated += 1

    return spools_updated


async def _report_partial_usage(printer_id: int, tracking):
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

    # Get current printer state for layer progress
    state = printer_manager.get_status(printer_id)
    if not state:
        logger.debug("[SPOOLMAN] No printer state available for partial usage")
        return

    current_layer = state.layer_num
    total_layers = state.total_layers

    if not current_layer or current_layer <= 0:
        logger.debug("[SPOOLMAN] No progress to report (layer 0 or unknown)")
        return

    logger.info(f"[SPOOLMAN] Reporting partial usage at layer {current_layer}/{total_layers or '?'}")

    # Get tracking data
    layer_usage = tracking.layer_usage
    filament_properties = tracking.filament_properties or {}
    filament_usage = tracking.filament_usage or []
    ams_trays = {int(k): v for k, v in (tracking.ams_trays or {}).items()}
    slot_to_tray = tracking.slot_to_tray

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
            logger.info(f"[SPOOLMAN] Using G-code parsed data for layer {current_layer}")

            # Build (slot_id, grams) list using Spoolman densities with 3MF fallback
            usage_items = []
            for filament_id, mm_used in usage_mm.items():
                slot_id = filament_id + 1  # filament_id is 0-based, slot_id is 1-based

                # Get density from Spoolman (most accurate), fall back to 3MF, then PLA default
                global_tray_id = _resolve_global_tray_id(slot_id, slot_to_tray)
                tray_info = ams_trays.get(global_tray_id)
                density = None
                diameter = 1.75

                if tray_info:
                    spool_tag = _resolve_spool_tag(tray_info)
                    if spool_tag:
                        spool = await client.find_spool_by_tag(spool_tag)
                        if spool:
                            filament_data = spool.get("filament", {})
                            density = filament_data.get("density")
                            diameter = filament_data.get("diameter", 1.75)

                if not density:
                    props = filament_properties.get(str(slot_id), filament_properties.get(slot_id, {}))
                    density = props.get("density", 1.24)
                    logger.debug(f"[SPOOLMAN] Using fallback density {density} for slot {slot_id}")

                grams_used = round(mm_to_grams(mm_used, diameter, density), 2)
                usage_items.append((slot_id, grams_used))

            spools_updated = await _report_spool_usage_for_slots(
                client, usage_items, ams_trays, slot_to_tray, "Partial (G-code)"
            )
            if spools_updated > 0:
                logger.info(f"[SPOOLMAN] Reported partial usage to {spools_updated} spool(s) using G-code data")
            return

    # Fallback: linear interpolation (if no G-code data available)
    if not total_layers or total_layers <= 0:
        logger.debug(f"[SPOOLMAN] Cannot use linear fallback: total_layers={total_layers}")
        return

    progress_ratio = min(current_layer / total_layers, 1.0)
    logger.info(f"[SPOOLMAN] Falling back to linear interpolation ({progress_ratio:.1%})")

    usage_items = []
    for usage in filament_usage:
        slot_id = usage.get("slot_id", 0)
        total_used_g = usage.get("used_g", 0)
        if total_used_g > 0:
            partial_used_g = round(total_used_g * progress_ratio, 2)
            usage_items.append((slot_id, partial_used_g))

    spools_updated = await _report_spool_usage_for_slots(
        client, usage_items, ams_trays, slot_to_tray, "Partial (linear)"
    )
    if spools_updated > 0:
        logger.info(f"[SPOOLMAN] Reported partial usage to {spools_updated} spool(s) using linear interpolation")


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
            logger.info(f"[SPOOLMAN] No tracking data for print (printer={printer_id}, archive={archive_id})")
            return

        filament_usage = tracking.filament_usage or []
        ams_trays = {int(k): v for k, v in (tracking.ams_trays or {}).items()}
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

        client = await _get_spoolman_client_with_fallback()
        if not client:
            logger.warning("[SPOOLMAN] Not reachable for usage reporting")
            return

        logger.info(f"[SPOOLMAN] Reporting per-filament usage for archive {archive_id}")

        usage_items = [(u.get("slot_id", 0), u.get("used_g", 0)) for u in filament_usage]
        spools_updated = await _report_spool_usage_for_slots(
            client, usage_items, ams_trays, slot_to_tray, f"Archive {archive_id}"
        )

        if spools_updated == 0:
            logger.info(f"[SPOOLMAN] Archive {archive_id}: no spools updated")
        else:
            logger.info(f"[SPOOLMAN] Archive {archive_id}: updated {spools_updated} spool(s)")
