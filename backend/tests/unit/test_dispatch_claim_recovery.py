"""A dispatch claim must not survive the dispatch that held it (#2615, #2702).

``dispatching_at`` holds a queue row out of the selection query for the duration
of an upload. Clearing it is best-effort, and the observed failure was narrow:
PostgreSQL refused a connection for a second or two at exactly the moment
dispatch ended, the single clear attempt failed, and the row stayed invisible to
the scheduler until the process restarted.

Two independent recoveries, tested here: the clear retries, and a later tick
releases any claim with no dispatch behind it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def scheduler():
    from backend.app.services.print_scheduler import PrintScheduler

    return PrintScheduler()


def _session(fail_times: int) -> MagicMock:
    """A session whose execute() fails `fail_times` times, then succeeds."""
    db = MagicMock()
    calls = {"n": 0}

    async def execute(*_a, **_k):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise RuntimeError("remaining connection slots are reserved for roles with the SUPERUSER attribute")
        return MagicMock(rowcount=1)

    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db._calls = calls
    return db


# ---------------------------------------------------------------------------
# The retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_a_transient_failure_is_retried_and_the_claim_clears(scheduler):
    """The reported case: one failed attempt used to wedge the row."""
    db = _session(fail_times=1)

    with patch("backend.app.services.print_scheduler.asyncio.sleep", new=AsyncMock()):
        await scheduler._clear_dispatch_claim(db, 597)

    assert db._calls["n"] == 2
    assert db.commit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_the_session_is_rolled_back_between_attempts(scheduler):
    """A failed write leaves the session needing a rollback before reuse."""
    db = _session(fail_times=1)

    with patch("backend.app.services.print_scheduler.asyncio.sleep", new=AsyncMock()):
        await scheduler._clear_dispatch_claim(db, 597)

    assert db.rollback.await_count == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_retries_are_bounded_and_never_raise(scheduler):
    """Dispatch's outcome must not be masked by this cleanup failing."""
    db = _session(fail_times=99)

    with patch("backend.app.services.print_scheduler.asyncio.sleep", new=AsyncMock()):
        await scheduler._clear_dispatch_claim(db, 597)  # must not raise

    assert db._calls["n"] == 3


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_retry_when_the_first_attempt_works(scheduler):
    """The happy path must not pay for the retry."""
    db = _session(fail_times=0)

    await scheduler._clear_dispatch_claim(db, 597)

    assert db._calls["n"] == 1


# ---------------------------------------------------------------------------
# The quiet-tick sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_the_sweep_does_nothing_while_an_upload_is_in_flight(scheduler):
    """An in-flight dispatch owns its claim — clearing it would let a second
    dispatch pick up the same row mid-upload, which is what #2615 prevents."""
    scheduler._inflight[597] = (MagicMock(), 1)

    with patch("backend.app.services.print_scheduler.async_session") as sess:
        await scheduler._clear_stale_dispatch_claims()

    sess.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_the_sweep_releases_a_claim_with_nothing_in_flight(scheduler):
    """`_inflight` is populated before the coroutine claims its row, and pruned
    after its `finally` — so "claim present, nothing in flight" is orphaned."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    db.commit = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.app.services.print_scheduler.async_session", return_value=ctx):
        await scheduler._clear_stale_dispatch_claims()

    assert db.execute.await_count == 1
    assert db.commit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_the_sweep_survives_a_database_that_is_still_down(scheduler):
    """It runs every tick; a failure must not break the scheduler loop."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("still refusing connections"))
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.app.services.print_scheduler.async_session", return_value=ctx):
        await scheduler._clear_stale_dispatch_claims()  # must not raise
