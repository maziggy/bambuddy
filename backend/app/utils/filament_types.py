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
"""

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
