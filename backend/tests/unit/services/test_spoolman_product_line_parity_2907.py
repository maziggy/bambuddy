"""The same Bambu roll must come out the same in both inventory modes (#2907).

The earlier tests for this fix staged a catalogue value the seed does not
contain ("Matte Charcoal" for PLA Matte at #000000, where ``catalog_defaults.py``
has "Charcoal") and so ran a branch the shipped catalogue never reaches. These
seed the real ``DEFAULT_COLOR_CATALOG`` and offer the real SpoolmanDB entries
for Bambu Lab PLA at #000000, as a live 0.26.1 instance returns them. The three
densities differ, which is what shows which library entry was taken.

Each roll then goes through both modes: ``create_spool_from_tray`` for the
built-in inventory, and ``sync_ams_tray`` plus ``_map_spoolman_spool`` for
Spoolman. Material, subtype and colour name have to agree.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool
from backend.app.core.catalog_defaults import DEFAULT_COLOR_CATALOG
from backend.app.models.color_catalog import ColorCatalogEntry
from backend.app.services.spool_tag_matcher import create_spool_from_tray
from backend.app.services.spoolman import AMSTray, SpoolmanClient

# From GET /api/v1/external/filament on Spoolman 0.26.1, trimmed to the fields
# the selector and create_filament read.
LIBRARY = [
    {
        "id": "bambulab_pla_black_1000_175_n",
        "manufacturer": "Bambu Lab",
        "name": "Black",
        "material": "PLA",
        "color_hex": "000000",
        "density": 1.24,
        "weight": 1000.0,
    },
    {
        "id": "bambulab_pla_mattecharcoal_1000_175_n",
        "manufacturer": "Bambu Lab",
        "name": "Matte Charcoal",
        "material": "PLA",
        "color_hex": "000000",
        "density": 1.31,
        "weight": 1000.0,
    },
    {
        "id": "bambulab_pla_tough+black_1000_175_n",
        "manufacturer": "Bambu Lab",
        "name": "Tough+ Black",
        "material": "PLA",
        "color_hex": "000000",
        "density": 1.21,
        "weight": 1000.0,
    },
]

ROLLS = [
    # sub-brand, the library entry that is this roll's, subtype, colour name
    ("PLA Basic", 1.24, "Basic", "Black"),
    ("PLA Matte", 1.31, "Matte", "Charcoal"),
    ("PLA Tough", 1.21, "Tough", "Black"),
]


async def _seed_the_shipped_catalogue(db) -> None:
    for manufacturer, color_name, hex_color, material in DEFAULT_COLOR_CATALOG:
        db.add(
            ColorCatalogEntry(manufacturer=manufacturer, color_name=color_name, hex_color=hex_color, material=material)
        )
    await db.commit()


def _tray(sub_brand: str) -> AMSTray:
    return AMSTray(
        ams_id=0,
        tray_id=0,
        tray_type="PLA",
        tray_sub_brands=sub_brand,
        tray_color="000000FF",
        remain=100,
        tag_uid="",
        tray_uuid="A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4",
        tray_info_idx="GFA00",
        tray_weight=1000,
    )


async def _through_spoolman(db, sub_brand: str) -> tuple[dict, dict]:
    """What Spoolman mode creates for this roll, and how the inventory then reads it."""
    client = SpoolmanClient("http://localhost:7912")
    with (
        patch.object(client, "find_spool_by_tag", AsyncMock(return_value=None)),
        patch.object(client, "ensure_bambu_vendor", AsyncMock(return_value=2)),
        patch.object(client, "get_filaments", AsyncMock(return_value=[])),
        patch.object(client, "get_external_filaments", AsyncMock(return_value=LIBRARY)),
        patch.object(client, "create_filament", AsyncMock(return_value={"id": 9})) as create_filament,
        patch.object(client, "create_spool", AsyncMock(return_value={"id": 99})) as create_spool,
    ):
        await client.sync_ams_tray(_tray(sub_brand), "TestPrinter", db)

    filament_kwargs = create_filament.call_args.kwargs
    stored = {
        "id": 99,
        "filament": {
            "id": 9,
            "name": filament_kwargs["name"],
            "material": filament_kwargs["material"],
            "color_hex": filament_kwargs["color_hex"],
            "vendor": {"id": 2, "name": "Bambu Lab"},
        },
        "initial_weight": 1000.0,
        "used_weight": 0.0,
        "remaining_weight": 1000.0,
        "extra": create_spool.call_args.kwargs["extra"],
    }
    return filament_kwargs, _map_spoolman_spool(stored)


@pytest.mark.asyncio
@pytest.mark.parametrize(("sub_brand", "density", "subtype", "color_name"), ROLLS)
async def test_the_roll_takes_its_own_library_entry(db_session, sub_brand, density, subtype, color_name):
    """Before round three: Basic took "Black", Matte reached no entry at all and
    lost the library's density, and Tough took PLA Basic's "Black"."""
    await _seed_the_shipped_catalogue(db_session)

    filament_kwargs, _ = await _through_spoolman(db_session, sub_brand)

    assert filament_kwargs.get("density") == density


@pytest.mark.asyncio
@pytest.mark.parametrize(("sub_brand", "density", "subtype", "color_name"), ROLLS)
async def test_both_modes_store_the_roll_the_same_way(db_session, sub_brand, density, subtype, color_name):
    """The review's table, as an assertion. Round two had Spoolman mode put the
    colour in the filament name, so its subtype read "Charcoal" where the
    built-in inventory has "Matte"."""
    await _seed_the_shipped_catalogue(db_session)

    internal = await create_spool_from_tray(
        db_session,
        {
            "tray_type": "PLA",
            "tray_sub_brands": sub_brand,
            "tray_color": "000000FF",
            "tray_uuid": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4",
            "tray_info_idx": "GFA00",
            "tray_weight": 1000,
        },
    )
    _, spoolman = await _through_spoolman(db_session, sub_brand)

    assert (internal.material, internal.subtype, internal.color_name) == ("PLA", subtype, color_name)
    assert (spoolman["material"], spoolman["subtype"], spoolman["color_name"]) == ("PLA", subtype, color_name)
