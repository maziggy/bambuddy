"""Home Assistant printer sensors keep to the caller's printers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.ha_sensor_manager import ha_sensor_manager
from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

PERMS = ["printers:read", "smart_plugs:read", "smart_plugs:create", "smart_plugs:update", "smart_plugs:delete"]


@pytest.fixture(autouse=True)
def _no_live_ha():
    with patch.object(ha_sensor_manager, "refresh_one", AsyncMock()):
        yield


def _door(printer_id: int, entity: str = "binary_sensor.door") -> dict:
    return {"name": "Door", "entity_id": entity, "kind": "binary", "alert_state": "on", "printer_id": printer_id}


async def _setup(client: AsyncClient, printer_factory):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    token = await _admin_token(client)
    admin = _auth(token)
    perms = await _group(client, token, "perms", permissions=PERMS)
    team = await _group(client, token, "team", printer_ids=[a.id])
    member = _auth((await _user(client, token, "member", [perms, team]))[0])
    ids = {}
    for name, printer in (("ours", a.id), ("theirs", b.id)):
        created = await client.post("/api/v1/ha-sensors/", headers=admin, json=_door(printer))
        assert created.status_code == 200, created.text
        ids[name] = created.json()["id"]
    return a.id, b.id, admin, member, ids


async def test_a_member_lists_and_reads_only_sensors_of_their_printers(async_client: AsyncClient, printer_factory):
    a_id, b_id, admin, member, ids = await _setup(async_client, printer_factory)

    listed = await async_client.get("/api/v1/ha-sensors/", headers=member)
    filtered = await async_client.get("/api/v1/ha-sensors/", headers=member, params={"printer_id": b_id})
    ours = await async_client.get(f"/api/v1/ha-sensors/{ids['ours']}", headers=member)
    theirs = await async_client.get(f"/api/v1/ha-sensors/{ids['theirs']}", headers=member)
    missing = await async_client.get("/api/v1/ha-sensors/999999", headers=member)
    everything = await async_client.get("/api/v1/ha-sensors/", headers=admin)

    assert [s["id"] for s in listed.json()] == [ids["ours"]]
    assert filtered.json() == []
    assert ours.status_code == 200
    assert (
        (theirs.status_code, theirs.json())
        == (missing.status_code, missing.json())
        == (
            404,
            {"detail": "Sensor not found"},
        )
    )
    assert len(everything.json()) == 2


async def test_a_member_changes_only_sensors_of_their_printers(async_client: AsyncClient, printer_factory):
    a_id, b_id, admin, member, ids = await _setup(async_client, printer_factory)

    patched_theirs = await async_client.patch(
        f"/api/v1/ha-sensors/{ids['theirs']}", headers=member, json={"block_print": True}
    )
    deleted_theirs = await async_client.delete(f"/api/v1/ha-sensors/{ids['theirs']}", headers=member)
    patched_ours = await async_client.patch(f"/api/v1/ha-sensors/{ids['ours']}", headers=member, json={"name": "Lid"})
    deleted_ours = await async_client.delete(f"/api/v1/ha-sensors/{ids['ours']}", headers=member)

    assert (patched_theirs.status_code, deleted_theirs.status_code) == (404, 404)
    assert (patched_ours.status_code, deleted_ours.status_code) == (200, 200)
    left = (await async_client.get(f"/api/v1/ha-sensors/{ids['theirs']}", headers=admin)).json()
    assert left["block_print"] is False


async def test_a_member_binds_sensors_to_their_printers_only(async_client: AsyncClient, printer_factory):
    a_id, b_id, admin, member, ids = await _setup(async_client, printer_factory)

    theirs = await async_client.post("/api/v1/ha-sensors/", headers=member, json=_door(b_id, "binary_sensor.lid"))
    missing = await async_client.post("/api/v1/ha-sensors/", headers=member, json=_door(999_999, "binary_sensor.lid"))
    ours = await async_client.post("/api/v1/ha-sensors/", headers=member, json=_door(a_id, "binary_sensor.lid"))

    assert (
        (theirs.status_code, theirs.json())
        == (missing.status_code, missing.json())
        == (
            404,
            {"detail": "Printer not found"},
        )
    )
    assert ours.status_code == 200
    on_b = (await async_client.get("/api/v1/ha-sensors/", headers=admin, params={"printer_id": b_id})).json()
    assert [s["entity_id"] for s in on_b] == ["binary_sensor.door"]
