"""The finish a filament's wording names: Basic, Matte, Silk, ...

A slot preset of one finish is not the preset of another, even for the same
material: a Silk spool must not take a slot's Matte preset. The same words pick
the generic preset for a finish that has one (Generic PLA Silk, PETG HF).
"""

from backend.app.utils.filament_ids import GENERIC_FILAMENT_IDS

# Finishes Bambu names in a sub-brand ("PLA Matte", "PLA Silk+", "PETG HF").
KNOWN_FINISHES = (
    "basic",
    "matte",
    "silk",
    "glow",
    "marble",
    "metal",
    "sparkle",
    "galaxy",
    "translucent",
    "tough",
    "wood",
    "aero",
    "lite",
    "gradient",
    "high speed",
    "dual color",
    "hf",
)


def finishes(text: str | None) -> list[str]:
    """The known finishes named in ``text``, "+" and "-" read as spaces
    ("Silk+", "Silk Multi-Color")."""
    words = f" {(text or '').lower().replace('+', ' ').replace('-', ' ')} "
    return [finish for finish in KNOWN_FINISHES if f" {finish} " in words]


def different_finish(spool_text: str | None, slot_text: str | None) -> bool:
    """True when both name a finish and they share none."""
    spool_finishes = set(finishes(spool_text))
    slot_finishes = set(finishes(slot_text))
    return bool(spool_finishes and slot_finishes and not spool_finishes & slot_finishes)


def finish_generic_id(material: str | None, text: str | None) -> str:
    """The generic preset for ``material`` in a finish ``text`` names, e.g.
    PLA + "Silk+" -> GFL96 (Generic PLA Silk); "" when there is none."""
    base = (material or "").upper().strip()
    for finish in finishes(text):
        generic = GENERIC_FILAMENT_IDS.get(f"{base} {finish.upper()}")
        if generic:
            return generic
    return ""
