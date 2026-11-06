"""Reservations no print can use any more are released at start.

Before b2ed37b6c, billing a print without a user (authentication off, or a
user deleted while it printed) stopped before releasing the print's budget
reservation, so it held that much of the cost centre's budget for good. A
reservation whose queue entry has finished or is gone can never be charged.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.app.core.database import _release_stuck_budget_reservations
from backend.app.models.finance import BudgetReservation, CostCenter
from backend.app.models.print_queue import PrintQueueItem

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

CASES = {
    # queue entry status (None: entry deleted) -> reservation released?
    "completed": True,
    "failed": True,
    "cancelled": True,
    None: True,
    "printing": False,
    "pending": False,
    "skipped": False,  # can go back to pending
}


async def test_reservations_of_finished_or_deleted_entries_are_released(db_session, printer_factory, archive_factory):
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    center = CostCenter(name="Lab", total_budget=100)
    db_session.add(center)
    await db_session.flush()
    reservation_ids = {}
    for status in CASES:
        if status is None:
            source_id = 999_999
        else:
            item = PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status=status)
            db_session.add(item)
            await db_session.flush()
            source_id = item.id
        reservation = BudgetReservation(
            cost_center_id=center.id, amount=5, status="active", source_type="print_queue", source_id=source_id
        )
        db_session.add(reservation)
        await db_session.flush()
        reservation_ids[status] = reservation.id
    other = BudgetReservation(cost_center_id=center.id, amount=5, status="active", source_type="manual", source_id=1)
    db_session.add(other)
    await db_session.commit()
    other_id = other.id

    await _release_stuck_budget_reservations(await db_session.connection())
    await _release_stuck_budget_reservations(await db_session.connection())  # a restart changes nothing
    await db_session.commit()

    db_session.expire_all()
    rows = dict((await db_session.execute(select(BudgetReservation.id, BudgetReservation.status))).all())
    released = {status: rows[rid] == "released" for status, rid in reservation_ids.items()}
    assert released == CASES
    assert rows[other_id] == "active"
