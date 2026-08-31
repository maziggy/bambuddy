"""Spoolman per-filament usage tracking for active prints.

Captures AMS tray state and G-code data at print start, then reports
per-filament usage to the correct Spoolman spools at print completion.
Supports accurate partial usage reporting for failed/cancelled prints.
"""

import json
import logging
import math
from dataclasses import dataclass

from sqlalchemy import delete, select

from backend.app.core.config import settings as app_settings
from backend.app.core.database import async_session
from backend.app.services.spoolman import (
    SpoolmanClientError,
    SpoolmanNotFoundError,
    SpoolmanUnavailableError,
    get_spoolman_client,
    init_spoolman_client,
)

logger = logging.getLogger(__name__)

# Zero UUID used by Bambu printers for empty/unset tray_uuid
_ZERO_UUID = "00000000000000000000000000000000"
_ZERO_TAG_UID = "0000000000000000"

# Highest global tray id that names a real slot. 255 does not: it is
# ``PrinterState.tray_now``'s initial value, what an unparseable reading falls
# back to, and what the field reads while nothing is loaded. The external spool
# reports 254 when it is actually in use, and ``bambu_mqtt`` applies the same
# cut-off when it seeds the tray-change log. Treating 255 as a slot would put
# ``(255, 1)`` into the "slots this print used" evidence and exclude every real
# one -- silently disabling the very fallback this guard protects (#1820).
#
# Applied to ``tray_now`` only. A 255 in the print's mapping or its tray-change
# log was written there by a print and is evidence, however odd; a 255 in
# ``tray_now`` is the field at rest, which is the absence of evidence.
_MAX_REAL_TRAY_ID = 254


def _is_real_tray_id(value) -> bool:
    """True when ``value`` names a physical slot rather than "nothing loaded"."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_REAL_TRAY_ID


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
    return get_fallback_spool_tag_for_slot(printer_serial, ams_id, tray_id)


def get_fallback_spool_tag_for_slot(printer_serial: str, ams_id: int, tray_id: int) -> str:
    """Public helper matching frontend getFallbackSpoolTag(serial, amsId, trayId).

    Used by stale-tag cleanup (#1457) to detect Spoolman spools still holding
    this slot's deterministic fallback tag in extra.tag.
    """
    if not printer_serial:
        return ""
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
    A value of -1 in the custom mapping means the slicer routed this slot to
    the external spool. BambuStudio converts virtual tray IDs (254/255) to -1
    in the flat ams_mapping array before sending to the printer — see
    start_print() in bambu_mqtt.py which documents this convention. We mirror
    it here: when -1 is seen, look up the external spool's actual
    global_tray_id (254/255) in ams_trays rather than falling through to the
    position-based default (which would map slot_id=1 to the first AMS tray
    and credit an unrelated spool — see #1276, regression of #853).
    Position-based default: uses sorted ams_trays keys so external spools (ID 254/255)
    naturally follow standard AMS trays, matching the slicer's slot numbering.
    Final fallback: slot_id - 1 (legacy, works for pure AMS without external spools).
    """
    if slot_to_tray and slot_id <= len(slot_to_tray):
        mapped_tray = slot_to_tray[slot_id - 1]
        if mapped_tray >= 0:
            return mapped_tray
        if mapped_tray == -1 and ams_trays:
            # -1 means external spool. 254 = VIRTUAL_TRAY_DEPUTY_ID (main on
            # single-nozzle, left/deputy on H2D dual-nozzle); 255 =
            # VIRTUAL_TRAY_MAIN_ID. Prefer 254 when both exist since that's
            # what single-nozzle printers report via tray_now.
            for ext_id in (254, 255):
                if ext_id in ams_trays:
                    return ext_id
    # Position-based default: sort available tray IDs so external spools (254/255)
    # come after standard AMS trays, matching the slicer's slot assignment order.
    if ams_trays:
        sorted_tray_ids = sorted(ams_trays.keys())
        if slot_id <= len(sorted_tray_ids):
            return sorted_tray_ids[slot_id - 1]
    return slot_id - 1


def _single_slot_tray_from_state(
    state,
    filament_usage: list[dict],
    tray_now_at_start: int | None = None,
) -> tuple[int, int] | None:
    """The tray a single-slot print actually drew from, read off the printer.

    A1, A1 mini, P1S and P2S publish no ``mapping`` field and drop the MQTT
    connection when we subscribe to their request topic, so neither of the
    other two fallbacks can answer for them. What they do report is which tray
    the extruder is fed from, and for a print that uses exactly one filament
    slot that is the same question: the one slot came from the one tray.

    The ladder mirrors ``usage_tracker.on_print_complete`` step 5, which has
    consulted these same fields since it started resolving mappings at
    completion. Spoolman users were the only ones not getting them (#2953).

    Gated on exactly one slot with usage, like the internal writer: on a
    multi-colour print every filament change moves ``tray_now``, so a single
    tray reading says nothing about which slot it belongs to.

    More than one tray-change entry means the print switched trays mid-run
    (AMS backup on runout, #957). ``report_usage`` splits those per segment
    and must not be handed a single-tray mapping instead, so this declines.

    Returns ``(slot_id, global_tray_id)``, or None when the printer offered no
    usable reading and the positional default stands.
    """
    nonzero = [u for u in filament_usage or [] if u.get("used_g", 0) > 0]
    if len(nonzero) != 1:
        return None
    slot_id = nonzero[0].get("slot_id", 0)
    if slot_id <= 0:
        return None

    changes = list(getattr(state, "tray_change_log", None) or [])
    if len(changes) > 1:
        return None
    if len(changes) == 1:
        entry = changes[0]
        if isinstance(entry, (tuple, list)) and entry and _is_real_tray_id(entry[0]):
            # Strongest evidence there is: the printer announced this switch
            # while the job was running, so it describes this print and no
            # other. On the reporter's A1 it read (3, 0) -- tray 3 at layer 0
            # -- while the positional default was charging tray 0.
            return slot_id, entry[0]

    # No mid-print switch recorded. Fall back to the standing tray readings,
    # newest evidence first. ``tray_now`` is 255 both at rest and while
    # nothing is loaded, which is why _MAX_REAL_TRAY_ID excludes it; A1
    # firmware parks there the moment a print ends, leaving last_loaded_tray
    # as the only survivor.
    for candidate in (
        tray_now_at_start,
        getattr(state, "tray_now", None),
        getattr(state, "last_loaded_tray", None),
    ):
        if _is_real_tray_id(candidate):
            return slot_id, candidate
    return None


def _resolve_slot_to_tray_fallback(
    printer_id: int,
    filament_usage: list[dict],
    tray_now_at_start: int | None = None,
) -> tuple[list[int] | None, str]:
    """Recover a slot-to-tray mapping at completion when print start captured none.

    ``store_print_data`` can only learn the mapping from two sources: the
    ``ams_mapping`` Bambuddy intercepts on the printer's local request topic, and
    a queue item's stored mapping. Neither exists for a print dispatched from
    Bambu Studio while the printer is cloud-bound — the command travels through
    Bambu's broker and never appears on the local topic we subscribe to. With
    ``slot_to_tray`` left NULL, ``_resolve_global_tray_id`` guesses by position:
    slicer slot 1 to the first loaded tray, slot 2 to the second, and so on. An
    AMS that isn't loaded in slicer order then charges every slot to the wrong
    spool, and the archive's filament is rewritten to match, so the print
    silently changes colour when it finishes (#2768).

    The printer knows the real answer. Its ``mapping`` field carries the actual
    slot-to-tray assignment for the running job, and for the models that never
    publish it (A1, P1S, P2S) the 3MF's per-slot colours can be matched against
    the loaded trays instead. Failing both, a print that used a single filament
    slot can be pinned to the tray the printer reported feeding from
    (``_single_slot_tray_from_state``).

    The built-in inventory writer has consulted all three for as long as it has
    resolved mappings at completion. The first version of this function offered
    only the first two, which left A1-class printers -- no ``mapping`` field, no
    request topic -- with nothing but the colour match, and that needs the
    slicer's filament colour to equal the tray's exactly. A generic black
    profile against a tray set to #111111 does not match, and the print is
    charged to whichever spool happens to sit in the first tray (#2953).

    Deliberately at completion rather than inside ``store_print_data``: the
    printer keeps publishing ``mapping`` long after a job ends — it is still in
    the status payload while the printer sits idle — so reading it at print start
    risks stamping the *previous* job's mapping onto this one before the printer
    has pushed the update. At completion the field unambiguously describes the
    job that just ran.

    Args:
        printer_id: Printer whose live state is consulted.
        filament_usage: The 3MF's per-slot estimates. The colour match reads
            ``slot_id``/``color``; the tray-state fallback reads
            ``slot_id``/``used_g``.
        tray_now_at_start: The tray the printer was feeding from when the print
            began, as captured by ``store_print_data``. Only consulted by the
            tray-state fallback.

    Returns:
        ``(mapping, source)``, or ``(None, "none")`` when no fallback produced
        anything and the positional default stands.
    """
    from backend.app.services.printer_manager import printer_manager
    from backend.app.services.usage_tracker import _decode_mqtt_mapping, _match_slots_by_color

    state = printer_manager.get_status(printer_id)
    raw_data = getattr(state, "raw_data", None) if state else None

    # Both of the first two fallbacks read the status payload; the third reads
    # fields ``bambu_mqtt`` maintains on the state object itself, so an empty
    # payload must not short-circuit past it.
    if raw_data:
        decoded = _decode_mqtt_mapping(raw_data.get("mapping"))
        if decoded:
            return decoded, "mqtt"

        matched = _match_slots_by_color(filament_usage, raw_data.get("ams"))
        if matched:
            return matched, "color_match"

    single = _single_slot_tray_from_state(state, filament_usage, tray_now_at_start)
    if single is not None:
        slot_id, global_tray_id = single
        # Only the one slot is claimed. The -1 padding is the array's existing
        # "not an AMS tray" value, and the slots carrying it consumed nothing,
        # so no caller resolves them: ``_report_spool_usage_for_slots`` skips
        # zero-gram slots before resolving, ``_print_used_tray_keys`` skips
        # negatives, and report_usage's handled-set skips them too.
        mapping = [-1] * slot_id
        mapping[slot_id - 1] = global_tray_id
        return mapping, "tray_state"

    return None, "none"


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


def _snapshot_tray_remain(raw_data: dict, skipped_out: list[str] | None = None) -> dict[str, dict]:
    """Capture per-slot ``remain%`` + ``tray_uuid`` at print start so the
    completion path can compute a remain-delta when 3MF data doesn't cover
    the slot (or there's no 3MF at all — #1820).

    Returns ``{"<ams_id>-<tray_id>": {"remain": int, "tray_uuid": str}}``.
    Only slots whose ``remain`` is a valid 0..100 int are included; invalid
    values mean the AMS hasn't read the spool yet and a delta would be
    meaningless. Mirrors the gate in
    ``usage_tracker.on_print_start:309``.

    A rejected slot is appended to *skipped_out* when one is supplied, so the
    caller can say which slots this print will not be able to charge. That is
    not hypothetical: an AMS reports a negative ``remain`` on a nearly empty
    spool, so the gate can drop the one slot that is about to do the printing
    (#1820).
    """
    snapshot: dict[str, dict] = {}
    ams_raw = raw_data.get("ams", [])
    ams_data = ams_raw.get("ams", []) if isinstance(ams_raw, dict) else ams_raw if isinstance(ams_raw, list) else []
    for ams_unit in ams_data:
        if not isinstance(ams_unit, dict):
            continue
        ams_id = int(ams_unit.get("id", 0))
        for tray in ams_unit.get("tray", []):
            if not isinstance(tray, dict):
                continue
            tray_id = int(tray.get("id", 0))
            remain = tray.get("remain", -1)
            if isinstance(remain, int) and 0 <= remain <= 100:
                snapshot[f"{ams_id}-{tray_id}"] = {
                    "remain": remain,
                    "tray_uuid": tray.get("tray_uuid", "") or "",
                }
            elif skipped_out is not None:
                skipped_out.append(f"AMS{ams_id}-T{tray_id}(remain={remain})")
    vt_tray_raw = raw_data.get("vt_tray") or []
    if isinstance(vt_tray_raw, dict):
        vt_tray_raw = [vt_tray_raw]
    for vt in vt_tray_raw:
        if not isinstance(vt, dict):
            continue
        vt_id = int(vt.get("id", 254))
        # 254 → (255, 0), 255 → (255, 1) — matches usage_tracker's encoding.
        vt_tray_id = vt_id - 254
        remain = vt.get("remain", -1)
        if isinstance(remain, int) and 0 <= remain <= 100:
            snapshot[f"255-{vt_tray_id}"] = {
                "remain": remain,
                "tray_uuid": vt.get("tray_uuid", "") or "",
            }
        elif skipped_out is not None:
            skipped_out.append(f"VT{vt_id}(remain={remain})")
    return snapshot


async def store_print_data(
    printer_id: int,
    archive_id: int,
    file_path: str,
    db,
    printer_manager,
    ams_mapping: list[int] | None = None,
    plate_id: int | None = None,
):
    """Store Spoolman tracking data at print start (persisted to database).

    Per-print tracking is the primary weight-update path for Spoolman, mirroring
    how the internal Filament Inventory works. The legacy AMS-remain%-based sync
    is no longer used as a weight writer (#1119), so this runs whenever Spoolman
    is enabled regardless of the deprecated `spoolman_disable_weight_sync` flag.

    ``plate_id``, when set, scopes the 3MF filament extract to a single plate so
    queue / direct-Print dispatch of plate N of a multi-plate file doesn't
    attribute every plate's filament to the printed spool (#1697). When unset,
    the queue item's plate_id (if any) is used; otherwise the whole-file sum is
    extracted, which is correct for direct prints that target the first/only
    plate of a single-plate file.
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

    # Get current AMS tray state up front — needed both for the 3MF path's
    # ams_trays field and for the remain%-delta snapshot (#1820 fallback for
    # no-3MF "Untitled" prints, mirroring usage_tracker.on_print_start).
    state = printer_manager.get_status(printer_id)
    ams_trays: dict[int, dict] = {}
    tray_remain_start: dict[str, dict] = {}
    if state and state.raw_data:
        ams_trays = build_ams_tray_lookup(state.raw_data)
        skipped_slots: list[str] = []
        tray_remain_start = _snapshot_tray_remain(state.raw_data, skipped_slots)
        if skipped_slots:
            # Matches what usage_tracker.on_print_start reports for the
            # internal inventory, so both backends name the slots that this
            # print will not be able to charge at AMS granularity.
            logger.info(
                "[SPOOLMAN] Printer %s: slots with no usable remain%% at print start: %s",
                printer_id,
                ", ".join(skipped_slots),
            )

    # Try to read per-slot filament estimates from the 3MF. Two paths can
    # leave ``filament_usage`` empty: (1) fallback archive (no .gcode.3mf
    # was downloadable from the printer — "Untitled" prints, see #1820),
    # (2) 3MF present but slice_info missing per-filament estimates.
    # Both fall through to the remain%-delta path at completion.
    filament_usage: list | None = None
    layer_usage_json: dict | None = None
    filament_properties: dict | None = None
    full_path = (
        app_settings.base_dir / file_path
    )  # SEC-PATH-OK: file_path is archive.file_path / library_file.file_path — DB-stored, internally generated
    threemf_available = bool(file_path) and full_path.exists()
    queue_item = None
    if threemf_available:
        # Resolve the queue item once — used both for the plate-scoped 3MF parsing
        # fallback (#1697: multi-plate file dispatched for one plate must only count
        # that plate's filament) and for the ams_mapping fallback below.
        queue_result = await db.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.archive_id == archive_id)
            .where(PrintQueueItem.status == "printing")
        )
        queue_item = queue_result.scalar_one_or_none()
        # Caller-supplied plate_id wins (direct-Print path); fall back to the queue
        # item's plate_id (queue dispatch path).
        effective_plate_id = (
            plate_id if plate_id is not None else (queue_item.plate_id if queue_item is not None else None)
        )
        filament_usage = extract_filament_usage_from_3mf(full_path, effective_plate_id) or None

        layer_usage = extract_layer_filament_usage_from_3mf(full_path, effective_plate_id)
        if layer_usage:
            # Convert int keys to string for JSON serialization
            layer_usage_json = {str(k): v for k, v in layer_usage.items()}
            logger.debug("[SPOOLMAN] Parsed %s layers from G-code", len(layer_usage))

        filament_properties = extract_filament_properties_from_3mf(full_path)
    else:
        # No 3MF on disk — common for "Untitled" prints whose .gcode.3mf
        # was never on the printer's FTP. Logged at debug since the
        # fallback path below picks up the slack when remain% is available.
        logger.debug("[SPOOLMAN] 3MF file not available: %s", full_path)

    # If neither path has anything useful, there's nothing to track.
    if not filament_usage and not tray_remain_start:
        if threemf_available:
            logger.debug("[SPOOLMAN] No filament usage data in 3MF for archive %s", archive_id)
        return

    # Prefer the explicit mapping captured from the print command, then fall back
    # to any queue mapping stored for scheduled/reprint jobs.
    slot_to_tray = ams_mapping if ams_mapping is not None else None
    mapping_source = "print_cmd" if slot_to_tray else None
    if not slot_to_tray and queue_item and queue_item.ams_mapping:
        try:
            slot_to_tray = json.loads(queue_item.ams_mapping)
            mapping_source = "queue"
        except json.JSONDecodeError:
            pass  # Ignore malformed AMS mapping; fall back to default slot assignment

    # Delete any existing row for this printer/archive (shouldn't exist, but just in case)
    await db.execute(
        delete(ActivePrintSpoolman)
        .where(ActivePrintSpoolman.printer_id == printer_id)
        .where(ActivePrintSpoolman.archive_id == archive_id)
    )

    # Insert new tracking data. ``filament_usage`` may be None for the
    # no-3MF case; report_usage falls back to ``tray_remain_start``.
    tracking = ActivePrintSpoolman(
        printer_id=printer_id,
        archive_id=archive_id,
        filament_usage=filament_usage,
        ams_trays=ams_trays,
        slot_to_tray=slot_to_tray,
        layer_usage=layer_usage_json,
        filament_properties=filament_properties,
        tray_remain_start=tray_remain_start or None,
        # Which slot the printer was drawing from when this print began. For a
        # print with no ams_mapping -- one started from the printer's own
        # screen, which is the case this whole fallback exists for -- it is the
        # only evidence of which slot the print used (#1820).
        tray_now_at_start=getattr(state, "tray_now", None) if state else None,
    )
    db.add(tracking)
    await db.commit()

    logger.info(
        "[SPOOLMAN] Stored tracking data for print: printer=%s, archive=%s (3mf=%s, remain_snapshot=%d slot(s))",
        printer_id,
        archive_id,
        "yes" if filament_usage else "no",
        len(tray_remain_start),
    )
    logger.debug("[SPOOLMAN] Filament usage: %s", filament_usage)
    logger.debug("[SPOOLMAN] AMS trays: %s", list(ams_trays.keys()))
    # Logged at info even when there is no mapping: "source: none" here is the
    # signal that completion will have to fall back, which is the single most
    # useful line in the log when a print is charged to the wrong spool (#2768).
    logger.info(
        "[SPOOLMAN] Print start: archive %s slot_to_tray=%s (source: %s)",
        archive_id,
        slot_to_tray,
        mapping_source or "none",
    )
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


async def _resolve_spool_id_via_slot_assignment(printer_id: int, ams_id: int, tray_id: int) -> int | None:
    """Look up the Spoolman spool ID locally bound to (printer, ams, tray).

    Fallback path for #1459: when a tag-less spool was assigned via the
    Bambuddy UI, the user's deterministic fallback tag is intentionally NOT
    written to Spoolman's extra.tag (kept clean per #1457), so
    find_spool_by_tag misses. The local spoolman_slot_assignments table is
    the authoritative binding for those spools.
    """
    from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

    async with async_session() as db:
        result = await db.execute(
            select(SpoolmanSlotAssignment.spoolman_spool_id).where(
                SpoolmanSlotAssignment.printer_id == printer_id,
                SpoolmanSlotAssignment.ams_id == ams_id,
                SpoolmanSlotAssignment.tray_id == tray_id,
            )
        )
        return result.scalar_one_or_none()


def _as_positive_number(value) -> float | None:
    """``value`` as a float when it is a usable positive quantity, else None.

    Rejects bools (``True`` is an int in Python, and ``float(True)`` is 1.0 --
    a weight of 1 g would price a spool per-gram at its whole cost), and
    rejects NaN and infinity, which compare False against every bound and would
    otherwise reach the archive as a NaN cost that no later comparison can
    clear.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _spool_cost_per_gram(spool: dict | None) -> float | None:
    """What one gram off this Spoolman spool costs, or None if it can't be said.

    Spoolman prices a spool in two places. ``filament.price`` is the catalogue
    figure for a full spool of that filament, and ``price`` on the spool itself
    overrides it when a particular purchase cost something else -- a sale, a
    different vendor, import duty. The spool's own value wins, which is the
    order the Spoolman UI presents them in.

    The divisor is ``filament.weight``: net filament grams, excluding the core.
    That is the same field the remain-delta path already divides by to turn a
    remain%% drop into grams, so a spool that can be charged by percentage can
    always be priced too.

    A missing or non-positive price is not a free spool, it is an unpriced one,
    and returns None so the caller can fall back to the global default rate
    rather than silently recording that this print cost nothing. Mirrors the
    ``cost_per_kg > 0`` guard the built-in inventory writer applies to its own
    per-spool rate.
    """
    if not isinstance(spool, dict):
        return None
    filament = spool.get("filament")
    if not isinstance(filament, dict):
        filament = {}

    # A spool-level 0 is treated as "not overridden" rather than "this roll was
    # free": Spoolman leaves the field null when unset, but an import or an API
    # client that writes 0 instead is common enough that reading it as free
    # would price a whole print at the default rate while a perfectly good
    # catalogue price sat one level down.
    raw_price = spool.get("price")
    if _as_positive_number(raw_price) is None:
        raw_price = filament.get("price")

    price = _as_positive_number(raw_price)
    weight = _as_positive_number(filament.get("weight"))
    if price is None or weight is None:
        return None
    # Both operands can be finite and the quotient still overflow. A non-finite
    # rate would reach the archive as a NaN or inf cost, and every later
    # comparison against it is False, so nothing downstream would correct it.
    rate = price / weight
    return rate if math.isfinite(rate) else None


@dataclass
class _PrintCost:
    """What a print cost, accumulated as each slot is actually charged.

    Only grams that were both charged to a spool *and* priced from it are
    counted. Everything else -- a slot whose spool has no price, a tray with no
    Spoolman row at all, filament the 3MF never attributed -- is left for the
    caller to cover at the global default rate, in one subtraction against the
    archive's own total. That is the same shape as the built-in inventory
    writer's untracked-grams top-up (#1344), and it means a partially priced
    print reports a whole-print figure rather than only the priced share.
    """

    cost: float = 0.0
    priced_grams: float = 0.0
    priced: int = 0
    unpriced: int = 0

    def add(self, grams: float, spool: dict | None, label: str) -> None:
        """Price ``grams`` off ``spool``. Call only after the charge succeeded."""
        if grams <= 0:
            return
        rate = _spool_cost_per_gram(spool)
        if rate is None:
            self.unpriced += 1
            logger.debug("[SPOOLMAN] %s: spool has no usable price, will fall back to the default rate", label)
            return
        self.cost += grams * rate
        self.priced_grams += grams
        self.priced += 1


async def _report_spool_usage_for_slots(
    client,
    filament_usage_items: list[tuple[int, float]],
    ams_trays: dict[int, dict],
    slot_to_tray: list | None,
    method_label: str,
    printer_serial: str = "",
    printer_id: int | None = None,
    slot_colors_out: dict[int, str] | None = None,
    slot_materials_out: dict[int, str] | None = None,
    cost_out: _PrintCost | None = None,
) -> int:
    """Report usage to Spoolman for a list of (slot_id, grams) pairs.

    Resolution order per slot: (1) Spoolman extra.tag match against the
    tray's RFID or deterministic fallback tag, (2) #1459 fallback —
    local spoolman_slot_assignments table keyed by (printer_id, ams_id,
    tray_id). Without (2), tag-less spools assigned via the Bambuddy UI
    never get their weight decremented because their extra.tag is empty
    on the Spoolman side.

    When ``slot_colors_out`` is provided it is populated with
    ``{slot_id: color_hex}`` for every resolved spool — used by
    :func:`report_usage` to stamp the archive's filament colour from the
    Spoolman spool rather than the slicer's 3MF value (#1494).

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

        spool_id_to_use: int | None = None
        resolution_path = ""
        # color_hex + material of the resolved spool's filament, for the #1494
        # archive colour rewrite and the #2563 type rewrite. The tag path
        # already has the full spool object; the slot-assignment path only
        # yields an id and is fetched below.
        spool_color_hex: str | None = None
        spool_material: str | None = None
        # Full spool row, kept so the price fields (#2591) can be read from the
        # same fetch the colour and material already pay for.
        spool_obj: dict | None = None

        spool_tag = _resolve_spool_tag(tray_info, printer_serial, global_tray_id)
        if spool_tag:
            spool = await client.find_spool_by_tag(spool_tag)
            if spool:
                spool_id_to_use = spool["id"]
                resolution_path = "tag"
                spool_obj = spool
                spool_color_hex = (spool.get("filament") or {}).get("color_hex")
                spool_material = (spool.get("filament") or {}).get("material")

        if spool_id_to_use is None and printer_id is not None:
            ams_id, tray_id = _global_tray_id_to_ams_slot(global_tray_id)
            spool_id_to_use = await _resolve_spool_id_via_slot_assignment(printer_id, ams_id, tray_id)
            if spool_id_to_use is not None:
                resolution_path = "slot-assignment"

        if spool_id_to_use is None:
            logger.debug(
                "[SPOOLMAN] Slot %s: no spool resolved (tag=%s, no slot-assignment)",
                slot_id,
                spool_tag[:16] if spool_tag else "none",
            )
            continue

        # Record the spool's filament colour + material for the archive
        # rewrites (#1494, #2563). The slot-assignment path resolved only an
        # id, so fetch the spool once for whichever value is still missing.
        # Strictly best-effort: a fetch failure must never abort the weight
        # reporting for the remaining slots, so the catch is broad.
        if slot_colors_out is not None or slot_materials_out is not None or cost_out is not None:
            need_color = slot_colors_out is not None and spool_color_hex is None
            need_material = slot_materials_out is not None and spool_material is None
            need_price = cost_out is not None and spool_obj is None
            if need_color or need_material or need_price:
                try:
                    spool_obj = await client.get_spool(spool_id_to_use)
                    _fil = spool_obj.get("filament") or {}
                    if need_color:
                        spool_color_hex = _fil.get("color_hex")
                    if need_material:
                        spool_material = _fil.get("material")
                except Exception as exc:  # noqa: BLE001 — colour/material/price are non-critical
                    logger.debug("[SPOOLMAN] Slot %s: could not fetch spool filament: %s", slot_id, exc)
            if slot_colors_out is not None and spool_color_hex:
                slot_colors_out[slot_id] = spool_color_hex
            if slot_materials_out is not None and spool_material:
                slot_materials_out[slot_id] = spool_material

        try:
            await client.use_spool(spool_id_to_use, grams_used)
            logger.info(
                "[SPOOLMAN] %s: slot %s: %sg -> spool %s (via %s)",
                method_label,
                slot_id,
                grams_used,
                spool_id_to_use,
                resolution_path,
            )
            spools_updated += 1
            # Priced only after the charge landed, so a spool Spoolman refused
            # cannot contribute to what the print is said to have cost.
            if cost_out is not None:
                cost_out.add(grams_used, spool_obj, f"Slot {slot_id}")
        except (SpoolmanNotFoundError, SpoolmanClientError, SpoolmanUnavailableError) as exc:
            logger.warning("[SPOOLMAN] Failed to record usage for spool %s: %s", spool_id_to_use, exc)

    return spools_updated


async def _report_spool_usage_split_by_tray_changes(
    client,
    filament_usage: list[dict],
    tray_changes: list[tuple[int, int]],
    ams_trays: dict[int, dict],
    layer_usage: dict[int, dict[int, float]] | None,
    filament_properties: dict | None,
    total_layers: int,
    last_layer_num: int,
    method_label: str,
    printer_serial: str,
    printer_id: int,
    slot_colors_out: dict[int, str] | None = None,
    slot_materials_out: dict[int, str] | None = None,
    cost_out: _PrintCost | None = None,
) -> tuple[int, set[int]]:
    """Split each slot's grams across ``tray_changes`` and charge per-segment.

    Mirrors ``usage_tracker`` Path 1's tray-switch branch so Spoolman and
    the internal Spool inventory attribute mid-print AMS-backup switches
    identically (#1793 — reporter's origin spool was over-charged the
    whole print because this path didn't exist). ``compute_tray_split_grams``
    holds the shared segment-math; this function wraps the per-segment
    spool resolution + ``use_spool`` sink for the Spoolman side.

    Returns ``(spools_updated, handled_global_tray_ids)`` — the caller
    passes ``handled_global_tray_ids`` into the remain-delta fallback so
    a tray attributed here is not double-charged there.
    """
    from backend.app.utils.tray_split import compute_tray_split_grams

    spools_updated = 0
    handled_global_tray_ids: set[int] = set()

    for usage in filament_usage:
        slot_id = usage.get("slot_id", 0)
        total_weight = usage.get("used_g", 0)
        if total_weight <= 0 or slot_id <= 0:
            continue

        props = (filament_properties or {}).get(str(slot_id)) or (filament_properties or {}).get(slot_id) or {}
        segments = compute_tray_split_grams(
            tray_changes=tray_changes,
            total_weight=float(total_weight),
            slot_id=slot_id,
            layer_usage=layer_usage,
            density=float(props.get("density", 1.24)),
            diameter=float(props.get("diameter", 1.75)),
            total_layers=total_layers,
            last_layer_num=last_layer_num,
        )

        for seg_idx, tray_global, segment_grams in segments:
            if segment_grams <= 0:
                continue

            # Mark this tray as handled BEFORE the resolution attempt so
            # remain-delta doesn't double-charge it, even if we fail to
            # find a spool below. Matches usage_tracker behaviour: the
            # tray was physically fed from during this print, whether or
            # not Spoolman happens to have a matching row.
            handled_global_tray_ids.add(tray_global)

            tray_info = ams_trays.get(tray_global) or {}
            spool_id_to_use: int | None = None
            resolution_path = ""
            spool_color_hex: str | None = None
            spool_material: str | None = None
            spool_obj: dict | None = None

            spool_tag = _resolve_spool_tag(tray_info, printer_serial, tray_global) if tray_info else ""
            if spool_tag:
                spool = await client.find_spool_by_tag(spool_tag)
                if spool:
                    spool_id_to_use = spool["id"]
                    resolution_path = "tag"
                    spool_obj = spool
                    spool_color_hex = (spool.get("filament") or {}).get("color_hex")
                    spool_material = (spool.get("filament") or {}).get("material")

            if spool_id_to_use is None:
                seg_ams_id, seg_tray_id = _global_tray_id_to_ams_slot(tray_global)
                spool_id_to_use = await _resolve_spool_id_via_slot_assignment(printer_id, seg_ams_id, seg_tray_id)
                if spool_id_to_use is not None:
                    resolution_path = "slot-assignment"

            if spool_id_to_use is None:
                logger.info(
                    "[SPOOLMAN] Split slot %s seg %s tray=%d: no spool resolved — %.2fg lost from split accounting",
                    slot_id,
                    seg_idx,
                    tray_global,
                    segment_grams,
                )
                continue

            # Colour (#1494) + material (#2563) rewrite — first segment for a
            # slot wins. The UI displays a single colour/type per slot, so
            # later segments on the same slot don't overwrite (a backup swap
            # can differ but the archive card stays consistent with the origin).
            need_color = slot_colors_out is not None and slot_id not in slot_colors_out and spool_color_hex is None
            need_material = (
                slot_materials_out is not None and slot_id not in slot_materials_out and spool_material is None
            )
            # Unlike the colour, every segment needs its own price: each was
            # charged to its own spool, and a backup roll can have cost
            # something different from the one it replaced.
            need_price = cost_out is not None and spool_obj is None
            if need_color or need_material or need_price:
                try:
                    spool_obj = await client.get_spool(spool_id_to_use)
                    _fil = spool_obj.get("filament") or {}
                    if need_color:
                        spool_color_hex = _fil.get("color_hex")
                    if need_material:
                        spool_material = _fil.get("material")
                except Exception as exc:  # noqa: BLE001 — colour/material/price are non-critical
                    logger.debug("[SPOOLMAN] Split slot %s: could not fetch spool filament: %s", slot_id, exc)
            if slot_colors_out is not None and slot_id not in slot_colors_out and spool_color_hex:
                slot_colors_out[slot_id] = spool_color_hex
            if slot_materials_out is not None and slot_id not in slot_materials_out and spool_material:
                slot_materials_out[slot_id] = spool_material

            try:
                await client.use_spool(spool_id_to_use, round(segment_grams, 2))
                logger.info(
                    "[SPOOLMAN] %s: slot %s seg %s tray=%d: %.2fg -> spool %s (via %s)",
                    method_label,
                    slot_id,
                    seg_idx,
                    tray_global,
                    segment_grams,
                    spool_id_to_use,
                    resolution_path,
                )
                spools_updated += 1
                if cost_out is not None:
                    cost_out.add(round(segment_grams, 2), spool_obj, f"Split slot {slot_id} seg {seg_idx}")
            except (SpoolmanNotFoundError, SpoolmanClientError, SpoolmanUnavailableError) as exc:
                logger.warning(
                    "[SPOOLMAN] Split slot %s seg %s: failed to record usage for spool %s: %s",
                    slot_id,
                    seg_idx,
                    spool_id_to_use,
                    exc,
                )

    return spools_updated, handled_global_tray_ids


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
    tray_remain_start = tracking.tray_remain_start or {}
    printer_serial = await _get_printer_serial(printer_id)

    client = await _get_spoolman_client_with_fallback()
    if not client:
        logger.warning("[SPOOLMAN] Not reachable for partial usage reporting")
        return

    # No-3MF aborted print (#1820 mirror of the completion path): nothing in
    # filament_usage or layer_usage to base partial estimates on, but the
    # remain%-delta snapshot we captured at start still describes consumption
    # up to the abort moment. Write it the same way report_usage's fallback
    # does, then return — there's no 3MF-derived partial to layer on top.
    # ``state`` was already fetched at the top of the function for current_layer.
    if not filament_usage and not layer_usage and tray_remain_start:
        current_lookup = _snapshot_tray_remain(state.raw_data) if state and state.raw_data else {}
        await _report_remain_delta_for_slots(
            client,
            printer_id=printer_id,
            tray_remain_start=tray_remain_start,
            current_lookup=current_lookup,
            handled_global_tray_ids=set(),
            archive_id=getattr(tracking, "archive_id", -1),
            print_used_keys=_print_used_tray_keys(slot_to_tray, getattr(tracking, "tray_now_at_start", None), state),
        )
        return

    # Same recovery the completion path does, for the same reason: a print
    # dispatched from Studio over the cloud left print start with no mapping to
    # store, and both paths below feed ``slot_to_tray`` to
    # ``_resolve_global_tray_id`` (#2768). An aborted print charges the wrong
    # spool just as readily as a finished one.
    if not slot_to_tray:
        slot_to_tray, _partial_mapping_source = _resolve_slot_to_tray_fallback(
            printer_id,
            filament_usage,
            getattr(tracking, "tray_now_at_start", None),
        )
        logger.info(
            "[SPOOLMAN] Partial usage: slot_to_tray=%s (source: %s)",
            slot_to_tray,
            _partial_mapping_source,
        )

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
                client,
                usage_items,
                ams_trays,
                slot_to_tray,
                "Partial (G-code)",
                printer_serial,
                printer_id=printer_id,
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
        client,
        usage_items,
        ams_trays,
        slot_to_tray,
        "Partial (linear)",
        printer_serial,
        printer_id=printer_id,
    )
    if spools_updated > 0:
        logger.info("[SPOOLMAN] Reported partial usage to %s spool(s) using linear interpolation", spools_updated)


async def report_usage(printer_id: int, archive_id: int):
    """Report filament usage to Spoolman after print completion.

    Two writers, mirroring the internal-inventory split in usage_tracker:

    1. **3MF path (primary)** — per-filament slice estimates captured at
       print start drive a precise per-slot ``use_spool`` call.
    2. **AMS remain%-delta (fallback)** — for slots the 3MF path didn't
       handle (including the no-3MF "Untitled" case from #1820): compute
       ``start_remain - current_remain``, multiply by the resolved
       Spoolman filament's reference weight, and write the delta. Mirrors
       ``usage_tracker.on_print_complete`` Path 2 (line 517).
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
        tray_remain_start = tracking.tray_remain_start or {}
        # ``layer_usage`` and ``filament_properties`` were added later than
        # the base tracking fields; use ``getattr`` so tests that stub
        # ``tracking`` as a lightweight SimpleNamespace stay valid, and
        # historic ORM rows loaded without these columns can't AttributeError
        # on read.
        layer_usage_raw = getattr(tracking, "layer_usage", None) or {}
        filament_properties = getattr(tracking, "filament_properties", None) or {}
        tray_now_at_start = getattr(tracking, "tray_now_at_start", None)
        printer_serial = await _get_printer_serial(printer_id)

        # Delete tracking row (we're done with it)
        await db.delete(tracking)
        await db.commit()

        if not filament_usage and not tray_remain_start:
            logger.debug("[SPOOLMAN] No usage data or remain-snapshot for archive %s", archive_id)
            return

        # Check if Spoolman is enabled
        spoolman_enabled = await get_setting(db, "spoolman_enabled")
        if not spoolman_enabled or spoolman_enabled.lower() != "true":
            return

        client = await _get_spoolman_client_with_fallback()
        if not client:
            logger.warning("[SPOOLMAN] Not reachable for usage reporting")
            return

        # Consult the live printer state for the tray-change log written by
        # ``bambu_mqtt.py`` on every mid-print ``tray_now`` change (#957).
        # When there's more than one entry, the print traversed >1 AMS tray
        # and the split path attributes each segment to the tray that was
        # loaded at the time — matches the internal Spool inventory writer
        # in ``usage_tracker.py``. Without this, an AMS-backup runout switch
        # charges the whole slot to the origin spool and pushes it past
        # ``initial_weight`` (#1793).
        #
        # Split only for SINGLE-slot prints — same gate as
        # ``usage_tracker.py:1002``. Multi-slot (multi-colour) prints
        # naturally cycle trays for every colour change, so splitting each
        # slot's grams across ALL tray_change_log entries would attribute
        # slot 1's grams to the segments where slot 2's tray was loaded and
        # vice versa. Multi-slot prints fall through to the existing
        # single-tray path (which uses the stable ``slot_to_tray`` mapping).
        nonzero_slots = [u for u in filament_usage if u.get("used_g", 0) > 0]
        tray_changes: list[tuple[int, int]] = []
        _state = None
        if len(nonzero_slots) == 1:
            from backend.app.services.printer_manager import printer_manager as _pm

            _state = _pm.get_status(printer_id)
            if _state is not None:
                tray_changes = list(getattr(_state, "tray_change_log", []) or [])
        _total_layers = int(getattr(_state, "total_layers", 0) or 0) if _state else 0
        _current_layer = int(getattr(_state, "layer_num", 0) or 0) if _state else 0
        # For the linear-fallback denominator when total_layers is 0 (P1S
        # firmware resets it at print end). At completion the current layer
        # is the print's last valid layer.
        _layer_denom_hint = _total_layers or _current_layer

        # Recover the mapping when print start had nothing to store — the
        # cloud-dispatched Studio print of #2768. Only the 3MF path consumes
        # ``slot_to_tray``; the remain-delta path below resolves spools from the
        # AMS slot directly, so there is nothing to recover for it.
        mapping_source = "stored" if slot_to_tray else "none"
        if filament_usage and not slot_to_tray:
            slot_to_tray, mapping_source = _resolve_slot_to_tray_fallback(printer_id, filament_usage, tray_now_at_start)
        logger.info(
            "[SPOOLMAN] Archive %s: slot_to_tray=%s (source: %s)",
            archive_id,
            slot_to_tray,
            mapping_source,
        )
        # Nothing named a tray for this print and no fallback could recover
        # one, so every slot is about to be resolved by position -- slicer slot
        # 1 to the first loaded tray, and so on. That guess is right for an AMS
        # loaded in slicer order and wrong for any other, and the caller has no
        # way to tell which it got. Say so at a level that survives the default
        # log filter, so a support bundle carries the reason (#2953).
        #
        # Excludes the tray-split path. It never reads ``slot_to_tray`` at all:
        # it charges each segment to the tray the printer announced switching
        # to, which is the same evidence the tray-state fallback is built on and
        # is not a guess. Calling it one would suppress the archive rewrite for
        # exactly the prints -- an AMS-backup runout on a Studio job (#1793 in
        # #2768's conditions) -- whose attribution is best supported.
        mapping_is_guess = bool(filament_usage) and mapping_source == "none" and len(tray_changes) <= 1
        if mapping_is_guess:
            logger.warning(
                "[SPOOLMAN] Archive %s: no slot-to-tray mapping from any source -- "
                "charging by tray position, which is a guess. Verify the spool weights "
                "if the AMS is not loaded in slicer order.",
                archive_id,
            )

        slot_colors: dict[int, str] = {}
        slot_materials: dict[int, str] = {}
        # Priced as each charge lands, so the figure the archive ends up with
        # describes the same grams Spoolman actually had deducted (#2591).
        print_cost = _PrintCost()
        handled_global_tray_ids: set[int] = set()
        spools_updated = 0

        # --- Path 1: 3MF per-slot estimates -----------------------------
        if filament_usage:
            if len(tray_changes) > 1:
                # Tray-split path — attribute per-segment to the tray that
                # was loaded at that time.
                logger.info(
                    "[SPOOLMAN] Reporting per-filament usage for archive %s with tray-split "
                    "(tray_change_log=%s, denom_layers=%d)",
                    archive_id,
                    tray_changes,
                    _layer_denom_hint,
                )
                # ``tracking.layer_usage`` was serialized to JSON so int keys
                # come back as strings. Restore them for the split math.
                layer_usage = None
                if layer_usage_raw:
                    try:
                        layer_usage = {
                            int(layer): {int(fid): mm for fid, mm in filaments.items()}
                            for layer, filaments in layer_usage_raw.items()
                        }
                    except (TypeError, ValueError, AttributeError):
                        # AttributeError catches ``inner.items()`` when the
                        # inner value isn't dict-shaped (corrupt JSON row).
                        # Missing gcode falls through to the linear-ratio
                        # branch inside ``compute_tray_split_grams`` — still
                        # gives a correct split, just less precise.
                        layer_usage = None
                split_updated, split_handled = await _report_spool_usage_split_by_tray_changes(
                    client,
                    filament_usage,
                    tray_changes,
                    ams_trays,
                    layer_usage,
                    filament_properties,
                    _total_layers,
                    _layer_denom_hint,
                    f"Archive {archive_id}",
                    printer_serial,
                    printer_id=printer_id,
                    slot_colors_out=slot_colors,
                    slot_materials_out=slot_materials,
                    cost_out=print_cost,
                )
                spools_updated += split_updated
                handled_global_tray_ids |= split_handled
            else:
                logger.info("[SPOOLMAN] Reporting per-filament usage for archive %s", archive_id)
                usage_items = [(u.get("slot_id", 0), u.get("used_g", 0)) for u in filament_usage]
                spools_updated = await _report_spool_usage_for_slots(
                    client,
                    usage_items,
                    ams_trays,
                    slot_to_tray,
                    f"Archive {archive_id}",
                    printer_serial,
                    printer_id=printer_id,
                    slot_colors_out=slot_colors,
                    slot_materials_out=slot_materials,
                    cost_out=print_cost,
                )
                # Track which physical slots the 3MF path already covered so
                # Path 2 doesn't double-charge them.
                for u in filament_usage:
                    if u.get("used_g", 0) <= 0:
                        # ``_report_spool_usage_for_slots`` skipped this slot
                        # before resolving a tray for it, so nothing was
                        # charged and Path 2 is free to cover the slot from
                        # remain% -- claiming it here would suppress a real
                        # drop on the strength of a zero-gram estimate.
                        continue
                    slot_id = u.get("slot_id", 0)
                    handled_global_tray_ids.add(_resolve_global_tray_id(slot_id, slot_to_tray, ams_trays))

        # --- Path 2: AMS remain%-delta for slots 3MF didn't cover -------
        # Triggered for no-3MF "Untitled" prints (#1820) AND for partial
        # 3MF coverage (slots whose filament_id wasn't in slice_info).
        if tray_remain_start:
            from backend.app.services.printer_manager import printer_manager

            current = printer_manager.get_status(printer_id)
            current_lookup = _snapshot_tray_remain(current.raw_data) if current and current.raw_data else {}
            fallback_updates = await _report_remain_delta_for_slots(
                client,
                printer_id=printer_id,
                tray_remain_start=tray_remain_start,
                current_lookup=current_lookup,
                handled_global_tray_ids=handled_global_tray_ids,
                archive_id=archive_id,
                print_used_keys=_print_used_tray_keys(slot_to_tray, tray_now_at_start, current),
                slot_colors_out=slot_colors,
                slot_materials_out=slot_materials,
                cost_out=print_cost,
            )
            spools_updated += fallback_updates

        if spools_updated == 0:
            logger.info("[SPOOLMAN] Archive %s: no spools updated", archive_id)
        else:
            logger.info("[SPOOLMAN] Archive %s: updated %s spool(s)", archive_id, spools_updated)

        # Stamp the archive's filament colour from the matched Spoolman spools
        # so it reflects the curated inventory colour, not the slicer's 3MF
        # value (#1494) — mirrors the built-in inventory path in usage_tracker.
        #
        # Skipped when the mapping was a positional guess. Charging the wrong
        # spool costs grams the owner can put back; rewriting the archive's
        # colour and material on top of it overwrites what the slicer actually
        # recorded, and the print then reads as a different filament than the
        # one that made it, with nothing left to compare against (#2953).
        if mapping_is_guess:
            if slot_colors or slot_materials:
                logger.info(
                    "[SPOOLMAN] Archive %s: leaving filament colour/type as sliced — "
                    "the spools were matched by position, not by a known mapping",
                    archive_id,
                )
        else:
            await _apply_spool_colors_to_archive(db, archive_id, filament_usage, slot_colors)

            # Same for the material: a slot mapped to a differently-typed spool
            # than it was sliced for otherwise records the sliced type (#2563).
            await _apply_spool_types_to_archive(db, archive_id, filament_usage, slot_materials)

        # Cost is applied whether or not the mapping was a guess, unlike the
        # colour and material above. Those overwrite what the slicer recorded,
        # which is why a guess must not touch them; the cost has no such
        # original -- archive.py's figure is itself derived from a default rate
        # -- and the grams have already been deducted from these spools, so the
        # archive should say what that deduction was worth.
        await _apply_spool_cost_to_archive(db, archive_id, print_cost)


def _print_used_tray_keys(
    slot_to_tray: list | None,
    tray_now_at_start: int | None,
    state,
) -> set[tuple[int, int]]:
    """Which AMS slots this print actually drew from, as far as we can tell.

    Mirrors the guard the internal tracker has carried since #1269. Without
    it, swapping a spool in a slot the print never touched drops that slot's
    ``remain%``, and the remain-delta path reads the drop as consumption and
    charges it to whoever the slot is assigned to. That is a phantom write to
    an uninvolved spool, and it is likeliest on exactly the prints this
    fallback serves -- ones with no 3MF, where nothing else limits which slots
    are considered.

    Three sources, matching the internal tracker's:

    - the print's ``ams_mapping``, stored here as ``slot_to_tray``;
    - every tray the printer switched to mid-print;
    - the tray it was drawing from at the start.

    An empty result means no evidence, not "no slots" -- callers must then
    consider every slot, as before, or a printer that reports none of the
    three would silently stop being tracked at all.

    Takes the two stored values rather than the tracking row: the caller
    deletes that row before it gets this far, and everything read off it is
    read into locals beforehand.
    """
    keys: set[tuple[int, int]] = set()
    for global_tray_id in list(slot_to_tray or []):
        if isinstance(global_tray_id, int) and global_tray_id >= 0:
            keys.add(_global_tray_id_to_ams_slot(global_tray_id))
    for change in getattr(state, "tray_change_log", None) or []:
        if isinstance(change, (tuple, list)) and change:
            global_tray_id = change[0]
            if isinstance(global_tray_id, int) and global_tray_id >= 0:
                keys.add(_global_tray_id_to_ams_slot(global_tray_id))
    if isinstance(tray_now_at_start, int) and 0 <= tray_now_at_start <= _MAX_REAL_TRAY_ID:
        keys.add(_global_tray_id_to_ams_slot(tray_now_at_start))
    return keys


async def _report_remain_delta_for_slots(
    client,
    *,
    printer_id: int,
    tray_remain_start: dict[str, dict],
    current_lookup: dict[str, dict],
    handled_global_tray_ids: set[int],
    archive_id: int,
    print_used_keys: set[tuple[int, int]] | None = None,
    slot_colors_out: dict[int, str] | None = None,
    slot_materials_out: dict[int, str] | None = None,
    cost_out: _PrintCost | None = None,
) -> int:
    """AMS remain%-delta path: write ``(start - current) * filament.weight``
    grams to Spoolman for slots the 3MF path didn't cover.

    Mirrors ``usage_tracker.on_print_complete`` Path 2: per-slot, gated on a
    valid current ``remain%``, skipped on spool swap (``tray_uuid`` changed),
    using the resolved spool's filament reference weight rather than MQTT's
    unreliable ``tray_weight`` (which is the failure mode #1119 documented).
    """
    spools_updated = 0
    not_in_print: list[str] = []
    for slot_key, start in tray_remain_start.items():
        try:
            ams_id_str, tray_id_str = slot_key.split("-", 1)
            ams_id, tray_id = int(ams_id_str), int(tray_id_str)
        except (ValueError, AttributeError):
            continue

        # Skip slots already handled by the 3MF path. Encoding mirrors
        # build_ams_tray_lookup: VT trays land at 254/255, AMS-HT keeps
        # its native id (>=128), regular AMS slots are ams_id*4+tray_id.
        if ams_id == 255:
            global_tray_id = 254 + tray_id
        elif ams_id >= 128:
            global_tray_id = ams_id
        else:
            global_tray_id = ams_id * 4 + tray_id
        if global_tray_id in handled_global_tray_ids:
            continue

        # Slots the print never touched (#1269's guard, see _print_used_tray_keys).
        # Only enforced when there is evidence of which slots it did use.
        # Collected rather than logged per slot: on a four-AMS farm a
        # single-colour print leaves fifteen of these, and they are the
        # expected case, unlike the "consumed but charged nothing" lines below.
        if print_used_keys and (ams_id, tray_id) not in print_used_keys:
            not_in_print.append(f"AMS{ams_id}-T{tray_id}")
            continue

        current = current_lookup.get(slot_key)
        if not current:
            # Reported at info, like the internal tracker's equivalent: on a
            # near-empty spool the AMS reports a negative remain%, which the
            # snapshot gate rejects, and the slot that was actually printing
            # disappears from this path entirely (#1820).
            logger.info(
                "[SPOOLMAN] AMS%d-T%d: no valid remain%% at completion, nothing charged for this slot", ams_id, tray_id
            )
            continue

        # Spool swap mid-print — tray_uuid changed. We don't know how much
        # of the print went to which spool; skip rather than mis-attribute.
        start_uuid = (start.get("tray_uuid") or "").lower()
        cur_uuid = (current.get("tray_uuid") or "").lower()
        if start_uuid and cur_uuid and start_uuid != cur_uuid:
            logger.info(
                "[SPOOLMAN] AMS%d-T%d: spool swapped mid-print (uuid changed), skipping remain-delta", ams_id, tray_id
            )
            continue

        delta_pct = start["remain"] - current["remain"]
        if delta_pct <= 0:
            # A fresh spool reads 100% for the first tens of grams and the AMS
            # estimate drifts upward on its own, so this covers a real print
            # that simply left no trace at AMS granularity -- not only a refill.
            # Said out loud so it can be told apart from having nothing to
            # charge, which is what "no spools updated" alone looked like.
            logger.info(
                "[SPOOLMAN] AMS%d-T%d: remain%% did not fall over the print (%d%% -> %d%%), nothing charged",
                ams_id,
                tray_id,
                start["remain"],
                current["remain"],
            )
            continue  # No consumption captured at AMS granularity, or refilled

        spool_id = await _resolve_spool_id_via_slot_assignment(printer_id, ams_id, tray_id)
        if spool_id is None:
            logger.info(
                "[SPOOLMAN] AMS%d-T%d: consumed %d%% but has no Spoolman slot assignment, nothing charged",
                ams_id,
                tray_id,
                delta_pct,
            )
            continue

        # Look up the spool's filament reference weight. Use a fresh GET so
        # we don't depend on a stale cached_spools list. Failure here is
        # silent-skip rather than fatal — other slots can still be written.
        try:
            spool = await client.get_spool(spool_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[SPOOLMAN] AMS%d-T%d: get_spool(%s) failed: %s", ams_id, tray_id, spool_id, exc)
            continue
        filament = spool.get("filament") or {}
        ref_weight = filament.get("weight")
        if not ref_weight or ref_weight <= 0:
            logger.debug(
                "[SPOOLMAN] AMS%d-T%d: spool %s has no filament.weight, skipping remain-delta",
                ams_id,
                tray_id,
                spool_id,
            )
            continue

        grams_used = round((delta_pct / 100.0) * ref_weight, 2)
        if grams_used <= 0:
            continue
        try:
            await client.use_spool(spool_id, grams_used)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SPOOLMAN] AMS%d-T%d: use_spool(%s, %.2fg) failed: %s", ams_id, tray_id, spool_id, grams_used, exc
            )
            continue

        spools_updated += 1
        # ``spool`` here is the full row fetched above for its filament weight,
        # so the price is already in hand (#2591).
        if cost_out is not None:
            cost_out.add(grams_used, spool, f"AMS{ams_id}-T{tray_id}")
        # No 3MF slot_id for this path — use the AMS slot key so the maps can
        # still be inspected by callers if needed. The archive rewrites
        # (#1494 colour, #2563 type) key on 3MF slot_ids, so remain-delta-only
        # prints intentionally don't participate (matches usage_tracker's
        # slot_id=None).
        if slot_colors_out is not None:
            color = filament.get("color_hex")
            if color:
                slot_colors_out[-(global_tray_id + 1)] = color
        if slot_materials_out is not None:
            material = filament.get("material")
            if material:
                slot_materials_out[-(global_tray_id + 1)] = material
        logger.info(
            "[SPOOLMAN] Archive %s AMS%d-T%d: %.2fg via remain-delta (%d%% of %.0fg) -> spool %s",
            archive_id,
            ams_id,
            tray_id,
            grams_used,
            delta_pct,
            ref_weight,
            spool_id,
        )
    if not_in_print:
        logger.info(
            "[SPOOLMAN] Archive %s: slots not part of this print, left alone: %s",
            archive_id,
            ", ".join(not_in_print),
        )
    return spools_updated


async def _apply_spool_cost_to_archive(db, archive_id: int, print_cost: _PrintCost) -> None:
    """Set an archive's cost from what the Spoolman spools that fed it are worth (#2591).

    Until now this was the one thing the Spoolman integration was asked for by
    name and did not do. ``archive.py`` prices a print once, at archive time,
    from the built-in Filament catalogue matched on the primary type, falling
    back to the global default rate -- and in Spoolman mode nothing ever
    revisited that figure, because the per-spool recompute in
    ``usage_tracker.on_print_complete`` only runs over rows the built-in
    inventory wrote and Spoolman mode writes none. An install with an empty
    catalogue therefore priced every print at the default no matter what the
    linked spool actually cost.

    Multi-material was wrong twice over there: the primary type's rate applied
    to the *whole* print's grams, so a slot of expensive PA came out at the
    price of the PLA next to it. Summing per charged slot is what fixes that,
    and it falls out of pricing each charge as it is made rather than pricing a
    total afterwards.

    Grams that could not be priced are covered at the global default rate in a
    single subtraction against the archive's own total -- a slot whose spool has
    no price, a tray with no Spoolman row, and filament the 3MF never attributed
    are all the same case. Without it a print with one priced slot out of four
    would report a quarter of its cost, which is #1344 in a different inventory
    mode.

    Only on the first run, matching the built-in writer (#1378): reprint actuals
    live in ``PrintLogEntry``, and the archive card keeps the first run's figure
    so a failed 10 g reprint doesn't visually clobber a successful 100 g print.

    Does nothing when no slot could be priced, leaving whatever ``archive.py``
    recorded. That keeps an install with prices in neither place exactly where
    it was.
    """
    if print_cost.priced == 0:
        if print_cost.unpriced:
            logger.info(
                "[SPOOLMAN] Archive %s: %d charged spool(s) carry no price -- "
                "leaving the cost as recorded at archive time",
                archive_id,
                print_cost.unpriced,
            )
        return

    from sqlalchemy import func

    from backend.app.api.routes.settings import get_setting
    from backend.app.models.archive import PrintArchive
    from backend.app.models.print_log import PrintLogEntry

    archive = (await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))).scalar_one_or_none()
    if archive is None:
        return

    total = print_cost.cost
    archive_grams = archive.filament_used_grams or 0
    unpriced_grams = max(0.0, archive_grams - print_cost.priced_grams)
    if unpriced_grams > 0:
        # Malformed settings must not cost the whole usage report; the rate is
        # the least important thing this pass produces.
        try:
            _setting = await get_setting(db, "default_filament_cost")
            default_cost_per_kg = float(_setting) if _setting else 25.0
        except (TypeError, ValueError):
            default_cost_per_kg = 25.0
        if default_cost_per_kg > 0:
            total += (unpriced_grams / 1000.0) * default_cost_per_kg

    if total <= 0:
        return

    existing_runs = (
        await db.execute(select(func.count(PrintLogEntry.id)).where(PrintLogEntry.archive_id == archive_id))
    ).scalar()
    if existing_runs:
        return

    new_cost = round(total, 2)
    if new_cost != archive.cost:
        logger.info(
            "[SPOOLMAN] Archive %s cost %s -> %s (%d slot(s) priced from Spoolman over %.2fg, "
            "%.2fg at the default rate)",
            archive_id,
            archive.cost,
            new_cost,
            print_cost.priced,
            print_cost.priced_grams,
            unpriced_grams,
        )
        archive.cost = new_cost
        await db.commit()


async def _apply_spool_colors_to_archive(
    db,
    archive_id: int,
    filament_usage: list[dict],
    slot_colors: dict[int, str],
) -> None:
    """Overwrite an archive's ``filament_color`` with the colours of the
    Spoolman spools that fed the print (#1494).

    All-or-nothing, exactly like the built-in inventory path: the colour is
    only rewritten when every used slot resolved to a spool that carries a
    colour, so a partial match never drops slots from the archive.
    """
    if not slot_colors:
        return

    from backend.app.models.archive import PrintArchive
    from backend.app.services.usage_tracker import (
        _archive_colors_from_spools,
        _spool_color_to_hex,
    )

    results = [{"slot_id": sid, "color": _spool_color_to_hex(hex_)} for sid, hex_ in slot_colors.items()]
    colors = _archive_colors_from_spools(filament_usage, results)
    if not colors:
        return

    archive = (await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))).scalar_one_or_none()
    if archive is None:
        return

    joined = ",".join(colors)
    if joined != archive.filament_color:
        logger.info(
            "[SPOOLMAN] Archive %s filament_color %r -> %r (from Spoolman spools)",
            archive_id,
            archive.filament_color,
            joined,
        )
        archive.filament_color = joined
        await db.commit()


async def _apply_spool_types_to_archive(
    db,
    archive_id: int,
    filament_usage: list[dict],
    slot_materials: dict[int, str],
) -> None:
    """Overwrite an archive's ``filament_type`` with the materials of the
    Spoolman spools that fed the print (#2563).

    All-or-nothing, exactly like the colour path and the built-in inventory
    path: the type is only rewritten when every used slot resolved to a spool
    that carries a material, so a partial match never drops slots from the
    archive or the material statistics.
    """
    if not slot_materials:
        return

    from backend.app.models.archive import PrintArchive
    from backend.app.services.usage_tracker import _archive_types_from_spools

    results = [{"slot_id": sid, "material": material} for sid, material in slot_materials.items()]
    types = _archive_types_from_spools(filament_usage, results)
    if not types:
        return

    archive = (await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))).scalar_one_or_none()
    if archive is None:
        return

    joined = ",".join(types)
    if joined != archive.filament_type:
        logger.info(
            "[SPOOLMAN] Archive %s filament_type %r -> %r (from Spoolman spools)",
            archive_id,
            archive.filament_type,
            joined,
        )
        archive.filament_type = joined
        await db.commit()
