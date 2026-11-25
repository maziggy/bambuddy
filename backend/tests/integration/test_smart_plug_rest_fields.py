"""A REST plug's URLs and request bodies reach only those who may edit plugs.

They commonly hold the device's credential: a login or a token in the URL, a
webhook id in its path, or a token in the body. Readers switch and poll a plug
by its id and never need them, so they get none; editors keep them to edit.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

REST = {
    "rest_on_url": "http://admin:on-value@plug.elsewhere.example/relay/0?turn=on",
    "rest_off_url": "http://plug.elsewhere.example/relay/0?turn=off&auth_key=off-value",
    "rest_status_url": "http://plug.elsewhere.example/api/webhook/status-value",
    "rest_power_url": "http://admin:power-value@plug.elsewhere.example/status",
    "rest_energy_url": "http://admin:energy-value@plug.elsewhere.example/energy",
    "rest_on_body": '{"token": "body-on-value"}',
    "rest_off_body": '{"token": "body-off-value"}',
}
HIDDEN = tuple(REST)


async def _admin(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "plugadmin", "admin_password": "AdminPass1!"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "plugadmin", "password": "AdminPass1!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _viewer(client: AsyncClient, admin: dict[str, str]) -> dict[str, str]:
    group = await client.post(
        "/api/v1/groups/",
        headers=admin,
        json={"name": "plugview", "permissions": ["smart_plugs:read", "printers:read"]},
    )
    await client.post(
        "/api/v1/users/",
        headers=admin,
        json={"username": "plugviewer", "password": "UserPass1!", "group_ids": [group.json()["id"]]},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "plugviewer", "password": "UserPass1!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _api_key(client: AsyncClient, admin: dict[str, str]) -> dict[str, str]:
    key = await client.post("/api/v1/api-keys/", headers=admin, json={"name": "reader", "can_read_status": True})
    return {"X-API-Key": key.json()["key"]}


@pytest.fixture
async def rest_plug(printer_factory, smart_plug_factory):
    printer = await printer_factory(name="P")
    return await smart_plug_factory(
        name="Rest", plug_type="rest", ip_address=None, printer_id=printer.id, rest_method="GET", **REST
    )


async def _bodies(client: AsyncClient, headers: dict[str, str], plug) -> list[dict]:
    listed = await client.get("/api/v1/smart-plugs/", headers=headers)
    single = await client.get(f"/api/v1/smart-plugs/{plug.id}", headers=headers)
    by_printer = await client.get(f"/api/v1/smart-plugs/by-printer/{plug.printer_id}", headers=headers)
    for response in (listed, single, by_printer):
        assert response.status_code == 200, response.text
    return [listed.json()[0], single.json(), by_printer.json()]


@pytest.mark.parametrize("who", ["viewer", "api_key"])
async def test_a_reader_gets_no_rest_urls_or_bodies(async_client: AsyncClient, rest_plug, who):
    admin = await _admin(async_client)
    headers = await (_viewer if who == "viewer" else _api_key)(async_client, admin)

    bodies = await _bodies(async_client, headers, rest_plug)

    for body in bodies:
        assert {field: body[field] for field in HIDDEN} == dict.fromkeys(HIDDEN)
        assert body["plug_type"] == "rest" and body["name"] == "Rest"
    assert "-value" not in str(bodies)


async def test_an_editor_gets_the_rest_urls_and_bodies(async_client: AsyncClient, rest_plug):
    admin = await _admin(async_client)

    for body in await _bodies(async_client, admin, rest_plug):
        assert {field: body[field] for field in HIDDEN} == REST


async def test_an_update_answers_with_and_keeps_the_rest_fields(async_client: AsyncClient, rest_plug, db_session):
    from backend.app.models.smart_plug import SmartPlug

    plug_id = rest_plug.id
    admin = await _admin(async_client)

    response = await async_client.patch(f"/api/v1/smart-plugs/{plug_id}", headers=admin, json={"name": "Renamed"})

    assert response.status_code == 200, response.text
    assert {field: response.json()[field] for field in HIDDEN} == REST
    db_session.expire_all()
    stored = await db_session.get(SmartPlug, plug_id)
    assert {field: getattr(stored, field) for field in HIDDEN} == REST


async def test_with_authentication_off_the_rest_fields_are_shown(async_client: AsyncClient, rest_plug):
    response = await async_client.get(f"/api/v1/smart-plugs/{rest_plug.id}")

    assert response.status_code == 200, response.text
    assert {field: response.json()[field] for field in HIDDEN} == REST
