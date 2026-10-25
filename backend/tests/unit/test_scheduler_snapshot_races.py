"""An edit made while the scheduler's pass runs is not overwritten (Q2).

``check_queue`` loads the pending items once and works on that copy. A queue
item edited in between (a mapping set by hand, a move to another printer) was
overwritten when the pass computed and saved its AMS mapping, or dispatched
to the printer it was on before. The edit lands where the pass waits on
something; the previous-print check is such a place.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import update

from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.tests.unit.test_scheduler_busy_reasons_3018 import _scheduler, one_printer  # noqa: F401


async def _pinned_item(ctx, ams_mapping: str | None = None) -> int:
    item_id = await ctx.add_item()
    async with ctx.session_maker() as db:
        await db.execute(
            update(PrintQueueItem)
            .where(PrintQueueItem.id == item_id)
            .values(require_previous_success=True, ams_mapping=ams_mapping)
        )
        await db.commit()
    return item_id


def _edit_during_the_pass(ctx, scheduler, **values):
    async def edit(_db, item, *_args, **_kwargs):
        async with ctx.session_maker() as other:
            await other.execute(update(PrintQueueItem).where(PrintQueueItem.id == item.id).values(**values))
            await other.commit()
        return True

    return patch.object(scheduler, "_check_previous_success", AsyncMock(side_effect=edit))


@pytest.mark.asyncio
async def test_a_mapping_set_meanwhile_is_kept(one_printer):  # noqa: F811
    """The pass would clear the bogus all-unresolved mapping it loaded."""
    item_id = await _pinned_item(one_printer, ams_mapping="[-1]")

    async with _scheduler(one_printer, idle=True) as scheduler:
        with _edit_during_the_pass(one_printer, scheduler, ams_mapping="[2]"):
            await scheduler.check_queue()

    async with one_printer.session_maker() as db:
        assert (await db.get(PrintQueueItem, item_id)).ams_mapping == "[2]"


@pytest.mark.asyncio
async def test_an_item_moved_meanwhile_is_not_sent_to_its_old_printer(one_printer):  # noqa: F811
    """Nor given a mapping worked out for its old printer: the pass would
    clear the bogus all-unresolved one it loaded."""
    item_id = await _pinned_item(one_printer, ams_mapping="[-1]")
    async with one_printer.session_maker() as db:
        other = Printer(name="Other", serial_number="OTHER", ip_address="10.0.0.2", access_code="code", model="X1C")
        db.add(other)
        await db.commit()
        other_id = other.id

    async with _scheduler(one_printer, idle=True) as scheduler:
        with _edit_during_the_pass(one_printer, scheduler, printer_id=other_id):
            await scheduler.check_queue()
            assert not scheduler._inflight

    async with one_printer.session_maker() as db:
        item = await db.get(PrintQueueItem, item_id)
    assert (item.status, item.printer_id, item.ams_mapping) == ("pending", other_id, "[-1]")


@pytest.mark.asyncio
async def test_the_claim_requires_the_printer_the_item_was_selected_for(one_printer):  # noqa: F811
    """A move landing after the re-read: the dispatch's claim still refuses it."""
    from backend.app.services.print_scheduler import PrintScheduler

    item_id = await one_printer.add_item()
    scheduler = PrintScheduler()

    async with one_printer.session_maker() as db:
        assert not await scheduler._claim_for_dispatch(db, item_id, one_printer.printer_id + 1)
        assert await scheduler._claim_for_dispatch(db, item_id, one_printer.printer_id)


@pytest.mark.asyncio
async def test_the_power_on_wait_holds_no_transaction(one_printer):  # noqa: F811
    """RC-C8: 30-180 s of waiting for a printer to boot with the pass's
    transaction open pins a connection (PostgreSQL) or a stale snapshot."""
    from unittest.mock import MagicMock

    from backend.app.services import print_scheduler as scheduler_module
    from backend.app.services.print_scheduler import PrintScheduler

    scheduler = PrintScheduler()
    order: list[str] = []
    plug = MagicMock(name="plug")
    plug.name = "Plug"
    service = MagicMock(
        get_status=AsyncMock(return_value={"reachable": True, "state": "ON"}), turn_on=AsyncMock(return_value=True)
    )

    async def sleep(_seconds):
        order.append("sleep")

    async with one_printer.session_maker() as db:
        real_commit = db.commit

        async def commit():
            order.append("commit")
            await real_commit()

        with (
            patch.object(scheduler_module.smart_plug_manager, "get_service_for_plug", AsyncMock(return_value=service)),
            patch.object(scheduler_module.asyncio, "sleep", sleep),
            patch.object(scheduler_module.printer_manager, "connect_printer", AsyncMock(return_value=True)),
            patch.object(db, "commit", commit),
        ):
            assert await scheduler._power_on_and_wait(plug, one_printer.printer_id, db)

    assert order[:2] == ["commit", "sleep"]


@pytest.mark.asyncio
async def test_an_item_cancelled_meanwhile_is_left_as_cancelled(one_printer):  # noqa: F811
    item_id = await _pinned_item(one_printer, ams_mapping="[-1]")

    async with _scheduler(one_printer, idle=True) as scheduler:
        with _edit_during_the_pass(one_printer, scheduler, status="cancelled"):
            await scheduler.check_queue()

    async with one_printer.session_maker() as db:
        item = await db.get(PrintQueueItem, item_id)
    assert (item.status, item.ams_mapping) == ("cancelled", "[-1]")


@pytest.mark.asyncio
async def test_another_plate_chosen_meanwhile_is_not_mapped_from_the_old_one(one_printer):  # noqa: F811
    """The new mapping belongs to the new plate; checked against the old
    plate's filaments it would be thrown away."""
    item_id = await _pinned_item(one_printer, ams_mapping="[-1]")

    async with _scheduler(one_printer, idle=True) as scheduler:
        with _edit_during_the_pass(one_printer, scheduler, plate_id=2, ams_mapping="[3]"):
            await scheduler.check_queue()
            assert not scheduler._inflight

    async with one_printer.session_maker() as db:
        item = await db.get(PrintQueueItem, item_id)
    assert (item.plate_id, item.ams_mapping) == (2, "[3]")


@pytest.mark.asyncio
async def test_a_model_job_pinned_meanwhile_is_not_sent_to_the_printer_it_was_offered(one_printer):  # noqa: F811
    item_id = await _pinned_item(one_printer)
    async with one_printer.session_maker() as db:
        other = Printer(name="Other", serial_number="OTHER", ip_address="10.0.0.2", access_code="code", model="X1C")
        db.add(other)
        await db.commit()
        other_id = other.id
        await db.execute(
            update(PrintQueueItem).where(PrintQueueItem.id == item_id).values(printer_id=None, target_model="X1C")
        )
        await db.commit()

    async with _scheduler(one_printer, idle=True) as scheduler:
        with _edit_during_the_pass(one_printer, scheduler, printer_id=other_id, target_model=None):
            await scheduler.check_queue()
            assert not scheduler._inflight

    async with one_printer.session_maker() as db:
        item = await db.get(PrintQueueItem, item_id)
    assert (item.status, item.printer_id, item.target_model) == ("pending", other_id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "edit",
    [
        {"plate_id": 2},
        {"filament_overrides": '[{"slot_id": 1, "type": "PETG"}]'},
        {"skip_filament_check": True},
        {"target_model": "P1S"},
        {"target_location": "Lab"},
    ],
)
async def test_any_edit_to_what_the_dispatch_is_worked_out_from_waits_a_round(one_printer, edit):  # noqa: F811
    await _pinned_item(one_printer)

    async with _scheduler(one_printer, idle=True) as scheduler:
        with _edit_during_the_pass(one_printer, scheduler, **edit):
            await scheduler.check_queue()
            assert not scheduler._inflight
