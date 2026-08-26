"""Which table holds a printer's slot assignments.

Bambuddy keeps AMS slot assignments in two places: ``spool_assignment`` for the
built-in inventory and ``spoolman_slot_assignments`` for Spoolman. Exactly one
of them describes reality at any moment, and which one is a user setting.

Until #2812 the two were kept from overlapping by emptying the inactive table
whenever the mode toggled, which made merely looking at the other mode destroy
the configuration you had. Nothing is deleted now, so both tables can hold rows
at once and every reader has to say which one it means.

This is deliberately a module of its own rather than a helper on the settings
routes: the readers are services, and importing an API route module from a
service to answer a one-key question invites an import cycle. Several call
sites already carry their own private copy of this predicate for that reason
(``filament_deficit``, ``print_scheduler``, ``inventory``); those are unchanged
and correct, and are only worth folding in here if they are touched anyway.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def spoolman_owns_assignments(db: AsyncSession) -> bool:
    """True when ``spoolman_slot_assignments`` is the table that counts.

    Fails closed to the built-in inventory: a setting that cannot be read is
    not evidence that the user switched modes, and treating an unreadable
    setting as "Spoolman" would make a built-in install look as though every
    tray were unassigned.
    """
    try:
        from backend.app.api.routes.settings import get_setting

        value = await get_setting(db, "spoolman_enabled")
        return bool(value) and value.lower() == "true"
    except Exception as exc:  # noqa: BLE001 — a mode probe must not raise into its callers
        logger.debug("Could not read spoolman_enabled, assuming built-in inventory: %s", exc)
        return False
