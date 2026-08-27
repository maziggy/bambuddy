"""Bambu Lab MQTT communication service.

IMPORTANT: Always use qos=1 for all MQTT publish calls!
The printer ignores qos=0 messages when busy broadcasting status updates.
Using qos=1 ensures the printer acknowledges and processes our commands immediately.
This was discovered when K-profile requests with qos=0 took 20-30 seconds,
but with qos=1 they respond instantly.
"""

import asyncio
import json
import logging
import os
import ssl
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from backend.app.services.hms_actions import HMSAction, get_actions_for_error_code
from backend.app.services.hms_errors import describe_fault
from backend.app.utils.ams_drying import ACTIVE_DRY_STATUSES

logger = logging.getLogger(__name__)

# AMS module name prefixes used in get_version responses.
# The numeric suffix after '/' is the AMS unit ID as reported in push_status.
#   "ams/<id>"  – original AMS (X1C, X1E, P1S, …)
#   "n3f/<id>"  – AMS 2 Pro (H2D Pro and similar)
#   "n3s/<id>"  – AMS HT (H2D Pro and similar; IDs typically start at 128)
_AMS_MODULE_PREFIXES = ("ams/", "n3f/", "n3s/")

# gcode_state values that mean the printer is not idle and must not be handed a
# new start-print (#2598). The firmware rejects a project_file while busy with
# 0500_4004 "Device is busy and cannot start a new task", and on some models
# (A1 mini reported) that error cancels the RUNNING job. IDLE / FINISH / FAILED
# are valid start targets and are deliberately excluded. Mirrors
# printer_manager.ACTIVE_PRINT_STATES and print_scheduler._ACTIVE_PRINT_STATES.
_ACTIVE_PRINT_STATES = frozenset({"PREPARE", "SLICING", "RUNNING", "PAUSE"})

# A drying cycle that runs to term ends with its countdown all but exhausted, so
# the last dry_time we saw before the drop to 0 tells us whether the firmware
# ended the cycle on schedule or aborted it. More than this many minutes still on
# the clock means it was cut short, and the firmware's own reason codes are worth
# capturing at INFO — #2770 aborted a 12-hour cycle 20 minutes in (700 minutes
# left), and the log said only "drying complete", so the report carried no
# evidence of why. The margin absorbs a stale last observation between AMS
# pushes; it is not a judgement about how short "short" is.
_EARLY_DRY_END_MINUTES = 5

# CONNACK reason codes that mean the printer actively refused our credentials,
# as opposed to being unreachable or busy. Bambu speaks MQTT 3.1.1, whose
# single-byte CONNACK return codes paho maps onto the v5 reason-code space:
# return code 4 ("bad user name or password") -> 134, and 5 ("not authorized")
# -> 135. Both mean the same thing in practice for a Bambu printer: the access
# code (or, on some firmware, the serial used as the username) is wrong.
_CONNACK_AUTH_REJECTED = frozenset({134, 135})

# Short, stable slugs recorded on the client and surfaced to the connection
# diagnostic as a `params.reason` variant. Deliberately not free text — the
# frontend picks a localized message key off these.
CONNECT_ERROR_AUTH_REJECTED = "auth_rejected"
CONNECT_ERROR_REFUSED = "refused"


def parse_ams_filament_backup_from_cfg(cfg_raw: object) -> bool | None:
    """Extract AMS Filament Backup state from a Bambu push_status ``print.cfg`` value.

    OrcaSlicer reads bit 18 of the hex string via
    ``get_flag_bits(cfg, 18)`` (DeviceManager.cpp:4961). Old-protocol families
    (A1 / A1 Mini) omit ``cfg`` entirely; this returns ``None`` for any input
    that doesn't yield a clean integer so downstream consumers preserve today's
    behaviour rather than treating "absent" as "OFF".
    """
    if not isinstance(cfg_raw, str) or not cfg_raw:
        return None
    try:
        return bool((int(cfg_raw, 16) >> 18) & 1)
    except ValueError:
        return None


# ── A2L "AMS Lite" unit-id normalisation (issue capture 2026-07-20) ──────────
# The A2L reports its 4-slot AMS Lite as physical unit **id 16**, but the
# firmware is internally inconsistent about it:
#   - its tray bitmasks (tray_exist_bits etc.) sit at **bit base 24**, i.e. the
#     position for id 6 (6*4), NOT id 16 (which would be bit 64);
#   - it reports `tray_now` as a **local** 0-3 slot, not a global id;
#   - `ams_mapping2` and per-unit commands use the **physical** id 16.
# So we normalise 16 -> 6 at the MQTT ingest boundary. Global tray ids then land
# at 24-27, which every `ams_id*4+slot` consumer handles unchanged, collides with
# nothing (regular AMS 0-15, AMS-HT 128-135, external 254/255) and passes the
# `ams_id <= 7` DB constraint. We translate 6 -> 16 (and the local slot) back to
# the physical form ONLY on the outbound wire. See memory a2l-am-unit-16.
A2L_LITE_PHYSICAL_AMS_ID = 16
A2L_LITE_NORMALIZED_AMS_ID = 6
A2L_LITE_GLOBAL_BASE = A2L_LITE_NORMALIZED_AMS_ID * 4  # 24


def normalize_am_unit_id(ams_id: int) -> int:
    """Map the A2L AMS-Lite's physical unit id (16) to its normalised id (6).

    Self-scoping: only id 16 is remapped, and no other Bambu device reports an
    AMS unit at id 16 (regular AMS 0-3, AMS-HT 128-135). All other ids pass
    through untouched.
    """
    return A2L_LITE_NORMALIZED_AMS_ID if ams_id == A2L_LITE_PHYSICAL_AMS_ID else ams_id


def a2l_lite_wire_ids(ams_id: int, tray_id: int) -> tuple[int, int, int] | None:
    """Translate a normalised A2L slot back to the physical wire form.

    Returns ``(wire_ams_id, wire_slot_id, wire_global_tray)`` for the AMS-Lite
    (normalised id 6), else ``None`` for every other unit.

    CONFIRMED from the firmware's own `ams_mapping2` ({ams_id:16, slot_id:0-3}):
    the wire uses the physical unit id 16 with a **local** 0-3 slot. NOT yet
    confirmed by capture: the physical **global** tray value some commands put on
    the wire (load `target`, extrusion_cali `tray_id`) — we extrapolate it as
    16*4+slot = 64-67 to stay consistent with the physical unit id. This is the
    single unverified encoding; a BambuStudio->A2L capture of a load or cali
    command would settle it, and it lives only here.
    """
    if ams_id != A2L_LITE_NORMALIZED_AMS_ID:
        return None
    local_slot = tray_id % 4
    return (
        A2L_LITE_PHYSICAL_AMS_ID,
        local_slot,
        A2L_LITE_PHYSICAL_AMS_ID * 4 + local_slot,
    )


def apply_tray_exist_bits(
    units: list,
    tray_exist_bits_str: str | int | None,
    *,
    power_on_flag: bool = True,
    log_label: str | None = None,
    annotate_exists: bool = False,
) -> int:
    """Wipe stale per-tray filament fields on slots whose `tray_exist_bits` bit is 0.

    `tray_exist_bits` is firmware's canonical "which slots have a spool" bitmask
    (BambuStudio uses it too). For every slot whose bit is 0, promote the tray
    `state` to 9 (firmware's "no spool" code) and clear `tray_type` / `tray_color`
    / `tray_info_idx` / `tag_uid` / `tray_uuid` / `remain` etc so downstream
    readers (Bambuddy's AMS card, the VP slicer-facing cache, inventory short-
    circuits keyed on `state in {9, 10}`) all see one canonical empty-slot signal
    instead of guessing from payload shape (#1322, #147).

    Two callers share this helper to keep their views consistent:

    1. ``_handle_ams_data`` for Bambuddy's internal AMS state (printer card).
    2. ``virtual_printer.mqtt_bridge._on_printer_raw`` for the cached slicer-
       facing push_status (#1726 — without this the VP would forward stale
       per-tray fields for empty slots, and BambuStudio's Sync would render
       phantom loaded slots).

    Skipped only on the printer-shutdown pattern: all-zero bits paired with
    ``power_on_flag=False`` (#765). Non-zero bits with ``power_on_flag=False``
    is valid idle-printer state (#1365 — X1C between prints) and MUST be applied
    so spool removal is detected without requiring a manual reconnect.

    AMS-HT units (``id`` 128-135) are single-tray dry boxes whose presence bit
    is packed as ONE consecutive bit starting at 16 (``16 + (ams_id - 128)``),
    NOT ``ams_id * 4`` (which would overflow to bit 512+). This is the firmware's
    authoritative empty signal for the HT — the only working clear path, since
    the HT keeps echoing stale ``tray_type`` and its ``state`` is firmware-variant
    (#2670). Verified against OrcaSlicer ``DevFilaSystem.cpp``
    (``is_exists = tray_exist_bits >> (16 + (ams_id-128))``) and a live H2D
    capture (HT-A → bit 16). The A2L-Lite lands at bits 24-27 via the regular
    ``ams_id * 4`` formula, matching OrcaSlicer's ``AMS_LITE_MIXED`` offset; the
    unit id is folded through ``normalize_am_unit_id`` first so callers holding
    the raw physical id 16 get the same bit base as callers holding the
    normalised 6 (#2697).

    `tray_exist_bits_str` is expected as a hex string (firmware sends it that
    way). Ints are tolerated for defensive symmetry but typically not seen
    on the wire. ``None`` / empty / unparseable → no-op.

    ``annotate_exists`` writes a per-tray ``exists`` bool (from the bitmask) on
    every processed slot. This is firmware's authoritative "spool physically
    present" signal — the same one BambuStudio uses to draw a ``?`` for a
    non-RFID spool in an otherwise-unidentified slot. Bambuddy's AMS card keys
    empty-vs-unknown off it so a non-Bambu spool shows ``?`` instead of "Empty"
    (#2527). Only the internal (printer-card) caller sets this; the VP bridge
    leaves it False so the ``exists`` key never reaches the slicer wire format.

    Mutates ``units`` in place. Returns the number of slots cleared.
    """
    if not tray_exist_bits_str:
        return 0
    try:
        if isinstance(tray_exist_bits_str, int):
            tray_exist_bits = tray_exist_bits_str
        else:
            tray_exist_bits = int(tray_exist_bits_str, 16)
    except (ValueError, TypeError):
        return 0
    if tray_exist_bits == 0 and not power_on_flag:
        return 0
    if not isinstance(units, list):
        return 0

    cleared = 0
    for ams_unit in units:
        if not isinstance(ams_unit, dict):
            continue
        ams_id_raw = ams_unit.get("id")
        if ams_id_raw is None:
            continue
        try:
            ams_id = int(ams_id_raw) if isinstance(ams_id_raw, str) else ams_id_raw
        except (ValueError, TypeError):
            continue
        if not isinstance(ams_id, int):
            continue
        # The A2L AMS-Lite reaches this helper under either id: `_handle_ams_data`
        # normalises 16 -> 6 before calling, but the VP bridge parses the raw
        # printer payload itself (`mqtt_bridge._on_printer_raw`) and still holds
        # the physical 16. Both mean bit base 24, so fold them together here
        # rather than relying on every caller to normalise first — reading 16 as
        # 16*4 = bit 64 finds nothing set and wipes every A2L slot (#2697).
        ams_id = normalize_am_unit_id(ams_id)
        # AMS-HT (n3s, id 128-135): single tray, presence bit at 16+(ams_id-128).
        # Regular AMS (and the A2L-Lite normalised to id 6): ams_id*4 + tray_id.
        # Anything outside those ranges has no known bit layout — don't guess it.
        is_ht = 128 <= ams_id <= 135
        if not is_ht and not (0 <= ams_id <= 15):
            continue
        for tray in ams_unit.get("tray", []):
            if not isinstance(tray, dict):
                continue
            tray_id_raw = tray.get("id")
            if tray_id_raw is None:
                continue
            try:
                tray_id = int(tray_id_raw) if isinstance(tray_id_raw, str) else tray_id_raw
            except (ValueError, TypeError):
                continue
            if not isinstance(tray_id, int):
                continue
            global_bit = (16 + (ams_id - 128)) if is_ht else (ams_id * 4 + tray_id)
            slot_exists = (tray_exist_bits >> global_bit) & 1
            if annotate_exists:
                tray["exists"] = bool(slot_exists)
            if slot_exists:
                continue
            tray["state"] = 9
            if tray.get("tray_type"):
                if log_label:
                    logger.debug(
                        f"[{log_label}] Clearing empty slot: AMS {ams_id} slot {tray_id} "
                        f"(tray_exist_bits bit {global_bit} = 0)"
                    )
                tray["tray_type"] = ""
                tray["tray_sub_brands"] = ""
                tray["tray_color"] = ""
                tray["tray_id_name"] = ""
                tray["tag_uid"] = "0000000000000000"
                tray["tray_uuid"] = "00000000000000000000000000000000"
                tray["tray_info_idx"] = ""
                tray["remain"] = 0
                cleared += 1
    return cleared


# --- H2C nozzle-rack dispatch mapping (#2800) -------------------------------
#
# Physical nozzle IDs the H2C reports for its six rack slots, verified on
# hardware. They sit well clear of the fixed hotend's own physical ID, so a
# rack position is never mistakable for the nozzle on the other carriage.
#
# Extruder indices are a different namespace that happens to overlap these
# low numbers -- index 1 means the rack, physical ID 1 means the fixed hotend.
# Nothing below may pass a value from one namespace to the other untranslated;
# doing exactly that is what #2800 was.
_RACK_NOZZLE_IDS = frozenset(range(16, 22))

# BambuStudio dispatches a fixed-length nozzle_mapping on rack models: one
# physical nozzle ID per filament slot, -1 for slots the plate does not print.
#
# Briefly changed to the plate's own slot count on the strength of a single
# 3-entry capture, then changed back: Studio's dispatch of a real 3-filament
# project print on the maintainer's H2C is 32 entries ([16, 1, 18, -1 x29],
# captured 2026-08-13 17:20, and that print completed). The 3-entry capture was
# a calibration job, so the length varies with whatever Studio is doing rather
# than with the filament count -- which makes it the wrong thing to derive.
_RACK_WIRE_SLOTS = 32

# The two carriages, as extruder indices in the form the queue stores (already
# translated through the file's physical_extruder_map).
#
# Measured on the maintainer's H2C 2026-08-14, from three sources that agree:
#
#   - telemetry: ``ams_extruder_map {'0': 1, '1': 0, '2': 0}`` -- AMS 0 feeds
#     extruder 1, AMS 1 and 2 feed extruder 0;
#   - BambuStudio's own dispatch of a plate using all three units sent AMS 0's
#     filament to physical nozzle 1 and AMS 1's to rack positions 16 and 18,
#     and that print completed. So extruder 1 is the fixed hotend and extruder
#     0 is the rack;
#   - our own constants were internally inconsistent about it: physical nozzle
#     id N sits on extruder N (see the L/R split in PrintersPage), and
#     ``_FIXED_NOZZLE_ID`` is 1, which cannot be reconciled with a fixed
#     extruder index of 0.
#
# These were the other way round until then, which is what dispatched a plate
# to the carriage that had not been levelled and printed its first layer in
# mid-air. That value came from #2800, where dispatching [17, -1, -1, 1] printed
# in mid-air and [1, -1, -1, 17] printed correctly -- but that A/B measured
# which *wire* worked, and the extruder indices were only inferred from it by
# pairing with a slot_extruders list the then-buggy 3MF reader had produced. The
# wire result stands; the inference from it did not.
_FIXED_EXTRUDER_ID = 1
_RACK_EXTRUDER_ID = 0

# The fixed hotend's physical ID, which is *not* its extruder index. The same
# hardware A/B ruled the index out: [0, -1, -1, 17] was rejected by the printer
# outright, which would not start the job at all. Native BambuStudio captures
# of a mixed plate agree -- [1, 17, ...], and [17, 1, ...] once the filament
# slot order is swapped, so the fixed side is 1 whichever slot it lands in.
_FIXED_NOZZLE_ID = 1


def resolve_rack_nozzle_mapping(
    slot_extruders: list[int],
    rack_nozzle_id: int | None,
) -> list[int] | None:
    """Expand a per-slot extruder mapping into an H2C physical nozzle_mapping.

    ``slot_extruders`` is the compact form stored on the queue item: MQTT
    extruder index per filament slot (index 0 = slot 1), -1 for a slot the
    plate does not print. ``rack_nozzle_id`` is the rack position the printer
    reports as live.

    Returns a ``_RACK_WIRE_SLOTS``-long list of physical nozzle IDs, or None
    when the mapping cannot be resolved with confidence -- in which case the
    caller omits the field entirely and the firmware falls back to its own
    nozzle pick, exactly as it did before this translation existed. Omitting
    is deliberately the failure mode: a *wrong* physical ID makes the printer
    level with one nozzle and print with another several millimetres off the
    bed, which is far worse than letting the firmware choose.

    Returns None specifically when:

    - a slot needs the rack but the printer has not reported a live rack
      position (mid-swap, or a stale connection);
    - no slot needs the rack at all. BambuStudio omits nozzle_mapping entirely
      for a plate sliced for the fixed hotend only (#2800 capture), so this
      matches it rather than naming a nozzle it does not have to name;
    - a slot names a carriage that is neither of the two an H2C has, which
      means the file was mapped for a machine this translation does not model;
    - the plate needs more slots than the wire format carries;
    - the input is not a list of whole numbers.

    Total by construction: it raises nothing, because the only caller is
    building an MQTT print command with no exception handler above it and the
    queue item has already been committed as `printing` by then. An
    unparseable input has to degrade to "let the firmware pick", not to a job
    wedged in a state no print will ever leave.
    """
    if not isinstance(slot_extruders, list) or not slot_extruders:
        return None
    if len(slot_extruders) > _RACK_WIRE_SLOTS:
        return None
    if not isinstance(rack_nozzle_id, int) or isinstance(rack_nozzle_id, bool):
        return None
    if rack_nozzle_id not in _RACK_NOZZLE_IDS:
        return None

    # Normalise first so the checks below, and the values that reach the wire,
    # are known ints. bool is an int subclass and would otherwise serialise as
    # a JSON `true`; None means "slot not printed" and is folded into -1.
    normalised: list[int] = []
    for extruder in slot_extruders:
        if extruder is None:
            normalised.append(-1)
        elif isinstance(extruder, int) and not isinstance(extruder, bool):
            normalised.append(extruder)
        else:
            return None

    if _RACK_EXTRUDER_ID not in normalised:
        return None

    wire = [-1] * _RACK_WIRE_SLOTS
    for index, extruder in enumerate(normalised):
        if extruder < 0:
            continue
        if extruder == _RACK_EXTRUDER_ID:
            wire[index] = rack_nozzle_id
        elif extruder == _FIXED_EXTRUDER_ID:
            wire[index] = _FIXED_NOZZLE_ID
        else:
            # An H2C has these two carriages and no others. A third index is a
            # file mapped for something else, and forwarding it raw would name
            # a physical nozzle by an index that does not identify one.
            return None
    return wire


# A rack position as the operator counts it (and as the printer card and
# BambuStudio both label it) is 1-based; the physical nozzle id is 15 higher.
# Measured 2026-08-14: a plate dispatched with the operator picking R1 and R2
# sent 16 and 17, and the same plate picking R1 and R3 sent 16 and 18.
_RACK_POSITION_BASE = 15
RACK_POSITIONS = tuple(range(1, len(_RACK_NOZZLE_IDS) + 1))


def rack_position_to_nozzle_id(position: int) -> int | None:
    """Physical nozzle id for a 1-based rack position, or None if out of range."""
    if not isinstance(position, int) or isinstance(position, bool):
        return None
    if position not in RACK_POSITIONS:
        return None
    return _RACK_POSITION_BASE + position


def _rack_slot_is_eligible(slot: dict, diameter: str, volume_type: str) -> bool:
    """Whether a live rack slot can print a group wanting this nozzle.

    Mirrors the filter BambuStudio applies in its own picker: the position has
    to hold a nozzle at all, and that nozzle has to match the slice's diameter
    and flow type. A mismatch here is not cosmetic -- it is the printer being
    asked to lay down a 0.4 extrusion through a 0.2 orifice.
    """
    if not isinstance(slot, dict):
        return False
    slot_diameter = str(slot.get("diameter") or "").strip()
    slot_type = str(slot.get("type") or "").strip()
    if not slot_diameter and not slot_type:
        return False  # empty position

    # "0.40" and "0.4" are the same nozzle spelled two ways -- the 3MF pads,
    # the printer does not.
    try:
        if round(float(slot_diameter), 2) != round(float(diameter), 2):
            return False
    except (TypeError, ValueError):
        return False

    # Flow type: the printer reports a code ("HS", "HH01"), the slice reports a
    # name ("Standard", "High Flow"). Compared only when both are stated, so a
    # printer that omits the code is not thereby ruled ineligible.
    wanted = volume_type.strip().lower()
    if wanted and slot_type:
        is_high_flow = slot_type.upper().startswith("HH")
        if wanted.startswith("high flow") != is_high_flow:
            return False
    return True


# The nozzle currently picked up onto the rack carriage. Physical id 1 is the
# fixed hotend (``_FIXED_NOZZLE_ID``), so the other carriage entry is 0.
_RACK_CARRIAGE_NOZZLE_ID = 0


def _rack_by_position(rack_slots: list[dict]) -> dict[int, dict]:
    """Live rack contents keyed by 1-based position, mounted nozzle included.

    The firmware omits a rack id entirely while that nozzle is picked up onto
    the carriage (#943) -- it does not send an empty placeholder. Taking the
    omission at face value would rule the nozzle ineligible for the very print
    that wants it, and it is the single most likely position to be picked,
    because it is the one the last print left mounted.

    The absent id is recoverable only when exactly one is missing: rack ids are
    fixed at 16..21, so a single gap alongside a loaded carriage is that
    carriage's nozzle. Two or more gaps are genuinely ambiguous -- an operator
    with four nozzles in six positions looks the same -- so those stay absent
    and the caller treats them as empty.

    Measured 2026-08-14 09:02 on the maintainer's H2C: ``IDs: [16, 1, 21, 19,
    18, 0, 20]`` -- both carriages present, rack id 17 the lone gap.
    """
    by_position: dict[int, dict] = {}
    carriage: dict | None = None
    for slot in rack_slots or []:
        if not isinstance(slot, dict) or not isinstance(slot.get("id"), int):
            continue
        if slot["id"] == _RACK_CARRIAGE_NOZZLE_ID:
            carriage = slot
            continue
        position = slot["id"] - _RACK_POSITION_BASE
        if position in RACK_POSITIONS:
            by_position[position] = slot

    missing = [position for position in RACK_POSITIONS if position not in by_position]
    if len(missing) == 1 and carriage is not None and (carriage.get("diameter") or carriage.get("type")):
        by_position[missing[0]] = carriage
    return by_position


def resolve_rack_plan_mapping(
    slot_groups: list[int],
    groups: dict[int, dict],
    choice: dict[int, int],
    rack_slots: list[dict],
) -> tuple[list[int] | None, str | None]:
    """Build a physical ``nozzle_mapping`` from a rack plan and a position pick.

    This is the multi-hotend counterpart to :func:`resolve_rack_nozzle_mapping`.
    That one can only name the single live rack position, so a plate wanting a
    different hotend per group is unresolvable to it. Here each group carries
    its own position, which is the operator's choice (#1784) -- the 3MF states
    it nowhere, proven by dispatching one plate twice with different picks and
    diffing the two files down to float noise.

    ``choice`` may be partial or empty; groups it does not name are assigned
    from the live rack, preferring a position already loaded with the group's
    own filament colour and otherwise taking the lowest eligible one.

    Returns ``(wire, None)`` on success, or ``(None, reason)`` where *reason*
    is a sentence naming what could not be satisfied. The caller decides what
    to do with a failure, and the two cases differ: a stale *explicit* pick
    should stop the print, while a failed auto-assignment should degrade to
    letting the firmware choose, exactly as before this existed.
    """
    if not isinstance(slot_groups, list) or not slot_groups:
        return None, "the plate lists no filament slots"
    if len(slot_groups) > _RACK_WIRE_SLOTS:
        return None, f"the plate needs {len(slot_groups)} filament slots and the printer takes {_RACK_WIRE_SLOTS}"

    by_position = _rack_by_position(rack_slots)

    # Assign every rack-bound group a position before building the wire, so a
    # group can never be handed one an earlier group already took. Explicit
    # picks are placed first: an auto-assignment must yield to them rather than
    # claim a position the operator asked for.
    assigned: dict[int, int] = {}
    rack_group_ids = sorted(gid for gid, g in groups.items() if g.get("on_rack"))

    for group_id in rack_group_ids:
        position = choice.get(group_id)
        if position is None:
            continue
        group = groups[group_id]
        if rack_position_to_nozzle_id(position) is None:
            return None, f"rack position {position} does not exist"
        if position in assigned.values():
            return None, f"rack position {position} is picked for more than one filament group"
        slot = by_position.get(position)
        if slot is None:
            return None, f"the printer reports nothing at rack position {position}"
        if not _rack_slot_is_eligible(slot, group.get("nozzle_diameter", ""), group.get("volume_type", "")):
            return None, (
                f"rack position {position} holds a "
                f"{slot.get('diameter') or 'missing'} {slot.get('type') or ''} nozzle, "
                f"and the plate needs {group.get('nozzle_diameter')} {group.get('volume_type')}".replace("  ", " ")
            )
        assigned[group_id] = position

    for group_id in rack_group_ids:
        if group_id in assigned:
            continue
        group = groups[group_id]
        eligible = [
            position
            for position in RACK_POSITIONS
            if position not in assigned.values()
            and position in by_position
            and _rack_slot_is_eligible(
                by_position[position], group.get("nozzle_diameter", ""), group.get("volume_type", "")
            )
        ]
        if not eligible:
            return None, (
                f"no free rack position holds a {group.get('nozzle_diameter')} "
                f"{group.get('volume_type')} nozzle for filament group {group_id}"
            )
        # Prefer a position already carrying this group's colour: picking it
        # means the operator does not have to move filament to make the print
        # match what they asked for.
        wanted_colour = str(group.get("filament_color") or "").strip().lstrip("#").upper()[:6]
        assigned[group_id] = next(
            (
                position
                for position in eligible
                if wanted_colour
                and str(by_position[position].get("filament_color") or "").strip().lstrip("#").upper()[:6]
                == wanted_colour
            ),
            eligible[0],
        )

    wire = [-1] * _RACK_WIRE_SLOTS
    for index, group_id in enumerate(slot_groups):
        if not isinstance(group_id, int) or isinstance(group_id, bool) or group_id < 0:
            continue  # slot this plate does not print
        group = groups.get(group_id)
        if group is None:
            return None, f"filament slot {index + 1} names group {group_id}, which the plate does not describe"
        if not group.get("on_rack"):
            wire[index] = _FIXED_NOZZLE_ID
            continue
        nozzle_id = rack_position_to_nozzle_id(assigned[group_id])
        if nozzle_id is None:  # pragma: no cover - assigned only ever holds valid positions
            return None, f"filament group {group_id} resolved to no rack position"
        wire[index] = nozzle_id

    if all(value == -1 for value in wire):
        return None, "the plate assigns no filament to a nozzle"
    return wire, None


@dataclass
class MQTTLogEntry:
    """Log entry for MQTT message debugging."""

    timestamp: str
    topic: str
    direction: str  # "in" or "out"
    payload: dict


@dataclass
class HMSError:
    """Health Management System error from printer."""

    code: str
    attr: int  # Attribute value for constructing wiki URL
    module: int
    severity: int  # 1=fatal, 2=serious, 3=common, 4=info
    # The bundled catalogue's sentence for this fault, resolved once here so
    # every surface that reports it — the status response, the WebSocket
    # broadcast, the completion payload, notifications — says the same thing.
    # None when the catalogue does not cover the code; `describe_fault` documents
    # the lookup and why the lossy `hms[]` collapse is kept as it was.
    # Replaces a `message` field that was never set or read anywhere.
    description: str | None = None
    # User-facing remediation actions from the bundled HMS catalog (e.g. "RESUME_PRINTING",
    # "CHECK_ASSISTANT"). Defaults to an empty list rather than None so the field always
    # satisfies HMSErrorResponse.actions: list[str] — a future code path that builds an
    # HMSError without explicitly passing actions can't silently land None on the schema
    # boundary and raise ValidationError at routes/printers.py response time.
    actions: list[str] = field(default_factory=list)
    # The `subtask_id` snapshotted from PrinterState when this error surfaced; Bambu's
    # HMS-aware commands echo it back as `job_id`. None for idle errors with no job.
    job_id: str | None = None
    # Canonical hex identifier for the firmware's `err` matching: 16 chars for the
    # 64-bit `hms[]` array path (`f"{attr:08X}{code:08X}"`), 8 chars for the
    # 32-bit `print_error` path. The frontend echoes this back to
    # execute_hms_action; the truncated 8-char short code that `_parse_status`
    # used to send caused the firmware to silently reject HMS commands on H2C
    # (#1830) and on `hms[]`-sourced faults generally.
    full_code: str = ""


# HMS short codes the firmware emits during normal user-cancel sequences.
# These aren't faults — they're status echoes that confirm the cancel happened.
# Filtering them at parse-time keeps them out of state.hms_errors entirely,
# so they don't drive the printer card's "X problem" badge, the red pip, or
# any other consumer that treats hms_errors as the active-fault list.
_HMS_USER_ACTION_CODES: frozenset[str] = frozenset(
    {
        "0300_400C",  # "The task was canceled."
        "0500_400E",  # "Printing was cancelled."
    }
)

# "MQTT command verification failed" — the printer's authorization/authentication
# protection (firmware >= 01.08.03.00beta / 01.08.05.00) rejecting a control
# command it could not verify. Queries (get_version, extrusion_cali_get,
# pushall) still answer, so the connection looks perfectly healthy while
# project_file, gcode_line and ams_change_filament are all silently dropped —
# which is exactly how it presents: uploads succeed, the printer echoes our
# subtask_id, then sits at IDLE forever (#2732).
#
# The 16-char form is load-bearing. This code's meaning lives in attr's low half
# (0500) and code's high half (0001); the MMMM_EEEE short code collapses it to
# "0500_0007", which matches nothing in any catalog.
HMS_MQTT_VERIFY_FAILED: str = "0500050000010007"


@dataclass
class KProfile:
    """Pressure advance (K) calibration profile from printer."""

    slot_id: int
    extruder_id: int
    nozzle_id: str
    nozzle_diameter: str
    filament_id: str
    name: str
    k_value: str
    n_coef: str = "0.000000"
    ams_id: int = 0
    tray_id: int = -1
    setting_id: str | None = None


@dataclass
class NozzleInfo:
    """Nozzle hardware configuration."""

    nozzle_type: str = ""  # "stainless_steel" or "hardened_steel"
    nozzle_diameter: str = ""  # e.g., "0.4"


@dataclass
class FilaSwitchState:
    """Filament Track Switch (FTS) accessory state.

    The FTS is an external accessory that mediates filament routing between an
    AMS and the printer's extruders. When installed, the AMS no longer has a
    fixed extruder assignment — any slot can be routed to any extruder via the
    track switch. Detected from print.device.fila_switch in MQTT.

    The switch has two inlets (In-A, In-B) and two outlets (Out-A, Out-B), and
    can pair any inlet with any outlet. Which AMS sits on which *inlet* is the
    stable, operator-visible relationship — it is set on the printer's "Manual
    AMS Setup" screen and read back from AMS ``info`` bits 24-27, not from here.

    Field semantics below are taken from BambuStudio's own parser
    (``DevFilaSwitch::ParseFilaSwitchInfo``), not inferred.
    """

    installed: bool = False
    # Raw ``in`` array, as it arrives. **Index 0 is In-B and index 1 is In-A** —
    # the arrays are ordered B-then-A, which is the opposite of how they read.
    # Each value is snow-encoded: bits 8-15 = AMS id, bits 0-7 = slot. -1 = the
    # inlet is empty. Use `inlet_slot()` rather than indexing this directly.
    in_slots: list[int] = field(default_factory=list)
    # Raw ``out`` array, same B-then-A order. out[i] = the extruder that *outlet*
    # terminates at (0 = right/main, 1 = left/deputy), or 0xE when unset. Note
    # this is the outlet's static wiring, NOT the live inlet→outlet route: which
    # inlet is currently paired with which outlet is not reported at all.
    out_extruders: list[int] = field(default_factory=list)
    stat: int = 0  # CaliStatus: 0 = idle, 1 = calibration stepping
    info: int = 0  # bit 0 = inlet has filament

    def inlet_slot(self, inlet: str) -> tuple[int, int] | None:
        """Decode ``in`` for inlet ``"A"`` or ``"B"`` into ``(ams_id, slot)``.

        Returns None when the inlet is empty, unreported, or ``inlet`` is not
        one of A/B.
        """
        index = {"A": 1, "B": 0}.get(inlet.upper())
        if index is None or index >= len(self.in_slots):
            return None
        raw = self.in_slots[index]
        if raw < 0:
            return None
        return (raw >> 8) & 0xFF, raw & 0xFF


# ``snow``/``spre``/``star`` all use this sentinel for "nothing here". Studio
# only special-cases it on single-extruder machines, but 0xFFFF decodes to AMS
# 255 slot 255 and slot 255 is not a real slot on any machine, so treating it
# as empty everywhere is strictly safer than reading it as the external spool.
_EXTRUDER_SLOT_EMPTY = 0xFFFF


@dataclass
class ExtruderSlot:
    """Which AMS slot an extruder is currently fed from.

    Parsed from ``print.device.extruder.info[i]`` — ``snow`` is snow-encoded
    exactly like ``fila_switch.in`` (bits 8-15 = AMS id, bits 0-7 = slot), and
    bit 1 of ``info`` says whether the extruder actually holds filament. Field
    semantics from BambuStudio's ``DevExtruderSystem::ParseExtruderInfo``.

    ``state.tray_now`` cannot answer this: it is a single value for the whole
    printer, so on a dual-nozzle machine with both hotends loaded it names only
    one of them. Unloading a specific slot needs to know which extruder is
    holding it, which is what this is for.
    """

    ams_id: int | None = None
    slot_id: int | None = None
    has_filament: bool = False

    def holds(self, ams_id: int, slot_id: int) -> bool:
        """True when this extruder is fed from exactly ``(ams_id, slot_id)``."""
        return self.ams_id == ams_id and self.slot_id == slot_id


@dataclass
class PrintOptions:
    """AI detection and print options from xcam data."""

    # Core AI detectors
    spaghetti_detector: bool = False
    print_halt: bool = False
    halt_print_sensitivity: str = "medium"  # Spaghetti sensitivity
    first_layer_inspector: bool = False
    printing_monitor: bool = False  # AI print quality monitoring
    buildplate_marker_detector: bool = False
    allow_skip_parts: bool = False
    # Additional AI detectors - decoded from cfg bitmask
    nozzle_clumping_detector: bool = True
    nozzle_clumping_sensitivity: str = "medium"
    pileup_detector: bool = True
    pileup_sensitivity: str = "medium"
    airprint_detector: bool = True
    airprint_sensitivity: str = "medium"
    auto_recovery_step_loss: bool = True  # Uses print.print_option command
    filament_tangle_detect: bool = False


@dataclass
class PrinterState:
    connected: bool = False
    state: str = "unknown"
    current_print: str | None = None
    subtask_name: str | None = None
    progress: float = 0.0
    remaining_time: int = 0
    layer_num: int = 0
    total_layers: int = 0
    temperatures: dict = field(default_factory=dict)
    raw_data: dict = field(default_factory=dict)
    gcode_file: str | None = None
    subtask_id: str | None = None
    hms_errors: list = field(default_factory=list)  # List of HMSError
    kprofiles: list = field(default_factory=list)  # List of KProfile
    sdcard: bool = False  # SD card inserted
    # Whether the printer has ever actually told us about `sdcard`. Without this
    # the default False is indistinguishable from a real "no card", and any
    # consumer that treats False as evidence would act on silence — which is how
    # a storage gate turns into a regression for every printer whose firmware
    # simply doesn't publish the field (#2780).
    sdcard_reported: bool = False
    store_to_sdcard: bool = False  # Store sent files on SD card (home_flag bit 11)
    # Scheme+path of a `project_file` dispatch seen on the request topic, from
    # whoever sent it (the slicer or us). Bambu states where the sliced file
    # went: `ftp://<name>` is external storage, which FTPS serves, while
    # `brtc://emmc/<name>` is the printer's internal storage, which it does not.
    #
    # Two fields, because the two readers need different guarantees.
    # ``current_project_url`` belongs to the print now running and is cleared
    # when that print ends, so a print Bambuddy saw no dispatch for reads as
    # "unknown" rather than inheriting the previous job's answer. That matters:
    # 18% of the print starts in #2780's bundle had no dispatch on the request
    # topic at all (touchscreen reprints, restart recovery), and a stale
    # internal-storage URL would make those skip an FTPS sweep that could have
    # found the file — losing an archive that works today.
    #
    # ``last_project_url`` is sticky and exists for reporting only: the
    # connection diagnostic is usually run *after* the print that prompted it,
    # by which point the per-print value is rightly gone.
    #
    # None means we never saw a dispatch — say nothing, don't guess.
    current_project_url: str | None = None
    last_project_url: str | None = None
    timelapse: bool = False  # Timelapse recording active
    ipcam: bool = False  # Live view / camera streaming enabled
    wifi_signal: int | None = None  # WiFi signal strength in dBm
    wired_network: bool = False  # Ethernet connection detected (home_flag bit 18)
    door_open: bool = False  # Enclosure door open (home_flag bit 23; models with a door sensor: X1/X1C/X1E/X2D/P2S/H2*)
    # Nozzle hardware info (for dual nozzle printers, index 0 = left, 1 = right)
    nozzles: list = field(default_factory=lambda: [NozzleInfo(), NozzleInfo()])
    # AI detection and print options
    print_options: PrintOptions = field(default_factory=PrintOptions)
    # Calibration stage tracking (from stg_cur and stg fields)
    stg_cur: int = -1  # Current stage index (-1 = not calibrating)
    stg: list = field(default_factory=list)  # List of stages to execute
    # Air conditioning mode (0=cooling, 1=heating)
    airduct_mode: int = 0
    # Print speed level (1=silent, 2=standard, 3=sport, 4=ludicrous)
    speed_level: int = 2
    # Chamber light on/off
    chamber_light: bool = False
    # Active extruder for dual nozzle (0=right, 1=left) - from device.extruder.info[X].hnow
    active_extruder: int = 0
    # Currently loaded tray (global ID): 254/255 = external spools, 255 = no filament on legacy printers
    tray_now: int = 255
    # Firmware's target/previous tray as reported in print.ams (RAW, not globalised):
    #   tray_tar = the slot the paused/loading print now expects
    #   tray_pre = the slot that was loaded before (e.g. the one that ran out)
    # For a single regular AMS these equal the global tray ID; for multi-AMS they
    # are local slot IDs (0-3) that must be resolved against the mapping field, and
    # for AMS-HT they are already global (128-135). 255 = none/idle, 254 = external.
    # Surfaced during a runout PAUSE so the UI can name the expected slot (#2587).
    tray_tar: int = 255
    tray_pre: int = 255
    # Last valid tray_now (0-253) — survives unload (255) for usage tracking after print completes
    last_loaded_tray: int = -1
    # Pending load target - used to track what tray we're loading for H2D disambiguation
    pending_tray_target: int | None = None
    # AMS status for filament change tracking (from print.ams.ams_status field)
    # ams_status is a combined value: lower 8 bits = sub status, bits 8-15 = main status
    # Main status: 0=idle, 1=filament_change, 2=rfid_identifying, 3=assist, 4=calibration, etc.
    ams_status: int = 0
    ams_status_main: int = 0  # (ams_status >> 8) & 0xFF
    ams_status_sub: int = 0  # ams_status & 0xFF
    # mc_print_sub_stage - filament change step indicator from print.mc_print_sub_stage
    # Used by OrcaSlicer/BambuStudio to track progress during filament load/unload
    mc_print_sub_stage: int = 0
    # AMS mapping for dual nozzle: which slot is active (from ams.ams_exist_bits/tray_exist_bits)
    ams_mapping: list = field(default_factory=list)
    # Per-AMS extruder map: {ams_id: extruder_id} where 0=right/main, 1=left/deputy
    ams_extruder_map: dict = field(default_factory=dict)
    # Filament Track Switch (FTS) accessory — when installed, AMS info reports
    # bits 8-11 = 0xE (uninitialized) because routing is dynamic. See #1162.
    fila_switch: "FilaSwitchState" = field(default_factory=lambda: FilaSwitchState())
    # Per-AMS FTS inlet binding: {ams_id: "A" | "B"}. Which of the switch's two
    # filament inlets an AMS is plumbed into, as set on the printer's "Manual AMS
    # Setup" screen. Only populated when an FTS is installed — without one an AMS
    # is bound to an extruder instead and this stays empty. See FilaSwitchState.
    ams_switch_inlet: dict = field(default_factory=dict)
    # Which AMS slot each extruder is fed from: {extruder_id: ExtruderSlot}.
    # Only populated by printers that report ``device.extruder.info`` (H2/X2
    # series). Empty elsewhere, which every reader has to tolerate — see
    # ExtruderSlot for why tray_now cannot stand in for it.
    extruder_slots: dict = field(default_factory=dict)
    # Plate dispatched by Bambuddy for the current print. Some firmware versions
    # (P1S 01.10.00.00) only put the .3mf filename in print.gcode_file, so the
    # regex used to derive the plate number from the path always falls back to
    # plate 1 — and the printer card shows the wrong thumbnail (#1166). When
    # Bambuddy dispatches the print itself we know the plate authoritatively;
    # we record it here and prefer it over the gcode_file regex. The subtask
    # field guards against staleness: if the printer is currently running a
    # different subtask (e.g. a Studio-direct dispatch), these values are
    # ignored. Cleared on disconnect.
    dispatched_plate_id: int | None = None
    dispatched_subtask: str | None = None
    # H2D per-extruder tray_now from snow field: {extruder_id: normalized_global_tray_id}
    # snow encodes AMS ID in high byte: ams_id = snow >> 8, slot = snow & 0xFF
    h2d_extruder_snow: dict = field(default_factory=dict)
    # H2C nozzle rack: full device.nozzle.info array for tool-changer printers (>2 nozzles)
    nozzle_rack: list = field(default_factory=list)
    # H2C rack position currently mounted / being moved to, from
    # device.nozzle.src_id / tar_id. These are PHYSICAL nozzle IDs (16-21 for
    # the six rack slots), not extruder indices, and they are what the
    # dispatch `nozzle_mapping` array has to carry (#2800). Only the printer
    # can tell us which hotend is in the carriage right now, so this is read
    # live rather than derived from the queued job.
    nozzle_rack_src_id: int | None = None
    nozzle_rack_tar_id: int | None = None
    # Timestamp of last AMS data update (for RFID refresh detection)
    last_ams_update: float = 0.0
    # Printable objects for skip object functionality: {identify_id: object_name}
    printable_objects: dict = field(default_factory=dict)
    # Objects that have been skipped during the current print
    skipped_objects: list = field(default_factory=list)
    # Fan speeds (0-100 percentage, None if not available for this model)
    cooling_fan_speed: int | None = None  # Part cooling fan
    big_fan1_speed: int | None = None  # Auxiliary fan
    big_fan2_speed: int | None = None  # Chamber/exhaust fan
    heatbreak_fan_speed: int | None = None  # Hotend heatbreak fan
    # Left auxiliary part cooling fan (optional accessory on P2S/X2D). Reported ONLY
    # via device.airduct.parts (decoded part id 10 = FAN_REMOTE_COOLING_1 in Bambu
    # Studio's AIR_FUN enum) — the firmware does NOT mirror it into any flat
    # big_fanX_speed field, which is why it was previously dropped. 0-100 percent.
    left_aux_fan_speed: int | None = None
    # Chamber exhaust fan, derived from the airduct parts list containing decoded
    # id 3. On the P2S this is the External Exhaust Fan kit and a base machine
    # omits it, which is the case this flag exists to detect.
    #
    # NOTE: the flag is not P2S/X2D-specific despite the name. The H2 series
    # (H2C/H2D/H2S) also reports part 3, so this goes True there too. That is
    # harmless because only the P2S/X2D badge consults it — those models keep
    # their unconditional "Chamber Fan" badge — but do not read this as
    # "an exhaust kit is fitted" without also checking the model.
    exhaust_fan_present: bool = False
    # Tray change history during current print: [(global_tray_id, layer_num), ...]
    # Used by usage tracker to split filament weight on mid-print tray switch
    tray_change_log: list = field(default_factory=list)
    # Firmware version info (from info.module[name="ota"].sw_ver)
    firmware_version: str | None = None
    # Developer LAN mode: parsed from MQTT "fun" field bit 0x20000000
    # True = dev mode ON (no encryption), False = dev mode OFF (encryption required), None = unknown
    developer_mode: bool | None = None
    # AMS Filament Backup: bit 18 of top-level print.cfg hex on new-protocol Bambu
    # printers (H/X/P/H2 families). True=ON, False=OFF, None=unknown (e.g. A1 family
    # which uses the old protocol path; field not yet found). Consumers must treat
    # None as "no opinion" — preserving today's behaviour, NOT as "disabled".
    ams_filament_backup: bool | None = None


# Stage name mapping from BambuStudio DeviceManager.cpp
STAGE_NAMES = {
    0: "Printing",
    1: "Auto bed leveling",
    2: "Heatbed preheating",
    3: "Vibration compensation",
    4: "Changing filament",
    5: "M400 pause",
    6: "Paused (filament ran out)",
    7: "Heating nozzle",
    8: "Calibrating dynamic flow",
    9: "Scanning bed surface",
    10: "Inspecting first layer",
    11: "Identifying build plate type",
    12: "Calibrating Micro Lidar",
    13: "Homing toolhead",
    14: "Cleaning nozzle tip",
    15: "Checking extruder temperature",
    16: "Paused by the user",
    17: "Pause (front cover fall off)",
    18: "Calibrating the micro lidar",
    19: "Calibrating flow ratio",
    20: "Pause (nozzle temperature malfunction)",
    21: "Pause (heatbed temperature malfunction)",
    22: "Filament unloading",
    23: "Pause (step loss)",
    24: "Filament loading",
    25: "Motor noise cancellation",
    26: "Pause (AMS offline)",
    27: "Pause (low speed of the heatbreak fan)",
    28: "Pause (chamber temperature control problem)",
    29: "Cooling chamber",
    30: "Pause (Gcode inserted by user)",
    31: "Motor noise showoff",
    32: "Pause (nozzle clumping)",
    33: "Pause (cutter error)",
    34: "Pause (first layer error)",
    35: "Pause (nozzle clog)",
    36: "Measuring motion precision",
    37: "Enhancing motion precision",
    38: "Measure motion accuracy",
    39: "Nozzle offset calibration",
    40: "High temperature auto bed leveling",
    41: "Auto Check: Quick Release Lever",
    42: "Auto Check: Door and Upper Cover",
    43: "Laser Calibration",
    44: "Auto Check: Platform",
    45: "Confirming BirdsEye Camera location",
    46: "Calibrating BirdsEye Camera",
    47: "Auto bed leveling - phase 1",
    48: "Auto bed leveling - phase 2",
    49: "Heating chamber",
    50: "Cooling heatbed",
    51: "Printing calibration lines",
    52: "Auto Check: Material",
    53: "Live View Camera Calibration",
    54: "Waiting for heatbed temperature",
    55: "Auto Check: Material Position",
    56: "Cutting Module Offset Calibration",
    57: "Measuring Surface",
    58: "Thermal Preconditioning",
    59: "Homing Blade Holder",
    60: "Calibrating Camera Offset",
    61: "Calibrating Blade Holder Position",
    62: "Hotend Pick and Place Test",
    63: "Waiting for Chamber temperature",
    64: "Preparing Hotend",
    65: "Calibrating nozzle clumping detection",
    66: "Purifying the chamber air",
    74: "Preparing",  # Seen on H2D during print preparation
    77: "Preparing AMS",
}


def get_stage_name(stage: int) -> str:
    """Get human-readable stage name from stage number."""
    try:
        return STAGE_NAMES.get(stage, f"Unknown stage ({stage})")
    except TypeError:
        # `stage` is an int by convention only -- it comes straight out of the
        # printer's JSON, and an unhashable value there would otherwise raise
        # from inside the f-string that builds the stage-change log line, which
        # is evaluated on every transition whatever the log level is set to.
        # Labelling a value must not be able to abort the state update.
        return f"Unknown stage ({stage})"


# #2547 end-of-print telemetry probe.
#
# The finish photo needs a "printing is done, toolhead parked, filament unload
# not started yet" moment. ``stg_cur=22`` was meant to be that moment (#1721)
# but fires on no model in the field: across 247 support bundles there is not a
# single ``FINISH PHOTO MOMENT (stage-22)``, including the 2026-06-13..07-08
# window where it was the only pre-FINISH trigger in the code (104 captures on
# A1, A1 Mini, H2C, H2D, P1S, P2S, X1C, X2D — all of them the FINISH fallback).
#
# We can't design a replacement from bundles we already have, because out of
# this window Bambuddy only ever parses ``stg_cur`` and ``mc_print_sub_stage``;
# every other stage/action field is dropped unread. The obvious candidates
# (``print_real_action``, ``mc_action``, ``mc_stage``) are also absent from
# A1/A1 Mini/P1S payloads, so none of them can be the universal answer on its
# own. Dumping the raw values for the window between the last object layer and
# ``gcode_state=FINISH`` lets one debug bundle per model settle what — if
# anything — marks that moment.
#
# Every field here is machine telemetry (stage codes, counters, bitfields).
# Nothing identifying, and nothing that could carry an access code.
_END_OF_PRINT_PROBE_FIELDS = (
    "gcode_state",
    "state",
    "print_error",
    "stg_cur",
    "stg",
    "stg_cd",
    "mc_print_stage",
    "mc_print_sub_stage",
    "mc_action",
    "mc_stage",
    "print_real_action",
    "print_gcode_action",
    "spd_lvl",
    "mc_percent",
    "mc_remaining_time",
    "layer_num",
    "total_layer_num",
    "home_flag",
    "prepare_per",
)

# Frame budget for one print's probe. A long final layer can hold the window
# open for minutes at ~1 frame/second; this stops a single print from filling
# the log the user then has to upload.
_END_OF_PRINT_PROBE_MAX_FRAMES = 400

# States that close the window. FINISH is the interesting one — the probe's
# whole job is to show what happened in the run-up to it.
_END_OF_PRINT_PROBE_CLOSING_STATES = frozenset({"FINISH", "FAILED", "IDLE", "PREPARE"})


class BambuMQTTClient:
    """MQTT client for Bambu Lab printer communication."""

    MQTT_PORT = 8883

    # Class-level cache: serial_number -> False when request topic is known unsupported.
    # Persists across client instances so reconnects don't re-trigger failed subscriptions.
    _request_topic_cache: dict[str, bool] = {}
    # serial_number -> consecutive disconnects seen shortly after subscribing to
    # the request topic. A SUBACK failure is the broker answering the question;
    # a disconnect is only circumstantial, and any drop inside the window looks
    # identical -- a network blip, the printer rebooting, the container being
    # stopped mid-probe. Latching on the first one costs ams_mapping capture for
    # the rest of the process on a printer that supports it perfectly well
    # (#2953). Require the drop to repeat before believing it; a printer that
    # really does refuse the topic answers the same way every time and pays one
    # extra reconnect for it.
    _request_topic_probe_failures: dict[str, int] = {}
    _REQUEST_TOPIC_PROBE_LIMIT: int = 2
    # Counter for generating unique MQTT client IDs across instances.
    _client_instance_counter: int = 0

    # #2582: how long to wait for the AMS telemetry to echo back an assignment
    # before declaring it un-confirmed. The printer re-broadcasts tray state
    # every few seconds (and register_assignment_verification nudges a fresh
    # pushall), so this only has to survive a couple of idle push intervals.
    ASSIGNMENT_VERIFY_TIMEOUT: float = 30.0

    def __init__(
        self,
        ip_address: str,
        serial_number: str,
        access_code: str,
        model: str | None = None,
        on_state_change: Callable[[PrinterState], None] | None = None,
        on_print_start: Callable[[dict], None] | None = None,
        on_print_complete: Callable[[dict], None] | None = None,
        on_ams_change: Callable[[list], None] | None = None,
        on_layer_change: Callable[[int], None] | None = None,
        on_print_progress: Callable[[int], None] | None = None,
        on_bed_temp_update: Callable[[float], None] | None = None,
        on_drying_complete: Callable[[int], None] | None = None,
        on_print_running_observed: Callable[[dict], None] | None = None,
        on_finish_photo_moment: Callable[[dict], None] | None = None,
        on_assignment_verified: Callable[[int, int, bool, dict], None] | None = None,
        on_tray_change: Callable[[int, int], None] | None = None,
        on_fts_inlet_change: Callable[[int, str], None] | None = None,
    ):
        self.ip_address = ip_address
        self.serial_number = serial_number
        self.access_code = access_code
        self.model = model
        # Last value logged by _debug_on_change(), keyed by log site. See there.
        self._debug_last: dict[str, object] = {}
        self.on_state_change = on_state_change
        self.on_print_start = on_print_start
        self.on_print_complete = on_print_complete
        self.on_ams_change = on_ams_change
        # Fired when an AMS is moved to the switch's other inlet, which changes
        # the nozzle it feeds and so invalidates its slots' K-profile bindings.
        self.on_fts_inlet_change = on_fts_inlet_change
        self.on_layer_change = on_layer_change
        # #2547: fired when `mc_percent` advances during a running print.
        # `on_layer_change` stops firing the instant the final layer starts, so
        # it is blind to the last few percent of a print — which is exactly the
        # window the finish-photo frame bank needs to keep refreshing through.
        # Progress is the one field that keeps ticking there and then freezes
        # before the end G-code runs, so banking on it stays inside the print.
        self.on_print_progress = on_print_progress
        self.on_bed_temp_update = on_bed_temp_update
        # #1349: fired when an AMS unit's dry_time falls from >0 to 0 — i.e.
        # the drying cycle just finished (auto- or manually-triggered).
        # Receives the AMS id of the unit that finished drying.
        self.on_drying_complete = on_drying_complete
        # #1485 follow-up: fired the first time we see RUNNING state in a
        # session WHEN on_print_start was suppressed (Bambuddy started mid-
        # print, the #1304 first-push guard skipped the start event). Lets
        # main.py capture a fresh timelapse baseline at restart-recovery
        # time so the completion-time snapshot-diff still works. Receives
        # the same shape as on_print_start (filename / subtask_name /
        # remaining_time / raw_data / ams_mapping).
        self.on_print_running_observed = on_print_running_observed
        # Fired for every entry appended to ``state.tray_change_log`` so main.py
        # can mirror it into ``active_print_sessions``. The in-memory log dies
        # with the process, and a long print outliving a restart would
        # otherwise lose the segment boundaries the usage tracker splits on.
        # Receives (global_tray_id, layer_num).
        self.on_tray_change = on_tray_change
        # #1721: fired the moment the printer enters the end-of-print
        # "Filament unloading" phase (stg_cur=22 while progress>=99 or
        # we've hit the last layer / remaining_time<=0). This is the
        # framing #1397 was after — toolhead parked, bed not yet
        # dropped — but reached via a clean state signal instead of
        # the per-layer M622 J1 macros which caused per-layer nozzle
        # parks on slicer profiles with Timelapse Type = Smooth.
        # A FINISH-state fallback below fires this same callback if
        # stage 22 never arrives (cancel mid-print, external-spool-
        # only prints, HMS halt before unload, firmware variants).
        self.on_finish_photo_moment = on_finish_photo_moment
        # #2582: fired after a spool assignment (ams_filament_setting +
        # extrusion_cali_sel) once the tray's telemetry either confirms the
        # push landed or a timeout elapses without it. Receives
        # (ams_id, tray_id, verified: bool, detail: dict). Lets the frontend
        # tell the user "loaded" vs "assignment didn't take" instead of the
        # historic fire-and-forget silence that made the AMS/Studio hand-off
        # feel random. See _check_assignment_verifications.
        self.on_assignment_verified = on_assignment_verified
        # Pending read-back verifications, keyed by (ams_id, tray_id). Each
        # value is the desired end-state we just pushed plus a monotonic
        # deadline. Populated by register_assignment_verification, drained by
        # _check_assignment_verifications on every AMS push.
        self._pending_assignments: dict[tuple[int, int], dict] = {}
        # Per-AMS previous dry_time, used to detect the falling edge above.
        # Seeded lazily as we observe each AMS unit.
        self._previous_dry_times: dict[int, int] = {}
        # Per-AMS active-cycle target params (filament + temp) we sent on the
        # last start. Bambu does not echo these back in the per-tick AMS push
        # — only the dry_time countdown — so we cache what we sent to drive
        # the UI badge. Cleared on stop or on the dry_time falling edge to 0.
        self._drying_targets: dict[int, dict[str, object]] = {}
        # AMS ids we have sent a stop for and not yet seen end. A stop always
        # ends a cycle far short of its duration, which on the telemetry alone
        # is indistinguishable from the firmware abandoning it — so the cycle-end
        # log would otherwise blame the printer for our own decision (#2770).
        self._drying_stops_sent: set[int] = set()
        # Stage numbers this printer has reported that STAGE_NAMES has no entry
        # for, so each is reported once rather than on every transition into it.
        self._unnamed_stages_seen: set[int] = set()

        self.state = PrinterState()
        self._client: mqtt.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._previous_gcode_state: str | None = None
        self._previous_gcode_file: str | None = None
        self._was_running: bool = False  # Track if we've seen RUNNING state for current print
        self._completion_triggered: bool = False  # Prevent duplicate completion triggers
        self._timelapse_during_print: bool = False  # Track if timelapse was active during this print
        # #1721: one-shot guard so the end-of-print stage-22 detector
        # and the FINISH-state fallback don't both fire on the same
        # print. Reset to False on every print start.
        self._finish_photo_captured: bool = False
        # #2702: one-shot re-request of the layer total. Armed at print start
        # when the starting frame carried no `total_layer_num`, spent on the
        # first layer advance that still has no denominator. Bambu firmware
        # only re-sends *changed* fields, so a total we never received (or
        # dropped) is only recoverable via a full pushall.
        self._total_layers_refresh_armed: bool = False
        # #2547 end-of-print telemetry probe state. `_armed` is cleared once the
        # window has run for a print so a late FINISH re-send can't reopen it.
        self._eop_probe_armed: bool = True
        self._eop_probe_open: bool = False
        self._eop_probe_frames: int = 0
        self._eop_probe_last: dict = {}
        self._last_valid_progress: float = 0.0  # Last non-zero progress (firmware resets on cancel)
        self._last_valid_layer_num: int = 0  # Last non-zero layer (firmware resets on cancel)
        # The subtask_id minted for the most recent start_print() command. The
        # printer echoes it back in status, but often not within the first few
        # seconds — so on_print_start uses this as the id source when the
        # printer hasn't reported it yet, letting queue/scheduled archives
        # persist a restart-stable id from the moment they dispatch (#1485).
        self.last_dispatch_subtask_id: str | None = None
        self._is_dual_nozzle: bool = False  # Set when device.extruder.info has >= 2 entries
        self._message_log: deque[MQTTLogEntry] = deque(maxlen=100)
        self._logging_enabled: bool = False
        self._last_message_time: float = 0.0  # Track when we last received a message
        # Count of report-topic messages received since the last (re)connect.
        # Lets check_staleness() distinguish "printer never sent a status
        # report" (typically a wrong / mis-cased serial) from a normal quiet
        # gap mid-session. _zero_report_hint_logged keeps the actionable hint
        # to once per client lifetime so the stale loop doesn't spam it (#1465).
        self._report_messages_since_connect: int = 0
        self._zero_report_hint_logged: bool = False
        # Set by mark_power_off() to the gcode_state held just before we
        # optimistically forced the printer to "unknown" (#2629). Restored on
        # the next inbound message, because message traffic proves the power
        # was never actually cut. None whenever no power-off is presumed.
        self._state_before_power_off: str | None = None
        # Raw-message fan-out for VP MQTT bridge (non-proxy modes republish the
        # printer's pushes verbatim to slicers connected to a virtual printer).
        # Handlers receive (topic, payload_bytes) before JSON parsing.
        self._raw_message_handlers: list[Callable[[str, bytes], None]] = []
        self._disconnection_event: threading.Event | None = None
        self._previous_ams_hash: str | None = None  # Track AMS changes
        # Track external-spool (vt_tray) identity changes separately: the AMS
        # hash above covers only AMS units, so an external-spool-only filament
        # swap would never re-trigger inventory reconciliation (#2575).
        self._previous_vt_tray_hash: str | None = None

        # Cache AMS firmware/SN from get_version in case it arrives before AMS status
        # Key: ams_id (int). Value: {'sw_ver': str, 'sn': str}
        self._ams_version_cache: dict[int, dict[str, str]] = {}

        # Track which (ams_id, field) warnings have already been emitted this connection
        # so that missing-serial / missing-firmware warnings fire only once per connection.
        self._ams_version_warned: set[tuple[int | str, str]] = set()

        # K-profile command tracking. One entry per in-flight extrusion_cali_get,
        # keyed by the sequence_id we sent, so two concurrent requests for
        # different nozzle sizes can't steal each other's response (#1748).
        # Value: {"nozzle": str, "event": asyncio.Event, "profiles": list | None}.
        self._sequence_id: int = 0
        self._pending_kprofile_requests: dict[str, dict] = {}
        # The printer's calibration table, one bucket per nozzle diameter.
        #
        # An extrusion_cali_get response is the complete table for *one* nozzle
        # size, and the printer answers whoever asks — including BambuStudio,
        # whose queries land on the same report topic we subscribe to. Assigning
        # each response straight to state.kprofiles therefore let any single
        # answer stand for the whole printer: a GitHub backup probing
        # 0.2/0.4/0.6/0.8 in turn finished on 0.8, which holds no profiles on a
        # 0.4+0.6 machine, and left the list empty until something refilled it.
        # Measured on the maintainer's H2 on 2026-08-25, and visible on the AMS
        # card because H2-series trays carry no `k` of their own — the slot's
        # K value is resolved from cali_idx against exactly this list.
        #
        # Keyed by diameter so a response only ever replaces the bucket it
        # actually describes; state.kprofiles is then the union across buckets.
        # An empty answer for a nozzle the printer doesn't have empties that
        # bucket alone.
        self._kprofiles_by_nozzle: dict[str, list] = {}
        # Acks for K-profile *writes* (extrusion_cali_set / extrusion_cali_del),
        # keyed by the sequence_id we sent. The printer echoes it back, measured
        # on both an X1C and an H2D (#2718). Filled by the MQTT thread, drained
        # by await_cali_ack.
        self._pending_cali_acks: dict[str, dict | None] = {}

        # Identifies the one project_file *we* dispatched, so its echo on the
        # topic can be told apart from a slicer's. One-shot: consumed by the
        # first frame that matches. See _project_file_key.
        self._own_project_file_key: str | None = None

        # Xcam hold timers - OrcaSlicer pattern: ignore incoming data for 3 seconds after command
        # Key: module_name, Value: timestamp when command was sent
        self._xcam_hold_start: dict[str, float] = {}
        self._xcam_hold_time: float = 3.0  # Ignore incoming data for 3 seconds after command

        # Track last requested tray ID for H2D dual-nozzle printers
        # H2D only reports slot number (0-3) in tray_now, not global tray ID
        # We use our tracked value to resolve the correct global ID
        self._last_load_tray_id: int | None = None

        # Captured ams_mapping from print commands on the request topic
        # Intercepts slicer/Bambuddy print commands to get the slot-to-tray mapping
        self._captured_ams_mapping: list[int] | None = None

        # True once we've seen (and normalised 16->6) an A2L AMS-Lite unit in the
        # AMS telemetry. Used to globalise the Lite's local `tray_now` to 24+slot.
        # See normalize_am_unit_id / a2l_lite_wire_ids and memory a2l-am-unit-16.
        self._has_a2l_am_unit: bool = False

        # Why the last connection attempt was refused by the printer, or None
        # when we have never seen a CONNACK failure since the last success.
        # Without this a rejected access code was completely invisible: paho
        # reports the follow-up disconnect as the generic "Unspecified error"
        # and `_on_connect`'s failure branch used to log nothing at all, so a
        # printer stuck in a reconnect loop looked identical whether it was
        # powered off, on the wrong IP, or refusing our credentials (#2698).
        # One of the CONNECT_ERROR_* slugs; the paired name is the paho reason
        # string, kept for the log line only.
        self.last_connect_error: str | None = None
        self.last_connect_error_name: str | None = None

        # Request topic subscription tracking
        # Some printer MQTT brokers (e.g. P1S, A1) reject subscriptions to the request
        # topic by killing the TCP connection. We detect this and gracefully degrade.
        # Check class-level cache first so new client instances don't retry known-bad subscriptions.
        self._request_topic_supported: bool = BambuMQTTClient._request_topic_cache.get(self.serial_number, True)
        self._request_topic_sub_mid: int | None = None
        self._request_topic_sub_time: float = 0.0
        self._request_topic_confirmed: bool = False

        # Developer mode probe: when the "fun" field is absent (A1/P1 printers),
        # we probe by sending an ams_filament_setting and checking the response.
        # "mqtt message verify failed" → dev mode OFF, success → dev mode ON.
        self._dev_mode_probed: bool = False
        self._dev_mode_needs_probe: bool = False  # True after seeing a pushall without "fun"
        self._dev_mode_probe_seq: str | None = None
        self._dev_mode_probe_time: float = 0.0  # monotonic timestamp when probe was sent
        self._dev_mode_probe_failures: int = 0  # consecutive unanswered probes
        # True while developer_mode=False came from HMS_MQTT_VERIFY_FAILED rather
        # than from the probe or the "fun" bit. The HMS is a latch, not a level:
        # the printer reports it until the fault clears, so when a later hms[]
        # arrives without it (user enabled Developer Mode and restarted the
        # printer) we drop back to "unknown" and let the probe re-run instead of
        # leaving a permanently-wrong False behind (#2732).
        self._dev_mode_from_hms: bool = False
        self._connect_time: float = 0.0  # monotonic timestamp of last _on_connect

        # Set when check_staleness() force-closes the socket to trigger reconnect.
        # Prevents _on_disconnect from redundantly broadcasting state (already done).
        self._stale_reconnecting: bool = False
        # Timestamp of last stale reconnect — prevents rapid-fire socket closes
        # when the frontend polls status faster than paho can reconnect.
        self._last_stale_reconnect: float = 0.0

        # Zombie session detection via ams_filament_setting response tracking (#887).
        # The dev-mode probe only runs on first connect; this catches zombie sessions
        # that develop later (telemetry flows but publishes silently fail).
        self._last_ams_cmd_time: float = 0.0  # monotonic time of last published command
        self._ams_cmd_unanswered: int = 0  # consecutive commands with no response

    @property
    def topic_subscribe(self) -> str:
        return f"device/{self.serial_number}/report"

    @property
    def topic_publish(self) -> str:
        return f"device/{self.serial_number}/request"

    @property
    def report_messages_since_connect(self) -> int:
        """Count of report-topic messages received since the latest (re)connect.

        Exposed for the connection diagnostic so it can distinguish "MQTT
        broker accepted us but the printer never published" (typically a
        wrong / mis-cased serial — #1622 follow-up to #1602) from a healthy
        bridge that happens to be idle right now. Zero immediately after a
        fresh connect is normal; zero after a full status push cycle is the
        wrong-serial failure mode.
        """
        return self._report_messages_since_connect

    # Maximum time (seconds) without a message before considering connection stale
    STALE_TIMEOUT = 60.0

    def is_stale(self) -> bool:
        """Check if the connection is stale (no messages for too long)."""
        if self._last_message_time == 0:
            return False  # Never received a message yet
        time_since_last = time.time() - self._last_message_time
        return time_since_last > self.STALE_TIMEOUT

    def mark_power_off(self) -> bool:
        """Presume the printer lost power (smart plug switched off).

        Optimistic: it skips the MQTT stale timeout so the UI updates at once.
        The presumption is undone by ``_on_message`` if the printer keeps
        talking — inbound traffic proves the power was never cut (#2629).
        Returns True when the state was actually changed.
        """
        if not self.state.connected:
            return False
        previous = self.state.state
        # Blank the state BEFORE recording what to restore. This runs on the
        # event loop while _on_message runs on the paho thread, and the restore
        # is a two-step (read saved state, compare against "unknown"). Writing
        # "unknown" first means an interleaved message either sees no saved
        # state yet (and skips, leaving the next message to restore) or sees a
        # consistent pair — never a saved state paired with a live state it
        # then discards, which would strand the printer on "unknown".
        self.state.connected = False
        self.state.state = "unknown"
        # Only the first mark wins: a second call before any message arrives
        # must not overwrite the real state with the "unknown" it just wrote.
        # Nothing to restore if the state was already blank.
        if self._state_before_power_off is None and previous not in ("", "unknown"):
            self._state_before_power_off = previous
        return True

    def _restore_state_after_false_power_off(self) -> bool:
        """Undo a presumed power-off once the printer proves it is alive.

        ``connected`` self-heals on the next message, but ``state`` does not:
        it is only rewritten when a payload carries ``gcode_state``, and the
        steady-state ``push_status`` frames are partial. Without this the
        forced "unknown" sticks until a full pushall (a manual Force Refresh),
        and the queue scheduler treats the printer as not idle the whole time
        (#2629). Returns True when a state was restored.
        """
        previous = self._state_before_power_off
        self._state_before_power_off = None
        if previous is None or self.state.state != "unknown":
            return False
        logger.info(
            "[%s] Printer still responding after presumed power-off — restoring state %s",
            self.serial_number,
            previous,
        )
        self.state.state = previous
        return True

    # Minimum seconds between stale reconnect attempts.  Frontend polls
    # status every few seconds — without a cooldown, each poll would
    # force-close the socket before paho has time to reconnect.
    STALE_RECONNECT_COOLDOWN = 30.0

    def check_staleness(self) -> bool:
        """Check staleness and update connected state if stale. Returns True if connected."""
        if self.state.connected and self.is_stale():
            # Don't force-close again if we already did recently — give paho
            # time to reconnect and the printer time to send its first message.
            now = time.time()
            if now - self._last_stale_reconnect < self.STALE_RECONNECT_COOLDOWN:
                return self.state.connected

            logger.warning(
                f"[{self.serial_number}] Connection stale - no message for {now - self._last_message_time:.1f}s, forcing reconnect"
            )
            # A connection that keeps going stale without ever receiving a
            # status report is almost always a wrong or mis-cased serial
            # number — the broker accepts the connection and the subscription
            # regardless, but the printer publishes to device/<real-serial>/
            # report, which is case-sensitive. Surface that once so the user
            # has something actionable instead of an endless reconnect loop.
            # Only meaningful once the *current* session has had time to receive
            # something. _report_messages_since_connect is reset by _on_connect,
            # so a reconnect that lands microseconds before this check leaves it
            # at 0 for reasons that have nothing to do with the serial — which is
            # how a healthy P1S ended up being told to go check its serial number
            # 1 ms after reconnecting (#2732). Requiring STALE_TIMEOUT of silence
            # on this session means the hint only fires when the printer really
            # has published nothing to the topic we subscribed to.
            # _connect_time of 0 means we have no timestamp to judge by (never went
            # through _on_connect); fall back to the old unconditional behaviour
            # rather than silently swallowing the hint.
            session_too_young = self._connect_time > 0 and (time.monotonic() - self._connect_time) < self.STALE_TIMEOUT
            if self._report_messages_since_connect == 0 and not session_too_young and not self._zero_report_hint_logged:
                self._zero_report_hint_logged = True
                logger.warning(
                    "[%s] Connected and subscribed, but the printer has sent zero "
                    "status reports. The most common cause is a wrong or mis-cased "
                    "serial number — the device/<serial>/report MQTT topic is "
                    "case-sensitive. Verify the serial number configured in Bambuddy "
                    "exactly matches the printer.",
                    self.serial_number,
                )
            self._last_stale_reconnect = now
            self.state.connected = False
            if self.on_state_change:
                self.on_state_change(self.state)
            # Route based on caller thread — see force_reconnect_stale_session.
            # check_staleness is normally called from FastAPI handlers (async,
            # gets the hard-reset path) but the dispatcher exists for safety.
            self._stale_reconnecting = True
            self._reset_client_for_reconnect()
        return self.state.connected

    def force_reconnect_stale_session(self, reason: str) -> None:
        # Heals the #887/#936/#1136 half-broken session: telemetry keeps
        # arriving but our publishes don't reach the printer.
        #
        # Two routing paths:
        #
        # Async-context callers (queue dispatch deadline)
        #   → full client teardown + fresh client_id. Wipes paho's client-side
        #     QoS 1 queue, which is exactly the #1136 reproducer: an unacked
        #     `project_file` from the broken session would otherwise replay on
        #     reconnect, mixing stale commands into the next dispatch and
        #     triggering 0500_4003 SD R/W on the printer.
        #
        # Paho-network-thread callers (line ~2604/~2623 — dev-mode probe and
        # ams_filament_setting zombie detection inside `_update_state`)
        #   → socket-close fallback. Calling `loop_stop()` from inside the
        #     network thread would self-join and deadlock; the safe pattern is
        #     to close the socket and let paho's own loop detect the broken
        #     connection and auto-reconnect (same instance, same client_id —
        #     queue replay is theoretically possible here but those paths have
        #     always done socket-close and #1136 was specifically triggered
        #     from the dispatch path).
        logger.warning("[%s] Forcing MQTT reconnect: %s", self.serial_number, reason)
        self._stale_reconnecting = True
        self.state.connected = False
        if self.on_state_change:
            self.on_state_change(self.state)
        self._reset_client_for_reconnect()

    def _reset_client_for_reconnect(self) -> None:
        """Route between hard-reset and socket-close based on caller thread.

        Hard-reset (preferred) requires we're not running on paho's network
        thread, since `loop_stop()` on the same thread deadlocks. Detect via
        ``asyncio.get_running_loop()`` — paho's callback thread has no loop;
        every legitimate hard-reset caller (FastAPI handlers, background
        async tasks) does."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            self._loop = loop
            self._hard_reset_client()
        else:
            self._socket_close_for_reconnect()

    def _hard_reset_client(self) -> None:
        """Tear down the paho client entirely and rebuild it with a fresh
        client_id, so the broker drops the old session and paho's local
        QoS 1 queue is gone. Must NOT be called from paho's network thread.
        Caller is responsible for setting ``_stale_reconnecting`` and
        broadcasting the disconnected state."""
        old_client = self._client
        self._client = None
        if old_client is not None:
            try:
                old_client.disconnect()  # MQTT DISCONNECT — broker drops session
            except Exception:
                pass
            try:
                old_client.loop_stop()  # blocks briefly until the network thread exits
            except Exception:
                pass
        # Skip reconnect if no asyncio loop is available (test environment or
        # pre-init). The next initial connect() call from PrinterManager will
        # set up the client fresh.
        if self._loop is None:
            return
        try:
            self.connect(loop=self._loop)
        except Exception as e:
            logger.error("[%s] Hard reset reconnect failed: %s", self.serial_number, e)

    def _socket_close_for_reconnect(self) -> None:
        """Close the underlying socket so paho's loop thread detects the
        broken connection and triggers auto-reconnect on the SAME client
        instance. Safe to call from paho's own network thread (the loop
        polls the socket on every iteration and handles a closed socket
        gracefully). Used as a fallback when hard-reset isn't safe; queue
        replay remains theoretically possible here but #1136 specifically
        traced through the dispatch-deadline path which now hard-resets."""
        if self._client:
            try:
                sock = self._client.socket()
                if sock:
                    sock.close()
            except Exception:
                pass

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.state.connected = True
            self.last_connect_error = None
            self.last_connect_error_name = None
            self._stale_reconnecting = False  # Clear stale-reconnect flag on successful connect
            # A dropped-and-restored MQTT session means the presumed power-off was
            # real (or at least that the printer restarted): there is nothing
            # legitimate left to restore, and the printer will send a full status
            # push shortly. Dropping the saved state keeps a stale one from being
            # broadcast ahead of the first real report (#2629, #1679).
            self._state_before_power_off = None
            # Reset per-connection warning state so warnings fire once per (re)connection
            self._ams_version_warned = set()
            # Preserve cached developer_mode across auto-reconnects to avoid
            # re-probing on every reconnect.  The probe (ams_filament_setting to
            # ext slot) can destabilize some firmware MQTT brokers, causing a
            # reconnect → probe → disconnect feedback loop (#887).  Only probe
            # once when developer_mode is truly unknown (first connect).
            # Reset probe tracking so stale timeout state doesn't carry over.
            self._dev_mode_probed = False
            self._dev_mode_needs_probe = False
            self._dev_mode_probe_seq = None
            self._dev_mode_probe_time = 0.0
            self._dev_mode_probe_failures = 0
            self._connect_time = time.monotonic()
            self._report_messages_since_connect = 0
            self._last_ams_cmd_time = 0.0
            self._ams_cmd_unanswered = 0
            # Drop any assignment verifications that were mid-flight before the
            # reconnect — their deadlines are stale and the tray state we would
            # compare against is about to be re-pushed from scratch (#2582).
            # Dropping is silent (no failure event) on purpose.
            self._pending_assignments.clear()
            client.subscribe(self.topic_subscribe)
            # Subscribe to request topic for ams_mapping capture (if supported by broker)
            if self._request_topic_supported:
                result, mid = client.subscribe(self.topic_publish)
                if result == mqtt.MQTT_ERR_SUCCESS:
                    self._request_topic_sub_mid = mid
                    self._request_topic_sub_time = time.time()
                    self._request_topic_confirmed = False
                else:
                    logger.warning(
                        "[%s] Failed to send request topic subscription",
                        self.serial_number,
                    )
                    self._request_topic_supported = False
                    BambuMQTTClient._request_topic_cache[self.serial_number] = False
            # Request full status update (includes nozzle info in push_status response)
            self._request_push_all()
            # Request firmware version info
            self._request_version()
            # Note: get_accessories returns stale nozzle data on H2D, so we don't use it.
            # The correct nozzle data comes from push_status.
            # Prime K-profile request (Bambu printers often ignore first request)
            self._prime_kprofile_request()
            # Immediately broadcast connection state change
            if self.on_state_change:
                self.on_state_change(self.state)
        else:
            self.state.connected = False
            self._record_connect_refusal(rc)

    def _record_connect_refusal(self, rc) -> None:
        """Log and remember why the printer refused the MQTT connection.

        The failure branch of ``_on_connect`` used to be a bare
        ``connected = False``, which threw away the only signal that says
        *why* a printer never comes online. The user-visible result was a
        30-second reconnect loop logging nothing but paho's generic
        ``MQTT disconnected: rc=Unspecified error`` — indistinguishable from a
        powered-off printer, so "my printer won't print" reports could not be
        triaged without a round trip (#2698).

        Never logs the access code itself; the code is the likely culprit but
        printing it would put a credential in every support bundle.
        """
        code = getattr(rc, "value", rc)
        name = rc.getName() if hasattr(rc, "getName") else str(rc)
        self.last_connect_error_name = name
        if isinstance(code, int) and code in _CONNACK_AUTH_REJECTED:
            self.last_connect_error = CONNECT_ERROR_AUTH_REJECTED
            logger.warning(
                "[%s] MQTT connection refused by the printer: %s (code %s). The access code "
                "or serial number is wrong — the access code changes every time LAN Only or "
                "Developer Mode is toggled, so re-read it from the printer's screen.",
                self.serial_number,
                name,
                code,
            )
        else:
            self.last_connect_error = CONNECT_ERROR_REFUSED
            logger.warning(
                "[%s] MQTT connection refused by the printer: %s (code %s).",
                self.serial_number,
                name,
                code,
            )

    def _on_subscribe(self, client, userdata, mid, reason_code_list, properties=None):
        """Handle SUBACK responses to detect request topic subscription rejection."""
        if mid == self._request_topic_sub_mid:
            for rc in reason_code_list:
                if rc.is_failure:
                    logger.warning(
                        "[%s] Request topic subscription rejected (code=%d: %s). "
                        "ams_mapping capture from slicer-initiated prints unavailable.",
                        self.serial_number,
                        rc.value,
                        rc.getName(),
                    )
                    self._request_topic_supported = False
                    BambuMQTTClient._request_topic_cache[self.serial_number] = False
                else:
                    logger.info(
                        "[%s] Request topic subscription accepted. "
                        "ams_mapping capture enabled for slicer-initiated prints.",
                        self.serial_number,
                    )
                    self._request_topic_confirmed = True
                    BambuMQTTClient._request_topic_cache[self.serial_number] = True
                    BambuMQTTClient._request_topic_probe_failures.pop(self.serial_number, None)
            self._request_topic_sub_mid = None
            self._request_topic_sub_time = 0.0

    def _on_disconnect(self, client, userdata, disconnect_flags=None, rc=None, properties=None):
        # Always unblock disconnect() callers, regardless of whether we suppress
        # the state broadcast below.  disconnect() sets _disconnection_event and
        # waits on it — every callback path must fire it.
        if self._disconnection_event:
            self._disconnection_event.set()

        # If we intentionally closed the socket for stale reconnect, don't broadcast
        # another state change — check_staleness() already set connected=False and
        # notified the UI.  Just log and let paho auto-reconnect.
        if self._stale_reconnecting:
            logger.info(
                "[%s] Disconnect callback after stale reconnect (expected), rc=%s",
                self.serial_number,
                rc,
            )
            return

        # Ignore spurious disconnect callbacks if we've received a message recently
        # Paho-mqtt sometimes fires disconnect callbacks while the connection is still active.
        # BUT: never suppress error disconnects (keepalive timeout, connection lost, etc.)
        # — only suppress when rc indicates a clean/normal disconnect.
        is_error_disconnect = rc is not None and hasattr(rc, "is_failure") and rc.is_failure
        time_since_last_message = time.time() - self._last_message_time
        if not is_error_disconnect and time_since_last_message < 10.0 and self._last_message_time > 0:
            logger.debug(
                f"[{self.serial_number}] Ignoring spurious disconnect (last message {time_since_last_message:.1f}s ago)"
            )
            return

        # Carry the last CONNACK refusal into the disconnect line. paho reports
        # the drop that follows a refused CONNACK as "Unspecified error", so on
        # its own this line says nothing useful about a printer that is looping
        # on bad credentials — and this is the line that fills a support bundle
        # (#2698).
        if self.last_connect_error:
            logger.warning(
                "[%s] MQTT disconnected: rc=%s, flags=%s (last connection attempt was refused: %s)",
                self.serial_number,
                rc,
                disconnect_flags,
                self.last_connect_error_name,
            )
        else:
            logger.warning("[%s] MQTT disconnected: rc=%s, flags=%s", self.serial_number, rc, disconnect_flags)

        # Detect if request topic subscription caused the disconnect.
        # If we just subscribed and got disconnected before any SUBACK confirmation,
        # the broker likely killed the connection due to the unauthorized subscription.
        if (
            self._request_topic_sub_time > 0
            and not self._request_topic_confirmed
            and time.time() - self._request_topic_sub_time < 10.0
            # A disconnect we asked for says nothing about the subscription.
            and self._disconnection_event is None
        ):
            failures = BambuMQTTClient._request_topic_probe_failures.get(self.serial_number, 0) + 1
            BambuMQTTClient._request_topic_probe_failures[self.serial_number] = failures
            if failures >= BambuMQTTClient._REQUEST_TOPIC_PROBE_LIMIT:
                logger.warning(
                    "[%s] Disconnected shortly after request topic subscription %d times. "
                    "Disabling request topic for this printer — ams_mapping capture from "
                    "slicer-initiated prints is unavailable, and their filament will be "
                    "attributed from the printer's own tray reporting instead.",
                    self.serial_number,
                    failures,
                )
                self._request_topic_supported = False
                BambuMQTTClient._request_topic_cache[self.serial_number] = False
            else:
                logger.info(
                    "[%s] Disconnected shortly after request topic subscription (%d/%d). "
                    "Retrying it on the next connection before giving up.",
                    self.serial_number,
                    failures,
                    BambuMQTTClient._REQUEST_TOPIC_PROBE_LIMIT,
                )
        self._request_topic_sub_mid = None
        self._request_topic_sub_time = 0.0

        self.state.connected = False
        if self.on_state_change:
            self.on_state_change(self.state)

    def _on_message(self, client, userdata, msg):
        for handler in self._raw_message_handlers:
            try:
                handler(msg.topic, msg.payload)
            except Exception:
                logger.exception(
                    "[%s] raw-message handler crashed for topic=%s",
                    self.serial_number,
                    msg.topic,
                )
        try:
            try:
                raw = msg.payload.decode()
            except UnicodeDecodeError:
                # Some firmware versions (e.g. A1 Mini 01.07.02.00) send payloads
                # with non-UTF-8 bytes. Replace invalid bytes to keep JSON parseable.
                raw = msg.payload.decode(errors="replace")
                logger.warning(
                    "[%s] MQTT payload contained non-UTF-8 bytes (topic=%s, len=%d)",
                    self.serial_number,
                    msg.topic,
                    len(msg.payload),
                )
            payload = json.loads(raw)
            # Track last message time - receiving a message proves we're connected
            self._last_message_time = time.time()
            self.state.connected = True

            # Intercept request-topic messages (print commands from slicer/Bambuddy)
            if msg.topic == self.topic_publish:
                # Record it before returning. This topic carries every command
                # travelling *to* the printer, including the ones Bambu Studio
                # sends, and it used to be the one thing an MQTT capture could
                # never show -- which is why "what does Studio put in the drying
                # command?" had no answer from a user's log (#2774). Filed as
                # "out" so the direction filter groups it with our own commands
                # rather than with printer telemetry; anything sent through
                # send_command lands twice, once on publish and once on the
                # broker's echo, and the pair is itself evidence the command
                # reached the broker.
                if self._logging_enabled:
                    self._message_log.append(
                        MQTTLogEntry(
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            topic=msg.topic,
                            direction="out",
                            payload=payload,
                        )
                    )
                self._handle_request_message(payload)
                return

            # Count status reports per connection so check_staleness() can tell
            # "printer never sent a report" apart from a mid-session quiet gap.
            if msg.topic == self.topic_subscribe:
                self._report_messages_since_connect += 1
                # Only report-topic traffic proves the *printer* is alive — the
                # request topic also carries slicer/Bambuddy commands.
                if self._state_before_power_off is not None:
                    if self._restore_state_after_false_power_off() and self.on_state_change:
                        self.on_state_change(self.state)

            # Log message if logging is enabled
            if self._logging_enabled:
                self._message_log.append(
                    MQTTLogEntry(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        topic=msg.topic,
                        direction="in",
                        payload=payload,
                    )
                )
            self._process_message(payload)
        except json.JSONDecodeError:
            pass  # Ignore non-JSON MQTT messages (e.g. binary or malformed payloads)

    def _handle_request_message(self, data: dict) -> None:
        """Intercept print commands on the request topic to capture ams_mapping."""
        print_data = data.get("print", {})
        if not isinstance(print_data, dict):
            return
        command = print_data.get("command", "")
        if command == "project_file":
            # Where the dispatcher put the sliced file. Captured for every
            # project_file, ours included: we publish to this same topic and
            # subscribe to it, so whoever dispatched last wins, which is exactly
            # the print the archive lookup is about to go looking for (#2780).
            url = print_data.get("url")
            if isinstance(url, str) and url:
                self.state.current_project_url = url
                self.state.last_project_url = url
            if "ams_mapping" in print_data:
                self._captured_ams_mapping = print_data["ams_mapping"]
                logger.info(
                    "[%s] Captured ams_mapping from print command: %s",
                    self.serial_number,
                    self._captured_ams_mapping,
                )
            # Diagnostic for #1162 follow-up (X2D + FTS routing): when a
            # slicer-launched project_file passes through the request topic,
            # log the full payload so we can diff Studio's field set against
            # ours.
            #
            # This used to read `sequence_id != "20000"`, on the belief that
            # 20000 was ours alone. It is not: 20000 is the slicer convention
            # Bambuddy adopted -- bind_server documents the slicer sending it
            # during detect, and measured on the wire OrcaSlicer dispatched
            # 20000 then 20001 while BambuStudio was on 20009/20010, both
            # counting up from the same base. So the test swallowed whichever
            # slicer dispatch happened to land on 20000, which on a fresh
            # slicer start is the first one. Match our own dispatch instead.
            if self._project_file_key(print_data) == self._own_project_file_key:
                self._own_project_file_key = None
            else:
                logger.info(
                    "[%s] External project_file payload: %s",
                    self.serial_number,
                    json.dumps(print_data),
                )

    def _capture_report_project_file(self, print_data: dict) -> None:
        """Read a print's destination off a ``project_file`` *response* (#1820).

        ``_handle_request_message`` only ever sees the request topic, so a print
        started from the printer's own touchscreen -- which publishes nothing --
        left ``current_project_url`` at None, and the storage verdict fell
        through to the ``sdcard`` fallback for the one case it was written for.
        On an H2S that flag is True (its "card" is the internal eMMC), so the
        verdict came back reachable and the ~110-connection sweep ran in full.

        The printer does announce it: an unsolicited ``project_file`` response
        on the report topic, ~2 s before ``gcode_state`` reaches PREPARE,
        carrying ``file:///userdata/model/history/<name>.gcode.3mf``.

        This also covers an install nobody had in view: some brokers refuse the
        request-topic subscription, and on those no print of any kind has ever
        populated the field.

        Both kinds of ``project_file`` on this topic are read -- the printer's
        echo of a dispatch and a screen start -- because both name the
        destination in ``url``, which is the only thing the verdict wants. What
        this must NOT do is reuse ``_handle_request_message``'s "External
        project_file payload" diagnostic: our own dispatch is echoed on *both*
        topics, the request-topic echo arrives first and clears
        ``_own_project_file_key``, so by the time this frame lands the key is
        already None and every Bambuddy-started print would log itself as
        someone else's.
        """
        # Same shape as _handle_request_message: the frame is whatever the
        # printer put on the wire, and this is the first thing to touch it.
        if not isinstance(print_data, dict) or print_data.get("command") != "project_file":
            return
        # A refused dispatch names a file that was never written. Acting on it
        # would pin an archive on a destination nothing ever went to.
        if print_data.get("result") != "SUCCESS":
            return
        url = print_data.get("url")
        if not isinstance(url, str) or not url:
            return
        if self.state.current_project_url != url:
            logger.info(
                "[%s] Print destination from the report topic: %s",
                self.serial_number,
                url,
            )
        self.state.current_project_url = url
        self.state.last_project_url = url
        # On a screen start this frame is the only place the mapping appears --
        # no slicer ever sent one. Fill a gap only: when the request topic
        # already captured this print's mapping that copy is the slicer's own,
        # and the echo can arrive without the field at all.
        if self._captured_ams_mapping is None and isinstance(print_data.get("ams_mapping"), list):
            self._captured_ams_mapping = print_data["ams_mapping"]
            logger.info(
                "[%s] Captured ams_mapping from print response: %s",
                self.serial_number,
                self._captured_ams_mapping,
            )

    @staticmethod
    def _project_file_key(print_data: dict) -> str:
        """Identity of a project_file dispatch, for telling ours from a slicer's.

        Sequence id alone cannot do it -- every slicer counts up from the same
        20000 -- so this also carries the file and its destination, which differ
        between any two real dispatches.
        """
        return "|".join(str(print_data.get(field, "")) for field in ("sequence_id", "file", "url", "subtask_name"))

    def _debug_on_change(self, key: str, value: object, msg: str, *args: object) -> None:
        """``logger.debug``, but only when ``value`` differs from the last call for ``key``.

        The state dumps in the push_status handler fire whenever their field is
        *present* in the frame — and a full push_status carries every field, so
        they fire on every frame regardless of whether anything changed. Several
        even say "updated" or "changes" in their own comment while doing nothing
        of the sort.

        On one printer that is ~1.5 lines/s and nobody noticed. On the 19-printer
        farm in #2555 it is ~100 lines/s, which fills the 5 MB log inside five
        minutes: the reporter enabled debug logging as asked and the support
        bundle came back holding under five minutes of history, almost none of it
        about the queue problem we were chasing. 27,727 of its 29,830 lines were
        these dumps.

        Deduplicating on the value keeps every transition — which is the only part
        anyone reads these lines for — and drops the steady-state repetition.
        ``value`` must capture everything interpolated into ``msg``, or a change
        will be swallowed; pass a tuple when the message renders several fields.
        """
        if not logger.isEnabledFor(logging.DEBUG):
            # Debug logging is toggled at RUNTIME (POST /support/debug-logging),
            # and these clients outlive the toggle. Letting INFO-level frames warm
            # the cache would be self-defeating: the operator turns debug on
            # precisely to see the printer's current state, and a cache already
            # holding every steady-state value would suppress that baseline until
            # something happened to change. On an idle printer the bundle would
            # come back with none of these lines at all.
            #
            # So while debug is off we record nothing and drop whatever we had.
            # Every enable then starts cold and dumps a full baseline on the next
            # frame, exactly as it did before this method existed.
            self._debug_last.clear()
            return
        if self._debug_last.get(key) == value:
            return
        self._debug_last[key] = value
        logger.debug(msg, *args)

    def _process_message(self, payload: dict):
        """Process incoming MQTT message from printer."""
        # Handle top-level AMS data (comes outside of "print" key)
        # Wrap in try/except to prevent breaking the MQTT connection
        if "ams" in payload:
            try:
                self._handle_ams_data(payload["ams"])
            except Exception as e:
                logger.error("[%s] Error handling AMS data: %s", self.serial_number, e)

        # Handle xcam data (camera settings and AI detection) at top level
        if "xcam" in payload:
            xcam_data = payload["xcam"]
            logger.debug("[%s] Received xcam data at top level: %s", self.serial_number, xcam_data)
            self._parse_xcam_data(xcam_data)
            # Fire state change callback for top-level xcam (not nested in "print")
            if "print" not in payload and self.on_state_change:
                self.on_state_change(self.state)

        # Handle system responses (accessories info, etc.)
        if "system" in payload:
            system_data = payload["system"]
            logger.debug("[%s] Received system data: %s", self.serial_number, system_data)
            self._handle_system_response(system_data)

        # Handle info responses (firmware version info from get_version command)
        if "info" in payload:
            info_data = payload["info"]
            if isinstance(info_data, dict) and info_data.get("command") == "get_version":
                self._handle_version_info(info_data)

        # Parse WiFi signal at top level (some printers send it here)
        if "wifi_signal" in payload:
            wifi_signal = payload["wifi_signal"]
            if isinstance(wifi_signal, (int, float)):
                self.state.wifi_signal = int(wifi_signal)
            elif isinstance(wifi_signal, str):
                try:
                    self.state.wifi_signal = int(wifi_signal.replace("dBm", "").strip())
                except ValueError:
                    pass  # Ignore unparseable wifi_signal strings; field is non-critical

            # Detect ethernet: wifi_signal == -90 is a sentinel for "WiFi disabled/ethernet"
            from backend.app.utils.printer_models import has_ethernet

            if has_ethernet(self.model):
                self.state.wired_network = self.state.wifi_signal == -90

        # Parse developer LAN mode from top-level "fun" field
        # Some firmware versions send "fun" at the top level, others inside "print"
        if "fun" in payload:
            try:
                fun_val = payload["fun"]
                fun_int = fun_val if isinstance(fun_val, int) else int(fun_val, 16)
                self.state.developer_mode = (fun_int & 0x20000000) == 0
            except (ValueError, TypeError):
                pass

        if "print" in payload:
            print_data = payload["print"]

            # Before anything reads the state: this is where a touchscreen-
            # started print announces where its file lives, and the print-start
            # handler asks ~2 s later (#1820).
            self._capture_report_project_file(print_data)

            # Check if xcam is nested inside print data
            if "xcam" in print_data:
                logger.debug("[%s] Found xcam inside print data: %s", self.serial_number, print_data["xcam"])
                self._parse_xcam_data(print_data["xcam"])

            # Log when we see gcode_state changes
            if "gcode_state" in print_data:
                logger.debug(
                    f"[{self.serial_number}] Received gcode_state: {print_data.get('gcode_state')}, "
                    f"gcode_file: {print_data.get('gcode_file')}, subtask_name: {print_data.get('subtask_name')}"
                )

            # AMS Filament Backup state lives in bit 18 of top-level print.cfg on
            # new-protocol printers. Verified against OrcaSlicer's
            # DeviceManager.cpp:4961 SetAutoRefillEnabled(get_flag_bits(cfg, 18))
            # and live H2D ON/OFF capture 2026-06-20.
            #
            # Hold-timer guard: when the user just toggled via the badge, the
            # next 1-2 push_status frames may still carry the printer's OLD cfg
            # for ~3 s before the firmware reflects the change. Without this
            # gate the UI would flicker ON→OFF→ON. Same pattern xcam uses.
            new_backup = parse_ams_filament_backup_from_cfg(print_data.get("cfg"))
            if new_backup is not None and new_backup != self.state.ams_filament_backup:
                hold_start = self._xcam_hold_start.get("print_option_auto_switch_filament")
                if hold_start is not None and (time.time() - hold_start) <= self._xcam_hold_time:
                    logger.debug(
                        "[%s] AMS Filament Backup push ignored (hold active for %.1fs)",
                        self.serial_number,
                        time.time() - hold_start,
                    )
                else:
                    logger.info(
                        "[%s] AMS Filament Backup: %s",
                        self.serial_number,
                        "ON" if new_backup else "OFF",
                    )
                    self.state.ams_filament_backup = new_backup
                    self._xcam_hold_start.pop("print_option_auto_switch_filament", None)

            # Detect dual-nozzle BEFORE processing AMS data (tray_now disambiguation needs it)
            # device.extruder.info with >= 2 entries only exists on dual-nozzle printers (H2D, H2D Pro)
            if not self._is_dual_nozzle and "device" in print_data:
                dev = print_data.get("device")
                if isinstance(dev, dict):
                    ext_info = dev.get("extruder", {}).get("info", [])
                    if isinstance(ext_info, list) and len(ext_info) >= 2:
                        self._is_dual_nozzle = True
                        logger.info("[%s] Detected dual-nozzle printer from device.extruder.info", self.serial_number)

            # Must run before _handle_ams_data: the per-AMS inlet binding is read
            # out of the AMS info bits, but only means anything once we know a
            # switch is installed. Parsing them the other way round would lose
            # the binding on every frame where the two arrive together.
            self._parse_fila_switch(print_data)

            # Handle AMS data that comes inside print key
            if "ams" in print_data:
                try:
                    self._handle_ams_data(print_data["ams"])
                except Exception as e:
                    logger.error("[%s] Error handling AMS data from print: %s", self.serial_number, e)

            # Handle vir_slot (H2-series external spool data) — list of external trays
            # Process vir_slot FIRST so it takes priority over vt_tray
            if "vir_slot" in print_data:
                vir_slot = print_data["vir_slot"]
                if isinstance(vir_slot, list) and vir_slot:
                    # Fix: single-nozzle printers (X1C, P1S, A1) report their single
                    # external slot with id=255 in vir_slot, but tray_now=254 when active.
                    # Remap id=255→254 for single-slot printers so active detection works.
                    # Dual-nozzle (H2D) has 2 slots: id=254 (Ext-L) and id=255 (Ext-R).
                    if len(vir_slot) == 1 and str(vir_slot[0].get("id", "")) == "255":
                        vir_slot[0]["id"] = "254"
                    self.state.raw_data["vt_tray"] = vir_slot

            # Handle vt_tray (virtual tray / external spool) data
            # Only use vt_tray if vir_slot is NOT in this message AND we don't already
            # have vir_slot data (H2-series sends vt_tray as a single active spool dict
            # which would overwrite the correct multi-slot vir_slot data)
            if "vt_tray" in print_data and "vir_slot" not in print_data:
                vt_tray = print_data["vt_tray"]
                existing = self.state.raw_data.get("vt_tray")
                # Don't let a single-spool vt_tray dict overwrite multi-slot vir_slot data
                if isinstance(vt_tray, dict) and isinstance(existing, list) and len(existing) > 1:
                    pass  # Keep the vir_slot data
                else:
                    if isinstance(vt_tray, dict):
                        vt_tray = [vt_tray]
                    self.state.raw_data["vt_tray"] = vt_tray

            # The regular AMS change-hash (in _handle_ams_data) only sees AMS
            # units, and _handle_ams_data runs before this block — so a change
            # to the external spool alone (e.g. swapping generic TPU for generic
            # ABS on the printer) never re-triggers on_ams_change, leaving a
            # stale inventory assignment on the ams_id=255 slot (#2575). Detect
            # external-spool identity changes here and fire the same callback.
            self._maybe_trigger_external_spool_change()

            # Parse ams_status directly from print data (NOT from print.ams)
            # ams_status is a combined value: lower 8 bits = sub status, bits 8-15 = main status
            # Main status: 0=idle, 1=filament_change, 2=rfid_identifying, 3=assist, 4=calibration
            # Sub status (when main=1): 2=heating, 3=AMS feeding, 4=retract, 6=push, 7=purge
            if "ams_status" in print_data:
                raw_ams_status = print_data["ams_status"]
                if isinstance(raw_ams_status, str):
                    try:
                        self.state.ams_status = int(raw_ams_status)
                    except ValueError:
                        self.state.ams_status = 0
                else:
                    self.state.ams_status = raw_ams_status if raw_ams_status is not None else 0

                # Compute main and sub status
                self.state.ams_status_sub = self.state.ams_status & 0xFF
                self.state.ams_status_main = (self.state.ams_status >> 8) & 0xFF

                # Log when ams_status changes (for filament change tracking debug)
                self._debug_on_change(
                    "ams_status:print",
                    self.state.ams_status,
                    "[%s] ams_status: %s (main=%s, sub=%s)",
                    self.serial_number,
                    self.state.ams_status,
                    self.state.ams_status_main,
                    self.state.ams_status_sub,
                )

            # Check for command responses
            if "command" in print_data:
                cmd = print_data.get("command")
                logger.debug("[%s] Received command response: %s", self.serial_number, cmd)
                if cmd in ("extrusion_cali_set", "extrusion_cali_del"):
                    # INFO, not debug: this is the printer's verdict on a write
                    # the user just made, and it was invisible in support
                    # bundles for as long as it sat at DEBUG (#2718). Same
                    # reasoning as ams_filament_drying below.
                    logger.info(
                        "[%s] %s response: result=%s reason=%s seq=%s",
                        self.serial_number,
                        cmd,
                        print_data.get("result"),
                        print_data.get("reason", ""),
                        print_data.get("sequence_id"),
                    )
                    logger.debug("[%s] %s full response: %s", self.serial_number, cmd, print_data)
                    ack_seq = str(print_data.get("sequence_id", ""))
                    if ack_seq in self._pending_cali_acks:
                        self._pending_cali_acks[ack_seq] = print_data
                elif cmd in ("extrusion_cali_sel", "ams_filament_setting"):
                    logger.debug("[%s] %s response: %s", self.serial_number, cmd, print_data)
                    # A refused ams_filament_setting is the printer's verdict on
                    # a write the user just made, and at DEBUG it never reached
                    # a support bundle: #2756 reported six manual Configure Slot
                    # attempts on an X1C, each returning HTTP 200 with the
                    # read-back still showing the previous profile, and no
                    # record of what the printer said about any of them. Same
                    # promotion as extrusion_cali_set (#2718) and
                    # ams_filament_drying (#1447) — but only on a non-success,
                    # because unlike those two this command is not rare: every
                    # spool assignment and every K-profile re-apply sends one,
                    # so promoting each ack would bury the interesting line.
                    #
                    # The developer-mode probe is excluded. It sends this exact
                    # command to the external slot precisely to see it refused
                    # on P1 firmware, so its failure is a normal reading rather
                    # than a fault. Its response is still matched below (this
                    # runs before _handle_dev_mode_probe_response clears the
                    # seq), and user-initiated commands can't be mistaken for
                    # it — they publish a hardcoded sequence_id of "0".
                    result = print_data.get("result")
                    is_dev_mode_probe = (
                        self._dev_mode_probe_seq is not None
                        and print_data.get("sequence_id") == self._dev_mode_probe_seq
                    )
                    if (
                        cmd == "ams_filament_setting"
                        and not is_dev_mode_probe
                        and isinstance(result, str)
                        and result.lower() != "success"
                    ):
                        logger.info(
                            "[%s] ams_filament_setting refused: result=%s reason=%s ams_id=%s tray_id=%s",
                            self.serial_number,
                            result,
                            print_data.get("reason", ""),
                            print_data.get("ams_id"),
                            print_data.get("tray_id"),
                        )
                # AMS drying responses are rare (user-initiated only) and the
                # full payload — including `result` and any `reason` code —
                # is the only way to diagnose silent rejections like #1447.
                # INFO level so the body lands in support bundles by default.
                elif cmd == "ams_filament_drying":
                    logger.info("[%s] ams_filament_drying response: %s", self.serial_number, print_data)
                # Check for developer mode probe response
                if (
                    cmd == "ams_filament_setting"
                    and self._dev_mode_probe_seq is not None
                    and print_data.get("sequence_id") == self._dev_mode_probe_seq
                ):
                    self._handle_dev_mode_probe_response(print_data)
                # Track user-initiated ams_filament_setting responses (#887
                # zombie detection). Reset both the timer AND the unanswered
                # counter on ANY response — the response proves the channel is
                # alive, so the counter must not stay armed even when the
                # watchdog already zeroed `_last_ams_cmd_time` on a previous
                # tick. The original `and self._last_ams_cmd_time > 0` guard
                # caused #1164: one sluggish response (>10s) would set the
                # counter to 1 and zero the timer; the late response arrived
                # but was ignored by this branch (timer is 0); the counter
                # stayed at 1 indefinitely; the very next slow response —
                # possibly hours later, on a totally unrelated command — would
                # take it to 2 and force-reconnect, surfacing as "filament
                # config doesn't reach the printer ~6 changes in".
                elif cmd == "ams_filament_setting":
                    self._last_ams_cmd_time = 0.0
                    self._ams_cmd_unanswered = 0
            is_kprofile_response = "command" in print_data and print_data.get("command") == "extrusion_cali_get"
            if is_kprofile_response:
                self._handle_kprofile_response(print_data)

            # An extrusion_cali_get response echoes the *requested* nozzle
            # diameter (get_kprofiles probes 0.2/0.4/0.6/0.8 in turn), not the
            # installed hardware. Feeding it to _update_state clobbered the real
            # nozzle size (#2663) — typically leaving 0.8, the last size probed,
            # which then failed the #1899 dispatch guard. The response carries no
            # status telemetry, so skip it; the true nozzle comes from pushall.
            # (Same reasoning as get_accessories in _handle_system_response.)
            if not is_kprofile_response:
                self._update_state(print_data)

    def _handle_system_response(self, data: dict):
        """Handle system responses including accessories info.

        Note: get_accessories returns stale/incorrect nozzle_type data on H2D.
        The correct nozzle data comes from push_status, so we don't update
        nozzle type/diameter from get_accessories. We just log the response
        for debugging purposes.
        """
        command = data.get("command")

        if command == "get_accessories":
            # Log response for debugging - but DON'T use it to update nozzle data
            # because it returns stale values (e.g., 'stainless_steel' when the
            # actual nozzle is 'HH01' hardened steel high-flow)
            logger.debug("[%s] Accessories response (not used for nozzle data): %s", self.serial_number, data)

    def _handle_version_info(self, data: dict):
        """Handle version info response from get_version command.

        Parses firmware version from the 'ota' module in the module list.
        Also extracts AMS unit firmware versions from AMS modules and stores
        them on the corresponding AMS unit in raw_data so the status route can
        expose them to the frontend.

        AMS module naming conventions (numeric suffix is the AMS unit ID):
        - ``ams/<id>``  – original AMS
        - ``n3f/<id>``  – AMS 2 Pro (H2D Pro and similar)
        - ``n3s/<id>``  – AMS HT (H2D Pro and similar)

        Message format:
        {
            "command": "get_version",
            "module": [
                {"name": "ota", "sw_ver": "01.08.05.00"},
                {"name": "rv1126", "sw_ver": "00.00.14.74"},
                {"name": "ams/0", "sw_ver": "00.00.06.96", "sn": "ABC123"},
                {"name": "n3f/0", "sw_ver": "03.00.21.29", "sn": "19C06A552504488"},
                {"name": "n3s/128", "sw_ver": "03.00.21.29", "sn": "19F06A561801096"},
                ...
            ]
        }
        """
        modules = data.get("module", [])
        if not isinstance(modules, list):
            return

        state_changed = False
        for module in modules:
            if not isinstance(module, dict):
                continue
            if module.get("name") == "ota":
                version = module.get("sw_ver")
                if version:
                    old_version = self.state.firmware_version
                    self.state.firmware_version = version
                    if old_version != version:
                        logger.info("[%s] Firmware version: %s", self.serial_number, version)
                    state_changed = True
                break

        # Extract AMS unit firmware versions from AMS modules.
        # See module-level _AMS_MODULE_PREFIXES for supported naming conventions.
        # Always cache regardless of whether AMS data has arrived yet — get_version
        # often arrives before the first push_status, so caching must be unconditional.
        ams_raw = self.state.raw_data.get("ams")
        for module in modules:
            if not isinstance(module, dict):
                continue
            name = module.get("name", "")
            if not any(name.startswith(prefix) for prefix in _AMS_MODULE_PREFIXES):
                continue
            try:
                ams_id = int(name.split("/", 1)[1])
            except (ValueError, IndexError):
                continue
            sw_ver = module.get("sw_ver", "")
            sn = module.get("sn", "")

            # Extract module type from prefix (e.g. "ams/0" → "ams", "n3f/0" → "n3f")
            module_type = name.split("/", 1)[0]

            # Always cache so _apply_ams_version_cache can apply it when AMS data arrives
            if sw_ver or sn or module_type:
                self._ams_version_cache[ams_id] = {"sw_ver": sw_ver, "sn": sn, "module_type": module_type}
                state_changed = True

            # Also directly update any AMS unit already present in raw_data
            if ams_raw and isinstance(ams_raw, list):
                for ams_unit in ams_raw:
                    if not isinstance(ams_unit, dict):
                        continue
                    try:
                        unit_id = int(ams_unit.get("id")) if ams_unit.get("id") is not None else None
                    except (ValueError, TypeError):
                        unit_id = None
                    if unit_id == ams_id:
                        if sw_ver:
                            ams_unit["sw_ver"] = sw_ver
                            logger.debug("[%s] AMS %s firmware: %s", self.serial_number, ams_id, sw_ver)
                        # Only set sn from version info if not already present in AMS data
                        if sn and not ams_unit.get("sn"):
                            ams_unit["sn"] = sn
                        if module_type:
                            ams_unit["module_type"] = module_type
                        break

        # Trigger state change callback AFTER both loops so AMS sn/sw_ver are
        # included in the broadcast (not just the printer firmware version).
        if state_changed and self.on_state_change:
            self.on_state_change(self.state)

        # Warn if any AMS unit is still missing serial number or firmware version
        # after processing the version info response. Warn only once per connection
        # to avoid repeated noise on older firmware that doesn't report these fields.
        if ams_raw and isinstance(ams_raw, list):
            for ams_unit in ams_raw:
                if not isinstance(ams_unit, dict):
                    continue
                ams_id = ams_unit.get("id", "?")
                if not ams_unit.get("sn") and not ams_unit.get("serial_number"):
                    key = (ams_id, "sn")
                    if key not in self._ams_version_warned:
                        self._ams_version_warned.add(key)
                        logger.warning(
                            "[%s] AMS unit %s: serial number not available in version info",
                            self.serial_number,
                            ams_id,
                        )
                if not ams_unit.get("sw_ver"):
                    key = (ams_id, "sw_ver")
                    if key not in self._ams_version_warned:
                        self._ams_version_warned.add(key)
                        logger.warning(
                            "[%s] AMS unit %s: firmware version not available in version info",
                            self.serial_number,
                            ams_id,
                        )

    def _apply_ams_version_cache(self, ams_list: list) -> None:
        """Apply cached AMS firmware/SN (from get_version) onto an AMS list in-place.

        get_version may arrive before pushall/AMS status, and AMS unit IDs may be
        strings in MQTT payloads. This helper normalizes IDs and fills missing
        sw_ver/sn fields without overwriting values already present.
        """
        if not ams_list or not isinstance(ams_list, list):
            return
        cache = self._ams_version_cache
        if not cache:
            return
        for unit in ams_list:
            if not isinstance(unit, dict):
                continue
            raw_id = unit.get("id")
            try:
                unit_id = int(raw_id) if raw_id is not None else None
            except (ValueError, TypeError):
                unit_id = None
            if unit_id is None:
                continue
            cached = cache.get(unit_id)
            if not cached:
                continue
            sw_ver = cached.get("sw_ver") or ""
            sn = cached.get("sn") or ""
            if sw_ver and not unit.get("sw_ver"):
                unit["sw_ver"] = sw_ver
            # Only set sn if not already present in AMS data
            if sn and not unit.get("sn") and not unit.get("serial_number"):
                unit["sn"] = sn
            module_type = cached.get("module_type") or ""
            if module_type and not unit.get("module_type"):
                unit["module_type"] = module_type

    def _parse_xcam_data(self, xcam_data):
        """Parse xcam data for camera settings and AI detection options."""
        if not isinstance(xcam_data, dict):
            return

        current_time = time.time()

        # Helper to check if we should accept incoming value for a module
        # OrcaSlicer pattern: simple hold timer, ignore ALL data for 3 seconds after command
        def should_accept_value(module_name: str, incoming_value: bool) -> bool:
            """Check if we should accept an incoming xcam value.

            OrcaSlicer pattern: After sending a command, ignore incoming data
            for 3 seconds. After that, accept whatever the printer sends.
            """
            if module_name not in self._xcam_hold_start:
                return True  # No hold timer, accept incoming

            hold_start = self._xcam_hold_start[module_name]
            elapsed = current_time - hold_start

            if elapsed > self._xcam_hold_time:
                # Hold timer expired - accept incoming and clear hold
                del self._xcam_hold_start[module_name]
                logger.debug("[%s] Hold expired for %s, accepting %s", self.serial_number, module_name, incoming_value)
                return True

            # Within hold period - ignore incoming data
            logger.debug(
                f"[{self.serial_number}] Ignoring {module_name}={incoming_value} "
                f"(hold active, {elapsed:.1f}s < {self._xcam_hold_time}s)"
            )
            return False

        # Log all xcam fields for debugging
        logger.debug("[%s] Parsing xcam data - all fields: %s", self.serial_number, list(xcam_data.keys()))

        # The cfg bitmask contains the ACTUAL detector states - the individual boolean
        # fields (spaghetti_detector, etc.) are often stale/cached.
        # CFG bitmask structure (each detector uses 3 bits: [sens_low, sens_high, enabled]):
        # - Bits 5-7: spaghetti_detector (sens in 5-6, enabled in 7)
        # - Bits 8-10: pileup_detector (sens in 8-9, enabled in 10)
        # - Bits 11-13: clump_detector/nozzle_clumping (sens in 11-12, enabled in 13)
        # - Bits 14-16: airprint_detector (sens in 14-15, enabled in 16)
        # Sensitivity values: 0=low, 1=medium, 2=high
        if "cfg" in xcam_data:
            cfg = xcam_data["cfg"]
            logger.debug("[%s] xcam cfg bitmask: %s (binary: %s)", self.serial_number, cfg, bin(cfg))

            def decode_detector(start_bit):
                """Decode a detector from cfg: returns (enabled, sensitivity_str)"""
                sens_bits = (cfg >> start_bit) & 0x3
                enabled = bool((cfg >> (start_bit + 2)) & 1)
                sensitivity = {0: "low", 1: "medium", 2: "high"}.get(sens_bits, "medium")
                return enabled, sensitivity

            # Spaghetti detector (bits 5-7)
            cfg_spaghetti, cfg_sensitivity = decode_detector(5)
            if should_accept_value("spaghetti_detector", cfg_spaghetti):
                old_value = self.state.print_options.spaghetti_detector
                if cfg_spaghetti != old_value:
                    logger.debug(
                        f"[{self.serial_number}] spaghetti_detector changed (from cfg): {old_value} -> {cfg_spaghetti}"
                    )
                self.state.print_options.spaghetti_detector = cfg_spaghetti

            # Check hold timer for sensitivity before accepting
            if "halt_print_sensitivity" not in self._xcam_hold_start:
                if cfg_sensitivity != self.state.print_options.halt_print_sensitivity:
                    logger.debug(
                        f"[{self.serial_number}] Sensitivity changed (from cfg): "
                        f"{self.state.print_options.halt_print_sensitivity} -> {cfg_sensitivity}"
                    )
                    self.state.print_options.halt_print_sensitivity = cfg_sensitivity
            else:
                hold_start = self._xcam_hold_start["halt_print_sensitivity"]
                elapsed = current_time - hold_start
                if elapsed <= self._xcam_hold_time:
                    logger.debug(
                        f"[{self.serial_number}] Ignoring cfg sensitivity={cfg_sensitivity} "
                        f"(hold active, {elapsed:.1f}s < {self._xcam_hold_time}s)"
                    )
                else:
                    # Hold expired - accept from cfg
                    if cfg_sensitivity != self.state.print_options.halt_print_sensitivity:
                        logger.debug(
                            f"[{self.serial_number}] Sensitivity synced (from cfg after hold): "
                            f"{self.state.print_options.halt_print_sensitivity} -> {cfg_sensitivity}"
                        )
                        self.state.print_options.halt_print_sensitivity = cfg_sensitivity
                    del self._xcam_hold_start["halt_print_sensitivity"]

            # Pileup detector (bits 8-10)
            cfg_pileup, cfg_pileup_sens = decode_detector(8)
            if should_accept_value("pileup_detector", cfg_pileup):
                if cfg_pileup != self.state.print_options.pileup_detector:
                    logger.debug(
                        f"[{self.serial_number}] pileup_detector changed (from cfg): {self.state.print_options.pileup_detector} -> {cfg_pileup}"
                    )
                    self.state.print_options.pileup_detector = cfg_pileup
            # Pileup sensitivity with hold timer
            if "pileup_sensitivity" not in self._xcam_hold_start:
                if cfg_pileup_sens != self.state.print_options.pileup_sensitivity:
                    logger.debug(
                        f"[{self.serial_number}] pileup_sensitivity changed (from cfg): {self.state.print_options.pileup_sensitivity} -> {cfg_pileup_sens}"
                    )
                    self.state.print_options.pileup_sensitivity = cfg_pileup_sens
            else:
                hold_start = self._xcam_hold_start["pileup_sensitivity"]
                elapsed = current_time - hold_start
                if elapsed > self._xcam_hold_time:
                    if cfg_pileup_sens != self.state.print_options.pileup_sensitivity:
                        logger.debug(
                            f"[{self.serial_number}] pileup_sensitivity synced (from cfg after hold): {self.state.print_options.pileup_sensitivity} -> {cfg_pileup_sens}"
                        )
                        self.state.print_options.pileup_sensitivity = cfg_pileup_sens
                    del self._xcam_hold_start["pileup_sensitivity"]

            # Clump/nozzle clumping detector (bits 11-13)
            cfg_clump, cfg_clump_sens = decode_detector(11)
            if should_accept_value("clump_detector", cfg_clump):
                if cfg_clump != self.state.print_options.nozzle_clumping_detector:
                    logger.debug(
                        f"[{self.serial_number}] nozzle_clumping_detector changed (from cfg): {self.state.print_options.nozzle_clumping_detector} -> {cfg_clump}"
                    )
                    self.state.print_options.nozzle_clumping_detector = cfg_clump
            # Clump sensitivity with hold timer
            if "nozzle_clumping_sensitivity" not in self._xcam_hold_start:
                if cfg_clump_sens != self.state.print_options.nozzle_clumping_sensitivity:
                    logger.debug(
                        f"[{self.serial_number}] nozzle_clumping_sensitivity changed (from cfg): {self.state.print_options.nozzle_clumping_sensitivity} -> {cfg_clump_sens}"
                    )
                    self.state.print_options.nozzle_clumping_sensitivity = cfg_clump_sens
            else:
                hold_start = self._xcam_hold_start["nozzle_clumping_sensitivity"]
                elapsed = current_time - hold_start
                if elapsed > self._xcam_hold_time:
                    if cfg_clump_sens != self.state.print_options.nozzle_clumping_sensitivity:
                        logger.debug(
                            f"[{self.serial_number}] nozzle_clumping_sensitivity synced (from cfg after hold): {self.state.print_options.nozzle_clumping_sensitivity} -> {cfg_clump_sens}"
                        )
                        self.state.print_options.nozzle_clumping_sensitivity = cfg_clump_sens
                    del self._xcam_hold_start["nozzle_clumping_sensitivity"]

            # Airprint detector (bits 14-16)
            cfg_airprint, cfg_airprint_sens = decode_detector(14)
            if should_accept_value("airprint_detector", cfg_airprint):
                if cfg_airprint != self.state.print_options.airprint_detector:
                    logger.debug(
                        f"[{self.serial_number}] airprint_detector changed (from cfg): {self.state.print_options.airprint_detector} -> {cfg_airprint}"
                    )
                    self.state.print_options.airprint_detector = cfg_airprint
            # Airprint sensitivity with hold timer
            if "airprint_sensitivity" not in self._xcam_hold_start:
                if cfg_airprint_sens != self.state.print_options.airprint_sensitivity:
                    logger.debug(
                        f"[{self.serial_number}] airprint_sensitivity changed (from cfg): {self.state.print_options.airprint_sensitivity} -> {cfg_airprint_sens}"
                    )
                    self.state.print_options.airprint_sensitivity = cfg_airprint_sens
            else:
                hold_start = self._xcam_hold_start["airprint_sensitivity"]
                elapsed = current_time - hold_start
                if elapsed > self._xcam_hold_time:
                    if cfg_airprint_sens != self.state.print_options.airprint_sensitivity:
                        logger.debug(
                            f"[{self.serial_number}] airprint_sensitivity synced (from cfg after hold): {self.state.print_options.airprint_sensitivity} -> {cfg_airprint_sens}"
                        )
                        self.state.print_options.airprint_sensitivity = cfg_airprint_sens
                    del self._xcam_hold_start["airprint_sensitivity"]

        # Camera settings
        if "ipcam_record" in xcam_data:
            self.state.ipcam = xcam_data.get("ipcam_record") == "enable"
        if "timelapse" in xcam_data:
            self.state.timelapse = xcam_data.get("timelapse") == "enable"
            # Track if timelapse was ever active during this print
            if self.state.timelapse and self._was_running:
                self._timelapse_during_print = True

        # Skip spaghetti_detector boolean field - we read from cfg bitmask above
        if "print_halt" in xcam_data:
            self.state.print_options.print_halt = bool(xcam_data.get("print_halt"))
        # Skip halt_print_sensitivity field - it's always stale ("medium")
        # We read the actual sensitivity from cfg bits 5-6 above
        if "first_layer_inspector" in xcam_data:
            new_value = bool(xcam_data.get("first_layer_inspector"))
            if should_accept_value("first_layer_inspector", new_value):
                self.state.print_options.first_layer_inspector = new_value
        if "printing_monitor" in xcam_data:
            new_value = bool(xcam_data.get("printing_monitor"))
            if should_accept_value("printing_monitor", new_value):
                self.state.print_options.printing_monitor = new_value
        if "buildplate_marker_detector" in xcam_data:
            new_value = bool(xcam_data.get("buildplate_marker_detector"))
            if should_accept_value("buildplate_marker_detector", new_value):
                self.state.print_options.buildplate_marker_detector = new_value
        if "allow_skip_parts" in xcam_data:
            new_value = bool(xcam_data.get("allow_skip_parts"))
            if should_accept_value("allow_skip_parts", new_value):
                self.state.print_options.allow_skip_parts = new_value

        # Additional AI detectors - these are decoded from cfg bitmask above, not from
        # individual boolean fields (which are not sent by the printer)
        # pileup_detector, nozzle_clumping_detector, airprint_detector - from cfg
        # auto_recovery_step_loss and filament_tangle_detect - tracked locally only
        if "auto_recovery_step_loss" in xcam_data:
            self.state.print_options.auto_recovery_step_loss = bool(xcam_data.get("auto_recovery_step_loss"))
        if "filament_tangle_detect" in xcam_data:
            self.state.print_options.filament_tangle_detect = bool(xcam_data.get("filament_tangle_detect"))

    @staticmethod
    def _resolve_local_slot_from_mapping(local_slot: int, mapping_raw: list | None) -> int | None:
        """Resolve a local AMS slot ID to a global tray ID using the MQTT mapping field.

        The MQTT mapping field is an array of snow-encoded values:
        each entry = ams_hw_id * 256 + slot_id (65535 = unmapped).

        Finds entries where the local slot matches, then computes the global tray ID.
        Returns the global ID if exactly one AMS matches, or None if ambiguous/unavailable.
        """
        if not isinstance(mapping_raw, list) or not mapping_raw:
            return None

        candidates: set[int] = set()
        for value in mapping_raw:
            if not isinstance(value, int) or value >= 65535:
                continue
            ams_hw_id = value >> 8
            slot = value & 0xFF
            if 0 <= ams_hw_id <= 3 and (slot & 0x03) == local_slot:
                candidates.add(ams_hw_id * 4 + local_slot)
            elif 128 <= ams_hw_id <= 135 and local_slot == 0:
                candidates.add(ams_hw_id)

        if len(candidates) == 1:
            return candidates.pop()
        return None

    def _maybe_trigger_external_spool_change(self):
        """Fire on_ams_change when the external spool (vt_tray) identity changes.

        The AMS change-hash in _handle_ams_data is built only from AMS units, so
        an external-spool-only filament swap would otherwise never re-run the
        inventory reconciliation that unlinks a stale ams_id=255 assignment
        (#2575). The reconciliation reads vt_tray from live status itself, so we
        just need to re-fire the callback with the current merged AMS data.
        """
        import hashlib

        vt_tray = self.state.raw_data.get("vt_tray")
        if not isinstance(vt_tray, list):
            return
        # Identity fields only — deliberately exclude `remain` so a print's
        # steadily-dropping fill percentage doesn't fire on every MQTT push.
        fp_parts = [
            f"{vt.get('id')}:{vt.get('tray_type')}:{vt.get('tray_color')}:"
            f"{vt.get('tag_uid')}:{vt.get('tray_uuid')}:{vt.get('tray_info_idx')}"
            for vt in vt_tray
            if isinstance(vt, dict)
        ]
        vt_hash = hashlib.md5(":".join(fp_parts).encode(), usedforsecurity=False).hexdigest()
        if vt_hash == self._previous_vt_tray_hash:
            return
        self._previous_vt_tray_hash = vt_hash
        if self.on_ams_change:
            logger.debug(
                "[%s] External spool (vt_tray) changed, triggering sync callback",
                self.serial_number,
            )
            self.on_ams_change(self.state.raw_data.get("ams") or [])

    def _normalize_a2l_am_units(self, ams_list) -> None:
        """A2L AMS-Lite normalisation (#a2l-am-unit-16): rewrite the physical unit
        id 16 -> 6 in place, as early as possible, so every downstream reader —
        the merge, apply_tray_exist_bits (bit base 24), the API, usage tracking,
        the DB constraint — sees the normalised id and needs no special-casing.
        ``tray_now`` (local) and the outbound wire are handled separately. Only id
        16 is ever touched, so every other printer/AMS type is untouched. Runs on
        both the dict-wrapped and bare-list AMS shapes.
        """
        if not isinstance(ams_list, list):
            return
        for unit in ams_list:
            if not isinstance(unit, dict):
                continue
            try:
                uid = int(unit.get("id"))
            except (TypeError, ValueError):
                continue
            if uid == A2L_LITE_PHYSICAL_AMS_ID:
                unit["id"] = A2L_LITE_NORMALIZED_AMS_ID
                if not self._has_a2l_am_unit:
                    logger.info(
                        "[%s] A2L AMS-Lite detected (unit id 16) — normalising to id %d",
                        self.serial_number,
                        A2L_LITE_NORMALIZED_AMS_ID,
                    )
                self._has_a2l_am_unit = True

    def _parse_fila_switch(self, data: dict) -> None:
        """Read the Filament Track Switch block out of a print payload — #1162.

        Presence of ``device.fila_switch`` means the accessory is installed. Kept
        separate from the rest of the state update because ``_handle_ams_data``
        needs the answer before it parses the AMS info bits, and that runs first.
        """
        if not isinstance(data.get("device"), dict):
            return
        fs_data = data["device"].get("fila_switch")
        if not isinstance(fs_data, dict):
            return
        in_raw = fs_data.get("in")
        out_raw = fs_data.get("out")
        self.state.fila_switch = FilaSwitchState(
            installed=True,
            in_slots=list(in_raw) if isinstance(in_raw, list) else [],
            out_extruders=list(out_raw) if isinstance(out_raw, list) else [],
            stat=int(fs_data.get("stat", 0) or 0),
            info=int(fs_data.get("info", 0) or 0),
        )

    def _parse_extruder_slots(self, data: dict) -> None:
        """Read which AMS slot each extruder is fed from — ``device.extruder.info``.

        Absent on printers that do not report the block, in which case the
        previous answer is kept rather than cleared: a partial payload carrying
        only temperatures must not look like "both hotends are now empty".
        """
        device = data.get("device")
        if not isinstance(device, dict):
            return
        info = device.get("extruder", {}).get("info") if isinstance(device.get("extruder"), dict) else None
        if not isinstance(info, list) or not info:
            return

        slots: dict[int, ExtruderSlot] = {}
        for entry in info:
            if not isinstance(entry, dict) or "id" not in entry:
                continue
            try:
                ext_id = int(entry["id"])
                snow = int(entry.get("snow", _EXTRUDER_SLOT_EMPTY))
                flags = int(entry.get("info", 0) or 0)
            except (TypeError, ValueError):
                continue
            if snow == _EXTRUDER_SLOT_EMPTY or snow < 0:
                ams_id = slot_id = None
            else:
                ams_id = (snow >> 8) & 0xFF
                slot_id = snow & 0xFF
            slots[ext_id] = ExtruderSlot(
                ams_id=ams_id,
                slot_id=slot_id,
                has_filament=bool(flags & 0b10),
            )

        if slots:
            self.state.extruder_slots = slots

    def _handle_ams_data(self, ams_data):
        """Handle AMS data changes for Spoolman integration.

        This is called when we receive top-level AMS data in MQTT messages.
        It detects changes and triggers the callback for Spoolman sync.
        """
        import hashlib

        # Handle nested ams structure: {"ams": {"ams": [...]}} or {"ams": [...]}
        # Also handle P1S partial updates: {"tray_now": ..., "tray_tar": ...} without "ams" key
        ams_list = None
        if isinstance(ams_data, dict):
            if "ams" in ams_data:
                ams_list = ams_data["ams"]
                self._normalize_a2l_am_units(ams_list)
            # Log all AMS dict fields to debug tray_now for H2D dual-nozzle
            non_list_fields = {k: v for k, v in ams_data.items() if k != "ams"}
            if non_list_fields:
                self._debug_on_change(
                    "ams_dict_fields",
                    non_list_fields,
                    "[%s] AMS dict fields: %s",
                    self.serial_number,
                    non_list_fields,
                )

            # IMPORTANT: Parse ams_status FIRST before tray_now, so we have fresh status
            # when checking if we're in filament change mode for tray_now disambiguation
            if "ams_status" in ams_data:
                raw_ams_status = ams_data["ams_status"]
                if isinstance(raw_ams_status, str):
                    try:
                        self.state.ams_status = int(raw_ams_status)
                    except ValueError:
                        self.state.ams_status = 0
                else:
                    self.state.ams_status = raw_ams_status if raw_ams_status is not None else 0
                # Compute main and sub status
                self.state.ams_status_sub = self.state.ams_status & 0xFF
                self.state.ams_status_main = (self.state.ams_status >> 8) & 0xFF
                self._debug_on_change(
                    "ams_status:ams",
                    self.state.ams_status,
                    "[%s] ams_status: %s (main=%s, sub=%s)",
                    self.serial_number,
                    self.state.ams_status,
                    self.state.ams_status_main,
                    self.state.ams_status_sub,
                )

            # Parse tray_tar / tray_pre (RAW). These identify the slot the firmware
            # now expects (tray_tar) and the slot loaded before (tray_pre) — the key
            # signal for a runout PAUSE where AMS Filament Backup has advanced to the
            # next compatible slot (#2587). Stored raw here; globalised at the API
            # boundary because that resolution needs the AMS layout. On H2D/multi-AMS
            # these are local slot numbers (0-3), not global IDs.
            for _tk, _attr in (("tray_tar", "tray_tar"), ("tray_pre", "tray_pre")):
                if _tk in ams_data:
                    _raw = ams_data[_tk]
                    if isinstance(_raw, str):
                        try:
                            _val = int(_raw)
                        except ValueError:
                            _val = 255
                    else:
                        _val = _raw if _raw is not None else 255
                    prev = getattr(self.state, _attr)
                    setattr(self.state, _attr, _val)
                    # Log changes only while paused — the moment the operator cares —
                    # so a healthy print's normal tar churn doesn't spam the log.
                    if _val != prev and _val not in (255, -1) and self.state.state == "PAUSE":
                        logger.info(
                            "[%s] AMS %s changed to %s while paused (expected/previous slot signal, #2587)",
                            self.serial_number,
                            _tk,
                            _val,
                        )

            # Parse tray_now from AMS dict - this is the currently loaded tray global ID
            # Note: tray_tar is also available but on H2D it's just slot number (0-3), not global ID
            if "tray_now" in ams_data:
                raw_tray_now = ams_data["tray_now"]
                # Convert string to int if needed
                if isinstance(raw_tray_now, str):
                    try:
                        parsed_tray_now = int(raw_tray_now)
                    except ValueError:
                        parsed_tray_now = 255
                else:
                    parsed_tray_now = raw_tray_now if raw_tray_now is not None else 255

                # H2D dual-nozzle printers report only slot number (0-3), not global tray ID
                # Use active_extruder + ams_extruder_map to determine which AMS the slot belongs to
                # Single-nozzle printers with multiple AMS (e.g. P2S) also report local slot IDs (#420)
                # — disambiguated below using MQTT mapping field
                ams_map = self.state.ams_extruder_map
                if self._is_dual_nozzle and 0 <= parsed_tray_now <= 3:
                    # First, check if we have a pending target that matches this slot
                    pending_target = self.state.pending_tray_target
                    if pending_target is not None:
                        pending_slot = pending_target % 4
                        if pending_slot == parsed_tray_now:
                            # Slot matches our pending target - use the full global ID
                            logger.debug(
                                f"[{self.serial_number}] H2D tray_now disambiguation: "
                                f"slot {parsed_tray_now} matches pending_tray_target {pending_target} -> using global ID {pending_target}"
                            )
                            self.state.tray_now = pending_target
                            # Clear pending target now that load is confirmed
                            self.state.pending_tray_target = None
                        else:
                            # Slot doesn't match our pending target - something changed, use slot as-is
                            logger.warning(
                                f"[{self.serial_number}] H2D tray_now: slot {parsed_tray_now} doesn't match "
                                f"pending_tray_target {pending_target} (slot {pending_slot}) - using slot as global ID"
                            )
                            self.state.tray_now = parsed_tray_now
                            # Clear pending target since it's stale
                            self.state.pending_tray_target = None
                    else:
                        # No pending target - use h2d_extruder_snow for accurate disambiguation
                        # H2D sends snow field in device.extruder.info with AMS ID in high byte
                        active_ext = self.state.active_extruder  # 0=right, 1=left

                        # Best source: use snow value from device.extruder.info if available
                        snow_tray = self.state.h2d_extruder_snow.get(active_ext)
                        if snow_tray is not None and snow_tray != 255:
                            # snow_tray is already normalized to global ID
                            # Verify the slot matches what we see in tray_now
                            # Regular AMS: slot = global_id % 4; AMS HT (128-135): single slot = 0
                            snow_slot = snow_tray % 4 if snow_tray < 128 else (0 if snow_tray <= 135 else -1)
                            if snow_slot == parsed_tray_now:
                                if self.state.tray_now != snow_tray:
                                    logger.debug(
                                        f"[{self.serial_number}] H2D tray_now from snow: "
                                        f"extruder[{active_ext}] snow={snow_tray} (slot {snow_slot})"
                                    )
                                self.state.tray_now = snow_tray
                            else:
                                # Slot mismatch - snow field may not have updated yet, trust snow
                                logger.debug(
                                    f"[{self.serial_number}] H2D tray_now: ams.tray_now slot {parsed_tray_now} "
                                    f"!= snow slot {snow_slot}, using snow value {snow_tray}"
                                )
                                self.state.tray_now = snow_tray
                        else:
                            # Fallback: snow not available, use ams_extruder_map (less reliable)
                            # Find ALL AMS units on the active extruder
                            ams_on_extruder = []
                            for ams_id_str, ext_id in ams_map.items():
                                if ext_id == active_ext:
                                    try:
                                        ams_on_extruder.append(int(ams_id_str))
                                    except ValueError:
                                        pass  # Skip AMS IDs that aren't valid integers

                            if len(ams_on_extruder) == 1:
                                # Single AMS on this extruder - unambiguous
                                active_ams_id = ams_on_extruder[0]
                                if 128 <= active_ams_id <= 135:
                                    # AMS-HT: single slot per unit, global ID = unit ID
                                    global_tray_id = active_ams_id
                                else:
                                    global_tray_id = active_ams_id * 4 + parsed_tray_now
                                logger.debug(
                                    f"[{self.serial_number}] H2D tray_now fallback: "
                                    f"slot {parsed_tray_now} + single AMS {active_ams_id} -> global ID {global_tray_id}"
                                )
                                self.state.tray_now = global_tray_id
                            elif len(ams_on_extruder) > 1:
                                # Multiple AMS on this extruder - keep current if valid, else try to narrow down
                                current_tray = self.state.tray_now
                                # Determine which AMS unit and slot the current tray belongs to
                                if 0 <= current_tray <= 15:
                                    current_ams = current_tray // 4
                                    current_slot = current_tray % 4
                                elif 128 <= current_tray <= 135:
                                    current_ams = current_tray  # AMS-HT: ID = tray ID
                                    current_slot = 0
                                else:
                                    current_ams = -1
                                    current_slot = -1
                                if current_ams in ams_on_extruder and current_slot == parsed_tray_now:
                                    # Current is valid and matches slot - keep it
                                    logger.debug(
                                        f"[{self.serial_number}] H2D tray_now: multiple AMS {ams_on_extruder}, "
                                        f"keeping current {current_tray} (matches slot {parsed_tray_now})"
                                    )
                                else:
                                    # Filter candidates: AMS-HT (128-135) only valid for slot 0
                                    if parsed_tray_now > 0:
                                        candidates = [a for a in ams_on_extruder if a <= 3]
                                    else:
                                        candidates = ams_on_extruder
                                    if len(candidates) == 1:
                                        cand = candidates[0]
                                        resolved = cand if 128 <= cand <= 135 else cand * 4 + parsed_tray_now
                                        logger.debug(
                                            f"[{self.serial_number}] H2D tray_now: multiple AMS {ams_on_extruder}, "
                                            f"narrowed to AMS {cand} -> global ID {resolved}"
                                        )
                                        self.state.tray_now = resolved
                                    else:
                                        # Genuinely ambiguous - use slot as-is (will be wrong for non-first AMS)
                                        logger.warning(
                                            f"[{self.serial_number}] H2D tray_now: multiple AMS {ams_on_extruder} on extruder {active_ext}, "
                                            f"no snow field, using slot {parsed_tray_now} (may be incorrect)"
                                        )
                                        self.state.tray_now = parsed_tray_now
                            else:
                                # No AMS on this extruder - use slot as-is
                                logger.warning(
                                    f"[{self.serial_number}] H2D tray_now: no AMS on extruder {active_ext}, "
                                    f"using slot {parsed_tray_now}"
                                )
                                self.state.tray_now = parsed_tray_now
                elif not self._is_dual_nozzle and 0 <= parsed_tray_now <= 3:
                    # Single-nozzle printer with tray_now in 0-3 range.
                    # #1822: H2S firmware reports tray_now as the AMS's idle
                    # slot (typically 0) when the active feed is actually the
                    # external spool. X1C / P1S / A1 correctly report 254 in
                    # that case; H2S does not. When the slicer-captured
                    # ams_mapping is all-external (every entry == -1), the
                    # print can only be feeding from the external spool, so
                    # promote tray_now to 254. Mixed (e.g. [5, -1]) and
                    # AMS-only mappings are NOT overridden — there's no
                    # evidence the firmware misreports in those cases. Prints
                    # started without a captured mapping (printer-screen start,
                    # or before Bambuddy connected) fall through unchanged.
                    captured = self._captured_ams_mapping
                    if captured and all(s == -1 for s in captured):
                        if self.state.tray_now != 254:
                            logger.debug(
                                f"[{self.serial_number}] tray_now external-spool override (#1822): "
                                f"slot {parsed_tray_now} -> 254 (ams_mapping={captured})"
                            )
                        self.state.tray_now = 254
                    else:
                        # P2S (and possibly other models) with multiple AMS units sends LOCAL slot IDs
                        # in tray_now, not global tray IDs (#420). Use the MQTT mapping field
                        # (snow-encoded) to resolve the correct AMS unit.
                        ams_exist_raw = ams_data.get("ams_exist_bits", "0")
                        try:
                            ams_exist = int(ams_exist_raw, 16) if isinstance(ams_exist_raw, str) else int(ams_exist_raw)
                        except (ValueError, TypeError):
                            ams_exist = 0
                        num_ams = bin(ams_exist).count("1")

                        if self._has_a2l_am_unit and num_ams <= 1:
                            # A2L AMS-Lite (normalised unit 6): the firmware reports
                            # tray_now as a LOCAL 0-3 slot, so globalise to 24+slot —
                            # otherwise usage tracking keys the wrong spool (it would
                            # deduct from AMS 0's slot). Confirmed by capture:
                            # tray_now="2" while printing physical slot 3.
                            self.state.tray_now = A2L_LITE_GLOBAL_BASE + parsed_tray_now
                        elif num_ams > 1:
                            # Multiple AMS on single-nozzle — tray_now is likely a local slot ID.
                            # Cross-reference with MQTT mapping field to find the correct AMS unit.
                            if self._has_a2l_am_unit:
                                # A2L Lite + a regular AMS attached together is out of
                                # scope: the flat mapping ids are unknown for that combo
                                # and could collide with AMS 0. Fall through to the
                                # mapping-based resolve, but warn — a capture is needed.
                                logger.warning(
                                    "[%s] A2L AMS-Lite alongside another AMS unit is unsupported — "
                                    "tray_now resolution may be wrong (needs a mixed-setup capture)",
                                    self.serial_number,
                                )
                            mapping_raw = self.state.raw_data.get("mapping")
                            resolved = self._resolve_local_slot_from_mapping(parsed_tray_now, mapping_raw)
                            if resolved is not None:
                                if resolved != parsed_tray_now:
                                    logger.debug(
                                        f"[{self.serial_number}] Multi-AMS tray_now: "
                                        f"local slot {parsed_tray_now} -> global ID {resolved} (from mapping)"
                                    )
                                self.state.tray_now = resolved
                            else:
                                # No mapping available (not printing, or ambiguous) — use as-is.
                                # This matches the old behavior and is correct for AMS 0.
                                self.state.tray_now = parsed_tray_now
                        else:
                            # Single AMS — local slot 0-3 equals global ID
                            self.state.tray_now = parsed_tray_now
                else:
                    # tray_now > 3 means it's already a global ID, or 255 means unloaded
                    # Note: Do NOT clear pending_tray_target on tray_now=255 here.
                    # During filament change, the printer sends 255 first (unload), then the slot.
                    # We only clear pending_tray_target explicitly in ams_unload_filament().
                    # Trust the printer's reported value.
                    self.state.tray_now = parsed_tray_now

                # Track last valid tray for usage tracking (survives retract → 255 at print end)
                # Valid physical trays: 0-15 (regular AMS), 24-27 (A2L AMS-Lite,
                # normalised unit 6), 128-135 (AMS-HT), 254 (external spool)
                tn = self.state.tray_now
                if (
                    (0 <= tn <= 15)
                    or (A2L_LITE_GLOBAL_BASE <= tn <= A2L_LITE_GLOBAL_BASE + 3)
                    or (128 <= tn <= 135)
                    or tn == 254
                ):
                    # Log tray change for mid-print usage splitting. Gate on the
                    # print-lifecycle flags (`_was_running` set on first RUNNING /
                    # new print, `_completion_triggered` set when on_print_complete
                    # fires) instead of `state in ("RUNNING", "PAUSE")` — P2S
                    # firmware briefly transitions out of RUNNING during AMS
                    # auto-fallback (#957), so a literal-string gate misses the
                    # switch and the usage tracker double-credits at completion.
                    if tn != self.state.last_loaded_tray and self._was_running and not self._completion_triggered:
                        self.state.tray_change_log.append((tn, self.state.layer_num))
                        logger.info(
                            "[%s] Tray change during print: tray=%d at layer=%d",
                            self.serial_number,
                            tn,
                            self.state.layer_num,
                        )
                        if self.on_tray_change:
                            self.on_tray_change(tn, self.state.layer_num)
                    self.state.last_loaded_tray = self.state.tray_now

                self._debug_on_change(
                    "tray_now",
                    self.state.tray_now,
                    "[%s] tray_now updated: %s",
                    self.serial_number,
                    self.state.tray_now,
                )

            # NOTE: ams_status is parsed BEFORE tray_now (see above) to ensure correct
            # state when checking filament change mode for H2D disambiguation

            # P1S/P1P send partial updates without "ams" key - this is valid, not an error
            # We've already processed the status fields above, so just return if no ams list
            if ams_list is None:
                logger.debug("[%s] AMS partial update (no tray data)", self.serial_number)
                return
        elif isinstance(ams_data, list):
            ams_list = ams_data
            self._normalize_a2l_am_units(ams_list)
        else:
            logger.warning("[%s] Unexpected AMS data format: %s", self.serial_number, type(ams_data))
            return

        # Merge AMS data instead of replacing, to handle partial updates
        # During prints, the printer may only send updates for active AMS units
        # We need deep merging at the tray level to preserve fields like tray_sub_brands
        existing_ams = self.state.raw_data.get("ams", [])
        existing_by_id = {ams.get("id"): ams for ams in existing_ams if ams.get("id") is not None}

        # Update existing units with new data, add new units
        for ams_unit in ams_list:
            ams_id = ams_unit.get("id")
            if ams_id is not None:
                existing_unit = existing_by_id.get(ams_id)
                if existing_unit and "tray" in ams_unit:
                    # Deep merge trays to preserve fields from previous updates
                    existing_trays = {t.get("id"): t for t in existing_unit.get("tray", []) if t.get("id") is not None}
                    merged_trays = []
                    for new_tray in ams_unit.get("tray", []):
                        tray_id = new_tray.get("id")
                        if tray_id is not None and tray_id in existing_trays:
                            # Merge: start with existing, update with new non-empty values
                            merged_tray = existing_trays[tray_id].copy()
                            # Detect slot-clearing updates (spool removal):
                            # When tray_type is explicitly empty, clear everything
                            # including RFID data (tag_uid/tray_uuid).
                            slot_clearing = new_tray.get("tray_type") == ""
                            # Some printers (e.g. H2D) only send {id, state} in
                            # incremental updates when a tray is not fully loaded.
                            # state=11 means loaded; other values (9=empty,
                            # 10=spool present but filament not in feeder) indicate
                            # the slot should be cleared.  Without this, old
                            # tray_type/tray_color persist indefinitely (#784).
                            #
                            # BUT this is regular-AMS semantics. An AMS-HT (single-
                            # tray high-temp dry box, id >= 128) reports its loaded
                            # tray as state=9, not 11 — it doesn't feed filament into
                            # a shared buffer the way a 4-slot AMS does. Applying the
                            # `state != 11 → empty` rule to an HT unit wiped a present
                            # spool on every power-on, when the printer sends a partial
                            # {id, state=9} for the HT tray (#2594). Skip the state
                            # heuristic for HT units — a genuine HT spool removal still
                            # clears via the explicit tray_type=="" case above and the
                            # tray_exist_bits cleanup below.
                            try:
                                _is_ht_unit = int(ams_id) >= 128
                            except (TypeError, ValueError):
                                _is_ht_unit = False
                            tray_state = new_tray.get("state")
                            if (
                                tray_state is not None
                                and tray_state != 11
                                and not _is_ht_unit
                                and "tray_type" not in new_tray
                                and merged_tray.get("tray_type")
                            ):
                                logger.info(
                                    "[%s] AMS %s tray %s: state=%s (not loaded) — clearing stale tray data",
                                    self.serial_number,
                                    ams_id,
                                    tray_id,
                                    tray_state,
                                )
                                slot_clearing = True
                                # The incremental update only has {id, state} — inject
                                # empty values for all content fields so the merge loop
                                # below clears the stale data from merged_tray.
                                new_tray.update(
                                    {
                                        "tray_type": "",
                                        "tray_sub_brands": "",
                                        "tray_color": "",
                                        "tray_id_name": "",
                                        "tray_info_idx": "",
                                        "tag_uid": "0000000000000000",
                                        "tray_uuid": "00000000000000000000000000000000",
                                        "remain": 0,
                                        "k": None,
                                        "cali_idx": None,
                                    }
                                )
                            for key, value in new_tray.items():
                                # Fields that should always be updated (even with empty/zero values):
                                # - remain, k, id, cali_idx: status indicators where 0 is valid
                                # - tray_type, tray_sub_brands, tray_info_idx, tray_color,
                                #   tray_id_name: slot content indicators that must be cleared
                                #   when a spool is removed (fixes #147 - old AMS empty slot)
                                # NOTE: tag_uid and tray_uuid are NOT in always_update_fields.
                                # They are only cleared during spool removal (slot_clearing=True).
                                # Periodic AMS updates often include empty RFID fields which
                                # would overwrite valid data from the initial pushall.
                                always_update_fields = (
                                    "remain",
                                    "k",
                                    "id",
                                    "cali_idx",
                                    "tray_type",
                                    "tray_sub_brands",
                                    "tray_info_idx",
                                    "tray_color",
                                    "tray_id_name",
                                )
                                if (
                                    key in always_update_fields
                                    or slot_clearing
                                    or value
                                    not in (
                                        None,
                                        "",
                                        "0000000000000000",
                                        "00000000000000000000000000000000",
                                    )
                                ):
                                    merged_tray[key] = value
                            merged_trays.append(merged_tray)
                        else:
                            merged_trays.append(new_tray)
                    # Update ams_unit with merged trays. Spread existing_unit
                    # FIRST so top-level fields the partial update omits —
                    # dry_time, info (which drives dry_status / dry_sub_status),
                    # humidity, temp — are preserved instead of dropped. The
                    # printer sends tray-bearing partials that carry no drying
                    # fields; without this, dry_time reads as absent → 0 and the
                    # falling-edge detector below fires a false "drying complete"
                    # (#1462). Mirrors the no-tray branch's merge semantics.
                    ams_unit = {**existing_unit, **ams_unit, "tray": merged_trays}
                elif existing_unit:
                    # Partial update without tray data: merge new fields into existing
                    # unit to preserve tray, sn, sw_ver, and other accumulated data.
                    ams_unit = {**existing_unit, **ams_unit}
                existing_by_id[ams_id] = ams_unit

        # Convert back to list, sorted by ID for consistent ordering
        merged_ams = sorted(existing_by_id.values(), key=lambda x: x.get("id", 0))

        # Empty-slot cleanup via tray_exist_bits (#147, #1322, #765, #1365).
        # Shared with the VP bridge cache so the slicer-facing view stays in
        # sync with Bambuddy's AMS card (#1726). See the helper's docstring
        # for the full rationale and the printer-shutdown guard.
        if isinstance(ams_data, dict):
            apply_tray_exist_bits(
                merged_ams,
                ams_data.get("tray_exist_bits"),
                power_on_flag=ams_data.get("power_on_flag", True),
                log_label=self.serial_number,
                annotate_exists=True,
            )

        self.state.raw_data["ams"] = merged_ams

        # Apply cached AMS firmware/SN from get_version (handles ordering and id type mismatches)
        self._apply_ams_version_cache(merged_ams)
        # Update timestamp for RFID refresh detection (frontend can detect "new data arrived")
        self.state.last_ams_update = time.time()
        self._debug_on_change(
            "merged_ams",
            (len(ams_list), len(merged_ams)),
            "[%s] Merged AMS data: %s new units, %s total",
            self.serial_number,
            len(ams_list),
            len(merged_ams),
        )

        # Extract ams_extruder_map from each AMS unit's info field
        # BambuStudio DevFilaSystem.cpp parses info as hex string:
        #   type_id    = get_flag_bits(info, 0, 4)   // bits 0-3: AMS type
        #   extruder_id = get_flag_bits(info, 8, 4)  // bits 8-11: extruder assignment
        #   bind_switch_in = get_flag_bits(info, 24, 4)  // bits 24-27: FTS inlet
        # where get_flag_bits uses std::stoull(str, nullptr, 16) — hex parsing.
        # extruder_id: 0=right/main, 1=left/deputy, 0xE=routing is not fixed
        #
        # 0xE does not mean "broken". On a Filament Track Switch machine it is the
        # normal steady state: the AMS is bound to a switch *inlet* rather than to
        # one extruder, and reaches both nozzles through it. Bits 24-27 then name
        # that inlet — 0 = In-B, 1 = In-A (BambuStudio's SwitchPos enum, which is
        # ordered B-then-A). Without an FTS, 0xE really is an uninitialised unit
        # and bits 24-27 carry nothing, which is why the inlet read is gated on
        # the switch being installed.
        #
        # Use merged_ams (not ams_list) to avoid partial MQTT updates overwriting
        # the full map. Merge into existing map to preserve entries from prior updates.

        fts_installed = self.state.fila_switch.installed
        inlet_moves: list[tuple[int, str]] = []
        ams_extruder_map = dict(self.state.ams_extruder_map) if self.state.ams_extruder_map else {}
        ams_switch_inlet = dict(self.state.ams_switch_inlet) if self.state.ams_switch_inlet else {}
        for ams_unit in merged_ams:
            ams_id = ams_unit.get("id")
            info = ams_unit.get("info")
            if ams_id is not None and info is not None:
                try:
                    # info is a hex-encoded string in MQTT JSON (e.g. "10001003")
                    info_val = int(str(info), 16)
                    # Extract 4 bits starting at bit 8 for extruder assignment
                    extruder_id = (info_val >> 8) & 0xF
                    if extruder_id == 0xE:
                        if fts_installed:
                            inlet = {0: "B", 1: "A"}.get((info_val >> 24) & 0xF)
                            if inlet is not None:
                                previous = ams_switch_inlet.get(str(ams_id))
                                ams_switch_inlet[str(ams_id)] = inlet
                                self._debug_on_change(
                                    f"ams_inlet:{ams_id}",
                                    inlet,
                                    "[%s] AMS %s info=0x%s -> FTS inlet %s",
                                    self.serial_number,
                                    ams_id,
                                    info,
                                    inlet,
                                )
                                if previous is not None and previous != inlet:
                                    # Only a genuine move, never the first sighting:
                                    # re-applying K-profiles on every reconnect would
                                    # fight a binding the operator set deliberately.
                                    logger.info(
                                        "[%s] AMS %s moved to FTS inlet %s (was %s)",
                                        self.serial_number,
                                        ams_id,
                                        inlet,
                                        previous,
                                    )
                                    inlet_moves.append((int(ams_id), inlet))
                        continue
                    ams_extruder_map[str(ams_id)] = extruder_id
                    self._debug_on_change(
                        f"ams_info:{ams_id}",
                        (info, extruder_id),
                        "[%s] AMS %s info=0x%s -> extruder %s",
                        self.serial_number,
                        ams_id,
                        info,
                        extruder_id,
                    )
                except (ValueError, TypeError):
                    pass  # Skip AMS units with unparseable info bitmask values
        if ams_extruder_map:
            self.state.raw_data["ams_extruder_map"] = ams_extruder_map
            self.state.ams_extruder_map = ams_extruder_map
            logger.debug("[%s] ams_extruder_map: %s", self.serial_number, ams_extruder_map)
        if ams_switch_inlet:
            self.state.ams_switch_inlet = ams_switch_inlet
        for moved_ams_id, moved_inlet in inlet_moves:
            if self.on_fts_inlet_change:
                self.on_fts_inlet_change(moved_ams_id, moved_inlet)

        # Extract drying status from info hex string and dry_sf_reason per AMS unit
        # BambuStudio DevFilaSystem.cpp parses info bits:
        #   dry_status     = get_flag_bits(info, 4, 4)   // bits 4-7
        #   dry_sub_status = get_flag_bits(info, 22, 4)  // bits 22-25
        for ams_unit in merged_ams:
            info = ams_unit.get("info")
            if info is not None:
                try:
                    info_val = int(str(info), 16)
                    ams_unit["dry_status"] = (info_val >> 4) & 0xF
                    ams_unit["dry_sub_status"] = (info_val >> 22) & 0xF
                except (ValueError, TypeError):
                    pass  # Skip unparseable info values
            # dry_sf_reason is a per-unit array of cannot-dry reason codes
            if "dry_sf_reason" in ams_unit:
                sf_reason = ams_unit["dry_sf_reason"]
                if isinstance(sf_reason, list):
                    ams_unit["dry_sf_reason"] = [
                        int(r) for r in sf_reason if isinstance(r, int) or (isinstance(r, str) and r.isdigit())
                    ]
                else:
                    ams_unit["dry_sf_reason"] = []

        # Persist updated drying fields back to raw_data
        self.state.raw_data["ams"] = merged_ams

        # Detect AMS drying-complete falling edge per-unit (#1349). When an
        # AMS's `dry_time` transitions from >0 to 0 the cycle just finished
        # — fire the callback so smart-plug auto-off-after-drying can run,
        # and drop our cached target-cycle params so the badge stops claiming
        # an active cycle. Works identically for queue-triggered, ambient,
        # and manual drying because we observe the firmware-reported state.
        for ams_unit in merged_ams:
            try:
                ams_id = int(ams_unit.get("id", -1))
            except (TypeError, ValueError):
                continue
            if ams_id < 0:
                continue
            # Only evaluate the edge when this update carries an explicit
            # dry_time. An absent / unparseable value is NOT zero — treating
            # it as 0 lets a tray-only partial fake a drying-complete edge
            # (#1462). Skip without touching the remembered value so the
            # next update that DOES carry dry_time sees the true previous.
            raw_dry_time = ams_unit.get("dry_time")
            if raw_dry_time is None:
                continue
            try:
                current = int(raw_dry_time)
            except (TypeError, ValueError):
                continue
            # A dry_time of 0 only means "finished" when the unit also reports
            # an idle phase. Between the command ack and the countdown settling
            # the firmware publishes a transient 0 while the AMS is still
            # Checking — #2759 caught a 720 → 0 → 719 sequence one minute into a
            # 12-hour cycle. Taking that at face value dropped the cached target
            # (leaving the badge to guess the filament from tray 1, so a PLA
            # cycle read "PETG @ 65°C") and fired on_drying_complete, which
            # schedules smart-plug auto-off. dry_status comes from the same info
            # hex parsed above; when it is absent we let the edge through, so a
            # firmware that never reports one still ends its cycles.
            if current == 0 and ams_unit.get("dry_status") in ACTIVE_DRY_STATUSES:
                # Leave the remembered value alone, exactly as the absent-
                # dry_time skip above does: whichever push ends the cycle for
                # real must still see a non-zero previous.
                logger.debug(
                    "[%s] AMS %d reported dry_time 0 in phase %s — cycle still live, ignoring",
                    self.serial_number,
                    ams_id,
                    ams_unit.get("dry_status"),
                )
                continue
            previous = self._previous_dry_times.get(ams_id, 0)
            self._previous_dry_times[ams_id] = current
            if previous > 0 and current == 0:
                self._log_drying_cycle_end(ams_id, previous, ams_unit, self._drying_targets.pop(ams_id, None))
                if self.on_drying_complete:
                    self.on_drying_complete(ams_id)

        # Create a hash of relevant AMS data to detect changes.
        # Hash the MERGED state, not the raw incoming ams_list: a removal signalled
        # only by tray_exist_bits (firmware still echoing the old tray_type in the
        # payload, unchanged remain) clears merged_ams via apply_tray_exist_bits
        # above but leaves the raw payload's tracked fields untouched — so a
        # raw-based hash never flips and on_ams_change never fires, leaving the
        # spool_assignment row bound to an emptied slot (#2670). merged_ams also
        # always spans every unit, so a partial single-unit update can't produce a
        # spuriously different hash from a full pushall.
        ams_hash_data = []
        for ams_unit in merged_ams:
            for tray in ams_unit.get("tray", []):
                # Include fields that matter for filament tracking
                ams_hash_data.append(
                    f"{ams_unit.get('id')}:{tray.get('id')}:"
                    f"{tray.get('tray_type')}:{tray.get('tag_uid')}:{tray.get('remain')}"
                )
        ams_hash = hashlib.md5(":".join(ams_hash_data).encode(), usedforsecurity=False).hexdigest()

        # Only trigger callback if AMS data actually changed
        if ams_hash != self._previous_ams_hash:
            self._previous_ams_hash = ams_hash
            if self.on_ams_change:
                logger.debug("[%s] AMS data changed, triggering sync callback", self.serial_number)
                # Pass merged AMS data (not raw ams_list) — partial MQTT updates
                # may lack fields like 'remain' that the merged state preserves
                self.on_ams_change(merged_ams)

        # #2582: read-back check runs on EVERY AMS push, not just hash changes.
        # The change hash keys on tray_type/tag_uid/remain — NOT tray_info_idx
        # or cali_idx — so an assignment that only swaps the filament id on an
        # already-loaded slot would not flip the hash, and gating the check on
        # it would miss exactly the confirmation we are after.
        if self._pending_assignments:
            self._check_assignment_verifications()

    def _log_drying_cycle_end(
        self,
        ams_id: int,
        remaining: int,
        ams_unit: dict,
        target: dict[str, object] | None,
    ) -> None:
        """Report a finished drying cycle, with the firmware's reason when it was
        cut short (#2770).

        A cycle that reaches its configured duration needs no explanation and
        keeps the one-line "drying complete" it has always had. One that ends
        with most of its countdown left was ended by somebody, and there are
        only two candidates: a stop Bambuddy sent — the print-takes-priority
        stop, or the user's Stop button — which is named as such, or the
        firmware.

        For the firmware case the only account of why lives in fields we already
        parse but have never written down: the ``dry_status`` /
        ``dry_sub_status`` phase from the info hex, the per-unit
        ``dry_sf_reason`` constraint codes, and whatever HMS errors are live at
        that moment. Logging them at INFO puts them in every support bundle by
        default, which is what a report like #2770 needs before its cause can be
        argued about at all.

        The unit's ``temp`` and ``humidity_raw`` at the moment of the end are
        logged for every cycle, early or not, because they are what decides
        whether auto-drying re-arms. Reconstructing them for #2770 meant
        cross-referencing hourly alarm lines against 30-second scheduler debug
        that was switched off at the time; one line here says it outright — a
        cycle ending at 63 degC with the reading still above the threshold is
        the whole shape of the re-arm loop.
        """
        box = f"temp={ams_unit.get('temp')} humidity={ams_unit.get('humidity_raw', ams_unit.get('humidity'))}"
        if ams_id in self._drying_stops_sent:
            self._drying_stops_sent.discard(ams_id)
            logger.info(
                "[%s] AMS %d drying stopped by Bambuddy (dry_time %d → 0, %s)",
                self.serial_number,
                ams_id,
                remaining,
                box,
            )
            return

        if remaining <= _EARLY_DRY_END_MINUTES:
            logger.info(
                "[%s] AMS %d drying complete (dry_time %d → 0, %s)",
                self.serial_number,
                ams_id,
                remaining,
                box,
            )
            return

        requested_minutes: int | None = None
        if target is not None:
            try:
                requested_minutes = int(target.get("duration_hours") or 0) * 60 or None
            except (TypeError, ValueError):
                requested_minutes = None

        logger.info(
            "[%s] AMS %d drying ended early — %d of %s minutes still on the clock. "
            "Bambuddy sent no stop command, so the firmware ended this cycle: "
            "dry_status=%s dry_sub_status=%s dry_sf_reason=%s hms=%s %s",
            self.serial_number,
            ams_id,
            remaining,
            requested_minutes if requested_minutes is not None else "?",
            ams_unit.get("dry_status"),
            ams_unit.get("dry_sub_status"),
            ams_unit.get("dry_sf_reason") or [],
            [e.full_code for e in self.state.hms_errors] or "none",
            box,
        )

    def register_assignment_verification(
        self,
        ams_id: int,
        tray_id: int,
        tray_info_idx: str,
        tray_color: str,
        cali_idx: int | None,
    ) -> None:
        """Record an assignment we just pushed so subsequent AMS telemetry can
        confirm the tray actually accepted it (#2582).

        Called right after ``ams_set_filament_setting`` + ``extrusion_cali_sel``.
        ``tray_info_idx`` is the primary signal — the slicer/printer echoes the
        accepted filament id back in the per-tray push, so a match means the
        setting landed. ``cali_idx`` (when >= 0) is verified as a secondary
        signal so we can specifically flag "filament loaded but K-profile not
        applied", which is the exact symptom the reporter chased via flow-cal.

        A blank ``tray_info_idx`` means we had nothing resolvable to send, so
        there is nothing to verify and no record is stored.
        """
        want_idx = (tray_info_idx or "").strip().upper()
        if not want_idx:
            return
        self._pending_assignments[(ams_id, tray_id)] = {
            "tray_info_idx": want_idx,
            "tray_color": (tray_color or "").strip().upper(),
            "cali_idx": cali_idx,
            "deadline": time.monotonic() + self.ASSIGNMENT_VERIFY_TIMEOUT,
            "last_seen_idx": None,
        }

    def _find_verify_tray(self, ams_id: int, tray_id: int) -> dict | None:
        """Locate the live tray dict for a pending verification.

        External spools (ams_id 255) live in ``vt_tray`` under global ids
        254/255; regular and HT AMS trays live under ``ams[].tray[]``. HT units
        report a single tray whose id may not equal the logical tray_id, so fall
        back to the sole tray when an id match fails.
        """
        raw = self.state.raw_data or {}
        if ams_id == 255:
            want_ext = 254 + tray_id
            for vt in raw.get("vt_tray", []) or []:
                if isinstance(vt, dict) and str(vt.get("id")) == str(want_ext):
                    return vt
            return None
        for unit in raw.get("ams", []) or []:
            if str(unit.get("id")) != str(ams_id):
                continue
            trays = unit.get("tray", []) or []
            for tray in trays:
                if str(tray.get("id")) == str(tray_id):
                    return tray
            if ams_id >= 128 and len(trays) == 1:
                return trays[0]
            return None
        return None

    def _check_assignment_verifications(self) -> None:
        """Compare each pending assignment against live tray telemetry and fire
        ``on_assignment_verified`` on a match or once the deadline passes.

        Runs on every AMS push. Non-matching-but-still-within-window entries are
        left in place for the next push. The timeout branch only fires when a
        later push arrives after the deadline; if the printer goes silent we
        simply never confirm, which is preferable to inventing a failure.
        """
        now = time.monotonic()
        for key, want in list(self._pending_assignments.items()):
            ams_id, tray_id = key
            tray = self._find_verify_tray(ams_id, tray_id)
            actual_idx = str((tray or {}).get("tray_info_idx") or "").strip().upper()
            if tray is not None and actual_idx:
                want["last_seen_idx"] = actual_idx
            if actual_idx and actual_idx == want["tray_info_idx"]:
                self._pending_assignments.pop(key, None)
                kprofile_applied = True
                want_cali = want.get("cali_idx")
                if want_cali is not None and want_cali >= 0:
                    actual_cali = tray.get("cali_idx")
                    kprofile_applied = actual_cali == want_cali
                self._fire_assignment_verified(
                    ams_id,
                    tray_id,
                    True,
                    {
                        "tray_info_idx": actual_idx,
                        "kprofile_applied": kprofile_applied,
                    },
                )
            elif now >= want["deadline"]:
                self._pending_assignments.pop(key, None)
                self._fire_assignment_verified(
                    ams_id,
                    tray_id,
                    False,
                    {
                        "expected_tray_info_idx": want["tray_info_idx"],
                        "actual_tray_info_idx": want.get("last_seen_idx"),
                        # True when we saw the tray at least once (so the push
                        # channel is alive and the printer really stored a
                        # different/blank id) vs never observing it at all.
                        "saw_tray": want.get("last_seen_idx") is not None,
                    },
                )

    def _fire_assignment_verified(self, ams_id: int, tray_id: int, verified: bool, detail: dict) -> None:
        if verified:
            logger.info(
                "[%s] Assignment verified: AMS%d-T%d now reports %s (kprofile_applied=%s)",
                self.serial_number,
                ams_id,
                tray_id,
                detail.get("tray_info_idx"),
                detail.get("kprofile_applied"),
            )
        else:
            logger.warning(
                "[%s] Assignment NOT confirmed: AMS%d-T%d expected %s, tray shows %s (saw_tray=%s)",
                self.serial_number,
                ams_id,
                tray_id,
                detail.get("expected_tray_info_idx"),
                detail.get("actual_tray_info_idx"),
                detail.get("saw_tray"),
            )
        if self.on_assignment_verified:
            try:
                self.on_assignment_verified(ams_id, tray_id, verified, detail)
            except Exception:
                logger.exception("[%s] on_assignment_verified callback failed", self.serial_number)

    @staticmethod
    def _probe_number(value, fallback: float | None = None) -> float | None:
        """Coerce a telemetry field to a number, or return `fallback`.

        Firmware is inconsistent about whether these arrive as ints or as
        numeric strings, and the probe must never raise on a surprise type.
        """
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _probe_end_of_print(self, data: dict) -> None:
        """Log raw end-of-print telemetry for one print at DEBUG (#2547).

        Opens on the first frame that looks like end-of-print (last object
        layer reached, progress at 99+, or no remaining time), then logs each
        frame in which any probed field changed, and closes on the transition
        out of RUNNING. Armed once per print — see the module-level comment on
        ``_END_OF_PRINT_PROBE_FIELDS`` for why this window is the one we can't
        currently see into.

        Read-only with respect to printer state: this is instrumentation, and
        nothing downstream may come to depend on it.
        """
        if not logger.isEnabledFor(logging.DEBUG):
            return
        if not self._eop_probe_open and not (self._eop_probe_armed and self._was_running):
            return

        present = {k: data[k] for k in _END_OF_PRINT_PROBE_FIELDS if k in data}
        if not present:
            return

        if not self._eop_probe_open:
            # Open on any end-of-print signal. Read from the raw frame first so
            # the frame that *carries* the signal is itself captured — state
            # fields are only updated further down this same call.
            layer = self._probe_number(data.get("layer_num"), self.state.layer_num) or 0
            total = self._probe_number(data.get("total_layer_num"), self.state.total_layers) or 0
            percent = self._probe_number(data.get("mc_percent"), self.state.progress) or 0
            remaining = self._probe_number(data.get("mc_remaining_time"), self.state.remaining_time)
            at_last_layer = total > 0 and layer >= total
            # `remaining <= 0` is only meaningful once the print has actually
            # progressed — it reads 0 during the pre-print calibration too.
            out_of_time = remaining is not None and remaining <= 0 and percent > 0
            if not (at_last_layer or percent >= 99 or out_of_time):
                return
            self._eop_probe_open = True
            self._eop_probe_frames = 0
            self._eop_probe_last = {}
            logger.debug(
                "[%s] EOP-PROBE open — layer=%s/%s percent=%s remaining=%s",
                self.serial_number,
                layer,
                total,
                percent,
                remaining,
            )

        closing = str(data.get("gcode_state") or "") in _END_OF_PRINT_PROBE_CLOSING_STATES
        changed = {k: v for k, v in present.items() if self._eop_probe_last.get(k, object()) != v}
        self._eop_probe_last.update(present)

        if self._eop_probe_frames >= _END_OF_PRINT_PROBE_MAX_FRAMES and not closing:
            if self._eop_probe_frames == _END_OF_PRINT_PROBE_MAX_FRAMES:
                self._eop_probe_frames += 1
                logger.debug(
                    "[%s] EOP-PROBE frame budget (%s) reached — suppressing until FINISH",
                    self.serial_number,
                    _END_OF_PRINT_PROBE_MAX_FRAMES,
                )
            return

        if changed or closing:
            self._eop_probe_frames += 1
            logger.debug(
                "[%s] EOP-PROBE %s%s: %s",
                self.serial_number,
                self._eop_probe_frames,
                " CLOSE" if closing else "",
                # `changed` on a closing frame can be empty; fall back to the
                # full picture so the last line is always self-contained.
                changed if changed else present,
            )

        if closing:
            self._eop_probe_open = False
            self._eop_probe_armed = False
            self._eop_probe_last = {}

    def _update_state(self, data: dict):
        """Update printer state from message data."""
        _previous_state = self.state.state

        # #2547: instrumentation only — runs before any state mutation so the
        # frame carrying an end-of-print signal is logged as it arrived.
        try:
            self._probe_end_of_print(data)
        except Exception:  # pragma: no cover - a probe must never break ingest
            logger.debug("[%s] EOP-PROBE failed", self.serial_number, exc_info=True)

        # Update state fields
        if "gcode_state" in data:
            self.state.state = data["gcode_state"]
        if "gcode_file" in data:
            self.state.gcode_file = data["gcode_file"]
            self.state.current_print = data["gcode_file"]
        if "subtask_name" in data:
            self.state.subtask_name = data["subtask_name"]
            # Prefer subtask_name as current_print if available
            if data["subtask_name"]:
                self.state.current_print = data["subtask_name"]
        if "subtask_id" in data:
            self.state.subtask_id = data["subtask_id"]
        if "mc_percent" in data:
            # Billing: retain this frame's latest positive value immediately.
            # A display-side abort may be the very next frame (and may omit
            # mc_percent entirely), so retaining only the previous frame can
            # lose the only usable estimate for proportional charging.
            previous_progress = self.state.progress
            new_progress = float(data["mc_percent"])
            if new_progress > 0:
                self._last_valid_progress = new_progress
            self.state.progress = new_progress
            # #2547: strictly-increasing only. The firmware resets progress to 0
            # on cancel and re-reports the same percent on most frames; neither
            # is the print advancing, and both would make the frame bank grab a
            # camera frame for nothing.
            if self.state.progress > previous_progress and self._was_running and self.on_print_progress:
                self.on_print_progress(int(self.state.progress))
        if "mc_remaining_time" in data:
            self.state.remaining_time = int(data["mc_remaining_time"])
        if "mc_print_sub_stage" in data:
            new_sub_stage = int(data["mc_print_sub_stage"])
            if new_sub_stage != self.state.mc_print_sub_stage:
                logger.debug(
                    f"[{self.serial_number}] mc_print_sub_stage changed: "
                    f"{self.state.mc_print_sub_stage} -> {new_sub_stage}"
                )
            self.state.mc_print_sub_stage = new_sub_stage
        # Positive `total_layer_num` carried by *this* frame, or 0. Read up
        # front because three places below consult it and they run in an order
        # that is not the order they read most naturally in: the layer-advance
        # refresh (#2702) must not fire on a frame that already answers it, the
        # apply step must ignore firmware-reset 0s (#1771), and the new-print
        # reset must not discard a total that belongs to the starting print.
        total_from_this_frame = 0
        if "total_layer_num" in data:
            try:
                total_from_this_frame = max(int(data["total_layer_num"] or 0), 0)
            except (TypeError, ValueError):
                # Must not escape. `_on_message` catches only JSONDecodeError
                # and paho is left at `suppress_exceptions = False`, so an
                # exception raised here is re-raised on the network thread and
                # takes the printer connection down over one unusable field.
                # Treat it as "not reported": the refresh below then recovers
                # the real total from a pushall.
                logger.debug(
                    "[%s] ignoring unusable total_layer_num: %r",
                    self.serial_number,
                    data["total_layer_num"],
                )

        if "layer_num" in data:
            try:
                new_layer = int(data["layer_num"])
            except (TypeError, ValueError):
                # Contained for the same reason as `total_layer_num` above: an
                # exception raised here escapes `_update_state` and paho
                # re-raises it on the network thread. Losing this frame would
                # also lose the print-start and completion detection further
                # down, which is worse than losing a layer number.
                #
                # Held at the last known layer rather than substituted with 0:
                # a fabricated 0 reads as the firmware's cancel reset, which
                # would move `_last_valid_layer_num` and show layer 0 in the UI
                # until the next good frame.
                logger.debug(
                    "[%s] ignoring unusable layer_num: %r",
                    self.serial_number,
                    data["layer_num"],
                )
                new_layer = self.state.layer_num
            old_layer = self.state.layer_num
            # Save last non-zero layer for usage tracking (firmware resets to 0 on cancel)
            if old_layer > 0:
                self._last_valid_layer_num = old_layer
            self.state.layer_num = new_layer
            # Trigger layer change callback if layer increased
            if new_layer > old_layer and self.on_layer_change:
                self.on_layer_change(new_layer)
            # #2702: the print is demonstrably laying down layers but we still
            # have no denominator, so the pushall requested at print start
            # either went unanswered or raced the printer learning the total.
            # Ask once more — by layer 1 the printer definitely knows it.
            # One-shot: an unanswered pushall must not turn into a per-layer
            # retry loop for the rest of the print.
            if (
                new_layer > old_layer
                and self._total_layers_refresh_armed
                and not self.state.total_layers
                and not total_from_this_frame
            ):
                self._total_layers_refresh_armed = False
                logger.debug(
                    "[%s] layer %s with no total_layer_num — re-requesting full status",
                    self.serial_number,
                    new_layer,
                )
                self._request_push_all()
            # #2547: there is deliberately NO finish-photo trigger on the
            # last-layer edge. `layer_num` reaching `total_layer_num` is the
            # moment the printer *starts* the final layer, not the moment it
            # finishes it — on the H2C capture that closed #2547 the edge
            # arrived at 92% with `mc_remaining_time=2`, three minutes and a
            # filament change before the print actually ended, so the photo
            # showed the toolhead mid-print over the part. Worse, the trigger
            # latched `_finish_photo_captured`, locking out both the stage-22
            # and FINISH triggers below for the rest of the print.
            #
            # #1867 (End G-code ejects the plate before FINISH) is handled
            # where it belongs instead: `on_finish_photo_moment` prefers the
            # in-print frame bank when the dispatcher recorded that it injected
            # End G-code into this print. See services/print_dispatch_context.
        if total_from_this_frame:
            # Firmware (P1S observed) resets `total_layer_num` to 0 at print
            # end — same shape as the `layer_num` reset guarded above. Applying
            # only positive values preserves the last known good denominator so
            # the usage-tracker split path (#1771) survives the reset frame.
            self.state.total_layers = total_from_this_frame

        # Fan speeds (MQTT sends as string "0"-"15" representing speed levels, or percentage)
        # Convert to 0-100 percentage for display
        def parse_fan_speed(value: str | int | None) -> int | None:
            if value is None:
                return None
            try:
                speed = int(value)
                # MQTT reports 0-15 speed levels, convert to percentage (0-100)
                # 15 = 100%, so multiply by 100/15 ≈ 6.67
                if speed <= 15:
                    return round(speed * 100 / 15)
                # If already a percentage (0-255 scale from some printers), convert
                elif speed <= 255:
                    return round(speed * 100 / 255)
                return speed
            except (ValueError, TypeError):
                return None

        # Log fan fields once for debugging
        if not hasattr(self, "_fan_fields_logged"):
            fan_fields = {k: v for k, v in data.items() if "fan" in k.lower()}
            if fan_fields:
                logger.debug("[%s] Fan fields in MQTT data: %s", self.serial_number, fan_fields)
                self._fan_fields_logged = True

        if "cooling_fan_speed" in data:
            self.state.cooling_fan_speed = parse_fan_speed(data["cooling_fan_speed"])
        if "big_fan1_speed" in data:
            self.state.big_fan1_speed = parse_fan_speed(data["big_fan1_speed"])
        if "big_fan2_speed" in data:
            self.state.big_fan2_speed = parse_fan_speed(data["big_fan2_speed"])
        if "heatbreak_fan_speed" in data:
            self.state.heatbreak_fan_speed = parse_fan_speed(data["heatbreak_fan_speed"])

        # Calibration stage tracking
        if "stg_cur" in data:
            new_stg = data["stg_cur"]
            prev_stg = self.state.stg_cur
            # Always log ANY stg_cur change for debugging filament operations
            if new_stg != prev_stg:
                logger.debug(
                    f"[{self.serial_number}] stg_cur changed: {prev_stg} -> {new_stg} ({get_stage_name(new_stg)})"
                )
                # A stage we cannot name is the one worth seeing at the default
                # log level: the DEBUG line above is off in normal running, so
                # an unnamed stage otherwise reaches the user as "Unknown stage
                # (72)" on a card with nothing behind it to say when it
                # happened or what the printer was doing. Recorded once per
                # stage number per session, with the stage it came from and the
                # print state, which is what naming it later needs. Guarded on
                # the int type because the field is whatever the firmware sent.
                if (
                    isinstance(new_stg, int)
                    and not isinstance(new_stg, bool)
                    # -1 is Bambuddy's own "not in a stage" sentinel and the
                    # initial value of the field, not something the firmware
                    # reports; every print would otherwise report it on the way
                    # out of its last real stage.
                    and new_stg != -1
                    and new_stg not in STAGE_NAMES
                    and new_stg not in self._unnamed_stages_seen
                ):
                    self._unnamed_stages_seen.add(new_stg)
                    logger.info(
                        "[%s] Unnamed print stage %s on model %s, entered from %s (%s); "
                        "state=%s progress=%s%% layer=%s/%s",
                        self.serial_number,
                        new_stg,
                        self.model,
                        prev_stg,
                        get_stage_name(prev_stg),
                        self.state.state,
                        self.state.progress,
                        self.state.layer_num,
                        self.state.total_layers,
                    )
            self.state.stg_cur = new_stg
            # #1721 end-of-print finish photo trigger.
            # Stage 22 = "Filament unloading" fires at end-of-print AND
            # during mid-print color swaps. The end-of-print gate
            # (progress>=99 / layer>=total / remaining<=0) disambiguates
            # — those signals only line up at the real end. Edge-only
            # (prev != 22) so the trigger fires once per stage entry.
            if (
                new_stg == 22
                and prev_stg != 22
                and self._was_running
                and not self._finish_photo_captured
                and self.on_finish_photo_moment
            ):
                progress = self.state.progress or 0.0
                layer_num = self.state.layer_num or 0
                total_layers = self.state.total_layers or 0
                remaining = self.state.remaining_time or 0
                is_end_of_print = progress >= 99 or (total_layers > 0 and layer_num >= total_layers) or remaining <= 0
                if is_end_of_print:
                    self._finish_photo_captured = True
                    logger.info(
                        f"[{self.serial_number}] FINISH PHOTO MOMENT (stage-22) — "
                        f"progress={progress}, layer={layer_num}/{total_layers}, "
                        f"remaining={remaining}min, timelapse_active={self._timelapse_during_print}"
                    )
                    self.on_finish_photo_moment(
                        {
                            "trigger": "stage_22",
                            "filename": self._previous_gcode_file or self.state.gcode_file,
                            "subtask_name": self.state.subtask_name,
                            "timelapse_was_active": self._timelapse_during_print,
                        }
                    )
        if "stg" in data:
            self.state.stg = data["stg"] if isinstance(data["stg"], list) else []

        # Temperature data
        temps = {}
        # Log all fields for debugging dual-nozzle temperature discovery (only once)
        if "bed_temper" in data and not hasattr(self, "_temp_fields_logged"):
            temp_fields = {k: v for k, v in data.items() if "temp" in k.lower() or "chamber" in k.lower()}
            logger.debug("[%s] Temperature-related fields: %s", self.serial_number, temp_fields)
            # Log ALL keys in print data for H2D temperature discovery
            all_keys = sorted(data.keys())
            logger.debug("[%s] ALL print data keys (%s): %s", self.serial_number, len(all_keys), all_keys)
            self._temp_fields_logged = True

        # Log vir_slot data (once) - this may contain per-extruder slot mapping for H2D
        if "vir_slot" in data and not hasattr(self, "_vir_slot_logged"):
            logger.debug("[%s] vir_slot data: %s", self.serial_number, data["vir_slot"])
            self._vir_slot_logged = True

        # Log nozzle hardware info fields (once)
        nozzle_fields = {
            k: v
            for k, v in data.items()
            if "nozzle" in k.lower() or "hw" in k.lower() or "extruder" in k.lower() or "upgrade" in k.lower()
        }
        if nozzle_fields and not hasattr(self, "_nozzle_fields_logged"):
            logger.debug("[%s] Nozzle/hardware fields in MQTT data: %s", self.serial_number, nozzle_fields)
            self._nozzle_fields_logged = True
        # Parse active extruder from device.extruder.state bit 8
        # bit 8 = 0 → RIGHT extruder (active_extruder=0)
        # bit 8 = 1 → LEFT extruder (active_extruder=1)
        if "device" in data and isinstance(data.get("device"), dict):
            device = data["device"]
            # One-shot identification probe: surface whatever the firmware uses to
            # name itself so an unknown model in a support bundle becomes self-
            # diagnosing. INFO level so it shows up without debug logging. Falls
            # back to dumping device.keys() if none of the known fields are present
            # (so a future Bambu rename like `model_name` is still observable).
            if not getattr(self, "_device_id_logged", False):
                id_fields = {
                    k: device.get(k)
                    for k in ("dev_model_name", "dev_product_name", "dev_id", "project_name")
                    if k in device
                }
                if id_fields:
                    logger.info("[%s] Device identification: %s", self.serial_number, id_fields)
                else:
                    logger.info(
                        "[%s] Device identification: no known id fields; device.keys=%s",
                        self.serial_number,
                        sorted(device.keys()),
                    )
                self._device_id_logged = True
            if "extruder" in device and "state" in device["extruder"]:
                state_val = device["extruder"]["state"]
                # Extract bit 8 for extruder position
                new_extruder = (state_val >> 8) & 0x1
                if new_extruder != self.state.active_extruder:
                    logger.debug(
                        f"[{self.serial_number}] ACTIVE EXTRUDER CHANGED (state bit 8): {self.state.active_extruder} -> {new_extruder} (0=right, 1=left) [state={state_val}]"
                    )
                    self.state.active_extruder = new_extruder

        # Log device.extruder structure for active extruder
        if "device" in data and isinstance(data.get("device"), dict):
            device = data["device"]
            if "extruder" in device:
                ext_data = device["extruder"]
                # Log 'state' field - OrcaSlicer uses bits 12-14 for switch state
                if "state" in ext_data:
                    state_val = ext_data["state"]
                    # Extract bits 12-14 (3 bits) for switch state
                    switch_state = (state_val >> 12) & 0x7
                    self._debug_on_change(
                        "extruder_state",
                        state_val,
                        "[%s] device.extruder.state=%s (switch_state bits 12-14: %s)",
                        self.serial_number,
                        state_val,
                        switch_state,
                    )
                # Log 'cur' field if present (might indicate current/active extruder)
                if "cur" in ext_data:
                    logger.debug("[%s] device.extruder.cur: %s", self.serial_number, ext_data["cur"])

        # Also parsed earlier in _process_message, because _handle_ams_data needs
        # it first. Repeated here so _update_state stays a complete "absorb this
        # payload" step for any other caller; re-parsing the same block is free.
        self._parse_fila_switch(data)
        self._parse_extruder_slots(data)

        if "bed_temper" in data:
            temps["bed"] = float(data["bed_temper"])
        if "bed_target_temper" in data:
            temps["bed_target"] = float(data["bed_target_temper"])
        # Check if this is H2D (has device.extruder.info with 2 extruders)
        has_h2d_extruder_info = (
            "device" in data
            and isinstance(data.get("device"), dict)
            and "extruder" in data["device"]
            and isinstance(data["device"]["extruder"].get("info"), list)
            and len(data["device"]["extruder"]["info"]) >= 2
        )

        # Standard nozzle fields: these are for the RIGHT/default nozzle on H2D
        # For H2D, we use these for nozzle_2 (RIGHT), for others use as nozzle (primary)
        # NOTE: On H2D, nozzle_temper seems to mirror left nozzle - we override with extruder_info[0] later
        if "nozzle_temper" in data:
            if has_h2d_extruder_info:
                temps["nozzle_2"] = float(data["nozzle_temper"])  # Will be overridden by extruder_info[0]
            else:
                temps["nozzle"] = float(data["nozzle_temper"])
        if "nozzle_target_temper" in data:
            if has_h2d_extruder_info:
                temps["nozzle_2_target"] = float(data["nozzle_target_temper"])  # RIGHT target on H2D
            else:
                temps["nozzle_target"] = float(data["nozzle_target_temper"])
        # Second nozzle for dual-extruder printers - skip for H2D (uses device.extruder.info instead)
        if not has_h2d_extruder_info:
            # Try multiple possible field names used by different firmware versions
            if "nozzle_temper_2" in data:
                val = float(data["nozzle_temper_2"])
                if -50 < val < 500:  # Valid temp range
                    temps["nozzle_2"] = val
                else:
                    logger.debug("[%s] nozzle_temper_2=%s out of range", self.serial_number, val)
            elif "right_nozzle_temper" in data:
                val = float(data["right_nozzle_temper"])
                if -50 < val < 500:  # Valid temp range
                    temps["nozzle_2"] = val
                else:
                    logger.debug("[%s] right_nozzle_temper=%s out of range", self.serial_number, val)
            if "nozzle_target_temper_2" in data:
                val = float(data["nozzle_target_temper_2"])
                if 0 <= val < 500:  # Valid temp range
                    temps["nozzle_2_target"] = val
                else:
                    logger.debug("[%s] nozzle_target_temper_2=%s out of range", self.serial_number, val)
            elif "right_nozzle_target_temper" in data:
                val = float(data["right_nozzle_target_temper"])
                if 0 <= val < 500:  # Valid temp range
                    temps["nozzle_2_target"] = val
                else:
                    logger.debug("[%s] right_nozzle_target_temper=%s out of range", self.serial_number, val)
            # Also check for left nozzle as primary (some H2 models)
            if "left_nozzle_temper" in data and "nozzle" not in temps:
                temps["nozzle"] = float(data["left_nozzle_temper"])
            if "left_nozzle_target_temper" in data and "nozzle_target" not in temps:
                temps["nozzle_target"] = float(data["left_nozzle_target_temper"])
        if "chamber_temper" in data:
            chamber_val = float(data["chamber_temper"])
            logger.debug("[%s] chamber_temper raw value: %s", self.serial_number, chamber_val)
            # Check if we recently set the target locally (within 5 seconds)
            local_set_time = self.state.temperatures.get("_chamber_target_set_time", 0)
            respect_local = (time.time() - local_set_time) < 5.0
            # H2D protocol: chamber_temper encoding indicates heater state
            # - When > 500: encoded as (target * 65536 + current) - heater is ON
            # - When < 500: direct Celsius current temp only - heater is OFF
            if -50 < chamber_val < 100:
                # Direct value = heater is OFF
                temps["chamber"] = chamber_val
                if not respect_local:
                    temps["chamber_target"] = 0.0  # Heater off means target = 0
                    logger.debug("[%s] chamber_temper direct value: %s°C (heater OFF)", self.serial_number, chamber_val)
            else:
                logger.debug("[%s] chamber_temper %s out of direct range", self.serial_number, chamber_val)
                # Try to decode if it looks like an encoded value
                if chamber_val > 500:
                    mqtt_target = int(chamber_val) // 65536
                    current = int(chamber_val) % 65536
                    logger.debug(
                        f"[{self.serial_number}] chamber_temper decoded: mqtt_target={mqtt_target}, current={current}, respect_local={respect_local}"
                    )
                    if -50 < current < 100:
                        temps["chamber"] = float(current)
                    # Store decoded target for later use, but DON'T set chamber_heating here!
                    # Heating state will be calculated later after parsing ctc.info.target (explicit target)
                    # which is the authoritative source the slicer uses.
                    if not respect_local:
                        if 0 <= mqtt_target <= 60:
                            # Store as "decoded" target - may be overridden by explicit target fields
                            temps["_chamber_decoded_target"] = float(mqtt_target)
        # Chamber target temperature (set by print file or display)
        if "mc_target_cham" in data:
            mc_target = float(data["mc_target_cham"])
            logger.debug("[%s] mc_target_cham raw value: %s", self.serial_number, mc_target)
            # Filter out encoded/invalid values - valid chamber target is 0-60°C
            if 0 <= mc_target <= 60:
                temps["chamber_target"] = mc_target
        # H2D series: Chamber temp is in info.temp (may be encoded or direct °C)
        # NOTE: Don't set chamber_heating here - let ctc.info.target or fallback logic handle it
        # The encoded target in info.temp may be stale (slicer uses ctc.info.target as source of truth)
        try:
            if "info" in data and isinstance(data["info"], dict):
                info_temp = data["info"].get("temp")
                if info_temp is not None and "chamber" not in temps:
                    # Check for encoded value (target * 65536 + current)
                    if info_temp > 500:
                        # Decode: extract current temperature and target
                        target = info_temp // 65536
                        current = info_temp % 65536
                        temps["chamber"] = float(current)
                        # Store decoded target as fallback (may be overridden by ctc.info.target)
                        if "_chamber_decoded_target" not in temps:
                            temps["_chamber_decoded_target"] = float(target)
                        logger.debug(
                            f"[{self.serial_number}] info.temp encoded: {info_temp} -> current={current}, decoded_target={target}"
                        )
                    elif -50 < info_temp < 100:
                        # Valid direct temperature - heater is OFF
                        temps["chamber"] = float(info_temp)
                        temps["chamber_target"] = 0.0  # Direct value means heater off
                        self._debug_on_change(
                            "info_temp_direct",
                            info_temp,
                            "[%s] info.temp direct: %s°C (heater OFF)",
                            self.serial_number,
                            info_temp,
                        )
            # H2D series: Dual extruder temps are in device.extruder.info array
            # Temperature values are encoded as fixed-point (value / 65536 = °C)
            if "device" in data and isinstance(data["device"], dict):
                device = data["device"]
                # Parse dual extruder temperatures
                extruder_data = device.get("extruder", {})
                extruder_info = extruder_data.get("info", [])
                if isinstance(extruder_info, list) and len(extruder_info) >= 1:
                    # H2D nozzle mapping: id=0 is RIGHT nozzle (default), id=1 is LEFT nozzle
                    # Only parse dual nozzle temps if this is actually a dual nozzle printer (H2D)
                    # has_h2d_extruder_info requires len(extruder_info) >= 2
                    if has_h2d_extruder_info:
                        # Right nozzle (extruder 0) - use extruder_info for actual temp, not nozzle_temper
                        # nozzle_temper field seems to mirror left nozzle on H2D, so use extruder_info[0]
                        if "temp" in extruder_info[0]:
                            temp_val = extruder_info[0]["temp"]
                            if temp_val > 500:
                                # Encoded format: temp = target * 65536 + current
                                target = temp_val // 65536
                                current = temp_val % 65536
                                if -50 < current < 500:
                                    temps["nozzle_2"] = float(current)
                                if 0 < target < 500:
                                    temps["nozzle_2_target"] = float(target)
                                temps["nozzle_2_heating"] = target > 0 and current < target
                            elif -50 < temp_val < 500:
                                # Direct Celsius value = heater is OFF
                                temps["nozzle_2"] = float(temp_val)
                                temps["nozzle_2_target"] = 0.0
                                temps["nozzle_2_heating"] = False
                    # Left nozzle (extruder 1) - only for dual nozzle printers
                    # H2D protocol: temp field encoding depends on value
                    # - When > 500: encoded as (target * 65536 + current) - heater is ON
                    # - When < 500: direct Celsius current temp only - heater is OFF
                    if len(extruder_info) >= 2 and "temp" in extruder_info[1]:
                        ext1 = extruder_info[1]
                        temp_val = ext1["temp"]

                        # Check if we recently set the target locally (within 5 seconds)
                        # If so, don't let MQTT data overwrite it
                        local_set_time = self.state.temperatures.get("_nozzle_target_set_time", 0)
                        respect_local_target = (time.time() - local_set_time) < 5.0

                        if temp_val > 500:
                            # Encoded format: temp = target * 65536 + current
                            target = temp_val // 65536
                            current = temp_val % 65536
                            if 0 < target < 500 and not respect_local_target:
                                temps["nozzle_target"] = float(target)
                            if -50 < current < 500:
                                temps["nozzle"] = float(current)
                            # Heating = encoded AND we're using the MQTT target (not local override)
                            # If local target is being respected, use local target to determine heating
                            if respect_local_target:
                                local_target = self.state.temperatures.get("nozzle_target", 0)
                                temps["nozzle_heating"] = local_target > 0 and current < local_target
                            else:
                                temps["nozzle_heating"] = target > 0 and current < target
                        elif -50 < temp_val < 500:
                            # Direct Celsius = heater is OFF (or at target with heater off)
                            temps["nozzle"] = float(temp_val)
                            if not respect_local_target:
                                temps["nozzle_target"] = 0.0
                            temps["nozzle_heating"] = False  # Direct = not heating
                    # Parse H2D snow field (slot now) for accurate tray_now disambiguation
                    # snow encodes AMS ID in high byte: ams_id = snow >> 8, slot = snow & 0xFF
                    if has_h2d_extruder_info:
                        for ext_info in extruder_info:
                            ext_id = ext_info.get("id")
                            snow = ext_info.get("snow")
                            if ext_id is not None and snow is not None and ext_id <= 1:
                                # Normalize H2D snow value to global tray ID
                                ams_id = snow >> 8
                                slot = snow & 0xFF
                                if 0 <= ams_id <= 3:
                                    # Regular AMS slot
                                    global_tray = ams_id * 4 + (slot & 0x03)
                                    old_val = self.state.h2d_extruder_snow.get(ext_id)
                                    if old_val != global_tray:
                                        logger.debug(
                                            f"[{self.serial_number}] H2D extruder[{ext_id}] snow: "
                                            f"raw={snow} (AMS {ams_id} slot {slot}) -> global tray {global_tray}"
                                        )
                                    self.state.h2d_extruder_snow[ext_id] = global_tray
                                elif ams_id == 254 or ams_id == 255:
                                    # External spool or unloaded
                                    normalized = 254 if slot != 255 else 255
                                    old_val = self.state.h2d_extruder_snow.get(ext_id)
                                    if old_val != normalized:
                                        logger.debug(
                                            f"[{self.serial_number}] H2D extruder[{ext_id}] snow: "
                                            f"raw={snow} -> {'external' if normalized == 254 else 'unloaded'}"
                                        )
                                    self.state.h2d_extruder_snow[ext_id] = normalized
                                elif 128 <= ams_id <= 135:
                                    # External spool with hub mapping
                                    old_val = self.state.h2d_extruder_snow.get(ext_id)
                                    if old_val != ams_id:
                                        logger.debug(
                                            f"[{self.serial_number}] H2D extruder[{ext_id}] snow: "
                                            f"raw={snow} -> external hub {ams_id}"
                                        )
                                    self.state.h2d_extruder_snow[ext_id] = ams_id
                # Parse bed heating state from device.bed.info.temp encoding
                # temp > 500 means encoded (target*65536+current), heating = target > 0 AND current < target
                bed_data = device.get("bed", {})
                bed_info = bed_data.get("info", {})
                if "temp" in bed_info:
                    temp_val = bed_info["temp"]
                    if temp_val > 500:
                        target = temp_val // 65536
                        current = temp_val % 65536
                        temps["bed_heating"] = target > 0 and current < target
                    else:
                        temps["bed_heating"] = False
                # Parse chamber temp from device.ctc.info.temp if not already set
                ctc_data = device.get("ctc", {})
                ctc_info = ctc_data.get("info", {})
                # Parse airduct mode (0=cooling, 1=heating)
                airduct_data = device.get("airduct", {})
                if "modeCur" in airduct_data:
                    new_mode = airduct_data["modeCur"]
                    if new_mode != self.state.airduct_mode:
                        logger.debug(
                            f"[{self.serial_number}] airduct_mode changed: {self.state.airduct_mode} -> {new_mode}"
                        )
                    self.state.airduct_mode = new_mode
                # Parse individual airduct fan parts (new-protocol models: P2S/X2D/H2*).
                # Raw part ids are bit-packed — decoded id = raw_id >> 4 (bits 4-11),
                # mirroring Bambu Studio DevFan::ParseV3_0. Decoded ids follow the
                # AIR_FUN enum: 1=part cooling, 2=right aux, 3=chamber/exhaust,
                # 10=left aux (FAN_REMOTE_COOLING_1). The airduct `parts` list only
                # contains the fans that physically exist, so it doubles as a
                # presence signal for the two P2S/X2D add-on kits:
                #   - id 10 (left auxiliary part cooling fan) — reported ONLY here,
                #     never mirrored into a flat big_fanX_speed field.
                #   - id 3 (chamber exhaust fan) — its speed is mirrored into
                #     big_fan2_speed, but the part is only listed when the External
                #     Exhaust Fan kit (get_version module "eef") is installed.
                # `state` is already a 0-100 percentage.
                parts = airduct_data.get("parts")
                if isinstance(parts, list):
                    speeds: dict[int, int] = {}
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        try:
                            # Studio reads the id with get_flag_bits(id, 4, 8),
                            # so mask after shifting for the same reason `state`
                            # is masked below. Every id seen in the wild
                            # (16/32/48/160) decodes identically either way —
                            # this is consistency, not a live bug.
                            part_id = (int(part["id"]) >> 4) & 0xFF
                            # `state` is bit-packed like its sibling `range`
                            # (end << 16 | start), so take only the low 8 bits —
                            # the same decode Bambu Studio does with
                            # get_flag_bits(state, 0, 8). Without the mask a
                            # packed value would clamp to 100 instead of
                            # decoding to the real percentage.
                            part_state = int(part["state"]) & 0xFF
                        except (KeyError, ValueError, TypeError):
                            continue
                        # Ids seen across the support-package archive:
                        #   1 part cooling, 2 aux, 3 chamber/exhaust,
                        #   6 (H2 series, unmapped), 10 left aux.
                        speeds[part_id] = max(0, min(100, part_state))

                    # Absence in this list is what tells us a kit is NOT fitted,
                    # so it may only be trusted when the list is a full
                    # inventory rather than a diff frame. `device.airduct` is
                    # pushed field by field — the `modeCur` handler above exists
                    # for exactly that reason — and a truncated `parts` read as
                    # gospel would retract both accessory badges mid-print and
                    # start rejecting `aux2` on a printer that has the fan.
                    #
                    # Every airduct layout in the support-package archive
                    # (P2S base 1,2 / P2S+kit 1,2,3 / X2D 1,2,3,10 /
                    # H2C,H2D,H2S 1,2,3,6 — 37 of 37 bundles) contains both the
                    # part cooling fan and the aux fan, neither of which is
                    # optional on any machine that reports an airduct at all.
                    # A list carrying both is therefore a complete inventory; a
                    # list missing either is a partial frame, and we take its
                    # speeds without touching presence.
                    is_full_inventory = 1 in speeds and 2 in speeds

                    left_aux_speed = speeds.get(10)
                    if left_aux_speed is None and not is_full_inventory:
                        # Partial frame that didn't mention the left aux fan —
                        # keep whatever we already knew about it.
                        left_aux_speed = self.state.left_aux_fan_speed
                    if left_aux_speed != self.state.left_aux_fan_speed:
                        logger.debug(
                            f"[{self.serial_number}] left_aux_fan_speed changed: "
                            f"{self.state.left_aux_fan_speed} -> {left_aux_speed}"
                        )
                    # A FULL parts list without id 10 means the left aux fan is
                    # not installed — report None so the UI can hide the widget.
                    self.state.left_aux_fan_speed = left_aux_speed
                    # id 3 present == chamber exhaust fan installed (base P2S
                    # omits it). Only ever retracted on a full inventory.
                    if 3 in speeds:
                        self.state.exhaust_fan_present = True
                    elif is_full_inventory:
                        self.state.exhaust_fan_present = False
                # Parse chamber temp - may be encoded as (target*65536+current) when > 500
                # Check if we recently set the target locally (within 5 seconds)
                local_set_time = self.state.temperatures.get("_chamber_target_set_time", 0)
                respect_local_target = (time.time() - local_set_time) < 5.0

                # Log ctc_info contents for debugging
                if ctc_info:
                    self._debug_on_change(
                        "ctc_info_keys",
                        tuple(ctc_info.keys()),
                        "[%s] ctc_info keys: %s",
                        self.serial_number,
                        list(ctc_info.keys()),
                    )

                # FIRST: Parse explicit ctc.info.target if available - this is the authoritative target
                # (what the slicer shows). This OVERRIDES any previously decoded target.
                explicit_target = None
                if "target" in ctc_info:
                    target_val = ctc_info["target"]
                    logger.debug(
                        f"[{self.serial_number}] ctc_info.target explicit value: {target_val}, respect_local={respect_local_target}"
                    )
                    # Filter out invalid values (valid chamber target is 0-60°C)
                    if 0 <= target_val <= 60 and not respect_local_target:
                        explicit_target = float(target_val)
                        temps["chamber_target"] = explicit_target  # Override any previous value
                        logger.debug(
                            f"[{self.serial_number}] Setting chamber_target from ctc_info.target: {explicit_target}"
                        )

                # Parse chamber temp from ctc.info.temp - may be encoded
                if "temp" in ctc_info and "chamber" not in temps:
                    temp_val = ctc_info["temp"]
                    logger.debug("[%s] ctc_info.temp raw value: %s", self.serial_number, temp_val)
                    if temp_val > 500:
                        # Encoded value: decode target and current
                        decoded_target = temp_val // 65536
                        current = temp_val % 65536
                        temps["chamber"] = float(current)
                        logger.debug(
                            f"[{self.serial_number}] ctc_info.temp decoded: target={decoded_target}, current={current}, explicit_target={explicit_target}"
                        )

                        # Determine which target to use for heating state:
                        # Priority: local target > explicit target > decoded target
                        if respect_local_target:
                            local_target = self.state.temperatures.get("chamber_target", 0)
                            temps["chamber_heating"] = local_target > 0 and current < local_target
                        elif explicit_target is not None:
                            # Use explicit ctc.info.target - this is what slicer sees
                            temps["chamber_heating"] = explicit_target > 0 and current < explicit_target
                        else:
                            # Fallback to decoded target only if no explicit target available
                            if not respect_local_target and "chamber_target" not in temps:
                                temps["chamber_target"] = float(decoded_target)
                            temps["chamber_heating"] = decoded_target > 0 and current < decoded_target
                    else:
                        # Direct value (not encoded) - heater is OFF
                        temps["chamber"] = float(temp_val)
                        temps["chamber_heating"] = False
        except Exception as e:
            logger.warning("[%s] Error parsing H2D temperatures: %s", self.serial_number, e)
        if temps:
            # Handle chamber_target: prefer explicit over decoded
            if "_chamber_decoded_target" in temps and "chamber_target" not in temps:
                # No explicit target available, use decoded target from chamber_temper
                temps["chamber_target"] = temps["_chamber_decoded_target"]
            # Remove internal temp key before merging
            temps.pop("_chamber_decoded_target", None)

            # Merge new temps into existing, preserving valid values when new ones are filtered out
            for key, value in temps.items():
                self.state.temperatures[key] = value

            # Notify bed temperature updates (used by event-driven bed cooldown monitor)
            if "bed" in temps and self.on_bed_temp_update:
                self.on_bed_temp_update(temps["bed"])

            # Calculate chamber_heating after all targets are known
            # Priority: local target (if recent) > explicit target (chamber_target) > 0
            if "chamber" in temps and "chamber_heating" not in temps:
                current = self.state.temperatures.get("chamber", 0)
                local_set_time = self.state.temperatures.get("_chamber_target_set_time", 0)
                respect_local = (time.time() - local_set_time) < 5.0

                if respect_local:
                    # Use locally-set target
                    target = self.state.temperatures.get("chamber_target", 0)
                else:
                    # Use explicit/decoded target from MQTT
                    target = self.state.temperatures.get("chamber_target", 0)

                self.state.temperatures["chamber_heating"] = target > 0 and current < target
                self._debug_on_change(
                    "chamber_heating",
                    (target, current, self.state.temperatures["chamber_heating"], respect_local),
                    "[%s] Chamber heating calculated: target=%s, current=%s, heating=%s, respect_local=%s",
                    self.serial_number,
                    target,
                    current,
                    self.state.temperatures["chamber_heating"],
                    respect_local,
                )

            # Debug: log chamber value if it was updated
            if "chamber" in temps:
                self._debug_on_change(
                    "chamber_temp",
                    (
                        self.state.temperatures.get("chamber"),
                        self.state.temperatures.get("chamber_target"),
                        self.state.temperatures.get("chamber_heating"),
                    ),
                    "[%s] Chamber temp updated to: %s, target: %s, heating: %s",
                    self.serial_number,
                    self.state.temperatures.get("chamber"),
                    self.state.temperatures.get("chamber_target"),
                    self.state.temperatures.get("chamber_heating"),
                )

            # Calculate nozzle_heating for single nozzle printers (not set by H2D parsing)
            # For H2D, nozzle_heating is set in temps dict; for single nozzle, calculate here
            if "nozzle" in temps and "nozzle_heating" not in temps:
                current = self.state.temperatures.get("nozzle", 0)
                target = self.state.temperatures.get("nozzle_target", 0)
                self.state.temperatures["nozzle_heating"] = target > 0 and current < target

        # Parse HMS (Health Management System) errors
        if "hms" in data:
            hms_list = data["hms"]
            logger.debug("[%s] HMS data received: %s", self.serial_number, hms_list)
            self.state.hms_errors = []
            verify_failed = False
            if isinstance(hms_list, list):
                for hms in hms_list:
                    if isinstance(hms, dict):
                        # HMS format: {"attr": attribute_code, "code": error_code}
                        # attr contains module/severity info, code contains error number
                        # Both are needed to construct the wiki URL
                        attr = hms.get("attr", 0)
                        code = hms.get("code", 0)
                        if isinstance(attr, str):
                            attr = int(attr.replace("0x", ""), 16) if attr else 0
                        if isinstance(code, str):
                            code = int(code.replace("0x", ""), 16) if code else 0
                        # Severity is in attr byte 1 (bits 8-15)
                        severity = (attr >> 8) & 0xF
                        # Module is in attr byte 3 (bits 24-31)
                        module = (attr >> 24) & 0xFF
                        # Skip non-error status codes — all real HMS errors
                        # have code >= 0x4000. Lower values are status/phase
                        # indicators that some firmware sends during normal printing.
                        if code < 0x4000:
                            continue
                        # Skip user-action echoes — the printer firmware emits these
                        # as part of normal user-cancel sequences. They're not faults
                        # and shouldn't count toward "X problem" badges or surface as
                        # red pips on the printer card. Backend's notification path
                        # already suppresses 0500_400E for the same reason.
                        short_code = f"{(attr >> 16) & 0xFFFF:04X}_{code & 0xFFFF:04X}"
                        if short_code in _HMS_USER_ACTION_CODES:
                            continue
                        # Catalog has both 8-char keys (base class) and 16-char keys
                        # (specific variants). The full 16-char identifier preserves
                        # the 32 bits of `attr_low` + `code_high` that the short_code
                        # discards — that's the firmware's matching key, so try it
                        # first and fall back to the short form.
                        full_code = f"{attr:08X}{code:08X}"
                        if full_code == HMS_MQTT_VERIFY_FAILED:
                            verify_failed = True
                        actions = get_actions_for_error_code(self.serial_number[:3], full_code)
                        if not actions:
                            actions = get_actions_for_error_code(self.serial_number[:3], short_code.replace("_", ""))
                        self.state.hms_errors.append(
                            HMSError(
                                code=f"0x{code:x}" if code else "0x0",
                                attr=attr,
                                module=module,
                                severity=severity if severity > 0 else 2,
                                actions=actions,
                                job_id=self.state.subtask_id,
                                full_code=full_code,
                                description=describe_fault(full_code),
                            )
                        )
            self._apply_mqtt_verify_state(verify_failed)

        # Parse print_error - this is a different error format than HMS
        # print_error is a 32-bit integer where:
        #   - High 16 bits contain module info (e.g., 0x0500)
        #   - Low 16 bits contain error code (e.g., 0x8061)
        # Format on printer screen: [0500-8061] -> short code: 0500_8061
        if "print_error" in data:
            print_error = data["print_error"]
            if print_error and print_error != 0:
                # Extract components: MMMMEEEE -> MMMM_EEEE
                module = (print_error >> 16) & 0xFFFF  # High 16 bits (e.g., 0x0500)
                error = print_error & 0xFFFF  # Low 16 bits (e.g., 0x8061)

                # Values below 0x4000 are status/phase indicators, not real errors.
                # All known HMS errors use 0x4xxx (fatal), 0x8xxx (warning), 0xCxxx (prompt).
                # Some firmware sends low values like 0x0002 during normal printing.
                if error < 0x4000:
                    pass  # Skip — not a real error
                else:
                    # Store in a format that matches the community error database
                    # attr stores the full 32-bit value for reconstruction
                    # code stores the short format string for lookup
                    short_code = f"{module:04X}_{error:04X}"

                    logger.debug(
                        f"[{self.serial_number}] print_error: {print_error} (0x{print_error:08x}) -> short_code={short_code}"
                    )

                    # Same user-action filter as the hms[] branch above — print_error
                    # carries the same cancel echoes (e.g. 0500_400E) and they must
                    # not surface as faults on the printer card.
                    if short_code in _HMS_USER_ACTION_CODES:
                        pass  # cancel echo — silently drop
                    else:
                        # Only add if not already in HMS errors (avoid duplicates)
                        existing_short_codes = set()
                        for e in self.state.hms_errors:
                            # Extract short code from existing errors
                            e_module = (e.attr >> 16) & 0xFFFF
                            e_error = int(e.code.replace("0x", ""), 16) if e.code else 0
                            existing_short_codes.add(f"{e_module:04X}_{e_error:04X}")

                        if short_code not in existing_short_codes:
                            # Bambu's HMS catalog keys by 3-letter device code (the SN
                            # prefix) and a 16-char short error code without the
                            # underscore separator we store internally.
                            actions = get_actions_for_error_code(self.serial_number[:3], short_code.replace("_", ""))
                            # Bambu pushes the current job as `subtask_id` on the
                            # state stream; the HMS-action commands echo it back as
                            # `job_id`. The error payload itself doesn't carry the
                            # id, so snapshot it from the live state at parse time
                            # and freeze it on the HMSError so subsequent
                            # job changes don't invalidate the action.
                            job_id = self.state.subtask_id
                            logger.debug(
                                "[%s, %s] HMS available actions: %s (job_id=%s)",
                                self.serial_number[:3],
                                short_code.replace("_", ""),
                                actions,
                                job_id,
                            )
                            self.state.hms_errors.append(
                                HMSError(
                                    code=f"0x{error:x}",
                                    attr=print_error,  # Store full value for display
                                    module=module >> 8,  # High byte of module (e.g., 0x05)
                                    severity=3,  # Warning level for print_error
                                    actions=actions,
                                    job_id=job_id,
                                    # print_error is already 32-bit — `f"{print_error:08X}"`
                                    # is the firmware's matching key with no truncation.
                                    full_code=f"{print_error:08X}",
                                    description=describe_fault(f"{print_error:08X}"),
                                )
                            )

        # Parse home_flag first so SD-card detection below can prefer it.
        # Bit 8 = HAS_SDCARD_NORMAL, bit 9 = HAS_SDCARD_ABNORMAL, bit 11 = store-to-SD,
        # bit 23 = door-open (X1 family only).
        home_flag = None
        if "home_flag" in data:
            home_flag = data["home_flag"]
            if home_flag < 0:
                home_flag = home_flag & 0xFFFFFFFF

        # SD card presence: the only remaining consumer is the firmware-update
        # precondition check (firmware_update.py). Use the top-level `sdcard`
        # field when present with a permissive truthy check covering the
        # bool/int/"HAS_SDCARD_NORMAL" variants real firmware emits. We do NOT
        # derive this from home_flag — heartbeat pushes clear bits 8-9 even
        # when a card is inserted, which caused the badge to flap before the
        # badge was removed entirely.
        if "sdcard" in data:
            raw_sdcard = data["sdcard"]
            if isinstance(raw_sdcard, str):
                self.state.sdcard = "HAS_SDCARD" in raw_sdcard.upper() or raw_sdcard.lower() in ("true", "normal", "1")
            else:
                self.state.sdcard = bool(raw_sdcard)
            self.state.sdcard_reported = True

        if home_flag is not None:
            store_to_sdcard = bool((home_flag >> 11) & 1)
            if store_to_sdcard != self.state.store_to_sdcard:
                logger.debug(
                    f"[{self.serial_number}] store_to_sdcard changed: {self.state.store_to_sdcard} -> {store_to_sdcard}"
                )
            self.state.store_to_sdcard = store_to_sdcard

        # Door open detection — source depends on printer family:
        #   X1 series (X1, X1C, X1E): home_flag bit 23
        #   All others (P1/P2/H2/A1/N-series): top-level `stat` field (hex string), bit 23
        # Both share the same bitmask (0x00800000) but live in different fields.
        model_upper = (self.model or "").upper().strip()
        is_x1_family = model_upper in ("X1", "X1C", "X1E")
        if is_x1_family and home_flag is not None:
            door_open = (home_flag & 0x00800000) != 0
            if door_open != self.state.door_open:
                logger.debug(
                    "[%s] door_open changed: %s -> %s (home_flag=0x%08X)",
                    self.serial_number,
                    self.state.door_open,
                    door_open,
                    home_flag,
                )
            self.state.door_open = door_open
        elif not is_x1_family and "stat" in data:
            try:
                stat_value = int(data["stat"], 16) if isinstance(data["stat"], str) else int(data["stat"])
                door_open = (stat_value & 0x00800000) != 0
                if door_open != self.state.door_open:
                    logger.debug(
                        "[%s] door_open changed: %s -> %s (stat=0x%08X)",
                        self.serial_number,
                        self.state.door_open,
                        door_open,
                        stat_value,
                    )
                self.state.door_open = door_open
            except (ValueError, TypeError):
                logger.debug("[%s] could not parse stat field: %r", self.serial_number, data["stat"])

        # Parse timelapse status (recording active during print)
        if "timelapse" in data:
            logger.debug("[%s] timelapse field: %s", self.serial_number, data["timelapse"])
            self.state.timelapse = data["timelapse"] is True
            # Track if timelapse was ever active during this print
            if self.state.timelapse and self._was_running:
                self._timelapse_during_print = True

        # Parse ipcam/live view status
        if "ipcam" in data:
            ipcam_data = data["ipcam"]
            self._debug_on_change("ipcam", ipcam_data, "[%s] ipcam field: %s", self.serial_number, ipcam_data)
            if isinstance(ipcam_data, dict):
                # Check ipcam_record field for live view status
                self.state.ipcam = ipcam_data.get("ipcam_record") == "enable"
                # Check timelapse field (H2D sends it here, not in xcam)
                if "timelapse" in ipcam_data:
                    timelapse_enabled = ipcam_data.get("timelapse") == "enable"
                    if timelapse_enabled != self.state.timelapse:
                        logger.debug(
                            f"[{self.serial_number}] timelapse changed (from ipcam): {self.state.timelapse} -> {timelapse_enabled}"
                        )
                    self.state.timelapse = timelapse_enabled
                    # Track if timelapse was ever active during this print
                    if self.state.timelapse and self._was_running:
                        self._timelapse_during_print = True
                        logger.debug("[%s] Timelapse detected during print (from ipcam)", self.serial_number)
            else:
                self.state.ipcam = ipcam_data is True

        # Parse WiFi signal strength (dBm)
        if "wifi_signal" in data:
            wifi_signal = data["wifi_signal"]
            self._debug_on_change(
                "wifi_signal", wifi_signal, "[%s] wifi_signal received: %s", self.serial_number, wifi_signal
            )
            if isinstance(wifi_signal, (int, float)):
                self.state.wifi_signal = int(wifi_signal)
            elif isinstance(wifi_signal, str):
                # Handle string format like "-52dBm"
                try:
                    self.state.wifi_signal = int(wifi_signal.replace("dBm", "").strip())
                except ValueError:
                    pass  # Ignore unparseable wifi_signal strings; field is non-critical

            # Detect ethernet connection: printers on ethernet with WiFi disabled
            # report a hardcoded wifi_signal of -90 dBm. Real WiFi signals vary
            # (typically -30 to -80 dBm). Only check models with an ethernet port.
            from backend.app.utils.printer_models import has_ethernet

            if has_ethernet(self.model):
                self.state.wired_network = self.state.wifi_signal == -90

        # Parse print speed level (1=silent, 2=standard, 3=sport, 4=ludicrous)
        if "spd_lvl" in data:
            new_speed = data["spd_lvl"]
            if new_speed != self.state.speed_level:
                logger.debug(
                    "[%s] speed_level changed: %s -> %s", self.serial_number, self.state.speed_level, new_speed
                )
            self.state.speed_level = new_speed

        # Parse skipped objects from printer status (s_obj field)
        # This allows us to restore skipped objects state after reconnection
        if "s_obj" in data:
            s_obj = data["s_obj"]
            if isinstance(s_obj, list):
                # Update skipped objects from printer's list
                new_skipped = [int(oid) for oid in s_obj if isinstance(oid, (int, str))]
                if new_skipped != self.state.skipped_objects:
                    logger.debug("[%s] skipped_objects updated from printer: %s", self.serial_number, new_skipped)
                    self.state.skipped_objects = new_skipped

        # Parse chamber light status from lights_report
        if "lights_report" in data:
            lights = data["lights_report"]
            logger.debug("[%s] lights_report: %s", self.serial_number, lights)
            if isinstance(lights, list):
                for light in lights:
                    if isinstance(light, dict) and light.get("node") == "chamber_light":
                        new_light_state = light.get("mode") == "on"
                        if new_light_state != self.state.chamber_light:
                            logger.debug(
                                f"[{self.serial_number}] chamber_light changed: {self.state.chamber_light} -> {new_light_state}"
                            )
                        self.state.chamber_light = new_light_state
                        break

        # Parse nozzle hardware info (single nozzle printers)
        if "nozzle_type" in data:
            self.state.nozzles[0].nozzle_type = str(data["nozzle_type"])
        if "nozzle_diameter" in data:
            self.state.nozzles[0].nozzle_diameter = str(data["nozzle_diameter"])

        # Parse nozzle hardware info (dual nozzle printers - H2D series)
        # Left nozzle
        if "left_nozzle_type" in data:
            self.state.nozzles[0].nozzle_type = str(data["left_nozzle_type"])
        if "left_nozzle_diameter" in data:
            self.state.nozzles[0].nozzle_diameter = str(data["left_nozzle_diameter"])
        # Right nozzle
        if "right_nozzle_type" in data:
            self.state.nozzles[1].nozzle_type = str(data["right_nozzle_type"])
        if "right_nozzle_diameter" in data:
            self.state.nozzles[1].nozzle_diameter = str(data["right_nozzle_diameter"])

        # Alternative format for dual nozzle (nozzle_type_2, etc.)
        if "nozzle_type_2" in data:
            self.state.nozzles[1].nozzle_type = str(data["nozzle_type_2"])
        if "nozzle_diameter_2" in data:
            self.state.nozzles[1].nozzle_diameter = str(data["nozzle_diameter_2"])

        # H2D/H2C series: Nozzle hardware info is in device.nozzle.info array
        if "device" in data and isinstance(data["device"], dict):
            device = data["device"]
            nozzle_data = device.get("nozzle", {})

            # H2C rack position (#2800). `tar_id` is where the carriage is
            # headed, `src_id` where it came from; mid-swap they differ, so
            # dispatch prefers tar_id and falls back to src_id. Both are
            # sticky — the field is only pushed when it changes, so an
            # absent key must leave the last known value alone rather than
            # reset it to None.
            if isinstance(nozzle_data, dict):
                for key, attr in (("src_id", "nozzle_rack_src_id"), ("tar_id", "nozzle_rack_tar_id")):
                    if key not in nozzle_data:
                        continue
                    try:
                        parsed_id = int(nozzle_data[key])
                    except (TypeError, ValueError):
                        continue
                    if getattr(self.state, attr) != parsed_id:
                        setattr(self.state, attr, parsed_id)
                        # DEBUG, not INFO: these move on every tool change, so
                        # a long multi-material print would otherwise write
                        # thousands of lines. The dispatch log records both
                        # values once per print, which is where triage needs
                        # them. Same reasoning as the one-shot `nozzle_info`
                        # log below.
                        logger.debug(
                            "[%s] Nozzle rack %s -> %s",
                            self.serial_number,
                            key,
                            parsed_id,
                        )

            nozzle_info = nozzle_data.get("info", [])
            if isinstance(nozzle_info, list):
                # H2 series: nozzle_info contains extended nozzle data (wear, serial,
                # max_temp, etc.) for all nozzles: L/R hotend (IDs 0,1) and rack slots
                # (IDs 16-21 on H2C). Store ALL entries so the frontend can use them
                # for hover cards on both the L/R indicator and the nozzle rack card.
                if nozzle_info:
                    self.state.nozzle_rack = sorted(
                        [
                            {
                                "id": n.get("id", i),
                                "type": str(n.get("type", "")),
                                "diameter": str(n.get("diameter", "")),
                                "wear": n.get("wear"),
                                "stat": n.get("stat"),
                                # H2C uses "tm", H2D uses "max_temp"
                                "max_temp": n.get("max_temp") or n.get("tm", 0),
                                # H2C uses "sn", H2D uses "serial_number"
                                "serial_number": str(n.get("serial_number") or n.get("sn", "")),
                                # H2C uses "color_m", H2D uses "filament_colour"
                                "filament_color": str(n.get("filament_colour") or n.get("color_m", "")),
                                # H2C uses "fila_id", H2D uses "filament_id"
                                "filament_id": str(n.get("filament_id") or n.get("fila_id", "")),
                                "filament_type": str(n.get("tray_type", "") or n.get("filament_type", "")),
                            }
                            for i, n in enumerate(nozzle_info)
                        ],
                        key=lambda x: x["id"],
                    )
                    if not hasattr(self, "_nozzle_rack_logged") and nozzle_info:
                        self._nozzle_rack_logged = True
                        logger.debug(
                            "[%s] Nozzle info: %d entries, IDs: %s",
                            self.serial_number,
                            len(nozzle_info),
                            [n.get("id") for n in nozzle_info],
                        )
                for nozzle in nozzle_info:
                    idx = nozzle.get("id", 0)
                    if idx < len(self.state.nozzles):
                        if "type" in nozzle and nozzle["type"]:
                            self.state.nozzles[idx].nozzle_type = str(nozzle["type"])
                        if "diameter" in nozzle:
                            self.state.nozzles[idx].nozzle_diameter = str(nozzle["diameter"])

        # Preserve AMS, vt_tray, ams_extruder_map, and mapping data when updating raw_data
        # (these fields aren't sent in every MQTT push, only when changed)
        ams_data = self.state.raw_data.get("ams")
        vt_tray_data = self.state.raw_data.get("vt_tray")
        ams_extruder_map_data = self.state.raw_data.get("ams_extruder_map")
        mapping_data = self.state.raw_data.get("mapping")

        # Normalize vt_tray in data before assigning to raw_data: MQTT sends it
        # as a dict but consumers expect a list.  Without this, the dev mode probe
        # below can release the GIL (via publish), letting the event-loop thread
        # read raw_data["vt_tray"] as a dict and crash iterating over string keys.
        if "vt_tray" in data and isinstance(data["vt_tray"], dict):
            data["vt_tray"] = [data["vt_tray"]]

        self.state.raw_data = data

        # Restore preserved fields BEFORE any work that may release the GIL
        # (e.g. _probe_developer_mode publishes an MQTT message).
        if ams_data is not None:
            self.state.raw_data["ams"] = ams_data
        if vt_tray_data is not None:
            self.state.raw_data["vt_tray"] = vt_tray_data
        if ams_extruder_map_data is not None:
            self.state.raw_data["ams_extruder_map"] = ams_extruder_map_data
        if mapping_data is not None and "mapping" not in data:
            self.state.raw_data["mapping"] = mapping_data

        # Parse developer LAN mode from "fun" field
        if "fun" in data:
            try:
                fun_val = data["fun"]
                fun_int = fun_val if isinstance(fun_val, int) else int(fun_val, 16)
                self.state.developer_mode = (fun_int & 0x20000000) == 0
            except (ValueError, TypeError):
                pass
        elif self.state.developer_mode is None and not self._dev_mode_probed:
            # No "fun" field — A1/P1 series never send it, so we need to probe.
            # Two gates: (1) wait for a full pushall (30+ keys) so we don't probe
            # before a pushall that might contain "fun" arrives, and (2) delay 5s
            # after connect to let the MQTT session stabilize — probing too early
            # can destabilize some firmware MQTT brokers (#887).
            if not self._dev_mode_needs_probe and len(data) > 30:
                # First full status without "fun" — mark that probe is needed
                self._dev_mode_needs_probe = True
            if self._dev_mode_needs_probe and time.monotonic() - self._connect_time >= 5.0:
                self._probe_developer_mode()
            elif self._dev_mode_needs_probe:
                logger.debug(
                    "[%s] Deferring developer mode probe (%.1fs since connect, need 5s)",
                    self.serial_number,
                    time.monotonic() - self._connect_time,
                )
        elif self._dev_mode_probed and self._dev_mode_probe_seq is not None:
            # Probe was sent but no response yet — check for timeout.
            # A half-broken MQTT session (e.g. after keep-alive timeout reconnect)
            # may deliver status pushes but silently drop commands (#887).
            elapsed = time.monotonic() - self._dev_mode_probe_time
            if elapsed > 10.0:
                self._dev_mode_probe_failures += 1
                logger.warning(
                    "[%s] Developer mode probe timed out after %.0fs (attempt %d)",
                    self.serial_number,
                    elapsed,
                    self._dev_mode_probe_failures,
                )
                self._dev_mode_probe_seq = None
                if self._dev_mode_probe_failures >= 2:
                    self.force_reconnect_stale_session("developer mode probe unanswered 2×")
                else:
                    # Allow retry on next full status message
                    self._dev_mode_probed = False

        # Zombie session detection: if an ams_filament_setting command has been
        # pending for >10s with no response, the publish path is likely dead (#887).
        if self._last_ams_cmd_time > 0:
            elapsed = time.monotonic() - self._last_ams_cmd_time
            if elapsed > 10.0:
                self._ams_cmd_unanswered += 1
                logger.warning(
                    "[%s] ams_filament_setting unanswered for %.0fs (count=%d)",
                    self.serial_number,
                    elapsed,
                    self._ams_cmd_unanswered,
                )
                self._last_ams_cmd_time = 0.0  # don't re-trigger on next push_status
                if self._ams_cmd_unanswered >= 2:
                    self.force_reconnect_stale_session("ams_filament_setting unanswered 2\u00d7")
                    self._ams_cmd_unanswered = 0

        # Log mapping data when received (for usage tracking debugging)
        if "mapping" in data:
            logger.debug("[%s] MQTT mapping field: %s", self.serial_number, data["mapping"])

        # Log state transitions for debugging
        if "gcode_state" in data:
            logger.debug(
                f"[{self.serial_number}] gcode_state: {self._previous_gcode_state} -> {self.state.state}, "
                f"file: {self.state.gcode_file}, subtask: {self.state.subtask_name}"
            )

        # Detect print start (state changes TO RUNNING with a file)
        current_file = self.state.gcode_file or self.state.current_print
        is_new_print = (
            self.state.state == "RUNNING"
            and self._previous_gcode_state is not None  # #1304: skip on first push after Bambuddy startup
            and self._previous_gcode_state != "RUNNING"
            and current_file
            and not self._was_running  # Prevent duplicates when resuming from PAUSE
        )
        # Also detect if file changed while running (new print started)
        is_file_change = (
            self.state.state == "RUNNING"
            and current_file
            and current_file != self._previous_gcode_file
            and self._previous_gcode_file is not None
        )

        # Track RUNNING state for more robust completion detection
        running_first_observed = False
        if self.state.state == "RUNNING" and current_file:
            if not self._was_running:
                logger.debug("[%s] Now tracking RUNNING state for %s", self.serial_number, current_file)
                # Check if timelapse was enabled in the same message (xcam parsed before this)
                if self.state.timelapse:
                    self._timelapse_during_print = True
                    logger.debug("[%s] Timelapse detected when entering RUNNING state", self.serial_number)
                # Mark this as the first RUNNING observation of the session.
                # If is_new_print also fires below, on_print_start handles
                # baseline capture and we suppress on_print_running_observed
                # to avoid double-capture. If is_new_print does NOT fire
                # (Bambuddy started mid-print — the #1304 guard suppressed
                # it), main.py needs this hook to catch the restart-recovery
                # case (#1485 follow-up).
                running_first_observed = True
            self._was_running = True
            self._completion_triggered = False

        if is_new_print or is_file_change:
            # Clear any old HMS errors when a new print starts
            self.state.hms_errors = []
            # Reset layer tracking for new print (needed for layer-based timelapse)
            self.state.layer_num = 0
            # Reset total_layers so the previous print's value can't bleed into
            # this print's usage-tracker split (#1771 follow-on to the
            # preservation guard at the `total_layer_num` parse above — that
            # guard ignores firmware-reset 0s, so the explicit reset has to
            # happen here instead).
            #
            # #2702: reset to *this frame's* total, not to 0. The frame that
            # trips the new-print detection can carry the new print's
            # `total_layer_num` as well — the parse above has already applied
            # it, and zeroing unconditionally threw it away. That looked
            # harmless but is not recoverable: Bambu firmware sends only
            # changed fields, so the printer never offers the total again, and
            # the print runs to completion at `n/0` in the UI, in
            # `{total_layers}` notifications, and as the usage-split
            # denominator. The value only reappears on the next full pushall
            # (reconnect / Force Refresh), which is why the symptom looked
            # random and why a *stable* connection made it worse.
            self.state.total_layers = total_from_this_frame
            # If the starting frame brought no total, ask for one. Costs one
            # MQTT message per print and covers the ordering where the printer
            # published the total a frame or two before the state flip.
            self._total_layers_refresh_armed = not total_from_this_frame
            if self._total_layers_refresh_armed:
                self._request_push_all()
            # Reset completion tracking for new print
            self._was_running = True
            self._completion_triggered = False
            # #1721: rearm the end-of-print finish-photo trigger for the new print
            self._finish_photo_captured = False
            # #2547: rearm the end-of-print telemetry probe for the new print
            self._eop_probe_armed = True
            self._eop_probe_open = False
            self._eop_probe_frames = 0
            self._eop_probe_last = {}
            # Reset last valid progress/layer for usage tracking
            self._last_valid_progress = 0.0
            self._last_valid_layer_num = 0
            # Clear and seed tray change log for mid-print usage splitting
            self.state.tray_change_log.clear()
            tn = self.state.tray_now
            if (
                (0 <= tn <= 15)
                or (A2L_LITE_GLOBAL_BASE <= tn <= A2L_LITE_GLOBAL_BASE + 3)
                or (128 <= tn <= 135)
                or tn == 254
            ):
                self.state.tray_change_log.append((tn, 0))
            # Initialize timelapse tracking based on current state
            # NOTE: xcam data is parsed BEFORE this code runs in _process_message,
            # so self.state.timelapse may already be set from this message.
            # We preserve that value instead of blindly resetting to False.
            if self.state.timelapse:
                self._timelapse_during_print = True
                logger.debug("[%s] Timelapse detected at print start", self.serial_number)
            else:
                self._timelapse_during_print = False

        if (is_new_print or is_file_change) and self.on_print_start:
            logger.info(
                f"[{self.serial_number}] PRINT START detected - file: {current_file}, "
                f"subtask: {self.state.subtask_name}, is_new: {is_new_print}, is_file_change: {is_file_change}"
            )
            self.on_print_start(
                {
                    "filename": current_file,
                    "subtask_name": self.state.subtask_name,
                    "remaining_time": self.state.remaining_time * 60
                    if self.state.remaining_time > 0
                    else None,  # Convert minutes to seconds
                    "raw_data": data,
                    "ams_mapping": self._captured_ams_mapping,
                }
            )
        elif running_first_observed and self.on_print_running_observed:
            # Restart-recovery hook (#1485 follow-up): Bambuddy started mid-
            # print, so the #1304 first-push guard suppressed on_print_start,
            # but we still need main.py to capture a fresh timelapse baseline
            # before the printer uploads the in-flight MP4. Same payload
            # shape as on_print_start so the consumer can reuse fields.
            logger.info(
                f"[{self.serial_number}] RUNNING observed without PRINT START "
                f"(restart-recovery) - file: {current_file}, subtask: {self.state.subtask_name}"
            )
            self.on_print_running_observed(
                {
                    "filename": current_file,
                    "subtask_name": self.state.subtask_name,
                    "remaining_time": self.state.remaining_time * 60 if self.state.remaining_time > 0 else None,
                    "raw_data": data,
                    "ams_mapping": self._captured_ams_mapping,
                }
            )

        # Detect print completion (FINISH = success, FAILED = error, IDLE = aborted)
        # Use _was_running flag in addition to _previous_gcode_state for more robust detection
        # This handles cases where server restarts during a print
        should_trigger_completion = (
            self.state.state in ("FINISH", "FAILED")
            and not self._completion_triggered
            and self.on_print_complete
            and (
                self._previous_gcode_state == "RUNNING"  # Normal transition
                or (self._was_running and self._previous_gcode_state != self.state.state)  # After server restart
                # Pre-print failure (#1111): printer rejected the job during setup
                # — wrong nozzle size, AMS error, etc. The print never reaches
                # RUNNING, so without this branch neither the RUNNING check nor
                # _was_running match and the queue item stays stuck at "printing".
                # Restricted to FAILED from pre-print states so a stale FAILED on
                # first connection (prev=None) still can't accidentally fire.
                or (self.state.state == "FAILED" and self._previous_gcode_state in ("PREPARE", "SLICING"))
            )
        )
        # For IDLE, only trigger if we just came from RUNNING (explicit abort/cancel)
        if (
            self.state.state == "IDLE"
            and self._previous_gcode_state == "RUNNING"
            and not self._completion_triggered
            and self.on_print_complete
        ):
            should_trigger_completion = True

        # Log when we FIRST see a terminal state but DON'T trigger completion (diagnostics)
        # Only log on the transition (prev != current) to avoid flooding logs every MQTT update
        if (
            not should_trigger_completion
            and self.state.state in ("FINISH", "FAILED")
            and self._previous_gcode_state != self.state.state
        ):
            logger.info(
                f"[{self.serial_number}] State is {self.state.state} but completion NOT triggered: "
                f"prev={self._previous_gcode_state}, was_running={self._was_running}, "
                f"already_triggered={self._completion_triggered}, has_callback={bool(self.on_print_complete)}"
            )
            # Mark as triggered so state is clean for the next print cycle
            self._completion_triggered = True

        if should_trigger_completion:
            if self.state.state == "FINISH":
                status = "completed"
            elif self.state.state == "FAILED":
                status = "failed"
            else:
                status = "aborted"
            logger.info(
                f"[{self.serial_number}] PRINT COMPLETE detected - state: {self.state.state}, "
                f"status: {status}, file: {self._previous_gcode_file or current_file}, "
                f"subtask: {self.state.subtask_name}, was_running: {self._was_running}, "
                f"timelapse_during_print: {self._timelapse_during_print}"
            )
            timelapse_was_active = self._timelapse_during_print
            # #1721 fallback: if the stage-22 trigger never fired (cancel,
            # external-spool-only, HMS halt, or firmware variant that skips
            # the unload phase) fire the finish-photo moment now. Bed has
            # already dropped, framing is worse, but we still capture.
            # Only on successful completion — aborted/failed prints don't
            # produce a meaningful finish photo.
            if status == "completed" and not self._finish_photo_captured and self.on_finish_photo_moment:
                self._finish_photo_captured = True
                logger.info(
                    f"[{self.serial_number}] FINISH PHOTO MOMENT (FINISH fallback) — "
                    f"stage-22 never fired; capturing at FINISH-state transition"
                )
                self.on_finish_photo_moment(
                    {
                        "trigger": "finish_state",
                        "filename": self._previous_gcode_file or current_file,
                        "subtask_name": self.state.subtask_name,
                        "timelapse_was_active": timelapse_was_active,
                    }
                )
            self._completion_triggered = True
            self._was_running = False
            self._timelapse_during_print = False  # Reset for next print
            # Include HMS errors for failure reason detection
            hms_errors_data = (
                [
                    {
                        "code": e.code,
                        "attr": e.attr,
                        "module": e.module,
                        "severity": e.severity,
                        # Carried so the queue's failure reason quotes the same
                        # sentence the status response and the broadcast do,
                        # rather than resolving the code a fourth time (#2926).
                        "description": e.description,
                    }
                    for e in self.state.hms_errors
                ]
                if self.state.hms_errors
                else []
            )
            self.on_print_complete(
                {
                    "status": status,
                    "filename": self._previous_gcode_file or current_file,
                    "subtask_name": self.state.subtask_name,
                    "raw_data": data,
                    "timelapse_was_active": timelapse_was_active,
                    "hms_errors": hms_errors_data,
                    "ams_mapping": self._captured_ams_mapping,
                    # Last valid progress/layer before firmware reset (for partial usage tracking)
                    "last_progress": self._last_valid_progress,
                    "last_layer_num": self._last_valid_layer_num,
                }
            )
            self._captured_ams_mapping = None
            # Same lifecycle as the mapping above: it described *this* print.
            # Leaving it set would hand the next print an answer about where a
            # different file went, and a stale "internal storage" reading costs
            # an archive that the FTPS sweep would have found (#2780).
            self.state.current_project_url = None

        self._previous_gcode_state = self.state.state
        if current_file:
            self._previous_gcode_file = current_file

        if self.on_state_change:
            self.on_state_change(self.state)

    def _request_push_all(self):
        """Request full status update from printer."""
        if self._client:
            message = {"pushing": {"command": "pushall"}}
            self._client.publish(self.topic_publish, json.dumps(message), qos=1)

    def _probe_developer_mode(self):
        """Probe developer mode by sending an ams_filament_setting for the external slot.

        Some printers (A1/P1 series) never send the "fun" field in MQTT status.
        For these, we detect developer mode by sending a harmless command and
        checking whether the printer accepts or rejects it:
        - result="success" → developer mode ON (commands accepted)
        - result="failed", reason="mqtt message verify failed" → developer mode OFF

        The probe re-sends the current external slot configuration so it's a no-op
        when the command succeeds. If there's no external slot data yet, we send a
        reset (empty filament) which is also safe.
        """
        if not self._client or not self.state.connected:
            return
        self._dev_mode_probed = True
        self._dev_mode_probe_time = time.monotonic()
        self._sequence_id += 1
        seq = str(self._sequence_id)
        self._dev_mode_probe_seq = seq

        # Build probe command: re-send current external slot config (no-op on success)
        vt_tray = self.state.raw_data.get("vt_tray", []) if self.state.raw_data else []
        current = vt_tray[0] if vt_tray else {}

        command = {
            "print": {
                "command": "ams_filament_setting",
                "ams_id": 255,
                "tray_id": 0,
                "slot_id": 0,
                "tray_info_idx": current.get("tray_info_idx", ""),
                "tray_type": current.get("tray_type", ""),
                "tray_sub_brands": current.get("tray_sub_brands", ""),
                "tray_color": current.get("tray_color", "00000000"),
                "nozzle_temp_min": current.get("nozzle_temp_min", 0),
                "nozzle_temp_max": current.get("nozzle_temp_max", 0),
                "sequence_id": seq,
            }
        }
        setting_id = current.get("setting_id")
        if setting_id:
            command["print"]["setting_id"] = setting_id

        logger.info("[%s] Probing developer mode via ams_filament_setting (seq=%s)", self.serial_number, seq)
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)

    def _apply_mqtt_verify_state(self, verify_failed: bool) -> None:
        """Reconcile developer_mode with the printer's own command-verification verdict.

        ``HMS_MQTT_VERIFY_FAILED`` is the only *direct* evidence we ever get that
        control commands are being refused, so it outranks the probe in both
        directions:

        * present  → developer_mode is definitively False, whatever the probe
          concluded. The probe can only read the response to its own
          ``ams_filament_setting``; on P1 firmware a refusal is reported here
          instead, so the probe answers ENABLED while every print silently dies
          (#2732).
        * gone again → drop the HMS-derived False back to unknown and re-arm the
          probe, so a user who enables Developer Mode and restarts the printer
          isn't stuck behind a verdict nothing would ever revisit.

        A False that came from the probe or the ``fun`` bit is left alone — this
        only ever unwinds its own latch.
        """
        if verify_failed:
            if not self._dev_mode_from_hms:
                logger.warning(
                    "[%s] Printer reported HMS %s (MQTT command verification failed): it is "
                    "rejecting control commands, so prints, temperature changes and filament "
                    "loads will be ignored. Enable Developer Mode on the printer and restart it.",
                    self.serial_number,
                    HMS_MQTT_VERIFY_FAILED,
                )
            self._dev_mode_from_hms = True
            self.state.developer_mode = False
            return

        if not self._dev_mode_from_hms:
            return
        logger.info(
            "[%s] HMS %s cleared — re-probing developer mode",
            self.serial_number,
            HMS_MQTT_VERIFY_FAILED,
        )
        self._dev_mode_from_hms = False
        self.state.developer_mode = None
        self._dev_mode_probed = False
        self._dev_mode_needs_probe = False

    def _handle_dev_mode_probe_response(self, data: dict):
        """Handle response to the developer mode probe command.

        Sets developer_mode based on whether the printer accepted or rejected the command.

        Three outcomes, not two. An explicit ``success`` proves commands are
        accepted and an explicit verify-failure proves they are not, but anything
        else proves nothing — P1S firmware 01.10.00.00 answers this probe with a
        bare ``{"command": "ams_filament_setting", "sequence_id": "3"}`` and no
        ``result`` at all, while refusing every control command and reporting
        ``HMS_MQTT_VERIFY_FAILED`` instead. Reading that empty response as ENABLED
        is what put ``developer_mode: pass`` in the support bundle of a printer
        that had not accepted a command all day (#2732). Leaving it unknown makes
        the connection diagnostic report ``skip``, which is the honest answer.
        """
        self._dev_mode_probe_seq = None  # One-shot: don't match future responses
        self._dev_mode_probe_failures = 0  # Reset on any response
        result = data.get("result", "")
        reason = data.get("reason", "")

        if result == "failed" and "verify failed" in reason:
            self.state.developer_mode = False
            logger.info("[%s] Developer mode probe: DISABLED (reason=%r)", self.serial_number, reason)
        elif str(result).lower() == "success":
            self.state.developer_mode = True
            logger.info("[%s] Developer mode probe: ENABLED (result=%r)", self.serial_number, result)
        else:
            # An HMS verdict already recorded here is real evidence; don't let an
            # inconclusive probe response wipe it back to unknown.
            if not self._dev_mode_from_hms:
                self.state.developer_mode = None
            logger.info(
                "[%s] Developer mode probe: INCONCLUSIVE (result=%r, reason=%r) — "
                "the printer neither confirmed nor refused the command",
                self.serial_number,
                result,
                reason,
            )

        if self.on_state_change:
            self.on_state_change(self.state)

    def _request_version(self):
        """Request firmware version info from printer."""
        if self._client:
            self._sequence_id += 1
            message = {
                "info": {
                    "sequence_id": str(self._sequence_id),
                    "command": "get_version",
                }
            }
            logger.debug("[%s] Requesting firmware version info", self.serial_number)
            self._client.publish(self.topic_publish, json.dumps(message), qos=1)

    def request_status_update(self) -> bool:
        """Request a full status update from the printer (public API).

        Sends both pushall and get_accessories commands to refresh all data
        including nozzle hardware info.

        Returns:
            True if the request was sent, False if not connected.
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] request_status_update: not connected", self.serial_number)
            return False
        logger.debug("[%s] Requesting status update (pushall)", self.serial_number)
        self._request_push_all()
        # Note: get_accessories returns stale nozzle data on H2D.
        # The correct nozzle data comes from push_status response.
        return True

    def _request_accessories(self):
        """Request accessories info (nozzle type, etc.) from printer."""
        if self._client:
            self._sequence_id += 1
            message = {
                "system": {
                    "sequence_id": str(self._sequence_id),
                    "command": "get_accessories",
                    "accessory_type": "none",
                }
            }
            logger.debug("[%s] Requesting accessories info", self.serial_number)
            self._client.publish(self.topic_publish, json.dumps(message), qos=1)

    def _prime_kprofile_request(self):
        """Send a priming K-profile request on connect.

        Bambu printers often ignore the first K-profile request after connection,
        so we send a dummy request on connect to 'prime' the system.
        """
        if self._client:
            self._sequence_id += 1
            command = {
                "print": {
                    "command": "extrusion_cali_get",
                    "filament_id": "",
                    "nozzle_diameter": "0.4",
                    "sequence_id": str(self._sequence_id),
                }
            }
            logger.debug("[%s] Sending K-profile priming request", self.serial_number)
            self._client.publish(self.topic_publish, json.dumps(command), qos=1)

    def connect(self, loop: asyncio.AbstractEventLoop | None = None):
        """Connect to the printer MQTT broker.

        Args:
            loop: The asyncio event loop to use for thread-safe callbacks.
                  If not provided, will try to get the running loop.
        """
        self._loop = loop
        BambuMQTTClient._client_instance_counter += 1
        client_id = f"bambuddy_{self.serial_number}_{os.getpid()}_{BambuMQTTClient._client_instance_counter}"
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )

        # Bambu's broker has racy PUBACK matching with paho's QoS=1 inflight
        # tracking (#1164). The default ceiling of 20 wedges sessions after
        # ~16-20 cumulative commands; lifting it well above any realistic
        # session count keeps QoS=1 working without changing wire-protocol
        # behaviour across printer models.
        self._client.max_inflight_messages_set(1000)

        self._client.username_pw_set("bblp", self.access_code)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_subscribe = self._on_subscribe
        self._client.on_message = self._on_message

        # TLS setup - Bambu uses self-signed certs
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        # Same reasoning as ImplicitFTP_TLS in bambu_ftp.py: create_default_context()
        # inherits its protocol floor from the OpenSSL build instead of declaring one.
        # Every Bambu broker measured (X1C, H2D on :8883) speaks TLS 1.2 and refuses
        # 1.0/1.1/1.3, so this floor is a no-op on the wire and closes the gap on
        # bare-metal installs whose build allows TLS 1.0.
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._client.tls_set_context(ssl_context)

        # Backoff reconnects to avoid tight reconnect loops on unstable brokers.
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

        # Keepalive: paho sends PINGREQs at this interval, broker considers
        # client dead at 1.5x.  30s is a good balance — fast enough to detect
        # real network loss (45s), not so aggressive that transient hiccups
        # trigger false disconnects.  Stale detection (60s no messages) handles
        # the P1S/P1P firmware bug where the broker stops publishing but the
        # TCP connection stays alive.
        self._client.connect_async(self.ip_address, self.MQTT_PORT, keepalive=30)
        self._client.loop_start()

    def start_print(
        self,
        filename: str,
        plate_id: int = 1,
        ams_mapping: list[int] | None = None,
        bed_levelling: str = "auto",
        flow_cali: str = "auto",
        vibration_cali: bool = True,
        layer_inspect: bool = False,
        timelapse: bool = False,
        use_ams: bool = True,
        nozzle_offset_cali: str = "auto",
        nozzle_mapping: str | None = None,
        nozzle_slot_extruders: str | None = None,
    ):
        """Start a print job on the printer.

        The file should already be uploaded to the printer's root directory via FTP.

        Args:
            filename: Name of the uploaded file
            plate_id: Plate number to print (default 1)
            ams_mapping: List of tray IDs for each filament slot in the 3MF.
                         Global tray ID = (ams_id * 4) + slot_id, external = 254
            timelapse: Record timelapse video
            bed_levelling: Bed levelling — tri-state "off"/"on"/"auto" (auto skips
                if the bed was levelled recently, matching BambuStudio).
            flow_cali: Flow/pressure advance calibration — "off"/"on"/"auto".
            vibration_cali: Vibration compensation calibration
            layer_inspect: First layer AI inspection
            use_ams: Use AMS for automatic filament changes
            nozzle_offset_cali: Nozzle offset calibration — "off"/"on"/"auto"
                (dual-nozzle printers only — silently ignored on single-nozzle).
            nozzle_mapping: Opaque JSON string captured from BambuStudio's
                project_file for H2C rack-swap (O1C2) (#1780). When non-null
                AND the printer is dual-nozzle, parsed and injected as the
                `nozzle_mapping` array on the dispatched project_file so the
                firmware honours the user's slicer pick instead of falling
                back to "last matching nozzle" auto-pick. Silently ignored
                on single-nozzle printers.
            nozzle_slot_extruders: Opaque JSON string of per-filament-slot
                MQTT extruder indices, derived from the 3MF when no
                BambuStudio capture exists (#2800). Consulted only on
                nozzle-rack models (H2C) and only when `nozzle_mapping` did
                not already supply one; resolved here into physical rack
                positions using the live `device.nozzle` state. When it
                cannot be resolved the field is omitted and the firmware
                picks, as it did before this existed.

        Returns True when the start command was published, False otherwise
        (not connected, or the printer is already busy — see the run-state
        guard below).
        """
        # Never dispatch project_file to a printer that is not idle (#2598).
        # This is the single publish choke point for every dispatch path — the
        # queue scheduler, a manual start, a webhook, and a Virtual-Printer
        # forwarded job all funnel through here — so one guard covers them all.
        # The firmware rejects a start while busy with 0500_4004 ("Device is
        # busy and cannot start a new task"), and on an A1 mini that error
        # cancels the RUNNING job (#2598). IDLE / FINISH / FAILED are valid
        # start targets; only the active-print states are refused. (A
        # transport-level QoS-1 replay on reconnect would bypass this guard,
        # but the dispatch/watchdog reconnect path hard-resets the client with a
        # fresh client_id, so paho has no inflight project_file to replay there.)
        if self.state.state in _ACTIVE_PRINT_STATES:
            logger.warning(
                "[%s] start_print refused: printer busy (gcode_state=%s) — not publishing project_file for %s",
                self.serial_number,
                self.state.state,
                filename,
            )
            return False

        if self._client and self.state.connected:
            # Bambu print command format — matches Bambu Studio's format.
            # The calibration/leveling fields (timelapse, bed_leveling,
            # flow_cali, vibration_cali, layer_inspect) are JSON booleans for
            # every model. An earlier revision integer-encoded them for the H2
            # family (H2D/H2S/H2C/X2D) on the belief that H2 firmware required
            # 0/1 — but a BambuStudio request-topic capture from a real H2D
            # sends plain booleans, and the integer encoding made the H2S
            # silently skip flow-dynamics calibration (#1478). use_ams is the
            # one field that genuinely must stay boolean: H2D Pro firmware
            # reads an integer use_ams as a nozzle index (1 = deputy), which is
            # what actually caused the wrong-extruder routing behind #1386.
            # Dual-nozzle routing for external spool (254 = deputy/left,
            # 255 = main/right) and the use_ams=False fallback. H2S is in the
            # H2 firmware family but is single-nozzle, despite sharing serial
            # prefix "094" with H2D. Prefer runtime detection from
            # device.extruder.info (set in _handle_push_status); fall back to
            # model name for the brief window after connect before push data
            # arrives. _is_dual_nozzle only ever flips False→True, so it's safe
            # as the primary signal.
            from backend.app.utils.printer_models import is_dual_nozzle_model, is_nozzle_rack_model

            is_dual_nozzle = self._is_dual_nozzle or is_dual_nozzle_model(self.model)

            # Build ams_mapping2 from ams_mapping (detailed format with ams_id/slot_id)
            ams_mapping2 = []
            # BambuStudio converts virtual tray IDs (254/255) to -1 in the flat
            # ams_mapping and relies on ams_mapping2 for external spool details.
            # Passing raw 254/255 in the flat array causes H2D firmware to fail
            # with 0700_8012 "Failed to get AMS mapping table".
            flat_ams_mapping = []
            if ams_mapping is not None:
                for tray_id in ams_mapping:
                    # Ensure tray_id is an integer (may be string from JSON)
                    tray_id = int(tray_id) if tray_id is not None else -1
                    if tray_id == -1:
                        # Unmapped filament slot
                        flat_ams_mapping.append(-1)
                        ams_mapping2.append({"ams_id": 255, "slot_id": 255})
                    elif tray_id >= 254:
                        # External/virtual spool. BambuStudio convention:
                        #   255 = VIRTUAL_TRAY_MAIN_ID (main/right nozzle)
                        #   254 = VIRTUAL_TRAY_DEPUTY_ID (deputy/left nozzle)
                        # Flat mapping must use -1 (firmware doesn't accept raw 254/255).
                        # Single-nozzle printers (X1C, P1S, A1, etc.) report tray_now=254
                        # for external spool, but BambuStudio always sends ams_id=255
                        # (VIRTUAL_TRAY_MAIN_ID) in ams_mapping2. Sending 254 causes the
                        # firmware to target AMS tray 0 instead of external spool, leading
                        # to 07FF_8012 "Failed to get AMS mapping table" or stuck prints.
                        # Only H2D dual-nozzle printers use 254 (deputy/left nozzle).
                        flat_ams_mapping.append(-1)
                        ext_ams_id = tray_id if is_dual_nozzle else 255
                        ams_mapping2.append({"ams_id": ext_ams_id, "slot_id": 0})
                    elif tray_id >= 128:
                        # AMS-HT: global tray ID IS the ams_id (single tray per unit)
                        flat_ams_mapping.append(tray_id)
                        ams_mapping2.append({"ams_id": tray_id, "slot_id": 0})
                    elif (_a2l := a2l_lite_wire_ids(tray_id // 4, tray_id)) is not None:
                        # A2L AMS-Lite (normalised global 24-27): flat mapping is the
                        # LOCAL slot 0-3 and ams_mapping2 carries {ams_id:16, slot_id:0-3}
                        # — both CONFIRMED against the firmware's own mapping
                        # (flat [1], ams_mapping2 {ams_id:16, slot_id:1}).
                        _wire_ams, _wire_slot, _ = _a2l
                        flat_ams_mapping.append(_wire_slot)
                        ams_mapping2.append({"ams_id": _wire_ams, "slot_id": _wire_slot})
                    else:
                        # Regular AMS tray: Global tray ID = (ams_id * 4) + slot_id
                        ams_id = tray_id // 4
                        slot_id = tray_id % 4
                        flat_ams_mapping.append(tray_id)
                        ams_mapping2.append({"ams_id": ams_id, "slot_id": slot_id})

            # Reconcile use_ams against the resolved ams_mapping for single-nozzle
            # printers — the mapping is authoritative about whether this print
            # actually feeds from the AMS. Skip for dual-nozzle printers, where
            # use_ams encodes nozzle routing rather than an AMS on/off flag.
            # H2S falls through here now (#1386): it is single-nozzle and was
            # hitting the dual-nozzle bypass, which caused 07FF_8012 when printing
            # without an AMS attached.
            #
            # Two symmetric corrections:
            #
            # (a) A mapping that resolves a *real* AMS tray (0-253) forces
            #     use_ams=True even if it arrived False. A print sent to a Virtual
            #     Printer is sliced against the VP, which advertises no AMS, so the
            #     slicer sends use_ams=false and that gets stamped on the queue item
            #     — but at dispatch the scheduler colour-matches a real printer and
            #     resolves a real AMS slot. Without this, the stale False reaches the
            #     printer, which ignores the mapped slot and aborts at layer 0 on the
            #     empty external spool ("not enough filament"). Diagnosed by
            #     @Sawtaytoes (#2595, PR #2596).
            #
            # (b) Only an *explicit* external/virtual spool (254/255) may downgrade
            #     to use_ams=False. P1S/P1P with no AMS rejects use_ams=True with
            #     "Failed to get AMS mapping table". An unresolved slot (-1) does
            #     NEITHER: it means the mapping was never resolved — e.g. a frontend
            #     status-load race that persisted [-1] (#2589) — and treating it as
            #     external silently started the print against an empty feed. A genuine
            #     external selection is >=254; unresolved is -1; a loaded tray is
            #     0-253. Keeping them distinct means an unresolved mapping fails loudly
            #     (or is recomputed upstream) instead of silently going external, and
            #     never gets force-enabled by (a) either.
            if ams_mapping and not is_dual_nozzle:
                has_real_tray = any(t is not None and 0 <= int(t) <= 253 for t in ams_mapping)
                all_external = all(t is None or int(t) >= 254 for t in ams_mapping)
                if has_real_tray and not use_ams:
                    use_ams = True
                    logger.info(
                        "[%s] AMS mapping resolved a real slot — setting use_ams=True (#2595)",
                        self.serial_number,
                    )
                elif use_ams and all_external:
                    use_ams = False
                    logger.info(
                        "[%s] All filament slots use external spool — setting use_ams=False",
                        self.serial_number,
                    )

            # Unique per-submission identity fields. Hardcoded "0" values caused
            # third-party MQTT observers (OctoEverywhere, etc.) to see reprints as
            # continuations of the same job: the printer reuses gcode_start_time
            # from the prior print with task_id=0, so observers latch onto a stale
            # timestamp and report compounding durations on repeat replays (#1011).
            # BambuStudio mints fresh IDs per submission; matching that behavior
            # makes the printer emit a clean state-transition for each job.
            # md5 is left empty — firmware historically accepts "" as "skip
            # validation" (unlike Studio, we don't have the file's real md5 here
            # without re-reading the upload, and sending a synthetic wrong digest
            # risks activation of md5 verification on some firmwares).
            # Cap at signed int32 max: P1S firmware (01.10.00.00) clamps oversized
            # task identity fields to 2**31-1, so raw epoch-ms (13 digits, ~1.7e12)
            # overflows and every submission ends up with the same task_id from
            # the printer's perspective — the printer then treats a fresh dispatch
            # as a continuation of the last FAILED job and never leaves IDLE (#1042).
            # Modulo keeps uniqueness within a ~24-day wrap window; `or 1` guards
            # the (astronomically unlikely) zero case since task_id=0 is rejected.
            submission_id = str(int(time.time() * 1000) % 2_147_483_647 or 1)
            # Remember it so on_print_start can persist a restart-stable id on
            # the archive even before the printer echoes subtask_id back (#1485).
            self.last_dispatch_subtask_id = submission_id

            # Tri-state calibration options → BambuStudio's getValueInt encoding:
            # off=0 (never), on=1 (force every print), auto=2 (printer runs it
            # only if it wasn't done recently). The paired bool field is true
            # only for the explicit "on" state — for "auto" the bool is false and
            # the int carries the intent, exactly as BambuStudio's SelectMachine
            # sends it. Unknown values fall back to auto.
            _tristate_wire = {"off": 0, "on": 1, "auto": 2}
            bed_level_int = _tristate_wire.get(bed_levelling, 2)
            flow_cali_int = _tristate_wire.get(flow_cali, 2)
            nozzle_cali_int = _tristate_wire.get(nozzle_offset_cali, 2)

            command = {
                "print": {
                    "sequence_id": "20000",
                    "command": "project_file",
                    "param": f"Metadata/plate_{plate_id}.gcode",
                    "url": f"ftp://{filename}",
                    "file": filename,
                    "md5": "",
                    "bed_type": "auto",
                    "timelapse": timelapse,
                    # bed_leveling stays a JSON bool (true only for "on") and
                    # auto_bed_leveling carries the tri-state int — the exact
                    # two-field shape BambuStudio sends. The int must stay a plain
                    # number, never quoted (#1478 boolean-family concern applies to
                    # the *_cali bools, not these companion ints).
                    "bed_leveling": bed_levelling == "on",
                    "auto_bed_leveling": bed_level_int,
                    "flow_cali": flow_cali == "on",
                    "vibration_cali": vibration_cali,
                    "layer_inspect": layer_inspect,
                    "use_ams": use_ams,
                    "cfg": "0",
                    # extrude_cali_flag gates flow-dynamics calibration:
                    # 0 = never, 1 = force every print, 2 = auto (run only if the
                    # filament wasn't calibrated recently). #1721 saw stage 8
                    # ("Calibrating dynamic flow") still queued when we send 2 —
                    # that is exactly the auto contract (the printer queues the
                    # stage and skips it at runtime if recent), not a bug, so 2 is
                    # the right wire value for "auto". off/on remain 0/1.
                    "extrude_cali_flag": flow_cali_int,
                    "extrude_cali_manual_mode": 0,
                    # 0 = never, 1 = force, 2 = auto (skip if recent). #1721 saw
                    # stage 39 ("Nozzle offset calibration") still queued on 2 —
                    # again the auto contract, not a failure to suppress.
                    # BambuStudio exposes the toggle only for dual-nozzle
                    # (H2D/H2D Pro/H2C/X2D); single-nozzle prints resolve to 0 so
                    # firmware never runs a calibration the head doesn't support.
                    "nozzle_offset_cali": nozzle_cali_int if is_dual_nozzle else 0,
                    "subtask_name": filename.replace(".3mf", "").replace(".gcode", ""),
                    "profile_id": "0",
                    "project_id": submission_id,
                    "subtask_id": submission_id,
                    "task_id": submission_id,
                }
            }

            # P2S-specific parameter adjustments
            # P2S printer doesn't support vibration calibration like X1/P1 series
            if self.model and self.model.upper().strip() in ("P2S", "N7"):
                command["print"]["vibration_cali"] = False
                logger.debug("[%s] P2S detected: disabling vibration_cali", self.serial_number)

            # Add AMS mapping if provided
            if ams_mapping is not None:
                command["print"]["ams_mapping"] = flat_ams_mapping
                command["print"]["ams_mapping2"] = ams_mapping2

            # H2C dual-nozzle-rack slicer-pick preservation (#1780).
            # `nozzle_mapping` carries per-filament physical nozzle position
            # IDs (`list[int]`), JSON-string-encoded when it leaves the queue
            # item; parse here so the wire ships an array, matching
            # BambuStudio's project_file shape. Gate by `is_dual_nozzle`
            # defensively — single-nozzle firmwares would ignore the field
            # but we err on the side of not emitting unrecognised fields. A
            # parse failure is logged but never blocks the dispatch — the
            # firmware will fall back to its auto-pick path, which is the
            # pre-fix behaviour.
            if is_dual_nozzle and nozzle_mapping:
                try:
                    command["print"]["nozzle_mapping"] = json.loads(nozzle_mapping)
                except json.JSONDecodeError:
                    logger.warning(
                        "[%s] Invalid nozzle_mapping JSON on dispatch, omitting from "
                        "project_file (firmware will auto-pick): %r",
                        self.serial_number,
                        nozzle_mapping,
                    )

            # Nozzle-rack fallback (#2800). Only consulted when BambuStudio
            # never saw the job, so it can never override a real capture. The
            # queue stores extruder indices per filament slot; the physical
            # rack position they resolve to is only knowable here, because the
            # mounted hotend can change between queueing and dispatch.
            if is_nozzle_rack_model(self.model) and nozzle_slot_extruders and "nozzle_mapping" not in command["print"]:
                try:
                    slot_extruders = json.loads(nozzle_slot_extruders)
                except (json.JSONDecodeError, TypeError):
                    # TypeError covers a caller handing us the list itself
                    # rather than its JSON — the field is opaque by contract,
                    # and a print must not die over the difference.
                    slot_extruders = None
                    logger.warning(
                        "[%s] Invalid nozzle_slot_extruders JSON on dispatch, "
                        "omitting nozzle_mapping (firmware will auto-pick): %r",
                        self.serial_number,
                        nozzle_slot_extruders,
                    )

                if isinstance(slot_extruders, list):
                    rack_nozzle_id = (
                        self.state.nozzle_rack_tar_id
                        if self.state.nozzle_rack_tar_id in _RACK_NOZZLE_IDS
                        else self.state.nozzle_rack_src_id
                    )
                    resolved = resolve_rack_nozzle_mapping(slot_extruders, rack_nozzle_id)
                    if resolved is None:
                        logger.info(
                            "[%s] Nozzle rack slots %s not resolvable (tar_id=%s src_id=%s); "
                            "omitting nozzle_mapping so the firmware picks",
                            self.serial_number,
                            slot_extruders,
                            self.state.nozzle_rack_tar_id,
                            self.state.nozzle_rack_src_id,
                        )
                    else:
                        logger.info(
                            "[%s] Nozzle rack mapping: slots=%s rack_id=%s -> %s",
                            self.serial_number,
                            slot_extruders,
                            rack_nozzle_id,
                            resolved,
                        )
                        command["print"]["nozzle_mapping"] = resolved

            logger.info("[%s] Sending print command: %s", self.serial_number, json.dumps(command))
            # Remember this dispatch so its echo on the topic is recognised as
            # ours rather than logged as a slicer's.
            self._own_project_file_key = self._project_file_key(command["print"])
            self._client.publish(self.topic_publish, json.dumps(command), qos=1)
            # Record what we dispatched so /cover can pick the right plate
            # thumbnail even when the printer's gcode_file echo is just the
            # 3MF filename without a plate path (#1166). Match the same
            # subtask_name shape we send so the comparison in the cover route
            # works against state.subtask_name reflected back via MQTT.
            self.state.dispatched_plate_id = plate_id
            self.state.dispatched_subtask = command["print"]["subtask_name"]
            return True
        else:
            # Log why we couldn't send the command
            if not self._client:
                logger.error("[%s] Cannot start print: MQTT client not initialized", self.serial_number)
            elif not self.state.connected:
                logger.error(
                    f"[{self.serial_number}] Cannot start print: Printer not connected (client exists but disconnected). "
                    f"Connection state: {self.state.connected}, Last message: {self._last_message_time}"
                )
            return False

    def stop_print(self) -> bool:
        """Stop the current print job."""
        if self._client and self.state.connected:
            command = {"print": {"command": "stop", "sequence_id": "0"}}
            self._client.publish(self.topic_publish, json.dumps(command), qos=1)
            logger.info("[%s] Sent stop print command", self.serial_number)
            return True
        return False

    def set_xcam_option(
        self, module_name: str, enabled: bool, print_halt: bool = True, sensitivity: str = "medium"
    ) -> bool:
        """Set an xcam (AI detection) option on the printer.

        Args:
            module_name: The xcam module to control (e.g., "spaghetti_detector",
                        "first_layer_inspector", "printing_monitor", "buildplate_marker_detector")
            enabled: Whether to enable or disable the feature
            print_halt: Whether to halt print on detection (only applies to some detectors)
            sensitivity: Sensitivity level ("low", "medium", "high", or "never_halt")

        Returns:
            True if command was sent, False if not connected
        """
        if not self._client or not self.state.connected:
            return False

        # auto_recovery_step_loss uses a different command format (print.print_option)
        if module_name == "auto_recovery_step_loss":
            return self._set_print_option("auto_recovery", enabled)

        self._sequence_id += 1

        # Build the xcam control command (exact OrcaSlicer format)
        # Key findings from OrcaSlicer source:
        # - Uses "xcam" wrapper (not "print")
        # - print_halt is ALWAYS true (legacy protocol requirement)
        # - Both "control" and "enable" are set to the same value
        # - halt_print_sensitivity controls actual halt behavior
        command = {
            "xcam": {
                "command": "xcam_control_set",
                "sequence_id": str(self._sequence_id),
                "module_name": module_name,
                "control": enabled,
                "enable": enabled,  # old protocol compatibility
                "print_halt": True,  # ALWAYS true per OrcaSlicer
            }
        }

        # Only add sensitivity if not "never_halt"
        # OrcaSlicer uses halt_print_sensitivity for ALL detectors
        # The module_name field determines which detector's sensitivity is being set
        if sensitivity and sensitivity != "never_halt":
            command["xcam"]["halt_print_sensitivity"] = sensitivity

        command_json = json.dumps(command)
        self._client.publish(self.topic_publish, command_json, qos=1)
        logger.debug(
            "[%s] Set xcam option: %s=%s, sensitivity=%s", self.serial_number, module_name, enabled, sensitivity
        )
        logger.debug("[%s] MQTT command sent: %s", self.serial_number, command_json)

        # OrcaSlicer pattern: Set hold timer to ignore incoming data for 3 seconds
        # This prevents stale MQTT data from immediately overwriting our change
        self._xcam_hold_start[module_name] = time.time()

        # Update local state immediately for responsive UI
        # NOTE: Spaghetti and Pileup sensitivities are linked in firmware
        # When spaghetti_detector sensitivity is changed, pileup also changes
        if module_name == "spaghetti_detector":
            self.state.print_options.spaghetti_detector = enabled
            self.state.print_options.print_halt = print_halt
            if sensitivity and sensitivity != "never_halt":
                # spaghetti_detector controls BOTH spaghetti and pileup sensitivities
                self.state.print_options.halt_print_sensitivity = sensitivity
                self.state.print_options.pileup_sensitivity = sensitivity
                self._xcam_hold_start["halt_print_sensitivity"] = time.time()
                self._xcam_hold_start["pileup_sensitivity"] = time.time()
        elif module_name == "first_layer_inspector":
            self.state.print_options.first_layer_inspector = enabled
        elif module_name == "printing_monitor":
            self.state.print_options.printing_monitor = enabled
        elif module_name == "buildplate_marker_detector":
            self.state.print_options.buildplate_marker_detector = enabled
        elif module_name == "allow_skip_parts":
            self.state.print_options.allow_skip_parts = enabled
        elif module_name == "pileup_detector":
            self.state.print_options.pileup_detector = enabled
            # Pileup sensitivity is linked to spaghetti - both are set via spaghetti_detector
        elif module_name == "clump_detector":
            self.state.print_options.nozzle_clumping_detector = enabled
            if sensitivity and sensitivity != "never_halt":
                self.state.print_options.nozzle_clumping_sensitivity = sensitivity
                self._xcam_hold_start["nozzle_clumping_sensitivity"] = time.time()
        elif module_name == "airprint_detector":
            self.state.print_options.airprint_detector = enabled
            if sensitivity and sensitivity != "never_halt":
                self.state.print_options.airprint_sensitivity = sensitivity
                self._xcam_hold_start["airprint_sensitivity"] = time.time()
        elif module_name == "auto_recovery_step_loss":
            self.state.print_options.auto_recovery_step_loss = enabled

        return True

    def _set_print_option(self, option_name: str, enabled: bool) -> bool:
        """Set a print option using the print.print_option command.

        This is different from xcam_control_set and is used for options like:
        - auto_recovery
        - air_print_detect
        - filament_tangle_detect
        - nozzle_blob_detect
        - sound_enable

        Args:
            option_name: The option to control (e.g., "auto_recovery")
            enabled: Whether to enable or disable the option

        Returns:
            True if command was sent, False if not connected
        """
        if not self._client or not self.state.connected:
            return False

        self._sequence_id += 1

        command = {
            "print": {
                "command": "print_option",
                "sequence_id": str(self._sequence_id),
                option_name: enabled,
            }
        }

        command_json = json.dumps(command)
        self._client.publish(self.topic_publish, command_json, qos=1)
        logger.debug("[%s] Set print option: %s=%s", self.serial_number, option_name, enabled)

        # Set hold timer
        hold_key = f"print_option_{option_name}"
        self._xcam_hold_start[hold_key] = time.time()

        # Update local state immediately
        if option_name == "auto_recovery":
            self.state.print_options.auto_recovery_step_loss = enabled
        elif option_name == "auto_switch_filament":
            self.state.ams_filament_backup = enabled

        return True

    def set_ams_filament_backup(self, enabled: bool) -> bool:
        """Toggle AMS Filament Backup (a.k.a. auto-switch / auto-refill).

        Mirrors BambuStudio's "AMS Filament Backup" checkbox. Verified payload
        shape from H2D capture 2026-06-20.
        """
        return self._set_print_option("auto_switch_filament", enabled)

    def start_calibration(
        self,
        bed_leveling: bool = False,
        vibration: bool = False,
        motor_noise: bool = False,
        nozzle_offset: bool = False,
        high_temp_heatbed: bool = False,
    ) -> bool:
        """Start printer calibration with selected options.

        Args:
            bed_leveling: Run bed leveling calibration
            vibration: Run vibration compensation calibration
            motor_noise: Run motor noise cancellation calibration
            nozzle_offset: Run nozzle offset calibration (dual nozzle printers)
            high_temp_heatbed: Run high-temperature heatbed calibration

        Returns:
            True if command was sent, False if not connected
        """
        if not self._client or not self.state.connected:
            return False

        # Build calibration bitmask based on OrcaSlicer DeviceManager.cpp
        # Bit 0: xcam_cali (not exposed in UI)
        # Bit 1: bed_leveling
        # Bit 2: vibration
        # Bit 3: motor_noise
        # Bit 4: nozzle_cali
        # Bit 5: bed_cali (high-temp heatbed)
        # Bit 6: clumppos_cali (not exposed in UI)
        option = 0
        if bed_leveling:
            option |= 1 << 1
        if vibration:
            option |= 1 << 2
        if motor_noise:
            option |= 1 << 3
        if nozzle_offset:
            option |= 1 << 4
        if high_temp_heatbed:
            option |= 1 << 5

        if option == 0:
            logger.warning("[%s] No calibration options selected", self.serial_number)
            return False

        self._sequence_id += 1

        command = {
            "print": {
                "command": "calibration",
                "sequence_id": str(self._sequence_id),
                "option": option,
            }
        }

        command_json = json.dumps(command)
        self._client.publish(self.topic_publish, command_json, qos=1)
        logger.info(
            f"[{self.serial_number}] Starting calibration: "
            f"bed_leveling={bed_leveling}, vibration={vibration}, "
            f"motor_noise={motor_noise}, nozzle_offset={nozzle_offset}, "
            f"high_temp_heatbed={high_temp_heatbed} (option={option})"
        )

        return True

    def disconnect(self, timeout: float = 0):
        """Disconnect from the printer."""
        if self._client:
            self._disconnection_event = threading.Event()
            self._client.disconnect()
            self._disconnection_event.wait(timeout=timeout)
            self._client.loop_stop()
            self._client = None
            self.state.connected = False

    def send_command(self, command: dict):
        """Send a command to the printer."""
        if self._client and self.state.connected:
            # Log outgoing message if logging is enabled
            if self._logging_enabled:
                self._message_log.append(
                    MQTTLogEntry(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        topic=self.topic_publish,
                        direction="out",
                        payload=command,
                    )
                )
            self._client.publish(self.topic_publish, json.dumps(command), qos=1)

    def enable_logging(self, enabled: bool = True):
        """Enable or disable MQTT message logging."""
        self._logging_enabled = enabled
        # Don't clear logs when stopping - user can manually clear with clear_logs()

    def get_logs(self) -> list[MQTTLogEntry]:
        """Get all logged MQTT messages."""
        return list(self._message_log)

    def clear_logs(self):
        """Clear the message log."""
        self._message_log.clear()

    @property
    def logging_enabled(self) -> bool:
        """Check if logging is enabled."""
        return self._logging_enabled

    def register_raw_message_handler(self, handler: Callable[[str, bytes], None]) -> None:
        """Register a handler invoked for every incoming MQTT message.

        Used by the VP MQTT bridge to republish the printer's report pushes to
        slicers connected to a virtual printer in non-proxy mode. Handlers run
        on paho's network thread and must not block; exceptions are caught.
        """
        if handler not in self._raw_message_handlers:
            self._raw_message_handlers.append(handler)

    def unregister_raw_message_handler(self, handler: Callable[[str, bytes], None]) -> None:
        """Unregister a previously-registered raw-message handler."""
        try:
            self._raw_message_handlers.remove(handler)
        except ValueError:
            pass

    def publish_raw(self, topic: str, payload: bytes | str, qos: int = 1) -> bool:
        """Publish a pre-formed payload directly to the printer's MQTT broker.

        Used by the VP MQTT bridge to forward slicer-originated commands without
        going through send_command's sequence-id mangling. Returns False if the
        underlying paho client isn't ready.
        """
        if self._client is None:
            return False
        try:
            info = self._client.publish(topic, payload, qos=qos)
            return info.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception:
            logger.exception("[%s] publish_raw failed for topic=%s", self.serial_number, topic)
            return False

    def send_drying_command(
        self, ams_id: int, temp: int, duration: int, mode: int = 1, filament: str = "", rotate_tray: bool = False
    ):
        """Send AMS drying start/stop command.

        Args:
            ams_id: AMS unit ID (0-3 for AMS 2 Pro, 128-135 for AMS-HT)
            temp: Target drying temperature (45-65 for AMS 2 Pro, 45-85 for AMS-HT)
            duration: Drying duration in hours
            mode: 1=start, 0=stop
            filament: Filament type string (e.g. "PLA", "PETG")
            rotate_tray: Whether to rotate the spool during drying for even heat
        """
        if not self._client:
            return False
        self._sequence_id += 1
        # A2L AMS-Lite: normalised id 6 -> physical 16 on the wire (the Lite does
        # not actually support drying, but keep the translation consistent). The
        # _drying_targets dict below stays keyed by the normalised id so the
        # on_drying_complete callback matches the telemetry.
        wire_ams_id = a2l_lite_wire_ids(ams_id, 0)[0] if ams_id == A2L_LITE_NORMALIZED_AMS_ID else ams_id
        command = {
            "print": {
                "sequence_id": str(self._sequence_id),
                "command": "ams_filament_drying",
                "ams_id": wire_ams_id,
                "temp": temp,
                "cooling_temp": 20 if mode == 1 else 0,
                "duration": duration,
                "humidity": 0,
                "mode": mode,
                "rotate_tray": rotate_tray,
                "filament": filament,
                "close_power_conflict": False,
            }
        }
        # Log the full wire JSON at INFO so support bundles capture exactly
        # what we sent — needed to diagnose silent rejections (#1447) where
        # the printer ACKs the command but never starts/stops drying.
        # Paired with the ams_filament_drying response-payload INFO log so
        # both halves of the conversation land in the bundle by default.
        wire_json = json.dumps(command)
        self._client.publish(self.topic_publish, wire_json, qos=1)
        logger.info(
            "[%s] Sent ams_filament_drying: %s",
            self.serial_number,
            wire_json,
        )
        # Track the active-cycle target so the badge can show "PETG @ 65°C"
        # while drying. Bambu only echoes dry_time on subsequent pushes.
        # duration_hours is not shown anywhere; it is what lets the cycle-end log
        # say how much of the requested time the firmware actually ran (#2770).
        if mode == 1:
            self._drying_targets[ams_id] = {
                "filament": filament or "",
                "temp": int(temp),
                "duration_hours": int(duration),
            }
            self._drying_stops_sent.discard(ams_id)
        else:
            self._drying_targets.pop(ams_id, None)
            # Remember that this cycle's end is ours, so the cycle-end log
            # attributes it to Bambuddy instead of to the firmware (#2770). A
            # stop always ends the cycle far short of its duration, which is
            # otherwise indistinguishable from the firmware abandoning it.
            self._drying_stops_sent.add(ams_id)
        return True

    @staticmethod
    def _parse_kprofile_entries(filaments: list, response_nozzle: str | None, log_errors: bool) -> list[KProfile]:
        """Build KProfile objects from an ``extrusion_cali_get`` filaments array.

        The printer reports ``nozzle_diameter`` **only on the response
        envelope** — the per-filament entries carry just setting_id,
        filament_id, name, k_value, n_coef and cali_idx. Defaulting the
        per-entry lookup to "0.4" therefore stamped every profile 0.4mm on
        single-nozzle printers regardless of the installed nozzle (#1748),
        which broke the K-Profiles display and, worse, the cali_idx cascade
        in the inventory/Spoolman assign paths that matches on
        nozzle_diameter. Fall back to the envelope value instead, and only
        to "0.4" when the envelope has none either.

        ``or`` rather than a dict default on purpose: it also covers an entry
        that carries the key with an empty value, and stops ``str()`` turning
        a missing envelope value into the literal "None".
        """
        profiles: list[KProfile] = []
        for i, f in enumerate(filaments):
            if not isinstance(f, dict):
                continue
            try:
                profiles.append(
                    KProfile(
                        # cali_idx is the actual slot/calibration index from the printer
                        slot_id=f.get("cali_idx", i),
                        extruder_id=int(f.get("extruder_id", 0)),
                        nozzle_id=str(f.get("nozzle_id", "")),
                        nozzle_diameter=str(f.get("nozzle_diameter") or response_nozzle or "0.4"),
                        filament_id=str(f.get("filament_id", "")),
                        name=str(f.get("name", "")),
                        k_value=str(f.get("k_value", "0.000000")),
                        n_coef=str(f.get("n_coef", "0.000000")),
                        ams_id=int(f.get("ams_id", 0)),
                        tray_id=int(f.get("tray_id", -1)),
                        setting_id=f.get("setting_id"),
                    )
                )
            except (ValueError, TypeError) as e:
                # Skip malformed entries; the remaining profiles stay usable.
                # Unsolicited broadcasts arrive constantly, so only a response
                # someone is actually waiting on is worth a warning.
                if log_errors:
                    logger.warning("Failed to parse K-profile: %s", e)
                else:
                    logger.debug("Failed to parse K-profile from broadcast: %s", e)
        return profiles

    def _store_kprofiles(self, profiles: list, response_nozzle: str | None) -> None:
        """File one calibration-table response under its nozzle diameter.

        ``response_nozzle`` names the table the printer just sent, so that
        bucket is replaced wholesale and every other one is left alone. When
        the envelope carries no diameter, fall back to the diameters the parsed
        profiles claim for themselves — and if there are none of those either,
        keep what we have rather than dropping a table we cannot attribute.

        ``state.kprofiles`` stays a flat list because that is what its readers
        expect; the three assign paths already filter it by ``nozzle_diameter``
        and were quietly finding nothing whenever the last response happened to
        be for a different nozzle.
        """
        buckets: dict[str, list] = {}
        if response_nozzle:
            buckets[str(response_nozzle)] = list(profiles)
        else:
            for profile in profiles:
                buckets.setdefault(str(profile.nozzle_diameter), []).append(profile)
        if not buckets:
            return
        self._kprofiles_by_nozzle.update(buckets)
        self.state.kprofiles = [
            kp for nozzle in sorted(self._kprofiles_by_nozzle) for kp in self._kprofiles_by_nozzle[nozzle]
        ]

    def _handle_kprofile_response(self, data: dict):
        """Handle K-profile response from printer."""
        response_nozzle = data.get("nozzle_diameter")
        response_seq_id = str(data.get("sequence_id", ""))
        filaments = data.get("filaments", [])

        # Snapshot the map: the asyncio thread adds and removes entries while
        # this MQTT callback thread walks it.
        pending = dict(self._pending_kprofile_requests)
        request = pending.get(response_seq_id)

        if request is None and pending:
            # Firmware that doesn't echo our sequence_id still has to be
            # served, so fall back to the pre-#1748 rule of matching on the
            # nozzle size. Only requests still waiting are eligible, and the
            # sequence_id lookup above has already claimed any response that
            # identifies itself, so this can no longer hand request A's
            # answer to request B when both are in flight.
            request = next(
                (r for r in pending.values() if r["nozzle"] == response_nozzle and r["profiles"] is None),
                None,
            )

        if pending:
            logger.info(
                "[%s] K-profile response: nozzle=%s, seq_id=%s, %d profiles, matched=%s",
                self.serial_number,
                response_nozzle,
                response_seq_id or "?",
                len(filaments),
                request is not None,
            )

        if request is None and pending:
            # A request is outstanding and this isn't its answer. The printer
            # broadcasts extrusion_cali_get unsolicited, so letting this
            # through would replace state.kprofiles with another nozzle's
            # profiles while the caller is still waiting.
            logger.debug(
                "[%s] Ignoring unmatched K-profile response: nozzle=%s, seq_id=%s",
                self.serial_number,
                response_nozzle,
                response_seq_id or "?",
            )
            return

        profiles = self._parse_kprofile_entries(filaments, response_nozzle, log_errors=request is not None)
        self._store_kprofiles(profiles, response_nozzle)

        if request is None:
            # Unsolicited broadcast with nothing in flight: state is refreshed,
            # nobody to wake. Worth a line — this is the printer answering
            # somebody else (BambuStudio queries the same report topic), and
            # until it was bucketed by nozzle it was also the quietest way for
            # the AMS card's K values to change underneath us.
            logger.debug(
                "[%s] Adopted unsolicited K-profile table: nozzle=%s, %d profiles",
                self.serial_number,
                response_nozzle or "?",
                len(profiles),
            )
            return

        logger.info("[%s] Got %s K-profiles for nozzle=%s", self.serial_number, len(profiles), response_nozzle)
        request["profiles"] = profiles

        # Signal the waiter. Use the thread-safe path since MQTT callbacks run
        # in a different thread than the event loop.
        event = request["event"]
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(event.set)
        else:
            # Fallback for when loop is not available
            event.set()

    async def get_kprofiles(
        self, nozzle_diameter: str = "0.4", timeout: float = 5.0, max_retries: int = 3
    ) -> list[KProfile]:
        """Request K-profiles from the printer with retry logic.

        Bambu printers sometimes ignore the first K-profile request, so we
        implement retry logic to ensure reliable retrieval.

        Args:
            nozzle_diameter: Filter by nozzle diameter (e.g., "0.4")
            timeout: Timeout in seconds to wait for each response attempt
            max_retries: Maximum number of retry attempts

        Returns:
            List of KProfile objects
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot get K-profiles: not connected", self.serial_number)
            return []

        # Capture current event loop for thread-safe callback
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[%s] No running event loop", self.serial_number)
            return []

        for attempt in range(max_retries):
            # Register this attempt under its own sequence_id so a concurrent
            # request for a different nozzle size can't consume its response
            # (#1748) — the pending map is keyed by exactly the id we send.
            self._sequence_id += 1
            seq_id = str(self._sequence_id)
            request: dict = {"nozzle": nozzle_diameter, "event": asyncio.Event(), "profiles": None}
            self._pending_kprofile_requests[seq_id] = request

            # Send the command with nozzle_diameter filter
            command = {
                "print": {
                    "command": "extrusion_cali_get",
                    "filament_id": "",
                    "nozzle_diameter": nozzle_diameter,
                    "sequence_id": seq_id,
                }
            }

            logger.info(
                f"[{self.serial_number}] Requesting K-profiles for nozzle_diameter={nozzle_diameter} (attempt {attempt + 1}/{max_retries}, seq_id={seq_id})"
            )
            logger.debug("[%s] K-profile request JSON: %s", self.serial_number, json.dumps(command))

            # Wait for the response (the handler matches it back to this entry)
            try:
                self._client.publish(self.topic_publish, json.dumps(command), qos=1)
                await asyncio.wait_for(request["event"].wait(), timeout=timeout)
                profiles = request["profiles"] or []
                logger.info(
                    f"[{self.serial_number}] Got {len(profiles)} K-profiles for nozzle={nozzle_diameter} on attempt {attempt + 1}"
                )
                return profiles
            except TimeoutError:
                logger.warning(
                    f"[{self.serial_number}] Timeout on K-profiles request attempt {attempt + 1}/{max_retries}"
                )
                if attempt < max_retries - 1:
                    # Brief delay before retry
                    await asyncio.sleep(0.5)
            finally:
                self._pending_kprofile_requests.pop(seq_id, None)

        logger.error("[%s] Failed to get K-profiles after %s attempts", self.serial_number, max_retries)
        return []

    def _publish_cali_write(self, command: dict, seq_id: str) -> bool:
        """Publish a K-profile write and arm its ack slot.

        Registration happens before the publish because the printer answers in
        well under a second — measured at 70-150ms — which is comfortably
        before an async caller gets back to awaiting.
        """
        self._pending_cali_acks[seq_id] = None
        try:
            self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        except Exception:
            self._pending_cali_acks.pop(seq_id, None)
            raise
        return True

    async def await_cali_ack(self, seq_id: str, timeout: float = 6.0) -> tuple[bool, str]:
        """Wait for the printer's verdict on a K-profile write.

        Returns ``(ok, detail)``. ``ok`` is False only when the printer
        explicitly said ``result: "fail"`` — a timeout returns True with a
        detail string, because "no answer" is not evidence of rejection and
        older firmware may not answer at all. Callers that need certainty read
        the calibration table back.

        Polled rather than event-driven on purpose: the ack is filled in by the
        MQTT callback thread, and polling a dict costs one lookup every 50ms
        for at most a few hundred milliseconds, against the cross-thread
        event plumbing it would otherwise take.
        """
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                ack = self._pending_cali_acks.get(seq_id)
                if ack is not None:
                    result = str(ack.get("result", "")).lower()
                    reason = str(ack.get("reason", "") or "")
                    if result == "fail":
                        return (False, reason or "printer reported failure")
                    return (True, reason)
                await asyncio.sleep(0.05)
        finally:
            self._pending_cali_acks.pop(seq_id, None)
        logger.warning("[%s] No ack for K-profile write seq=%s within %.1fs", self.serial_number, seq_id, timeout)
        return (True, "no acknowledgement from printer")

    def set_kprofile(
        self,
        filament_id: str,
        name: str,
        k_value: str,
        nozzle_diameter: str = "0.4",
        nozzle_id: str = "HS00-0.4",
        extruder_id: int = 0,
        setting_id: str | None = None,
        slot_id: int = 0,
        cali_idx: int | None = None,
    ) -> str | None:
        """Set/update a K-profile on the printer.

        Args:
            filament_id: Bambu filament identifier
            name: Profile name
            k_value: Pressure advance value (e.g., "0.020000")
            nozzle_diameter: Nozzle diameter (e.g., "0.4")
            nozzle_id: Nozzle identifier (e.g., "HS00-0.4")
            extruder_id: Extruder ID (0 or 1 for dual nozzle)
            setting_id: Existing setting ID for updates, None for new
            slot_id: Calibration index (cali_idx) for the profile
            cali_idx: For edits, the existing slot being edited (enables in-place edit)

        Returns:
            The sequence_id the command was sent under, so the caller can
            await the printer's verdict via await_cali_ack. None if the
            command could not be sent.
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set K-profile: not connected", self.serial_number)
            return None

        self._sequence_id += 1
        seq_id = str(self._sequence_id)

        # Build the filament entry - printer uses cali_idx for profile identification
        # For new profiles (slot_id=0), use cali_idx=-1 to tell printer to create new slot
        # For edits, use the provided cali_idx or slot_id
        if cali_idx is not None:
            effective_cali_idx = cali_idx
        else:
            effective_cali_idx = -1 if slot_id == 0 else slot_id

        # Generate a setting_id for new profiles (required by printer)
        # Format: "PF" + 17 random digits
        import random

        if not setting_id and slot_id == 0:
            setting_id = f"PF{random.randint(10000000000000000, 99999999999999999)}"

        filament_entry = {
            "ams_id": 0,
            "cali_idx": effective_cali_idx,
            "extruder_id": extruder_id,
            "filament_id": filament_id,
            "k_value": k_value,
            "n_coef": "0.000000",
            "name": name,
            "nozzle_diameter": nozzle_diameter,
            "nozzle_id": nozzle_id,
            "setting_id": setting_id if setting_id else "",
            # 0, not -1. Single-nozzle firmware validates this field and
            # answers `result: "fail", reason: "invalid tray_id"` to -1 — while
            # applying the write anyway, so the rejection looked like noise.
            # Measured on an X1C: flipping only this value turns the ack into
            # `success` (#2718). BambuStudio always sends a real tray_id and
            # defaults it to 0 for a manually entered profile.
            "tray_id": 0,
        }

        command = {
            "print": {
                "command": "extrusion_cali_set",
                "filaments": [filament_entry],
                "nozzle_diameter": nozzle_diameter,
                "sequence_id": seq_id,
            }
        }

        command_json = json.dumps(command)
        logger.info(
            f"[{self.serial_number}] Setting K-profile: {name} = {k_value} (cali_idx={effective_cali_idx}, new={slot_id == 0})"
        )
        logger.debug("[%s] K-profile SET command: %s", self.serial_number, command_json)
        self._publish_cali_write(command, seq_id)
        return seq_id

    def set_kprofiles_batch(
        self,
        profiles: list[dict],
        nozzle_diameter: str = "0.4",
    ) -> str | None:
        """Set multiple K-profiles in a single command (for dual-nozzle).

        Args:
            profiles: List of profile dicts, each with:
                - filament_id, name, k_value, nozzle_id, extruder_id, setting_id (optional), slot_id
            nozzle_diameter: Common nozzle diameter for all profiles

        Returns:
            The sequence_id the command was sent under (see set_kprofile),
            or None if it could not be sent.
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set K-profiles batch: not connected", self.serial_number)
            return None

        import random

        self._sequence_id += 1
        seq_id = str(self._sequence_id)

        filament_entries = []
        for p in profiles:
            slot_id = p.get("slot_id", 0)
            cali_idx = p.get("cali_idx")

            if cali_idx is not None:
                effective_cali_idx = cali_idx
            else:
                effective_cali_idx = -1 if slot_id == 0 else slot_id

            setting_id = p.get("setting_id")
            if not setting_id and slot_id == 0:
                setting_id = f"PF{random.randint(10000000000000000, 99999999999999999)}"

            filament_entries.append(
                {
                    "ams_id": 0,
                    "cali_idx": effective_cali_idx,
                    "extruder_id": p.get("extruder_id", 0),
                    "filament_id": p.get("filament_id", ""),
                    "k_value": p.get("k_value", "0.020000"),
                    "n_coef": "0.000000",
                    "name": p.get("name", ""),
                    "nozzle_diameter": nozzle_diameter,
                    "nozzle_id": p.get("nozzle_id", f"HS00-{nozzle_diameter}"),
                    "setting_id": setting_id if setting_id else "",
                    # See set_kprofile: -1 is rejected as "invalid tray_id" by
                    # single-nozzle firmware even though the write lands (#2718).
                    "tray_id": 0,
                }
            )

        command = {
            "print": {
                "command": "extrusion_cali_set",
                "filaments": filament_entries,
                "nozzle_diameter": nozzle_diameter,
                "sequence_id": seq_id,
            }
        }

        command_json = json.dumps(command)
        logger.info("[%s] Setting %s K-profiles in batch", self.serial_number, len(filament_entries))
        logger.debug("[%s] K-profile SET batch command: %s", self.serial_number, command_json)
        self._publish_cali_write(command, seq_id)
        return seq_id

    def delete_kprofile(
        self,
        cali_idx: int,
        filament_id: str,
        nozzle_id: str,
        nozzle_diameter: str = "0.4",
        extruder_id: int = 0,
        setting_id: str | None = None,
    ) -> str | None:
        """Delete a K-profile from the printer.

        Args:
            cali_idx: The calibration index (slot_id) of the profile to delete
            filament_id: Bambu filament identifier
            nozzle_id: Nozzle identifier (e.g., "HH00-0.4")
            nozzle_diameter: Nozzle diameter (e.g., "0.4")
            extruder_id: Extruder ID (0 or 1 for dual nozzle)
            setting_id: Unique setting identifier (for X1C series)

        Returns:
            The sequence_id the command was sent under (see set_kprofile),
            or None if it could not be sent.
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot delete K-profile: not connected", self.serial_number)
            return None

        self._sequence_id += 1
        seq_id = str(self._sequence_id)

        # Dual-nozzle K-profile delete uses the extruder_id/nozzle_id format;
        # single-nozzle printers (X1C/P1/A1/P2S/H2S) need the setting_id form.
        # Prefer runtime detection from device.extruder.info; fall back to
        # model name. H2S is single-nozzle but shares serial prefix "094" with
        # H2D, so a prefix-only check misclassified it (#1386).
        from backend.app.utils.printer_models import is_dual_nozzle_model

        is_dual_nozzle = self._is_dual_nozzle or is_dual_nozzle_model(self.model)

        if is_dual_nozzle:
            # H2D format: uses extruder_id, nozzle_id, nozzle_diameter
            command = {
                "print": {
                    "command": "extrusion_cali_del",
                    "sequence_id": seq_id,
                    "extruder_id": extruder_id,
                    "nozzle_id": nozzle_id,
                    "filament_id": filament_id,
                    "cali_idx": cali_idx,
                    "nozzle_diameter": nozzle_diameter,
                }
            }
        else:
            # X1C/P1/A1 format: include all fields like the set command
            # The delete command structure should match what set uses
            command = {
                "print": {
                    "command": "extrusion_cali_del",
                    "sequence_id": seq_id,
                    "filament_id": filament_id,
                    "cali_idx": cali_idx,
                    "setting_id": setting_id if setting_id else "",
                    "nozzle_diameter": nozzle_diameter,
                    "nozzle_id": nozzle_id,
                    "extruder_id": extruder_id,
                }
            }

        command_json = json.dumps(command)
        logger.info(
            f"[{self.serial_number}] Deleting K-profile: cali_idx={cali_idx}, filament={filament_id}, setting_id={setting_id}, dual={is_dual_nozzle}"
        )
        logger.debug("[%s] K-profile DELETE command: %s", self.serial_number, command_json)
        # QoS 1 for reliable delivery (at least once)
        self._publish_cali_write(command, seq_id)
        return seq_id

    # =========================================================================
    # Printer Control Commands
    # =========================================================================

    def pause_print(self) -> bool:
        """Pause the current print job."""
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot pause print: not connected", self.serial_number)
            return False

        command = {"print": {"command": "pause", "sequence_id": "0"}}
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Sent pause print command", self.serial_number)
        return True

    def resume_print(self) -> bool:
        """Resume a paused print job."""
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot resume print: not connected", self.serial_number)
            return False

        command = {"print": {"command": "resume", "sequence_id": "0"}}
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Sent resume print command", self.serial_number)
        return True

    def clear_hms_errors(self) -> bool:
        """Clear HMS/print errors on the printer and locally."""
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot clear HMS errors: not connected", self.serial_number)
            return False

        command = {"print": {"command": "clean_print_error", "sequence_id": "0"}}
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        self.state.hms_errors = []
        logger.info("[%s] Sent clear HMS errors command", self.serial_number)
        return True

    def skip_objects(self, object_ids: list[int]) -> bool:
        """Skip specific objects during a print.

        This command tells the printer to skip printing the specified objects.
        The object IDs come from the slice_info.config file in the 3MF.

        Args:
            object_ids: List of identify_id values from slice_info.config

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot skip objects: not connected", self.serial_number)
            return False

        if self.state.state != "RUNNING" and self.state.state != "PAUSE":
            logger.warning(
                f"[{self.serial_number}] Cannot skip objects: printer not printing (state={self.state.state})"
            )
            return False

        if not object_ids:
            logger.warning("[%s] Cannot skip objects: no object IDs provided", self.serial_number)
            return False

        # Validate all IDs are integers
        try:
            obj_list = [int(oid) for oid in object_ids]
        except (ValueError, TypeError) as e:
            logger.warning("[%s] Invalid object IDs: %s", self.serial_number, e)
            return False

        self._sequence_id += 1
        command = {"print": {"sequence_id": str(self._sequence_id), "command": "skip_objects", "obj_list": obj_list}}
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Sent skip_objects command: %s", self.serial_number, obj_list)

        # Track skipped objects in state
        for oid in obj_list:
            if oid not in self.state.skipped_objects:
                self.state.skipped_objects.append(oid)

        return True

    def send_gcode(self, gcode: str) -> bool:
        """Send G-code command(s) to the printer.

        Multiple commands can be separated by newlines.

        Args:
            gcode: G-code command(s) to send

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot send G-code: not connected", self.serial_number)
            return False

        self._sequence_id += 1
        command = {"print": {"command": "gcode_line", "param": gcode, "sequence_id": str(self._sequence_id)}}
        # Use QoS 1 for reliable delivery (at least once)
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.debug("[%s] Sent G-code: %s...", self.serial_number, gcode[:50])
        return True

    def set_bed_temperature(self, target: int) -> bool:
        """Set the bed target temperature.

        Args:
            target: Target temperature in Celsius (0 to turn off)

        Returns:
            True if command was sent, False otherwise
        """
        return self.send_gcode(f"M140 S{target}")

    def set_nozzle_temperature(self, target: int, nozzle: int = 0) -> bool:
        """Set the nozzle target temperature.

        Args:
            target: Target temperature in Celsius (0 to turn off)
            nozzle: Nozzle index (0 for right/default, 1 for left on H2D)

        Returns:
            True if command was sent, False otherwise
        """
        # Use M104 for non-blocking
        # Always use T parameter for H2D compatibility
        result = self.send_gcode(f"M104 T{nozzle} S{target}")
        # H2D quirk: left nozzle (nozzle=1) target isn't reported in MQTT
        # Track it locally so we can display it correctly
        if result and nozzle == 1:
            self.state.temperatures["nozzle_target"] = float(target)
            self.state.temperatures["_nozzle_target_set_time"] = time.time()
            logger.info("[%s] Tracking LEFT nozzle target locally: %s°C", self.serial_number, target)
        return result

    def set_chamber_temperature(self, target: int) -> bool:
        """Set the chamber target temperature.

        Args:
            target: Target temperature in Celsius (0 to turn off heating)

        Returns:
            True if command was sent, False otherwise
        """
        # M141 sets chamber temperature
        result = self.send_gcode(f"M141 S{target}")
        # Track chamber target locally (MQTT reports encoded values that need filtering)
        if result:
            self.state.temperatures["chamber_target"] = float(target)
            self.state.temperatures["_chamber_target_set_time"] = time.time()
            # Update heating state immediately based on new target
            current_temp = self.state.temperatures.get("chamber", 0)
            self.state.temperatures["chamber_heating"] = target > 0 and current_temp < target
            logger.info(
                f"[{self.serial_number}] Tracking chamber target locally: {target}°C (heating={self.state.temperatures['chamber_heating']})"
            )
        return result

    def set_print_speed(self, mode: int) -> bool:
        """Set the print speed mode.

        Args:
            mode: Speed mode (1=silent, 2=standard, 3=sport, 4=ludicrous)

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set print speed: not connected", self.serial_number)
            return False

        if mode not in (1, 2, 3, 4):
            logger.warning("[%s] Invalid speed mode: %s", self.serial_number, mode)
            return False

        command = {"print": {"command": "print_speed", "param": str(mode), "sequence_id": "0"}}
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Set print speed mode to %s", self.serial_number, mode)
        return True

    def set_fan_speed(self, fan: int, speed: int) -> bool:
        """Set fan speed.

        Args:
            fan: Fan index (1=part cooling, 2=auxiliary, 3=chamber, 10=left auxiliary).
                Index 10 is the optional left auxiliary part cooling fan on P2S/X2D
                (airduct part id 10); Bambu's official machine profiles drive it with
                "M106 P10" in start/layer-change gcode.
            speed: Speed 0-255 (0=off, 255=full)

        Returns:
            True if command was sent, False otherwise
        """
        if fan not in (1, 2, 3, 10):
            logger.warning("[%s] Invalid fan index: %s", self.serial_number, fan)
            return False

        speed = max(0, min(255, speed))  # Clamp to 0-255
        return self.send_gcode(f"M106 P{fan} S{speed}")

    def set_part_fan(self, speed: int) -> bool:
        """Set part cooling fan speed (0-255)."""
        return self.set_fan_speed(1, speed)

    def set_aux_fan(self, speed: int) -> bool:
        """Set auxiliary fan speed (0-255)."""
        return self.set_fan_speed(2, speed)

    def set_chamber_fan(self, speed: int) -> bool:
        """Set chamber fan speed (0-255)."""
        return self.set_fan_speed(3, speed)

    def set_left_aux_fan(self, speed: int) -> bool:
        """Set left auxiliary part cooling fan speed (0-255). P2S/X2D accessory."""
        return self.set_fan_speed(10, speed)

    def set_airduct_mode(self, mode: str) -> bool:
        """Set air conditioning mode (cooling or heating).

        Args:
            mode: "cooling" (modeId=0) or "heating" (modeId=1)
                - Cooling: Suitable for PLA/PETG/TPU, filters and cools chamber air
                - Heating: Suitable for ABS/ASA/PC/PA, circulates and heats chamber air,
                           closes top exhaust flap

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set airduct mode: not connected", self.serial_number)
            return False

        self._sequence_id += 1
        mode_id = 0 if mode == "cooling" else 1
        command = {
            "print": {"command": "set_airduct", "modeId": mode_id, "sequence_id": str(self._sequence_id), "submode": -1}
        }
        # Use QoS 1 for reliable delivery
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info(
            "[%s] Set airduct mode to %s (modeId=%s, seq=%s)", self.serial_number, mode, mode_id, self._sequence_id
        )
        return True

    def set_chamber_light(self, on: bool) -> bool:
        """Turn chamber light on or off.

        Args:
            on: True to turn on, False to turn off

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set chamber light: not connected", self.serial_number)
            return False

        mode = "on" if on else "off"
        # Control both chamber lights (some printers like H2D have two)
        for led_node in ["chamber_light", "chamber_light2"]:
            self._sequence_id += 1
            command = {
                "system": {
                    "command": "ledctrl",
                    "led_node": led_node,
                    "led_mode": mode,
                    "led_on_time": 500,
                    "led_off_time": 500,
                    "loop_times": 0,
                    "interval_time": 0,
                    "sequence_id": str(self._sequence_id),
                }
            }
            self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Set chamber lights %s (seq=%s)", self.serial_number, "on" if on else "off", self._sequence_id)
        return True

    def select_extruder(self, extruder: int) -> bool:
        """Select the active extruder for dual-nozzle printers (H2D).

        Args:
            extruder: Extruder index (0=right, 1=left for H2D)

        Returns:
            True if command was sent, False otherwise
        """
        if extruder not in (0, 1):
            logger.warning("[%s] Invalid extruder: %s", self.serial_number, extruder)
            return False

        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot switch extruder: not connected", self.serial_number)
            return False

        # H2D extruder switching via select_extruder command
        # Command format captured from OrcaSlicer:
        # {"print": {"command": "select_extruder", "extruder_index": 0, "sequence_id": "..."}}
        # extruder_index: 0 = RIGHT, 1 = LEFT
        self._sequence_id += 1
        command = {
            "print": {"command": "select_extruder", "extruder_index": extruder, "sequence_id": str(self._sequence_id)}
        }
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info(
            "[%s] Sent select_extruder command: extruder_index=%s (0=right, 1=left)", self.serial_number, extruder
        )
        return True

    def home_axes(self, axes: str = "XYZ") -> bool:
        """Run the printer's full auto-home sequence.

        The ``axes`` argument is ignored: a bare ``G28`` is always sent so
        Bambu firmware runs its safe multi-step routine (park toolhead →
        home XY → home Z). Partial-axis variants like ``G28 Z`` skip the
        toolhead-park step and can crash the bed into the toolhead on H2C
        / H2D / H2S / X1 where Z-home moves the bed UP — see #1052.
        """
        return self.send_gcode("G28")

    def move_axis(self, axis: str, distance: float, speed: int = 3000) -> bool:
        """Move an axis by a relative distance.

        Args:
            axis: Axis to move ("X", "Y", or "Z")
            distance: Distance to move in mm (positive or negative)
            speed: Movement speed in mm/min

        Returns:
            True if command was sent, False otherwise
        """
        axis = axis.upper()
        if axis not in ("X", "Y", "Z"):
            logger.warning("[%s] Invalid axis: %s", self.serial_number, axis)
            return False

        # G91 = relative mode, G0 = rapid move, G90 = back to absolute
        gcode = f"G91\nG0 {axis}{distance:.2f} F{speed}\nG90"
        return self.send_gcode(gcode)

    def disable_motors(self) -> bool:
        """Disable all stepper motors.

        Warning: This will cause the printer to lose its position.
        A homing operation will be required before printing.

        Returns:
            True if command was sent, False otherwise
        """
        return self.send_gcode("M18")

    def enable_motors(self) -> bool:
        """Enable all stepper motors.

        Returns:
            True if command was sent, False otherwise
        """
        return self.send_gcode("M17")

    def ams_load_filament(self, tray_id: int, extruder_id: int | None = None) -> bool:
        """Load filament from a specific AMS tray.

        Args:
            tray_id: Global tray ID — 0..15 for AMS slots, 254 for external spool
                (single-external printers and Ext-L on dual-nozzle H2D),
                255 for Ext-R on dual-nozzle H2D.
            extruder_id: Which hotend to feed (0 = right/main, 1 = left/deputy).
                Sent only when given, matching BambuStudio: ``extruder_id`` is
                an optional field on ``ams_change_filament``
                (``DeviceManager::command_ams_change_filament``) and Studio
                omits it unless a Filament Track Switch is installed. Without a
                switch the firmware derives the hotend from the AMS's own
                extruder binding and an explicit value is redundant; *with* one
                every AMS reports 0xE and is bound to a switch inlet instead, so
                the firmware has nothing to derive from and the load silently
                does nothing until we name the hotend.

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot load filament: not connected", self.serial_number)
            return False

        # Build the ams_change_filament command. Encoding differs by target type:
        #   - AMS slots (0..15): slot_id is the local slot, curr/tar_temp = -1.
        #   - External spool (tray_id=254): legacy capture from a single-extruder
        #     printer used slot_id=254, curr/tar_temp=-1; preserved here.
        #   - Ext-R on dual-nozzle H2D (tray_id=255): captured shape from
        #     BambuStudio uses slot_id=0 (extruder index, 0=right), and
        #     curr_temp/tar_temp = the actual right-nozzle temp.  See #891.
        self._sequence_id += 1
        wire_target = tray_id
        if tray_id == 255:
            ams_id = 255
            slot_id = 0  # extruder index for the right nozzle
            right_temp = int(self.state.temperatures.get("nozzle_2", 0) or 0)
            if right_temp < 180:
                right_temp = 215  # Reasonable default if right nozzle is cold/unknown
            curr_temp = right_temp
            tar_temp = right_temp
        elif tray_id == 254:
            ams_id = 255
            slot_id = 254
            curr_temp = -1
            tar_temp = -1
        elif (_a2l := a2l_lite_wire_ids(tray_id // 4, tray_id)) is not None:
            # A2L AMS-Lite: physical unit 16 + local slot confirmed; the wire
            # `target` (physical global 64-67) is extrapolated (no A2L load
            # capture yet). See a2l_lite_wire_ids.
            ams_id, slot_id, wire_target = _a2l
            curr_temp = -1
            tar_temp = -1
        else:
            ams_id = tray_id // 4
            slot_id = tray_id % 4
            curr_temp = -1
            tar_temp = -1

        command = {
            "print": {
                "command": "ams_change_filament",
                "sequence_id": str(self._sequence_id),
                "ams_id": ams_id,
                "slot_id": slot_id,
                "target": wire_target,
                "curr_temp": curr_temp,
                "tar_temp": tar_temp,
            }
        }
        if extruder_id is not None:
            command["print"]["extruder_id"] = int(extruder_id)

        command_json = json.dumps(command)
        logger.info("[%s] Publishing ams_change_filament command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        logger.info("[%s] Loading filament from tray %s (AMS %s slot %s)", self.serial_number, tray_id, ams_id, slot_id)

        # Track this load request for H2D dual-nozzle disambiguation
        # H2D reports only slot number (0-3) in tray_now, so we use our tracked value
        self._last_load_tray_id = tray_id
        self.state.pending_tray_target = tray_id
        logger.info("[%s] Set pending_tray_target=%s for H2D disambiguation", self.serial_number, tray_id)

        return True

    def ams_unload_filament(self, tray_id: int | None = None) -> bool:
        """Unload filament, optionally naming the slot to unload.

        Args:
            tray_id: Global tray ID of the slot being unloaded. When given, the
                command is addressed to that slot's AMS and is only sent if an
                extruder is actually fed from it — BambuStudio does the same
                (``StatusPanel::on_ams_unload`` walks the extruders and sends
                nothing when none matches). When omitted, the pre-existing
                behaviour is kept: unload whatever ``tray_now`` names.

        ``tray_now`` is a single value for the whole printer, so on a dual-nozzle
        machine with both hotends loaded it names only one of them and an
        unaddressed unload picks that one regardless of which slot the operator
        clicked. Passing the slot is what makes the two hotends distinguishable.

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot unload filament: not connected", self.serial_number)
            return False

        # Get the currently loaded tray info
        tray_now = self.state.tray_now
        source_tray = tray_now if tray_id is None else tray_id
        logger.info("[%s] Unload requested, tray_now=%s, tray_id=%s", self.serial_number, tray_now, tray_id)

        # Determine source ams_id for the unload command
        if source_tray == 255 or source_tray == 254:
            ams_id = 255  # No filament or external spool
        elif (_a2l := a2l_lite_wire_ids(source_tray // 4, source_tray)) is not None:
            ams_id = _a2l[0]  # A2L AMS-Lite: normalised 6 -> physical 16
        else:
            ams_id = source_tray // 4  # Source AMS

        # Refuse an addressed unload of a slot no hotend is holding — but only on
        # a printer that has more than one hotend, which is the only case the
        # check exists for. With one hotend there is nothing to disambiguate:
        # tray_now already names the loaded slot exactly, and running the check
        # anyway would stake unload on `snow` meaning ams*4+slot there too. It
        # very likely does, but single-nozzle machines do report the block —
        # BambuStudio has a dedicated branch for `m_total_extder_count == 1` and
        # an X1C on the maintainer's own network sends `device.extruder` — and
        # nobody has read a single-nozzle `snow` off the wire. Guessing wrong
        # would 409 every unload on every X1C, P1S and A1.
        #
        # Gated on the runtime flag rather than on len(extruder_slots), which is
        # rebuilt from each payload's array and would flip the check off for any
        # frame that carried a short one; and deliberately not on
        # ``is_dual_nozzle_model``, whose model-name fallback reports at least
        # one single-nozzle machine as dual (#1386) — the false positive there is
        # exactly the case this gate exists to keep out.
        #
        # The external spool is excluded for a different reason: 254/255 are not
        # ams*4+slot, so the local-slot arithmetic below cannot describe them.
        if tray_id is not None and tray_id not in (254, 255) and self._is_dual_nozzle and self.state.extruder_slots:
            local_slot = _a2l[1] if (_a2l := a2l_lite_wire_ids(tray_id // 4, tray_id)) is not None else tray_id % 4
            holder = next(
                (ext for ext, slot in self.state.extruder_slots.items() if slot.holds(ams_id, local_slot)),
                None,
            )
            if holder is None:
                logger.info(
                    "[%s] Unload skipped: no extruder is fed from AMS %s slot %s",
                    self.serial_number,
                    ams_id,
                    local_slot,
                )
                return False
            logger.info(
                "[%s] Unloading AMS %s slot %s from extruder %s", self.serial_number, ams_id, local_slot, holder
            )

        # Command format from BambuStudio traffic capture:
        # - No extruder_id field
        # - For UNLOAD: curr_temp and tar_temp are the actual nozzle temp (e.g., 210)
        # - slot_id=255 and target=255 for unload
        # Get current nozzle temperature for the unload command
        nozzle_temp = int(self.state.temperatures.get("nozzle", 210))
        if nozzle_temp < 180:
            nozzle_temp = 210  # Default to PLA temp if nozzle is cold

        self._sequence_id += 1
        command = {
            "print": {
                "command": "ams_change_filament",
                "sequence_id": str(self._sequence_id),
                "ams_id": ams_id,
                "slot_id": 255,  # 255 = unload marker
                "target": 255,  # 255 = unload destination
                "curr_temp": nozzle_temp,
                "tar_temp": nozzle_temp,
            }
        }

        command_json = json.dumps(command)
        logger.info("[%s] Publishing ams_change_filament (unload) command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        logger.info("[%s] Unloading filament (tray_now was %s)", self.serial_number, tray_now)

        # Clear tracked load request since we're unloading
        self._last_load_tray_id = None
        self.state.pending_tray_target = None
        logger.info("[%s] Cleared pending_tray_target (unload)", self.serial_number)

        return True

    def ams_control(self, action: str) -> bool:
        """Control AMS operations.

        Args:
            action: "resume", "reset", or "pause"

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot control AMS: not connected", self.serial_number)
            return False

        if action not in ("resume", "reset", "pause"):
            logger.warning("[%s] Invalid AMS action: %s", self.serial_number, action)
            return False

        command = {"print": {"command": "ams_control", "param": action, "sequence_id": "0"}}
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] AMS control: %s", self.serial_number, action)
        return True

    def ams_refresh_tray(self, ams_id: int, tray_id: int) -> tuple[bool, str]:
        """Trigger RFID re-read for a specific AMS tray.

        Args:
            ams_id: AMS unit ID (0-3, or 128 for H2D external tray)
            tray_id: Tray ID within the AMS (0-3)

        Returns:
            Tuple of (success, message)
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot refresh AMS tray: not connected", self.serial_number)
            return False, "Printer not connected"

        # Check if filament is currently loaded (tray_now != 255)
        # RFID refresh requires the AMS to move filament, which can't happen if one is loaded
        tray_now = self.state.tray_now
        if tray_now != 255:
            # Decode which tray is loaded for the message
            if tray_now == 254:
                loaded_tray = "external spool"
            elif tray_now >= 0 and tray_now < 128:
                loaded_ams = tray_now // 4
                loaded_slot = tray_now % 4
                loaded_tray = f"AMS {loaded_ams + 1} slot {loaded_slot + 1}"
            else:
                loaded_tray = f"tray {tray_now}"
            logger.warning("[%s] Cannot refresh AMS tray: filament loaded from %s", self.serial_number, loaded_tray)
            return False, f"Please unload filament first. Currently loaded: {loaded_tray}"

        # A2L AMS-Lite: physical unit 16 + local slot (matches ams_mapping2).
        wire_ams_id, wire_slot_id = ams_id, tray_id
        if (_a2l := a2l_lite_wire_ids(ams_id, tray_id)) is not None:
            wire_ams_id, wire_slot_id, _ = _a2l

        # Use ams_get_rfid command to trigger RFID re-read
        # This command is used by Bambu Studio to re-read the RFID tag
        command = {
            "print": {"command": "ams_get_rfid", "ams_id": wire_ams_id, "slot_id": wire_slot_id, "sequence_id": "0"}
        }
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Triggering RFID re-read: AMS %s, slot %s", self.serial_number, ams_id, tray_id)

        return True, f"Refreshing AMS {ams_id} tray {tray_id}"

    def ams_set_filament_setting(
        self,
        ams_id: int,
        tray_id: int,
        tray_info_idx: str,
        tray_type: str,
        tray_sub_brands: str,
        tray_color: str,
        nozzle_temp_min: int,
        nozzle_temp_max: int,
        setting_id: str = "",
    ) -> bool:
        """Set AMS tray filament settings (type, color, temperature).

        Note: K value is set separately via extrusion_cali_sel command.

        Args:
            ams_id: AMS unit ID (0-3 for regular AMS, 128-135 for HT AMS)
            tray_id: Tray ID within the AMS (0-3)
            tray_info_idx: Filament ID short format (e.g., "GFL05")
            tray_type: Filament type (e.g., "PLA", "PETG")
            tray_sub_brands: Sub-brand name (e.g., "PLA Basic", "PETG HF")
            tray_color: Color in RRGGBBAA hex format (e.g., "FFFF00FF")
            nozzle_temp_min: Minimum nozzle temperature
            nozzle_temp_max: Maximum nozzle temperature
            setting_id: Full setting ID with version (e.g., "GFSL05_07") - optional

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set AMS filament setting: not connected", self.serial_number)
            return False

        # Calculate mqtt IDs based on AMS type.
        # External-spool convention verified against a BambuStudio→X1C packet capture
        # (issue #1279, May 2026): for `ams_filament_setting` Studio sends the
        # *global* tray index in `tray_id`, not a local position within the virtual
        # unit. The printer's response echoes `tray_id: 0` (slot position), which
        # is what the original code was matching — but the request and response
        # use different semantics for that field. Sending `tray_id: 0` is what
        # the P1S in #1279 rejected with `result: "fail"`.
        if ams_id == 255:
            vt_tray = self.state.raw_data.get("vt_tray", []) if self.state.raw_data else []
            if len(vt_tray) > 1:
                # Dual external slots (H2D): each ext slot is its own virtual AMS unit
                # (254=ext-L / slot 0, 255=ext-R / slot 1). The dual case is NOT
                # covered by the X1C capture — left at `mqtt_tray_id = 0` until a
                # captured Studio→H2D exchange confirms the correct value.
                mqtt_ams_id = 254 + tray_id
                mqtt_tray_id = 0
            else:
                # Single external slot (X1C, P1S, A1): global tray_id=254.
                mqtt_ams_id = 255
                mqtt_tray_id = 254
            slot_id = 0
        elif (_a2l := a2l_lite_wire_ids(ams_id, tray_id)) is not None:
            # A2L AMS-Lite: physical unit 16, local 0-3 slot (matches the
            # firmware's own ams_mapping2 {ams_id:16, slot_id:0-3}).
            mqtt_ams_id, slot_id, _ = _a2l
            mqtt_tray_id = slot_id
        elif ams_id <= 3:
            mqtt_ams_id = ams_id
            mqtt_tray_id = tray_id
            slot_id = tray_id
        else:
            # AMS-HT: single tray per unit
            mqtt_ams_id = ams_id
            mqtt_tray_id = tray_id
            slot_id = 0

        command = {
            "print": {
                "command": "ams_filament_setting",
                "ams_id": mqtt_ams_id,
                "tray_id": mqtt_tray_id,
                "slot_id": slot_id,
                "tray_info_idx": tray_info_idx,
                "tray_type": tray_type,
                "tray_sub_brands": tray_sub_brands,
                "tray_color": tray_color,
                "nozzle_temp_min": nozzle_temp_min,
                "nozzle_temp_max": nozzle_temp_max,
                "sequence_id": "0",
            }
        }

        # Include setting_id if provided (helps slicer show correct profile)
        if setting_id:
            command["print"]["setting_id"] = setting_id

        command_json = json.dumps(command)
        logger.info(
            f"[{self.serial_number}] Publishing ams_filament_setting: AMS {ams_id}, tray {tray_id}, tray_info_idx={tray_info_idx}, setting_id={setting_id}"
        )
        logger.debug("[%s] ams_filament_setting command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        self._last_ams_cmd_time = time.monotonic()
        return True

    def reset_ams_slot(self, ams_id: int, tray_id: int) -> bool:
        """Reset an AMS slot to empty/unconfigured state.

        Args:
            ams_id: AMS unit ID (0-3 for regular AMS, 128-135 for HT AMS)
            tray_id: Tray ID within the AMS (0-3)

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot reset AMS slot: not connected", self.serial_number)
            return False

        # Calculate mqtt IDs based on AMS type — same convention as
        # ams_set_filament_setting above. See its comment for the #1279 capture rationale.
        if ams_id == 255:
            vt_tray = self.state.raw_data.get("vt_tray", []) if self.state.raw_data else []
            if len(vt_tray) > 1:
                # Dual external slots (H2D): each ext slot is its own virtual AMS unit
                mqtt_ams_id = 254 + tray_id
                mqtt_tray_id = 0
            else:
                # Single external slot (X1C, P1S, A1): global tray_id=254.
                mqtt_ams_id = 255
                mqtt_tray_id = 254
            slot_id = 0
        elif (_a2l := a2l_lite_wire_ids(ams_id, tray_id)) is not None:
            # A2L AMS-Lite: physical unit 16, local 0-3 slot (matches ams_mapping2).
            mqtt_ams_id, slot_id, _ = _a2l
            mqtt_tray_id = slot_id
        elif ams_id <= 3:
            mqtt_ams_id = ams_id
            mqtt_tray_id = tray_id
            slot_id = tray_id
        else:
            # AMS-HT: single tray per unit
            mqtt_ams_id = ams_id
            mqtt_tray_id = tray_id
            slot_id = 0

        command = {
            "print": {
                "command": "ams_filament_setting",
                "ams_id": mqtt_ams_id,
                "tray_id": mqtt_tray_id,
                "slot_id": slot_id,
                "tray_info_idx": "",
                "tray_type": "",
                "tray_sub_brands": "",
                "tray_color": "00000000",
                "nozzle_temp_min": 0,
                "nozzle_temp_max": 0,
                "sequence_id": "0",
            }
        }

        command_json = json.dumps(command)
        logger.info("[%s] Resetting AMS slot: AMS %s, tray %s", self.serial_number, ams_id, tray_id)
        logger.debug("[%s] reset_ams_slot command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        self._last_ams_cmd_time = time.monotonic()
        return True

    def extrusion_cali_sel(
        self,
        ams_id: int,
        tray_id: int,
        cali_idx: int,
        filament_id: str,
        nozzle_diameter: str = "0.4",
    ) -> bool:
        """Set calibration profile (K value) for an AMS slot.

        This command selects a K profile from the printer's calibration list.
        Use cali_idx=-1 to use the default K value (0.020).

        Note: Do NOT send setting_id in this command — BambuStudio never includes
        it, and adding it causes the firmware to mislink the profile on X1C/P1S.

        Args:
            ams_id: AMS unit ID (0-3 for regular AMS, 128-135 for HT AMS)
            tray_id: Tray ID within the AMS (0-3)
            cali_idx: Calibration profile index (-1 for default)
            filament_id: Filament preset ID (same as tray_info_idx)
            nozzle_diameter: Nozzle diameter string (e.g., "0.4")

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set calibration: not connected", self.serial_number)
            return False

        # Calculate mqtt IDs based on AMS type.
        # IMPORTANT: extrusion_cali_sel uses GLOBAL tray_id (unlike ams_filament_setting
        # which uses LOCAL).  BambuStudio confirms: tray_id = ams_id * 4 + slot.
        if ams_id == 255:
            # External spool: extrusion_cali_sel uses GLOBAL tray_id (unlike
            # ams_filament_setting which uses LOCAL tray_id=0).
            vt_tray = self.state.raw_data.get("vt_tray", []) if self.state.raw_data else []
            if len(vt_tray) > 1:
                # Dual external slots (H2D): each ext slot is its own virtual AMS unit
                # Confirmed from BambuStudio logs: ext-R sends ams_id=255, tray_id=255
                mqtt_ams_id = 254 + tray_id
                mqtt_tray_id = 254 + tray_id
            else:
                # Single external slot (X1C, P1S, A1): global tray_id=254
                mqtt_ams_id = 254
                mqtt_tray_id = 254
            slot_id = 0
        elif ams_id <= 3:
            mqtt_ams_id = ams_id
            mqtt_tray_id = ams_id * 4 + tray_id
            slot_id = tray_id
        elif (_a2l := a2l_lite_wire_ids(ams_id, tray_id)) is not None:
            # A2L AMS-Lite: physical unit 16 + local slot are confirmed; the GLOBAL
            # tray_id this command wants (physical 16*4+slot) is extrapolated (no
            # A2L cali_sel capture yet) — see a2l_lite_wire_ids.
            mqtt_ams_id, slot_id, mqtt_tray_id = _a2l
        elif ams_id >= 128 and ams_id <= 135:
            mqtt_ams_id = ams_id
            mqtt_tray_id = tray_id
            slot_id = 0
        else:
            mqtt_ams_id = ams_id
            mqtt_tray_id = tray_id
            slot_id = 0

        command = {
            "print": {
                "command": "extrusion_cali_sel",
                "cali_idx": cali_idx,
                "filament_id": filament_id,
                "nozzle_diameter": nozzle_diameter,
                "ams_id": mqtt_ams_id,
                "tray_id": mqtt_tray_id,
                "slot_id": slot_id,
                "sequence_id": "0",
            }
        }

        command_json = json.dumps(command)
        logger.info(
            f"[{self.serial_number}] Publishing extrusion_cali_sel: AMS {ams_id}, tray {tray_id}, cali_idx={cali_idx}"
        )
        logger.debug("[%s] extrusion_cali_sel command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        return True

    def extrusion_cali_set(
        self,
        tray_id: int,
        k_value: float,
        nozzle_diameter: str = "0.4",
        nozzle_temp: int = 220,
        filament_id: str = "",
        setting_id: str = "",
        name: str = "",
        cali_idx: int = -1,
    ) -> bool:
        """Directly set K value (pressure advance) for a tray.

        Uses the filaments array format required by current firmware.

        Args:
            tray_id: Global tray ID (ams_id * 4 + slot)
            k_value: Pressure advance K value (e.g., 0.020)
            nozzle_diameter: Nozzle diameter string (e.g., "0.4")
            nozzle_temp: Nozzle temperature for calibration reference
            filament_id: Filament preset ID (e.g., "GFA02")
            setting_id: Setting ID (e.g., "GFSA02_07")
            name: Profile display name
            cali_idx: Calibration index (-1 for new)

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set K value: not connected", self.serial_number)
            return False

        # Was reusing the previous command's id — harmless while nothing
        # correlated on it, but the printer echoes sequence_id back and the
        # K-profile write path now matches acks by it (#2718).
        self._sequence_id += 1

        nozzle_id = f"HS00-{nozzle_diameter}"

        # A2L AMS-Lite: a normalised global tray (24-27) must go out as the
        # physical global (extrapolated 64-67; see a2l_lite_wire_ids). ams_id
        # stays 0 (hardcoded, as for every other unit here).
        wire_tray_id = tray_id
        if 0 <= tray_id <= 253 and (_a2l := a2l_lite_wire_ids(tray_id // 4, tray_id)) is not None:
            wire_tray_id = _a2l[2]

        filament_entry = {
            "ams_id": 0,
            "cali_idx": cali_idx,
            "extruder_id": 0,
            "filament_id": filament_id,
            "k_value": f"{k_value:.6f}",
            "n_coef": "1.400000",
            "name": name,
            "nozzle_diameter": nozzle_diameter,
            "nozzle_id": nozzle_id,
            "setting_id": setting_id,
            "tray_id": wire_tray_id,
        }

        command = {
            "print": {
                "command": "extrusion_cali_set",
                "filaments": [filament_entry],
                "nozzle_diameter": nozzle_diameter,
                "sequence_id": str(self._sequence_id),
            }
        }

        command_json = json.dumps(command)
        logger.info("[%s] Publishing extrusion_cali_set: tray %s, k_value=%s", self.serial_number, tray_id, k_value)
        logger.debug("[%s] extrusion_cali_set command: %s", self.serial_number, command_json)
        self._client.publish(self.topic_publish, command_json, qos=1)
        return True

    def set_timelapse(self, enable: bool) -> bool:
        """Enable or disable timelapse recording.

        Args:
            enable: True to enable, False to disable

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set timelapse: not connected", self.serial_number)
            return False

        command = {"pushing": {"command": "pushall", "sequence_id": "0"}}
        # First send the timelapse setting
        timelapse_cmd = {
            "print": {"command": "gcode_line", "param": f"M981 S{1 if enable else 0} P20000", "sequence_id": "0"}
        }
        self._client.publish(self.topic_publish, json.dumps(timelapse_cmd), qos=1)
        # Request status update
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        logger.info("[%s] Set timelapse %s", self.serial_number, "enabled" if enable else "disabled")
        return True

    def set_liveview(self, enable: bool) -> bool:
        """Enable or disable live view / camera streaming.

        Args:
            enable: True to enable, False to disable

        Returns:
            True if command was sent, False otherwise
        """
        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot set liveview: not connected", self.serial_number)
            return False

        command = {
            "xcam": {"command": "ipcam_record_set", "control": "enable" if enable else "disable", "sequence_id": "0"}
        }
        self._client.publish(self.topic_publish, json.dumps(command), qos=1)
        # Request status update
        pushall = {"pushing": {"command": "pushall", "sequence_id": "0"}}
        self._client.publish(self.topic_publish, json.dumps(pushall), qos=1)
        logger.info("[%s] Set liveview %s", self.serial_number, "enabled" if enable else "disabled")
        return True

    def execute_hms_action(self, print_error: str, action: str, job_id: str | None = None) -> bool:
        """Dispatch the user's choice from the HMS-error modal as a printer command.

        Args:
            print_error: Canonical hex identifier for the fault — 8 chars for the
                32-bit `print_error` path, 16 chars for the 64-bit `hms[]` path
                (HMSError.full_code). Carried through unchanged from the route.
                Converted to its DECIMAL string form for the `ignore` /
                `idle_ignore` commands' `err` field, which is what the firmware
                actually compares against the active fault. The pre-#1869
                hex-string `err` was silently rejected because the firmware was
                being asked to match `"05008051"` against int 0x05008051
                (= 83918929 decimal) — see BambuStudio's
                DeviceManager.cpp:1450-1462 (`command_hms_ignore`) which passes
                `std::to_string(int m_error_code)`.
            action: One of HMSAction's string values.
            job_id: The `subtask_id` snapshotted onto the HMSError at parse-time.
                Required by BambuStudio's `command_hms_ignore` / `command_hms_stop`
                shapes; empty string is the no-job-id sentinel.

        Returns False when the MQTT client is offline or when `action` is unknown
        so the route surfaces it as a 4xx rather than a silent no-op.
        """

        if not self._client or not self.state.connected:
            logger.warning("[%s] Cannot execute HMS action: not connected", self.serial_number)
            return False

        # Always re-push the full state after a command so the modal's underlying
        # status query reflects the new error list (or absence) on the next tick.
        def publish(payload: dict):
            self._client.publish(self.topic_publish, json.dumps(payload), qos=1)
            self._client.publish(
                self.topic_publish, json.dumps({"pushing": {"command": "pushall", "sequence_id": "0"}}), qos=1
            )

        # BambuStudio's `err` field is the DECIMAL string of the error code's int
        # value (DeviceErrorDialog.cpp passes `std::to_string(m_error_code)` to
        # every command_hms_* call). Our route hands us the hex string —
        # convert. Falls back to the raw input if it's not parseable so the
        # firmware can reject it and the route can surface 502 instead of us
        # raising ValueError mid-dispatch.
        try:
            err_decimal = str(int(print_error, 16))
        except ValueError:
            err_decimal = print_error

        def hms_resume():
            # Plain resume — verified against the user's H2D/H2S to leave PAUSE
            # cleanly when "Problem Solved and Resume" is clicked. BambuStudio
            # sends `{command: "resume", err: "<decimal>", param: "reserve",
            # job_id: ...}` from `command_hms_resume`; we kept the simpler
            # shape historically because it works, and changing it without a
            # field test risks regressing a path that the user has confirmed.
            publish(
                {
                    "print": {
                        "command": "resume",
                        "param": "",
                        "sequence_id": "0",
                    }
                }
            )

        def hms_stop():
            # Same as hms_resume — plain shape, confirmed working by the user
            # for "Stop Printing".
            publish(
                {
                    "print": {
                        "command": "stop",
                        "param": "",
                        "sequence_id": "0",
                    }
                }
            )

        def hms_ignore_command():
            # BambuStudio's `command_hms_ignore` (DeviceManager.cpp:1450) —
            # what the "Ignore this and Resume" button actually publishes.
            # Distinct from `idle_ignore`: this command has the firmware
            # suppress the next re-check of the named fault AND resume the
            # paused print in a single operation. The previous Bambuddy code
            # redirected IGNORE_RESUME to a plain `resume`, which is why the
            # wrong-plate HMS came back 1-2 s later: `resume` means "I fixed
            # the problem, re-check normally" so the firmware re-detected the
            # wrong plate and re-paused with the same code (#1869).
            #
            # BambuStudio also routes IGNORE_NO_REMINDER_NEXT_TIME (a.k.a.
            # DONT_REMIND_NEXT_TIME) to this same command — the persistent
            # variant of "don't remind next time" lives on `idle_ignore`'s
            # type=1, not as a separate ignore shape.
            publish(
                {
                    "print": {
                        "command": "ignore",
                        "err": err_decimal,
                        "param": "reserve",
                        "job_id": job_id or "",
                        "sequence_id": "0",
                    }
                }
            )

        def hms_idle_ignore(persistent: bool = False):
            # `idle_ignore` is BambuStudio's "dismiss this warning without
            # resuming" command for non-pause warnings — what
            # `command_hms_idle_ignore` (DeviceManager.cpp:1424) sends.
            # type=0 dismisses once, type=1 suppresses the same warning
            # permanently. Used by NO_REMINDER_NEXT_TIME, which BambuStudio
            # explicitly dispatches via `command_hms_idle_ignore(..., 0)` —
            # NOT via the resume-bearing `ignore` command.
            publish(
                {
                    "print": {
                        "command": "idle_ignore",
                        "err": err_decimal,
                        "type": 1 if persistent else 0,
                        "sequence_id": "0",
                    }
                }
            )

        def ams_control(param: str):
            publish(
                {
                    "print": {
                        "command": "ams_control",
                        "param": param,
                        "sequence_id": "0",
                    }
                }
            )

        def clean_print_error():
            # Matches the existing `clear_hms_errors` shape — Bambu does not
            # expect `print_error` in the body; the command clears whatever
            # error dialog is currently active on the printer.
            publish(
                {
                    "print": {
                        "command": "clean_print_error",
                        "sequence_id": "0",
                    }
                }
            )

        def uiop_close():
            # `err` is the 8-char hex short code (already a string from the
            # frontend), uppercased for consistency with how BambuStudio sends it.
            publish(
                {
                    "system": {
                        "command": "uiop",
                        "name": "print_error",
                        "action": "close",
                        "source": 1,
                        "type": "dialog",
                        "err": print_error.upper(),
                        "sequence_id": "0",
                    }
                }
            )

        match action:
            case (
                HMSAction.RESUME_PRINTING
                | HMSAction.RESUME_PRINTING_DEFECTS
                | HMSAction.RESUME_PRINTING_PROBELM_SOLVED
                | HMSAction.PROBLEM_SOLVED_RESUME
                | HMSAction.FILAMENT_LOAD_RESUME
                | HMSAction.PROCEED
            ):
                hms_resume()

            case HMSAction.STOP_PRINTING:
                hms_stop()

            case HMSAction.IGNORE_RESUME | HMSAction.IGNORE_NO_REMINDER_NEXT_TIME | HMSAction.DONT_REMIND_NEXT_TIME:
                # All three buttons map to BambuStudio's `command_hms_ignore`
                # (DeviceErrorDialog.cpp:596-602). The "no reminder next time"
                # half of IGNORE_NO_REMINDER_NEXT_TIME is the firmware's
                # responsibility — the wire shape is identical.
                hms_ignore_command()

            case HMSAction.NO_REMINDER_NEXT_TIME:
                # BambuStudio's NO_REMINDER_NEXT_TIME branch dispatches
                # `command_hms_idle_ignore` with type=0
                # (DeviceErrorDialog.cpp:588-590). Distinct from the
                # IGNORE_* buttons above: idle_ignore does NOT resume, only
                # dismisses the dialog.
                hms_idle_ignore(persistent=False)

            case HMSAction.FILAMENT_EXTRUDED | HMSAction.DBL_CHECK_DONE:
                ams_control("done")

            case (
                HMSAction.RETRY_FILAMENT_EXTRUDED
                | HMSAction.CONTINUE
                | HMSAction.RETRY_PROBLEM_SOLVED
                | HMSAction.DBL_CHECK_RETRY
            ):
                ams_control("resume")

            case HMSAction.ABORT:
                ams_control("abort")

            case HMSAction.OK_BUTTON:
                clean_print_error()

            case HMSAction.DBL_CHECK_OK:
                clean_print_error()
                uiop_close()

            case HMSAction.DBL_CHECK_RESUME:
                # Plain resume — not HMS-aware, no err/job_id.
                publish(
                    {
                        "print": {
                            "command": "resume",
                            "param": "",
                            "sequence_id": "0",
                        }
                    }
                )

            case HMSAction.REFRESH_NOZZLE:
                publish({"print": {"command": "refresh_nozzle", "sequence_id": "0"}})

            case HMSAction.TURN_OFF_FIRE_ALARM:
                publish({"print": {"command": "buzzer_ctrl", "mode": 0, "sequence_id": "0"}})

            case HMSAction.STOP_DRYING:
                publish({"print": {"command": "auto_stop_ams_dry", "sequence_id": "0"}})

            case HMSAction.DISABLE_PURIFICATION:
                publish({"print": {"command": "close_air_filt", "sequence_id": "0"}})

            case (
                HMSAction.CHECK_ASSISTANT
                | HMSAction.JUMP_TO_LIVEVIEW
                | HMSAction.OK_JUMP_RACK
                | HMSAction.REMOVE_CLOSE_BTN
                | HMSAction.LOAD_VIRTUAL_TRAY
                | HMSAction.CANCLE
                | HMSAction.DBL_CHECK_CANCEL
            ):
                # UI-only actions — the printer's own screen handles these; the
                # modal still surfaces them so the user has parity with Studio.
                pass

            case _:
                logger.warning("[%s] Unknown HMS action '%s'", self.serial_number, action)
                return False

        return True
