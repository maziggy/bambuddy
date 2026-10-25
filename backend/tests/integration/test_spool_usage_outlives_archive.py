"""Deleting an archive a spool was used for keeps the spool's usage record.

The usage row pointed at the archive with no delete rule: on PostgreSQL every
hard delete of such an archive (purge from statistics, or a printer delete
with its archives) failed on the foreign key with a server error. The row is
the spool's consumption record, so it stays and only loses the link.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.spool import Spool
from backend.app.models.spool_usage_history import SpoolUsageHistory

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _used_archive(db_session, archive_factory, printer_id: int) -> tuple[int, int]:
    archive = await archive_factory(printer_id)
    spool = Spool(material="PLA")
    db_session.add(spool)
    await db_session.flush()
    usage = SpoolUsageHistory(spool_id=spool.id, printer_id=printer_id, archive_id=archive.id, weight_used=10, cost=0.5)
    db_session.add(usage)
    await db_session.commit()
    return archive.id, usage.id


async def _usage(db_session, usage_id: int):
    db_session.expire_all()
    return (
        await db_session.execute(
            select(SpoolUsageHistory.archive_id, SpoolUsageHistory.weight_used).where(SpoolUsageHistory.id == usage_id)
        )
    ).one_or_none()


async def test_purging_an_archive_keeps_the_spool_usage(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    printer = await printer_factory()
    archive_id, usage_id = await _used_archive(db_session, archive_factory, printer.id)

    response = await async_client.delete(f"/api/v1/archives/{archive_id}?purge_stats=true")

    assert response.status_code == 200, response.text
    assert tuple(await _usage(db_session, usage_id)) == (None, 10)


async def test_deleting_a_printer_with_its_archives_keeps_the_spool_usage(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    printer = await printer_factory()
    _, usage_id = await _used_archive(db_session, archive_factory, printer.id)

    response = await async_client.delete(f"/api/v1/printers/{printer.id}?delete_archives=true")

    assert response.status_code == 200, response.text
    row = await _usage(db_session, usage_id)
    assert row is not None and row[0] is None


async def _unlinked_usage(db_session, print_name: str, cost: float, created_at) -> None:
    spool = Spool(material="PLA")
    db_session.add(spool)
    await db_session.flush()
    db_session.add(
        SpoolUsageHistory(
            spool_id=spool.id, print_name=print_name, archive_id=None, weight_used=10, cost=cost, created_at=created_at
        )
    )
    await db_session.commit()


async def test_recalculating_costs_does_not_match_unlinked_usage_by_name(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    # A deleted archive's usage keeps its print name but loses its link; usage
    # without a link is not charged to an archive by name, so the archive gets
    # its estimate from the catalog price.
    from datetime import datetime, timedelta

    from backend.app.models.archive import PrintArchive

    printer = await printer_factory()
    printed = datetime(2026, 9, 1, 12, 0)
    archive = await archive_factory(
        printer.id, print_name="Benchy", created_at=printed, filament_used_grams=100, filament_type="PLA"
    )
    archive_id = archive.id
    await _unlinked_usage(db_session, "Benchy", 9.99, printed + timedelta(hours=1))

    response = await async_client.post("/api/v1/archives/recalculate-costs")

    assert response.status_code == 200, response.text
    db_session.expire_all()
    cost = (await db_session.execute(select(PrintArchive.cost).where(PrintArchive.id == archive_id))).scalar()
    # 100 g at the default 25.00 per kg, not the unlinked 9.99.
    assert cost == 2.5
