"""Telling the outside world that jobs were added to the queue.

Every path that creates queue jobs calls this once, after its commit: the print
dialog, File Manager's "Add to queue", the webhook, a virtual printer, a
pipeline run and an order's "dispatch remaining". The "job added" notification
is one summary per printer target, so a bulk add is one message, not one per
job, and a provider scoped to one printer hears only of that printer's jobs.
The MQTT relay is a machine event and gets one per job.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer

logger = logging.getLogger(__name__)

# Names listed in a summary of different files; the rest is "…".
_NAMES_LISTED = 3


def _job_name(filename: str | None, item_id: int) -> str:
    if not filename:
        return f"Job #{item_id}"
    return filename.replace(".gcode.3mf", "").replace(".3mf", "")


def _summary(names: list[str]) -> str:
    distinct = list(dict.fromkeys(names))
    if len(distinct) == 1:
        return f"{distinct[0]} ×{len(names)}" if len(names) > 1 else distinct[0]
    listed = ", ".join(distinct[:_NAMES_LISTED]) + (", …" if len(distinct) > _NAMES_LISTED else "")
    return f"{len(names)} jobs ({listed})"


def _target(printer_name: str | None, models: list[str]) -> str:
    if printer_name:
        return printer_name
    distinct = list(dict.fromkeys(models))
    return f"Any {' / '.join(distinct)}" if distinct else "Unassigned"


async def announce_queue_jobs_added(db: AsyncSession, item_ids: Sequence[int]) -> None:
    """Send the "job added" notification and relay events for new queue jobs.

    Never raises: a queue add must not fail because a notification did.
    """
    if not item_ids:
        return
    try:
        await _announce(db, item_ids)
    except Exception as e:
        logger.warning("Could not announce new queue jobs: %s", e)


async def _announce(db: AsyncSession, item_ids: Sequence[int]) -> None:
    try:
        rows = (
            await db.execute(
                select(
                    PrintQueueItem.id,
                    PrintQueueItem.printer_id,
                    PrintQueueItem.target_model,
                    Printer.name.label("printer_name"),
                    PrintArchive.filename.label("archive_filename"),
                    LibraryFile.filename.label("library_filename"),
                )
                .outerjoin(Printer, Printer.id == PrintQueueItem.printer_id)
                .outerjoin(PrintArchive, PrintArchive.id == PrintQueueItem.archive_id)
                .outerjoin(LibraryFile, LibraryFile.id == PrintQueueItem.library_file_id)
                .where(PrintQueueItem.id.in_(list(item_ids)))
                .order_by(PrintQueueItem.position, PrintQueueItem.id)
            )
        ).all()
    except Exception as e:
        logger.warning("Could not announce new queue jobs: %s", e)
        return

    from backend.app.services.mqtt_relay import mqtt_relay
    from backend.app.services.notification_service import notification_service

    groups: dict[int | None, list] = {}
    for row in rows:
        groups.setdefault(row.printer_id, []).append(row)
        try:
            await mqtt_relay.on_queue_job_added(
                job_id=row.id,
                filename=row.archive_filename or row.library_filename or "",
                printer_id=row.printer_id,
                printer_name=row.printer_name,
            )
        except Exception:
            pass  # Don't fail queue add if MQTT fails

    for printer_id, group in groups.items():
        printer_name = group[0].printer_name
        try:
            await notification_service.on_queue_job_added(
                job_name=_summary([_job_name(r.archive_filename or r.library_filename, r.id) for r in group]),
                target=_target(printer_name, [r.target_model for r in group if r.target_model]),
                db=db,
                printer_id=printer_id,
                printer_name=printer_name,
            )
        except Exception as e:
            logger.warning("Queue job added notification failed: %s", e)
            # Its log write commits on this session: a failed one must not leave
            # the request's session unusable after the job was committed.
            try:
                await db.rollback()
            except Exception:
                pass
