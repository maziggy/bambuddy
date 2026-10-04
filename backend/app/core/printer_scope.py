"""Which printers a caller may see and control (#1727).

Permissions answer *what* a caller may do; a printer scope answers *on which
printers*. The two are checked separately: a route first passes its
permission gate, then refuses any printer outside the caller's scope.

How a scope is derived:

* Auth disabled, or an admin user: every printer.
* A user: the union of the printers of each of their groups that has
  ``restrict_printers`` set, a group's printers being the ones picked for it
  plus every printer whose location it was given. A user in no such group sees every printer, so
  installs that never configure this behave exactly as before. A group
  without the flag doesn't contribute, so permission groups (Operators,
  Viewers) combine with team groups without widening them.
* An API key: its own ``printer_ids`` (None = all), narrowed to its owner's
  scope, so a key can never reach a printer its owner can't.

A printer outside the scope is reported as missing (404), never as forbidden,
so its id isn't confirmed to a caller who can't see it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.group import Group, group_locations, group_printers


@dataclass(frozen=True)
class PrinterScope:
    """The printers a caller may use. ``printer_ids=None`` means all of them."""

    printer_ids: frozenset[int] | None = None

    @property
    def is_unrestricted(self) -> bool:
        return self.printer_ids is None

    def allows(self, printer_id: int | None) -> bool:
        """Whether ``printer_id`` is in scope. ``None`` (no printer) always is."""
        if printer_id is None or self.printer_ids is None:
            return True
        return printer_id in self.printer_ids

    def ensure(self, printer_id: int | None) -> None:
        """Raise the same 404 a missing printer gets when ``printer_id`` is out of scope."""
        if not self.allows(printer_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Printer not found")

    def intersect(self, other: PrinterScope) -> PrinterScope:
        if self.printer_ids is None:
            return other
        if other.printer_ids is None:
            return self
        return PrinterScope(self.printer_ids & other.printer_ids)

    def filter_ids(self, printer_ids: Iterable[int]) -> list[int]:
        """``printer_ids`` minus the ones out of scope, order kept."""
        return [pid for pid in printer_ids if self.allows(pid)]

    def where(self, column):
        """A WHERE clause limiting ``column`` (a printer id column) to the scope.

        Returns None when unrestricted so callers can skip the filter. Rows
        whose printer is NULL stay visible: they aren't bound to any printer
        (orphaned archives, queue items waiting for a model match).
        """
        if self.printer_ids is None:
            return None
        return column.is_(None) | column.in_(self.printer_ids)

    def where_strict(self, column):
        """Like ``where`` but also drops rows with no printer."""
        if self.printer_ids is None:
            return None
        return column.in_(self.printer_ids)


ALL_PRINTERS = PrinterScope()


def ensure_model_target_allowed(user, scope: PrinterScope) -> None:
    """Refuse an "any printer of a model" job from a limited caller with no user.

    The scheduler keeps such a job within its *creator's* scope, but a job
    queued through an API key records no creator, so a key limited to certain
    printers could otherwise reach the rest of the fleet through it. Users are
    unaffected: their jobs carry their id.
    """
    if user is None and not scope.is_unrestricted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A caller limited to certain printers must queue to a specific printer, not to any printer of a model",
        )


async def group_printer_ids(db: AsyncSession, group_id: int) -> list[int]:
    result = await db.execute(
        select(group_printers.c.printer_id)
        .where(group_printers.c.group_id == group_id)
        .order_by(group_printers.c.printer_id)
    )
    return list(result.scalars().all())


async def group_location_names(db: AsyncSession, group_id: int) -> list[str]:
    result = await db.execute(
        select(group_locations.c.location)
        .where(group_locations.c.group_id == group_id)
        .order_by(group_locations.c.location)
    )
    return list(result.scalars().all())


async def groups_printer_ids(db: AsyncSession, group_ids: Iterable[int]) -> frozenset[int]:
    """Every printer the given groups reach, picked or through a location."""
    from backend.app.models.printer import Printer

    ids = list(group_ids)
    if not ids:
        return frozenset()
    picked = select(group_printers.c.printer_id).where(group_printers.c.group_id.in_(ids))
    located = (
        select(Printer.id)
        .join(group_locations, group_locations.c.location == Printer.location)
        .where(group_locations.c.group_id.in_(ids))
    )
    result = await db.execute(picked.union(located))
    return frozenset(result.scalars().all())


async def resolve_user_printer_scope(db: AsyncSession, user) -> PrinterScope:
    """Scope of a loaded ``User`` (``groups`` must already be loaded)."""
    if user.is_admin:
        return ALL_PRINTERS
    restricted = [g.id for g in user.groups if g.restrict_printers]
    if not restricted:
        return ALL_PRINTERS
    return PrinterScope(await groups_printer_ids(db, restricted))


async def location_grantees(db: AsyncSession, locations: Iterable[str | None]) -> list[str]:
    """Names of the restricted groups granted any of ``locations``.

    A printer moving between these locations changes who can reach it.
    """
    names = [loc for loc in locations if loc]
    if not names:
        return []
    result = await db.execute(
        select(Group.name)
        .join(group_locations, group_locations.c.group_id == Group.id)
        .where(group_locations.c.location.in_(names), Group.restrict_printers.is_(True))
        .distinct()
        .order_by(Group.name)
    )
    return list(result.scalars().all())


async def resolve_user_id_printer_scope(db: AsyncSession, user_id: int | None) -> PrinterScope:
    """Scope of the user with ``user_id``; no user (VP, auth off) means every printer.

    For background work acting on a user's behalf, such as the scheduler
    choosing a printer for a queued job. A deleted or deactivated user gets an
    empty scope rather than everything, so their leftover jobs don't spread
    onto printers they were never allowed to use.
    """
    from sqlalchemy.orm import selectinload

    from backend.app.models.user import User

    if user_id is None:
        return ALL_PRINTERS
    result = await db.execute(select(User).where(User.id == user_id).options(selectinload(User.groups)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return PrinterScope(frozenset())
    return await resolve_user_printer_scope(db, user)


def api_key_own_scope(api_key) -> PrinterScope:
    """The key's own ``printer_ids`` allowlist, without its owner's narrowing."""
    if api_key.printer_ids is None:
        return ALL_PRINTERS
    return PrinterScope(frozenset(int(pid) for pid in api_key.printer_ids))


async def group_restricts_printers(db: AsyncSession, group_ids: Iterable[int]) -> bool:
    ids = list(group_ids)
    if not ids:
        return False
    result = await db.execute(select(Group.id).where(Group.id.in_(ids), Group.restrict_printers.is_(True)).limit(1))
    return result.first() is not None
