"""Integration tests for archive auto-purge (#1008 follow-up)."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
@pytest.mark.integration
async def test_settings_defaults_when_unset(async_client: AsyncClient):
    """GET /archives/purge/settings returns sensible defaults on a fresh install."""
    resp = await async_client.get("/api/v1/archives/purge/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["days"] == 365
    # #1390: default soft-delete — preserves Quick Stats contribution.
    assert body["purge_stats"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_settings_roundtrip(async_client: AsyncClient):
    """PUT persists, GET returns the saved values, days is clamped."""
    resp = await async_client.put(
        "/api/v1/archives/purge/settings",
        json={"enabled": True, "days": 180, "purge_stats": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True, "days": 180, "purge_stats": True}

    resp = await async_client.get("/api/v1/archives/purge/settings")
    assert resp.json() == {"enabled": True, "days": 180, "purge_stats": True}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_settings_rejects_out_of_range_days(async_client: AsyncClient):
    """days below MIN or above MAX is rejected."""
    resp = await async_client.put(
        "/api/v1/archives/purge/settings",
        json={"enabled": True, "days": 1},
    )
    # Pydantic validation returns 422; explicit bound check returns 400.
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_preview_counts_old_archives(async_client: AsyncClient, archive_factory, printer_factory, db_session):
    """Preview returns the count + total bytes of archives older than the threshold."""
    printer = await printer_factory()
    old = await archive_factory(printer.id, print_name="Old", file_size=1000)
    fresh = await archive_factory(printer.id, print_name="Fresh", file_size=2000)

    old.created_at = datetime.now(timezone.utc) - timedelta(days=400)
    fresh.created_at = datetime.now(timezone.utc) - timedelta(days=10)
    await db_session.commit()

    resp = await async_client.get("/api/v1/archives/purge/preview?older_than_days=365")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["total_bytes"] == 1000
    assert "Old" in body["sample_filenames"][0] or old.filename in body["sample_filenames"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_preview_ignores_recently_reprinted_archives(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """Reprints update completed_at but leave created_at pinned; purge must honour that."""
    printer = await printer_factory()
    reprinted = await archive_factory(printer.id, print_name="Reprinted", file_size=1000)

    # Originally printed 400 days ago, but a reprint last week refreshed completed_at.
    reprinted.created_at = datetime.now(timezone.utc) - timedelta(days=400)
    reprinted.started_at = datetime.now(timezone.utc) - timedelta(days=7)
    reprinted.completed_at = datetime.now(timezone.utc) - timedelta(days=7)
    await db_session.commit()

    resp = await async_client.get("/api/v1/archives/purge/preview?older_than_days=365")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_manual_purge_soft_deletes_by_default(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """#1390: POST /archives/purge with no body flag soft-deletes — files
    off disk, ``deleted_at`` set, archive row survives so Quick Stats keeps
    every contribution. Matches the single-archive delete default from #1343."""
    from backend.app.models.archive import PrintArchive

    printer = await printer_factory()
    old = await archive_factory(printer.id, print_name="Old")
    fresh = await archive_factory(printer.id, print_name="Fresh")

    old_id = old.id
    fresh_id = fresh.id
    old.created_at = datetime.now(timezone.utc) - timedelta(days=400)
    fresh.created_at = datetime.now(timezone.utc) - timedelta(days=10)
    await db_session.commit()

    resp = await async_client.post(
        "/api/v1/archives/purge",
        json={"older_than_days": 365},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 1
    assert body["purge_stats"] is False

    db_session.expire_all()
    # Old row still exists in DB but is soft-deleted.
    old_row = await db_session.get(PrintArchive, old_id)
    assert old_row is not None
    assert old_row.deleted_at is not None
    fresh_row = await db_session.get(PrintArchive, fresh_id)
    assert fresh_row is not None
    assert fresh_row.deleted_at is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_manual_purge_hard_deletes_when_purge_stats_set(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """#1390: when ``purge_stats=true`` is sent in the body, the bulk purge
    hard-deletes the archive AND the linked PrintLogEntry rows so the
    contribution drops from /stats — matches the single-archive route's
    ``?purge_stats=true`` semantics."""
    from backend.app.models.archive import PrintArchive

    printer = await printer_factory()
    old = await archive_factory(printer.id, print_name="Old")
    old_id = old.id
    old.created_at = datetime.now(timezone.utc) - timedelta(days=400)
    await db_session.commit()

    resp = await async_client.post(
        "/api/v1/archives/purge",
        json={"older_than_days": 365, "purge_stats": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 1
    assert body["purge_stats"] is True

    db_session.expire_all()
    assert await db_session.get(PrintArchive, old_id) is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_auto_purge_soft_deletes_by_default(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """#1390: scheduled auto-purge defaults to soft-delete — Quick Stats
    preserved unless the admin explicitly opts into hard-delete via the
    settings toggle.

    ``async_client`` is included solely so its fixture activates the module-level
    ``async_session`` patches that let :meth:`purge_older_than`'s per-row
    delete sessions reach the in-memory test database.
    """
    from backend.app.models.archive import PrintArchive
    from backend.app.services.archive_purge import archive_purge_service

    printer = await printer_factory()
    stale = await archive_factory(printer.id, print_name="Stale")
    stale_id = stale.id
    stale.created_at = datetime.now(timezone.utc) - timedelta(days=400)
    await db_session.commit()

    await archive_purge_service.set_settings(db_session, enabled=True, days=365)

    deleted = await archive_purge_service._maybe_run_auto_purge(db_session)
    assert deleted >= 1

    db_session.expire_all()
    stale_row = await db_session.get(PrintArchive, stale_id)
    assert stale_row is not None
    assert stale_row.deleted_at is not None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_auto_purge_hard_deletes_when_settings_opts_in(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """#1390: scheduled auto-purge honours the ``purge_stats`` setting —
    when True the sweeper hard-deletes archive rows AND linked PrintLogEntry
    rows, dropping every contribution from /stats."""
    from backend.app.models.archive import PrintArchive
    from backend.app.services.archive_purge import archive_purge_service

    printer = await printer_factory()
    stale = await archive_factory(printer.id, print_name="Stale")
    stale_id = stale.id
    stale.created_at = datetime.now(timezone.utc) - timedelta(days=400)
    await db_session.commit()

    await archive_purge_service.set_settings(db_session, enabled=True, days=365, purge_stats=True)

    deleted = await archive_purge_service._maybe_run_auto_purge(db_session)
    assert deleted >= 1

    db_session.expire_all()
    assert await db_session.get(PrintArchive, stale_id) is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_auto_purge_throttles_within_24h(async_client: AsyncClient, archive_factory, printer_factory, db_session):
    """A recent last-run timestamp blocks the sweeper for 24h."""
    from backend.app.services.archive_purge import archive_purge_service

    printer = await printer_factory()
    stale = await archive_factory(printer.id, print_name="Stale")
    stale.created_at = datetime.now(timezone.utc) - timedelta(days=400)
    await db_session.commit()

    await archive_purge_service.set_settings(db_session, enabled=True, days=365)
    # Stamp a last-run time 1h ago — should block the sweeper for another 23h.
    await archive_purge_service._stamp_last_run(db_session, datetime.now(timezone.utc) - timedelta(hours=1))

    deleted = await archive_purge_service._maybe_run_auto_purge(db_session)
    assert deleted == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_auto_purge_skipped_when_disabled(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """When the toggle is off, old archives stay put."""
    from backend.app.models.archive import PrintArchive
    from backend.app.services.archive_purge import archive_purge_service

    printer = await printer_factory()
    stale = await archive_factory(printer.id, print_name="Stale")
    stale_id = stale.id
    stale.created_at = datetime.now(timezone.utc) - timedelta(days=400)
    await db_session.commit()

    await archive_purge_service.set_settings(db_session, enabled=False, days=365)
    deleted = await archive_purge_service._maybe_run_auto_purge(db_session)
    assert deleted == 0

    db_session.expire_all()
    assert await db_session.get(PrintArchive, stale_id) is not None
