"""Action dispatch for Obico failure detection.

Separated from the detection loop so actions can be unit-tested and swapped.
"""

import logging

from sqlalchemy import select

from backend.app.core.database import async_session
from backend.app.models.printer import Printer

logger = logging.getLogger(__name__)


async def execute_action(printer_id: int, action: str, task_name: str, score: float) -> None:
    """Run the configured action for a detected print failure.

    action: 'notify' | 'pause' | 'pause_and_off'
    """
    printer_name = await _get_printer_name(printer_id)

    if action in ("pause", "pause_and_off"):
        _pause_print(printer_id)

    if action == "pause_and_off":
        await _turn_off_linked_plugs(printer_id)

    await _notify(printer_id, printer_name, task_name, score, action)


async def _get_printer_name(printer_id: int) -> str:
    async with async_session() as db:
        result = await db.execute(select(Printer).where(Printer.id == printer_id))
        printer = result.scalar_one_or_none()
    return printer.name if printer else f"Printer {printer_id}"


def _pause_print(printer_id: int) -> None:
    from backend.app.services.printer_manager import printer_manager

    client = printer_manager.get_client(printer_id)
    if not client:
        logger.warning("Obico pause: no MQTT client for printer %s", printer_id)
        return
    if not client.pause_print():
        logger.warning("Obico pause: pause_print() returned False for printer %s", printer_id)


async def _turn_off_linked_plugs(printer_id: int) -> None:
    from backend.app.services.smart_plug_manager import smart_plug_manager

    async with async_session() as db:
        plugs = await smart_plug_manager._get_plugs_for_printer(printer_id, db)
        for plug in plugs:
            if not plug.enabled:
                continue
            try:
                service = await smart_plug_manager.get_service_for_plug(plug, db)
                await service.turn_off(plug)
                logger.info("Obico action: turned off plug %s for printer %s", plug.name, printer_id)
            except Exception as e:
                logger.error("Obico action: failed to turn off plug %s: %s", plug.name, e)


async def _notify(printer_id: int, printer_name: str, task_name: str, score: float, action: str) -> None:
    from backend.app.services.notification_service import notification_service

    detail = (
        f"Possible print failure detected on '{task_name or 'current job'}' "
        f"(confidence {score:.2f}). Action taken: {action}."
    )
    async with async_session() as db:
        try:
            await notification_service.on_printer_error(
                printer_id=printer_id,
                printer_name=printer_name,
                error_type="ai_failure_detection",
                db=db,
                error_detail=detail,
            )
        except Exception as e:
            logger.error("Obico notify failed for printer %s: %s", printer_id, e)
