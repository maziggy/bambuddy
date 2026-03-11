"""FilaMan per-filament consumption tracking for active prints.

Captures AMS tray state and G-code data at print start, then reports
per-filament consumption to the correct FilaMan spools at print completion
via POST /api/v1/spools/{id}/consumptions.
Supports accurate partial usage reporting for failed/cancelled prints.
"""

import json
import logging

from sqlalchemy import delete, select

from backend.app.core.config import settings as app_settings
from backend.app.core.database import async_session
from backend.app.services.filaman import close_filaman_client, get_filaman_client, init_filaman_client  # noqa: F401

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
    """Map a 1-based slot_id to a global_tray_id using optional custom mapping."""
    global_tray_id = slot_id - 1
    if slot_to_tray and slot_id <= len(slot_to_tray):
        mapped_tray = slot_to_tray[slot_id - 1]
        if mapped_tray >= 0:
            global_tray_id = mapped_tray
    return global_tray_id


def build_ams_tray_lookup(raw_data: dict) -> dict[int, dict]:
    """Build lookup of global_tray_id -> tray info from printer state."""
    lookup = {}
    ams_data = raw_data.get("ams", [])
    for ams_unit in ams_data:
        ams_id = ams_unit.get("id", 0)
        for tray in ams_unit.get("tray", []):
            tray_id = tray.get("id", 0)
            global_tray_id = ams_id if ams_id >= 128 else ams_id * 4 + tray_id
            lookup[global_tray_id] = {
                "tray_uuid": tray.get("tray_uuid", ""),
                "tag_uid": tray.get("tag_uid", ""),
                "tray_type": tray.get("tray_type", ""),
            }

    # External spool(s)
    for vt in raw_data.get("vt_tray") or []:
        if vt.get("tray_type"):
            tray_id = int(vt.get("id", 254))
            lookup[tray_id] = {
                "tray_uuid": vt.get("tray_uuid", ""),
                "tag_uid": vt.get("tag_uid", ""),
                "tray_type": vt.get("tray_type", ""),
            }

    return lookup


async def store_print_data(printer_id: int, archive_id: int, file_path: str, db, printer_manager):
    """Store FilaMan tracking data at print start (persisted to database).

    Only stores data when FilaMan is enabled and AMS weight sync is disabled
    (i.e., we're using per-usage tracking instead of AMS percentage estimates).
    """
    from backend.app.api.routes.settings import get_setting
    from backend.app.models.active_print_filaman import ActivePrintFilaMan
    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.utils.threemf_tools import (
        extract_filament_properties_from_3mf,
        extract_filament_usage_from_3mf,
        extract_layer_filament_usage_from_3mf,
    )

    # Check if FilaMan is enabled
    filaman_enabled = await get_setting(db, "filaman_enabled")
    if not filaman_enabled or filaman_enabled.lower() != "true":
        return

    # Only store tracking data if "Disable AMS Weight Sync" is enabled
    disable_weight_sync_str = await get_setting(db, "filaman_disable_weight_sync")
    disable_weight_sync = disable_weight_sync_str and disable_weight_sync_str.lower() == "true"
    if not disable_weight_sync:
        logger.debug("[FILAMAN] Weight sync enabled, skipping per-usage tracking data storage")
        return

    # Get 3MF file path
    full_path = app_settings.base_dir / file_path
    if not full_path.exists():
        logger.debug("[FILAMAN] 3MF file not found: %s", full_path)
        return

    # Extract per-filament usage from 3MF
    filament_usage = extract_filament_usage_from_3mf(full_path)
    if not filament_usage:
        logger.debug("[FILAMAN] No filament usage data in 3MF for archive %s", archive_id)
        return

    # Get current AMS tray state
    state = printer_manager.get_status(printer_id)
    ams_trays = {}
    if state and state.raw_data:
        ams_trays = build_ams_tray_lookup(state.raw_data)

    # Get custom slot-to-tray mapping from queue item (if queued print)
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

    # Parse G-code for per-layer filament usage
    layer_usage = extract_layer_filament_usage_from_3mf(full_path)
    layer_usage_json = None
    if layer_usage:
        layer_usage_json = {str(k): v for k, v in layer_usage.items()}
        logger.debug("[FILAMAN] Parsed %s layers from G-code", len(layer_usage))

    # Extract filament properties for mm -> grams conversion
    filament_properties = extract_filament_properties_from_3mf(full_path)

    # Delete any existing row for this printer/archive
    await db.execute(
        delete(ActivePrintFilaMan)
        .where(ActivePrintFilaMan.printer_id == printer_id)
        .where(ActivePrintFilaMan.archive_id == archive_id)
    )

    # Insert new tracking data
    tracking = ActivePrintFilaMan(
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

    logger.info("[FILAMAN] Stored tracking data for print: printer=%s, archive=%s", printer_id, archive_id)
    logger.debug("[FILAMAN] Filament usage: %s", filament_usage)
    logger.debug("[FILAMAN] AMS trays: %s", list(ams_trays.keys()))


async def cleanup_tracking(printer_id: int, archive_id: int, db):
    """Report partial usage and clean up FilaMan tracking data for failed/aborted prints."""
    from backend.app.models.active_print_filaman import ActivePrintFilaMan

    result = await db.execute(
        select(ActivePrintFilaMan)
        .where(ActivePrintFilaMan.printer_id == printer_id)
        .where(ActivePrintFilaMan.archive_id == archive_id)
    )
    tracking = result.scalar_one_or_none()

    if not tracking:
        logger.debug("[FILAMAN] No tracking data to clean up for printer=%s, archive=%s", printer_id, archive_id)
        return

    # Try to report partial usage before cleanup
    try:
        await _report_partial_usage(printer_id, tracking)
    except Exception as e:
        logger.warning("[FILAMAN] Partial usage report failed: %s", e)

    await db.execute(
        delete(ActivePrintFilaMan)
        .where(ActivePrintFilaMan.printer_id == printer_id)
        .where(ActivePrintFilaMan.archive_id == archive_id)
    )
    await db.commit()
    logger.debug("[FILAMAN] Cleaned up tracking data for printer=%s, archive=%s", printer_id, archive_id)


async def _get_filaman_client_with_fallback():
    """Get FilaMan client, initializing from settings if needed."""
    client = get_filaman_client()
    if not client:
        async with async_session() as db:
            from backend.app.api.routes.settings import get_setting

            filaman_url = await get_setting(db, "filaman_url")
            filaman_api_key = await get_setting(db, "filaman_api_key")
            if filaman_url and filaman_api_key:
                client = init_filaman_client(filaman_url, filaman_api_key)

    if not client or not await client.health_check():
        return None

    return client


async def _report_consumption_for_slots(
    client,
    filament_usage_items: list[tuple[int, float]],
    ams_trays: dict[int, dict],
    slot_to_tray: list | None,
    method_label: str,
) -> int:
    """Report consumption to FilaMan for a list of (slot_id, grams) pairs.

    Returns number of spools successfully updated.
    """
    spools_updated = 0
    for slot_id, grams_used in filament_usage_items:
        if grams_used <= 0:
            continue

        global_tray_id = _resolve_global_tray_id(slot_id, slot_to_tray)
        tray_info = ams_trays.get(global_tray_id)
        if not tray_info:
            logger.debug("[FILAMAN] Slot %s: no tray at global_tray_id %s", slot_id, global_tray_id)
            continue

        spool_tag = _resolve_spool_tag(tray_info)
        if not spool_tag:
            logger.debug("[FILAMAN] Slot %s: no identifier for tray %s", slot_id, global_tray_id)
            continue

        spool = await client.find_spool_by_tray_uuid(spool_tag)
        if not spool:
            logger.debug("[FILAMAN] Slot %s: no spool for tag %s...", slot_id, spool_tag[:16])
            continue

        result = await client.report_consumption(spool["id"], grams_used)
        if result:
            logger.info("[FILAMAN] %s: slot %s: %sg -> spool %s", method_label, slot_id, grams_used, spool["id"])
            spools_updated += 1

    return spools_updated


async def _report_partial_usage(printer_id: int, tracking):
    """Report partial filament consumption based on actual G-code layer data."""
    from backend.app.services.printer_manager import printer_manager
    from backend.app.utils.threemf_tools import get_cumulative_usage_at_layer, mm_to_grams

    async with async_session() as db:
        from backend.app.api.routes.settings import get_setting

        report_partial = await get_setting(db, "filaman_report_partial_usage")
        if report_partial and report_partial.lower() == "false":
            logger.debug("[FILAMAN] Partial usage reporting disabled by setting")
            return

        filaman_enabled = await get_setting(db, "filaman_enabled")
        if not filaman_enabled or filaman_enabled.lower() != "true":
            return

    state = printer_manager.get_status(printer_id)
    if not state:
        logger.debug("[FILAMAN] No printer state available for partial usage")
        return

    current_layer = state.layer_num
    total_layers = state.total_layers

    if not current_layer or current_layer <= 0:
        logger.debug("[FILAMAN] No progress to report (layer 0 or unknown)")
        return

    logger.info("[FILAMAN] Reporting partial usage at layer %s/%s", current_layer, total_layers or "?")

    layer_usage = tracking.layer_usage
    filament_properties = tracking.filament_properties or {}
    filament_usage = tracking.filament_usage or []
    ams_trays = {int(k): v for k, v in (tracking.ams_trays or {}).items()}
    slot_to_tray = tracking.slot_to_tray

    client = await _get_filaman_client_with_fallback()
    if not client:
        logger.warning("[FILAMAN] Not reachable for partial usage reporting")
        return

    # Try to use accurate G-code parsed data
    if layer_usage:
        layer_usage_int = {
            int(layer): {int(fid): mm for fid, mm in filaments.items()} for layer, filaments in layer_usage.items()
        }
        usage_mm = get_cumulative_usage_at_layer(layer_usage_int, current_layer)

        if usage_mm:
            logger.info("[FILAMAN] Using G-code parsed data for layer %s", current_layer)

            usage_items = []
            for filament_id, mm_used in usage_mm.items():
                slot_id = filament_id + 1

                global_tray_id = _resolve_global_tray_id(slot_id, slot_to_tray)
                tray_info = ams_trays.get(global_tray_id)
                density = None
                diameter = 1.75

                if tray_info:
                    spool_tag = _resolve_spool_tag(tray_info)
                    if spool_tag:
                        spool = await client.find_spool_by_tray_uuid(spool_tag)
                        if spool:
                            filament_data = spool.get("filament") or {}
                            density = filament_data.get("density")
                            diameter = filament_data.get("diameter", 1.75)

                if not density:
                    props = filament_properties.get(str(slot_id), filament_properties.get(slot_id, {}))
                    density = props.get("density", 1.24)
                    logger.debug("[FILAMAN] Using fallback density %s for slot %s", density, slot_id)

                grams_used = round(mm_to_grams(mm_used, diameter, density), 2)
                usage_items.append((slot_id, grams_used))

            spools_updated = await _report_consumption_for_slots(
                client, usage_items, ams_trays, slot_to_tray, "Partial (G-code)"
            )
            if spools_updated > 0:
                logger.info("[FILAMAN] Reported partial usage to %s spool(s) using G-code data", spools_updated)
            return

    # Fallback: linear interpolation
    if not total_layers or total_layers <= 0:
        logger.debug("[FILAMAN] Cannot use linear fallback: total_layers=%s", total_layers)
        return

    progress_ratio = min(current_layer / total_layers, 1.0)
    logger.info("[FILAMAN] Falling back to linear interpolation (%s)", progress_ratio)

    usage_items = []
    for usage in filament_usage:
        slot_id = usage.get("slot_id", 0)
        total_used_g = usage.get("used_g", 0)
        if total_used_g > 0:
            partial_used_g = round(total_used_g * progress_ratio, 2)
            usage_items.append((slot_id, partial_used_g))

    spools_updated = await _report_consumption_for_slots(
        client, usage_items, ams_trays, slot_to_tray, "Partial (linear)"
    )
    if spools_updated > 0:
        logger.info("[FILAMAN] Reported partial usage to %s spool(s) using linear interpolation", spools_updated)


async def report_usage(printer_id: int, archive_id: int):
    """Report filament consumption to FilaMan after print completion."""
    async with async_session() as db:
        from backend.app.api.routes.settings import get_setting
        from backend.app.models.active_print_filaman import ActivePrintFilaMan

        result = await db.execute(
            select(ActivePrintFilaMan)
            .where(ActivePrintFilaMan.printer_id == printer_id)
            .where(ActivePrintFilaMan.archive_id == archive_id)
        )
        tracking = result.scalar_one_or_none()

        if not tracking:
            logger.info("[FILAMAN] No tracking data for print (printer=%s, archive=%s)", printer_id, archive_id)
            return

        filament_usage = tracking.filament_usage or []
        ams_trays = {int(k): v for k, v in (tracking.ams_trays or {}).items()}
        slot_to_tray = tracking.slot_to_tray

        # Delete tracking row
        await db.delete(tracking)
        await db.commit()

        if not filament_usage:
            logger.debug("[FILAMAN] No filament usage data for archive %s", archive_id)
            return

        # Check if FilaMan is enabled
        filaman_enabled = await get_setting(db, "filaman_enabled")
        if not filaman_enabled or filaman_enabled.lower() != "true":
            return

        client = await _get_filaman_client_with_fallback()
        if not client:
            logger.warning("[FILAMAN] Not reachable for usage reporting")
            return

        logger.info("[FILAMAN] Reporting per-filament consumption for archive %s", archive_id)

        usage_items = [(u.get("slot_id", 0), u.get("used_g", 0)) for u in filament_usage]
        spools_updated = await _report_consumption_for_slots(
            client, usage_items, ams_trays, slot_to_tray, f"Archive {archive_id}"
        )

        if spools_updated == 0:
            logger.info("[FILAMAN] Archive %s: no spools updated", archive_id)
        else:
            logger.info("[FILAMAN] Archive %s: updated %s spool(s)", archive_id, spools_updated)
