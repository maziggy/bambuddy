"""GET /api/v1/printers/inventory-filaments (#3590)."""

import pytest
from httpx import AsyncClient

from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

URL = "/api/v1/printers/inventory-filaments"


async def test_response_shape(async_client: AsyncClient, db_session):
    db_session.add_all(
        [
            Spool(
                material="PLA",
                subtype="Matte",
                color_name="Red",
                rgba="FF0000FF",
                label_weight=1000,
                weight_used=100,
                slicer_filament="GFA01",
            ),
            Spool(material="PETG", rgba="00FF00FF", label_weight=1000, weight_used=0),
        ]
    )
    await db_session.commit()

    body = (await async_client.get(URL)).json()
    assert body["inventory_available"] is True
    assert len(body["filaments"]) == 2
    pla = next(f for f in body["filaments"] if f["type"] == "PLA")
    assert pla == {
        "type": "PLA",
        "color": "#FF0000",
        "tray_info_idx": "",
        "subtype": "Matte",
        "color_name": "Red",
        "rgba": "FF0000FF",
        "extra_colors": None,
        "effect_type": None,
        "spool_count": 1,
        "remaining_grams": 900.0,
    }


async def test_spoolman_mode(async_client: AsyncClient, db_session):
    db_session.add_all([Settings(key="spoolman_enabled", value="true"), Spool(material="PLA", rgba="FF0000FF")])
    await db_session.commit()
    assert (await async_client.get(URL)).json() == {"inventory_available": False, "filaments": []}


async def test_requires_queue_create(async_client: AsyncClient):
    admin = await _admin_token(async_client)
    allowed, _ = await _user(
        async_client, admin, "q", [await _group(async_client, admin, "g1", permissions=["queue:create"])]
    )
    denied, _ = await _user(
        async_client, admin, "n", [await _group(async_client, admin, "g2", permissions=["printers:read"])]
    )

    assert (await async_client.get(URL, headers=_auth(allowed))).status_code == 200
    assert (await async_client.get(URL, headers=_auth(denied))).status_code == 403
    assert (await async_client.get(URL)).status_code == 401
