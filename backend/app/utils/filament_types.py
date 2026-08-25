"""Filament type equivalence — one answer to "will this spool do for that
requirement?", shared by everyone who asks it.

``print_scheduler`` decides that question at dispatch; ``pipeline_eligibility``
predicts the same answer before the queue runs. They used to carry a copy each,
and the copies drifted until they disagreed in both directions: eligibility
passed jobs the matcher then failed on type (it aliased ``PLA Basic`` to
``PLA``, the matcher did not) and flagged jobs the matcher handled fine (the
matcher groups ``PA12-CF`` with ``PA-CF``, eligibility did not). A predictor
that disagrees with the thing it predicts is wrong whichever way it leans, so
both now read from here.

Deliberately *not* shared with ``PrintScheduler._normalize_filament_type``,
which reduces a tray type to a drying-preset key. "Which drying profile?" is a
genuinely different question with a different answer — PLA Silk dries like PLA
but does not print like it — so folding those together would be the wrong kind
of tidy.

Two more readings of a filament type live here for the same reason. Writing one
into an AMS slot needs the name the printer knows rather than the one on the
spool (``printer_filament_type``), and two guards need to agree on when a value
is a material name rather than a preset the printer can resolve
(``is_material_name``). Both were open-coded and inconsistent before #2902.
"""

import re

from backend.app.utils.filament_ids import GENERIC_FILAMENT_IDS, MATERIAL_TEMPS

# Types within a group are interchangeable on the printer side; Bambu Lab
# firmware treats them as the same material. The first entry is canonical.
#
# Product variants are deliberately absent. "PLA Silk" is not substitutable
# for "PLA Basic" the way PA12-CF is for PA-CF — different temperature, flow
# and finish, so standing one in for the other hands back a print nobody asked
# for. It rarely arises anyway: the printer reports the generic material in
# ``tray_type`` and the product name in ``tray_sub_brands``, so what reaches
# this function is a bare "PLA".
FILAMENT_TYPE_GROUPS: list[list[str]] = [
    ["PA-CF", "PA12-CF", "PAHT-CF"],
]

_EQUIV_MAP: dict[str, str] = {}
for _group in FILAMENT_TYPE_GROUPS:
    _canonical = _group[0].upper()
    for _type in _group:
        _EQUIV_MAP[_type.upper()] = _canonical


def canonical_filament_type(ftype: str | None) -> str:
    """Return the canonical type name used for equivalence matching.

    Deliberately does *not* strip surrounding whitespace, so this is byte-for-byte
    the rule the dispatch matcher already applied, and the same one
    ``canonicalFilamentType`` applies in ``frontend/src/utils/amsHelpers.ts``.

    Stripping looks like an improvement — it would let a padded " PETG " match
    "PETG" — but it also collapses a whitespace-only ``tray_type`` to "", and a
    3MF whose filament element carries no ``type`` attribute yields "" as well
    (``filament_requirements``). The two would then compare equal, so a
    requirement with no declared type would start matching a tray whose type is
    junk, where today it correctly reports the slot unmapped. Handling padded
    types is worth doing on its own terms, with that case addressed; it is not
    worth smuggling in here.
    """
    upper = (ftype or "").upper()
    return _EQUIV_MAP.get(upper, upper)


def filament_types_compatible(a: str | None, b: str | None) -> bool:
    """Whether two filament types may stand in for one another."""
    return canonical_filament_type(a) == canonical_filament_type(b)


# ---------------------------------------------------------------------------
# Printer-side material names
# ---------------------------------------------------------------------------

# What a Bambu printer and the slicers accept in an AMS slot's ``tray_type``.
# Grounded in the catalogues this repo already carries: ``filament_fields.json``
# is the list Bambuddy itself offers when a preset is created, so every value in
# it has to appear here -- ``TestTheMaterialsBambuddyOffers`` fails if one does
# not. On top of that, every "Generic X" entry in
# ``cloud._BUILTIN_FILAMENT_NAMES`` names a real type, the "Bambu X" entries add
# the composites, and the frontend's ``parsePresetName`` list contributes
# PEEK / PEI / PC-CF / PC-ABS.
#
# A filled or foamed variant is a type of its own, not a flavour of the base
# material: PLA-AERO is a foaming PLA and PLA-GF a glass-filled one, and a slot
# that reduces either to "PLA" invites a plain PLA plate onto filament that
# will not print it. Four of the ones the dropdown offers were missing when
# #2902 first landed and were being reduced exactly that way, ASA-AERO -- which
# only the cloud catalogue names (GFB02) -- with them. See the issue thread,
# where @doncaruana caught PLA Aero.
#
# Product lines, by contrast, are deliberately absent. "PLA Matte", "PETG HF"
# and "eSUN PLA+" are things you buy, not types the firmware knows -- they
# belong in ``tray_sub_brands``, which is where Bambu itself puts them.
_PRINTER_TYPES: tuple[str, ...] = (
    # Order within a length matters for ties: "PLA/PHA" must read as PLA.
    "PLA-AERO",
    "ASA-AERO",
    "PAHT-CF",
    "PA12-CF",
    "PETG-CF",
    "PPS-CF",
    "PPS-GF",
    "PPA-CF",
    "PPA-GF",
    "PLA-CF",
    "PLA-GF",
    "PA6-CF",
    "PA6-GF",
    "ABS-GF",
    "ASA-CF",
    "ASA-GF",
    "PET-CF",
    "PC-ABS",
    "PA-CF",
    "PC-CF",
    "PP-CF",
    "PP-GF",
    "PE-CF",
    "PCTG",
    "PETG",
    "BVOH",
    "HIPS",
    "PEEK",
    "PLA",
    "PHA",
    "ABS",
    "ASA",
    "TPU",
    "PVA",
    "PPS",
    "EVA",
    "PEI",
    "PC",
    "PA",
    "PP",
    "PE",
)

# Names that are a material by another name. ``GENERIC_FILAMENT_IDS`` already
# points NYLON and PA at the same generic id, so they are the same thing to
# everything downstream of here.
_TYPE_ALIASES: dict[str, str] = {
    "NYLON": "PA",
}

_PRINTER_TYPE_SET = frozenset(_PRINTER_TYPES)

# Longest first, so "PLA-CF" is recognised before the "PLA" inside it. Sorted
# stably, so the declaration order above still decides same-length ties.
_TYPES_LONGEST_FIRST: tuple[str, ...] = tuple(sorted((*_PRINTER_TYPES, *_TYPE_ALIASES), key=len, reverse=True))

# Splits a material name into words while keeping "-" and "+" inside them:
# "PLA-CF" is one word, "PLA/PHA" is two, and the "+" of "PLA+" stays attached
# so the prefix rule below can see a non-letter after the type.
_WORDS = re.compile(r"[^A-Z0-9+\-]+")


def _word_names_type(word: str, candidate: str) -> bool:
    """Whether one word of a material name says "this is a ``candidate``"."""
    if word == candidate:
        return True
    # Two-letter types are too short to recognise inside a longer word. "PA" is
    # nylon and "Pastel" is not, and there is no reading of the rules below that
    # separates them -- so PA, PC, PE and PP are taken only as a word of their
    # own. A spool whose material really is bare nylon still says so.
    if len(candidate) < 3:
        return False
    # A prefix counts only when a non-letter follows it -- "PLA+" and "PETG-HS"
    # are the type, "PLASTIC" is a word that merely starts like one.
    if word.startswith(candidate) and not word[len(candidate)].isalpha():
        return True
    # A suffix needs no such guard: "HTPLA" and "rPETG" are how vendors write
    # their own PLA and PETG, and nothing else ends in a material name.
    return word.endswith(candidate)


def printer_filament_type(material: str | None) -> str:
    """Reduce a spool's material to the name an AMS slot can carry.

    Bambuddy lets a spool's material be anything -- typed by hand, synced from
    Spoolman, or picked from the colour catalogue, whose material column is the
    vendor's product line ("PLA+", "HTPLA", "PolyTerra PLA"). Every assignment
    path then wrote that string into the slot's ``tray_type``, and a slot whose
    type is "PLA+" satisfies nothing that asks for PLA: not the slicer, and not
    Bambuddy's own dispatch matcher, which compares the printer's reported
    ``tray_type`` to the 3MF's declared type as plain equality (issue #2902).

    Returns the material name unchanged when it cannot be placed. That is the
    important half of the contract -- an unrecognised material is left exactly
    as it arrives rather than guessed at, so this can only ever fix a slot that
    was already wrong.
    """
    text = (material or "").strip()
    if not text:
        return ""

    upper = text.upper()
    if upper in _PRINTER_TYPE_SET:
        return upper
    if upper in _TYPE_ALIASES:
        return _TYPE_ALIASES[upper]

    words = [w for w in _WORDS.split(upper) if w]

    # A hyphenated type written with a space is still that type. The table
    # spells it "PLA-AERO" because that is how the preset dropdown and the
    # slicers spell it, while a spool says "PLA Aero" and a preset name says
    # "Bambu PLA Aero" -- and the word rules below would find only the "PLA"
    # in those and hand back a slot that lies about what is loaded.
    #
    # Adjacent words only, and only when the join is a type exactly. The prefix
    # and suffix rules are deliberately not applied across a space: "Support
    # for PLA" would otherwise start reading as a type by its tail.
    for first, second in zip(words, words[1:], strict=False):
        joined = f"{first}-{second}"
        if joined in _PRINTER_TYPE_SET:
            return joined
        if joined in _TYPE_ALIASES:
            return _TYPE_ALIASES[joined]

    for candidate in _TYPES_LONGEST_FIRST:
        if any(_word_names_type(w, candidate) for w in words):
            return _TYPE_ALIASES.get(candidate, candidate)

    return text


def nozzle_temp_range(material: str | None, tray_type: str | None) -> tuple[int, int]:
    """The nozzle range to send with a slot, given a spool's material and the
    type the slot will carry.

    The spool's own wording leads, as it does for the filament-id lookup, so a
    material that has its own entry keeps it. The reduced type answers for
    everything else -- and when the reduced type is a filled or foamed variant,
    the base material answers for that, because ``MATERIAL_TEMPS`` carries
    eleven entries and none of them is ASA-GF. Without that last step an
    ASA-GF spool took the 200/240 catch-all and would not have extruded;
    "ASA" gives it ASA's 240/270 (#2902).
    """
    base = (tray_type or "").split("-")[0]
    for key in (material, tray_type, base):
        temps = MATERIAL_TEMPS.get((key or "").upper().strip())
        if temps:
            return temps
    return (200, 240)


# Both tables are keyed by material, so their keys are exactly the set of names
# that are a material rather than a preset the printer can resolve.
_MATERIAL_NAMES = frozenset(MATERIAL_TEMPS) | frozenset(GENERIC_FILAMENT_IDS)

# "GF" + letter + digits for Bambu's own presets, "P" + hex for local and cloud
# ones -- the shapes ``slicer_filament_resolver`` documents. Matched loosely on
# purpose: this only ever has to be sure a value is NOT a bare material name.
_PRESET_ID_SHAPE = re.compile(r"^(?:GF|P)[A-Za-z0-9_]*$")


def is_material_name(value: str | None) -> bool:
    """Whether a candidate filament id is really just a material name.

    Two places ask this, and have to answer it the same way: the slicer-filament
    resolver, which throws such a value away so its caller's generic fallback
    can rescue the slot, and the slot-reuse check, which will not carry one
    forward. Both compared against the table above and so saw only bare types --
    but "PLA+" is exactly as unusable a filament id as the "PLA" they already
    rejected, and reaching the printer is how a product line ended up in the
    field the calibration table is keyed by (issue #2902).

    A value shaped like a preset id is never a material name, whatever letters
    it happens to end in. Reading "GFPLA" as PLA would discard it, and what the
    user loses when that happens is the calibrated preset in the slot.
    """
    text = (value or "").strip()
    if not text:
        return False
    if text.upper() in _MATERIAL_NAMES:
        return True
    if _PRESET_ID_SHAPE.match(text):
        return False
    reduced = printer_filament_type(text).upper()
    if reduced in _MATERIAL_NAMES:
        return True
    # A filled or foamed variant is its base material by another name, and the
    # base is what decides. Saying yes means the caller throws this value away
    # and rescues the slot from its generic-material fallback -- so the answer
    # has to be no when that fallback has nothing to offer, or the slot goes out
    # with no filament id at all, which is worse than the junk it replaced.
    # ``ABS-GF`` reduces to a generic ABS the printer can resolve; ``PPS-CF``
    # reduces to nothing, so it is left for the caller to send as it stands.
    #
    # This is also what keeps a type added to the table above from silently
    # changing the answer: "PLA-AERO" read as a material name when the table
    # had no row for it, and it still does (#2902).
    return reduced.split("-")[0] in _MATERIAL_NAMES
