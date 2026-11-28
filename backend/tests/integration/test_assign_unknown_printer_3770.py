"""Assigning a spool to a printer that does not exist is refused (#3770).

The route only checked the printer scope, which an unrestricted caller passes
for any id, and SQLite does not enforce the foreign key, so the row was stored
with ``printer_name: null``. The Spoolman route already answered 404.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_an_unknown_printer_is_404_and_stores_nothing(async_client: AsyncClient, db_session):
    spool = Spool(material="PLA", label_weight=1000, weight_used=0)
    db_session.add(spool)
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/inventory/assignments",
        json={"spool_id": spool.id, "printer_id": 999, "ams_id": 0, "tray_id": 2},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Printer not found"
    assert (await db_session.execute(select(SpoolAssignment))).scalars().all() == []


async def test_a_known_printer_still_stores_the_row(async_client: AsyncClient, db_session, printer_factory):
    printer = await printer_factory()
    spool = Spool(material="PLA", label_weight=1000, weight_used=0)
    db_session.add(spool)
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/inventory/assignments",
        json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 2},
    )

    assert response.status_code == 200
    rows = (await db_session.execute(select(SpoolAssignment))).scalars().all()
    assert [(row.printer_id, row.spool_id) for row in rows] == [(printer.id, spool.id)]
