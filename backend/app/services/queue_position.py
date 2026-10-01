"""The queue's position sequence.

Positions are one sequence across every pending item, not one per printer
(#3200). The queue page lists and reorders pending items as a single list, and
the scheduler dispatches in that order, so a pinned job and an "Any <model>" job
compete for a printer by position. A per-printer sequence gave each lane its
own 1, 2, 3 and made a job added last land in the middle of the list.
"""

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.print_queue import PrintQueueItem

# Advisory lock key for the shared sequence, in the 1625 namespace. The key
# used to be the printer id, back when every printer had a sequence of its own.
QUEUE_POSITION_LOCK_KEY = 0


async def lock_queue_positions(db: AsyncSession) -> None:
    """Serialize inserts that read MAX(position) (#1625-followup).

    Two concurrent inserts into an empty queue would otherwise both read
    MAX(position) as 0 and land on position 1. The lock is transaction-scoped
    and released at commit/rollback. SQLite serializes writes implicitly and
    needs no equivalent.

    The dialect is read from the session binding, not the ``is_sqlite()``
    helper: the test fixture binds a SQLite engine while
    ``settings.database_url`` may still point at Postgres.
    """
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(1625, :k)"), {"k": QUEUE_POSITION_LOCK_KEY})


async def max_queue_position(db: AsyncSession) -> int:
    """Highest position among pending items, or 0 for an empty queue."""
    result = await db.execute(select(func.max(PrintQueueItem.position)).where(PrintQueueItem.status == "pending"))
    return result.scalar() or 0


async def next_queue_position(db: AsyncSession) -> int:
    """Position for an item appended to the end of the queue, under the lock."""
    await lock_queue_positions(db)
    return await max_queue_position(db) + 1
