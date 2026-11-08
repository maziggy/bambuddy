"""Bambu Studio's global tray numbering, in one place.

A global tray id names one filament source of a printer: what ``tray_now``
reports, what a print's ``ams_mapping`` holds and what Bambuddy compares slots
by. Studio derives it from the unit's AMS type, bits 0-3 of its hex ``info``
(``DevFilaSystem::GetTrayIndexMap``, ``DevAmsType``)::

    AMS / AMS Lite / AMS 2 Pro (1, 2, 3)   ams_id * 4 + slot
    A2L AMS Lite (5)                       24 + slot
    AMS 3H (6, ZMULTI_A)                   28 + slot
    AMS-B (7, ZMULTI_B)                    37 + slot
    AMS-HT (4, N3S, units 128-135)         ams_id
    external input                         its vir_slot id, 254 / 255

On a printer with a direct-connected nozzle rack (the H3C) each hotend has its
own external input, vir_slot 247-255, and input ``v`` feeds hotend ``255 - v``
(``DevAmsTray::get_nozzle_addr_id``); rack AMS slot ``k`` feeds hotend ``k``.

A slot is stored as ``(ams_id, tray_id)``, every external input under ams_id
255 (tray_id 0 = 254, 1 = 255, 2-8 = 247-253). The A2L's AMS Lite is stored as
unit 6, so ``6 * 4 + slot`` is already Studio's 24-27.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum


class AmsType(IntEnum):
    """Bambu Studio's ``DevAmsType``: bits 0-3 of an AMS unit's ``info``."""

    EXT_SPOOL = 0
    AMS = 1
    AMS_LITE = 2
    N3F = 3  # AMS 2 Pro
    N3S = 4  # AMS-HT
    AMS_LITE_MIXED = 5  # the A2L's AMS Lite
    ZMULTI_A = 6  # AMS 3H (H3C)
    ZMULTI_B = 7  # AMS-B


RACK_AMS_TYPES = frozenset({AmsType.ZMULTI_A, AmsType.ZMULTI_B})
RACK_TRAY_BASE: Mapping[AmsType, int] = {AmsType.ZMULTI_A: 28, AmsType.ZMULTI_B: 37}
RACK_SLOT_COUNT = 9  # one slot per hotend of the H3C's rack
AMS_LITE_MIXED_TRAY_BASE = 24
# The H3C reports its AMS 3H as unit 32; tells the type when ``info`` is missing.
AMS3H_UNIT_ID = 32
_AMS3H_TRAY_IDS = range(RACK_TRAY_BASE[AmsType.ZMULTI_A], RACK_TRAY_BASE[AmsType.ZMULTI_A] + RACK_SLOT_COUNT)

AMS_HT_IDS = range(128, 136)
_REGULAR_WIRE_AMS_IDS = range(4)  # the regular units (ams_id << 8) | slot names

EXTERNAL_AMS_ID = 255
EXTERNAL_MAIN_ID = 255  # VIRTUAL_TRAY_MAIN_ID
EXTERNAL_DEPUTY_ID = 254  # VIRTUAL_TRAY_DEPUTY_ID
RACK_EXTERNAL_IDS = range(247, 256)
NO_TRAY = 255  # tray_now: nothing loaded
WIRE_SLOT_NONE = 0xFFFF


def _as_int(value: object) -> int | None:
    """An id field as an int (MQTT sends int or numeric str), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def ams_type_from_info(info: object) -> AmsType | None:
    """The AMS type in a unit's ``info`` (hex string, or an already parsed int), else None."""
    if isinstance(info, bool):
        return None
    if isinstance(info, int):
        raw = info
    elif isinstance(info, str) and info.strip():
        try:
            raw = int(info.strip(), 16)
        except ValueError:
            return None
    else:
        return None
    try:
        return AmsType(raw & 0xF)
    except ValueError:
        return None


def is_rack_ams(ams_type: object) -> bool:
    """Whether ``ams_type`` is a rack AMS (AMS 3H / AMS-B): one slot per hotend."""
    return ams_type in RACK_AMS_TYPES


def is_ams_ht_id(ams_id: object) -> bool:
    """Whether ``ams_id`` is an AMS-HT unit (128-135, one tray each)."""
    return isinstance(ams_id, int) and not isinstance(ams_id, bool) and ams_id in AMS_HT_IDS


def unit_ams_type(ams_id: int, info: object = None) -> AmsType:
    """The AMS type of unit ``ams_id``: from ``info``, else the id (128-135 AMS-HT, 32 AMS 3H, else AMS)."""
    if is_ams_ht_id(ams_id):
        return AmsType.N3S
    from_info = ams_type_from_info(info)
    if from_info is not None and from_info != AmsType.EXT_SPOOL:
        return from_info
    if ams_id >= 128:
        return AmsType.N3S
    if ams_id == AMS3H_UNIT_ID:
        return AmsType.ZMULTI_A
    return AmsType.AMS


def global_tray_id(ams_id: int, slot: int, ams_type: AmsType | int | None = None) -> int:
    """Bambu Studio's global tray id of an AMS slot (``ams_type`` defaults to ``unit_ams_type``)."""
    kind = unit_ams_type(ams_id) if ams_type is None else ams_type
    if kind == AmsType.N3S:
        return ams_id
    if kind == AmsType.AMS_LITE_MIXED:
        return AMS_LITE_MIXED_TRAY_BASE + slot
    if kind in RACK_TRAY_BASE:
        return RACK_TRAY_BASE[AmsType(kind)] + slot
    return ams_id * 4 + slot


def legacy_tray_index(ams_id: int, slot: int) -> int:
    """The firmware's flat ``ams_id * 4 + slot`` (not a Studio id outside a regular AMS).

    What ``M620 R<n>``, ``ams_filament_setting`` / ``extrusion_cali_set``'s
    ``tray_id`` on units 0-3 and the A2L AMS Lite's physical tray (64-67) carry.
    """
    return ams_id * 4 + slot


def unit_label(ams_id: int, ams_type: AmsType | int | None = None) -> str:
    """Short name of AMS unit ``ams_id`` for alerts and logs: "AMS-A", "HT-A", "AMS 3H"."""
    kind = unit_ams_type(ams_id) if ams_type is None else ams_type
    if kind == AmsType.N3S:
        return f"HT-{chr(65 + (ams_id - 128))}"
    if kind == AmsType.ZMULTI_A:
        return "AMS 3H"
    if kind == AmsType.ZMULTI_B:
        return f"AMS {ams_id}"
    return f"AMS-{chr(65 + ams_id)}"


def tray_flag_bit(ams_id: int, slot: int, ams_type: AmsType | int | None = None) -> int | None:
    """Bit of a slot in the ``tray_*_bits`` masks (``DevAmsTray::get_tray_flag_bit``).

    An AMS-HT at ``16 + (ams_id - 128)``, a rack AMS and the AMS Lite at their
    global id, a regular AMS (units 0-15) at ``ams_id * 4 + slot``; None otherwise.
    """
    kind = unit_ams_type(ams_id) if ams_type is None else ams_type
    if kind == AmsType.N3S:
        return 16 + (ams_id - 128) if is_ams_ht_id(ams_id) else None
    if kind == AmsType.AMS_LITE_MIXED or kind in RACK_TRAY_BASE:
        return global_tray_id(ams_id, slot, kind)
    if 0 <= ams_id <= 15:
        return legacy_tray_index(ams_id, slot)
    return None


def is_external_tray(tray_id: object, rack: bool = False) -> bool:
    """Whether global tray ``tray_id`` is an external input: 254/255, and 247-255 on a ``rack`` printer."""
    if not isinstance(tray_id, int) or isinstance(tray_id, bool):
        return False
    if tray_id in (EXTERNAL_DEPUTY_ID, EXTERNAL_MAIN_ID):
        return True
    return rack and tray_id in RACK_EXTERNAL_IDS


def external_hotend(tray_id: int) -> int:
    """The hotend a rack printer's external input ``tray_id`` feeds: ``255 - tray_id``."""
    return EXTERNAL_MAIN_ID - tray_id


def external_slot_index(tray_id: int) -> int:
    """The ``tray_id`` external input ``tray_id`` is stored under: 254 -> 0, 255 -> 1, 247-253 -> 2-8."""
    if tray_id in (EXTERNAL_DEPUTY_ID, EXTERNAL_MAIN_ID):
        return tray_id - EXTERNAL_DEPUTY_ID
    return tray_id - RACK_EXTERNAL_IDS.start + 2


def external_tray_id(slot_index: int) -> int:
    """The global tray id of the external input stored as ``(255, slot_index)``."""
    if slot_index in (0, 1):
        return EXTERNAL_DEPUTY_ID + slot_index
    return RACK_EXTERNAL_IDS.start + slot_index - 2


def _unit_id_and_type(unit: object) -> tuple[int, AmsType] | None:
    """(id, type) of one unit: a raw MQTT dict, an API dict (``ams_type``) or an object."""
    if isinstance(unit, Mapping):
        uid = _as_int(unit.get("id"))
        explicit = unit.get("ams_type")
        info = unit.get("info")
    else:
        uid = _as_int(getattr(unit, "id", None))
        explicit = getattr(unit, "ams_type", None)
        info = getattr(unit, "info", None)
    if uid is None:
        return None
    if isinstance(explicit, int) and not isinstance(explicit, bool) and explicit in AmsType._value2member_map_:
        kind = AmsType(explicit)
        if kind != AmsType.EXT_SPOOL:
            return uid, kind
    return uid, unit_ams_type(uid, info)


@dataclass(frozen=True)
class TrayLayout:
    """How one printer numbers its trays: its AMS units' ``(ams_id, type)`` and whether it is a rack printer.

    ``rack``: per-hotend external inputs 247-255 (a rack AMS, or a reported input 247-253).
    """

    units: tuple[tuple[int, AmsType], ...] = ()
    rack: bool = False

    @classmethod
    def from_units(cls, units: Iterable[object] | None, vt_trays: Iterable[object] | None = None) -> TrayLayout:
        """Layout of an AMS unit list (raw or API units) and a vt_tray list; a unit listed twice counts once."""
        pairs: list[tuple[int, AmsType]] = []
        seen: set[int] = set()
        for unit in units or ():
            pair = _unit_id_and_type(unit)
            if pair is not None and pair[0] not in seen:
                seen.add(pair[0])
                pairs.append(pair)
        rack = any(is_rack_ams(kind) for _, kind in pairs)
        if not rack:
            for vt in vt_trays or ():
                vt_id = _as_int(vt.get("id") if isinstance(vt, Mapping) else getattr(vt, "id", None))
                if vt_id is not None and RACK_EXTERNAL_IDS.start <= vt_id < EXTERNAL_DEPUTY_ID:
                    rack = True
                    break
        return cls(units=tuple(pairs), rack=rack)

    @classmethod
    def ams3h(cls) -> TrayLayout:
        """An H3C's layout (its AMS 3H as unit 32), for when its own AMS data is not at hand."""
        return cls(units=((AMS3H_UNIT_ID, AmsType.ZMULTI_A),), rack=True)

    @classmethod
    def from_state(cls, state: object, ams3h_model: bool = False) -> TrayLayout:
        """Layout of a printer's live state; with no AMS data, ``ams3h_model`` (``printer_models.has_ams3h``) gives ``ams3h()``."""
        raw = getattr(state, "raw_data", None) if state is not None else None
        layout = cls()
        if isinstance(raw, Mapping):
            units = raw.get("ams")
            if isinstance(units, Mapping):  # also stored as the push's {"ams": [...]}
                units = units.get("ams")
            vt_trays = raw.get("vt_tray")
            if isinstance(vt_trays, Mapping):
                vt_trays = [vt_trays]
            layout = cls.from_units(
                units if isinstance(units, list) else None, vt_trays if isinstance(vt_trays, list) else None
            )
        if ams3h_model and not layout.units:
            return cls.ams3h()
        return layout

    def ams_type(self, ams_id: int) -> AmsType:
        """Type of unit ``ams_id``; told from the id when the unit isn't listed."""
        for uid, kind in self.units:
            if uid == ams_id:
                return kind
        return unit_ams_type(ams_id)

    @property
    def rack_units(self) -> tuple[tuple[int, AmsType], ...]:
        """The rack AMS units as ``(ams_id, type)``."""
        return tuple((uid, kind) for uid, kind in self.units if is_rack_ams(kind))

    def global_tray_id(self, ams_id: int, slot: int) -> int:
        """Global tray id of slot ``slot`` of unit ``ams_id``."""
        return global_tray_id(ams_id, slot, self.ams_type(ams_id))

    def is_external(self, tray_id: object) -> bool:
        """Whether global tray ``tray_id`` is one of this printer's external inputs."""
        return is_external_tray(tray_id, self.rack)

    def is_loaded_tray(self, tray_id: int) -> bool:
        """Whether a ``tray_now`` value names a filament source rather than "nothing loaded"."""
        return (
            0 <= tray_id <= 15
            or AMS_LITE_MIXED_TRAY_BASE <= tray_id < AMS_LITE_MIXED_TRAY_BASE + 4
            or is_ams_ht_id(tray_id)
            or tray_id == EXTERNAL_DEPUTY_ID
            or self.rack_slot(tray_id) is not None
            or (self.is_external(tray_id) and tray_id != NO_TRAY)
        )

    def rack_slot(self, tray_id: int) -> tuple[int, int] | None:
        """``(ams_id, slot)`` of a listed rack AMS's slot, else None."""
        for uid, kind in self.rack_units:
            base = RACK_TRAY_BASE[kind]
            if base <= tray_id < base + RACK_SLOT_COUNT:
                return uid, tray_id - base
        return None

    def slot_key(self, tray_id: int) -> tuple[int, int]:
        """The ``(ams_id, tray_id)`` global tray ``tray_id`` is stored under.

        247-255 are only ever external inputs. 28-36 are the AMS 3H's, as in
        Studio, unless the printer lists a rack AMS or a regular unit 7, so a
        stored AMS 3H slot keeps its key without live data.
        """
        if tray_id in RACK_EXTERNAL_IDS:
            return EXTERNAL_AMS_ID, external_slot_index(tray_id)
        if tray_id >= 128:
            return tray_id, 0
        rack = self.rack_slot(tray_id)
        if rack is not None:
            return rack
        if tray_id in _AMS3H_TRAY_IDS and not self.rack_units and not any(uid == 7 for uid, _ in self.units):
            return AMS3H_UNIT_ID, tray_id - _AMS3H_TRAY_IDS.start
        return tray_id // 4, tray_id % 4

    def tray_from_slot_key(self, ams_id: int, tray_id: int) -> int:
        """The global tray id of the stored slot ``(ams_id, tray_id)``."""
        if ams_id == EXTERNAL_AMS_ID:
            return external_tray_id(tray_id)
        if ams_id >= 128:
            return ams_id
        return self.global_tray_id(ams_id, tray_id)

    def raw_slot_to_global(self, raw: int) -> int | None:
        """A rack printer's ``tray_now`` / ``tray_tar`` / ``tray_pre`` (the H3C reports the raw slot 0-8).

        None unless the printer has exactly one rack AMS and ``raw`` is one of its slots.
        """
        racks = self.rack_units
        if len(racks) != 1 or not 0 <= raw < RACK_SLOT_COUNT:
            return None
        uid, kind = racks[0]
        return global_tray_id(uid, raw, kind)


_NO_LAYOUT = TrayLayout()


def decode_wire_slot(value: object, layout: TrayLayout | None = None) -> int | None:
    """Global tray id of a slot the firmware encodes as ``(ams_id << 8) | slot``.

    ``print.mapping``, ``ams.csl`` and the extruder / nozzle ``snow`` fields use
    it; 0xFFFF is "none". Units 0-3 (slot bits ``& 3``), the AMS-HT, a rack AMS
    (the AMS 3H also without a layout), and the external spool: 254/255 give
    254, or 255 for slot 255; a rack printer's inputs 247-255 give their own id.
    None when the value names no tray.
    """
    encoded = _as_int(value)
    if encoded is None or not 0 <= encoded < WIRE_SLOT_NONE:
        return None
    ams_id, slot = encoded >> 8, encoded & 0xFF
    layout = layout or _NO_LAYOUT
    if layout.rack and ams_id in RACK_EXTERNAL_IDS:
        return ams_id
    if ams_id in (EXTERNAL_DEPUTY_ID, EXTERNAL_MAIN_ID):
        return EXTERNAL_MAIN_ID if slot == 255 else EXTERNAL_DEPUTY_ID
    if is_ams_ht_id(ams_id):
        return ams_id
    if ams_id in _REGULAR_WIRE_AMS_IDS:
        return layout.global_tray_id(ams_id, slot & 0x03)
    kind = layout.ams_type(ams_id)
    if is_rack_ams(kind) and slot < RACK_SLOT_COUNT:
        return global_tray_id(ams_id, slot, kind)
    return None


def mapped_trays_with_slot(mapping_raw: object, local_slot: int) -> dict[int, int]:
    """``{global tray: ams_id}`` for the regular AMS / AMS-HT trays of a ``print.mapping`` whose local slot is ``local_slot``.

    Places a bare local ``tray_now`` / ``tray_tar`` (0-3) on a printer with several AMS units.
    """
    found: dict[int, int] = {}
    for value in mapping_raw if isinstance(mapping_raw, list) else []:
        tray = decode_wire_slot(value)
        if tray is None or is_external_tray(tray):
            continue
        ams_id, slot = _NO_LAYOUT.slot_key(tray)
        if slot == local_slot and (ams_id in _REGULAR_WIRE_AMS_IDS or is_ams_ht_id(ams_id)):
            found[tray] = ams_id
    return found
