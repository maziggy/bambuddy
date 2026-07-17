"""Cross-model dispatch gate (#2578).

A queue row can carry a target_model that does not match the model its 3MF was
sliced for (pre-fix UI wrote such rows silently; direct API writes could too).
G-code is only interchangeable within an explicit family — everything else must
be held back at the dispatch boundary, because model-based assignment has no
human in the loop to catch it.

Covers the pure helper (``is_gcode_compatible``) and the scheduler behaviour:
a mismatched pending item is never offered a printer and gets an actionable
``waiting_reason`` instead of being dispatched.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import PrintScheduler
from backend.app.utils.printer_models import is_gcode_compatible

# ---------------------------------------------------------------------------
# is_gcode_compatible
# ---------------------------------------------------------------------------


def test_same_model_is_compatible():
    assert is_gcode_compatible("X1C", "X1C")
    assert is_gcode_compatible("H2D", "H2D")


def test_x1_p1_family_is_interchangeable():
    # Intentional mixed-farm workflow: X1-sliced jobs on P1S/P1P and back.
    assert is_gcode_compatible("X1C", "P1S")
    assert is_gcode_compatible("X1C", "P1P")
    assert is_gcode_compatible("P1S", "X1E")
    assert is_gcode_compatible("X1", "P1P")


def test_cross_family_is_blocked():
    # The reporter's case: X1C-sliced G-code targeted at an H2D.
    assert not is_gcode_compatible("X1C", "H2D")
    assert not is_gcode_compatible("P1S", "H2D")
    assert not is_gcode_compatible("X1C", "A1")
    assert not is_gcode_compatible("A1", "A1 Mini")
    assert not is_gcode_compatible("H2D", "H2S")
    assert not is_gcode_compatible("X1C", "P2S")


def test_unknown_metadata_is_failsafe_compatible():
    # Legacy files without sliced_for_model can't be validated — never block.
    assert is_gcode_compatible(None, "H2D")
    assert is_gcode_compatible("X1C", None)
    assert is_gcode_compatible(None, None)
    assert is_gcode_compatible("", "H2D")


def test_normalization_spaces_dashes_case():
    assert is_gcode_compatible("x1c", "X1C")
    assert is_gcode_compatible("A1 Mini", "A1-MINI")
    assert is_gcode_compatible("H2D Pro", "H2DPRO")


def test_internal_codes_resolve_to_short_names():
    # slice_info printer_model_id codes compare equal to their short names.
    assert is_gcode_compatible("C11", "X1C")
    assert is_gcode_compatible("O1D", "H2D")
    assert is_gcode_compatible("C11", "P1S")  # X1C → family with P1S
    assert not is_gcode_compatible("C11", "H2D")


# ---------------------------------------------------------------------------
# Scheduler: mismatched rows are held back, not dispatched
# ---------------------------------------------------------------------------


@pytest.fixture
async def queue_db():
    """In-memory DB seeded with one idle H2D printer."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        db.add(
            Printer(
                id=1,
                name="H2D-1",
                serial_number="H2D0001",
                ip_address="10.0.0.1",
                access_code="x",
                model="H2D",
                is_active=True,
            )
        )
        await db.commit()

    try:
        yield SimpleNamespace(session_maker=session_maker)
    finally:
        await engine.dispose()


async def _add_model_item(ctx, *, target_model, sliced_for_model=None, library_meta=None):
    """Seed one pending model-based queue item, archive- or library-backed."""
    async with ctx.session_maker() as db:
        if library_meta is not None:
            source = LibraryFile(
                filename="job.3mf",
                file_path="/library/job.3mf",
                file_size=10,
                file_type="3mf",
                file_metadata=library_meta,
            )
            db.add(source)
            await db.flush()
            item = PrintQueueItem(library_file_id=source.id, target_model=target_model, status="pending", position=1)
        else:
            source = PrintArchive(
                filename="job.3mf",
                file_path="archives/job.3mf",
                file_size=10,
                status="completed",
                sliced_for_model=sliced_for_model,
            )
            db.add(source)
            await db.flush()
            item = PrintQueueItem(archive_id=source.id, target_model=target_model, status="pending", position=1)
        db.add(item)
        await db.commit()
        return item.id


async def _run_check_queue(ctx, scheduler, finder, waiting_notification):
    patches = [
        patch("backend.app.services.print_scheduler.async_session", ctx.session_maker),
        patch("backend.app.core.database.async_session", ctx.session_maker),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_waiting",
            waiting_notification,
        ),
        patch.object(scheduler, "_find_idle_printer_for_model", finder),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return await scheduler.check_queue()


async def _get_item(ctx, item_id):
    async with ctx.session_maker() as db:
        return (await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


@pytest.mark.asyncio
async def test_mismatched_item_is_held_and_never_offered_a_printer(queue_db):
    """target_model=H2D on an X1C-sliced archive: the matcher must not even run."""
    item_id = await _add_model_item(queue_db, target_model="H2D", sliced_for_model="X1C")
    scheduler = PrintScheduler()
    finder = AsyncMock(return_value=(1, None))  # would happily offer the H2D
    waiting = AsyncMock()

    await _run_check_queue(queue_db, scheduler, finder, waiting)

    finder.assert_not_awaited()
    item = await _get_item(queue_db, item_id)
    assert item.status == "pending"
    assert item.printer_id is None
    assert "sliced for X1C" in item.waiting_reason
    # Actionable reason → the user is notified once on transition
    waiting.assert_awaited_once()


@pytest.mark.asyncio
async def test_mismatched_library_item_is_held(queue_db):
    """Same gate for library-file-backed rows (metadata JSON, not a column)."""
    item_id = await _add_model_item(queue_db, target_model="H2D", library_meta={"sliced_for_model": "P1S"})
    scheduler = PrintScheduler()
    finder = AsyncMock(return_value=(1, None))

    await _run_check_queue(queue_db, scheduler, finder, AsyncMock())

    finder.assert_not_awaited()
    item = await _get_item(queue_db, item_id)
    assert item.status == "pending"
    assert "sliced for P1S" in item.waiting_reason


@pytest.mark.asyncio
async def test_compatible_and_unknown_items_still_reach_the_matcher(queue_db):
    """Family-compatible (X1C→P1S would be, but here same-model) and
    metadata-less rows must keep flowing to the printer matcher."""
    item_id = await _add_model_item(queue_db, target_model="H2D", sliced_for_model="H2D")
    scheduler = PrintScheduler()
    # Matcher returns no printer so the pass stops after the gate — all we
    # assert is that the gate let the item through.
    finder = AsyncMock(return_value=(None, "Busy: H2D-1 (Printing)"))

    await _run_check_queue(queue_db, scheduler, finder, AsyncMock())

    finder.assert_awaited_once()
    item = await _get_item(queue_db, item_id)
    assert item.status == "pending"
    assert item.waiting_reason == "Busy: H2D-1 (Printing)"


@pytest.mark.asyncio
async def test_legacy_item_without_metadata_reaches_the_matcher(queue_db):
    item_id = await _add_model_item(queue_db, target_model="H2D", sliced_for_model=None)
    scheduler = PrintScheduler()
    finder = AsyncMock(return_value=(None, "Busy: H2D-1 (Printing)"))

    await _run_check_queue(queue_db, scheduler, finder, AsyncMock())

    finder.assert_awaited_once()
    item = await _get_item(queue_db, item_id)
    assert item.status == "pending"
