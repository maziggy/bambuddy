"""Utility functions for converting between filament_id and setting_id formats, and shared filament constants.

Bambu printers use two ID formats for filament presets:
  - **filament_id** (aka tray_info_idx): e.g. "GFL05", "GFG02", "GFA00"
    Reported by printer firmware (RFID tags, AMS status).
  - **setting_id**: e.g. "GFSL05", "GFSG02", "GFSA00"
    Used by BambuStudio / Bambu Cloud API to resolve presets.

For most official Bambu filaments the only difference is an "S" inserted after
"GF". Two kinds do not follow that rule (#3387):
  - SUNLU: setting_id "GFSNLS03_04", filament_id "GFSNL03" -- a filament_id
    that itself starts with "GFS" plus a letter.
  - About thirty P1/X1-series presets whose filament_id is another one
    altogether ("GFSA05_10", Bambu PLA Matte @BBL P1P, is "GFA01", while every
    other "GFSA05" is Bambu PLA Silk, "GFA05"). Those are listed by full
    setting_id in ``filament_id_tables.SETTING_ID_FILAMENT_IDS``.
User presets (starting with "P") use the same ID in both contexts.
"""

import re

from backend.app.utils.filament_id_tables import ORCA_FILAMENT_IDS, SETTING_ID_FILAMENT_IDS

# SUNLU's ids in Bambu's catalogue: filament_id "GFSNL03", setting_id "GFSNLS03".
_SUNLU_FILAMENT_ID = re.compile(r"GFSNL(\d+)")
_SUNLU_SETTING_ID = re.compile(r"GFSNLS(\d+)")
# OrcaSlicer's own filament ids ("OFMUWNkp"); Orca maps them to Bambu's at the
# printer boundary with the table ORCA_FILAMENT_IDS is generated from.
_ORCA_FILAMENT_ID = re.compile(r"OF[0-9A-Za-z]{6}")

MATERIAL_TEMPS: dict[str, tuple[int, int]] = {
    "PLA": (190, 230),
    "PETG": (220, 260),
    "ABS": (240, 270),
    "ASA": (240, 270),
    "TPU": (200, 240),
    "PA": (260, 290),
    "PC": (250, 280),
    "PVA": (190, 210),
    "PLA-CF": (210, 240),
    "PETG-CF": (240, 270),
    "PA-CF": (270, 300),
}

# Bambu's generic preset per material, for a slot whose spool has no preset of
# its own. Every assignment path and the Configure dialog read this one table;
# a material missing here goes to the printer with an empty tray_info_idx (#3273).
GENERIC_FILAMENT_IDS: dict[str, str] = {
    "PLA": "GFL99",
    "PETG": "GFG99",
    "ABS": "GFB99",
    "ASA": "GFB98",
    "PC": "GFC99",
    "PA": "GFN99",
    "NYLON": "GFN99",
    "TPU": "GFU99",
    "PVA": "GFS99",
    "HIPS": "GFS98",
    "PLA-CF": "GFL98",
    "PETG-CF": "GFG98",
    "PA-CF": "GFN98",
    "PETG HF": "GFG96",
    "PLA SILK": "GFL96",
    "PLA HIGH SPEED": "GFL95",
    "PCTG": "GFG97",
    "PE": "GFP99",
    "PP": "GFP97",
}

# The generics the slot-reuse check replaces instead of carrying forward:
# a slot holding one of these gets the generic for the new spool's own
# material. Deliberately the set from before the table above grew (#3273), so
# a slot set to Generic PLA Silk or PLA High Speed still keeps it for a
# preset-less PLA spool, as it always has.
GENERIC_IDS_REPLACED_ON_REUSE: frozenset[str] = frozenset(
    {
        "GFL99",
        "GFL98",
        "GFG99",
        "GFG98",
        "GFG96",
        "GFB99",
        "GFB98",
        "GFC99",
        "GFN99",
        "GFN98",
        "GFU99",
        "GFS99",
        "GFS98",
    }
)


def filament_id_to_setting_id(filament_id: str) -> str:
    """Convert filament_id → setting_id (e.g. "GFL05" → "GFSL05").

    - Already a setting_id ("GFSL05", "GFSS99", "GFSNLS03") → returned
      unchanged; a support filament_id ("GFS99") still converts, and so does
      SUNLU's ("GFSNL03" → "GFSNLS03").
    - User presets ("P…") → returned unchanged.
    - Empty / unknown → returned unchanged.
    """
    if not filament_id:
        return filament_id

    # User presets start with "P" - leave unchanged
    if filament_id.startswith("P"):
        return filament_id

    sunlu = _SUNLU_FILAMENT_ID.fullmatch(filament_id)
    if sunlu:
        return f"GFSNLS{sunlu.group(1)}"

    # Official Bambu presets: GFx## -> GFSx##
    if filament_id.startswith("GF") and len(filament_id) >= 4:
        # Already a setting_id: "GFS" then the family letter ("GFSL05").
        # Support filaments are family S, so their filament_id starts with
        # "GFS" too -- but a digit follows it ("GFS99" is Generic PVA, whose
        # setting_id is "GFSS99").
        if filament_id[2] == "S" and filament_id[3].isalpha():
            return filament_id
        return f"GFS{filament_id[2:]}"

    return filament_id


def setting_id_to_filament_id(setting_id: str) -> str:
    """Convert setting_id → filament_id (e.g. "GFSL05_07" → "GFL05"), with
    any version suffix stripped.

    - A setting_id Bambu gives a filament_id the rule does not lead to → that
      filament_id, looked up by the full id, suffix included (#3387).
    - Already a filament_id ("GFL05", a support one like "GFS99", or SUNLU's
      "GFSNL03") → returned unchanged.
    - User presets ("P…") → returned without the suffix.
    - Empty / unknown → returned without the suffix.
    """
    if not setting_id:
        return setting_id

    listed = SETTING_ID_FILAMENT_IDS.get(setting_id)
    if listed:
        return listed
    return derived_filament_id(setting_id.split("_")[0])


def derived_filament_id(base: str) -> str:
    """The filament_id the naming rule gives for a suffix-free setting_id,
    without the table of exceptions. ``scripts/generate_filament_id_tables.py``
    lists exactly the presets where this differs from Bambu's own id."""
    sunlu = _SUNLU_SETTING_ID.fullmatch(base)
    if sunlu:
        return f"GFSNL{sunlu.group(1)}"
    if _SUNLU_FILAMENT_ID.fullmatch(base):
        return base

    # Setting_id format: GFSx## -> GFx##  (remove the "S"). A digit after
    # "GFS" means a support filament_id, not a setting_id (see above).
    if base.startswith("GFS") and len(base) >= 5 and base[3].isalpha():
        return f"GF{base[3:]}"

    return base


def orca_to_bambu_filament_id(filament_id: str) -> str:
    """The Bambu filament_id for an OrcaSlicer one ("OFMUWNkp" → "GFSNL03").

    OrcaSlicer's system presets carry ids of their own; the printer only knows
    Bambu's. An Orca id the table does not know returns ``""`` -- sent to the
    printer it would resolve nowhere. Any other id is returned unchanged.
    """
    if _ORCA_FILAMENT_ID.fullmatch(filament_id or ""):
        return ORCA_FILAMENT_IDS.get(filament_id, "")
    return filament_id


def normalize_slicer_filament(slicer_filament: str | None) -> tuple[str, str]:
    """Normalize a slicer_filament value into (tray_info_idx, setting_id).

    The slicer_filament field on a spool can be stored in either format:
      - filament_id: "GFL05"  (from RFID tag scan)
      - setting_id:  "GFSL05" or "GFSL05_07"  (from cloud preset picker)

    Returns (tray_info_idx, setting_id) with version suffixes stripped.
    """
    raw = slicer_filament or ""
    if not raw:
        return ("", "")

    # Strip version suffix (e.g. "GFSL05_07" → "GFSL05"); the filament_id is
    # read from the full id, since the suffix can decide it (#3387).
    base = raw.split("_")[0] if "_" in raw else raw

    tray_info_idx = setting_id_to_filament_id(raw)
    sid = filament_id_to_setting_id(base)

    return (tray_info_idx, sid)
