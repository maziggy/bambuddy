"""A queue row that was never closed must not block the printer forever (#2829).

``on_print_complete`` refuses to close a row when the completion's subtask name
disagrees with the file it was dispatched with, so another print's completion
cannot end someone's job early. Nothing took the refusal back, though, and
``check_queue`` counts every ``printing`` row as a busy printer -- so one bad
comparison wedged that printer's queue until a human pressed cancel.

The name comparison is fixed separately; this is the net under it, for the next
name format nobody predicted.
"""

import types
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.services.print_scheduler import _STRANDED_PRINTING_GRACE_SECONDS, _terminal_queue_status

pytestmark = pytest.mark.integration


def _state(state="FINISH", connected=True):
    return types.SimpleNamespace(state=state, connected=connected)


async def _noop():
    return None


async def _noop_arg(*_args, **_kwargs):
    return None


class TestTerminalStatusMapping:
    @pytest.mark.parametrize(
        "printer_state,expected",
        [("FINISH", "completed"), ("FAILED", "failed"), ("IDLE", "cancelled")],
    )
    def test_terminal_states_imply_a_queue_status(self, printer_state, expected):
        """Mirrors what the MQTT completion path would have set, so a recovered
        row cannot disagree with one closed normally."""
        assert _terminal_queue_status(_state(printer_state)) == expected

    @pytest.mark.parametrize("printer_state", ["RUNNING", "PREPARE", "PAUSE", "SLICING", None])
    def test_a_busy_printer_implies_nothing(self, printer_state):
        assert _terminal_queue_status(_state(printer_state)) is None

    def test_a_disconnected_printer_implies_nothing(self):
        """Its `state` is whatever we last heard, which proves nothing about
        what the printer is doing now -- closing on it would be a guess."""
        assert _terminal_queue_status(_state("FINISH", connected=False)) is None

    def test_no_state_at_all_implies_nothing(self):
        assert _terminal_queue_status(None) is None


@pytest.mark.asyncio
class TestTheSweep:
    """Drives the real sweep against a real database."""

    @pytest.fixture
    def scheduler(self, test_engine):
        """The sweep opens its own session from the scheduler module, which the
        widespread `patch("backend.app.main.async_session")` does not reach --
        the same trap b5a34b7ba's own commit message describes. Patch it at the
        module, as the other scheduler integration tests do."""
        import backend.app.services.print_scheduler as scheduler_module
        from backend.app.services.print_scheduler import PrintScheduler

        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with patch.object(scheduler_module, "async_session", maker):
            yield PrintScheduler()

    async def _item(self, db_session, printer, status="printing"):
        from backend.app.models.print_queue import PrintQueueItem

        item = PrintQueueItem(printer_id=printer.id, status=status)
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)
        return item

    async def _status_of(self, db_session, item_id):
        from backend.app.models.print_queue import PrintQueueItem

        db_session.expire_all()
        return (await db_session.get(PrintQueueItem, item_id)).status

    async def test_a_row_is_left_alone_inside_the_grace_period(
        self, scheduler, db_session, printer_factory, monkeypatch
    ):
        """A real completion arrives seconds after the printer goes terminal.
        Closing early would race the normal path and beat it to the row."""
        printer = await printer_factory()
        item = await self._item(db_session, printer)
        monkeypatch.setattr(
            "backend.app.services.print_scheduler.printer_manager.get_status", lambda _pid: _state("FINISH")
        )

        await scheduler._close_stranded_printing_items()

        assert await self._status_of(db_session, item.id) == "printing"

    async def test_a_row_is_closed_once_the_grace_period_passes(
        self, scheduler, db_session, printer_factory, monkeypatch
    ):
        printer = await printer_factory()
        item = await self._item(db_session, printer)
        monkeypatch.setattr(
            "backend.app.services.print_scheduler.printer_manager.get_status", lambda _pid: _state("FINISH")
        )

        await scheduler._close_stranded_printing_items()
        # Age the clock rather than sleeping five minutes.
        scheduler._terminal_since[printer.id] -= _STRANDED_PRINTING_GRACE_SECONDS + 1
        await scheduler._close_stranded_printing_items()

        assert await self._status_of(db_session, item.id) == "completed"

    async def test_the_clock_restarts_when_the_printer_goes_busy_again(
        self, scheduler, db_session, printer_factory, monkeypatch
    ):
        """The grace period has to measure one unbroken terminal run. A printer
        that finished, started something else, and finished again must not have
        the two stretches added together."""
        printer = await printer_factory()
        item = await self._item(db_session, printer)
        state = _state("FINISH")
        monkeypatch.setattr("backend.app.services.print_scheduler.printer_manager.get_status", lambda _pid: state)

        await scheduler._close_stranded_printing_items()
        scheduler._terminal_since[printer.id] -= _STRANDED_PRINTING_GRACE_SECONDS + 1

        state.state = "RUNNING"
        await scheduler._close_stranded_printing_items()
        assert printer.id not in scheduler._terminal_since

        state.state = "FINISH"
        await scheduler._close_stranded_printing_items()

        assert await self._status_of(db_session, item.id) == "printing"

    async def test_a_disconnected_printer_is_never_closed_on(self, scheduler, db_session, printer_factory, monkeypatch):
        printer = await printer_factory()
        item = await self._item(db_session, printer)
        monkeypatch.setattr(
            "backend.app.services.print_scheduler.printer_manager.get_status",
            lambda _pid: _state("FINISH", connected=False),
        )

        await scheduler._close_stranded_printing_items()
        scheduler._terminal_since[printer.id] = 0.0  # as if it had been ages
        await scheduler._close_stranded_printing_items()

        assert await self._status_of(db_session, item.id) == "printing"

    async def test_the_failure_status_is_carried_over(self, scheduler, db_session, printer_factory, monkeypatch):
        printer = await printer_factory()
        item = await self._item(db_session, printer)
        monkeypatch.setattr(
            "backend.app.services.print_scheduler.printer_manager.get_status", lambda _pid: _state("FAILED")
        )

        await scheduler._close_stranded_printing_items()
        scheduler._terminal_since[printer.id] -= _STRANDED_PRINTING_GRACE_SECONDS + 1
        await scheduler._close_stranded_printing_items()

        assert await self._status_of(db_session, item.id) == "failed"

    async def test_rows_that_are_not_printing_are_ignored(self, scheduler, db_session, printer_factory, monkeypatch):
        printer = await printer_factory()
        pending = await self._item(db_session, printer, status="pending")
        monkeypatch.setattr(
            "backend.app.services.print_scheduler.printer_manager.get_status", lambda _pid: _state("FINISH")
        )

        await scheduler._close_stranded_printing_items()
        scheduler._terminal_since[printer.id] = 0.0
        await scheduler._close_stranded_printing_items()

        assert await self._status_of(db_session, pending.id) == "pending"

    async def test_a_completed_row_clears_the_clock(self, scheduler, db_session, printer_factory, monkeypatch):
        """Nothing printing means nothing to time, and a stale entry would give
        the next print a head start on its own grace period."""
        printer = await printer_factory()
        monkeypatch.setattr(
            "backend.app.services.print_scheduler.printer_manager.get_status", lambda _pid: _state("FINISH")
        )
        scheduler._terminal_since[printer.id] = 0.0

        await scheduler._close_stranded_printing_items()

        assert scheduler._terminal_since == {}

    async def test_the_scheduler_loop_actually_runs_the_sweep(self, scheduler, monkeypatch):
        """The sweep is only worth anything if the loop calls it.

        Without this, every test above passes against a build where the call
        was never wired in -- which is exactly what a mutation check caught.
        """
        called = []
        monkeypatch.setattr(scheduler, "_close_stranded_printing_items", lambda: called.append(True) or _noop())
        monkeypatch.setattr(scheduler, "_clear_stale_dispatch_claims", lambda **_kw: _noop())
        monkeypatch.setattr(scheduler, "_sample_chamber_temps", lambda: None)

        async def stop_after_one_pass():
            scheduler._running = False
            return False

        monkeypatch.setattr(scheduler, "check_queue", stop_after_one_pass)
        monkeypatch.setattr("backend.app.services.print_scheduler.asyncio.sleep", _noop_arg)

        await scheduler.run()

        assert called, "the scheduler loop never called the stranded-item sweep"

    async def test_a_broken_sweep_does_not_break_the_scheduler_loop(self, scheduler, monkeypatch):
        """It runs beside the dispatch-claim sweep on every tick. A recovery
        path that can take the loop down is worse than the strand it fixes."""
        monkeypatch.setattr(
            "backend.app.services.print_scheduler.printer_manager.get_status",
            lambda _pid: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        await scheduler._close_stranded_printing_items()  # must not raise
