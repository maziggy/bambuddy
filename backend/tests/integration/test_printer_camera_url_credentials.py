"""External camera credentials reach only those who may edit the printer.

The camera login commonly sits in its URL (rtsp://user:password@host/...).
Printer readers got both camera URLs as stored; the printer access code was
already withheld from them. Readers now get the URLs with the password
replaced; editors keep the full URLs they need to edit them.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

STREAM = "rtsp://cam:stream-pw@192.168.1.50:554/stream"
SNAPSHOT = "http://cam:snap-pw@192.168.1.50/snapshot.jpg"


async def _admin(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "camadmin", "admin_password": "AdminPass1!"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "camadmin", "password": "AdminPass1!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _viewer(client: AsyncClient, admin: dict[str, str]) -> dict[str, str]:
    group = await client.post(
        "/api/v1/groups/", headers=admin, json={"name": "camview", "permissions": ["printers:read"]}
    )
    await client.post(
        "/api/v1/users/",
        headers=admin,
        json={"username": "camviewer", "password": "UserPass1!", "group_ids": [group.json()["id"]]},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "camviewer", "password": "UserPass1!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _api_key(client: AsyncClient, admin: dict[str, str]) -> dict[str, str]:
    key = await client.post("/api/v1/api-keys/", headers=admin, json={"name": "reader", "can_read_status": True})
    return {"X-API-Key": key.json()["key"]}


@pytest.fixture
async def camera_printer(printer_factory):
    return await printer_factory(
        name="Cam", external_camera_enabled=True, external_camera_url=STREAM, external_camera_snapshot_url=SNAPSHOT
    )


@pytest.mark.parametrize("who", ["viewer", "api_key"])
async def test_a_reader_gets_the_camera_urls_without_their_passwords(async_client: AsyncClient, camera_printer, who):
    admin = await _admin(async_client)
    headers = await (_viewer if who == "viewer" else _api_key)(async_client, admin)

    listed = await async_client.get("/api/v1/printers/", headers=headers)
    single = await async_client.get(f"/api/v1/printers/{camera_printer.id}", headers=headers)

    assert listed.status_code == 200 and single.status_code == 200, (listed.text, single.text)
    for body in (listed.json()[0], single.json()):
        assert "stream-pw" not in body["external_camera_url"]
        assert "snap-pw" not in body["external_camera_snapshot_url"]
        assert body["external_camera_url"].endswith("@192.168.1.50:554/stream")
    assert "stream-pw" not in listed.text + single.text


async def test_an_editor_gets_the_full_camera_urls(async_client: AsyncClient, camera_printer):
    admin = await _admin(async_client)

    single = (await async_client.get(f"/api/v1/printers/{camera_printer.id}", headers=admin)).json()
    listed = (await async_client.get("/api/v1/printers/", headers=admin)).json()[0]

    for body in (single, listed):
        assert body["external_camera_url"] == STREAM
        assert body["external_camera_snapshot_url"] == SNAPSHOT


async def test_an_edit_leaves_the_stored_camera_url_alone(async_client: AsyncClient, camera_printer, db_session):
    """The update's response is the reader shape; nothing it shows is written back."""
    from backend.app.models.printer import Printer

    printer_id = camera_printer.id
    admin = await _admin(async_client)

    response = await async_client.patch(f"/api/v1/printers/{printer_id}", headers=admin, json={"camera_rotation": 90})

    assert response.status_code == 200, response.text
    db_session.expire_all()
    stored = await db_session.get(Printer, printer_id)
    assert stored.external_camera_url == STREAM and stored.external_camera_snapshot_url == SNAPSHOT


async def test_the_camera_test_takes_the_url_in_its_body_and_still_in_the_query(
    async_client: AsyncClient, camera_printer
):
    """In the body it stays out of the access log; the query form is kept for a
    cached older page."""
    from unittest.mock import AsyncMock, patch

    printer_id = camera_printer.id
    admin = await _admin(async_client)

    with patch(
        "backend.app.services.external_camera.test_connection",
        new_callable=AsyncMock,
        return_value={"success": True},
    ) as test:
        body = await async_client.post(
            f"/api/v1/printers/{printer_id}/camera/external/test",
            headers=admin,
            json={"url": STREAM, "camera_type": "rtsp"},
        )
        query = await async_client.post(
            f"/api/v1/printers/{printer_id}/camera/external/test",
            headers=admin,
            params={"url": STREAM, "camera_type": "rtsp"},
        )

    assert body.status_code == 200, body.text
    assert query.status_code == 200, query.text
    assert [c.args for c in test.await_args_list] == [(STREAM, "rtsp"), (STREAM, "rtsp")]
