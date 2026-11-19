"""GET /api/v1/printers/inventory-filaments (#3590)."""

import pytest
from httpx import AsyncClient

from backend.app.core.auth import generate_api_key
from backend.app.models.api_key import APIKey
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


async def test_works_with_the_override_setting_off(async_client: AsyncClient, db_session):
    """The endpoint does not depend on `filament_override_inventory` (#3590)."""
    db_session.add_all(
        [
            Settings(key="filament_override_inventory", value="false"),
            Spool(material="PLA", rgba="FF0000FF", label_weight=1000, weight_used=0),
        ]
    )
    await db_session.commit()
    body = (await async_client.get(URL)).json()
    assert body["inventory_available"] is True
    assert [f["type"] for f in body["filaments"]] == ["PLA"]


async def test_spoolman_mode(async_client: AsyncClient, db_session):
    db_session.add_all([Settings(key="spoolman_enabled", value="true"), Spool(material="PLA", rgba="FF0000FF")])
    await db_session.commit()
    assert (await async_client.get(URL)).json() == {"inventory_available": False, "filaments": []}


async def test_requires_queue_create_and_inventory_read(async_client: AsyncClient, db_session):
    """It is the inventory's stock: queue:create alone must not read it, setting on or off (#3590)."""
    db_session.add_all(
        [
            Settings(key="filament_override_inventory", value="false"),
            Spool(material="PLA", rgba="FF0000FF", label_weight=1000, weight_used=0),
        ]
    )
    await db_session.commit()
    admin = await _admin_token(async_client)

    async def status_for(name: str, permissions: list[str]) -> int:
        token, _ = await _user(
            async_client, admin, name, [await _group(async_client, admin, f"g-{name}", permissions=permissions)]
        )
        return (await async_client.get(URL, headers=_auth(token))).status_code

    assert await status_for("both", ["queue:create", "inventory:read"]) == 200
    assert await status_for("queue", ["queue:create"]) == 403
    assert await status_for("stock", ["inventory:read"]) == 403
    assert await status_for("neither", ["printers:read"]) == 403
    assert (await async_client.get(URL)).status_code == 401


@pytest.mark.parametrize(
    ("can_queue", "can_read_status", "expected"),
    [(True, True, 200), (True, False, 403), (False, True, 403)],
)
async def test_api_key_needs_queue_and_read_status(
    async_client: AsyncClient, db_session, can_queue: bool, can_read_status: bool, expected: int
):
    """An API key reads the stock only with both Queue and Read Status (#3590)."""
    full_key, key_hash, key_prefix = generate_api_key()
    db_session.add_all(
        [
            Settings(key="auth_enabled", value="true"),
            APIKey(
                name="k",
                key_hash=key_hash,
                key_prefix=key_prefix,
                can_queue=can_queue,
                can_read_status=can_read_status,
                enabled=True,
            ),
        ]
    )
    await db_session.commit()

    assert (await async_client.get(URL, headers={"X-API-Key": full_key})).status_code == expected
    assert (await async_client.get(URL, headers=_auth(full_key))).status_code == expected
