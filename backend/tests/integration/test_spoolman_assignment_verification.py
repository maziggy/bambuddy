"""A Spoolman slot assignment is checked against the printer's read-back (#2582).

The built-in assign registers what it pushed, so the AMS pushes that follow
can confirm the tray took it and a "not confirmed" notice appears when it did
not. The Spoolman assign and link published the same commands and never
registered anything: a printer that ignored them left the slot on its old
filament with no notice, and usage went to the wrong spool.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

SPOOL = {
    "id": 10,
    "filament": {"id": 1, "name": "PLA Basic", "material": "PLA", "color_hex": "FF0000", "weight": 1000},
    "remaining_weight": 800.0,
    "used_weight": 200.0,
    "archived": False,
    "extra": {},
}


@pytest.fixture
async def printer_id(db_session, printer_factory):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()
    return (await printer_factory()).id


def _spoolman_client():
    client = MagicMock()
    client.base_url = "http://localhost:7912"
    client.health_check = AsyncMock(return_value=True)
    client.get_spool = AsyncMock(return_value=SPOOL)
    client.get_spools = AsyncMock(return_value=[])
    client.merge_spool_extra = AsyncMock(return_value={"id": 10, "extra": {}})
    client.get_distinct_locations = AsyncMock(return_value=[])
    client.has_tag_api = AsyncMock(return_value=False)
    client.add_native_tags = AsyncMock(return_value=0)
    client.ensure_tag_extra_field = AsyncMock(return_value=True)
    client.ensure_extra_field = AsyncMock(return_value=True)
    return client


def _printer_client():
    mqtt = MagicMock()
    mqtt.ams_set_filament_setting = MagicMock()
    mqtt.extrusion_cali_sel = MagicMock()
    mqtt.register_assignment_verification = MagicMock()
    return mqtt


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_assignment_is_registered_for_read_back(async_client: AsyncClient, printer_id):
    mqtt = _printer_client()
    with (
        patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=_spoolman_client())),
        patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm,
    ):
        pm.get_client = MagicMock(return_value=mqtt)
        pm.get_status = MagicMock(return_value=None)
        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={"spoolman_spool_id": 10, "printer_id": printer_id, "ams_id": 0, "tray_id": 1},
        )

    assert response.status_code == 200
    pushed = mqtt.ams_set_filament_setting.call_args.kwargs
    mqtt.register_assignment_verification.assert_called_once_with(
        ams_id=0, tray_id=1, tray_info_idx=pushed["tray_info_idx"], tray_color=pushed["tray_color"], cali_idx=-1
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_link_with_a_slot_is_registered_for_read_back(async_client: AsyncClient, printer_id):
    mqtt = _printer_client()
    with (
        patch("backend.app.api.routes.spoolman.get_spoolman_client", AsyncMock(return_value=_spoolman_client())),
        patch("backend.app.api.routes.spoolman.printer_manager") as pm,
    ):
        pm.get_client = MagicMock(return_value=mqtt)
        pm.get_status = MagicMock(return_value=None)
        pm.get_model = MagicMock(return_value="X1C")
        response = await async_client.post(
            "/api/v1/spoolman/spools/10/link",
            json={"tray_uuid": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4", "printer_id": printer_id, "ams_id": 0, "tray_id": 2},
        )

    assert response.status_code == 200, response.text
    pushed = mqtt.ams_set_filament_setting.call_args.kwargs
    # No stored K-profile and none live: the link leaves the K-profile alone,
    # so only the filament is checked.
    mqtt.register_assignment_verification.assert_called_once_with(
        ams_id=0, tray_id=2, tray_info_idx=pushed["tray_info_idx"], tray_color=pushed["tray_color"], cali_idx=None
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_assignment_to_an_empty_slot_is_not_checked(async_client: AsyncClient, printer_id):
    """An empty slot echoes nothing back until a spool goes in, so a check
    could only time out into a false "not confirmed"."""
    from types import SimpleNamespace

    mqtt = _printer_client()
    state = SimpleNamespace(
        raw_data={"ams": [{"id": 0, "tray": [{"id": 1, "state": 9}]}]},
        nozzles=[],
        ams_extruder_map={},
        ams_switch_inlet=None,
        kprofiles=[],
    )
    with (
        patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=_spoolman_client())),
        patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm,
    ):
        pm.get_client = MagicMock(return_value=mqtt)
        pm.get_status = MagicMock(return_value=state)
        response = await async_client.post(
            "/api/v1/spoolman/inventory/slot-assignments",
            json={"spoolman_spool_id": 10, "printer_id": printer_id, "ams_id": 0, "tray_id": 1},
        )

    assert response.status_code == 200
    mqtt.ams_set_filament_setting.assert_called_once()
    mqtt.register_assignment_verification.assert_not_called()
