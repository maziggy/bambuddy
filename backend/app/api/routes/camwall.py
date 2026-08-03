"""Read-only Cam Wall feed for token-authenticated kiosk displays (#2531).

The Cam Wall inside the SPA runs on the ordinary printers API, behind a JWT. A
wall pinned to a TV has no login, so it authenticates with a long-lived
``camwall``-scoped token carried in the URL — and a URL on a lobby screen is
about as private as a sticky note.

That is why this endpoint exists instead of letting a token through to
``GET /printers``: the printer list carries ``serial_number`` and
``ip_address`` (see ``schemas/printer.py``), and neither belongs on a screen in
a shared room. What a wall tile actually draws is the whole payload here — a
name, a connection flag, a state, a progress bar.

Notably absent is the print filename. A token wall renders the compact status
overlay, so the part being printed is never named to the room; the field simply
isn't served rather than being served and then hidden client-side.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequireCamWallTokenIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.models.printer import Printer
from backend.app.services.printer_manager import printer_manager

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/camwall", tags=["camwall"])


@router.get("/printers")
async def list_camwall_printers(
    _: None = RequireCamWallTokenIfAuthEnabled,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Every printer plus the handful of status fields a Cam Wall tile draws.

    One call for the whole wall rather than one per printer: a kiosk polls this
    on a fixed interval with no WebSocket to invalidate it, and N+1 requests
    every few seconds is a poor trade for a screen nobody is interacting with.

    Ordered by name so tile positions stay put across polls — a wall that
    reshuffles itself is unusable to watch.
    """
    result = await db.execute(select(Printer).order_by(Printer.name))
    printers = list(result.scalars().all())

    payload: list[dict] = []
    for printer in printers:
        state = printer_manager.get_status(printer.id)
        entry: dict = {
            "id": printer.id,
            "name": printer.name,
            "camera_rotation": printer.camera_rotation or 0,
            # Mirrors get_printer_status(): no state object at all means the
            # printer was never connected this run; a state object still has
            # to be asked whether its link is currently up.
            "connected": bool(state and state.connected),
            "state": None,
            "progress": None,
            "remaining_time": None,
            "layer_num": None,
            "total_layers": None,
            # Codes only — enough for the client to run the same
            # filterKnownHMSErrors() it uses on the authenticated wall, so the
            # error chip means the same thing in both modes.
            "hms_errors": [],
        }
        if state is not None:
            entry.update(
                {
                    "state": state.state,
                    "progress": state.progress,
                    "remaining_time": state.remaining_time,
                    "layer_num": state.layer_num,
                    "total_layers": state.total_layers,
                    "hms_errors": [
                        {
                            "code": e.code,
                            "attr": e.attr,
                            "module": e.module,
                            "severity": e.severity,
                            "actions": e.actions or [],
                        }
                        for e in (state.hms_errors or [])
                    ],
                }
            )
        payload.append(entry)

    return payload
