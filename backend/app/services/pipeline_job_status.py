"""A pipeline job keeps the final status of its queue entry.

A dispatched job's own status stays ``queued``; while its queue entry exists
the run view reads the entry's status. The entry can be deleted later (queue
page, archive delete, user delete), which clears the job's link, and the job
could then not tell a printed copy from one that never ran. So when the entry
ends -- reaches a final status, or is deleted before that (a cancellation) --
the job stores that status itself.

Hooks on every session flush (status changes, ORM deletes) and on statements
run through a session (bulk deletes and status updates of print_queue). A
delete the database cascades on its own (a printer or batch removed with its
pending entries) is not seen: such a job reads as unknown, never as failed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Table, event, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import ORMExecuteState, Session, attributes
from sqlalchemy.sql.dml import Delete, Update

from backend.app.models.pipeline_run import PipelineJob
from backend.app.models.print_queue import PrintQueueItem

# Final job statuses, and the queue entry statuses that end a job.
JOB_FINAL_STATUSES = ("completed", "failed", "cancelled")
_FINAL_FROM_QUEUE = {"completed": "completed", "failed": "failed", "aborted": "failed", "cancelled": "cancelled"}

_queue_table: Table = PrintQueueItem.__table__  # type: ignore[assignment]
_job_table: Table = PipelineJob.__table__  # type: ignore[assignment]


def final_job_status(queue_status: str | None, *, entry_deleted: bool) -> str | None:
    """The status a job ends with when its entry has ``queue_status``, or None if it doesn't end."""
    final = _FINAL_FROM_QUEUE.get(queue_status or "")
    if final is not None:
        return final
    # Deleted while still waiting or printing: the copy was cancelled.
    return "cancelled" if entry_deleted else None


def _store(connection: Connection, entries: dict[int, str]) -> None:
    """Give the not yet ended jobs of each queue entry id its final status."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for status in set(entries.values()):
        ids = [entry_id for entry_id, final in entries.items() if final == status]
        connection.execute(
            _job_table.update()
            .where(_job_table.c.queue_entry_id.in_(ids), _job_table.c.status.not_in(JOB_FINAL_STATUSES))
            .values(status=status, completed_at=now)
        )


@event.listens_for(Session, "before_flush")
def _store_on_flush(session: Session, flush_context: Any, instances: Any) -> None:
    entries: dict[int, str] = {}
    for obj in session.dirty:
        if isinstance(obj, PrintQueueItem) and obj.id is not None:
            if attributes.get_history(obj, "status").has_changes():
                final = final_job_status(obj.status, entry_deleted=False)
                if final is not None:
                    entries[obj.id] = final
    for obj in session.deleted:
        if isinstance(obj, PrintQueueItem) and obj.id is not None:
            final = final_job_status(obj.status, entry_deleted=True)
            if final is not None:
                entries[obj.id] = final
    if entries:
        _store(session.connection(), entries)


def _is_queue_table(table: Any) -> bool:
    # ORM statements carry an annotated copy of the table, so compare by name.
    return getattr(table, "name", None) == _queue_table.name


def _set_status(statement: Update) -> str | None:
    """The literal status a bulk update writes into print_queue, if any."""
    for column, value in (statement._values or {}).items():  # noqa: SLF001
        key = column if isinstance(column, str) else getattr(column, "key", None)
        if key == "status":
            literal = getattr(value, "value", value)
            return literal if isinstance(literal, str) else None
    return None


# First among the statement hooks: core/sqlite_fk clears the jobs' link to a
# deleted entry in its own hook, after which they could not be found.
@event.listens_for(Session, "do_orm_execute", insert=True)
def _store_on_bulk(state: ORMExecuteState) -> None:
    statement = state.statement
    if state.is_delete and isinstance(statement, Delete):
        if not _is_queue_table(statement.table):
            return
        new_status = None
        entry_deleted = True
    elif state.is_update and isinstance(statement, Update):
        if not _is_queue_table(statement.table):
            return
        new_status = _set_status(statement)
        if final_job_status(new_status, entry_deleted=False) is None:
            return
        entry_deleted = False
    else:
        return
    connection = state.session.connection()
    query = select(_queue_table.c.id, _queue_table.c.status)
    if statement.whereclause is not None:
        query = query.where(statement.whereclause)
    entries: dict[int, str] = {}
    for entry_id, status in connection.execute(query):
        final = final_job_status(new_status or status, entry_deleted=entry_deleted)
        if final is not None:
            entries[entry_id] = final
    if entries:
        _store(connection, entries)
