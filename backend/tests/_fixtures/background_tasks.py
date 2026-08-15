"""Patching ``spawn_background_task`` without leaking the coroutine.

Every caller builds its coroutine as a *call argument*::

    spawn_background_task(self._watchdog_print_start(...), name=...)

so the coroutine object is constructed whether or not the replacement ever
schedules it. A bare ``MagicMock`` then parks it in ``call_args`` and it is
finalised, never awaited, during some *later* test's garbage collection —
surfacing as a ``PytestUnraisableExceptionWarning`` attributed to whichever
unrelated test happened to be running at the time. That makes the report
useless for finding the leak and, because it depends on GC timing and test
order, it appears and disappears between runs of the same suite.

Closing the coroutine mirrors what the real helper does — take ownership of it
— while still keeping the work from running.

``DEFAULT`` rather than the ``close()`` return: the mock's return value stands
in for the ``asyncio.Task``, and the queue-pool path in ``_process_queue``
calls ``task.add_done_callback(...)`` on it. Returning ``None`` from the
side-effect would replace the usual ``MagicMock`` return with ``None`` and
break that caller.
"""

from unittest.mock import DEFAULT, patch

SCHEDULER_TARGET = "backend.app.services.print_scheduler.spawn_background_task"
MAIN_TARGET = "backend.app.main.spawn_background_task"


def close_and_default(coro, **kwargs):
    """Take ownership of ``coro`` the way the real helper would, then stand down."""
    coro.close()
    return DEFAULT


def discarding_spawn_patch(target: str = SCHEDULER_TARGET):
    """``patch`` for ``spawn_background_task`` that closes what it is handed.

    A drop-in for ``patch(target, MagicMock())`` — still a mock, so call
    assertions work — that does not leave an un-awaited coroutine behind.
    """
    return patch(target, side_effect=close_and_default)
