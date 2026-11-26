"""Plate changer capacity counting (#3575).

A printer with a plate changer capacity set spends one plate on every
successful print whose plate nobody has to confirm. The queue sends it nothing
once none is left, until someone refills the changer and empties its bin.
"""

import logging

from sqlalchemy import select, text, update

from backend.app.core.database import async_session, run_with_retry
from backend.app.core.websocket import ws_manager
from backend.app.models.printer import Printer
from backend.app.services import plate_clear
from backend.app.services.notification_service import notification_service

logger = logging.getLogger(__name__)

# Printers whose finished print is being closed out and whose plate count is not
# booked yet (#3575). The queue leaves them alone meanwhile.
_count_pending: set[int] = set()
# Printers whose count could not be booked (#3575). They read as empty until
# someone resets the count: a plate changer with no plate left can damage the printer.
_count_unknown: set[int] = set()


def mark_count_pending(printer_id: int) -> None:
    """The completion path's first step: a plate count is about to be booked."""
    _count_pending.add(printer_id)


def clear_count_pending(printer_id: int) -> None:
    _count_pending.discard(printer_id)


def is_count_pending(printer_id: int) -> bool:
    return printer_id in _count_pending


def mark_count_unknown(printer_id: int) -> None:
    _count_unknown.add(printer_id)


def is_count_unknown(printer_id: int) -> bool:
    return printer_id in _count_unknown


def clear_count_unknown(printer_id: int) -> None:
    """Someone reset the count (refill or capacity edit): trust the stored value again."""
    _count_unknown.discard(printer_id)


def forget_printer(printer_id: int) -> None:
    _count_pending.discard(printer_id)
    _count_unknown.discard(printer_id)


async def _fail_closed(printer_id: int) -> None:
    """The count could not be booked: treat the printer as empty until it is reset."""
    mark_count_unknown(printer_id)
    logger.error(
        "Printer %s: plate changer count could not be booked — treating it as empty until it is reset (#3575)",
        printer_id,
    )
    # Persisted on top of the in-memory mark, so it survives a restart.
    try:

        async def _zero(db):
            await db.execute(
                text("UPDATE printers SET plates_remaining = 0 WHERE id = :id AND plate_capacity IS NOT NULL"),
                {"id": printer_id},
            )
            await db.commit()

        await run_with_retry(_zero, label="plate changer fail closed")
    except Exception:
        logger.exception("plate changer could not be zeroed for printer %s", printer_id)

    try:
        async with async_session() as db:
            row = (
                await db.execute(select(Printer.name, Printer.plate_capacity).where(Printer.id == printer_id))
            ).first()
            if row is None or row.plate_capacity is None:
                return
            await ws_manager.send_printer_plates_changed(printer_id, 0, row.plate_capacity)
            # Not the "all plates used" text: nobody knows how many are left.
            await notification_service.on_plate_changer_empty(
                printer_id, row.name, row.plate_capacity, db, count_unknown=True
            )
    except Exception:
        logger.exception("plate changer empty notification failed for printer %s", printer_id)


async def consume_auto_cleared_plate(printer_id: int, *, job_clears_plate: bool = False) -> None:
    """Count down a plate changer after a print that needed no plate confirmation.

    Does nothing for a printer without a capacity, or one that waits for the
    plate-clear confirmation after a print: someone clears that one by hand.
    ``job_clears_plate`` says the finished job cleared its own plate (#3576):
    it swapped the plate, so one is spent whatever the plate-clear setting says.
    Never raises, so a counting problem cannot break the print completion. When
    the count cannot be booked, the printer reads as empty until it is reset.
    """
    from backend.app.api.routes.settings import get_setting, setting_is_true

    try:

        async def _consume(db):
            # Cheap read first: a printer without a plate changer costs no write.
            if await db.scalar(select(Printer.plate_capacity).where(Printer.id == printer_id)) is None:
                return None
            # Where the plate-clear gate applies, the app setting or the printer's
            # own (#2860), someone clears the plate by hand and no plate is spent,
            # unless the job cleared its plate itself (#3576).
            setting = setting_is_true(await get_setting(db, "require_plate_clear"))
            if not job_clears_plate and plate_clear.applies(printer_id, setting):
                return None
            # One atomic statement: two prints ending together cannot both read
            # the same count, and it never goes below zero.
            row = (
                await db.execute(
                    update(Printer)
                    .where(
                        Printer.id == printer_id,
                        Printer.plate_capacity.is_not(None),
                        Printer.plates_remaining > 0,
                    )
                    .values(plates_remaining=Printer.plates_remaining - 1)
                    .returning(Printer.name, Printer.plate_capacity, Printer.plates_remaining)
                    .execution_options(synchronize_session=False)
                )
            ).first()
            await db.commit()
            return row

        try:
            row = await run_with_retry(_consume, label="plate changer count")
        except Exception:
            logger.exception("plate changer count not updated for printer %s", printer_id)
            await _fail_closed(printer_id)
            return
        finally:
            clear_count_pending(printer_id)
        if row is None:
            return
        name, capacity, remaining = row

        try:
            await ws_manager.send_printer_plates_changed(printer_id, remaining, capacity)
        except Exception:
            logger.exception("plate changer count broadcast failed for printer %s", printer_id)

        if remaining == 0:
            logger.info("Printer %s (%s) used its last plate (capacity %s)", printer_id, name, capacity)
            try:
                async with async_session() as db:
                    await notification_service.on_plate_changer_empty(printer_id, name, capacity, db)
            except Exception:
                logger.exception("plate changer empty notification failed for printer %s", printer_id)
    except Exception:
        logger.exception("plate changer count failed for printer %s", printer_id)
        clear_count_pending(printer_id)
