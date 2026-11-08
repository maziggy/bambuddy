"""Which owned rows (archives, queue jobs, library files) a caller reaches through something that holds them."""

from backend.app.core.auth import ApiKeyActor
from backend.app.core.permissions import Permission
from backend.app.core.printer_scope import PrinterScope
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.user import User


def owned_row_clauses(
    model, actor: User | ApiKeyActor | None, all_permission: Permission, own_permission: Permission
) -> list | None:
    """WHERE clauses for the rows of ``model`` the caller may reach by ownership, or None for none.

    Their own unless they may reach everyone's (a row without an owner then
    stays out). Auth off (no actor) reaches all.
    """
    if actor is None:
        return []
    # A key acts with its owner's rights to these rows, not with its own scope
    # flags (a project key needs no archive or queue flag, #1893), so it never
    # reaches a row its owner could not. A legacy key without an owner keeps
    # every row.
    user = actor.owner if isinstance(actor, ApiKeyActor) else actor
    if user is None or user.has_permission(all_permission.value):
        return []
    if user.has_permission(own_permission.value):
        return [model.created_by_id == user.id]
    return None


def visible_row_clauses(
    model: type[PrintArchive] | type[PrintQueueItem],
    printer_scope: PrinterScope,
    actor: User | ApiKeyActor | None,
    all_permission: Permission,
    own_permission: Permission,
    *,
    by_runs: bool = False,
) -> list | None:
    """WHERE clauses for the archive or queue rows the caller may reach, or None for none.

    The rows the caller reaches through the archives or the queue themselves:
    on their printers, and by ``owned_row_clauses``. ``by_runs``: archives
    printed on one of their printers too (``archive_scope_clause``), for a
    listing that shows them as the caller sees them (the archives routes'
    ``_scoped``); others show an archive's latest print as it is.
    """
    owned = owned_row_clauses(model, actor, all_permission, own_permission)
    if owned is None:
        return None
    clause = (
        archive_scope_clause(printer_scope)
        if model is PrintArchive and by_runs
        else printer_scope.where(model.printer_id)
    )
    if clause is not None:
        return [clause, *owned]
    return owned


def archive_scope_clause(printer_scope: PrinterScope):
    """The archives a printer scope sees (#1727), as a WHERE clause; None
    when it sees every printer. An archive is a file printed as runs, maybe
    on several printers (archive redesign): it is seen on its own printer
    (the latest run's, or none) and on every printer one of its runs printed
    on, a print running now included.
    """
    from sqlalchemy import or_, select

    from backend.app.models.print_log import PrintLogEntry

    own = printer_scope.where(PrintArchive.printer_id)
    if own is None:
        return None
    # The table, not the entity: the ORM hides open runs from every read of
    # PrintLogEntry, and a print running on the caller's printer counts.
    runs = PrintLogEntry.__table__
    printed_here = select(runs.c.archive_id).where(
        runs.c.archive_id.is_not(None), runs.c.printer_id.in_(printer_scope.printer_ids or ())
    )
    return or_(own, PrintArchive.id.in_(printed_here))


def whole_archive_scope_clause(printer_scope: PrinterScope):
    """The archives every print of which the scope sees, as a WHERE clause;
    None when it sees every printer. What changes or removes the whole
    archive (its file, its video, the archive itself) needs this: it would
    change other printers' prints too (archive redesign)."""
    from sqlalchemy import and_, not_, select

    from backend.app.models.print_log import PrintLogEntry

    own = printer_scope.where(PrintArchive.printer_id)
    if own is None:
        return None
    runs = PrintLogEntry.__table__
    printed_elsewhere = select(runs.c.archive_id).where(
        runs.c.archive_id.is_not(None),
        runs.c.printer_id.is_not(None),
        runs.c.printer_id.not_in(printer_scope.printer_ids or ()),
    )
    return and_(own, not_(PrintArchive.id.in_(printed_elsewhere)))


def may_reach_row(
    created_by_id: int | None,
    actor: User | ApiKeyActor | None,
    all_permission: Permission,
    own_permission: Permission,
) -> bool:
    """Whether the caller may reach one row owned by ``created_by_id``, printer scope aside.

    The rule of ``visible_row_clauses`` for a row already loaded.
    """
    if actor is None:
        return True
    user = actor.owner if isinstance(actor, ApiKeyActor) else actor
    if user is None or user.has_permission(all_permission.value):
        return True
    return user.has_permission(own_permission.value) and created_by_id is not None and created_by_id == user.id
