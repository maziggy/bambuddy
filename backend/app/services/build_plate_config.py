"""Which build plates the user owns (#1306).

A plate is a slicer base type (``utils.bed_types.BED_TYPES``) plus an optional
pattern, such as a Bambu 3D Effect "Carbon Fiber" sheet, which the slicer treats
as Smooth PEI. The base types are fixed, because matching depends on the name the
slicer writes into the file; the user only says which of them they own. Patterns
are free names the user adds under a type.

Both lists are stored as JSON in the settings table:

* ``build_plate_types``: the owned base-type keys. Until it is saved for the
  first time, the types the user has already printed on count as owned (read
  from the print archive), or every type when there is no history yet.
* ``build_plate_patterns``: ``[{"base_type": ..., "name": ...}]``.

Owning only narrows what the plate pickers offer. Matching never looks at it: a
job keeps the plate its file needs even when that type is unticked.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.utils.bed_types import BED_TYPES, normalize_bed_type, same_pattern

logger = logging.getLogger(__name__)

TYPES_SETTING = "build_plate_types"
PATTERNS_SETTING = "build_plate_patterns"
PATTERN_MAX_LENGTH = 64

# Offered as suggestions when adding a pattern: the Bambu 3D Effect sheets, all
# sliced as Smooth PEI. Any other name is accepted too.
PATTERN_SUGGESTIONS = ("Carbon Fiber", "Starry", "Diamond", "Galaxy")


@dataclass(frozen=True)
class PlatePattern:
    base_type: str
    name: str


@dataclass
class PlateConfig:
    owned_types: list[str]
    patterns: list[PlatePattern] = field(default_factory=list)

    def find_pattern(self, base_type: str, name: str | None) -> PlatePattern | None:
        """The configured pattern of *base_type* called *name* (any case), if any."""
        if not name:
            return None
        return next((p for p in self.patterns if p.base_type == base_type and same_pattern(p.name, name)), None)


def _parse_types(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    try:
        values = json.loads(raw)
    except ValueError:
        logger.warning("Ignoring unreadable %s setting", TYPES_SETTING)
        return None
    return [k for k in BED_TYPES if isinstance(values, list) and k in values]


def _parse_patterns(raw: str | None) -> list[PlatePattern]:
    try:
        values = json.loads(raw or "[]")
    except ValueError:
        logger.warning("Ignoring unreadable %s setting", PATTERNS_SETTING)
        return []
    patterns: list[PlatePattern] = []
    for value in values if isinstance(values, list) else []:
        if isinstance(value, dict) and value.get("base_type") in BED_TYPES and value.get("name"):
            patterns.append(PlatePattern(base_type=value["base_type"], name=str(value["name"])))
    return patterns


async def _types_printed_on(db: AsyncSession) -> list[str]:
    """Base types of the plates the print archive says were used, in display order."""
    from backend.app.models.archive import PrintArchive

    raw = (
        await db.execute(select(PrintArchive.bed_type).where(PrintArchive.bed_type.is_not(None)).distinct())
    ).scalars()
    seen = {normalize_bed_type(value) for value in raw}
    return [k for k in BED_TYPES if k in seen]


async def load_plate_config(db: AsyncSession) -> PlateConfig:
    from backend.app.api.routes.settings import get_setting

    owned = _parse_types(await get_setting(db, TYPES_SETTING))
    if owned is None:
        owned = await _types_printed_on(db) or list(BED_TYPES)
    return PlateConfig(owned_types=owned, patterns=_parse_patterns(await get_setting(db, PATTERNS_SETTING)))


async def save_plate_config(db: AsyncSession, config: PlateConfig) -> None:
    """Store *config*. The caller commits."""
    from backend.app.api.routes.settings import set_setting

    await set_setting(db, TYPES_SETTING, json.dumps([k for k in BED_TYPES if k in config.owned_types]))
    await set_setting(
        db,
        PATTERNS_SETTING,
        json.dumps([{"base_type": p.base_type, "name": p.name} for p in config.patterns]),
    )
