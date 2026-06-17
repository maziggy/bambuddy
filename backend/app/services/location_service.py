"""Storage location catalog — single write path for spool location fields (#1004)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.location import Location
from backend.app.models.spool import Spool

logger = logging.getLogger(__name__)

DUPLICATE_LOCATION_NAME = "A location with this name already exists"


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


async def _create_location_or_get_existing(db: AsyncSession, normalized: str) -> Location:
    """Insert a location row, returning the winner on concurrent name_key collision."""
    existing = await get_location_by_name(db, normalized)
    if existing:
        return existing
    location = Location()
    assign_location_name(location, normalized)
    try:
        async with db.begin_nested():
            db.add(location)
            await db.flush()
        return location
    except IntegrityError as exc:
        winner = await get_location_by_name(db, normalized)
        if winner:
            return winner
        raise ValueError(DUPLICATE_LOCATION_NAME) from exc


async def _insert_location_if_absent(db: AsyncSession, name: str) -> bool:
    """Stage a new location row when absent. Returns True when one was added."""
    normalized = normalize_location_name(name)
    if await get_location_by_name(db, normalized):
        return False
    location = Location()
    assign_location_name(location, normalized)
    try:
        async with db.begin_nested():
            db.add(location)
            await db.flush()
        return True
    except IntegrityError:
        # Race: another writer inserted the same name between our check and
        # flush. The row already exists by definition — surface as "not added"
        # rather than re-raising. Anything else (NULL constraint, FK, check
        # constraint) would be a programming bug — re-fetch to verify so we
        # don't silently drop unrelated IntegrityErrors.
        if await get_location_by_name(db, normalized):
            return False
        logger.warning("IntegrityError on insert of location %r without surviving row", normalized)
        raise


async def resolve_location_by_name(db: AsyncSession, name: str, *, create: bool = True) -> Location | None:
    """Find a location by name (case-insensitive), optionally creating it."""
    normalized = normalize_location_name(name)
    existing = await get_location_by_name(db, normalized)
    if existing:
        return existing
    if not create:
        return None
    return await _create_location_or_get_existing(db, normalized)


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
    keys = {location_name_key(s["storage_location"]) for s in spools if (s.get("storage_location") or "").strip()}
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
        raise ValueError(DUPLICATE_LOCATION_NAME)

    old_name = location.name
    # Mirror the SQL TRIM on the Python side so a legacy row whose
    # `storage_location` has trailing whitespace still matches against the
    # `old_name` we just lifted off the Location row. Without `.strip()` the
    # equality is asymmetric (SQL strips the column; Python doesn't) and
    # legacy rows quietly fall out of the rename cascade.
    old_name_key = old_name.strip().lower()
    assign_location_name(location, normalized)
    await db.execute(update(Spool).where(Spool.location_id == location.id).values(storage_location=normalized))
    # Keep legacy rows in sync when only storage_location was set.
    await db.execute(
        update(Spool)
        .where(
            Spool.location_id.is_(None),
            func.lower(func.trim(Spool.storage_location)) == old_name_key,
        )
        .values(storage_location=normalized, location_id=location.id)
    )
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ValueError(DUPLICATE_LOCATION_NAME) from exc
    return location


async def sync_locations_from_spoolman(db: AsyncSession, client) -> bool:
    """Import distinct Spoolman location strings into the local catalog.

    Returns True when new rows were staged (caller must commit). Logs and
    returns False on Spoolman fetch failures so the calling read path keeps
    serving the local catalog instead of 500ing; bare-Exception swallow used
    to be the shape here and hid both transport errors and shape regressions.
    """
    from backend.app.services.spoolman import SpoolmanClientError, SpoolmanUnavailableError

    try:
        names = await client.get_distinct_locations()
    except (SpoolmanUnavailableError, SpoolmanClientError, httpx.HTTPError) as exc:
        logger.warning("location sync from Spoolman failed: %s", exc)
        return False

    # Collapse case variants before insert — Spoolman may return both
    # "Drybox 1" and "DRYBOX 1" in the same payload.
    by_key: dict[str, str] = {}
    for raw in names:
        name = (raw or "").strip()
        if not name:
            continue
        key = location_name_key(name)
        if key not in by_key:
            by_key[key] = name

    changed = False
    for name in by_key.values():
        if await _insert_location_if_absent(db, name):
            changed = True
    return changed


# Per-URL last-sync timestamp guard. Calling list_spools runs the sync, so on
# a polling UI without this guard every refetch round-trips to Spoolman and
# opens a write transaction — measurable latency and SQLite write contention.
# 60s is long enough to absorb dashboard polling, short enough that a manual
# spool rename in Spoolman shows up on the next minute's refresh.
_SPOOLMAN_LOCATION_SYNC_TTL_SECONDS = 60.0
_spoolman_location_sync_last_run: dict[str, float] = {}


def _spoolman_location_sync_cache_clear() -> None:
    """Test hook: drop the TTL cache so each test starts from a clean slate."""
    _spoolman_location_sync_last_run.clear()


async def maybe_sync_spoolman_locations(db: AsyncSession, *, client=None) -> bool:
    """Sync Spoolman location names into the local catalog when integration is enabled.

    Pass ``client`` when the caller has already resolved one (the GET /spools
    route does); otherwise the function falls back to ``init_spoolman_client``.
    Passing the route's client keeps test fixtures honest — without it, the
    fall-back path imports from ``backend.app.services.spoolman`` directly and
    bypasses any patch that targets the route module's alias, which causes
    real TCP connects to whatever ``spoolman_url`` happens to point at.
    """
    from backend.app.api.routes._spoolman_helpers import assert_safe_spoolman_url
    from backend.app.models.settings import Settings

    result = await db.execute(select(Settings))
    settings = {s.key: s.value for s in result.scalars().all()}
    if settings.get("spoolman_enabled", "false").lower() != "true":
        return False
    url = settings.get("spoolman_url", "").strip()
    if not url:
        return False

    # Debounce: skip the round-trip when we synced this URL recently.
    cache_key = url.rstrip("/")
    last_run = _spoolman_location_sync_last_run.get(cache_key, 0.0)
    now = time.monotonic()
    if now - last_run < _SPOOLMAN_LOCATION_SYNC_TTL_SECONDS:
        return False

    try:
        assert_safe_spoolman_url(url)
    except ValueError as exc:
        logger.warning("Spoolman URL rejected by SSRF guard during location sync: %s", exc)
        return False

    if client is None:
        from backend.app.services.spoolman import get_spoolman_client, init_spoolman_client

        client = await get_spoolman_client()
        if not client or client.base_url != cache_key:
            client = await init_spoolman_client(url)
    if not client:
        return False

    changed = await sync_locations_from_spoolman(db, client)
    _spoolman_location_sync_last_run[cache_key] = now
    return changed
