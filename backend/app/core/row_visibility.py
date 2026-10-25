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
) -> list | None:
    """WHERE clauses for the archive or queue rows the caller may reach, or None for none.

    The rows the caller reaches through the archives or the queue themselves:
    on their printers, and by ``owned_row_clauses``.
    """
    owned = owned_row_clauses(model, actor, all_permission, own_permission)
    if owned is None:
        return None
    if (clause := printer_scope.where(model.printer_id)) is not None:
        return [clause, *owned]
    return owned


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
