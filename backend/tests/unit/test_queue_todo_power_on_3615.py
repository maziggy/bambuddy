"""A pinned job's failed smart-plug power-on reaches the "To do" list (#3615)."""

from unittest.mock import AsyncMock

import pytest

from backend.app.services.print_scheduler import PrintScheduler
from backend.tests.unit.test_monitor_only_plugs_3583 import _add_typed_plug
from backend.tests.unit.test_scheduler_class_target_smart_plug_2786 import (  # noqa: F401 - fixture
    _add_item,
    _get_item,
    _run,
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
