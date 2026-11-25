"""Persistence for the HMS faults already notified (#3486).

The notification de-duplication itself stays in ``main._take_new_hms_faults``,
in memory. This only stores which fault keys it holds, so a restart does not
send every fault a printer still lists again.
"""

import asyncio
import logging

from sqlalchemy import delete, select

from backend.app.core.database import async_session
from backend.app.models.hms_notified_fault import HmsNotifiedFault

logger = logging.getLogger(__name__)

# One writer at a time per printer, so the last write is the latest set.
_locks: dict[int, asyncio.Lock] = {}


async def load_notified_hms_faults() -> dict[int, set[str]]:
    """Every printer's notified fault keys, as stored."""
    async with async_session() as db:
        rows = (await db.execute(select(HmsNotifiedFault.printer_id, HmsNotifiedFault.fault_key))).all()
    loaded: dict[int, set[str]] = {}
    for printer_id, fault_key in rows:
        loaded.setdefault(printer_id, set()).add(fault_key)
    return loaded


async def save_notified_hms_faults(printer_id: int, current_keys) -> None:
    """Store *current_keys()* as the printer's notified faults.

    *current_keys* is called once the lock is held, so a write that waited
    stores the set as it is then, not as it was when the write was asked for.
    """
    lock = _locks.setdefault(printer_id, asyncio.Lock())
    async with lock:
        keys = set(current_keys())
        async with async_session() as db:
            stored = set(
                (
                    await db.execute(
                        select(HmsNotifiedFault.fault_key).where(HmsNotifiedFault.printer_id == printer_id)
                    )
                ).scalars()
            )
            gone = stored - keys
            if gone:
                await db.execute(
                    delete(HmsNotifiedFault).where(
                        HmsNotifiedFault.printer_id == printer_id, HmsNotifiedFault.fault_key.in_(gone)
                    )
                )
            for key in keys - stored:
                db.add(HmsNotifiedFault(printer_id=printer_id, fault_key=key))
            await db.commit()
