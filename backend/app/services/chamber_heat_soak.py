"""Explicit, bounded pre-print heat soaking; no material or keep-warm policy.

Database write locks serialize controls with cancellation. A process owns a
reservation until it finishes or misses its heartbeat; another worker may only
abort an expired attempt, never resume its timer or dispatch its job.
"""

import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.tasks import spawn_background_task
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.printer_manager import printer_manager, supports_chamber_heater

logger = logging.getLogger(__name__)
HEARTBEAT_TIMEOUT = 90
TELEMETRY_TIMEOUT = 60


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def supports_airduct(model: str | None) -> bool:
    return supports_chamber_heater(model) or (model or "").strip().upper() in {"P2S", "N7"}


async def lock_queue_item(db: AsyncSession, item_id: int) -> PrintQueueItem | None:
    """Take a write lock on both SQLite and PostgreSQL, then discard stale ORM state."""
    with db.no_autoflush:
        result = await db.execute(
            update(PrintQueueItem)
            .where(PrintQueueItem.id == item_id)
            .values(status=PrintQueueItem.status)
            .execution_options(synchronize_session=False)
        )
        if not result.rowcount:
            return None
        return await db.get(PrintQueueItem, item_id, populate_existing=True)


def _reported(state, key: str, value: int, since: datetime) -> bool:
    report = getattr(state, "heat_soak_reports", {}).get(key)
    return bool(
        report
        and report[0] == value
        and report[1] >= since.replace(tzinfo=timezone.utc).timestamp()
        and 0 <= time.time() - report[1] < TELEMETRY_TIMEOUT
    )


def _show_preheating(printer_id: int, active: bool) -> None:
    state = printer_manager.get_status(printer_id)
    if state and getattr(state, "preheating", False) != active:
        state.preheating = active
        spawn_background_task(
            printer_manager._broadcast_status_change(printer_id), name=f"heat-soak-status-{printer_id}"
        )


def _heaters_off(printer: Printer) -> None:
    client = printer_manager.get_client(printer.id)
    if not client or not printer_manager.is_connected(printer.id):
        return
    # Attempt every shutdown command even if another one fails.
    commands = [(client.set_bed_temperature, 0)]
    if supports_chamber_heater(printer.model):
        commands.append((client.set_chamber_temperature, 0))
    if supports_airduct(printer.model):
        commands.append((client.set_airduct_mode, "cooling"))
    for command, value in commands:
        try:
            command(value)
        except Exception:
            logger.exception("Heat-soak heater shutdown failed for printer %s", printer.id)


async def abort_heat_soak(db: AsyncSession, item: PrintQueueItem, reason: str, *, status: str = "pending") -> None:
    """Caller holds the queue write lock. Persist cleanup even if the item is deleted."""
    printer = await db.get(Printer, item.printer_id)
    if printer:
        printer.heat_soak_shutdown_pending = True
        printer.heat_soak_shutdown_at = utcnow()
        _heaters_off(printer)
    _show_preheating(item.printer_id, False)
    item.status = status
    item.error_message = reason
    item.completed_at = utcnow()
    item.preheat_owner = None
    item.preheat_started_at = None
    item.preheat_checked_at = None
    # An explicit retry must repeat the complete soak.
    item.manual_start = True
    await db.commit()


class ChamberHeatSoak:
    def __init__(self):
        self.owner = str(uuid4())
        self._visible_printers: set[int] = set()

    async def stage(self, db: AsyncSession, item: PrintQueueItem) -> bool:
        item_id, printer_id = item.id, item.printer_id
        item = await lock_queue_item(db, item_id)
        if not item or item.status != "pending":
            await db.rollback()
            return False
        # Preserve the selected printer for model-based queue items, but claim
        # only a still-pending row. Concurrent workers cannot reassign a winner.
        now = utcnow()
        try:
            result = await db.execute(
                update(PrintQueueItem)
                .where(PrintQueueItem.id == item_id, PrintQueueItem.status == "pending")
                .values(
                    status="preheating",
                    printer_id=printer_id,
                    preheat_owner=self.owner,
                    preheat_requested_at=now,
                    preheat_checked_at=now,
                    preheat_started_at=None,
                    dispatched_at=None,
                    dispatch_subtask_id=None,
                    error_message=None,
                    waiting_reason=None,
                )
                .execution_options(synchronize_session=False)
            )
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return False
        if not result.rowcount:
            return False
        # Reservation is durable before any heater command. Re-lock to ensure
        # a cancellation during commit cannot be followed by heater-on commands.
        item = await lock_queue_item(db, item_id)
        if not item or item.status != "preheating" or item.preheat_owner != self.owner:
            await db.rollback()
            return False
        printer = await db.get(Printer, printer_id, populate_existing=True)
        state = printer_manager.get_status(printer_id)
        client = printer_manager.get_client(printer_id)
        if (
            not printer
            or printer.heat_soak_shutdown_pending
            or not client
            or not printer_manager.is_connected(printer_id)
            or not state
            or state.state not in ("IDLE", "FINISH", "FAILED")
        ):
            await abort_heat_soak(db, item, "Heat soak could not start: printer unavailable or heater shutdown pending")
            return False
        # The soak duration is measured from the heater command, not from a
        # later telemetry update. Target telemetry can lag or be omitted by
        # firmware, and it should not make the wait unpredictable.
        heating_started_at = utcnow()
        try:
            accepted = True
            if supports_airduct(printer.model):
                accepted = client.set_airduct_mode("heating") and accepted
            accepted = client.set_bed_temperature(item.heat_soak_temperature) and accepted
            if supports_chamber_heater(printer.model):
                accepted = client.set_chamber_temperature(item.heat_soak_temperature) and accepted
            client.request_status_update()
            if not accepted:
                raise RuntimeError("Heating command could not be sent")
        except Exception:
            logger.exception("Could not start heat soak for queue item %s", item_id)
            await abort_heat_soak(db, item, "Heat-soak heating commands failed; retry required")
            return False
        item.preheat_started_at = heating_started_at
        await db.commit()
        _show_preheating(printer_id, True)
        self._visible_printers.add(printer_id)
        return True

    async def check(self, db: AsyncSession) -> list[int]:
        """Advance timers without sleeping or blocking other printers' scheduling."""
        await self.cleanup(db)
        ids = list(
            (
                await db.scalars(
                    select(PrintQueueItem.id).where(
                        or_(
                            PrintQueueItem.status == "preheating",
                            and_(
                                PrintQueueItem.status == "dispatching",
                                PrintQueueItem.chamber_heat_soak.is_(True),
                                PrintQueueItem.dispatch_subtask_id.is_(None),
                            ),
                        )
                    )
                )
            ).all()
        )
        ready = []
        visible: set[int] = set()
        for item_id in ids:
            item = await lock_queue_item(db, item_id)
            if not item or item.status not in ("preheating", "dispatching") or item.dispatch_subtask_id:
                await db.rollback()
                continue
            now = utcnow()
            elapsed = (now - item.preheat_checked_at).total_seconds() if item.preheat_checked_at else HEARTBEAT_TIMEOUT
            if elapsed < 0 or elapsed >= HEARTBEAT_TIMEOUT:
                await abort_heat_soak(db, item, "Heat soak interrupted by restart or scheduler timeout; retry required")
                continue
            if item.status == "dispatching":
                await db.rollback()
                continue
            visible.add(item.printer_id)
            _show_preheating(item.printer_id, True)
            if item.preheat_owner != self.owner:
                # A second live worker may own this reservation. The heartbeat
                # timeout above is the restart/recovery boundary.
                await db.rollback()
                continue
            printer = await db.get(Printer, item.printer_id)
            state = printer_manager.get_status(item.printer_id)
            requested = item.preheat_requested_at
            disconnected_at = getattr(state, "heat_soak_disconnected_at", 0) if state else 0
            if (
                not printer
                or not printer.is_active
                or not state
                or not printer_manager.is_connected(item.printer_id)
                or not requested
                or disconnected_at >= requested.replace(tzinfo=timezone.utc).timestamp()
                or state.state not in ("IDLE", "FINISH", "FAILED")
            ):
                await abort_heat_soak(
                    db, item, "Printer disconnected or became unavailable during heat soak; retry required"
                )
                continue
            if item.preheat_started_at is None:
                await abort_heat_soak(db, item, "Heat-soak start time missing; retry required")
                continue
            if (now - item.preheat_started_at).total_seconds() >= item.heat_soak_minutes * 60:
                _show_preheating(item.printer_id, False)
                item.status = "dispatching"
                item.dispatched_at = now
                ready.append(item.id)
            client = printer_manager.get_client(item.printer_id)
            if client:
                client.request_status_update()
            item.preheat_checked_at = now
            await db.commit()
        for printer_id in self._visible_printers - visible:
            _show_preheating(printer_id, False)
        self._visible_printers = visible
        return ready

    async def cleanup(self, db: AsyncSession) -> None:
        printers = list((await db.scalars(select(Printer).where(Printer.heat_soak_shutdown_pending.is_(True)))).all())
        for printer in printers:
            # Lock the printer so cleanup cannot race a new staging attempt.
            await db.execute(
                update(Printer)
                .where(Printer.id == printer.id)
                .values(heat_soak_shutdown_pending=Printer.heat_soak_shutdown_pending)
            )
            await db.refresh(printer)
            if not printer.heat_soak_shutdown_pending:
                await db.rollback()
                continue
            _heaters_off(printer)
            client = printer_manager.get_client(printer.id)
            if client:
                client.request_status_update()
            state = printer_manager.get_status(printer.id)
            since = printer.heat_soak_shutdown_at
            confirmed = bool(
                state
                and since
                and printer_manager.is_connected(printer.id)
                and _reported(state, "bed_target", 0, since)
            )
            if supports_chamber_heater(printer.model):
                confirmed = confirmed and _reported(state, "chamber_target", 0, since)
            if confirmed:
                printer.heat_soak_shutdown_pending = False
                printer.heat_soak_shutdown_at = None
            await db.commit()
