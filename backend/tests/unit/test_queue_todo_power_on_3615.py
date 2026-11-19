"""Smart-plug power-ons and the "To do" list: failed ones are listed, a wake keeps the filament entry (#3615)."""

from unittest.mock import AsyncMock

import pytest

from backend.app.services.print_scheduler import PrintScheduler
from backend.app.services.queue_todo import FilamentHold, TodoFilament
from backend.tests.unit.test_monitor_only_plugs_3583 import _add_typed_plug
from backend.tests.unit.test_scheduler_class_target_smart_plug_2786 import (  # noqa: F401 - fixture
    _add_item,
    _add_plug,
    _external,
    _get_item,
    _run,
    _woken_printer_ids,
    queue_db,
)


@pytest.mark.asyncio
async def test_pinned_power_on_failure_is_recorded_per_pass(queue_db):
    scheduler = PrintScheduler()
    await _add_typed_plug(queue_db, 1, "rest")
    item_id = await _add_item(queue_db, printer_id=1)

    await _run(queue_db, scheduler, power_on=AsyncMock(return_value=False))

    assert "could not power it on" in ((await _get_item(queue_db, item_id)).waiting_reason or "")
    assert scheduler.power_on_failed_printers() == {1}

    # Next pass the printer powers on: the record is swapped out.
    await _run(queue_db, scheduler, power_on=AsyncMock(return_value=True))
    assert 1 not in scheduler.power_on_failed_printers()


@pytest.mark.asyncio
async def test_a_wake_keeps_the_filament_wait_on_the_other_printers(queue_db):
    """The pass that switches a printer on ends early for the job; its entry must not vanish for it."""
    scheduler = PrintScheduler()
    await _add_plug(queue_db, 1)
    await _add_plug(queue_db, 2)
    item_id = await _add_item(queue_db, target_model="X1C", required_filament_types=["PETG"])
    # Printer 1 last reported PLA only, so it is not woken; printer 2 is.
    statuses = {1: _external("PLA", "FFFFFFFF"), 2: _external("PETG", "FFFFFFFF")}

    power_on = await _run(queue_db, scheduler, power_on=AsyncMock(return_value=True), statuses=statuses)

    assert _woken_printer_ids(power_on) == [2]
    assert scheduler.filament_holds_snapshot() == {
        item_id: [FilamentHold(item_id, 1, "X1C", (TodoFilament("PETG", ""),))]
    }
