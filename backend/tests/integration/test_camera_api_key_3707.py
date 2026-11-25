"""API keys in a header on the camera routes (#3707).

A server-side caller can send a header, so it doesn't need a token in the URL,
where a reverse proxy's access log would keep it. ``?token=`` stays as it was.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

REFUSED = {401, 403, 404}


async def _admin(async_client: AsyncClient) -> dict[str, str]:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "camkeyadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "camkeyadmin", "password": "AdminPass1!"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _key(async_client: AsyncClient, admin: dict[str, str], **fields) -> dict:
    created = await async_client.post("/api/v1/api-keys/", headers=admin, json={"name": "camera", **fields})
    assert created.status_code in (200, 201), created.text
    return created.json()


def _snapshot(printer_id: int) -> str:
    return f"/api/v1/printers/{printer_id}/camera/snapshot"


class TestApiKeyHeader:
    async def test_x_api_key_reaches_the_snapshot(self, async_client, printer_factory):
        printer = await printer_factory(name="A")
        admin = await _admin(async_client)
        key = await _key(async_client, admin, can_read_status=True)
        response = await async_client.get(_snapshot(printer.id), headers={"X-API-Key": key["key"]})
        assert response.status_code not in REFUSED

    async def test_a_bearer_key_reaches_the_snapshot(self, async_client, printer_factory):
        printer = await printer_factory(name="A")
        admin = await _admin(async_client)
        key = await _key(async_client, admin, can_read_status=True)
        response = await async_client.get(_snapshot(printer.id), headers={"Authorization": f"Bearer {key['key']}"})
        assert response.status_code not in REFUSED

    async def test_a_key_without_read_status_is_refused(self, async_client, printer_factory):
        printer = await printer_factory(name="A")
        admin = await _admin(async_client)
        key = await _key(async_client, admin, can_read_status=False, can_queue=True)
        response = await async_client.get(_snapshot(printer.id), headers={"X-API-Key": key["key"]})
        assert response.status_code == 403

    async def test_a_key_stays_within_its_printers(self, async_client, printer_factory):
        a, b = await printer_factory(name="A"), await printer_factory(name="B")
        admin = await _admin(async_client)
        key = await _key(async_client, admin, can_read_status=True, printer_ids=[a.id])
        response = await async_client.get(_snapshot(b.id), headers={"X-API-Key": key["key"]})
        assert response.status_code == 404

    async def test_an_invalid_key_is_refused(self, async_client, printer_factory):
        printer = await printer_factory(name="A")
        await _admin(async_client)
        response = await async_client.get(_snapshot(printer.id), headers={"X-API-Key": "bb_not_a_key"})
        assert response.status_code == 401

    async def test_an_invalid_bearer_key_is_refused(self, async_client, printer_factory):
        printer = await printer_factory(name="A")
        await _admin(async_client)
        response = await async_client.get(_snapshot(printer.id), headers={"Authorization": "Bearer bb_not_a_key"})
        assert response.status_code == 401

    async def test_a_disabled_key_is_refused(self, async_client, printer_factory):
        printer = await printer_factory(name="A")
        admin = await _admin(async_client)
        key = await _key(async_client, admin, can_read_status=True)
        changed = await async_client.patch(f"/api/v1/api-keys/{key['id']}", headers=admin, json={"enabled": False})
        assert changed.status_code == 200, changed.text
        response = await async_client.get(_snapshot(printer.id), headers={"X-API-Key": key["key"]})
        assert response.status_code == 401

    async def test_the_stream_names_the_key_as_its_opener(self, async_client, printer_factory):
        from backend.app.core.auth import require_camera_stream_token_if_auth_enabled

        printer = await printer_factory(name="A")
        admin = await _admin(async_client)
        key = await _key(async_client, admin, can_read_status=True)
        opener = await require_camera_stream_token_if_auth_enabled()(printer.id, None, None, key["key"])
        assert opener.auth_on is True
        assert opener.api_key_id == key["id"]
        assert opener.api_key_prefix == key["key_prefix"]


class TestUnchanged:
    async def test_no_credential_is_refused(self, async_client, printer_factory):
        printer = await printer_factory(name="A")
        await _admin(async_client)
        assert (await async_client.get(_snapshot(printer.id))).status_code == 401

    async def test_a_login_session_still_needs_a_token(self, async_client, printer_factory):
        printer = await printer_factory(name="A")
        admin = await _admin(async_client)
        assert (await async_client.get(_snapshot(printer.id), headers=admin)).status_code == 401

    async def test_a_url_token_still_works(self, async_client, printer_factory):
        printer = await printer_factory(name="A")
        admin = await _admin(async_client)
        minted = await async_client.post("/api/v1/printers/camera/stream-token", headers=admin)
        response = await async_client.get(f"{_snapshot(printer.id)}?token={minted.json()['token']}")
        assert response.status_code not in REFUSED

    async def test_a_url_token_wins_over_a_key(self, async_client, printer_factory):
        printer = await printer_factory(name="A")
        admin = await _admin(async_client)
        key = await _key(async_client, admin, can_read_status=False, can_queue=True)
        minted = await async_client.post("/api/v1/printers/camera/stream-token", headers=admin)
        response = await async_client.get(
            f"{_snapshot(printer.id)}?token={minted.json()['token']}", headers={"X-API-Key": key["key"]}
        )
        assert response.status_code not in REFUSED

    async def test_auth_off_needs_no_credential(self, async_client, printer_factory):
        printer = await printer_factory(name="A")
        assert (await async_client.get(_snapshot(printer.id))).status_code not in REFUSED
        response = await async_client.get(_snapshot(printer.id), headers={"X-API-Key": "bb_not_a_key"})
        assert response.status_code not in REFUSED

    async def test_a_bad_url_token_is_not_rescued_by_a_key(self, async_client, printer_factory):
        printer = await printer_factory(name="A")
        admin = await _admin(async_client)
        key = await _key(async_client, admin, can_read_status=True)
        response = await async_client.get(f"{_snapshot(printer.id)}?token=wrong", headers={"X-API-Key": key["key"]})
        assert response.status_code == 401
