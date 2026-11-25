"""An AMS label saved before its unit reported a serial (#3348).

A label is kept under the unit's serial, or under the position key
``p<printer>a<unit>`` while the unit reports none. The AMS Lite reported none
until #3348, so its labels sit under the position key, and now the unit comes
with a serial. The label must still show everywhere, be cleared by Clear, and
move to the serial when it is saved again.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.ams_label import AmsLabel

SERIAL = "AMSLITE0001"


def _state():
    return MagicMock(connected=True, raw_data={"ams": [{"id": 0, "sn": SERIAL}]})


async def _labels(db_session):
    rows = (await db_session.execute(select(AmsLabel))).scalars().all()
    return {r.ams_serial_number: r.label for r in rows}


@pytest.fixture
async def printer(printer_factory, db_session):
    printer = await printer_factory(model="A1")
    db_session.add(AmsLabel(ams_serial_number=f"p{printer.id}a0", ams_id=0, label="Desk Lite"))
    await db_session.commit()
    return printer


@pytest.mark.asyncio
@pytest.mark.integration
class TestPrinterCard:
    async def test_still_shown(self, async_client: AsyncClient, printer):
        with patch("backend.app.api.routes.printers.printer_manager") as pm:
            pm.get_status.return_value = _state()
            resp = await async_client.get(f"/api/v1/printers/{printer.id}/ams-labels")

        assert resp.json() == {"0": "Desk Lite"}

    async def test_clear_removes_it(self, async_client: AsyncClient, printer, db_session):
        resp = await async_client.delete(f"/api/v1/printers/{printer.id}/ams-labels/0?ams_serial={SERIAL}")

        assert resp.json() == {"success": True}
        assert await _labels(db_session) == {}

    async def test_saving_moves_it_to_the_serial(self, async_client: AsyncClient, printer, db_session):
        await async_client.put(
            f"/api/v1/printers/{printer.id}/ams-labels/0", json={"label": "Lite", "ams_serial": SERIAL}
        )

        assert await _labels(db_session) == {SERIAL: "Lite"}

    async def test_saving_without_a_serial_keeps_the_position_key(self, async_client: AsyncClient, printer, db_session):
        await async_client.put(f"/api/v1/printers/{printer.id}/ams-labels/0", json={"label": "Lite"})

        assert await _labels(db_session) == {f"p{printer.id}a0": "Lite"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_inventory_location_shows_it(async_client: AsyncClient, printer, db_session):
    from backend.app.models.spool import Spool
    from backend.app.models.spool_assignment import SpoolAssignment

    spool = Spool(material="PLA", brand="eSUN", rgba="E1E9E9FF", label_weight=1000, weight_used=0)
    db_session.add(spool)
    await db_session.commit()
    await db_session.refresh(spool)
    db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=0, tray_id=1))
    await db_session.commit()

    with patch("backend.app.services.printer_manager.printer_manager.get_all_statuses") as statuses:
        statuses.return_value = {printer.id: _state()}
        resp = await async_client.get("/api/v1/inventory/assignments")

    assert [a["ams_label"] for a in resp.json()] == ["Desk Lite"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_spoolman_location_shows_it(async_client: AsyncClient, printer, db_session):
    from backend.app.models.settings import Settings
    from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

    db_session.add_all(
        [Settings(key="spoolman_enabled", value="true"), Settings(key="spoolman_url", value="http://localhost:7912")]
    )
    db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=1, spoolman_spool_id=216))
    await db_session.commit()

    with patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm:
        pm.get_all_statuses.return_value = {printer.id: _state()}
        resp = await async_client.get("/api/v1/spoolman/inventory/slot-assignments/all")

    assert [a["ams_label"] for a in resp.json()] == ["Desk Lite"]
