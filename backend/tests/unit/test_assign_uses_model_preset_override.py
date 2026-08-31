"""The per-model override has to reach the slot, not just the database.

``apply_spool_to_slot_via_mqtt`` is where a spool becomes an AMS slot
configuration, and the preset it resolves is what the printer (and the slicer
reading the slot back) ends up with. These drive that function and assert on
what it handed to ``resolve_slicer_filament``, which is the last point the
preset is still a stored reference rather than a printer-side id.

The regression they guard is the whole point of the feature: a spool whose
single ``slicer_filament`` is an ``@BBL X1C`` preset, assigned on an H2C,
previously configured that H2C slot with the X1C preset.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.models.spool import Spool
from backend.app.models.spool_filament_preset import SpoolFilamentPreset

pytestmark = pytest.mark.asyncio

SPOOL_DEFAULT = "GFSA00"
SPOOL_DEFAULT_NAME = "Bambu PLA Basic @BBL X1C"


class _Nozzle:
    def __init__(self, diameter: str):
        self.nozzle_diameter = diameter
        self.nozzle_type = "HH01"


class _State:
    """Just enough live printer state for the assign path."""

    def __init__(self, diameter: str = "0.4"):
        self.nozzles = [_Nozzle(diameter), _Nozzle(diameter)]
        self.ams_extruder_map = {}
        self.kprofiles = []
        self.raw_data = {}


async def _spool(db_session) -> Spool:
    spool = Spool(
        brand="Bambu",
        material="PLA",
        color_name="Charcoal",
        slicer_filament=SPOOL_DEFAULT,
        slicer_filament_name=SPOOL_DEFAULT_NAME,
    )
    db_session.add(spool)
    await db_session.commit()
    # Loaded the way every production caller loads it: k_profiles is a lazy
    # relationship the assign path walks, and an async session cannot resolve
    # it mid-call.
    result = await db_session.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool.id))
    return result.scalar_one()


async def _assign(db_session, spool, printer, *, model: str, diameter: str = "0.4"):
    """Run the assign path far enough to capture the resolved preset.

    Returns the kwargs ``resolve_slicer_filament`` was called with. Everything
    downstream of it is stubbed: this asserts which preset was chosen, not how
    the MQTT payload is built (that is covered elsewhere).
    """
    from backend.app.api.routes import inventory as inventory_module

    resolver = AsyncMock(return_value=("GFL99", "GFSL99", "", ""))

    manager = MagicMock()
    manager.get_client = MagicMock(return_value=MagicMock())
    manager.get_status = MagicMock(return_value=_State(diameter))
    manager.get_model = MagicMock(return_value=model)

    with (
        patch.object(inventory_module, "resolve_slicer_filament", resolver),
        patch("backend.app.services.printer_manager.printer_manager", manager),
    ):
        await inventory_module.apply_spool_to_slot_via_mqtt(
            db=db_session,
            current_user=None,
            spool=spool,
            printer_id=printer.id,
            ams_id=0,
            tray_id=0,
        )

    assert resolver.await_count == 1, "the assign path did not reach the preset resolver"
    return resolver.await_args.kwargs


class TestWithoutAnOverride:
    async def test_the_spools_own_preset_is_used(self, db_session, printer_factory):
        """Every spool in every existing install. Behaviour must be unchanged."""
        printer = await printer_factory(model="H2C")
        spool = await _spool(db_session)

        kwargs = await _assign(db_session, spool, printer, model="H2C")

        assert kwargs["slicer_filament"] == SPOOL_DEFAULT
        assert kwargs["slicer_filament_name"] == SPOOL_DEFAULT_NAME


class TestWithAModelOverride:
    async def test_the_override_replaces_the_spools_preset(self, db_session, printer_factory):
        printer = await printer_factory(model="H2C")
        spool = await _spool(db_session)
        db_session.add(
            SpoolFilamentPreset(
                spool_id=spool.id,
                printer_model="H2C",
                nozzle_diameter="",
                slicer_filament="GFSA09",
                slicer_filament_name="Bambu PLA Basic @BBL H2C",
            )
        )
        await db_session.commit()

        kwargs = await _assign(db_session, spool, printer, model="H2C")

        assert kwargs["slicer_filament"] == "GFSA09"
        assert kwargs["slicer_filament_name"] == "Bambu PLA Basic @BBL H2C"

    async def test_a_different_model_still_gets_the_spools_preset(self, db_session, printer_factory):
        """An override for the H2C must not follow the spool onto the X1C."""
        printer = await printer_factory(model="X1C")
        spool = await _spool(db_session)
        db_session.add(
            SpoolFilamentPreset(
                spool_id=spool.id,
                printer_model="H2C",
                nozzle_diameter="",
                slicer_filament="GFSA09",
                slicer_filament_name="Bambu PLA Basic @BBL H2C",
            )
        )
        await db_session.commit()

        kwargs = await _assign(db_session, spool, printer, model="X1C")

        assert kwargs["slicer_filament"] == SPOOL_DEFAULT


class TestPerDiameterOverride:
    async def test_the_slots_nozzle_diameter_selects_the_preset(self, db_session, printer_factory):
        """The diameter comes from live printer state, so the same spool on
        the same model resolves differently once a 0.2 nozzle is fitted."""
        printer = await printer_factory(model="A1 mini")
        spool = await _spool(db_session)
        db_session.add_all(
            [
                SpoolFilamentPreset(
                    spool_id=spool.id,
                    printer_model="A1 mini",
                    nozzle_diameter="",
                    slicer_filament="GFSA20",
                    slicer_filament_name="Bambu PLA Basic @BBL A1M",
                ),
                SpoolFilamentPreset(
                    spool_id=spool.id,
                    printer_model="A1 mini",
                    nozzle_diameter="0.2",
                    slicer_filament="GFSA21",
                    slicer_filament_name="Bambu PLA Basic @BBL A1M 0.2 nozzle",
                ),
            ]
        )
        await db_session.commit()

        on_04 = await _assign(db_session, spool, printer, model="A1 mini", diameter="0.4")
        on_02 = await _assign(db_session, spool, printer, model="A1 mini", diameter="0.2")

        assert on_04["slicer_filament"] == "GFSA20"
        assert on_02["slicer_filament"] == "GFSA21"
