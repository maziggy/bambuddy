"""Regression tests for the runtime-tracking task (#1521).

The ``runtime_seconds`` counter on each printer feeds hours-based maintenance
intervals (rod lubrication, belt checks, nozzle cleaning). It was accumulating
elapsed time whenever ``state.state`` was ``RUNNING`` *or* ``PAUSE``, which
meant a print paused for hours (e.g. overnight) inflated the maintenance
clock without any actual mechanical wear. Fix excludes PAUSE; these tests
pin the new contract.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


async def _build_db_with_printer(*, runtime_seconds: int, last_runtime_update: datetime | None):
    """Spin up an in-memory DB with one active printer in the requested state."""
    import backend.app.models  # noqa: F401  -- register all models on Base.metadata
    from backend.app.core.database import Base
    from backend.app.models.printer import Printer

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_maker() as db:
        db.add(
            Printer(
                id=1,
                name="P1",
                serial_number="S1",
                ip_address="1.1.1.1",
                access_code="x",
                is_active=True,
                runtime_seconds=runtime_seconds,
                last_runtime_update=last_runtime_update,
            )
        )
        await db.commit()
    return engine, session_maker


async def _run_one_iteration(session_maker, state_value: str):
    """Run a single iteration of track_printer_runtime() against a mocked state.

    Patches ``asyncio.sleep`` to skip the startup wait and cancel after the
    first loop tick. Patches ``printer_manager.get_status`` to return a fake
    state with the requested ``.state`` value. Points the module-level
    ``async_session`` at the test DB so the loop's queries hit it.
    """
    from backend.app import main as app_main

    sleep_calls = {"count": 0}
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds, *args, **kwargs):
        sleep_calls["count"] += 1
        # First sleep = the 15s startup wait. Second sleep = end-of-iteration
        # tick; raise here so the loop exits cleanly via its CancelledError
        # handler after exactly one work cycle.
        if sleep_calls["count"] >= 2:
            raise asyncio.CancelledError()
        # Yield control to keep the event loop healthy without blocking.
        await real_sleep(0)

    fake_state = SimpleNamespace(state=state_value, connected=True)

    # The loop's tail-of-iteration sleep is OUTSIDE its try/except, so the
    # CancelledError raised from fake_sleep propagates out of the function
    # rather than triggering the inner break — catch it at the test boundary.
    with (
        patch.object(app_main, "async_session", session_maker),
        patch.object(app_main.printer_manager, "get_status", return_value=fake_state),
        patch.object(app_main.asyncio, "sleep", fake_sleep),
        pytest.raises(asyncio.CancelledError),
    ):
        await app_main.track_printer_runtime()


@pytest.mark.asyncio
async def test_pause_state_does_not_accumulate_runtime():
    """PAUSE must NOT add to runtime_seconds — paused = no motion = no wear (#1521)."""
    seeded_runtime = 1000  # 1000s already accumulated
    seeded_last_update = datetime.now(timezone.utc) - timedelta(seconds=300)  # 5min ago
    engine, session_maker = await _build_db_with_printer(
        runtime_seconds=seeded_runtime, last_runtime_update=seeded_last_update
    )

    await _run_one_iteration(session_maker, state_value="PAUSE")

    from backend.app.models.printer import Printer

    async with session_maker() as db:
        row = (await db.execute(select(Printer).where(Printer.id == 1))).scalar_one()
        # Runtime counter unchanged — the 5 minutes paused contributed nothing.
        assert row.runtime_seconds == seeded_runtime
        # last_runtime_update cleared on the non-running branch so the next
        # transition to RUNNING starts fresh and doesn't back-bill paused time.
        assert row.last_runtime_update is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_running_state_still_accumulates_runtime():
    """RUNNING must continue to accumulate — the bug was scope, not the whole feature."""
    seeded_runtime = 1000
    seeded_last_update = datetime.now(timezone.utc) - timedelta(seconds=60)
    engine, session_maker = await _build_db_with_printer(
        runtime_seconds=seeded_runtime, last_runtime_update=seeded_last_update
    )

    await _run_one_iteration(session_maker, state_value="RUNNING")

    from backend.app.models.printer import Printer

    async with session_maker() as db:
        row = (await db.execute(select(Printer).where(Printer.id == 1))).scalar_one()
        # Wall-clock elapsed since seeded_last_update should now be added.
        # Allow a generous lower bound (≥30s) — actual elapsed depends on
        # how fast the test runs, but it MUST have grown past the seed.
        assert row.runtime_seconds > seeded_runtime
        assert row.runtime_seconds >= seeded_runtime + 30
        assert row.last_runtime_update is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_idle_state_clears_last_update_without_accumulating():
    """A non-active state (FINISH/IDLE/PREPARE/etc.) must clear last_runtime_update
    so a later RUNNING transition doesn't retroactively back-bill all the idle time."""
    seeded_runtime = 1000
    seeded_last_update = datetime.now(timezone.utc) - timedelta(seconds=3600)  # 1h ago
    engine, session_maker = await _build_db_with_printer(
        runtime_seconds=seeded_runtime, last_runtime_update=seeded_last_update
    )

    await _run_one_iteration(session_maker, state_value="FINISH")

    from backend.app.models.printer import Printer

    async with session_maker() as db:
        row = (await db.execute(select(Printer).where(Printer.id == 1))).scalar_one()
        assert row.runtime_seconds == seeded_runtime  # no accumulation
        assert row.last_runtime_update is None  # cleared, prevents back-bill
    await engine.dispose()
