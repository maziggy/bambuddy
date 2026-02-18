"""Automatic filament consumption tracking.

Captures AMS tray remain% at print start, then computes consumption
deltas at print complete to update spool weight_used and last_used.

Primary tracking uses 3MF slicer estimates (precise per-filament data).
AMS remain% delta is the fallback for trays not covered by 3MF data.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spool_usage_history import SpoolUsageHistory

logger = logging.getLogger(__name__)


def _decode_mqtt_mapping(mapping_raw: list | None) -> list[int] | None:
    """Decode MQTT mapping field (snow-encoded) to bambuddy global tray IDs.

    The printer's MQTT mapping field is an array indexed by slicer filament slot
    (0-based). Each value uses snow encoding: ams_hw_id * 256 + local_slot.
    65535 means unmapped.

    Returns a list of bambuddy global tray IDs (or -1 for unmapped), or None if
    no valid mappings found.
    """
    if not isinstance(mapping_raw, list) or not mapping_raw:
        return None

    result = []
    for value in mapping_raw:
        if not isinstance(value, int) or value >= 65535:
            result.append(-1)
            continue

        ams_hw_id = value >> 8
        slot = value & 0xFF

        if 0 <= ams_hw_id <= 3:
            # Regular AMS: sequential global ID
            result.append(ams_hw_id * 4 + (slot & 0x03))
        elif 128 <= ams_hw_id <= 135:
            # AMS-HT: global ID is the hardware ID (one slot per unit)
            result.append(ams_hw_id)
        elif ams_hw_id in (254, 255):
            # External spool
            result.append(254 if slot != 255 else 255)
        else:
            result.append(-1)

    # Only return if at least one valid mapping exists
    if all(v < 0 for v in result):
        return None

    return result


@dataclass
class PrintSession:
    printer_id: int
    print_name: str
    started_at: datetime
    tray_remain_start: dict[tuple[int, int], int] = field(default_factory=dict)
    # tray_now at print start (correct value, unlike at completion where it's 255)
    tray_now_at_start: int = -1


# Module-level storage, keyed by printer_id
_active_sessions: dict[int, PrintSession] = {}


async def on_print_start(printer_id: int, data: dict, printer_manager) -> None:
    """Capture AMS tray remain% at print start."""
    state = printer_manager.get_status(printer_id)
    if not state or not state.raw_data:
        logger.debug("[UsageTracker] No state for printer %d, skipping", printer_id)
        return

    ams_raw = state.raw_data.get("ams", [])
    ams_data = ams_raw.get("ams", []) if isinstance(ams_raw, dict) else ams_raw if isinstance(ams_raw, list) else []
    if not ams_data:
        logger.debug("[UsageTracker] No AMS data for printer %d, skipping", printer_id)
        return

    tray_remain_start: dict[tuple[int, int], int] = {}
    for ams_unit in ams_data:
        ams_id = int(ams_unit.get("id", 0))
        for tray in ams_unit.get("tray", []):
            tray_id = int(tray.get("id", 0))
            remain = tray.get("remain", -1)
            if isinstance(remain, int) and 0 <= remain <= 100:
                tray_remain_start[(ams_id, tray_id)] = remain

    print_name = data.get("subtask_name", "") or data.get("filename", "unknown")

    # Capture tray_now at print start (reliable, unlike at completion where it's 255)
    tray_now_at_start = state.tray_now if state else -1

    # --- Diagnostic logging: dump mapping-related MQTT fields at print start ---
    # This helps us understand what each printer model reports for slot-to-tray mapping.
    mapping_field = state.raw_data.get("mapping")
    logger.info(
        "[UsageTracker] PRINT START printer %d: mapping=%s, tray_now=%d, last_loaded_tray=%s",
        printer_id,
        mapping_field,
        tray_now_at_start,
        getattr(state, "last_loaded_tray", "N/A"),
    )
    # Log all raw_data keys containing "map" or "ams" for discovery
    map_keys = {k: state.raw_data[k] for k in state.raw_data if "map" in k.lower()}
    if map_keys:
        logger.info("[UsageTracker] PRINT START printer %d: mapping-related keys: %s", printer_id, map_keys)
    # Log per-tray summary: tray_now, tray_tar, tray_type, tray_color for each slot
    for ams_unit in ams_data:
        ams_id = int(ams_unit.get("id", 0))
        tray_summary = []
        for tray in ams_unit.get("tray", []):
            tray_summary.append(
                f"T{tray.get('id', '?')}(type={tray.get('tray_type', '')}, "
                f"color={tray.get('tray_color', '')}, "
                f"now={ams_raw.get('tray_now', '?') if isinstance(ams_raw, dict) else '?'}, "
                f"tar={ams_raw.get('tray_tar', '?') if isinstance(ams_raw, dict) else '?'})"
            )
        logger.info("[UsageTracker] PRINT START printer %d AMS %d: %s", printer_id, ams_id, ", ".join(tray_summary))

    # Always create session (even without valid remain data) so print_name
    # is available at completion for 3MF-based tracking
    session = PrintSession(
        printer_id=printer_id,
        print_name=print_name,
        started_at=datetime.now(timezone.utc),
        tray_remain_start=tray_remain_start,
        tray_now_at_start=tray_now_at_start,
    )
    _active_sessions[printer_id] = session

    if tray_remain_start:
        logger.info(
            "[UsageTracker] Captured start remain%% for printer %d (%d trays): %s",
            printer_id,
            len(tray_remain_start),
            {f"{k[0]}-{k[1]}": v for k, v in tray_remain_start.items()},
        )
    else:
        logger.debug("[UsageTracker] No valid remain%% for printer %d, 3MF fallback available", printer_id)


async def on_print_complete(
    printer_id: int,
    data: dict,
    printer_manager,
    db: AsyncSession,
    archive_id: int | None = None,
    ams_mapping: list[int] | None = None,
) -> list[dict]:
    """Compute consumption deltas and update spool weight_used/last_used.

    Uses two tracking strategies in priority order:
    1. 3MF per-filament estimates (primary) — precise slicer data for all spools
    2. AMS remain% delta (fallback) — only for trays not already handled by 3MF

    Returns a list of dicts describing what was logged (for WebSocket broadcast).
    """
    session = _active_sessions.pop(printer_id, None)
    status = data.get("status", "completed")
    results = []
    handled_trays: set[tuple[int, int]] = set()

    logger.info(
        "[UsageTracker] on_print_complete: printer=%d, archive=%s, session=%s, ams_mapping=%s",
        printer_id,
        archive_id,
        "yes" if session else "no",
        ams_mapping,
    )

    # --- Diagnostic logging: dump mapping-related MQTT fields at print completion ---
    state = printer_manager.get_status(printer_id)
    if state and state.raw_data:
        logger.info(
            "[UsageTracker] PRINT COMPLETE printer %d: mapping=%s, tray_now=%s, last_loaded_tray=%s",
            printer_id,
            state.raw_data.get("mapping"),
            state.tray_now,
            getattr(state, "last_loaded_tray", "N/A"),
        )

    # --- Path 1 (PRIMARY): 3MF per-filament estimates ---
    if archive_id:
        print_name = (
            (session.print_name if session else None) or data.get("subtask_name", "") or data.get("filename", "unknown")
        )
        threemf_results = await _track_from_3mf(
            printer_id,
            archive_id,
            status,
            print_name,
            handled_trays,
            printer_manager,
            db,
            ams_mapping=ams_mapping,
            tray_now_at_start=session.tray_now_at_start if session else -1,
            last_progress=data.get("last_progress", 0.0),
            last_layer_num=data.get("last_layer_num", 0),
        )
        results.extend(threemf_results)

    # --- Path 2 (FALLBACK): AMS remain% delta (only for trays not handled by 3MF) ---
    if session and session.tray_remain_start:
        state = printer_manager.get_status(printer_id)
        if state and state.raw_data:
            ams_raw = state.raw_data.get("ams", [])
            ams_data = (
                ams_raw.get("ams", []) if isinstance(ams_raw, dict) else ams_raw if isinstance(ams_raw, list) else []
            )

            for ams_unit in ams_data:
                ams_id = int(ams_unit.get("id", 0))
                for tray in ams_unit.get("tray", []):
                    tray_id = int(tray.get("id", 0))
                    key = (ams_id, tray_id)

                    if key in handled_trays:
                        continue  # Already tracked via 3MF

                    if key not in session.tray_remain_start:
                        continue

                    current_remain = tray.get("remain", -1)
                    if not isinstance(current_remain, int) or current_remain < 0 or current_remain > 100:
                        continue

                    start_remain = session.tray_remain_start[key]
                    delta_pct = start_remain - current_remain

                    if delta_pct <= 0:
                        continue  # No consumption or tray was refilled

                    # Look up SpoolAssignment for this slot
                    result = await db.execute(
                        select(SpoolAssignment).where(
                            SpoolAssignment.printer_id == printer_id,
                            SpoolAssignment.ams_id == ams_id,
                            SpoolAssignment.tray_id == tray_id,
                        )
                    )
                    assignment = result.scalar_one_or_none()
                    if not assignment:
                        continue

                    # Load spool
                    spool_result = await db.execute(select(Spool).where(Spool.id == assignment.spool_id))
                    spool = spool_result.scalar_one_or_none()
                    if not spool:
                        continue

                    # Compute weight consumed
                    weight_grams = (delta_pct / 100.0) * spool.label_weight

                    # Update spool
                    spool.weight_used = (spool.weight_used or 0) + weight_grams
                    spool.last_used = datetime.now(timezone.utc)

                    # Insert usage history record
                    history = SpoolUsageHistory(
                        spool_id=spool.id,
                        printer_id=printer_id,
                        print_name=session.print_name,
                        weight_used=round(weight_grams, 1),
                        percent_used=delta_pct,
                        status=status,
                    )
                    db.add(history)

                    handled_trays.add(key)
                    results.append(
                        {
                            "spool_id": spool.id,
                            "weight_used": round(weight_grams, 1),
                            "percent_used": delta_pct,
                            "ams_id": ams_id,
                            "tray_id": tray_id,
                            "material": spool.material,
                        }
                    )

                    logger.info(
                        "[UsageTracker] Spool %d consumed %.1fg (%d%%) on printer %d AMS%d-T%d (AMS fallback, %s)",
                        spool.id,
                        weight_grams,
                        delta_pct,
                        printer_id,
                        ams_id,
                        tray_id,
                        status,
                    )

    if results:
        await db.commit()

    return results


async def _track_from_3mf(
    printer_id: int,
    archive_id: int,
    status: str,
    print_name: str,
    handled_trays: set[tuple[int, int]],
    printer_manager,
    db: AsyncSession,
    ams_mapping: list[int] | None = None,
    tray_now_at_start: int = -1,
    last_progress: float = 0.0,
    last_layer_num: int = 0,
) -> list[dict]:
    """Track usage from 3MF per-filament slicer data (primary path).

    Uses slicer-estimated filament weight for all spools (BL and non-BL).
    For partial prints (failed/aborted), tries per-layer gcode data first,
    then falls back to linear scaling by progress.

    Slot-to-tray mapping priority:
    1. Stored ams_mapping from print command (reprints/direct prints)
    2. MQTT mapping field from printer state (universal, all print sources)
    3. Queue item ams_mapping (for queue-initiated prints)
    4. tray_now from printer state (for single-filament non-queue prints)
    5. Default mapping: slot_id - 1 = global_tray_id (last resort)
    """
    from backend.app.core.config import settings as app_settings
    from backend.app.models.archive import PrintArchive
    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.utils.threemf_tools import extract_filament_usage_from_3mf

    result = await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive or not archive.file_path:
        logger.info("[UsageTracker] 3MF: archive %s has no file_path, skipping", archive_id)
        return []

    file_path = app_settings.base_dir / archive.file_path
    if not file_path.exists():
        logger.info("[UsageTracker] 3MF: file not found: %s", file_path)
        return []

    filament_usage = extract_filament_usage_from_3mf(file_path)
    if not filament_usage:
        logger.info("[UsageTracker] 3MF: no filament usage data in %s", file_path)
        return []

    logger.info("[UsageTracker] 3MF: archive %s, filament_usage=%s", archive_id, filament_usage)

    # --- Resolve slot-to-tray mapping ---
    mapping_source = None

    # 1. Use stored ams_mapping from the print command (reprints/direct prints)
    slot_to_tray = ams_mapping
    if slot_to_tray:
        mapping_source = "print_cmd"

    # 2. Try MQTT mapping field from printer state (universal, all print sources)
    if not slot_to_tray:
        state = printer_manager.get_status(printer_id)
        raw_data = getattr(state, "raw_data", None) if state else None
        if raw_data:
            mqtt_mapping = raw_data.get("mapping")
            decoded = _decode_mqtt_mapping(mqtt_mapping)
            if decoded:
                slot_to_tray = decoded
                mapping_source = "mqtt"

    # 3. Try queue item ams_mapping (queue-initiated prints store the exact mapping)
    if not slot_to_tray:
        queue_result = await db.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.archive_id == archive_id)
            .where(PrintQueueItem.status.in_(["printing", "completed", "failed"]))
        )
        queue_item = queue_result.scalar_one_or_none()
        if queue_item and queue_item.ams_mapping:
            try:
                slot_to_tray = json.loads(queue_item.ams_mapping)
                mapping_source = "queue"
            except (json.JSONDecodeError, TypeError):
                pass

    logger.info(
        "[UsageTracker] 3MF: slot_to_tray=%s (source: %s)",
        slot_to_tray,
        mapping_source or "none",
    )

    # 3. For single-filament non-queue prints, use tray_now from printer state
    #    Priority: tray_now_at_start > current tray_now > last_loaded_tray > vt_tray check
    nonzero_slots = [u for u in filament_usage if u.get("used_g", 0) > 0]
    tray_now_override: int | None = None
    if not slot_to_tray and len(nonzero_slots) == 1:
        state = printer_manager.get_status(printer_id)
        # Try tray_now_at_start first (captured at print start)
        if 0 <= tray_now_at_start <= 254:
            tray_now_override = tray_now_at_start
            logger.info("[UsageTracker] 3MF: using tray_now_at_start=%d (single-filament fallback)", tray_now_at_start)
        elif state and 0 <= state.tray_now <= 254:
            # Current state is valid (printer didn't retract yet)
            tray_now_override = state.tray_now
            logger.info("[UsageTracker] 3MF: using current tray_now=%d", state.tray_now)
        elif state and 0 <= state.last_loaded_tray <= 253:
            # Last valid tray before retract (H2D retracts before completion callback)
            tray_now_override = state.last_loaded_tray
            logger.info("[UsageTracker] 3MF: using last_loaded_tray=%d (post-retract fallback)", state.last_loaded_tray)
        elif state and state.tray_now == 255:
            # 255 = "no filament" on legacy printers, but valid 2nd external spool on H2-series
            vt_tray = state.raw_data.get("vt_tray") or []
            if any(int(vt.get("id", 0)) == 255 for vt in vt_tray if isinstance(vt, dict)):
                tray_now_override = state.tray_now
                logger.info("[UsageTracker] 3MF: using tray_now=255 (H2-series external spool)")
        if tray_now_override is None:
            logger.info(
                "[UsageTracker] 3MF: no valid tray_now (at_start=%d, current=%s, last_loaded=%s)",
                tray_now_at_start,
                state.tray_now if state else "N/A",
                state.last_loaded_tray if state else "N/A",
            )

    # Scale factor for partial prints (failed/aborted)
    if status == "completed":
        scale = 1.0
    else:
        state = printer_manager.get_status(printer_id)
        progress = state.progress if state else 0
        # Firmware resets progress to 0 on cancel — use last valid progress captured during print
        if progress <= 0 and last_progress > 0:
            progress = last_progress
            logger.info("[UsageTracker] 3MF: using last_progress=%.1f (firmware reset current to 0)", last_progress)
        scale = max(0.0, min(progress / 100.0, 1.0))

    # Per-layer gcode accuracy for partial prints
    layer_grams: dict[int, float] | None = None
    if status != "completed":
        state = printer_manager.get_status(printer_id)
        current_layer = state.layer_num if state else 0
        # Firmware resets layer_num to 0 on cancel — use last valid layer captured during print
        if current_layer <= 0 and last_layer_num > 0:
            current_layer = last_layer_num
            logger.info("[UsageTracker] 3MF: using last_layer_num=%d (firmware reset current to 0)", last_layer_num)
        if current_layer > 0:
            try:
                from backend.app.utils.threemf_tools import (
                    extract_filament_properties_from_3mf,
                    extract_layer_filament_usage_from_3mf,
                    get_cumulative_usage_at_layer,
                    mm_to_grams,
                )

                layer_usage = extract_layer_filament_usage_from_3mf(file_path)
                if layer_usage:
                    cumulative_mm = get_cumulative_usage_at_layer(layer_usage, current_layer)
                    filament_props = extract_filament_properties_from_3mf(file_path)
                    layer_grams = {}
                    for filament_id, mm_used in cumulative_mm.items():
                        slot_id = filament_id + 1  # 0-based to 1-based
                        props = filament_props.get(slot_id, {})
                        density = props.get("density", 1.24)
                        diameter = props.get("diameter", 1.75)
                        layer_grams[slot_id] = mm_to_grams(mm_used, diameter, density)
            except Exception:
                pass  # Fall back to linear scaling

    results = []

    for usage in filament_usage:
        slot_id = usage.get("slot_id", 0)
        used_g = usage.get("used_g", 0)
        if used_g <= 0:
            continue

        # Map 3MF slot_id to physical (ams_id, tray_id) using resolved mapping
        if tray_now_override is not None:
            # Single-filament non-queue print: use actual tray from printer state
            global_tray_id = tray_now_override
        else:
            # Queue mapping or default: slot_id - 1, overridden by ams_mapping
            global_tray_id = slot_id - 1
            if slot_to_tray and slot_id <= len(slot_to_tray):
                mapped = slot_to_tray[slot_id - 1]
                if isinstance(mapped, int) and mapped >= 0:
                    global_tray_id = mapped

        if global_tray_id >= 254:
            # External spool: ams_id=255 (sentinel), tray_id=slot index (0 or 1)
            ams_id = 255
            tray_id = global_tray_id - 254
        elif global_tray_id >= 128:
            ams_id = global_tray_id
            tray_id = 0
        else:
            ams_id = global_tray_id // 4
            tray_id = global_tray_id % 4

        logger.info(
            "[UsageTracker] 3MF: slot_id=%d -> global_tray=%d -> AMS%d-T%d (used_g=%.1f, tray_now_override=%s)",
            slot_id,
            global_tray_id,
            ams_id,
            tray_id,
            used_g,
            tray_now_override,
        )

        key = (ams_id, tray_id)
        if key in handled_trays:
            continue

        # Find spool assignment for this tray
        assign_result = await db.execute(
            select(SpoolAssignment).where(
                SpoolAssignment.printer_id == printer_id,
                SpoolAssignment.ams_id == ams_id,
                SpoolAssignment.tray_id == tray_id,
            )
        )
        assignment = assign_result.scalar_one_or_none()
        if not assignment:
            logger.info("[UsageTracker] 3MF: no spool assignment at printer %d AMS%d-T%d", printer_id, ams_id, tray_id)
            continue

        # Load spool
        spool_result = await db.execute(select(Spool).where(Spool.id == assignment.spool_id))
        spool = spool_result.scalar_one_or_none()
        if not spool:
            continue

        # Use per-layer grams if available, otherwise linear scale
        if layer_grams and slot_id in layer_grams:
            weight_grams = layer_grams[slot_id]
        else:
            weight_grams = used_g * scale

        if weight_grams <= 0:
            continue

        # Update spool
        spool.weight_used = (spool.weight_used or 0) + weight_grams
        spool.last_used = datetime.now(timezone.utc)

        percent = round(weight_grams / (spool.label_weight or 1000) * 100)

        # Insert usage history record
        history = SpoolUsageHistory(
            spool_id=spool.id,
            printer_id=printer_id,
            print_name=print_name,
            weight_used=round(weight_grams, 1),
            percent_used=percent,
            status=status,
        )
        db.add(history)

        handled_trays.add(key)
        results.append(
            {
                "spool_id": spool.id,
                "weight_used": round(weight_grams, 1),
                "percent_used": percent,
                "ams_id": ams_id,
                "tray_id": tray_id,
                "material": spool.material,
            }
        )

        # Determine mapping source for debug logging
        if tray_now_override is not None:
            map_src = ", tray_now"
        elif mapping_source:
            map_src = f", {mapping_source}_map"
        else:
            map_src = ""
        logger.info(
            "[UsageTracker] Spool %d consumed %.1fg (3MF%s%s) on printer %d AMS%d-T%d (%s)",
            spool.id,
            weight_grams,
            " per-layer" if (layer_grams and slot_id in layer_grams) else (f" scaled {scale:.0%}" if scale < 1 else ""),
            map_src,
            printer_id,
            ams_id,
            tray_id,
            status,
        )

    return results
