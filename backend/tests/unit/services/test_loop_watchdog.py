"""Tests for the event-loop stall watchdog (#1486)."""

import asyncio
import faulthandler
from unittest.mock import patch

import pytest

from backend.app.services import loop_watchdog


@pytest.fixture(autouse=True)
def _mock_faulthandler():
    """Patch faulthandler so tests never arm a real 30s stall timer that
    could fire mid-suite. Yields (arm_mock, cancel_mock)."""
    with (
        patch.object(faulthandler, "dump_traceback_later") as arm,
        patch.object(faulthandler, "cancel_dump_traceback_later") as cancel,
    ):
        yield arm, cancel
    # Safety net: make sure no test leaves the watchdog task running.
    loop_watchdog.stop_loop_watchdog()


async def test_start_arms_the_stall_timer(_mock_faulthandler):
    arm, cancel = _mock_faulthandler
    loop_watchdog.start_loop_watchdog()
    await asyncio.sleep(0)  # let the heartbeat run its first iteration

    assert cancel.called, "previous timer must be cancelled before re-arming"
    assert arm.called
    # Armed STALL_THRESHOLD seconds ahead, single-shot.
    args, kwargs = arm.call_args
    assert args[0] == loop_watchdog.STALL_THRESHOLD
    assert kwargs.get("repeat") is False


async def test_start_is_idempotent(_mock_faulthandler):
    loop_watchdog.start_loop_watchdog()
    first = loop_watchdog._watchdog_task
    loop_watchdog.start_loop_watchdog()
    assert loop_watchdog._watchdog_task is first, "second start must not spawn a task"


async def test_stop_cancels_the_task_and_disarms(_mock_faulthandler):
    _arm, cancel = _mock_faulthandler
    loop_watchdog.start_loop_watchdog()
    task = loop_watchdog._watchdog_task
    assert task is not None

    cancel.reset_mock()
    loop_watchdog.stop_loop_watchdog()

    assert loop_watchdog._watchdog_task is None
    assert cancel.called, "stop must disarm the pending faulthandler timer"
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()


async def test_heartbeat_interval_is_below_stall_threshold():
    """A healthy loop must always re-arm before the timer can fire."""
    assert loop_watchdog.HEARTBEAT_INTERVAL < loop_watchdog.STALL_THRESHOLD


async def test_rearm_failure_does_not_crash_the_watchdog(_mock_faulthandler):
    """A faulthandler hiccup must not take down the heartbeat task."""
    arm, _cancel = _mock_faulthandler
    arm.side_effect = RuntimeError("boom")
    loop_watchdog.start_loop_watchdog()
    await asyncio.sleep(0)
    task = loop_watchdog._watchdog_task
    assert task is not None and not task.done(), "watchdog must survive a re-arm error"
