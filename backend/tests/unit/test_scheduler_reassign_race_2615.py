"""Reassign-during-dispatch race regression (#2615).

A queue row stays ``status='pending'`` for the whole (slow) FTP upload — status
only flips to ``printing`` at the very end. That left a window where a PATCH
could reassign ``printer_id`` mid-upload while the in-flight dispatch kept using
the old printer, splitting the queue row from the archive / expected-print /
physical command. The fix is a ``dispatching_at`` claim, stamped atomically
before any slow I/O, that the edit routes reject on and the scheduler won't
re-select. These tests cover the claim primitives, the guaranteed release, and
the startup reconciliation that clears a claim orphaned by a crash mid-dispatch.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
import backend.app.services.print_scheduler as scheduler_module
from backend.app.core.database import Base
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
async def ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as db:
        printer = Printer(name="P", serial_number="S", ip_address="127.0.0.1", access_code="c", model="X1C")
        db.add(printer)
        await db.flush()
        item = PrintQueueItem(printer_id=printer.id, status="pending")
        db.add(item)
        await db.commit()
        item_id = item.id

    try:
        yield SimpleNamespace(sm=sm, item_id=item_id, printer_id=printer.id)
    finally:
        await engine.dispose()


async def _get(ctx, item_id=None):
    async with ctx.sm() as db:
        return await db.get(PrintQueueItem, item_id or ctx.item_id)


@pytest.mark.asyncio
async def test_claim_stamps_pending_row_and_is_exclusive(ctx):
    sched = PrintScheduler()
    async with ctx.sm() as db:
        assert await sched._claim_for_dispatch(db, ctx.item_id) is True
    assert (await _get(ctx)).dispatching_at is not None

    # A second claim on an already-claimed row loses.
    async with ctx.sm() as db:
        assert await sched._claim_for_dispatch(db, ctx.item_id) is False


@pytest.mark.asyncio
async def test_claim_fails_on_non_pending_row(ctx):
    sched = PrintScheduler()
    async with ctx.sm() as db:
        item = await db.get(PrintQueueItem, ctx.item_id)
        item.status = "printing"
        await db.commit()
    async with ctx.sm() as db:
        assert await sched._claim_for_dispatch(db, ctx.item_id) is False
    assert (await _get(ctx)).dispatching_at is None


@pytest.mark.asyncio
async def test_clear_releases_the_claim(ctx):
    sched = PrintScheduler()
    async with ctx.sm() as db:
        await sched._claim_for_dispatch(db, ctx.item_id)
    async with ctx.sm() as db:
        await sched._clear_dispatch_claim(db, ctx.item_id)
    assert (await _get(ctx)).dispatching_at is None


@pytest.mark.asyncio
async def test_dispatch_one_claims_then_releases_around_start_print(ctx):
    sched = PrintScheduler()
    seen = {}

    async def fake_start_print(db, item):
        # Observe the claim is held while dispatch runs.
        row = await db.get(PrintQueueItem, item.id)
        seen["claimed_during"] = row.dispatching_at is not None

    with (
        patch.object(scheduler_module, "async_session", ctx.sm),
        patch.object(sched, "_start_print", side_effect=fake_start_print) as sp,
    ):
        await sched._dispatch_one(ctx.item_id)

    assert seen["claimed_during"] is True, "claim must be held while dispatch runs"
    sp.assert_awaited_once()
    # Released on exit so a deferred (still-pending) row can re-dispatch.
    assert (await _get(ctx)).dispatching_at is None


@pytest.mark.asyncio
async def test_dispatch_one_skips_an_already_claimed_row(ctx):
    sched = PrintScheduler()
    # Pre-claim the row (as if another worker owns it).
    async with ctx.sm() as db:
        await sched._claim_for_dispatch(db, ctx.item_id)

    with (
        patch.object(scheduler_module, "async_session", ctx.sm),
        patch.object(sched, "_start_print", new=AsyncMock()) as sp,
    ):
        await sched._dispatch_one(ctx.item_id)

    sp.assert_not_called()  # claim lost → no dispatch
    # And it must NOT clear the other worker's claim.
    assert (await _get(ctx)).dispatching_at is not None


@pytest.mark.asyncio
async def test_startup_reconciliation_clears_stale_claims(ctx):
    sched = PrintScheduler()
    async with ctx.sm() as db:
        await sched._claim_for_dispatch(db, ctx.item_id)
    assert (await _get(ctx)).dispatching_at is not None

    with patch.object(scheduler_module, "async_session", ctx.sm):
        await sched._clear_stale_dispatch_claims()

    assert (await _get(ctx)).dispatching_at is None, "a claim orphaned by a restart must be cleared"
