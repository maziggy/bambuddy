"""Patching ``spawn_background_task`` must not leave a coroutine un-awaited.

The scheduler builds its coroutine as a call argument::

    spawn_background_task(self._watchdog_print_start(...), name=...)

so the object exists whether or not the replacement schedules it. A bare
``MagicMock`` parks it in ``call_args``; when that reference finally drops, the
coroutine is finalised never having run and CPython emits
``RuntimeWarning: coroutine ... was never awaited`` from ``__del__``. Because
that happens during whatever test the garbage collector is running under, it is
reported as a ``PytestUnraisableExceptionWarning`` against an unrelated file,
and it comes and goes with test ordering.

The first test below reproduces exactly that, so the second is not merely
asserting that a fix does nothing.
"""

import gc
import warnings
from unittest.mock import DEFAULT, MagicMock

import pytest

from backend.tests._fixtures.background_tasks import close_and_default, discarding_spawn_patch

pytestmark = pytest.mark.unit


async def _work():
    """Stands in for ``_watchdog_print_start`` — never scheduled by these tests."""


def _never_awaited_warnings(replacement) -> list[warnings.WarningMessage]:
    """Hand ``replacement`` a coroutine, drop every reference, and collect."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        replacement(_work(), name="watchdog-print-start-1")
        replacement.reset_mock()  # the last reference, held in call_args
        gc.collect()
    return [w for w in caught if "never awaited" in str(w.message)]


def test_a_bare_mock_leaks_the_coroutine():
    """The behaviour being fixed. If this ever stops warning, the second test
    below is no longer evidence of anything."""
    assert _never_awaited_warnings(MagicMock())


def test_the_shared_patch_does_not():
    with discarding_spawn_patch() as spawn:
        assert not _never_awaited_warnings(spawn)


def test_it_is_still_a_mock_that_records_its_calls():
    """It replaces ``patch(target, MagicMock())`` at call sites that may assert
    the spawn happened, so it has to stay inspectable."""
    with discarding_spawn_patch() as spawn:
        from backend.app.services import print_scheduler

        print_scheduler.spawn_background_task(_work(), name="watchdog-print-start-1")

        spawn.assert_called_once()
        assert spawn.call_args.kwargs["name"] == "watchdog-print-start-1"


def test_the_caller_still_gets_a_task_stand_in():
    """``_process_queue`` calls ``task.add_done_callback(...)`` on the result.
    Returning ``close()``'s ``None`` instead of ``DEFAULT`` would break it."""
    with discarding_spawn_patch() as spawn:
        from backend.app.services import print_scheduler

        task = print_scheduler.spawn_background_task(_work(), name="queue-upload-1")

        assert task is spawn.return_value
        task.add_done_callback(lambda _t: None)  # must not raise


def test_close_and_default_defers_the_return_value_to_the_mock():
    coro = _work()

    assert close_and_default(coro) is DEFAULT

    coro.close()  # idempotent — proves the helper left it in a closed state
