"""Regression tests for ``_ams_assignment_locks`` (per-printer serialisation
of ``on_ams_change``'s spool-assignment block).

Background
==========

MQTT bursts can deliver two ``ams_data`` push frames for the same printer ~30
ms apart (observed in the wild: H2D + dual AMS at K-profile load + RFID-read
boundaries). Without serialisation, both ``on_ams_change`` callbacks read
"no assignment for ``(printer, ams, tray)``" in their respective sessions,
both call ``auto_assign_spool``, both ``INSERT``, and the second commit
violates ``spool_assignment_printer_id_ams_id_tray_id_key``:

    asyncpg.exceptions.UniqueViolationError: duplicate key value violates
    unique constraint "spool_assignment_printer_id_ams_id_tray_id_key"
    DETAIL:  Key (printer_id, ams_id, tray_id)=(1, 0, 0) already exists.

SQLite's WAL serialises writes so the bug stayed latent there for ~7 weeks.
It surfaced when optional Postgres support landed and asyncpg started
allowing true concurrent transactions.

These tests assert the lock primitive's properties, not the full
``on_ams_change`` flow — wiring the whole callback through a real DB at unit
scope would dwarf the size of the fix and add no signal beyond what the
existing integration suite already covers.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.app.main import _ams_assignment_locks, _get_ams_assignment_lock


@pytest.fixture(autouse=True)
def _isolate_locks_dict():
    """Each test gets a fresh module-level locks dict — otherwise prior
    tests' lazy-created locks leak across runs and a stale ``Lock`` object
    bound to an already-closed event loop trips uvloop's "Future attached to
    a different loop" assertion."""
    saved = dict(_ams_assignment_locks)
    _ams_assignment_locks.clear()
    try:
        yield
    finally:
        _ams_assignment_locks.clear()
        _ams_assignment_locks.update(saved)


class TestLockKeySeparation:
    def test_same_printer_returns_same_lock(self):
        """Two callbacks for the same printer must contend on the same lock —
        otherwise serialisation buys us nothing."""
        a = _get_ams_assignment_lock(7)
        b = _get_ams_assignment_lock(7)
        assert a is b

    def test_different_printers_get_different_locks(self):
        """Per-printer scope: one printer's slow assignment must not block
        unrelated printers from processing their own AMS pushes."""
        a = _get_ams_assignment_lock(7)
        b = _get_ams_assignment_lock(8)
        assert a is not b


class TestLockSerialisesConcurrentCallbacks:
    @pytest.mark.asyncio
    async def test_second_acquirer_waits_for_first(self):
        """The exact race the bug fix targets: two coroutines for the same
        printer must serialise inside the lock, so the second only enters
        the critical section after the first has committed."""
        printer_id = 42
        order: list[str] = []
        first_inside = asyncio.Event()
        first_release = asyncio.Event()

        async def first():
            async with _get_ams_assignment_lock(printer_id):
                order.append("first-enter")
                first_inside.set()
                # Hold the lock until the test allows release; this is what
                # gives the second coroutine a chance to queue up if the
                # primitive is doing its job.
                await first_release.wait()
                order.append("first-exit")

        async def second():
            await first_inside.wait()  # ensure first holds the lock
            async with _get_ams_assignment_lock(printer_id):
                order.append("second-enter")

        task_a = asyncio.create_task(first())
        task_b = asyncio.create_task(second())

        await first_inside.wait()
        # Yield the loop a few times so `second()` has every opportunity to
        # mistakenly enter early; without the lock, "second-enter" would land
        # before "first-exit".
        for _ in range(5):
            await asyncio.sleep(0)

        assert order == ["first-enter"]

        first_release.set()
        await asyncio.gather(task_a, task_b)

        assert order == ["first-enter", "first-exit", "second-enter"]

    @pytest.mark.asyncio
    async def test_different_printers_run_in_parallel(self):
        """Cross-printer independence: two callbacks for distinct printers
        must NOT block each other, otherwise a single slow printer would
        stall every other printer's AMS handling."""
        order: list[str] = []
        printer_a_inside = asyncio.Event()
        printer_a_release = asyncio.Event()

        async def printer_a():
            async with _get_ams_assignment_lock(1):
                order.append("a-enter")
                printer_a_inside.set()
                await printer_a_release.wait()
                order.append("a-exit")

        async def printer_b():
            await printer_a_inside.wait()
            async with _get_ams_assignment_lock(2):
                order.append("b-enter-and-exit")

        task_a = asyncio.create_task(printer_a())
        task_b = asyncio.create_task(printer_b())

        # Wait for printer_a to be holding the lock, then yield for printer_b.
        await printer_a_inside.wait()
        for _ in range(5):
            await asyncio.sleep(0)

        # printer_b must have entered AND exited its own lock while
        # printer_a is still holding lock A. If the locks were a single
        # global mutex, "b-enter-and-exit" would not yet appear.
        assert "b-enter-and-exit" in order
        assert "a-exit" not in order

        printer_a_release.set()
        await asyncio.gather(task_a, task_b)
