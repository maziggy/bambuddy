"""Scheduler pre-dispatch filament-deficit guard tests (#1496).

``PrintScheduler._block_on_filament_deficit`` is the gate that keeps an
auto_dispatch=True VP intake (or any other scheduler-driven dispatch) from
sending a print onto a spool that can't satisfy it. On a deficit it
promotes the item to manual_start; when a previously-flagged item's spool
is now adequate it clears the flag so the next tick dispatches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.filament_deficit import FilamentDeficit
from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
def scheduler():
    """A fresh scheduler instance — internal state is not exercised."""
    return PrintScheduler()


@pytest.fixture
def queue_item(db_session, printer_factory):
    """Helper to drop a queue item the helper can mutate."""

    async def _make(**overrides):
        printer = await printer_factory()
        defaults = {
            "printer_id": printer.id,
            "status": "pending",
            "manual_start": False,
            "filament_short": False,
        }
        defaults.update(overrides)
        item = PrintQueueItem(**defaults)
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)
        return item

    return _make


@pytest.mark.asyncio
async def test_blocks_on_deficit_promotes_to_manual_start(scheduler, db_session, queue_item):
    item = await queue_item()
    with patch(
        "backend.app.services.print_scheduler.compute_deficit_for_queue_item",
        AsyncMock(
            return_value=[
                FilamentDeficit(
                    slot_id=1,
                    ams_id=0,
                    tray_id=0,
                    filament_type="PLA",
                    required_grams=270.0,
                    remaining_grams=200.0,
                ),
            ]
        ),
    ):
        blocked = await scheduler._block_on_filament_deficit(db_session, item)

    assert blocked is True
    await db_session.refresh(item)
    assert item.manual_start is True
    assert item.filament_short is True


@pytest.mark.asyncio
async def test_clears_stale_flag_when_deficit_resolves(scheduler, db_session, queue_item):
    """Previously-flagged item whose spool was swapped is unblocked."""
    item = await queue_item(filament_short=True, manual_start=False)
    with patch(
        "backend.app.services.print_scheduler.compute_deficit_for_queue_item",
        AsyncMock(return_value=[]),
    ):
        blocked = await scheduler._block_on_filament_deficit(db_session, item)

    assert blocked is False
    await db_session.refresh(item)
    assert item.filament_short is False
    assert item.manual_start is False


@pytest.mark.asyncio
async def test_no_deficit_no_op(scheduler, db_session, queue_item):
    """Happy path — no deficit, no flag changes, dispatch proceeds."""
    item = await queue_item()
    with patch(
        "backend.app.services.print_scheduler.compute_deficit_for_queue_item",
        AsyncMock(return_value=[]),
    ):
        blocked = await scheduler._block_on_filament_deficit(db_session, item)

    assert blocked is False
    await db_session.refresh(item)
    assert item.filament_short is False
    assert item.manual_start is False


@pytest.mark.asyncio
async def test_helper_exception_does_not_wedge_dispatch(scheduler, db_session, queue_item):
    """A flaky deficit check (e.g. Spoolman timeout) must not block dispatch."""
    item = await queue_item()
    with patch(
        "backend.app.services.print_scheduler.compute_deficit_for_queue_item",
        AsyncMock(side_effect=RuntimeError("network down")),
    ):
        blocked = await scheduler._block_on_filament_deficit(db_session, item)

    assert blocked is False
    await db_session.refresh(item)
    assert item.filament_short is False
