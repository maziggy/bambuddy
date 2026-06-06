"""Regression test for the cancellation-cascade recovery migration (#1667).

Pre-fix: the scheduler's `_check_previous_success` lookback included
`skipped` and excluded `cancelled`, so a single user-cancelled print
poisoned every downstream item with `require_previous_success=True`
indefinitely (reporter saw 18 items blocked over 3 days from one
cancellation).

This migration reverses the bug surgically: ONLY skipped items whose
immediate real predecessor (by `completed_at` desc, excluding skipped
items themselves) was `cancelled` get reset to `pending`. Items whose
true predecessor was `failed` or `aborted` stay skipped — those were
legitimate failure-gated skips.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.app.core.database import run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """Force the SQLite branch regardless of test env settings."""
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    from backend.app.core import database as database_module

    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


def _register_all_models():
    """run_migrations touches multiple tables; the full schema must exist."""
    from backend.app.models import (  # noqa: F401
        ams_history,
        ams_label,
        api_key,
        archive,
        color_catalog,
        external_link,
        filament,
        group,
        kprofile_note,
        maintenance,
        notification,
        notification_template,
        print_log,
        print_queue,
        printer,
        project,
        project_bom,
        settings,
        slot_preset,
        smart_plug,
        smart_plug_energy_snapshot,
        spool,
        spool_assignment,
        spool_catalog,
        spool_k_profile,
        spool_usage_history,
        spoolbuddy_device,
        user,
        user_email_pref,
        virtual_printer,
    )


@pytest.fixture
async def engine():
    from backend.app.core.database import Base

    _register_all_models()

    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


BASE_TIME = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)


async def _insert_queue_item(
    engine, *, id: int, printer_id: int, status: str, minutes_offset: int, error_message: str | None = None
) -> None:
    """Insert a print_queue row via the ORM so Python-side defaults
    (manual_start, position, bed_levelling, …) all apply without us having
    to mirror every NOT NULL column."""
    from backend.app.models.print_queue import PrintQueueItem

    async with AsyncSession(engine) as session:
        session.add(
            PrintQueueItem(
                id=id,
                printer_id=printer_id,
                status=status,
                error_message=error_message,
                completed_at=BASE_TIME + timedelta(minutes=minutes_offset),
                require_previous_success=True,
                position=id,
            )
        )
        await session.commit()


async def _get_status(engine, item_id: int) -> tuple[str, str | None]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT status, error_message FROM print_queue WHERE id = :id"), {"id": item_id})
        ).first()
    return row.status, row.error_message


@pytest.mark.asyncio
async def test_skipped_after_cancelled_resets_to_pending(engine):
    """Bug A + B: cancelled → skipped → migration resets the skipped item."""
    await _insert_queue_item(engine, id=10, printer_id=1, status="cancelled", minutes_offset=1)
    await _insert_queue_item(
        engine,
        id=11,
        printer_id=1,
        status="skipped",
        minutes_offset=2,
        error_message="Previous print failed or was aborted",
    )

    async with engine.begin() as conn:
        await run_migrations(conn)

    status, error_message = await _get_status(engine, 11)
    assert status == "pending"
    assert error_message is None


@pytest.mark.asyncio
async def test_skipped_after_failed_stays_skipped(engine):
    """Genuine failure-gated skip must NOT be reset — the user really did
    have a failure they need to deal with before downstream items run."""
    await _insert_queue_item(engine, id=20, printer_id=1, status="failed", minutes_offset=1)
    await _insert_queue_item(
        engine,
        id=21,
        printer_id=1,
        status="skipped",
        minutes_offset=2,
        error_message="Previous print failed or was aborted",
    )

    async with engine.begin() as conn:
        await run_migrations(conn)

    status, _ = await _get_status(engine, 21)
    assert status == "skipped"


@pytest.mark.asyncio
async def test_skipped_after_aborted_stays_skipped(engine):
    """Printer-detected abort is a real failure too — gate stays in place."""
    await _insert_queue_item(engine, id=30, printer_id=1, status="aborted", minutes_offset=1)
    await _insert_queue_item(
        engine,
        id=31,
        printer_id=1,
        status="skipped",
        minutes_offset=2,
        error_message="Previous print failed or was aborted",
    )

    async with engine.begin() as conn:
        await run_migrations(conn)

    status, _ = await _get_status(engine, 31)
    assert status == "skipped"


@pytest.mark.asyncio
async def test_skipped_with_other_error_message_untouched(engine):
    """Migration narrows on the exact buggy error string. A skipped item
    written by some other code path (different error_message) is left alone."""
    await _insert_queue_item(engine, id=40, printer_id=1, status="cancelled", minutes_offset=1)
    await _insert_queue_item(
        engine,
        id=41,
        printer_id=1,
        status="skipped",
        minutes_offset=2,
        error_message="Some other reason",
    )

    async with engine.begin() as conn:
        await run_migrations(conn)

    status, error_message = await _get_status(engine, 41)
    assert status == "skipped"
    assert error_message == "Some other reason"


@pytest.mark.asyncio
async def test_reporter_exact_cascade_resets_all_three(engine):
    """The reporter's exact pattern: failed → cancelled → skipped → skipped.
    Predecessors (by completed_at desc, skipped excluded) are cancelled for
    both stuck items, so both reset."""
    await _insert_queue_item(engine, id=50, printer_id=1, status="failed", minutes_offset=1)
    await _insert_queue_item(engine, id=51, printer_id=1, status="cancelled", minutes_offset=2)
    await _insert_queue_item(
        engine,
        id=52,
        printer_id=1,
        status="skipped",
        minutes_offset=3,
        error_message="Previous print failed or was aborted",
    )
    await _insert_queue_item(
        engine,
        id=53,
        printer_id=1,
        status="skipped",
        minutes_offset=4,
        error_message="Previous print failed or was aborted",
    )

    async with engine.begin() as conn:
        await run_migrations(conn)

    assert (await _get_status(engine, 52))[0] == "pending"
    assert (await _get_status(engine, 53))[0] == "pending"
    # The original failed/cancelled items are untouched
    assert (await _get_status(engine, 50))[0] == "failed"
    assert (await _get_status(engine, 51))[0] == "cancelled"


@pytest.mark.asyncio
async def test_migration_is_idempotent(engine):
    """Running the migration twice doesn't re-touch already-reset rows."""
    await _insert_queue_item(engine, id=60, printer_id=1, status="cancelled", minutes_offset=1)
    await _insert_queue_item(
        engine,
        id=61,
        printer_id=1,
        status="skipped",
        minutes_offset=2,
        error_message="Previous print failed or was aborted",
    )

    async with engine.begin() as conn:
        await run_migrations(conn)
    async with engine.begin() as conn:
        await run_migrations(conn)  # second pass should be a no-op

    status, _ = await _get_status(engine, 61)
    assert status == "pending"


@pytest.mark.asyncio
async def test_per_printer_isolation(engine):
    """A cancelled item on printer A must not affect a skipped item on
    printer B (different printer queues are independent)."""
    await _insert_queue_item(engine, id=70, printer_id=1, status="cancelled", minutes_offset=1)
    await _insert_queue_item(engine, id=71, printer_id=2, status="failed", minutes_offset=1)
    await _insert_queue_item(
        engine,
        id=72,
        printer_id=2,
        status="skipped",
        minutes_offset=2,
        error_message="Previous print failed or was aborted",
    )

    async with engine.begin() as conn:
        await run_migrations(conn)

    # printer 2's skipped item had a failed predecessor → stays skipped
    status, _ = await _get_status(engine, 72)
    assert status == "skipped"
