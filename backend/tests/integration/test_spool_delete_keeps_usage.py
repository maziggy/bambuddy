"""Deleting a spool keeps its usage history, and its id is not given to a new spool.

PostgreSQL deleted the usage with the spool, so recalculating the cost of its
prints fell back to an estimate. SQLite kept the usage with the old spool id,
and could give that id to the next spool, which then showed the deleted
spool's prints as its own.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.models.spool import Spool
from backend.app.models.spool_usage_history import SpoolUsageHistory

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_deleting_a_spool_keeps_the_usage_and_the_print_cost(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    printer = await printer_factory()
    archive = await archive_factory(printer.id, filament_used_grams=20.0, cost=None)
    archive_id = archive.id
    spool = Spool(material="PLA")
    db_session.add(spool)
    await db_session.flush()
    spool_id = spool.id
    db_session.add(
        SpoolUsageHistory(spool_id=spool_id, printer_id=printer.id, archive_id=archive_id, weight_used=20, cost=0.5)
    )
    await db_session.commit()

    deleted = await async_client.delete(f"/api/v1/inventory/spools/{spool_id}")
    recalculated = await async_client.post("/api/v1/archives/recalculate-costs")

    assert deleted.status_code in (200, 204), deleted.text
    assert recalculated.status_code == 200, recalculated.text
    db_session.expire_all()
    usage = (await db_session.execute(select(SpoolUsageHistory.spool_id, SpoolUsageHistory.cost))).one()
    assert tuple(usage) == (None, 0.5)
    cost = (await db_session.execute(select(PrintArchive.cost).where(PrintArchive.id == archive_id))).scalar()
    assert cost == 0.5


async def test_a_new_spool_does_not_get_the_id_of_a_deleted_one(async_client: AsyncClient, db_session):
    spool = Spool(material="PLA")
    db_session.add(spool)
    await db_session.commit()
    old_id = spool.id

    deleted = await async_client.delete(f"/api/v1/inventory/spools/{old_id}")
    assert deleted.status_code in (200, 204), deleted.text
    replacement = Spool(material="PETG")
    db_session.add(replacement)
    await db_session.commit()

    assert replacement.id != old_id


async def test_the_usage_list_shows_usage_of_a_deleted_spool(async_client: AsyncClient, db_session):
    spool = Spool(material="PLA")
    db_session.add(spool)
    await db_session.flush()
    db_session.add(SpoolUsageHistory(spool_id=spool.id, weight_used=12, cost=0.3))
    await db_session.commit()
    deleted = await async_client.delete(f"/api/v1/inventory/spools/{spool.id}")
    assert deleted.status_code in (200, 204), deleted.text

    response = await async_client.get("/api/v1/inventory/usage")

    assert response.status_code == 200, response.text
    assert [(row["spool_id"], row["weight_used"]) for row in response.json()] == [(None, 12)]
