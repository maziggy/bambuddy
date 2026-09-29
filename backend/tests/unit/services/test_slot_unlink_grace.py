"""slot_unlink_grace holds an automatic slot unlink until the slot has stayed
empty for the grace period (#3186)."""

import asyncio
from unittest.mock import patch

import pytest

from backend.app.services import slot_unlink_grace as grace

KEY = ("inventory", 1, 2, 33)


@pytest.fixture
def clock():
    now = [1000.0]
    with patch.object(grace, "_now", lambda: now[0]):
        yield now


def test_a_first_sighting_is_held(clock):
    assert grace.removal_confirmed(7, KEY) is False


def test_confirmed_once_the_grace_period_has_passed(clock):
    grace.removal_confirmed(7, KEY)
    clock[0] += grace.GRACE_SECONDS - 1
    assert grace.removal_confirmed(7, KEY) is False
    clock[0] += 1
    assert grace.removal_confirmed(7, KEY) is True


def test_settle_forgets_a_slot_the_pass_did_not_hold_again(clock):
    grace.removal_confirmed(7, KEY)
    grace.settle(7, "inventory", set())
    clock[0] += grace.GRACE_SECONDS
    assert grace.removal_confirmed(7, KEY) is False, "the recovered slot must start a fresh clock"


def test_settle_keeps_other_printers_and_scopes(clock):
    grace.removal_confirmed(7, KEY)
    grace.removal_confirmed(8, KEY)
    grace.removal_confirmed(7, ("spoolman", 1, 2, 41))
    grace.settle(8, "inventory", set())
    grace.settle(7, "spoolman", set())
    clock[0] += grace.GRACE_SECONDS
    assert grace.removal_confirmed(7, KEY) is True


def test_a_hold_nobody_renewed_starts_over(clock):
    """A pass that did not run (Spoolman unreachable, printer offline) cannot
    have watched the slot stay empty, so its old first sighting does not count."""
    grace.removal_confirmed(7, KEY)
    clock[0] += 3 * grace.GRACE_SECONDS
    assert grace.removal_confirmed(7, KEY) is False


def test_is_held_follows_the_hold(clock):
    assert grace.is_held(7, KEY) is False
    grace.removal_confirmed(7, KEY)
    assert grace.is_held(7, KEY) is True
    grace.settle(7, "inventory", set())
    assert grace.is_held(7, KEY) is False


def test_is_held_ignores_a_stale_hold(clock):
    grace.removal_confirmed(7, KEY)
    clock[0] += 3 * grace.GRACE_SECONDS
    assert grace.is_held(7, KEY) is False


def test_forget_slot_drops_every_hold_on_that_slot_only(clock):
    grace.removal_confirmed(7, ("spoolman", 1, 2, 41))
    grace.removal_confirmed(7, ("inventory", 1, 2, 99))
    grace.removal_confirmed(7, ("spoolman", 1, 3, 42))
    grace.removal_confirmed(8, ("spoolman", 1, 2, 41))

    grace.forget_slot(7, 1, 2)

    assert grace.is_held(7, ("spoolman", 1, 2, 41)) is False
    assert grace.is_held(7, ("inventory", 1, 2, 99)) is False
    assert grace.is_held(7, ("spoolman", 1, 3, 42)) is True
    assert grace.is_held(8, ("spoolman", 1, 2, 41)) is True


def test_no_recheck_without_a_registered_callback(clock):
    with patch.object(grace, "_recheck", None):
        grace.removal_confirmed(7, KEY)
    assert grace._recheck_tasks == {}


@pytest.mark.asyncio
async def test_holds_on_one_printer_share_a_single_recheck(clock):
    async def recheck(printer_id: int) -> None:
        pass

    with patch.object(grace, "_recheck", recheck):
        grace.removal_confirmed(7, KEY)
        grace.removal_confirmed(7, ("inventory", 1, 3, 34))
        grace.removal_confirmed(8, KEY)

    assert sorted(grace._recheck_tasks) == [7, 8]
    for task in grace._recheck_tasks.values():
        task.cancel()
    await asyncio.gather(*grace._recheck_tasks.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_the_recheck_runs_the_callback_and_frees_its_slot():
    calls: list[int] = []

    async def recheck(printer_id: int) -> None:
        # Freed before the callback, so a hold the re-check renews can
        # schedule the next one.
        assert printer_id not in grace._recheck_tasks
        calls.append(printer_id)

    with patch.object(grace, "_recheck", recheck):
        grace._recheck_tasks[7] = asyncio.current_task()
        await grace._run_recheck(7, -1)

    assert calls == [7]


@pytest.mark.asyncio
async def test_a_failing_recheck_is_logged_not_raised():
    async def recheck(printer_id: int) -> None:
        raise RuntimeError("boom")

    with patch.object(grace, "_recheck", recheck), patch.object(grace.logger, "exception") as log:
        await grace._run_recheck(7, -1)

    log.assert_called_once()
