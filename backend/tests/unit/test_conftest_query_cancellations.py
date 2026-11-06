"""The test harness waits for asyncpg's query cancellations before it closes
a test's engines.

A test that cancels a task mid-query leaves asyncpg's own cancel task behind;
if the test ends first, it is still pending when the loop closes and shows up
as an unraisable "Connection._cancel" warning in some later test on PostgreSQL.
"""

import asyncio

from backend.tests.conftest import _finish_query_cancellations


class Connection:
    """Stands in for asyncpg's: the helper recognises the task by this name."""

    async def _cancel(self) -> None:
        await asyncio.sleep(0.05)


async def test_a_pending_cancellation_is_finished_first():
    task = asyncio.get_running_loop().create_task(Connection()._cancel())

    await _finish_query_cancellations()

    assert task.done()


async def test_other_tasks_are_left_alone():
    async def unrelated() -> None:
        await asyncio.sleep(10)

    task = asyncio.get_running_loop().create_task(unrelated())
    try:
        await asyncio.wait_for(_finish_query_cancellations(), timeout=1)
        assert not task.done()
    finally:
        task.cancel()
