"""Stopping a print on a user's behalf.

Every place a user can stop a print goes through here, so none can send the
stop without the "stopped by the user" mark: the printer card, the queue and
the webhook routes call stop_print_by_user; the HMS error dialog's "Stop
Printing" sends its own stop payload and calls mark_stopped_by_user. Without
the mark the printer's resulting "failed"/"aborted" status is taken at face
value: a "print failed" notification goes out, and on some printers the
cancel sequence's own error is reported as a fault such as a layer shift.
"""

import logging

from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)


def mark_stopped_by_user(printer_id: int) -> None:
    """Record that the user stopped the print on this printer (see module docstring)."""
    try:
        from backend.app.main import mark_printer_stopped_by_user

        mark_printer_stopped_by_user(printer_id)
    except Exception as e:
        logger.warning("Failed to mark printer %s as user-stopped: %s", printer_id, e)


def stop_print_by_user(printer_id: int, *, mark_when_unsent: bool = False) -> bool:
    """Send the stop command; returns whether it was sent.

    The printer is marked as stopped by the user when the command was sent
    and the printer is not known to be idle (nothing to stop), or always with
    ``mark_when_unsent`` (the queue, which closes the job even
    when the printer is offline, so a "failed" it reports later still reads
    as the user's cancel). Errors sending the command propagate to the caller.
    """
    try:
        sent = printer_manager.stop_print(printer_id)
    except Exception:
        if mark_when_unsent:
            mark_stopped_by_user(printer_id)
        raise
    if mark_when_unsent or (sent and not _known_idle(printer_id)):
        mark_stopped_by_user(printer_id)
    return sent


def _known_idle(printer_id: int) -> bool:
    """The printer reports a state with no print loaded.

    A stop sent then stops nothing, and its mark would linger until the next
    print's start clears it -- which a new MQTT client that first sees that
    print mid-way never runs, so the print's later failure read as the user's
    cancel (USP/I4). An unknown state is not idle: the mark is the safe side.
    That includes "unknown", the state before the first report after a
    (re)connect.
    """
    status = printer_manager.get_status(printer_id)
    state = getattr(status, "state", None)
    if not state or state == "unknown":
        return False
    return state not in printer_manager.ACTIVE_PRINT_STATES
