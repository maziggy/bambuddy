"""Spool assignments made by hand wait for the printer's assignment lock (SA-5).

The AMS change handler writes a printer's slot assignments under a per-printer
lock, so two of its updates can't both find a slot free and both insert. The
assign, unassign and create-from-slot routes wrote the same rows without it: a
manual assign racing an AMS update (or a double-click) on PostgreSQL failed
with a 500 on the unique (printer, AMS, tray) constraint, or on a row the other
writer had just deleted.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.ams_assignment_lock import ams_assignment_locks, get_ams_assignment_lock

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _fresh_locks():
    """A contended lock binds to its test's event loop; printer ids repeat."""
    ams_assignment_locks.clear()
    yield
    ams_assignment_locks.clear()


TRAY = {"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF", "tag_uid": "A1B2C3D4E5F60708", "state": 11}


async def _waits_for_the_lock(printer_id: int, request) -> object:
    """Run *request* while the lock is held: it must not finish until released."""
    lock = get_ams_assignment_lock(printer_id)
    await lock.acquire()
    try:
        task = asyncio.ensure_future(request)
        await asyncio.sleep(0.3)
        assert not task.done(), "the route wrote the slot without waiting for the lock"
    finally:
        lock.release()
    return await asyncio.wait_for(task, 10)


@pytest.fixture
def printer_status():
    status = SimpleNamespace(raw_data={"ams": [{"id": 0, "tray": [TRAY]}]}, state="IDLE", connected=True)
    with (
        patch("backend.app.services.printer_manager.printer_manager.get_status", MagicMock(return_value=status)),
        patch("backend.app.services.printer_manager.printer_manager.get_client", MagicMock(return_value=None)),
        patch("backend.app.api.routes.inventory.ws_manager") as ws,
    ):
        ws.broadcast = AsyncMock()
        yield


async def _spool(db_session) -> Spool:
    spool = Spool(material="PLA", label_weight=1000, weight_used=0)
    db_session.add(spool)
    await db_session.commit()
    return spool


async def test_assigning(async_client, db_session, printer_factory, printer_status):
    printer = await printer_factory()
    spool = await _spool(db_session)

    response = await _waits_for_the_lock(
        printer.id,
        async_client.post(
            "/api/v1/inventory/assignments",
            json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 0},
        ),
    )

    assert response.status_code == 200, response.text


async def test_unassigning(async_client, db_session, printer_factory, printer_status):
    printer = await printer_factory()
    spool = await _spool(db_session)
    db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=0, tray_id=0))
    await db_session.commit()

    response = await _waits_for_the_lock(
        printer.id, async_client.delete(f"/api/v1/inventory/assignments/{printer.id}/0/0")
    )

    assert response.status_code == 200, response.text
    assert (await db_session.execute(select(SpoolAssignment))).scalars().first() is None


async def test_creating_a_spool_from_a_slot(async_client, db_session, printer_factory, printer_status):
    printer = await printer_factory()

    response = await _waits_for_the_lock(
        printer.id,
        async_client.post(
            "/api/v1/inventory/spools/from-slot", json={"printer_id": printer.id, "ams_id": 0, "tray_id": 0}
        ),
    )

    assert response.status_code == 200, response.text
