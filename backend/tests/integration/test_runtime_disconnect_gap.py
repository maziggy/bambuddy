"""A printer's time offline is not counted as runtime (DB-C5).

The runtime counter adds the time since its last tick while a printer is
RUNNING. When a printer dropped off mid-print and came back hours later with a
print running, that first tick added the whole gap: hours-based maintenance
came due early. The timestamp is now cleared when a printer this process saw
connected goes offline. A Bambuddy restart is still counted from the stored
timestamp, as before -- those printers have not been seen connected yet.
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app import main as main_module
from backend.app.models.printer import Printer

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _forget_seen():
    main_module._runtime_seen_connected.clear()
    yield
    main_module._runtime_seen_connected.clear()


async def _ticks(test_engine, states: list) -> None:
    """Run the tracker for one tick per state, then stop it."""
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    feed = iter(states)
    current = {"state": None}
    sleeps = {"n": 0}

    async def fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] > len(states):
            raise asyncio.CancelledError
        current["state"] = next(feed)

    with (
        patch.object(main_module, "async_session", maker),
        patch.object(main_module.asyncio, "sleep", fake_sleep),
        patch.object(main_module.printer_manager, "get_status", lambda _pid: current["state"]),
    ):
        try:
            await main_module.track_printer_runtime()
        except asyncio.CancelledError:
            pass


async def _printer(db_session, printer_factory, last_update):
    printer = await printer_factory()
    printer.runtime_seconds = 1000
    printer.last_runtime_update = last_update
    await db_session.commit()
    return printer


async def test_a_disconnect_clears_the_timestamp(test_engine, db_session, printer_factory):
    printer = await _printer(db_session, printer_factory, datetime.utcnow() - timedelta(seconds=60))
    running = SimpleNamespace(connected=True, state="RUNNING")
    offline = SimpleNamespace(connected=False, state="RUNNING")

    await _ticks(test_engine, [running, offline])

    await db_session.refresh(printer)
    assert printer.last_runtime_update is None


async def test_a_restart_still_counts_the_stored_timestamp(test_engine, db_session, printer_factory):
    stored = datetime.utcnow() - timedelta(minutes=30)
    printer = await _printer(db_session, printer_factory, stored)
    # Still connecting after the restart, then printing.
    connecting = SimpleNamespace(connected=False, state="unknown")
    running = SimpleNamespace(connected=True, state="RUNNING")

    await _ticks(test_engine, [connecting, running])

    await db_session.refresh(printer)
    assert printer.runtime_seconds >= 1000 + 29 * 60


async def test_a_printer_still_offline_after_the_startup_grace_is_not_credited(
    test_engine, db_session, printer_factory, monkeypatch
):
    """It dropped off before the restart and never came back during it: the
    stored timestamp is from before the gap, not from a print that ran on."""
    monkeypatch.setattr(main_module, "RUNTIME_STARTUP_GRACE_SECONDS", 0)
    printer = await _printer(db_session, printer_factory, datetime.utcnow() - timedelta(hours=10))
    offline = SimpleNamespace(connected=False, state="unknown")

    await _ticks(test_engine, [offline])

    await db_session.refresh(printer)
    assert printer.last_runtime_update is None


async def test_a_clear_that_failed_is_tried_again(test_engine, db_session, printer_factory):
    printer = await _printer(db_session, printer_factory, datetime.utcnow() - timedelta(seconds=60))
    running = SimpleNamespace(connected=True, state="RUNNING")
    offline = SimpleNamespace(connected=False, state="RUNNING")
    real_get = AsyncSession.get
    calls = {"n": 0}

    async def flaky_get(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return await real_get(self, *args, **kwargs)

    with patch.object(AsyncSession, "get", flaky_get):
        await _ticks(test_engine, [running, offline, offline])

    await db_session.refresh(printer)
    assert printer.last_runtime_update is None
