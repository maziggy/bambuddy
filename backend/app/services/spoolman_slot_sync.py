"""Writing a Spoolman AMS sync's results to the local slot table.

Shared by the automatic pass in ``on_ams_change`` and the Sync buttons in
Settings. A sync reads the slot table, then awaits Spoolman for every tray, so
by the time it writes, a slot may hold a spool the user assigned meanwhile.
Writing every synced slot back, the unchanged ones included, overwrote that
assignment with what the sync had read (SSA-3). Only a slot whose spool the
sync changed is written, and only while it still holds what the sync read.

What it did change is announced, its grace hold dropped, as a hand assignment
does; the Sync buttons did neither (SSA-2).
"""

import logging
from collections.abc import Iterable, Mapping
from typing import cast

from sqlalchemy import delete, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.websocket import ws_manager
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services import slot_unlink_grace, spool_swaps

logger = logging.getLogger(__name__)

Slot = tuple[int, int, int]  # (printer_id, ams_id, tray_id)


async def write_slot_results(
    db: AsyncSession,
    read: Mapping[Slot, int] | None,
    synced: Iterable[tuple[int, int, int, int]],
    emptied: Iterable[Slot],
) -> list[Slot]:
    """Write the slots the sync changed and commit; returns them.

    ``read`` is the slot table as the sync read it, ``synced`` the
    (printer_id, ams_id, tray_id, spoolman_spool_id) it found in the slots, and
    ``emptied`` the slots it found empty. Raises on a database error, as the
    callers report it.

    ``read`` None: the table could not be read, so there is nothing to compare
    against, and every slot is written as it was before the compare existed. A
    swap and a removal must not be dropped for want of a snapshot.
    """
    if read is None:
        return await _write_unconditionally(db, synced, emptied)
    changed: list[Slot] = []
    assigned: list[Slot] = []
    for printer_id, ams_id, tray_id, spool_id in synced:
        slot = (printer_id, ams_id, tray_id)
        before = read.get(slot)
        if before == spool_id:
            continue
        where = (
            SpoolmanSlotAssignment.printer_id == printer_id,
            SpoolmanSlotAssignment.ams_id == ams_id,
            SpoolmanSlotAssignment.tray_id == tray_id,
        )
        if before is None:
            statement = text(
                "INSERT INTO spoolman_slot_assignments (printer_id, ams_id, tray_id, spoolman_spool_id)"
                " VALUES (:printer_id, :ams_id, :tray_id, :spool_id)"
                " ON CONFLICT(printer_id, ams_id, tray_id) DO NOTHING"
            ).bindparams(printer_id=printer_id, ams_id=ams_id, tray_id=tray_id, spool_id=spool_id)
        else:
            statement = (
                update(SpoolmanSlotAssignment)
                .where(*where, SpoolmanSlotAssignment.spoolman_spool_id == before)
                .values(spoolman_spool_id=spool_id)
                .execution_options(synchronize_session=False)
            )
        if cast(CursorResult, await db.execute(statement)).rowcount:
            changed.append(slot)
            assigned.append(slot)
            await spool_swaps.record_assignment(db, printer_id, ams_id, tray_id, spool_id, spool_swaps.SPOOLMAN)
    for slot in emptied:
        # A slot the sync read without a spool matches no row below: there is
        # nothing of the sync's own to remove.
        before = read.get(slot)
        printer_id, ams_id, tray_id = slot
        result = cast(
            CursorResult,
            await db.execute(
                delete(SpoolmanSlotAssignment)
                .where(
                    SpoolmanSlotAssignment.printer_id == printer_id,
                    SpoolmanSlotAssignment.ams_id == ams_id,
                    SpoolmanSlotAssignment.tray_id == tray_id,
                    SpoolmanSlotAssignment.spoolman_spool_id == before,
                )
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount:
            changed.append(slot)
    await db.commit()
    await _announce(assigned, changed)
    return changed


async def _write_unconditionally(
    db: AsyncSession, synced: Iterable[tuple[int, int, int, int]], emptied: Iterable[Slot]
) -> list[Slot]:
    assigned: list[Slot] = []
    for printer_id, ams_id, tray_id, spool_id in synced:
        await db.execute(
            text(
                "INSERT INTO spoolman_slot_assignments (printer_id, ams_id, tray_id, spoolman_spool_id)"
                " VALUES (:printer_id, :ams_id, :tray_id, :spool_id)"
                " ON CONFLICT(printer_id, ams_id, tray_id)"
                " DO UPDATE SET spoolman_spool_id = excluded.spoolman_spool_id"
            ).bindparams(printer_id=printer_id, ams_id=ams_id, tray_id=tray_id, spool_id=spool_id)
        )
        assigned.append((printer_id, ams_id, tray_id))
        await spool_swaps.record_assignment(db, printer_id, ams_id, tray_id, spool_id, spool_swaps.SPOOLMAN)
    removed: list[Slot] = []
    for printer_id, ams_id, tray_id in emptied:
        await db.execute(
            delete(SpoolmanSlotAssignment)
            .where(
                SpoolmanSlotAssignment.printer_id == printer_id,
                SpoolmanSlotAssignment.ams_id == ams_id,
                SpoolmanSlotAssignment.tray_id == tray_id,
            )
            .execution_options(synchronize_session=False)
        )
        removed.append((printer_id, ams_id, tray_id))
    await db.commit()
    changed = assigned + removed
    await _announce(assigned, changed)
    return changed


async def _announce(assigned: list[Slot], changed: list[Slot]) -> None:
    for printer_id, ams_id, tray_id in assigned:
        slot_unlink_grace.forget_slot(printer_id, ams_id, tray_id)
    for printer_id, ams_id, tray_id in changed:
        await ws_manager.broadcast(
            {"type": "spool_assignment_changed", "printer_id": printer_id, "ams_id": ams_id, "tray_id": tray_id}
        )
