"""The archive purge keeps favourites (#3700).

The purge modal and the changelog told people to favourite an archive they
wanted to keep, but every purge (manual, either mode, and the daily
auto-purge) removed favourites like any other archive.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient


async def _old_archives(archive_factory, printer_factory, db_session):
    """A favourite and an ordinary archive, both 400 days old, and a fresh one."""
    printer = await printer_factory()
    favourite = await archive_factory(printer.id, print_name="Favourite", file_size=1000)
    ordinary = await archive_factory(printer.id, print_name="Ordinary", file_size=2000)
    fresh = await archive_factory(printer.id, print_name="Fresh", file_size=4000)
    old = datetime.now(timezone.utc) - timedelta(days=400)
    favourite.created_at = old
    favourite.is_favorite = True
    ordinary.created_at = old
    fresh.created_at = datetime.now(timezone.utc) - timedelta(days=10)
    await db_session.commit()
    return favourite.id, ordinary.id, fresh.id


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("purge_stats", [False, True])
async def test_preview_leaves_favourites_out_and_counts_them(
    async_client: AsyncClient, archive_factory, printer_factory, db_session, purge_stats
):
    await _old_archives(archive_factory, printer_factory, db_session)

    resp = await async_client.get(
        f"/api/v1/archives/purge/preview?older_than_days=365&purge_stats={str(purge_stats).lower()}"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["total_bytes"] == 2000
    assert len(body["sample_filenames"]) == 1
    assert body["favorites_kept"] == 1


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("purge_stats", [False, True])
async def test_manual_purge_keeps_favourites(
    async_client: AsyncClient, archive_factory, printer_factory, db_session, purge_stats
):
    from backend.app.models.archive import PrintArchive

    favourite_id, ordinary_id, fresh_id = await _old_archives(archive_factory, printer_factory, db_session)

    resp = await async_client.post("/api/v1/archives/purge", json={"older_than_days": 365, "purge_stats": purge_stats})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1

    db_session.expire_all()
    favourite = await db_session.get(PrintArchive, favourite_id)
    assert favourite is not None
    assert favourite.deleted_at is None
    ordinary = await db_session.get(PrintArchive, ordinary_id)
    assert ordinary is None if purge_stats else ordinary.deleted_at is not None
    assert (await db_session.get(PrintArchive, fresh_id)).deleted_at is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_auto_purge_keeps_favourites(async_client: AsyncClient, archive_factory, printer_factory, db_session):
    """``async_client`` activates the ``async_session`` patches the per-row
    delete sessions need."""
    from backend.app.models.archive import PrintArchive
    from backend.app.services.archive_purge import archive_purge_service

    favourite_id, ordinary_id, _fresh_id = await _old_archives(archive_factory, printer_factory, db_session)
    await archive_purge_service.set_settings(db_session, enabled=True, days=365)

    assert await archive_purge_service._maybe_run_auto_purge(db_session) == 1

    db_session.expire_all()
    assert (await db_session.get(PrintArchive, favourite_id)).deleted_at is None
    assert (await db_session.get(PrintArchive, ordinary_id)).deleted_at is not None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_favourite_already_purged_is_not_counted_as_kept(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """A favourite a purge before this fix already removed has no files to keep.

    The default purge leaves soft-deleted rows alone, so it does not count
    them; the statistics mode does reach them, and keeps favourites among them.
    """
    from backend.app.models.archive import PrintArchive

    favourite_id, _ordinary_id, _fresh_id = await _old_archives(archive_factory, printer_factory, db_session)
    favourite = await db_session.get(PrintArchive, favourite_id)
    favourite.deleted_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    soft = (await async_client.get("/api/v1/archives/purge/preview?older_than_days=365")).json()
    assert soft["favorites_kept"] == 0
    hard = (await async_client.get("/api/v1/archives/purge/preview?older_than_days=365&purge_stats=true")).json()
    assert hard["favorites_kept"] == 1
