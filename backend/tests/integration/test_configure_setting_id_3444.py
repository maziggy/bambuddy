"""A Bambu setting id sent to Configure as the filament id (#3444).

The reporter's iOS client called ``/slots/{ams}/{tray}/configure`` with a
spool's ``slicer_filament`` as ``tray_info_idx``: "GFSL99", a setting id. The
route passed it on, the P1S reported the slot blank and auto-unlink dropped the
spool. The assignment path converts it (normalize_slicer_filament); Configure
now does the same, with the #3387 helper.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


def _client():
    client = MagicMock()
    client.ams_set_filament_setting.return_value = True
    client.extrusion_cali_sel.return_value = True
    return client


def _status():
    status = MagicMock()
    status.raw_data = {"ams": {"ams": []}}
    status.nozzles = [MagicMock(nozzle_diameter="0.4")]
    status.ams_extruder_map = None
    status.kprofiles = []
    return status


async def _configure(async_client, printer_id, **params):
    client = _client()
    with patch("backend.app.api.routes.printers.printer_manager") as pm:
        pm.get_client.return_value = client
        pm.get_status.return_value = _status()
        response = await async_client.post(
            f"/api/v1/printers/{printer_id}/slots/0/0/configure",
            params={
                "tray_type": "PLA",
                "tray_sub_brands": "Generic PLA",
                "tray_color": "000000FF",
                "nozzle_temp_min": 190,
                "nozzle_temp_max": 250,
                **params,
            },
        )
    assert response.status_code == 200, response.text
    return client


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("sent", "filament_id", "setting_id"),
    [
        ("GFSL99", "GFL99", "GFSL99"),  # the report
        ("GFSL99_02", "GFL99", "GFSL99_02"),  # with its version
        ("GFSA05_10", "GFA01", "GFSA05_10"),  # Bambu gives this one Matte's id (#3387)
        ("GFSNLS03", "GFSNL03", "GFSNLS03"),  # SUNLU
    ],
)
async def test_a_setting_id_is_sent_as_its_filament_id(
    async_client: AsyncClient, printer_factory, sent, filament_id, setting_id
):
    printer = await printer_factory(model="P1S")
    client = await _configure(async_client, printer.id, tray_info_idx=sent)
    slot = client.ams_set_filament_setting.call_args.kwargs
    assert slot["tray_info_idx"] == filament_id
    assert slot["setting_id"] == setting_id
    assert client.extrusion_cali_sel.call_args.kwargs["filament_id"] == filament_id


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("sent", ["GFL99", "GFS99", "GFSNL03", "P4d64437"])
async def test_filament_ids_are_left_alone(async_client: AsyncClient, printer_factory, sent):
    """Support ids ("GFS99") and SUNLU's ("GFSNL03") start with GFS but are filament ids."""
    printer = await printer_factory(model="P1S")
    client = await _configure(async_client, printer.id, tray_info_idx=sent)
    assert client.ams_set_filament_setting.call_args.kwargs["tray_info_idx"] == sent


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_clients_own_setting_id_is_kept(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(model="P1S")
    client = await _configure(async_client, printer.id, tray_info_idx="GFSL99", setting_id="GFSL99_01")
    slot = client.ams_set_filament_setting.call_args.kwargs
    assert (slot["tray_info_idx"], slot["setting_id"]) == ("GFL99", "GFSL99_01")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_k_profile_setting_id_is_converted_too(async_client: AsyncClient, printer_factory):
    """The K profile's filament id becomes the slot's when they differ, and the
    calibration table is keyed by filament ids."""
    printer = await printer_factory(model="P1S")
    client = await _configure(
        async_client, printer.id, tray_info_idx="GFL99", cali_idx=3, kprofile_filament_id="GFSL99_02"
    )
    slot = client.ams_set_filament_setting.call_args.kwargs
    assert slot["tray_info_idx"] == "GFL99"
    assert client.extrusion_cali_sel.call_args.kwargs["filament_id"] == "GFL99"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_k_profile_for_another_preset_realigns_the_slot_with_its_filament_id(
    async_client: AsyncClient, printer_factory
):
    """Realigned to the K profile, the slot gets its filament id, and its setting id as setting_id."""
    printer = await printer_factory(model="P1S")
    client = await _configure(
        async_client, printer.id, tray_info_idx="GFL99", cali_idx=3, kprofile_filament_id="GFSL05_07"
    )
    slot = client.ams_set_filament_setting.call_args.kwargs
    assert (slot["tray_info_idx"], slot["setting_id"]) == ("GFL05", "GFSL05_07")
    assert client.extrusion_cali_sel.call_args.kwargs["filament_id"] == "GFL05"
