"""A spool with a brand and no subtype must not send the string "None" (#2987).

``tray_sub_brands`` was built by interpolating brand, material and subtype into
one f-string whenever the spool had a brand -- without checking that the subtype
existed. The unbranded branch guarded it; the branded one did not. The
reporter's Sunlu roll has no subtype, so the slot was configured as

    "tray_sub_brands": "Sunlu PLA Matte None"

which is what the printer stored and what Bambu Studio then displayed.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.printer import Printer
from backend.app.models.spool import Spool


def _mqtt_mock():
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


async def _assign(async_client, db_session, serial, **spool_kwargs):
    printer = Printer(name="P1S", serial_number=serial, ip_address="192.168.1.78", access_code="12345678")
    db_session.add(printer)
    spool = Spool(rgba="09ff00ff", label_weight=1000, weight_used=0, **spool_kwargs)
    db_session.add(spool)
    await db_session.commit()
    await db_session.refresh(printer)
    await db_session.refresh(spool)

    client = _mqtt_mock()
    with patch("backend.app.services.printer_manager.printer_manager") as pm:
        pm.get_client.return_value = client
        pm.get_status.return_value = _status()
        response = await async_client.post(
            "/api/v1/inventory/assignments",
            json={"spool_id": spool.id, "printer_id": printer.id, "ams_id": 0, "tray_id": 2},
        )
    assert response.status_code == 200
    client.ams_set_filament_setting.assert_called_once()
    return client.ams_set_filament_setting.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_branded_spool_without_a_subtype_sends_no_none(async_client: AsyncClient, db_session: AsyncSession):
    """The reported case."""
    sent = await _assign(async_client, db_session, "SB2987A", brand="Sunlu", material="PLA Matte", subtype=None)

    assert sent["tray_sub_brands"] == "Sunlu PLA Matte"
    assert "None" not in sent["tray_sub_brands"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_branded_spool_with_a_subtype_still_carries_all_three(
    async_client: AsyncClient, db_session: AsyncSession
):
    """The case that already worked, pinned so the fix does not drop the subtype."""
    sent = await _assign(async_client, db_session, "SB2987B", brand="Sunlu", material="PLA", subtype="Silk")

    assert sent["tray_sub_brands"] == "Sunlu PLA Silk"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_unbranded_spool_with_a_subtype_is_unchanged(async_client: AsyncClient, db_session: AsyncSession):
    sent = await _assign(async_client, db_session, "SB2987C", brand=None, material="PLA", subtype="Silk")

    assert sent["tray_sub_brands"] == "PLA Silk"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_spool_with_only_a_material_is_unchanged(async_client: AsyncClient, db_session: AsyncSession):
    sent = await _assign(async_client, db_session, "SB2987D", brand=None, material="PLA", subtype=None)

    assert sent["tray_sub_brands"] == "PLA"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_empty_string_subtype_is_treated_as_absent(async_client: AsyncClient, db_session: AsyncSession):
    """The old branded branch stripped a trailing space away, so "" already
    behaved; None was the only broken input and both must stay correct."""
    sent = await _assign(async_client, db_session, "SB2987E", brand="Sunlu", material="PLA Matte", subtype="")

    assert sent["tray_sub_brands"] == "Sunlu PLA Matte"
