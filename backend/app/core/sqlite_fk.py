"""Selected ON DELETE rules of the foreign keys, applied by hand on SQLite.

The SQLite database runs with ``PRAGMA foreign_keys`` off: turning it on breaks
the migrations' table rebuilds (measured: every test errors with "no such table:
main.print_archives"). So a delete there applies none of the models' ON DELETE
CASCADE / SET NULL rules. Rows pointing at the deleted row keep its id, and
SQLite hands the highest freed id to the next row created, which then inherits
them: a queue item prints another archive, a project shows another project's
files.

Only the references in APPLIED are handled: those where a dangling id does harm
and the model's rule is what the app wants. Not every model rule is (deleting a
printer would make a printer-scoped notification provider global, and delete
finished queue history), so a reference is added here deliberately, never by
default.

Hooks apply them at every ORM delete, so no delete path needs to remember:

- ``after_delete`` on every mapped class collects the ids of ``db.delete(obj)``,
  and ``after_flush`` applies the rules once per flush, one statement per rule;
- ``do_orm_execute`` applies them before ``db.execute(delete(Model)...)``.

Rule statements go straight to the connection and don't trigger the hooks again.
``sweep_orphans`` repairs what earlier deletes left behind; init_db runs it at
every start. Nothing here does anything on PostgreSQL, which enforces the rules
itself.
"""

import logging
from functools import cache
from typing import Any

from sqlalchemy import Column, Connection, Delete, ForeignKey, Table, delete, event, exists, select, text, update
from sqlalchemy.orm import Mapper, ORMExecuteState, Session

from backend.app.core.database import Base

logger = logging.getLogger(__name__)

# (child table, child column) whose ON DELETE rule is applied on SQLite.
APPLIED: frozenset[tuple[str, str]] = frozenset(
    {
        # print_archives
        ("active_print_spoolman", "archive_id"),
        ("budget_reservations", "print_archive_id"),
        ("library_folders", "archive_id"),
        ("pending_uploads", "archived_id"),
        ("pipeline_runs", "source_archive_id"),
        ("print_batches", "archive_id"),
        ("print_log_entries", "archive_id"),
        ("print_queue", "archive_id"),
        ("project_bom_items", "archive_id"),
        ("spool_usage_history", "archive_id"),
        # spool
        ("spool_usage_history", "spool_id"),
        ("telegram_pending_verdicts", "archive_id"),
        ("wallet_transactions", "print_archive_id"),
        # library_files. print_queue.library_file_id is CASCADE in the model, but
        # library deletes run release_queue_references first, which keeps the rows
        # (finished plates count towards a batch); the rule then finds nothing.
        ("library_file_tags", "file_id"),
        ("pipeline_runs", "sliced_library_file_id"),
        ("pipeline_runs", "source_library_file_id"),
        ("print_archives", "library_file_id"),
        ("print_batches", "library_file_id"),
        ("print_queue", "library_file_id"),
        ("print_queue_variants", "library_file_id"),
        # print_queue
        ("pipeline_jobs", "queue_entry_id"),
        ("print_log_entries", "queue_item_id"),
        ("print_queue_variants", "queue_item_id"),
        ("wallet_transactions", "print_queue_id"),
        # print_batches
        ("print_batch_plates", "batch_id"),
        ("print_queue", "batch_id"),
        # projects
        ("library_files", "project_id"),
        ("library_folders", "project_id"),
        ("pending_uploads", "project_id"),
        ("print_archives", "project_id"),
        ("print_batches", "project_id"),
        ("print_queue", "project_id"),
        ("project_bom_items", "project_id"),
        # printers: what the next printer given the id would otherwise take
        # over. Only these: providers, plugs and virtual printers go with the
        # printer in its delete route, and the rest is decided separately. The
        # print being tracked would be restored for a new printer that is already
        # printing, charging its filament to the deleted printer's spools.
        ("active_print_sessions", "printer_id"),
        ("active_print_spoolman", "printer_id"),
        ("pipeline_jobs", "assigned_printer_id"),
        ("slicer_pipelines", "target_printer_id"),
        # A deleted printer's spools would stay "assigned" to it (SA-1).
        ("spool_assignment", "printer_id"),
        # A profile default's K profile would select a calibration slot on
        # the next printer given the id (#1326).
        ("filament_profile_default_k_profile", "printer_id"),
        # filament_profile_default: the next default given the id would take
        # over its presets and K profiles (#1326).
        ("filament_profile_default_k_profile", "profile_default_id"),
        ("filament_profile_default_preset", "profile_default_id"),
        # external_links: the next link given the id would show the count an
        # app reported for the deleted one.
        ("external_link_badges", "link_id"),
        # smart_plugs: a printer delete now deletes its plugs; their energy
        # history would pass to the next plug given the id.
        ("smart_plug_energy_snapshots", "plug_id"),
        # users: everything a user owns goes with them or loses its owner, or a
        # new user given the freed id inherits it (wallet, cost centres, private
        # folders). Without an owner, folders and private cost centres are left
        # to admins, not opened to everyone.
        ("announcement_reads", "user_id"),
        ("api_keys", "user_id"),
        ("connected_app_grants", "user_id"),
        ("connected_apps", "created_by_id"),
        ("cost_center_members", "user_id"),
        ("cost_centers", "owner_user_id"),
        ("file_variant_groups", "created_by_id"),
        ("library_files", "created_by_id"),
        ("library_folders", "created_by_id"),
        ("long_lived_tokens", "user_id"),
        ("pipeline_runs", "created_by"),
        ("print_archives", "created_by_id"),
        ("print_batches", "created_by_id"),
        ("print_log_entries", "created_by_id"),
        ("print_queue", "created_by_id"),
        ("scheduled_dryings", "created_by_id"),
        ("slicer_pipelines", "created_by"),
        ("sponsor_toast_state", "user_id"),
        ("user_email_preferences", "user_id"),
        ("user_groups", "user_id"),
        ("user_oidc_links", "user_id"),
        ("user_otp_codes", "user_id"),
        ("user_totp", "user_id"),
        ("user_wallets", "user_id"),
        ("wallet_transactions", "created_by_user_id"),
        ("wallet_transactions", "user_id"),
    }
)

_PENDING = "bambuddy_sqlite_fk_pending"
_TABLES = "bambuddy_sqlite_fk_tables"
# Keeps any one statement's IN list small.
_CHUNK = 500
_MAX_DEPTH = 32


def _applies(connection: Connection) -> bool:
    """Only SQLite: PostgreSQL enforces the rules itself."""
    return connection.dialect.name == "sqlite"


def _existing_tables(connection: Connection) -> set[str]:
    """Tables the database has (cached on the connection); a model without one has no rows to fix."""
    tables = connection.info.get(_TABLES)
    if tables is None:
        result = connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))
        tables = connection.info[_TABLES] = {row[0] for row in result}
    return tables


def _single_primary_key(table: Table) -> Column | None:
    columns = list(table.primary_key.columns)
    return columns[0] if len(columns) == 1 else None


@cache
def _applied_foreign_keys() -> tuple[ForeignKey, ...]:
    """The model foreign keys of APPLIED references to a single-column primary key."""
    from backend.app.core.database import load_all_models

    found = []
    for table in load_all_models().sorted_tables:
        for fk in table.foreign_keys:
            if (
                (table.name, fk.parent.name) in APPLIED
                and fk.ondelete in ("CASCADE", "SET NULL")
                and isinstance(fk.column.table, Table)
                and fk.column is _single_primary_key(fk.column.table)
            ):
                found.append(fk)
    return tuple(found)


@cache
def _rules(parent_table: str) -> tuple[tuple[Table, Column, str], ...]:
    """(child table, child column, ON DELETE action) of the APPLIED references to *parent_table*."""
    return tuple(
        (fk.parent.table, fk.parent, str(fk.ondelete))
        for fk in _applied_foreign_keys()
        if fk.column.table.name == parent_table and isinstance(fk.parent.table, Table)
    )


def _apply(connection: Connection, parent_table: str, ids: Any, depth: int = 0) -> None:
    """Apply the rules for rows of *parent_table* whose primary key is in *ids* (a list or a select)."""
    rules = _rules(parent_table)
    if not rules or depth > _MAX_DEPTH:
        return
    if isinstance(ids, list):
        for start in range(0, len(ids), _CHUNK):
            _apply_chunk(connection, rules, ids[start : start + _CHUNK], depth)
    else:
        _apply_chunk(connection, rules, ids, depth)


def _apply_chunk(connection: Connection, rules: tuple, ids: Any, depth: int) -> None:
    tables = _existing_tables(connection)
    for child, column, action in rules:
        if child.name not in tables:
            continue
        if action == "SET NULL":
            connection.execute(update(child).where(column.in_(ids)).values({column.name: None}))
            continue
        child_key = _single_primary_key(child)
        if child_key is not None:
            # By depth, not by table: a table that refers to itself recurses too.
            _apply(connection, child.name, select(child_key).where(column.in_(ids)), depth + 1)
        connection.execute(delete(child).where(column.in_(ids)))


@event.listens_for(Base, "after_delete", propagate=True)
def _collect_orm_delete(mapper: Mapper, connection: Connection, target: Any) -> None:
    table = mapper.local_table
    session = Session.object_session(target)
    if session is None or not isinstance(table, Table) or not _rules(table.name) or not _applies(connection):
        return
    pending = session.info.setdefault(_PENDING, {})
    pending.setdefault(table.name, []).append(mapper.primary_key_from_instance(target)[0])


@event.listens_for(Session, "after_flush")
def _apply_orm_deletes(session: Session, flush_context: Any) -> None:
    pending = session.info.pop(_PENDING, None)
    if not pending:
        return
    connection = session.connection()
    # The parent rows are already gone; with foreign keys off that doesn't stop
    # the rules from finding their dependents.
    for table, ids in pending.items():
        _apply(connection, table, ids)


@event.listens_for(Session, "after_soft_rollback")
def _forget_pending(session: Session, previous_transaction: Any) -> None:
    # A flush that failed after collecting must not leave ids for the next one.
    session.info.pop(_PENDING, None)


@event.listens_for(Session, "do_orm_execute")
def _before_bulk_delete(state: ORMExecuteState) -> None:
    statement = state.statement
    if not state.is_delete or not isinstance(statement, Delete):
        return
    table = statement.table
    if not isinstance(table, Table) or not _rules(table.name):
        return
    key = _single_primary_key(table)
    if key is None:
        return
    connection = state.session.connection()
    if not _applies(connection):
        return
    # The session would flush pending changes only after this hook; flush now so
    # the rules see them (a queue item just moved off the archive must not go).
    if state.session.autoflush:
        state.session.flush()
    where = statement.whereclause
    _apply(connection, table.name, select(key).where(where) if where is not None else select(key))


def _still_not_null(connection: Connection, column: Column) -> bool:
    """Whether the column is NOT NULL in the database, whatever the model says."""
    rows = connection.execute(text(f'PRAGMA table_info("{column.table.name}")'))
    return any(row[1] == column.name and row[3] for row in rows)


async def sweep_orphans(conn: Any) -> None:
    """Repair APPLIED references to rows that no longer exist (SQLite only).

    A SET NULL reference to a missing row is cleared and a CASCADE row is
    deleted, which is what the rule would have done at the delete. Repeated
    until a pass changes nothing, since a deleted row can leave its own
    dependents behind.
    """

    def run(sync: Any) -> None:
        connection = sync.connection() if isinstance(sync, Session) else sync
        if not _applies(connection):
            return
        tables = _existing_tables(connection)
        rules = [
            fk for fk in _applied_foreign_keys() if fk.parent.table.name in tables and fk.column.table.name in tables
        ]
        refused = {fk for fk in rules if fk.ondelete == "SET NULL" and _still_not_null(connection, fk.parent)}
        for fk in refused:
            # A migration that would have dropped it failed (and said so); clearing
            # the column would fail too, and take the start of the app with it.
            logger.warning("Not clearing %s.%s: the column is still NOT NULL", fk.parent.table.name, fk.parent.name)
        rules = [fk for fk in rules if fk not in refused]
        for _ in range(10):
            changed = 0
            for fk in rules:
                child, column, referenced = fk.parent.table, fk.parent, fk.column
                missing = column.is_not(None) & ~exists(
                    select(1).select_from(referenced.table).where(referenced == column)
                )
                if fk.ondelete == "SET NULL":
                    result = connection.execute(update(child).where(missing).values({column.name: None}))
                else:
                    result = connection.execute(delete(child).where(missing))
                if result.rowcount:
                    changed += result.rowcount
                    logger.info(
                        "%s %d %s row(s) whose %s no longer exists",
                        "Cleared" if fk.ondelete == "SET NULL" else "Deleted",
                        result.rowcount,
                        child.name,
                        referenced.table.name,
                    )
            if not changed:
                return

    await conn.run_sync(run)
