"""Which of a printer's plugs measures its energy? (#2859)

Per-print energy is the delta of one plug's lifetime counter between print start
and print end, so both readings have to come from the same plug. The two call
sites used to assume a printer had exactly one: they selected every plug with
``SmartPlug.printer_id == printer_id`` and then called ``scalar_one_or_none()``.

Nothing enforces that assumption. The plug API rejects a second *Tasmota* plug
on a printer and deliberately allows any number of Home Assistant entities --
"allow multiple per printer (for different automations)" -- which is how a
filter fan, a dry box or a lights script ends up linked beside the printer's own
plug. On those installs the query returned two rows, ``scalar_one_or_none()``
raised, the print-start handler caught it as just another failure and logged a
warning, and no archive on that printer ever carried an energy figure again. It
was silent because the print-end handler then reports "no start kWh recorded",
which reads exactly like "this printer has no plug".

The rule below picks the printer's own plug without asking the user to nominate
one. ``controls_printer_power`` (#2629) already means "this plug really feeds
the printer" rather than an accessory that merely follows the print cycle, so it
ranks above one that does not, and the id breaks ties so the start and end
readings agree on the answer. The decisive test in practice is the last one: a
candidate has to actually report a lifetime counter to be chosen, and accessory
plugs are usually switch-only, so they drop out with nothing configured.

Deliberately *not* enforced: one plug per printer. Bambuddy dropped the UNIQUE
constraint on ``smart_plugs.printer_id`` on purpose, and the flag defaults to on
for every existing plug, so clearing it to make it unique would change which
plugs may mark a printer offline on auto-off (#2629) -- not this module's
business.

Equally deliberate: nothing is excluded, only ranked. A printer with one linked
plug used it whatever it was, and must keep doing so, so a disabled row or a
script still gets its turn once the plausible candidates have declined.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.smart_plug import SmartPlug

# Reads a plug's energy dict, or None when the device did not answer. Injected
# rather than imported so this module stays independent of the plug-type
# dispatch that lives with the callers.
EnergyReader = Callable[[SmartPlug, AsyncSession], Awaitable[dict | None]]


def _is_script_entity(plug: SmartPlug) -> bool:
    """A Home Assistant ``script.*`` entity linked to a printer for automation.

    Stored as plugs so they can follow the print cycle (see
    ``trigger_associated_scripts``), but a script has nothing to meter.
    """
    return bool(plug.plug_type == "homeassistant" and plug.ha_entity_id and plug.ha_entity_id.startswith("script."))


def _rank(plug: SmartPlug) -> tuple:
    """Sort key: least surprising source of a printer's meter first.

    Ranking rather than filtering, deliberately. A printer with exactly one
    linked row behaved the same before this module existed whatever that row
    was -- disabled, a script, an accessory -- and it has to keep behaving that
    way, so nothing is excluded outright and every rejection is left to the one
    test that cannot be wrong: does it actually report a counter.
    """
    return (
        _is_script_entity(plug),
        not plug.enabled,
        not plug.controls_printer_power,
        plug.id,
    )


async def energy_plug_candidates(db: AsyncSession, printer_id: int | None) -> list[SmartPlug]:
    """Plugs on *printer_id* that could supply its energy counter, best first."""
    if printer_id is None:
        # `printer_id == None` compiles to `IS NULL`, which would return every
        # plug linked to no printer at all and bill a print against whichever
        # one happened to answer. Callers are typed `int`, so this is a guard
        # against a future one rather than a live path.
        return []
    result = await db.execute(select(SmartPlug).where(SmartPlug.printer_id == printer_id))
    return sorted(result.scalars().all(), key=_rank)


async def select_energy_reading(
    candidates: list[SmartPlug],
    read_energy: EnergyReader,
    db: AsyncSession,
) -> tuple[SmartPlug, dict] | None:
    """First candidate that actually reports a lifetime counter, with its reading.

    Returns the reading alongside the plug so the caller does not poll twice --
    the value that decided the choice is the value it needs.
    """
    for plug in candidates:
        energy = await read_energy(plug, db)
        if energy and energy.get("total") is not None:
            return plug, energy
    return None
