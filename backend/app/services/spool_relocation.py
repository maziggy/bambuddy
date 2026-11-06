"""Taking a spool out of its other slots once an AMS has read its tag (#3427).

A tag read in a slot says where the physical spool is, so any other slot the
spool is still assigned to is stale. The RFID auto-assign and the Spoolman AMS
sync wrote the slot they read and left those rows alone: a spool assigned by
hand to printer A's empty slot and then loaded into printer B stayed assigned
to both, and A's slot was configured from, and charged to, a spool in B.

This does what a manual move (#1574) does to the other slots, with the same
locks in the same order: the spool's move lock, then, for the internal
inventory, each holding printer's assignment lock by id. It must run after the
caller has let go of its own printer's lock; taking another printer's lock
while holding one is the reverse of a move's order.

The removed slots get no MQTT command and keep what the printer has set, as a
manual unassign does. A Spoolman spool also loses a vacated slot's fallback
tag, as in a Spoolman move. Callers only pass spools already known by the tag
read: a material/colour match proves nothing about where a spool is.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack

from sqlalchemy import select

from backend.app.core.websocket import ws_manager
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.ams_assignment_lock import get_ams_assignment_lock, get_spool_move_lock
from backend.app.utils.tag_normalization import normalize_tag_uid, normalize_tray_uuid

logger = logging.getLogger(__name__)

Slot = tuple[int, int, int]  # (printer_id, ams_id, tray_id)

INTERNAL = "inventory"
SPOOLMAN = "spoolman"


def _live_tray(printer_id: int, ams_id: int, tray_id: int) -> dict | None:
    """The tray the printer reports for a slot, or None."""
    from backend.app.services.printer_manager import printer_manager

    state = printer_manager.get_status(printer_id)
    raw = getattr(state, "raw_data", None) if state is not None else None
    if not isinstance(raw, dict):
        return None
    if ams_id == 255:
        vt_trays = raw.get("vt_tray") or []
        for tray in [vt_trays] if isinstance(vt_trays, dict) else vt_trays:
            if isinstance(tray, dict) and str(tray.get("id", 254)) == str(254 + tray_id):
                return tray
        return None
    units = raw.get("ams") or []
    if isinstance(units, dict):  # also stored as the push's {"ams": [...]}
        units = units.get("ams") or []
    for unit in units:
        if not isinstance(unit, dict) or str(unit.get("id")) != str(ams_id):
            continue
        for tray in unit.get("tray") or []:
            if isinstance(tray, dict) and str(tray.get("id")) == str(tray_id):
                return tray
    return None


def _tag_ids(tray: dict | None) -> set[str]:
    """The tag's usable identifiers (tag_uid, tray_uuid), normalised; empty when untagged."""
    if not tray:
        return set()
    ids = set()
    uid = normalize_tag_uid(tray.get("tag_uid") or "")
    if uid and uid != "0" * len(uid):
        ids.add(f"uid:{uid}")
    uuid = normalize_tray_uuid(tray.get("tray_uuid") or "")
    if uuid and uuid != "0" * len(uuid):
        ids.add(f"uuid:{uuid}")
    return ids


async def _clear_fallback_tag(db, spool_id: int, vacated: list[Slot]) -> None:
    """Clear the Spoolman spool's extra.tag when it is a vacated slot's fallback tag.

    As a Spoolman move does (#1574): the tag lookup, which usage tracking asks
    before the slot table, would otherwise keep finding the spool for the old
    slot. Best effort: the rows are already gone, and a failure is logged.
    """
    from backend.app.api.routes.spoolman_inventory import _clear_vacated_slot_fallback_tag
    from backend.app.services.spoolman import get_spoolman_client

    try:
        client = await get_spoolman_client()
        spool = await client.get_spool(spool_id) if client else None
        if client and spool:
            await _clear_vacated_slot_fallback_tag(client, db, spool_id=spool_id, spool=spool, vacated=vacated)
    except Exception as e:
        logger.warning("Could not clear the old slot's fallback tag from Spoolman spool %s: %s", spool_id, e)


async def release_other_slots(inventory: str, spool_id: int, keep: Slot) -> list[Slot]:
    """Unassign ``spool_id`` from every slot but ``keep``, where a tag was just read.

    Only acts while ``keep`` reports a tag and still holds the spool. A slot
    whose printer reports the same tag right now keeps its assignment: one tag
    in two slots is a misread, and taking turns removing each other would be
    worse than leaving both. Returns the slots it removed, already announced.
    """
    from backend.app.core.database import async_session

    tag = _tag_ids(_live_tray(*keep))
    if not tag:
        return []

    model = SpoolAssignment if inventory == INTERNAL else SpoolmanSlotAssignment
    spool_col = SpoolAssignment.spool_id if inventory == INTERNAL else SpoolmanSlotAssignment.spoolman_spool_id

    async def _holders() -> set[int]:
        async with async_session() as db:
            rows = await db.execute(select(model.printer_id).where(spool_col == spool_id).distinct())
            return set(rows.scalars())

    # Spoolman slots have no printer lock to take, as a Spoolman move takes none.
    locked = ({keep[0]} | await _holders()) if inventory == INTERNAL else set()
    vacated: list[Slot] = []
    while True:
        async with AsyncExitStack() as locks:
            await locks.enter_async_context(get_spool_move_lock(inventory, spool_id))
            for pid in sorted(locked):
                await locks.enter_async_context(get_ams_assignment_lock(pid))
            async with async_session() as db:
                rows = (await db.execute(select(model).where(spool_col == spool_id))).scalars().all()
                if inventory == INTERNAL and not {row.printer_id for row in rows} <= locked:
                    locked |= {row.printer_id for row in rows}
                    continue
                if keep not in {(row.printer_id, row.ams_id, row.tray_id) for row in rows}:
                    # Reassigned since the read: whatever holds the slot now decides.
                    return []
                for row in rows:
                    slot = (row.printer_id, row.ams_id, row.tray_id)
                    if slot == keep or _tag_ids(_live_tray(*slot)) & tag:
                        continue
                    vacated.append(slot)
                    await db.delete(row)
                await db.commit()
                if inventory == SPOOLMAN and vacated:
                    await _clear_fallback_tag(db, spool_id, vacated)
        break

    if vacated:
        logger.info(
            "Tag of spool %s read on printer %d AMS%d-T%d: unassigned it from %s",
            spool_id,
            *keep,
            ", ".join(f"printer {p} AMS{a}-T{t}" for p, a, t in vacated),
        )
    for printer_id, ams_id, tray_id in vacated:
        await ws_manager.broadcast(
            {"type": "spool_assignment_changed", "printer_id": printer_id, "ams_id": ams_id, "tray_id": tray_id}
        )
    return vacated
