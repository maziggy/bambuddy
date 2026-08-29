"""The per-printer-model preset cascade.

``Spool.slicer_filament`` holds one preset and is printer-agnostic by design.
That breaks as soon as a spool is used on two printer models, because a cloud
or Orca preset is bound to a model (``@BBL X1C``): the AMS slot on an H2C gets
configured with a preset that machine has no profile for.

``services.spool_filament_preset`` resolves, most specific first:

    (model, diameter) -> (model, "") -> the spool's own slicer_filament

These pin the resolution order itself, including the two cases that are easy
to get backwards: a spool with no overrides must behave exactly as it did
before the feature existed, and a stored row whose preset is blank is the
user clearing the preset for that model, not a hole to fall through.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.spool import Spool
from backend.app.models.spool_filament_preset import SpoolFilamentPreset, SpoolmanFilamentPreset
from backend.app.services.spool_filament_preset import resolve_spool_preset, resolve_spoolman_preset

pytestmark = pytest.mark.asyncio

DEFAULT = ("GFSA00", "Bambu PLA Basic @BBL X1C")


async def _spool(engine) -> tuple[async_sessionmaker, int]:
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        spool = Spool(
            brand="Bambu",
            material="PLA",
            color_name="Charcoal",
            slicer_filament=DEFAULT[0],
            slicer_filament_name=DEFAULT[1],
        )
        db.add(spool)
        await db.commit()
        await db.refresh(spool)
        return maker, spool.id


async def _resolve(maker, spool_id, model, diameter):
    async with maker() as db:
        return await resolve_spool_preset(
            db,
            spool_id=spool_id,
            printer_model=model,
            nozzle_diameter=diameter,
            fallback_filament=DEFAULT[0],
            fallback_name=DEFAULT[1],
        )


async def _add(maker, spool_id, model, diameter, code, name):
    async with maker() as db:
        db.add(
            SpoolFilamentPreset(
                spool_id=spool_id,
                printer_model=model,
                nozzle_diameter=diameter,
                slicer_filament=code,
                slicer_filament_name=name,
            )
        )
        await db.commit()


class TestNoOverrides:
    """Every spool in every existing install is this case."""

    async def test_falls_back_to_the_spools_own_preset(self, test_engine):
        maker, spool_id = await _spool(test_engine)
        assert await _resolve(maker, spool_id, "H2C", "0.4") == DEFAULT

    async def test_an_unknown_model_falls_back(self, test_engine):
        maker, spool_id = await _spool(test_engine)
        await _add(maker, spool_id, "X1C", "", "GFSA01", "PLA @X1C")
        assert await _resolve(maker, spool_id, "P1S", "0.4") == DEFAULT

    async def test_no_model_at_all_falls_back(self, test_engine):
        """A printer that has not reported its model yet cannot be more
        specific than the spool itself -- it must not match some other row."""
        maker, spool_id = await _spool(test_engine)
        await _add(maker, spool_id, "X1C", "", "GFSA01", "PLA @X1C")
        assert await _resolve(maker, spool_id, None, "0.4") == DEFAULT
        assert await _resolve(maker, spool_id, "", "0.4") == DEFAULT


class TestModelDefault:
    """Diameter "" = any nozzle of the model.

    The spool form writes one row per nozzle size and never this one, but the
    API accepts it and it has to keep resolving -- these pin that level of the
    cascade so a client using it does not break silently.
    """

    async def test_model_row_wins_over_the_spool_default(self, test_engine):
        maker, spool_id = await _spool(test_engine)
        await _add(maker, spool_id, "H2C", "", "GFSA09", "Bambu PLA Basic @BBL H2C")
        assert await _resolve(maker, spool_id, "H2C", "0.4") == ("GFSA09", "Bambu PLA Basic @BBL H2C")

    async def test_it_applies_to_every_nozzle_of_that_model(self, test_engine):
        maker, spool_id = await _spool(test_engine)
        await _add(maker, spool_id, "H2C", "", "GFSA09", "Bambu PLA Basic @BBL H2C")
        for diameter in ("0.2", "0.4", "0.6", "0.8", ""):
            assert (await _resolve(maker, spool_id, "H2C", diameter))[0] == "GFSA09", diameter

    async def test_other_models_are_untouched(self, test_engine):
        maker, spool_id = await _spool(test_engine)
        await _add(maker, spool_id, "H2C", "", "GFSA09", "Bambu PLA Basic @BBL H2C")
        await _add(maker, spool_id, "A1 mini", "", "GFSA20", "Bambu PLA Basic @BBL A1M")
        assert (await _resolve(maker, spool_id, "H2C", "0.4"))[0] == "GFSA09"
        assert (await _resolve(maker, spool_id, "A1 mini", "0.4"))[0] == "GFSA20"
        assert (await _resolve(maker, spool_id, "X1C", "0.4"))[0] == DEFAULT[0]


class TestPerDiameterException:
    """Why diameter is in the key at all: the preset lands on an AMS slot and
    a slot feeds exactly one nozzle, so a machine with two diameters fitted
    needs two answers for one model."""

    async def test_exact_diameter_beats_the_model_default(self, test_engine):
        maker, spool_id = await _spool(test_engine)
        await _add(maker, spool_id, "A1 mini", "", "GFSA20", "Bambu PLA Basic @BBL A1M")
        await _add(maker, spool_id, "A1 mini", "0.2", "GFSA21", "Bambu PLA Basic @BBL A1M 0.2 nozzle")

        assert (await _resolve(maker, spool_id, "A1 mini", "0.2"))[0] == "GFSA21"
        assert (await _resolve(maker, spool_id, "A1 mini", "0.4"))[0] == "GFSA20"

    async def test_a_diameter_row_alone_still_leaves_other_nozzles_on_the_default(self, test_engine):
        maker, spool_id = await _spool(test_engine)
        await _add(maker, spool_id, "A1 mini", "0.2", "GFSA21", "Bambu PLA Basic @BBL A1M 0.2 nozzle")

        assert (await _resolve(maker, spool_id, "A1 mini", "0.2"))[0] == "GFSA21"
        assert (await _resolve(maker, spool_id, "A1 mini", "0.4"))[0] == DEFAULT[0]

    async def test_the_two_hotends_of_one_machine_resolve_differently(self, test_engine):
        """The case that put diameter in the key: 0.4 on one hotend, 0.2 on
        the other, one model, one spool."""
        maker, spool_id = await _spool(test_engine)
        await _add(maker, spool_id, "H2C", "0.4", "GFSA09", "Bambu PLA Basic @BBL H2C")
        await _add(maker, spool_id, "H2C", "0.2", "GFSA10", "Bambu PLA Basic @BBL H2C 0.2 nozzle")

        assert (await _resolve(maker, spool_id, "H2C", "0.4"))[0] == "GFSA09"
        assert (await _resolve(maker, spool_id, "H2C", "0.2"))[0] == "GFSA10"


class TestClearedPreset:
    async def test_a_blank_row_means_none_not_fall_through(self, test_engine):
        """The user picking the empty entry for a model is a decision. Falling
        back to the spool's preset would silently reinstate what they cleared,
        and it is the spool's preset that is wrong on that model."""
        maker, spool_id = await _spool(test_engine)
        await _add(maker, spool_id, "H2C", "", None, None)
        assert await _resolve(maker, spool_id, "H2C", "0.4") == (None, None)


class TestSpoolmanFlavour:
    """Spoolman spools live in Spoolman; the override is Bambuddy's, keyed by
    the remote id. Same cascade -- the two inventory modes must not drift."""

    async def test_same_cascade(self, test_engine):
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            db.add(
                SpoolmanFilamentPreset(
                    spoolman_spool_id=7,
                    printer_model="H2C",
                    nozzle_diameter="",
                    slicer_filament="GFSA09",
                    slicer_filament_name="Bambu PLA Basic @BBL H2C",
                )
            )
            db.add(
                SpoolmanFilamentPreset(
                    spoolman_spool_id=7,
                    printer_model="H2C",
                    nozzle_diameter="0.2",
                    slicer_filament="GFSA10",
                    slicer_filament_name="Bambu PLA Basic @BBL H2C 0.2 nozzle",
                )
            )
            await db.commit()

        async def resolve(model, diameter, spool_id=7):
            async with maker() as db:
                return await resolve_spoolman_preset(
                    db,
                    spoolman_spool_id=spool_id,
                    printer_model=model,
                    nozzle_diameter=diameter,
                    fallback_filament=DEFAULT[0],
                    fallback_name=DEFAULT[1],
                )

        assert (await resolve("H2C", "0.4"))[0] == "GFSA09"
        assert (await resolve("H2C", "0.2"))[0] == "GFSA10"
        assert (await resolve("X1C", "0.4"))[0] == DEFAULT[0]
        # Another spool's rows must not leak into this one.
        assert (await resolve("H2C", "0.4", spool_id=8))[0] == DEFAULT[0]
