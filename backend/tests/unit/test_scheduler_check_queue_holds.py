"""What check_queue holds back, run on a real database with the dispatch drained.

* An item whose creator may no longer use its printer (#1727) is held with a
  reason, not dispatched and not failed.
* A printer that already has an item printing takes no second one, even while
  it reports IDLE (the phantom-print seed).
"""

import pytest
from sqlalchemy import insert, update

from backend.app.models.group import Group, group_printers
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.tests.unit.test_scheduler_busy_reasons_3018 import _scheduler, one_printer  # noqa: F401


async def _restricted_creator(session_maker, allowed_printer_id: int) -> int:
    async with session_maker() as db:
        db.add(Settings(key="auth_enabled", value="true"))
        group = Group(name="team", permissions=["queue:create"], restrict_printers=True)
        user = User(username="member", password_hash="x", is_active=True, groups=[group])
        db.add_all([group, user])
        await db.flush()
        await db.execute(insert(group_printers).values(group_id=group.id, printer_id=allowed_printer_id))
        await db.commit()
        return user.id


async def _other_printer(session_maker) -> int:
    async with session_maker() as db:
        printer = Printer(name="Other", serial_number="OTHER", ip_address="10.0.0.2", access_code="code", model="X1C")
        db.add(printer)
        await db.commit()
        return printer.id


class TestACreatorWhoLostThePrinter:
    @pytest.mark.asyncio
    async def test_the_item_is_held_with_a_reason(self, one_printer):  # noqa: F811
        allowed = await _other_printer(one_printer.session_maker)
        creator = await _restricted_creator(one_printer.session_maker, allowed)
        item_id = await one_printer.add_item()
        async with one_printer.session_maker() as db:
            await db.execute(update(PrintQueueItem).where(PrintQueueItem.id == item_id).values(created_by_id=creator))
            await db.commit()

        async with _scheduler(one_printer, idle=True) as scheduler:
            await scheduler.check_queue()
            assert not scheduler._inflight

        async with one_printer.session_maker() as db:
            item = await db.get(PrintQueueItem, item_id)
        assert item.status == "pending"
        assert "no longer has access" in (item.waiting_reason or "")

    @pytest.mark.asyncio
    async def test_a_creator_who_still_has_it_is_dispatched(self, one_printer):  # noqa: F811
        creator = await _restricted_creator(one_printer.session_maker, one_printer.printer_id)
        item_id = await one_printer.add_item()
        async with one_printer.session_maker() as db:
            await db.execute(update(PrintQueueItem).where(PrintQueueItem.id == item_id).values(created_by_id=creator))
            await db.commit()

        async with _scheduler(one_printer, idle=True) as scheduler:
            await scheduler.check_queue()

        async with one_printer.session_maker() as db:
            assert (await db.get(PrintQueueItem, item_id)).status != "pending"


class TestAPrinterWithAnItemPrinting:
    @pytest.mark.asyncio
    async def test_takes_no_second_item_while_it_reports_idle(self, one_printer):  # noqa: F811
        printing_id = await one_printer.add_item(status="printing", position=0)
        pending_id = await one_printer.add_item(position=1)

        async with _scheduler(one_printer, idle=True) as scheduler:
            await scheduler.check_queue()
            assert not scheduler._inflight

        async with one_printer.session_maker() as db:
            assert (await db.get(PrintQueueItem, printing_id)).status == "printing"
            assert (await db.get(PrintQueueItem, pending_id)).status == "pending"

    @pytest.mark.asyncio
    async def test_without_it_the_same_item_goes_out(self, one_printer):  # noqa: F811
        """The control: nothing else holds this item back."""
        pending_id = await one_printer.add_item(position=1)

        async with _scheduler(one_printer, idle=True) as scheduler:
            await scheduler.check_queue()

        async with one_printer.session_maker() as db:
            assert (await db.get(PrintQueueItem, pending_id)).status != "pending"
