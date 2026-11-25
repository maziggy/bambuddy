"""Profile defaults per product: what a new spool starts with (#1326).

A default (``models.filament_profile_default``) holds the slicer preset, the
per-model preset overrides and the K profiles for one product, for all its
colours (``color_name`` "") or for one. ``resolve_profile_default`` merges the
two levels, the colour row winning per preset and per hotend, and the
``fill_*`` helpers copy the result onto a spool -- filling only what the spool
does not have yet, so they are safe on a create path that was handed some
profiles already and on spools that have been in stock for months.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import delete, inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.filament_profile_default import (
    FilamentProfileDefault,
    FilamentProfileDefaultKProfile,
    FilamentProfileDefaultPreset,
)
from backend.app.models.spool import Spool
from backend.app.models.spool_filament_preset import SpoolFilamentPreset, SpoolmanFilamentPreset
from backend.app.models.spool_k_profile import SpoolKProfile
from backend.app.models.spoolman_k_profile import SpoolmanKProfile

logger = logging.getLogger(__name__)

ProductKey = tuple[str, str, str, str]  # (brand, material, subtype, color_name)

K_FIELDS = ("printer_id", "extruder", "nozzle_diameter", "nozzle_type", "k_value", "name", "cali_idx", "setting_id")
PRESET_FIELDS = ("printer_model", "nozzle_diameter", "slicer_filament", "slicer_filament_name")


def product_key(*, brand: str | None, material: str | None, subtype: str | None, color_name: str | None) -> ProductKey:
    """The key a spool's product fields map to: trimmed, "" for missing."""
    return ((brand or "").strip(), (material or "").strip(), (subtype or "").strip(), (color_name or "").strip())


def match_key(key: ProductKey) -> str:
    """What ``FilamentProfileDefault.match_key`` stores for a product key, and
    what every comparison of product fields here goes through."""
    return "\x1f".join(part.lower() for part in key)


def hotend_key(printer_id: int, extruder: int | None, nozzle_diameter: str | None) -> tuple[int, int, str]:
    """One hotend: what a spool holds at most one K profile for. The diameter
    falls back to "0.4" as the K-profile columns do."""
    return (printer_id, int(extruder or 0), (nozzle_diameter or "").strip() or "0.4")


def k_key(row: dict) -> tuple[int, int, str]:
    return hotend_key(row["printer_id"], row.get("extruder"), row.get("nozzle_diameter"))


def preset_key(row: dict) -> tuple[str, str]:
    return (row.get("printer_model") or "", row.get("nozzle_diameter") or "")


# Spool.slicer_filament / _name are narrower than the default's columns, which
# follow the Spoolman schema. A preset id is never cut short: one that does not
# fit is not filled in at all.
_SPOOL_SLICER_FILAMENT_LEN = 50
_SPOOL_SLICER_FILAMENT_NAME_LEN = 100


@dataclass
class ResolvedProfiles:
    """A product's default, both levels merged."""

    slicer_filament: str | None = None
    slicer_filament_name: str | None = None
    presets: list[dict] = field(default_factory=list)
    k_profiles: list[dict] = field(default_factory=list)
    # The rows it came from, all-colours first.
    default_ids: list[int] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.slicer_filament or self.presets or self.k_profiles)


def _with_children():
    return (
        selectinload(FilamentProfileDefault.presets),
        selectinload(FilamentProfileDefault.k_profiles),
    )


async def find_default(db: AsyncSession, key: ProductKey) -> FilamentProfileDefault | None:
    """The default stored for exactly this key, case-insensitively."""
    result = await db.execute(
        select(FilamentProfileDefault)
        .options(*_with_children())
        .where(FilamentProfileDefault.match_key == match_key(key))
    )
    return result.scalars().first()


async def resolve_profile_default(
    db: AsyncSession,
    *,
    brand: str | None,
    material: str | None,
    subtype: str | None,
    color_name: str | None,
) -> ResolvedProfiles | None:
    """The profiles a new spool of this product gets, or None when there is no default.

    The all-colours row is the base; the row for the spool's colour replaces
    its slicer preset when it has one, and each of its presets and K profiles
    replaces the base's for the same (model, nozzle) or hotend.
    """
    key = product_key(brand=brand, material=material, subtype=subtype, color_name=color_name)
    if not key[1]:
        return None
    rows = [await find_default(db, (*key[:3], ""))]
    if key[3]:
        rows.append(await find_default(db, key))
    rows = [r for r in rows if r is not None]
    if not rows:
        return None

    resolved = ResolvedProfiles()
    presets: dict[tuple[str, str], dict] = {}
    k_profiles: dict[tuple[int, int, str], dict] = {}
    for row in rows:
        resolved.default_ids.append(row.id)
        if row.slicer_filament:
            resolved.slicer_filament = row.slicer_filament
            resolved.slicer_filament_name = row.slicer_filament_name
        for p in row.presets:
            preset = {name: getattr(p, name) for name in PRESET_FIELDS}
            presets[preset_key(preset)] = preset
        for k in row.k_profiles:
            k_profile = {name: getattr(k, name) for name in K_FIELDS}
            k_profiles[k_key(k_profile)] = k_profile
    resolved.presets = list(presets.values())
    resolved.k_profiles = list(k_profiles.values())
    return resolved


async def save_default(
    db: AsyncSession,
    key: ProductKey,
    *,
    slicer_filament: str | None,
    slicer_filament_name: str | None,
    presets: list[dict],
    k_profiles: list[dict],
    keep_printer_ids: set[int] | None = None,
) -> FilamentProfileDefault:
    """Create or replace the default for ``key``. The caller commits.

    ``keep_printer_ids``: K profiles of these printers are left as they are
    instead of being replaced -- the printers a caller limited to some of them
    (#1727) cannot see, and so cannot have meant to clear.
    """
    row = await find_default(db, key)
    if row is None:
        brand, material, subtype, color_name = key
        row = FilamentProfileDefault(
            match_key=match_key(key),
            brand=brand,
            material=material,
            subtype=subtype,
            color_name=color_name,
            presets=[],
            k_profiles=[],
        )
        db.add(row)
        await db.flush()
    row.slicer_filament = slicer_filament or None
    row.slicer_filament_name = (slicer_filament_name or None) if slicer_filament else None

    await db.execute(
        delete(FilamentProfileDefaultPreset).where(FilamentProfileDefaultPreset.profile_default_id == row.id)
    )
    k_delete = delete(FilamentProfileDefaultKProfile).where(FilamentProfileDefaultKProfile.profile_default_id == row.id)
    if keep_printer_ids:
        k_delete = k_delete.where(FilamentProfileDefaultKProfile.printer_id.not_in(keep_printer_ids))
    await db.execute(k_delete)
    # The deletes before the inserts, or the UNIQUE constraints see both.
    await db.flush()
    kept = {
        hotend_key(k.printer_id, k.extruder, k.nozzle_diameter)
        for k in row.k_profiles
        if keep_printer_ids and k.printer_id in keep_printer_ids
    }
    for p in presets:
        db.add(FilamentProfileDefaultPreset(profile_default_id=row.id, **{n: p.get(n) for n in PRESET_FIELDS}))
    for k in k_profiles:
        if k_key(k) in kept:
            continue
        db.add(FilamentProfileDefaultKProfile(profile_default_id=row.id, **{n: k.get(n) for n in K_FIELDS}))
    await db.flush()
    db.expire(row, ["presets", "k_profiles", "updated_at"])
    return row


async def load_default(db: AsyncSession, default_id: int) -> FilamentProfileDefault | None:
    result = await db.execute(
        select(FilamentProfileDefault)
        .options(*_with_children())
        .where(FilamentProfileDefault.id == default_id)
        .execution_options(populate_existing=True)
    )
    return result.scalars().first()


async def fill_internal_spool(
    db: AsyncSession,
    spool: Spool,
    *,
    include_slicer: bool = True,
    resolved: ResolvedProfiles | None = None,
) -> bool:
    """Copy the product's default onto an internal spool where it has nothing.

    Never replaces: a slicer preset, a preset for a (model, nozzle) or a K
    profile for a hotend the spool already has stays. ``include_slicer=False``
    leaves the spool's own preset alone even when empty. The caller flushes
    or commits. Returns whether anything was added.
    """
    if resolved is None:
        resolved = await resolve_profile_default(
            db, brand=spool.brand, material=spool.material, subtype=spool.subtype, color_name=spool.color_name
        )
    if resolved is None or resolved.is_empty:
        return False
    changed = False
    if (
        include_slicer
        and resolved.slicer_filament
        and not spool.slicer_filament
        and len(resolved.slicer_filament) <= _SPOOL_SLICER_FILAMENT_LEN
    ):
        spool.slicer_filament = resolved.slicer_filament
        spool.slicer_filament_name = (resolved.slicer_filament_name or "")[:_SPOOL_SLICER_FILAMENT_NAME_LEN] or None
        changed = True

    have_presets: set[tuple[str, str]] = set(
        (
            await db.execute(
                select(SpoolFilamentPreset.printer_model, SpoolFilamentPreset.nozzle_diameter).where(
                    SpoolFilamentPreset.spool_id == spool.id
                )
            )
        ).all()
    )
    for p in resolved.presets:
        if preset_key(p) in have_presets:
            continue
        db.add(SpoolFilamentPreset(spool_id=spool.id, **p))
        changed = True

    # The RFID assign that follows an auto-create reads the spool's in-memory
    # list, which create_spool_from_tray initialised: rows only added to the
    # session would miss that assign's K profile.
    loaded = "k_profiles" not in sa_inspect(spool).unloaded
    if loaded:
        have_k = {hotend_key(k.printer_id, k.extruder, k.nozzle_diameter) for k in spool.k_profiles}
    else:
        have_k = {
            hotend_key(pid, ext, dia)
            for pid, ext, dia in (
                await db.execute(
                    select(SpoolKProfile.printer_id, SpoolKProfile.extruder, SpoolKProfile.nozzle_diameter).where(
                        SpoolKProfile.spool_id == spool.id
                    )
                )
            ).all()
        }
    for k in resolved.k_profiles:
        if k_key(k) in have_k:
            continue
        row = SpoolKProfile(spool_id=spool.id, **k)
        if loaded:
            spool.k_profiles.append(row)
        else:
            db.add(row)
        have_k.add(k_key(k))
        changed = True
    return changed


async def fill_spoolman_rows(db: AsyncSession, spoolman_spool_id: int, resolved: ResolvedProfiles) -> bool:
    """The Bambuddy-side half of ``fill_internal_spool`` for a Spoolman spool:
    preset overrides and K profiles, which live here keyed by the remote id.
    The spool's own slicer preset lives in Spoolman, see the callers. The
    caller commits."""
    changed = False
    have_presets = set(
        (
            await db.execute(
                select(SpoolmanFilamentPreset.printer_model, SpoolmanFilamentPreset.nozzle_diameter).where(
                    SpoolmanFilamentPreset.spoolman_spool_id == spoolman_spool_id
                )
            )
        ).all()
    )
    for p in resolved.presets:
        if preset_key(p) in have_presets:
            continue
        db.add(SpoolmanFilamentPreset(spoolman_spool_id=spoolman_spool_id, **p))
        changed = True
    have_k = {
        hotend_key(pid, ext, dia)
        for pid, ext, dia in (
            await db.execute(
                select(SpoolmanKProfile.printer_id, SpoolmanKProfile.extruder, SpoolmanKProfile.nozzle_diameter).where(
                    SpoolmanKProfile.spoolman_spool_id == spoolman_spool_id
                )
            )
        ).all()
    }
    for k in resolved.k_profiles:
        if k_key(k) in have_k:
            continue
        db.add(SpoolmanKProfile(spoolman_spool_id=spoolman_spool_id, **k))
        have_k.add(k_key(k))
        changed = True
    return changed


async def fill_auto_added_spoolman_spool(spool: dict, client=None, *, include_slicer: bool = False) -> None:
    """The profile default for a spool the AMS sync just added to Spoolman.

    Its preset overrides and K profiles, and -- with ``include_slicer``, for a
    tray whose tag names no preset -- the default's own preset, written to the
    spool's extra dict through ``client``. A tag's preset is left alone, as for
    an internal RFID spool. In a session of its own because the sync's callers
    do not commit, and a failure is only logged: the spool is in Spoolman
    either way.
    """
    import json

    from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool
    from backend.app.core.database import async_session
    from backend.app.services.spool_filament_preset import forget_spoolman_spool_own_preset

    try:
        mapped = _map_spoolman_spool(spool)
        async with async_session() as db:
            resolved = await resolve_profile_default(
                db,
                brand=mapped["brand"],
                material=mapped["material"],
                subtype=mapped["subtype"],
                color_name=None if mapped["color_name_is_synthesized"] else mapped["color_name"],
            )
            if resolved is None:
                return
            if await fill_spoolman_rows(db, mapped["id"], resolved):
                await db.commit()
        if include_slicer and client is not None and resolved.slicer_filament and not mapped["slicer_filament"]:
            extra = {"bambu_slicer_filament": json.dumps(resolved.slicer_filament)}
            if resolved.slicer_filament_name:
                extra["bambu_slicer_filament_name"] = json.dumps(resolved.slicer_filament_name)
            for name in extra:
                await client.ensure_extra_field(name)
            await client.merge_spool_extra(mapped["id"], extra)
            forget_spoolman_spool_own_preset(mapped["id"])
    except Exception as exc:  # noqa: BLE001 — never fail the AMS sync over a default
        logger.warning("Profile default for auto-added Spoolman spool %s not applied: %s", spool.get("id"), exc)


def default_matches(row: FilamentProfileDefault, *, brand, material, subtype, color_name) -> bool:
    """Whether a spool with these fields is covered by ``row`` (its colour, or
    any colour for an all-colours row)."""
    spool = match_key(product_key(brand=brand, material=material, subtype=subtype, color_name=color_name)).split("\x1f")
    want = row.match_key.split("\x1f")
    return spool[:3] == want[:3] and (not want[3] or spool[3] == want[3])


async def apply_to_internal_spools(db: AsyncSession, row: FilamentProfileDefault) -> int:
    """Fill every active spool ``row`` covers. Returns how many changed; the
    caller commits."""
    # Matched in Python, with the same fold as the defaults' match_key.
    candidates = (await db.execute(select(Spool).where(Spool.archived_at.is_(None)))).scalars().all()
    spools = [
        s
        for s in candidates
        if default_matches(row, brand=s.brand, material=s.material, subtype=s.subtype, color_name=s.color_name)
    ]
    changed = 0
    cache: dict[str, ResolvedProfiles | None] = {}
    for spool in spools:
        # Each spool resolves on its own colour: applying the all-colours row
        # to a colour that has a row of its own still gives that colour its values.
        colour = (spool.color_name or "").strip().lower()
        if colour not in cache:
            cache[colour] = await resolve_profile_default(
                db, brand=row.brand, material=row.material, subtype=row.subtype, color_name=spool.color_name
            )
        if await fill_internal_spool(db, spool, resolved=cache[colour]):
            changed += 1
    return changed
