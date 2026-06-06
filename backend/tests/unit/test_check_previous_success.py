"""Tests for `PrintScheduler._check_previous_success` (#1667).

Pre-fix behaviour: the lookback `.in_([...])` list excluded `cancelled` and
included `skipped`, so a single user-cancelled print blocked every downstream
item with `require_previous_success=True` permanently (the reporter saw 18
items blocked over 3 days from one cancellation, because each new skip
became the next skip's "failed predecessor").

Post-fix behaviour:
- `cancelled` is a neutral outcome → returns True (a deliberate user action
  is not a print failure)
- `skipped` is excluded from the lookback → an already-skipped item never
  counts as a predecessor; the query walks back to the most recent real
  print attempt
- `failed` and `aborted` still gate as before
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
def scheduler():
    return PrintScheduler()


@pytest.fixture
def queue_factory(db_session, printer_factory):
    """Helper to drop completed/failed/cancelled/skipped queue items in order.

    Each call assigns a monotonically increasing `completed_at` so the
    scheduler's `ORDER BY completed_at DESC` reliably picks the latest as
    the predecessor. `printer_id` is shared so all items count.
    """
    base_time = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)
    counter = {"n": 0}
    printer_holder: dict = {}

    async def _make_printer():
        if "p" not in printer_holder:
            printer_holder["p"] = await printer_factory()
        return printer_holder["p"]

    async def _add(status: str, error_message: str | None = None) -> PrintQueueItem:
        printer = await _make_printer()
        counter["n"] += 1
        item = PrintQueueItem(
            printer_id=printer.id,
            status=status,
            error_message=error_message,
            completed_at=base_time + timedelta(minutes=counter["n"]),
            require_previous_success=True,
        )
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)
        return item

    async def _add_pending() -> PrintQueueItem:
        printer = await _make_printer()
        item = PrintQueueItem(
            printer_id=printer.id,
            status="pending",
            require_previous_success=True,
        )
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)
        return item

    return {"add": _add, "add_pending": _add_pending}


@pytest.mark.asyncio
async def test_no_previous_item_returns_true(scheduler, db_session, queue_factory):
    """First item in the queue has no predecessor → always passes."""
    pending = await queue_factory["add_pending"]()
    assert await scheduler._check_previous_success(db_session, pending) is True


@pytest.mark.asyncio
async def test_previous_completed_returns_true(scheduler, db_session, queue_factory):
    await queue_factory["add"]("completed")
    pending = await queue_factory["add_pending"]()
    assert await scheduler._check_previous_success(db_session, pending) is True


@pytest.mark.asyncio
async def test_previous_failed_returns_false(scheduler, db_session, queue_factory):
    await queue_factory["add"]("failed")
    pending = await queue_factory["add_pending"]()
    assert await scheduler._check_previous_success(db_session, pending) is False


@pytest.mark.asyncio
async def test_previous_aborted_returns_false(scheduler, db_session, queue_factory):
    """A printer-detected abort (e.g. clogged nozzle) is a real failure → blocks."""
    await queue_factory["add"]("aborted")
    pending = await queue_factory["add_pending"]()
    assert await scheduler._check_previous_success(db_session, pending) is False


@pytest.mark.asyncio
async def test_previous_cancelled_returns_true_bug_a(scheduler, db_session, queue_factory):
    """#1667 bug A: user cancellation is deliberate, not a failure → passes."""
    await queue_factory["add"]("cancelled")
    pending = await queue_factory["add_pending"]()
    assert await scheduler._check_previous_success(db_session, pending) is True


@pytest.mark.asyncio
async def test_skipped_predecessor_is_walked_past_bug_b(scheduler, db_session, queue_factory):
    """#1667 bug B: a skipped item is not an attempt — query walks back to the
    most recent real outcome instead of treating skipped as failed."""
    await queue_factory["add"]("completed")  # real predecessor that should be found
    await queue_factory["add"]("skipped", "Previous print failed or was aborted")
    pending = await queue_factory["add_pending"]()
    assert await scheduler._check_previous_success(db_session, pending) is True


@pytest.mark.asyncio
async def test_only_skipped_history_returns_true(scheduler, db_session, queue_factory):
    """Edge case: every prior item is skipped → no real predecessor found,
    returns True (first-in-queue semantics)."""
    await queue_factory["add"]("skipped", "Previous print failed or was aborted")
    await queue_factory["add"]("skipped", "Previous print failed or was aborted")
    pending = await queue_factory["add_pending"]()
    assert await scheduler._check_previous_success(db_session, pending) is True


@pytest.mark.asyncio
async def test_cascade_reporters_scenario(scheduler, db_session, queue_factory):
    """The exact #1667 reporter scenario: failed → cancelled → skipped → pending.

    Pre-fix: pending blocked because the buggy lookback walked past the
    cancelled item (excluded) and the prior skipped item (included), found
    the failed item, and returned False.
    Post-fix: cancelled is the predecessor (skipped is excluded; cancelled
    is included and passes), pending dispatches.
    """
    await queue_factory["add"]("failed")
    await queue_factory["add"]("cancelled")
    await queue_factory["add"]("skipped", "Previous print failed or was aborted")
    pending = await queue_factory["add_pending"]()
    assert await scheduler._check_previous_success(db_session, pending) is True


@pytest.mark.asyncio
async def test_failed_then_cancelled_still_passes(scheduler, db_session, queue_factory):
    """User cancelled after a failure → most recent action wins. The cancellation
    is the user explicitly choosing to move on, so dispatching the next item
    respects their intent."""
    await queue_factory["add"]("failed")
    await queue_factory["add"]("cancelled")
    pending = await queue_factory["add_pending"]()
    assert await scheduler._check_previous_success(db_session, pending) is True


@pytest.mark.asyncio
async def test_completed_then_failed_blocks(scheduler, db_session, queue_factory):
    """Regression guard: a real failure after a previously-successful print
    still gates downstream items. Only the MOST RECENT outcome matters."""
    await queue_factory["add"]("completed")
    await queue_factory["add"]("failed")
    pending = await queue_factory["add_pending"]()
    assert await scheduler._check_previous_success(db_session, pending) is False
