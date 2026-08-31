"""RFID auto-assign has to pick the K profile for the hotend the slot feeds.

``auto_assign_spool`` runs unattended every time a Bambu spool is detected in a
slot. It sends no ``ams_filament_setting`` -- the firmware already has the
filament from the tag, and overwriting it turns the eye icon into a pen in
Studio -- but it does select a K profile with ``extrusion_cali_sel``.

It used to take the first stored row matching (printer, nozzle diameter) with
no extruder test at all. On a dual-nozzle printer a spool calibrated on both
hotends therefore had a coin toss decide which K value the slot got, and the
losing side prints with the other nozzle's pressure advance.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from backend.app.models.spool import Spool
from backend.app.models.spool_k_profile import SpoolKProfile
from backend.app.services.spool_tag_matcher import auto_assign_spool

pytestmark = pytest.mark.asyncio

RIGHT, LEFT = 0, 1


class _Nozzle:
    def __init__(self, diameter, nozzle_type=""):
        self.nozzle_diameter = diameter
        self.nozzle_type = nozzle_type


class _State:
    """Dual-nozzle printer, AMS 0 on the left hotend and AMS 1 on the right."""

    def __init__(self, diameters=("0.4", "0.4"), types=("", "")):
        self.nozzles = [_Nozzle(d, t) for d, t in zip(diameters, types, strict=False)]
        self.ams_extruder_map = {"0": LEFT, "1": RIGHT}
        self.ams_switch_inlet = None
        self.raw_data = {}


def _manager(state, client):
    manager = MagicMock()
    manager.get_status = MagicMock(return_value=state)
    manager.get_client = MagicMock(return_value=client)
    manager.get_model = MagicMock(return_value="H2D")
    return manager


async def _spool_with_both_hotends(engine, printer_id, diameters=("0.4", "0.4")):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        spool = Spool(brand="Bambu", material="PLA", color_name="Black", slicer_filament="GFSA00")
        db.add(spool)
        await db.commit()
        await db.refresh(spool)

        # Same spool, calibrated on both hotends -- the maintainer's H2C reads
        # 0.018 left and 0.020 right for one black PLA.
        db.add_all(
            [
                SpoolKProfile(
                    spool_id=spool.id,
                    printer_id=printer_id,
                    extruder=RIGHT,
                    nozzle_diameter=diameters[RIGHT],
                    k_value=0.020,
                    cali_idx=15,
                ),
                SpoolKProfile(
                    spool_id=spool.id,
                    printer_id=printer_id,
                    extruder=LEFT,
                    nozzle_diameter=diameters[LEFT],
                    k_value=0.018,
                    cali_idx=16,
                ),
            ]
        )
        await db.commit()
    return maker, spool.id


async def _load(maker, spool_id) -> Spool:
    async with maker() as db:
        result = await db.execute(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool_id))
        return result.scalar_one()


async def _assign(maker, spool, printer, ams_id, state):
    client = MagicMock()
    async with maker() as db:
        await auto_assign_spool(printer.id, ams_id, 0, spool, _manager(state, client), db, tray_info_idx="GFA00")
    return client


class TestWhichHotendsProfileIsSelected:
    async def test_a_slot_on_the_left_gets_the_left_profile(self, test_engine, printer_factory):
        printer = await printer_factory(model="H2D")
        maker, spool_id = await _spool_with_both_hotends(test_engine, printer.id)
        spool = await _load(maker, spool_id)

        client = await _assign(maker, spool, printer, ams_id=0, state=_State())

        client.extrusion_cali_sel.assert_called_once()
        assert client.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 16

    async def test_a_slot_on_the_right_gets_the_right_profile(self, test_engine, printer_factory):
        printer = await printer_factory(model="H2D")
        maker, spool_id = await _spool_with_both_hotends(test_engine, printer.id)
        spool = await _load(maker, spool_id)

        client = await _assign(maker, spool, printer, ams_id=1, state=_State())

        assert client.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 15

    async def test_the_slots_own_nozzle_size_decides_the_diameter(self, test_engine, printer_factory):
        """0.4 right, 0.2 left: the left slot must look up 0.2 profiles, which
        is what reading nozzles[0] for every slot got wrong."""
        printer = await printer_factory(model="H2D")
        maker, spool_id = await _spool_with_both_hotends(test_engine, printer.id, diameters=("0.4", "0.2"))
        spool = await _load(maker, spool_id)

        client = await _assign(maker, spool, printer, ams_id=0, state=_State(("0.4", "0.2")))

        kwargs = client.extrusion_cali_sel.call_args.kwargs
        assert kwargs["nozzle_diameter"] == "0.2"
        assert kwargs["cali_idx"] == 16


class TestTheFilamentIdSentWithTheSelection:
    """extrusion_cali_sel carries a filament id so the printer can link the
    calibration index to the slot. A cloud USER preset id (PFUS/PFCN) is not one
    the slicer accepts -- and a per-model override can now BE such an id, since
    picking your own cloud preset for a model stores exactly that."""

    async def test_a_cloud_user_preset_override_is_not_sent_to_the_printer(self, test_engine, printer_factory):
        from backend.app.models.spool_filament_preset import SpoolFilamentPreset

        printer = await printer_factory(model="H2D")
        maker, spool_id = await _spool_with_both_hotends(test_engine, printer.id)
        async with maker() as db:
            db.add(
                SpoolFilamentPreset(
                    spool_id=spool_id,
                    printer_model="H2D",
                    nozzle_diameter="0.4",
                    slicer_filament="PFUS279c9bd2c689d5",
                    slicer_filament_name="# Bambu PETG HF @BBL H2D 0.4 nozzle",
                )
            )
            await db.commit()
        spool = await _load(maker, spool_id)

        client = await _assign(maker, spool, printer, ams_id=0, state=_State())

        # Falls through to the spool's own preset rather than sending a value
        # the printer rejects, which would silently lose the K-profile link.
        assert client.extrusion_cali_sel.call_args.kwargs["filament_id"] == "GFSA00"

    async def test_a_normal_override_is_sent(self, test_engine, printer_factory):
        from backend.app.models.spool_filament_preset import SpoolFilamentPreset

        printer = await printer_factory(model="H2D")
        maker, spool_id = await _spool_with_both_hotends(test_engine, printer.id)
        async with maker() as db:
            db.add(
                SpoolFilamentPreset(
                    spool_id=spool_id,
                    printer_model="H2D",
                    nozzle_diameter="0.4",
                    slicer_filament="GFSG02_15",
                    slicer_filament_name="Bambu PETG HF @BBL H2D",
                )
            )
            await db.commit()
        spool = await _load(maker, spool_id)

        client = await _assign(maker, spool, printer, ams_id=0, state=_State())

        assert client.extrusion_cali_sel.call_args.kwargs["filament_id"] == "GFSG02_15"


class TestFallbacks:
    async def test_a_profile_for_the_other_hotend_is_better_than_none(self, test_engine, printer_factory):
        """An operator who calibrated one side only should still get that
        profile rather than nothing -- the fallback the old code had by
        accident, kept deliberately."""
        printer = await printer_factory(model="H2D")
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            spool = Spool(brand="Bambu", material="PLA", color_name="Black")
            db.add(spool)
            await db.commit()
            await db.refresh(spool)
            db.add(
                SpoolKProfile(
                    spool_id=spool.id,
                    printer_id=printer.id,
                    extruder=RIGHT,
                    nozzle_diameter="0.4",
                    k_value=0.020,
                    cali_idx=15,
                )
            )
            await db.commit()
        loaded = await _load(maker, spool.id)

        client = await _assign(maker, loaded, printer, ams_id=0, state=_State())

        assert client.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 15

    async def test_a_profile_for_a_different_nozzle_size_is_not_used(self, test_engine, printer_factory):
        """Diameter is not negotiable the way the hotend is: a K value measured
        on a 0.6 says nothing about a 0.4."""
        printer = await printer_factory(model="H2D")
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            spool = Spool(brand="Bambu", material="PLA", color_name="Black")
            db.add(spool)
            await db.commit()
            await db.refresh(spool)
            db.add(
                SpoolKProfile(
                    spool_id=spool.id,
                    printer_id=printer.id,
                    extruder=LEFT,
                    nozzle_diameter="0.6",
                    k_value=0.030,
                    cali_idx=20,
                )
            )
            await db.commit()
        loaded = await _load(maker, spool.id)

        client = await _assign(maker, loaded, printer, ams_id=0, state=_State())

        # No stored profile for 0.4 -- nothing is selected from the store.
        selected = [c for c in client.extrusion_cali_sel.call_args_list if c.kwargs.get("cali_idx") == 20]
        assert selected == []


class TestFlowType:
    """A K value measured through a high-flow nozzle is not a fact about a
    standard one -- the printer files them as separate calibration entries, and
    a machine can hold both for the same diameter."""

    async def _spool_with_flows(self, engine, printer_id):
        from backend.app.models.spool_k_profile import SpoolKProfile

        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            spool = Spool(brand="Bambu", material="PLA", color_name="Black", slicer_filament="GFSA00")
            db.add(spool)
            await db.commit()
            await db.refresh(spool)
            db.add_all(
                [
                    SpoolKProfile(
                        spool_id=spool.id,
                        printer_id=printer_id,
                        extruder=LEFT,
                        nozzle_diameter="0.4",
                        nozzle_type="HS",
                        k_value=0.019,
                        cali_idx=30,
                    ),
                    SpoolKProfile(
                        spool_id=spool.id,
                        printer_id=printer_id,
                        extruder=LEFT,
                        nozzle_diameter="0.4",
                        nozzle_type="HH",
                        k_value=0.026,
                        cali_idx=31,
                    ),
                ]
            )
            await db.commit()
        return maker, spool.id

    async def test_the_fitted_flow_decides_which_profile_applies(self, test_engine, printer_factory):
        printer = await printer_factory(model="H2D")
        maker, spool_id = await self._spool_with_flows(test_engine, printer.id)
        spool = await _load(maker, spool_id)

        high = await _assign(maker, spool, printer, 0, _State(types=("HH01", "HH01")))
        assert high.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 31

        spool = await _load(maker, spool_id)
        standard = await _assign(maker, spool, printer, 0, _State(types=("HS01", "HS01")))
        assert standard.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 30

    async def test_a_profile_with_no_stored_flow_still_applies(self, test_engine, printer_factory):
        """Every profile saved before flow was recorded has none, so a strict
        comparison would stop applying all of them at once."""
        printer = await printer_factory(model="H2D")
        maker, spool_id = await _spool_with_both_hotends(test_engine, printer.id)
        spool = await _load(maker, spool_id)

        client = await _assign(maker, spool, printer, 0, _State(types=("HH01", "HH01")))

        assert client.extrusion_cali_sel.call_args.kwargs["cali_idx"] == 16

    async def test_a_printer_that_declares_no_flow_applies_everything(self, test_engine, printer_factory):
        """The X1C case, measured: it answers with nozzle_id '' on every
        profile, so filtering on an invented Standard would drop the lot."""
        printer = await printer_factory(model="H2D")
        maker, spool_id = await self._spool_with_flows(test_engine, printer.id)
        spool = await _load(maker, spool_id)

        client = await _assign(maker, spool, printer, 0, _State(types=("", "")))

        # Nothing is excluded, so the first stored row wins as it always did.
        assert client.extrusion_cali_sel.call_args.kwargs["cali_idx"] in (30, 31)
