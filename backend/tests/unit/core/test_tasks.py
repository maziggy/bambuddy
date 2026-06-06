"""Unit tests for the spawn_background_task helper (#1648 follow-up).

asyncio holds only weak references to tasks, so a fire-and-forget
create_task whose return value is discarded can be GC'd mid-flight and
log ``Task was destroyed but it is pending!`` with no traceback.
``spawn_background_task`` is the central helper that fixes this: it
stores a strong reference until completion, surfaces uncaught exceptions
through the logger, and auto-removes finished tasks.
"""

import asyncio
import logging

import pytest

from backend.app.core.tasks import active_task_count, spawn_background_task


@pytest.mark.asyncio
async def test_holds_strong_ref_until_completion():
    """Discarding the returned task must not let asyncio reap it mid-flight.
    Pre-fix, ``asyncio.create_task(coro)`` with no caller-side reference
    would let GC swallow short tasks before they finished."""
    finished = asyncio.Event()

    async def work() -> None:
        await asyncio.sleep(0)
        finished.set()

    # Note: NOT storing the returned task -- this is exactly the
    # pattern the helper exists to support.
    spawn_background_task(work())
    await asyncio.wait_for(finished.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_removes_from_strong_ref_set_after_completion():
    """The strong-ref set must shrink as tasks complete; otherwise a
    long-running process accumulates one entry per spawned task and the
    helper itself becomes a leak."""
    before = active_task_count()

    async def work() -> None:
        await asyncio.sleep(0)

    spawn_background_task(work())
    spawn_background_task(work())
    # Yield enough times for both tasks + their done-callbacks to run.
    for _ in range(5):
        await asyncio.sleep(0)
    assert active_task_count() == before


@pytest.mark.asyncio
async def test_uncaught_exception_logged_as_warning(caplog):
    """A fire-and-forget task that raises must surface the exception via
    the logger with the traceback attached -- otherwise the error vanishes
    silently and only an opaque ``Task was destroyed`` notice reaches the
    support bundle."""

    async def boom() -> None:
        raise RuntimeError("synthetic failure for test")

    with caplog.at_level(logging.WARNING, logger="backend.app.core.tasks"):
        spawn_background_task(boom(), name="boom-task")
        for _ in range(5):
            await asyncio.sleep(0)

    # One WARNING with the task name and the exception info.
    boom_records = [r for r in caplog.records if "boom-task" in r.message]
    assert len(boom_records) == 1
    assert boom_records[0].levelno == logging.WARNING
    assert boom_records[0].exc_info is not None
    assert isinstance(boom_records[0].exc_info[1], RuntimeError)


@pytest.mark.asyncio
async def test_cancelled_task_does_not_log_exception(caplog):
    """Explicit cancellation isn't an error -- a service shutting down
    its background loops should not be reported as 'uncaught exception'."""

    async def long_running() -> None:
        await asyncio.sleep(10.0)

    with caplog.at_level(logging.WARNING, logger="backend.app.core.tasks"):
        task = spawn_background_task(long_running(), name="cancel-me")
        await asyncio.sleep(0)  # Let it start.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert not any("cancel-me" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_task_name_propagates():
    """Named tasks make the leak source visible in tracebacks and the
    done-callback log line. Pin that ``name`` reaches the underlying
    Task so support bundles surface the spawn site."""
    task = spawn_background_task(asyncio.sleep(0), name="named-spawn-test")
    assert task.get_name() == "named-spawn-test"
    await task
