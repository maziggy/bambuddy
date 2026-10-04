"""Printer locations (groups): list, create, rename, restyle, delete, assign (#2962).

A printer's location is the free-text ``printers.location`` column, which the
scheduler's model-based targeting (``print_queue.target_location``) and the
printers filter match exactly. ``printer_locations`` adds the rest: a location
with no printers yet, and its icon and colour. The list is the union of both,
so a location typed into the printer dialog shows up here without a row.

Every write is one transaction on the server. The page used to send one PATCH
per printer from its cached printer list: a failure halfway left a location
split under two names, and a printer another user had moved in the meantime
was moved back.

Locations are also an access setting (#1727): a restricted group can be given
a location, and then reaches every printer in it. So these routes follow the
same rules as editing a printer: moving printers into or out of a granted
location is for admins only, a rename carries the grants along to the new
name, and a caller limited to some printers sees and changes only locations
made of their own printers.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequestPrinterScope, RequirePermissionIfAuthEnabled, is_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.core.printer_scope import PrinterScope, location_grantees
from backend.app.core.websocket import ws_manager
from backend.app.models.group import group_locations
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.printer_location import PrinterLocation
from backend.app.models.user import User
from backend.app.schemas.printer_location import (
    PrinterLocationAssign,
    PrinterLocationAssignResult,
    PrinterLocationCreate,
    PrinterLocationDelete,
    PrinterLocationDeleteResult,
    PrinterLocationResponse,
    PrinterLocationUpdate,
)
from backend.app.utils.natural_sort import natural_sort_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/printer-locations", tags=["printer-locations"])

DUPLICATE_NAME = "A location with this name already exists"


def _blank_location():
    """Printers with no location: NULL, or "" from before blanks were folded."""
    return (Printer.location.is_(None)) | (func.trim(Printer.location) == "")


async def _printer_counts(db: AsyncSession, scope: PrinterScope | None = None) -> dict[str, int]:
    """Printers per location; with ``scope``, only the printers in it."""
    query = select(Printer.location, func.count(Printer.id)).where(~_blank_location())
    limit = scope.where_strict(Printer.id) if scope is not None else None
    if limit is not None:
        query = query.where(limit)
    result = await db.execute(query.group_by(Printer.location))
    return dict(result.all())


async def _ensure_all_in_scope(db: AsyncSession, scope: PrinterScope, names: set[str]) -> None:
    """Refuse a limited caller a location that also holds printers they can't see.

    Renaming or deleting it would change those printers too.
    """
    if scope.is_unrestricted or not names:
        return
    ids = (await db.execute(select(Printer.id).where(Printer.location.in_(names)))).scalars().all()
    if any(not scope.allows(pid) for pid in ids):
        raise HTTPException(
            status_code=403,
            detail="This location also holds printers you can't access, so only someone who can see them all may change it.",
        )


async def _ensure_admin_for_access_change(db: AsyncSession, user: User | None, grantees: list[str]) -> None:
    """Moving printers into or out of a granted location changes who reaches them (#1727)."""
    if grantees and await is_auth_enabled(db) and not (user is not None and user.is_admin):
        raise HTTPException(
            status_code=403,
            detail=(
                "This changes which printers the groups "
                + ", ".join(grantees)
                + " can access through their locations. Only an admin can do that."
            ),
        )


async def _row(db: AsyncSession, name: str) -> PrinterLocation | None:
    result = await db.execute(select(PrinterLocation).where(PrinterLocation.name == name))
    return result.scalar_one_or_none()


async def _name_taken(db: AsyncSession, name: str, *, ignore: str | None = None) -> bool:
    """Whether ``name`` is a location already, ignoring case.

    Matching elsewhere is exact, so "Workshop" and "workshop" would be two
    locations that look like one. Refusing the second keeps them apart.
    """
    key = name.casefold()
    rows = (await db.execute(select(PrinterLocation.name))).scalars().all()
    used = (await db.execute(select(Printer.location).where(~_blank_location()).distinct())).scalars().all()
    return any(n.casefold() == key and n != ignore for n in (*rows, *used))


async def _conflicts(db: AsyncSession, name: str) -> bool:
    """Whether using ``name`` would add a case variant of a location.

    A name that already exists exactly is never a conflict, even when an older
    install also holds a case variant of it: refusing would leave no way to
    move a printer into, or style, a location that is plainly there.
    """
    if await _row(db, name) is not None:
        return False
    if name in await _printer_counts(db):
        return False
    return await _name_taken(db, name)


async def _move_grants(db: AsyncSession, old: str, new: str) -> None:
    """Re-point the groups' grants of ``old`` to ``new`` (#1727).

    A group already granted ``new`` keeps one grant: the pair is the primary key.
    """
    holders = (
        (await db.execute(select(group_locations.c.group_id).where(group_locations.c.location == old))).scalars().all()
    )
    if not holders:
        return
    already = set(
        (
            await db.execute(
                select(group_locations.c.group_id).where(
                    group_locations.c.location == new, group_locations.c.group_id.in_(holders)
                )
            )
        )
        .scalars()
        .all()
    )
    await db.execute(delete(group_locations).where(group_locations.c.location == old))
    rows = [{"group_id": gid, "location": new} for gid in holders if gid not in already]
    if rows:
        await db.execute(insert(group_locations), rows)


async def _broadcast() -> None:
    await ws_manager.broadcast({"type": "printer_locations_changed"})


@router.get("/", response_model=list[PrinterLocationResponse])
async def list_printer_locations(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """Every location: those with a row, and those only printers carry.

    A caller limited to some printers (#1727) sees only the locations their
    printers are in, counted over those printers.
    """
    counts = await _printer_counts(db, printer_scope)
    rows = (await db.execute(select(PrinterLocation))).scalars().all()
    if not printer_scope.is_unrestricted:
        rows = [row for row in rows if row.name in counts]
    out = {
        row.name: PrinterLocationResponse(
            id=row.id, name=row.name, icon=row.icon, color=row.color, printer_count=counts.get(row.name, 0)
        )
        for row in rows
    }
    for name, count in counts.items():
        if name not in out:
            out[name] = PrinterLocationResponse(name=name, printer_count=count)
    return sorted(out.values(), key=lambda loc: natural_sort_key(loc.name))


@router.post("/", response_model=PrinterLocationResponse, status_code=201)
async def create_printer_location(
    data: PrinterLocationCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
):
    """Create a location, with or without printers in it yet."""
    if await _row(db, data.name) is not None or await _conflicts(db, data.name):
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME)
    # A location printers already use but that has no row yet gets one, which is
    # how it gets an icon and colour.
    row = PrinterLocation(name=data.name, icon=data.icon, color=data.color)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME) from exc
    await db.refresh(row)
    await _broadcast()
    counts = await _printer_counts(db)
    return PrinterLocationResponse(
        id=row.id, name=row.name, icon=row.icon, color=row.color, printer_count=counts.get(row.name, 0)
    )


@router.patch("/", response_model=PrinterLocationResponse)
async def update_printer_location(
    data: PrinterLocationUpdate,
    db: AsyncSession = Depends(get_db),
    user: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """Rename a location and/or change its icon and colour.

    A rename moves its printers and the queue items that target it in the same
    transaction, so an "any printer in <location>" job, and the next run of a
    batch, keep finding their printers.
    """
    row = await _row(db, data.name)
    counts = await _printer_counts(db)
    if row is None and data.name not in counts:
        raise HTTPException(status_code=404, detail="Location not found")
    if not printer_scope.is_unrestricted and data.name not in await _printer_counts(db, printer_scope):
        raise HTTPException(status_code=404, detail="Location not found")
    await _ensure_all_in_scope(db, printer_scope, {data.name})

    name = data.name
    new_name = data.new_name if "new_name" in data.model_fields_set else None
    if new_name is not None and new_name != name:
        if await _name_taken(db, new_name, ignore=name):
            raise HTTPException(status_code=409, detail=DUPLICATE_NAME)
        # A grant left on the new name (#1727) would hand these printers to
        # another group: that is an access change.
        await _ensure_admin_for_access_change(db, user, await location_grantees(db, [new_name]))
        await db.execute(update(Printer).where(Printer.location == name).values(location=new_name))
        # The groups given this location keep it under its new name, so the
        # rename changes no one's access.
        await _move_grants(db, name, new_name)
        # Every row, not only pending ones: a batch clones its next run from
        # its newest row whatever that row's status, so a finished run still
        # pointing at the old name would send future runs nowhere. A rename is
        # the same place under a new name, so history follows it too.
        await db.execute(
            update(PrintQueueItem).where(PrintQueueItem.target_location == name).values(target_location=new_name)
        )
        name = new_name

    if row is None:
        row = PrinterLocation(name=name)
        db.add(row)
    else:
        row.name = name
    if "icon" in data.model_fields_set:
        row.icon = data.icon
    if "color" in data.model_fields_set:
        row.color = data.color

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME) from exc
    await db.refresh(row)
    await _broadcast()
    counts = await _printer_counts(db)
    return PrinterLocationResponse(
        id=row.id, name=row.name, icon=row.icon, color=row.color, printer_count=counts.get(row.name, 0)
    )


@router.post("/delete", response_model=PrinterLocationDeleteResult)
async def delete_printer_locations(
    data: PrinterLocationDelete,
    db: AsyncSession = Depends(get_db),
    user: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """Delete locations; their printers end up with no location.

    Pending queue items that target a deleted location are left alone: changing
    them to "any location" would let them start on printers they were meant to
    stay off.

    A location given to a group (#1727) can only be deleted by an admin, and
    its grants go with it: left behind, they would hand a later location of
    the same name to that group.
    """
    names = set(data.names)
    if not printer_scope.is_unrestricted:
        # A limited caller can only reach locations made of their own printers.
        names &= set(await _printer_counts(db, printer_scope))
    await _ensure_all_in_scope(db, printer_scope, names)
    grantees = await location_grantees(db, names)
    await _ensure_admin_for_access_change(db, user, grantees)
    existing = set((await db.execute(select(PrinterLocation.name).where(PrinterLocation.name.in_(names)))).scalars())
    existing |= set(await _printer_counts(db)) & names
    await db.execute(delete(PrinterLocation).where(PrinterLocation.name.in_(names)))
    moved = await db.execute(update(Printer).where(Printer.location.in_(names)).values(location=None))
    if names:
        await db.execute(delete(group_locations).where(group_locations.c.location.in_(names)))
    await db.commit()
    if grantees:
        await ws_manager.refresh_printer_scopes()
    await _broadcast()
    return PrinterLocationDeleteResult(deleted=len(existing), printers_ungrouped=moved.rowcount or 0)


@router.post("/assign", response_model=PrinterLocationAssignResult)
async def assign_printer_location(
    data: PrinterLocationAssign,
    db: AsyncSession = Depends(get_db),
    user: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
    printer_scope: PrinterScope = RequestPrinterScope,
):
    """Move printers into a location, or out of any with null.

    By printer id, on the server: a printer someone else moved meanwhile is
    moved again only if it is in this request. A printer outside the caller's
    scope (#1727) is "not found", and moving printers into or out of a
    location given to a group is for admins only, as when editing a printer.
    """
    # Moving into a new case variant of an existing location would split it.
    if data.location is not None and await _conflicts(db, data.location):
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME)
    ids = list(dict.fromkeys(data.printer_ids))
    rows = (await db.execute(select(Printer.id, Printer.location).where(Printer.id.in_(ids)))).all()
    found = {pid for pid, _ in rows if printer_scope.allows(pid)}
    missing = sorted(set(ids) - found)
    if missing:
        raise HTTPException(status_code=404, detail=f"Printer not found: {', '.join(map(str, missing))}")
    leaving = {location for _, location in rows if location != data.location}
    grantees = await location_grantees(db, [*leaving, data.location]) if leaving else []
    await _ensure_admin_for_access_change(db, user, grantees)
    result = await db.execute(update(Printer).where(Printer.id.in_(ids)).values(location=data.location))
    await db.commit()
    if grantees:
        await ws_manager.refresh_printer_scopes()
    await _broadcast()
    return PrinterLocationAssignResult(moved=result.rowcount or 0)
