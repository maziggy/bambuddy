"""Service layer for pending slot assignments.

Manages the lifecycle of assign-on-next-slot requests: creation, completion on
AMS slot transitions, timeout expiry, cancellation, and event broadcasting.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.websocket import ws_manager
from backend.app.models.pending_slot_assignment import PendingSlotAssignment
from backend.app.models.spool import Spool

logger = logging.getLogger(__name__)

# In-memory set of active timeout tasks keyed by assignment ID so they can be
# cancelled if the assignment completes or is explicitly cancelled.
_timeout_tasks: dict[int, asyncio.Task] = {}


async def _resolve_spool(
    db: AsyncSession, spool_id: int | None, tray_uuid: str | None, tag_uid: str | None
) -> Spool | None:
    """Find an inventory spool by spool_id (preferred), tray_uuid, or tag_uid (fallback)."""
    if spool_id:
        result = await db.execute(select(Spool).where(Spool.id == spool_id, Spool.archived_at.is_(None)))
        spool = result.scalar_one_or_none()
        if spool:
            return spool
    if tray_uuid:
        result = await db.execute(
            select(Spool).where(
                func.upper(Spool.tray_uuid) == tray_uuid.upper(),
                Spool.archived_at.is_(None),
            )
        )
        spool = result.scalar_one_or_none()
        if spool:
            return spool
    if tag_uid:
        result = await db.execute(
            select(Spool).where(
                func.upper(Spool.tag_uid) == tag_uid.upper(),
                Spool.archived_at.is_(None),
            )
        )
        return result.scalar_one_or_none()
    return None


async def create_pending_assignment(
    db: AsyncSession,
    *,
    tray_uuid: str | None,
    tag_uid: str | None,
    printer_id: int | None,
    spool_id: int | None,
    source: str,
    timeout_seconds: int,
) -> PendingSlotAssignment:
    """Create a new pending assignment or return existing one for idempotency.

    Idempotency is based on tray_uuid/tag_uid: if a pending assignment already
    exists for the same spool identifiers, the existing one is returned.
    """

    # Build filters to match an existing pending assignment by identifiers
    id_filters = []
    if spool_id:
        id_filters.append(PendingSlotAssignment.spool_id == spool_id)
    if tray_uuid:
        id_filters.append(func.upper(PendingSlotAssignment.tray_uuid) == tray_uuid.upper())
    if tag_uid:
        id_filters.append(func.upper(PendingSlotAssignment.tag_uid) == tag_uid.upper())

    # Check for an existing pending assignment for the same spool
    # to avoid assigning the same spool to multiple slots concurrently.
    if id_filters:
        existing_spool = await db.execute(
            select(PendingSlotAssignment).where(
                PendingSlotAssignment.status == "pending",
                or_(*id_filters),
            )
        )
        found_spool = existing_spool.scalar_one_or_none()
        if found_spool:
            return found_spool

    # Resolve spool_id from tray_uuid or tag_uid
    spool = await _resolve_spool(db, spool_id, tray_uuid, tag_uid)

    assignment = PendingSlotAssignment(
        tray_uuid=tray_uuid,
        tag_uid=tag_uid,
        spool_id=spool.id if spool else None,
        printer_id=printer_id,
        source=source,
        status="pending",
        timeout_seconds=timeout_seconds,
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)

    # Schedule timeout task
    _schedule_timeout(assignment.id, timeout_seconds)

    logger.info(
        "Created pending slot assignment %d for spool_id=%d tray_uuid=%s tag_uid=%s printer=%s source=%s timeout=%ds",
        assignment.id,
        spool_id,
        tray_uuid,
        tag_uid,
        printer_id,
        source,
        timeout_seconds,
    )

    return assignment


def _schedule_timeout(assignment_id: int, timeout_seconds: int) -> None:
    """Schedule an asyncio task to expire the assignment after timeout."""
    task = asyncio.ensure_future(_expire_assignment(assignment_id, timeout_seconds))
    _timeout_tasks[assignment_id] = task
    task.add_done_callback(lambda _: _timeout_tasks.pop(assignment_id, None))


async def _expire_assignment(assignment_id: int, timeout_seconds: int) -> None:
    """Wait for timeout and then mark the assignment as timed_out."""
    await asyncio.sleep(timeout_seconds)

    from backend.app.core.database import async_session

    async with async_session() as db:
        result = await db.execute(
            select(PendingSlotAssignment).where(
                PendingSlotAssignment.id == assignment_id,
                PendingSlotAssignment.status == "pending",
            )
        )
        assignment = result.scalar_one_or_none()
        if assignment:
            assignment.status = "timed_out"
            await db.commit()

            logger.info("Pending slot assignment %d timed out", assignment_id)

            # Notify clients via WebSocket
            await ws_manager.broadcast(
                {
                    "type": "pending_slot_assignment_timed_out",
                    "assignment_id": assignment_id,
                    "tray_uuid": assignment.tray_uuid,
                    "tag_uid": assignment.tag_uid,
                }
            )

            # Notify via MQTT relay
            try:
                from backend.app.services.mqtt_relay import mqtt_relay

                await mqtt_relay.on_slot_assignment_timed_out(
                    assignment_id=assignment_id,
                    tray_uuid=assignment.tray_uuid,
                    tag_uid=assignment.tag_uid,
                )
            except Exception:
                pass


async def cancel_pending_assignment(db: AsyncSession, assignment_id: int) -> PendingSlotAssignment | None:
    """Cancel a pending assignment. Returns the updated record or None if not found."""
    result = await db.execute(
        select(PendingSlotAssignment).where(
            PendingSlotAssignment.id == assignment_id,
            PendingSlotAssignment.status == "pending",
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        return None

    assignment.status = "cancelled"
    await db.commit()

    # Cancel the timeout task
    task = _timeout_tasks.pop(assignment_id, None)
    if task and not task.done():
        task.cancel()

    logger.info("Cancelled pending slot assignment %d", assignment_id)

    await ws_manager.broadcast(
        {
            "type": "pending_slot_assignment_cancelled",
            "assignment_id": assignment_id,
            "tray_uuid": assignment.tray_uuid,
            "tag_uid": assignment.tag_uid,
        }
    )

    return assignment


async def get_pending_assignment(db: AsyncSession, assignment_id: int) -> PendingSlotAssignment | None:
    """Get a pending assignment by ID."""
    result = await db.execute(select(PendingSlotAssignment).where(PendingSlotAssignment.id == assignment_id))
    return result.scalar_one_or_none()


async def try_complete_pending_assignments(
    db: AsyncSession,
    printer_id: int,
    ams_id: int,
    tray_id: int,
) -> bool:
    """Check if any pending assignment should be completed for this slot.

    Called from the AMS change handler when a slot transitions from empty → filled.
    Returns True if a pending assignment was completed.
    """
    # Find pending assignments that match this printer (or any printer)
    result = await db.execute(
        select(PendingSlotAssignment)
        .where(
            PendingSlotAssignment.status == "pending",
            ((PendingSlotAssignment.printer_id == printer_id) | (PendingSlotAssignment.printer_id.is_(None))),
        )
        .order_by(PendingSlotAssignment.created_at.asc())
    )
    pending_assignments = result.scalars().all()

    if not pending_assignments:
        return False

    # Take the oldest pending assignment (FIFO)
    assignment = pending_assignments[0]

    # Resolve the spool if not already resolved
    spool_id = assignment.spool_id
    if not spool_id:
        spool = await _resolve_spool(db, assignment.tray_uuid, assignment.tag_uid)
        if spool:
            spool_id = spool.id
            assignment.spool_id = spool_id

    if not spool_id:
        logger.warning(
            "Pending assignment %d: no inventory spool found for tray_uuid=%s tag_uid=%s, skipping",
            assignment.id,
            assignment.tray_uuid,
            assignment.tag_uid,
        )
        return False

    # Delegate the actual assignment (upsert + MQTT config + WS broadcast) to
    # the existing assign_spool endpoint handler.
    try:
        from backend.app.api.routes.inventory import assign_spool
        from backend.app.schemas.spool import SpoolAssignmentCreate

        await assign_spool(
            data=SpoolAssignmentCreate(
                spool_id=spool_id,
                printer_id=printer_id,
                ams_id=ams_id,
                tray_id=tray_id,
            ),
            db=db,
            current_user=None,
        )
    except Exception:
        logger.exception(
            "Failed to execute assign_spool for pending assignment %d (spool %d → printer %d AMS%d-T%d)",
            assignment.id,
            spool_id,
            printer_id,
            ams_id,
            tray_id,
        )
        return False

    # Mark the pending assignment as completed
    now = datetime.now(timezone.utc)
    time_to_placement = (now - assignment.created_at.replace(tzinfo=timezone.utc)).total_seconds()

    assignment.status = "completed"
    assignment.assigned_printer_id = printer_id
    assignment.assigned_ams_id = ams_id
    assignment.assigned_tray_id = tray_id
    assignment.completed_at = now
    assignment.time_to_placement = time_to_placement

    await db.commit()

    # Cancel timeout task
    task = _timeout_tasks.pop(assignment.id, None)
    if task and not task.done():
        task.cancel()

    logger.info(
        "Completed pending slot assignment %d: spool %d → printer %d AMS%d-T%d (%.1fs)",
        assignment.id,
        spool_id,
        printer_id,
        ams_id,
        tray_id,
        time_to_placement,
    )

    # Notify clients via WebSocket
    await ws_manager.broadcast(
        {
            "type": "pending_slot_assignment_completed",
            "assignment_id": assignment.id,
            "tray_uuid": assignment.tray_uuid,
            "tag_uid": assignment.tag_uid,
            "spool_id": spool_id,
            "printer_id": printer_id,
            "ams_id": ams_id,
            "tray_id": tray_id,
            "time_to_placement": time_to_placement,
        }
    )

    # Notify via MQTT relay
    try:
        from backend.app.services.mqtt_relay import mqtt_relay

        await mqtt_relay.on_slot_assignment_completed(
            assignment_id=assignment.id,
            tray_uuid=assignment.tray_uuid,
            tag_uid=assignment.tag_uid,
            spool_id=spool_id,
            printer_id=printer_id,
            ams_id=ams_id,
            tray_id=tray_id,
            time_to_placement=time_to_placement,
        )
    except Exception:
        pass

    return True


async def restore_pending_timeouts() -> None:
    """Re-schedule timeout tasks for any pending assignments on startup.

    Called during application lifespan startup to resume monitoring of
    assignments that were created before a restart.
    """
    from backend.app.core.database import async_session

    async with async_session() as db:
        result = await db.execute(select(PendingSlotAssignment).where(PendingSlotAssignment.status == "pending"))
        pending = result.scalars().all()
        now = datetime.now(timezone.utc)
        for assignment in pending:
            created = assignment.created_at.replace(tzinfo=timezone.utc)
            elapsed = (now - created).total_seconds()
            remaining = assignment.timeout_seconds - elapsed
            if remaining <= 0:
                # Already expired — mark it
                assignment.status = "timed_out"
            else:
                _schedule_timeout(assignment.id, int(remaining))
        if pending:
            await db.commit()
            logger.info(
                "Restored %d pending slot assignment timeout tasks on startup",
                len([a for a in pending if a.status == "pending"]),
            )
