"""Storage location catalog — single write path for spool location fields (#1004)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.location import Location
from backend.app.models.spool import Spool


def normalize_location_name(name: str) -> str:
    trimmed = name.strip()
    if not trimmed:
        raise ValueError("name must not be empty")
    return trimmed


def location_name_key(name: str) -> str:
    """Case-insensitive lookup key stored on Location.name_key."""
    return normalize_location_name(name).lower()


def assign_location_name(location: Location, name: str) -> None:
    normalized = normalize_location_name(name)
    location.name = normalized
    location.name_key = location_name_key(normalized)


@dataclass(frozen=True)
class SpoolLocationFields:
    """Canonical spool location state: FK + denormalized string for Spoolman/display."""

    location_id: int | None
    storage_location: str | None


async def get_location_by_id(db: AsyncSession, location_id: int) -> Location | None:
    result = await db.execute(select(Location).where(Location.id == location_id))
    return result.scalar_one_or_none()


async def get_location_by_name(db: AsyncSession, name: str) -> Location | None:
    key = location_name_key(name)
    result = await db.execute(select(Location).where(Location.name_key == key))
    return result.scalar_one_or_none()


async def get_locations_by_name_keys(db: AsyncSession, keys: set[str]) -> dict[str, Location]:
    if not keys:
        return {}
    result = await db.execute(select(Location).where(Location.name_key.in_(keys)))
    return {loc.name_key: loc for loc in result.scalars().all()}


async def resolve_location_by_name(db: AsyncSession, name: str, *, create: bool = True) -> Location | None:
    """Find a location by name (case-insensitive), optionally creating it."""
    normalized = normalize_location_name(name)
    existing = await get_location_by_name(db, normalized)
    if existing:
        return existing
    if not create:
        return None
    location = Location()
    assign_location_name(location, normalized)
    db.add(location)
    await db.flush()
    return location


async def resolve_spool_location_fields(
    db: AsyncSession,
    *,
    location_id: int | None = None,
    storage_location: str | None = None,
    fields_set: set[str],
) -> SpoolLocationFields | None:
    """Resolve location_id + storage_location from API input.

    ``location_id`` wins when both fields appear in ``fields_set``.
    Returns ``None`` when neither location field was provided.
    """
    if "location_id" in fields_set:
        if location_id is None:
            return SpoolLocationFields(location_id=None, storage_location=None)
        loc = await get_location_by_id(db, location_id)
        if not loc:
            raise ValueError(f"Location {location_id} not found")
        return SpoolLocationFields(location_id=loc.id, storage_location=loc.name)

    if "storage_location" in fields_set:
        if not storage_location:
            return SpoolLocationFields(location_id=None, storage_location=None)
        loc = await resolve_location_by_name(db, storage_location)
        if not loc:
            return SpoolLocationFields(location_id=None, storage_location=None)
        return SpoolLocationFields(location_id=loc.id, storage_location=loc.name)

    return None


async def prepare_internal_spool_payload(db: AsyncSession, data: dict, fields_set: set[str]) -> dict:
    """Apply resolved location fields before creating or updating an internal spool."""
    payload = dict(data)
    resolved = await resolve_spool_location_fields(
        db,
        location_id=payload.get("location_id"),
        storage_location=payload.get("storage_location"),
        fields_set=fields_set,
    )
    if resolved is not None:
        payload["location_id"] = resolved.location_id
        payload["storage_location"] = resolved.storage_location
    return payload


async def resolve_spoolman_location_string(
    db: AsyncSession,
    *,
    location_id: int | None = None,
    storage_location: str | None = None,
    fields_set: set[str],
) -> tuple[str | None, bool]:
    """Return (Spoolman location string, changed) for proxy writes."""
    resolved = await resolve_spool_location_fields(
        db,
        location_id=location_id,
        storage_location=storage_location,
        fields_set=fields_set,
    )
    if resolved is None:
        return None, False
    return resolved.storage_location, True


async def count_internal_spools_at_location(db: AsyncSession, location_id: int) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Spool)
        .where(
            Spool.location_id == location_id,
            Spool.archived_at.is_(None),
        )
    )
    return int(result.scalar() or 0)


async def count_spools_at_location_by_name(db: AsyncSession, name: str) -> int:
    normalized = name.strip()
    if not normalized:
        return 0
    result = await db.execute(
        select(func.count())
        .select_from(Spool)
        .where(
            Spool.archived_at.is_(None),
            func.lower(func.trim(Spool.storage_location)) == normalized.lower(),
        )
    )
    return int(result.scalar() or 0)


async def enrich_spool_dicts_with_location_id(db: AsyncSession, spools: list[dict]) -> None:
    """Attach location_id to mapped Spoolman-style spool dicts in place."""
    keys = {
        location_name_key(s["storage_location"])
        for s in spools
        if (s.get("storage_location") or "").strip()
    }
    if not keys:
        for s in spools:
            s["location_id"] = None
        return

    by_key = await get_locations_by_name_keys(db, keys)
    for s in spools:
        raw = (s.get("storage_location") or "").strip()
        if not raw:
            s["location_id"] = None
            continue
        loc = by_key.get(location_name_key(raw))
        s["location_id"] = loc.id if loc else None


async def rename_location(db: AsyncSession, location: Location, new_name: str) -> Location:
    normalized = normalize_location_name(new_name)
    existing = await get_location_by_name(db, normalized)
    if existing and existing.id != location.id:
        raise ValueError("A location with this name already exists")

    old_name = location.name
    assign_location_name(location, normalized)
    await db.execute(
        update(Spool)
        .where(Spool.location_id == location.id)
        .values(storage_location=normalized)
    )
    # Keep legacy rows in sync when only storage_location was set.
    await db.execute(
        update(Spool)
        .where(
            Spool.location_id.is_(None),
            func.lower(func.trim(Spool.storage_location)) == old_name.lower(),
        )
        .values(storage_location=normalized, location_id=location.id)
    )
    await db.flush()
    return location


async def sync_locations_from_spoolman(db: AsyncSession, client) -> bool:
    """Import distinct Spoolman location strings into the local catalog.

    Returns True when new rows were staged (caller must commit).
    """
    try:
        names = await client.get_distinct_locations()
    except Exception:
        return False

    changed = False
    for raw in names:
        name = (raw or "").strip()
        if not name:
            continue
        existing = await get_location_by_name(db, name)
        if not existing:
            location = Location()
            assign_location_name(location, name)
            db.add(location)
            changed = True
    return changed
