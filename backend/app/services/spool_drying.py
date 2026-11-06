"""Record on each spool when it was last dried (#2863).

An AMS drying cycle dries every spool in that unit, but the AMS humidity only
describes the unit, so once a spool is moved the fact is lost. When a cycle
ends, every spool assigned to a slot of that AMS is stamped with the time, the
target temperature and the hours it actually ran, in whichever inventory holds
the slot assignments.

A cycle counts when it ran at least ``DRIED_MIN_FRACTION`` of its length. One
stopped a few minutes in, by the user, the firmware or a print taking
priority, has not dried anything and must not overwrite an earlier, real
drying.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes._spoolman_helpers import (
    BAMBU_LAST_DRIED_AT_KEY,
    BAMBU_LAST_DRIED_HOURS_KEY,
    BAMBU_LAST_DRIED_TEMP_KEY,
)
from backend.app.services.bambu_mqtt import DryingCycleEnd
from backend.app.utils.local_time import utcnow_naive

logger = logging.getLogger(__name__)

# Share of a cycle's length that must have run for its spools to count as dried.
DRIED_MIN_FRACTION = 0.5

# A cycle that runs to term ends with its countdown all but exhausted; the last
# dry_time seen can trail by a push or two. Same margin the MQTT client uses to
# tell a finished cycle from an aborted one.
RAN_TO_TERM_MINUTES = 5


@dataclass(frozen=True)
class DryingRecord:
    temp: int | None
    hours: float | None


def drying_record(cycle: DryingCycleEnd) -> DryingRecord | None:
    """What a finished cycle says about its spools, or None when it does not count.

    The cycle's length is the requested duration when Bambuddy started it, else
    the highest countdown seen. When the start was not watched and nothing was
    requested, that peak understates the length, so the elapsed share is a
    lower bound and the hours are left unknown rather than understated. A cycle
    that ran to term counts either way.
    """
    requested = (cycle.target_hours or 0) * 60
    length = max(cycle.peak_minutes, requested)
    elapsed = max(0, length - cycle.remaining_minutes)
    ran_to_term = cycle.remaining_minutes <= RAN_TO_TERM_MINUTES
    if not ran_to_term and (length <= 0 or elapsed / length < DRIED_MIN_FRACTION):
        return None
    length_known = cycle.start_seen or requested > 0
    hours = round(elapsed / 60, 1) if length_known and elapsed > 0 else None
    return DryingRecord(temp=cycle.target_temp, hours=hours)


async def _spoolman_spool_ids(db: AsyncSession, printer_id: int, ams_id: int) -> list[int]:
    from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

    result = await db.execute(
        select(SpoolmanSlotAssignment.spoolman_spool_id).where(
            SpoolmanSlotAssignment.printer_id == printer_id,
            SpoolmanSlotAssignment.ams_id == ams_id,
        )
    )
    return sorted(set(result.scalars().all()))


async def _stamp_internal(db: AsyncSession, printer_id: int, ams_id: int, record: DryingRecord) -> int:
    from backend.app.models.spool import Spool
    from backend.app.models.spool_assignment import SpoolAssignment

    result = await db.execute(
        select(Spool)
        .join(SpoolAssignment, SpoolAssignment.spool_id == Spool.id)
        .where(SpoolAssignment.printer_id == printer_id, SpoolAssignment.ams_id == ams_id)
    )
    spools = list(result.scalars().unique().all())
    now = utcnow_naive()
    for spool in spools:
        spool.last_dried_at = now
        spool.last_dried_temp = record.temp
        spool.last_dried_hours = record.hours
    if spools:
        await db.commit()
    return len(spools)


async def _stamp_spoolman(spool_ids: list[int], record: DryingRecord) -> int:
    from backend.app.services.spoolman import get_spoolman_client

    client = await get_spoolman_client()
    if client is None:
        logger.info("Drying not recorded on Spoolman spools %s: Spoolman is not connected", spool_ids)
        return 0
    # JSON strings: the form Spoolman's default "text" extra fields accept.
    fields = {
        BAMBU_LAST_DRIED_AT_KEY: json.dumps(utcnow_naive().isoformat(timespec="seconds")),
        # An empty string clears a value left by an earlier cycle.
        BAMBU_LAST_DRIED_TEMP_KEY: json.dumps("" if record.temp is None else str(record.temp)),
        BAMBU_LAST_DRIED_HOURS_KEY: json.dumps("" if record.hours is None else str(record.hours)),
    }
    stamped = 0
    for spool_id in spool_ids:
        try:
            await client.merge_spool_extra(spool_id, fields)
            stamped += 1
        except Exception as exc:  # noqa: BLE001 — one unreachable spool must not skip the rest
            logger.warning("Could not record drying on Spoolman spool %d: %s", spool_id, exc)
    return stamped


async def record_drying_cycle(db: AsyncSession, printer_id: int, cycle: DryingCycleEnd) -> int:
    """Stamp the spools of the AMS that just finished drying. Returns how many."""
    from backend.app.services.inventory_mode import spoolman_owns_assignments

    record = drying_record(cycle)
    if record is None:
        logger.info(
            "Printer %d AMS %d drying ended with %d of %d minutes left: too short to mark its spools as dried",
            printer_id,
            cycle.ams_id,
            cycle.remaining_minutes,
            max(cycle.peak_minutes, (cycle.target_hours or 0) * 60),
        )
        return 0

    if await spoolman_owns_assignments(db):
        spool_ids = await _spoolman_spool_ids(db, printer_id, cycle.ams_id)
        stamped = await _stamp_spoolman(spool_ids, record) if spool_ids else 0
    else:
        stamped = await _stamp_internal(db, printer_id, cycle.ams_id, record)

    logger.info(
        "Printer %d AMS %d drying finished (%s °C, %s h): marked %d spool(s) as dried",
        printer_id,
        cycle.ams_id,
        record.temp if record.temp is not None else "?",
        record.hours if record.hours is not None else "?",
        stamped,
    )
    if stamped:
        from backend.app.core.websocket import ws_manager

        await ws_manager.broadcast({"type": "inventory_changed"})
    return stamped
