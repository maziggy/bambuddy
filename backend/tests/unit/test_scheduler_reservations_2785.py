"""Reservation-aware auto-dispatch in check_queue (#2785).

An item stamped ``respect_reservations`` (by a VP with the toggle on) is staged
for manual start, with a reason naming the reservation, when its printer is
reserved now or would be before the print finishes. Items without the flag,
and the reservation's own owner, are not affected.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer_reservation import ALL_DAYS_MASK, PrinterReservation
from backend.app.models.user import User
from backend.app.services.printer_reservations import RESERVATION_HOLD_PREFIX
from backend.app.utils.local_time import local_zone
from backend.tests.unit.test_scheduler_busy_reasons_3018 import _scheduler, one_printer  # noqa: F401


async def _reserve(session_maker, printer_id: int, **kw) -> int:
    fields = {"label": "Project X", "printer_ids": [printer_id], "days_mask": ALL_DAYS_MASK, "all_day": True}
    fields.update(kw)
    async with session_maker() as db:
        row = PrinterReservation(**fields)
        db.add(row)
        await db.commit()
        return row.id


async def _flag(session_maker, item_id: int, **values) -> None:
    async with session_maker() as db:
        await db.execute(update(PrintQueueItem).where(PrintQueueItem.id == item_id).values(**values))
        await db.commit()


async def _item(session_maker, item_id: int) -> PrintQueueItem:
    async with session_maker() as db:
        return await db.get(PrintQueueItem, item_id)


@pytest.mark.asyncio
async def test_reserved_printer_stages_a_reservation_aware_item(one_printer):  # noqa: F811
    await _reserve(one_printer.session_maker, one_printer.printer_id)
    item_id = await one_printer.add_item()
    await _flag(one_printer.session_maker, item_id, respect_reservations=True)

    async with _scheduler(one_printer, idle=True) as scheduler:
        await scheduler.check_queue()
        assert not scheduler._inflight
        # A second pass goes through the manual_start branch, which must keep
        # the reason rather than clear it.
        await scheduler.check_queue()

    item = await _item(one_printer.session_maker, item_id)
    assert item.status == "pending"
    assert item.manual_start is True
    assert (item.waiting_reason or "").startswith(RESERVATION_HOLD_PREFIX)
    assert "Project X" in item.waiting_reason


@pytest.mark.asyncio
async def test_items_without_the_flag_ignore_reservations(one_printer):  # noqa: F811
    await _reserve(one_printer.session_maker, one_printer.printer_id)
    item_id = await one_printer.add_item()

    async with _scheduler(one_printer, idle=True) as scheduler:
        await scheduler.check_queue()

    assert (await _item(one_printer.session_maker, item_id)).status != "pending"


@pytest.mark.asyncio
async def test_the_owner_prints_into_their_own_reservation(one_printer):  # noqa: F811
    async with one_printer.session_maker() as db:
        owner = User(username="owner", password_hash="x", is_active=True)
        db.add(owner)
        await db.commit()
        owner_id = owner.id
    await _reserve(one_printer.session_maker, one_printer.printer_id, created_by_id=owner_id)
    item_id = await one_printer.add_item()
    await _flag(one_printer.session_maker, item_id, respect_reservations=True, created_by_id=owner_id)

    async with _scheduler(one_printer, idle=True) as scheduler:
        await scheduler.check_queue()

    assert (await _item(one_printer.session_maker, item_id)).status != "pending"


@pytest.mark.asyncio
async def test_a_print_that_would_run_into_a_window_is_staged(one_printer):  # noqa: F811
    # A window starting two hours from now (local wall clock), lasting an hour.
    start_local = datetime.now(timezone.utc).astimezone(local_zone()) + timedelta(hours=2)
    start_minute = start_local.hour * 60 + start_local.minute
    await _reserve(
        one_printer.session_maker,
        one_printer.printer_id,
        all_day=False,
        start_minute=start_minute,
        end_minute=(start_minute + 60) % (24 * 60),
    )
    short_id = await one_printer.add_item(position=0)
    await _flag(one_printer.session_maker, short_id, respect_reservations=True, print_time_seconds=30 * 60)

    async with _scheduler(one_printer, idle=True) as scheduler:
        await scheduler.check_queue()

    # Thirty minutes fits before the window.
    assert (await _item(one_printer.session_maker, short_id)).status != "pending"

    long_id = await one_printer.add_item(position=1)
    await _flag(one_printer.session_maker, long_id, respect_reservations=True, print_time_seconds=5 * 3600)
    await _flag(one_printer.session_maker, short_id, status="completed")

    async with _scheduler(one_printer, idle=True) as scheduler:
        await scheduler.check_queue()

    item = await _item(one_printer.session_maker, long_id)
    assert item.status == "pending"
    assert item.manual_start is True
    assert "before this print would finish" in (item.waiting_reason or "")


@pytest.mark.asyncio
async def test_no_reservation_dispatches_as_before(one_printer):  # noqa: F811
    item_id = await one_printer.add_item()
    await _flag(one_printer.session_maker, item_id, respect_reservations=True)

    async with _scheduler(one_printer, idle=True) as scheduler:
        await scheduler.check_queue()

    assert (await _item(one_printer.session_maker, item_id)).status != "pending"


@pytest.mark.asyncio
async def test_model_job_is_staged_when_the_only_idle_printer_is_reserved(one_printer):  # noqa: F811
    await _reserve(one_printer.session_maker, one_printer.printer_id)
    item_id = await one_printer.add_item()
    await _flag(one_printer.session_maker, item_id, printer_id=None, target_model="X1C", respect_reservations=True)

    async with _scheduler(one_printer, idle=True) as scheduler:
        await scheduler.check_queue()
        assert not scheduler._inflight

    item = await _item(one_printer.session_maker, item_id)
    assert item.status == "pending"
    assert item.printer_id is None
    assert item.manual_start is True
    assert (item.waiting_reason or "").startswith(RESERVATION_HOLD_PREFIX)


@pytest.mark.asyncio
async def test_model_job_control_without_reservation_is_assigned(one_printer):  # noqa: F811
    item_id = await one_printer.add_item()
    await _flag(one_printer.session_maker, item_id, printer_id=None, target_model="X1C", respect_reservations=True)

    async with _scheduler(one_printer, idle=True) as scheduler:
        await scheduler.check_queue()

    assert (await _item(one_printer.session_maker, item_id)).printer_id == one_printer.printer_id
