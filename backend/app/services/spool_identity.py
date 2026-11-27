"""What an inventory spool looks like once it is in an AMS slot (#3590).

One derivation shared by the slot-assign path (``apply_spool_to_slot_via_mqtt``)
and the queue's filament override, so a spool picked as an override matches the
tray the same spool would produce when assigned.
"""

import re
from dataclasses import dataclass

from backend.app.models.spool import Spool
from backend.app.services.slicer_filament_resolver import realign_builtin_filament_id
from backend.app.utils.color_utils import forced_colors_match
from backend.app.utils.filament_ids import GENERIC_FILAMENT_IDS, normalize_slicer_filament
from backend.app.utils.filament_types import (
    canonical_filament_type,
    printer_filament_type,
    support_filament_type,
)

# "GF" + family letter + two digits (GFL05, GFS00) or SUNLU's GFSNL03.
_SPECIFIC_BAMBU_ID = re.compile(r"GF[A-Z]\d{2}|GFSNL\d+")
_GENERIC_IDS = frozenset(GENERIC_FILAMENT_IDS.values())


def spool_tray_type(
    material: str | None,
    *,
    type_override: str | None = None,
    tray_info_idx: str = "",
    name_hint: str = "",
) -> str:
    """The ``tray_type`` a spool's slot carries.

    The material column is reduced to the printer's name for it; a preset's own
    type (``type_override``) wins when there is one; a support material keeps
    the slicer's suffix (#2902, #3559).
    """
    tray_type = printer_filament_type(material)
    if type_override:
        tray_type = printer_filament_type(type_override)
    return support_filament_type(tray_type, tray_info_idx, name_hint)


@dataclass(frozen=True)
class SpoolIdentity:
    type: str
    color: str  # "#RRGGBB"
    tray_info_idx: str  # "" unless a specific Bambu filament id


def specific_bambu_filament_id(slicer_filament: str | None, slicer_filament_name: str | None = None) -> str:
    """The spool's preset as a SPECIFIC Bambu filament id, resolved without network.

    Generic ids (GFL99 ...), material names, cloud/local/Orca ids all give "".
    """
    idx = normalize_slicer_filament((slicer_filament or "").strip())[0]
    if not idx:
        return ""
    # Same static realignment the resolver applies when the name disagrees.
    idx = realign_builtin_filament_id(idx, slicer_filament_name)
    if idx in _GENERIC_IDS or idx.endswith("99") or not _SPECIFIC_BAMBU_ID.fullmatch(idx):
        return ""
    return idx


def spool_override_identity(spool: Spool) -> SpoolIdentity:
    """Identity of ``spool`` for a filament override.

    ``type_override`` (a cloud/local preset's own type) is not available
    offline and is not used here; the material column and the Bambu id decide.
    """
    idx = specific_bambu_filament_id(spool.slicer_filament, spool.slicer_filament_name)
    brands = " ".join(p for p in (spool.brand, spool.material, spool.subtype) if p) or spool.material
    tray_type = spool_tray_type(
        spool.material,
        tray_info_idx=idx,
        name_hint=f"{spool.slicer_filament_name or ''} {brands or ''}",
    )
    color = "#" + (spool.rgba or "FFFFFFFF")[:6].upper()
    return SpoolIdentity(type=tray_type, color=color, tray_info_idx=idx)


def identity_matches(
    a_type: str | None,
    a_color: str | None,
    a_idx: str | None,
    b_type: str | None,
    b_color: str | None,
    b_idx: str | None,
) -> bool:
    """The scheduler's forced-override match: type, colour, and idx when both set."""
    if canonical_filament_type(a_type) != canonical_filament_type(b_type):
        return False
    if not forced_colors_match(a_color, b_color):
        return False
    return not (a_idx and b_idx and a_idx != b_idx)
