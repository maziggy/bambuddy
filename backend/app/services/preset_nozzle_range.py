"""Resolve a spool's ``slicer_filament`` to the nozzle temperature range its preset carries (#3769).

The spool dialog offers the range as an optional field and prefills it from the
chosen preset, so a spool created from a preset does not need the range typed a
second time. Nothing is stored here: the caller gets the numbers and the dialog
saves them only when the user touches the field.

Three sources, one per kind of reference a spool can hold:

- **local**  -- a numeric id: ``LocalPreset.nozzle_temp_min`` / ``nozzle_temp_max``,
                taken from the preset's resolved content when it was imported.
- **cloud**  -- ``PFUS…`` / ``PFCN…``: the preset's detail from the caller's own
                Bambu Cloud sign-in (the #3519 rule: the signed-in user, or the
                owner of an API key), with its ``inherits`` chain resolved
                against the Orca base profiles.
- **system** -- ``GF…`` / ``GFS…``: Bambu's own filament. The id names the filament
                (``GFA00`` is "Bambu PLA Basic"), and Orca's ``<name> @base``
                profile, with its parents, carries the range.

Anything else, any failed lookup and any preset without a range gives ``None``.
This never raises: no range is an answer, not an error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import is_auth_enabled
from backend.app.core.permissions import Permission
from backend.app.models.local_preset import LocalPreset
from backend.app.models.user import User
from backend.app.services.orca_profiles import extract_core_fields, fetch_and_cache_base_profile, resolve_preset
from backend.app.utils.filament_ids import setting_id_to_filament_id

logger = logging.getLogger(__name__)

RangeSource = Literal["local", "cloud", "system"]

# The keys a preset states its range with. A preset that has neither only has a
# print temperature, which ``extract_core_fields`` would turn into a range of one
# value: that is not the filament's range, so it is not taken for one.
_RANGE_KEYS = ("nozzle_temperature_range_low", "nozzle_temperature_range_high")


@dataclass(frozen=True)
class PresetNozzleRange:
    min: int
    max: int
    source: RangeSource


def _valid_range(low: object, high: object, source: RangeSource) -> PresetNozzleRange | None:
    """The pair as a range, or ``None`` when it is not one (missing, zero, or not ascending)."""
    if not isinstance(low, int) or not isinstance(high, int) or isinstance(low, bool) or isinstance(high, bool):
        return None
    if low <= 0 or high <= low:
        return None
    return PresetNozzleRange(min=low, max=high, source=source)


def _range_from_content(content: dict, source: RangeSource) -> PresetNozzleRange | None:
    """Read the range out of a resolved preset, only when it states one."""
    if not all(content.get(key) for key in _RANGE_KEYS):
        return None
    fields = extract_core_fields(content)
    return _valid_range(fields.get("nozzle_temp_min"), fields.get("nozzle_temp_max"), source)


async def _local_range(db: AsyncSession, preset_id: int) -> PresetNozzleRange | None:
    result = await db.execute(
        select(LocalPreset.nozzle_temp_min, LocalPreset.nozzle_temp_max).where(
            LocalPreset.id == preset_id, LocalPreset.preset_type == "filament"
        )
    )
    row = result.first()
    if row is None:
        return None
    return _valid_range(row.nozzle_temp_min, row.nozzle_temp_max, "local")


async def _cloud_range(db: AsyncSession, user: User | None, setting_id: str) -> PresetNozzleRange | None:
    """The range of a custom Bambu Cloud preset, with the caller's own sign-in.

    Without one there is nothing to ask. The sign-in stored without a user
    belongs to an install with login off, so with login on a caller without a
    user of its own does not borrow it (the rule ``preset_resolver`` applies).
    """
    if user is not None and not user.has_permission(Permission.CLOUD_AUTH.value):
        return None
    if user is None and await is_auth_enabled(db):
        return None

    # Imported here: the cloud routes import this package's services.
    from backend.app.api.routes.cloud import build_authenticated_cloud

    cloud = await build_authenticated_cloud(db, user)
    if cloud is None:
        return None
    try:
        if not cloud.is_authenticated:
            return None
        detail = await cloud.get_setting_detail(setting_id)
    finally:
        await cloud.close()

    # The preset JSON sits under ``setting``; some responses carry it at the top level.
    nested = detail.get("setting") if isinstance(detail, dict) else None
    content = nested if isinstance(nested, dict) else detail
    if not isinstance(content, dict):
        return None
    return _range_from_content(await resolve_preset(content, "filament", db), "cloud")


async def _system_range(db: AsyncSession, setting_id: str) -> PresetNozzleRange | None:
    """The range of a Bambu system filament, through Orca's base profile for its name."""
    # Imported here: the cloud routes import this package's services.
    from backend.app.api.routes.cloud import _BUILTIN_FILAMENT_NAMES

    name = _BUILTIN_FILAMENT_NAMES.get(setting_id_to_filament_id(setting_id))
    if not name:
        return None
    # Orca names the per-printer variants "<name> @BBL X1C" and keeps the one
    # they all inherit as "<name> @base"; a few filaments only have the plain name.
    for candidate in (f"{name} @base", name):
        profile = await fetch_and_cache_base_profile(candidate, "filament", db)
        if profile is not None:
            return _range_from_content(await resolve_preset(profile, "filament", db), "system")
    return None


async def resolve_preset_nozzle_range(
    *,
    db: AsyncSession,
    user: User | None,
    slicer_filament: str | None,
) -> PresetNozzleRange | None:
    """The nozzle temperature range of the preset a spool's ``slicer_filament`` names, or ``None``."""
    sf = (slicer_filament or "").strip()
    if not sf:
        return None
    base_sf = sf.split("_")[0]

    try:
        if sf.isdecimal():
            return await _local_range(db, int(sf))
        if base_sf.startswith(("PFUS", "PFCN")):
            return await _cloud_range(db, user, base_sf)
        if base_sf.startswith("GF"):
            return await _system_range(db, sf)
    except Exception as e:
        # Bambu Cloud or GitHub down, a token Bambu refused: no range, not a failed request.
        logger.warning("Nozzle range of preset %r not resolved: %s", sf, e)
    return None
