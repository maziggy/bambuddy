"""A Spoolman spool's own slicer preset is used for its slot.

The spool keeps its preset in Spoolman (extra.bambu_slicer_filament). Configure
AMS Slot and the K-profile lookup passed no fallback for a Spoolman spool, so
one without a per-model override opened with no preset and the K-profile
matched against the tray's own filament id. Read from Spoolman, remembered for
a minute per spool.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services import spool_filament_preset

URL = "/api/v1/printers/{pid}/slots/0/0/spool-defaults"

SPOOL = {
    "id": 42,
    "filament": {"id": 1, "name": "PLA Matte", "material": "PLA", "color_hex": "000000", "weight": 1000},
    "remaining_weight": 800.0,
    "used_weight": 200.0,
    "archived": False,
    "extra": {
        "bambu_slicer_filament": '"GFA01"',
        "bambu_slicer_filament_name": '"Bambu PLA Matte @BBL X1C"',
    },
}


@pytest.fixture(autouse=True)
def _empty_cache():
    spool_filament_preset._spoolman_own_presets.clear()
    yield
    spool_filament_preset._spoolman_own_presets.clear()


@pytest.fixture
async def spoolman_slot(db_session, printer_factory):
    from backend.app.models.settings import Settings
    from backend.app.models.spoolman_k_profile import SpoolmanKProfile
    from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    printer = await printer_factory(model="X1C")
    db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=0, spoolman_spool_id=42))
    db_session.add(
        SpoolmanKProfile(
            spoolman_spool_id=42, printer_id=printer.id, extruder=0, nozzle_diameter="0.4", k_value=0.02, cali_idx=7
        )
    )
    await db_session.commit()
    return printer.id


@pytest.fixture
def spoolman():
    client = MagicMock()
    client.get_spool = AsyncMock(return_value=SPOOL)
    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)):
        yield client


class _Nozzle:
    nozzle_diameter = "0.4"


@pytest.fixture
def printer_state():
    state = MagicMock(nozzles=[_Nozzle()], ams_extruder_map={"0": 0}, ams_switch_inlet=None, raw_data={})
    with patch("backend.app.api.routes.printers.printer_manager") as pm:
        pm.get_status = MagicMock(return_value=state)
        pm.get_model = MagicMock(return_value="X1C")
        yield pm


@pytest.mark.asyncio
@pytest.mark.integration
async def test_configure_slot_opens_with_the_spools_own_preset(
    async_client: AsyncClient, spoolman_slot, spoolman, printer_state
):
    body = (await async_client.get(URL.format(pid=spoolman_slot))).json()

    assert body["slicer_filament"] == "GFA01"
    assert body["slicer_filament_name"] == "Bambu PLA Matte @BBL X1C"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_k_profile_lookup_uses_the_spools_own_preset(db_session, spoolman_slot, spoolman):
    from backend.app.services.slot_kprofile import find_slot_kprofile_for_extruder

    profile = await find_slot_kprofile_for_extruder(db_session, spoolman_slot, 0, 0, 0, "0.4", "X1C", None)

    assert (profile.cali_idx, profile.filament_id) == (7, "GFA01")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_per_model_override_wins_without_asking_spoolman(db_session, spoolman_slot, spoolman):
    from backend.app.models.spool_filament_preset import SpoolmanFilamentPreset
    from backend.app.services.slot_kprofile import find_slot_kprofile_for_extruder

    db_session.add(
        SpoolmanFilamentPreset(spoolman_spool_id=42, printer_model="X1C", nozzle_diameter=None, slicer_filament="GFA05")
    )
    await db_session.commit()

    profile = await find_slot_kprofile_for_extruder(db_session, spoolman_slot, 0, 0, 0, "0.4", "X1C", None)

    assert profile.filament_id == "GFA05"
    spoolman.get_spool.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_own_preset_is_remembered_and_dropped_on_an_edit(spoolman):
    await spool_filament_preset.spoolman_spool_own_preset(42)
    await spool_filament_preset.spoolman_spool_own_preset(42)
    assert spoolman.get_spool.await_count == 1

    spool_filament_preset.forget_spoolman_spool_own_preset(42)
    await spool_filament_preset.spoolman_spool_own_preset(42)
    assert spoolman.get_spool.await_count == 2


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_override_left_blank_on_purpose_keeps_the_slot_without_a_preset(db_session, spoolman_slot, spoolman):
    from backend.app.models.spool_filament_preset import SpoolmanFilamentPreset
    from backend.app.services.slot_kprofile import find_slot_kprofile_for_extruder

    db_session.add(
        SpoolmanFilamentPreset(spoolman_spool_id=42, printer_model="X1C", nozzle_diameter=None, slicer_filament=None)
    )
    await db_session.commit()

    profile = await find_slot_kprofile_for_extruder(db_session, spoolman_slot, 0, 0, 0, "0.4", "X1C", None)

    assert profile.filament_id is None
    spoolman.get_spool.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("stored,expected", [('"GFSA01_02"', "GFA01"), ('"38"', None)])
async def test_the_k_profile_filament_id_is_the_one_the_printer_takes(
    db_session, spoolman_slot, spoolman, stored, expected
):
    """The cloud picker stores the setting-id form; a local preset is stored
    by its number, which only the slicer resolver turns into a filament id."""
    from backend.app.services.slot_kprofile import find_slot_kprofile_for_extruder

    spoolman.get_spool = AsyncMock(return_value={**SPOOL, "extra": {"bambu_slicer_filament": stored}})

    profile = await find_slot_kprofile_for_extruder(db_session, spoolman_slot, 0, 0, 0, "0.4", "X1C", None)

    assert profile.filament_id == expected
