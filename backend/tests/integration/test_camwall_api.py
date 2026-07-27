"""Integration tests for the token-authenticated Cam Wall feed (#2531).

The feature's whole reason for existing as a separate endpoint (rather than
letting a token through to ``GET /printers``) is that a kiosk URL is not a
secret. So the tests that matter here are the negative ones: what a Cam Wall
token *cannot* reach, and what the payload *does not* contain.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _setup_admin(async_client: AsyncClient, *, suffix: str) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": f"camwalladmin{suffix}",
            "admin_password": "AdminPass1!",
        },
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": f"camwalladmin{suffix}", "password": "AdminPass1!"},
    )
    return login.json()["access_token"]


async def _mint(async_client: AsyncClient, jwt: str, *, scope: str, name: str = "kiosk") -> str:
    response = await async_client.post(
        "/api/v1/auth/tokens",
        headers={"Authorization": f"Bearer {jwt}"},
        json={"name": name, "expires_in_days": 30, "scope": scope},
    )
    assert response.status_code == 201, response.text
    assert response.json()["scope"] == scope
    return response.json()["token"]


@pytest.fixture
async def printer_row(db_session):
    """Insert the printer straight into the DB.

    POST /printers probes the real device before it will store a row, and there
    is no printer on the other end of a test run.
    """
    from backend.app.models.printer import Printer

    printer = Printer(
        name="Wall P1S",
        ip_address="192.168.1.77",
        access_code="12345678",
        serial_number="01P00A000000001",
        model="P1S",
    )
    db_session.add(printer)
    await db_session.commit()
    return printer


class TestCamWallFeedAuth:
    async def test_no_token_is_rejected(self, async_client: AsyncClient):
        await _setup_admin(async_client, suffix="_notoken")
        response = await async_client.get("/api/v1/camwall/printers")
        assert response.status_code == 401

    async def test_garbage_token_is_rejected(self, async_client: AsyncClient):
        await _setup_admin(async_client, suffix="_garbage")
        response = await async_client.get("/api/v1/camwall/printers?token=bblt_aaaaaaaa_nope")
        assert response.status_code == 401

    async def test_camera_stream_token_cannot_reach_the_feed(self, async_client: AsyncClient):
        """The point of the separate scope.

        ``camera_stream`` tokens are already in the wild, minted by users who
        agreed to hand out *video*. Shipping the Cam Wall must not retroactively
        grant them the ability to enumerate printers by name.
        """
        jwt = await _setup_admin(async_client, suffix="_wrongscope")
        stream_token = await _mint(async_client, jwt, scope="camera_stream")

        response = await async_client.get(f"/api/v1/camwall/printers?token={stream_token}")
        assert response.status_code == 401

    async def test_camwall_token_reaches_the_feed(self, async_client: AsyncClient, printer_row):
        jwt = await _setup_admin(async_client, suffix="_rightscope")
        camwall_token = await _mint(async_client, jwt, scope="camwall")

        response = await async_client.get(f"/api/v1/camwall/printers?token={camwall_token}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body) == 1
        assert body[0]["name"] == "Wall P1S"

    async def test_revoked_camwall_token_is_rejected(self, async_client: AsyncClient):
        jwt = await _setup_admin(async_client, suffix="_revoked")
        created = await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {jwt}"},
            json={"name": "kiosk", "expires_in_days": 30, "scope": "camwall"},
        )
        camwall_token = created.json()["token"]
        await async_client.delete(
            f"/api/v1/auth/tokens/{created.json()['id']}",
            headers={"Authorization": f"Bearer {jwt}"},
        )

        response = await async_client.get(f"/api/v1/camwall/printers?token={camwall_token}")
        assert response.status_code == 401


class TestCamWallFeedPayload:
    async def test_payload_withholds_secrets_and_filenames(self, async_client: AsyncClient, printer_row):
        """A URL taped to a TV must not disclose more than the picture does.

        Serial number and IP ride along on the ordinary printer list even for
        non-secret callers, and the filename names the customer's part. None of
        the three may appear here.
        """
        jwt = await _setup_admin(async_client, suffix="_payload")
        camwall_token = await _mint(async_client, jwt, scope="camwall")

        response = await async_client.get(f"/api/v1/camwall/printers?token={camwall_token}")
        assert response.status_code == 200
        entry = response.json()[0]

        for leaked in ("serial_number", "ip_address", "access_code", "subtask_name", "gcode_file"):
            assert leaked not in entry, f"{leaked} must not be served to a kiosk token"

        assert set(entry) == {
            "id",
            "name",
            "camera_rotation",
            "connected",
            "state",
            "progress",
            "remaining_time",
            "layer_num",
            "total_layers",
            "hms_errors",
        }

    async def test_disconnected_printer_reports_connected_false(self, async_client: AsyncClient, printer_row):
        """No MQTT client is running in tests, so the printer has no state at
        all — the tile must render as offline rather than blank.
        """
        jwt = await _setup_admin(async_client, suffix="_offline")
        camwall_token = await _mint(async_client, jwt, scope="camwall")

        response = await async_client.get(f"/api/v1/camwall/printers?token={camwall_token}")
        entry = response.json()[0]
        assert entry["connected"] is False
        assert entry["state"] is None
        assert entry["hms_errors"] == []


class TestCamWallTokenReachesTheVideo:
    """A wall that can list the tiles but not fill them is useless — the same
    token has to satisfy the camera-stream gate.
    """

    async def test_camwall_token_passes_the_camera_stream_gate(self, async_client: AsyncClient):
        from backend.app.core.auth import verify_camera_stream_token

        jwt = await _setup_admin(async_client, suffix="_video")
        camwall_token = await _mint(async_client, jwt, scope="camwall")

        assert await verify_camera_stream_token(camwall_token) is True

    async def test_camera_stream_token_still_passes_its_own_gate(self, async_client: AsyncClient):
        """Regression guard on #1108: widening the accepted scopes must not have
        broken the tokens that were already working.
        """
        from backend.app.core.auth import verify_camera_stream_token

        jwt = await _setup_admin(async_client, suffix="_video_legacy")
        stream_token = await _mint(async_client, jwt, scope="camera_stream")

        assert await verify_camera_stream_token(stream_token) is True

    async def test_camwall_gate_rejects_a_camera_stream_token(self, async_client: AsyncClient):
        from backend.app.core.auth import verify_camwall_token

        jwt = await _setup_admin(async_client, suffix="_gate_narrow")
        stream_token = await _mint(async_client, jwt, scope="camera_stream")

        assert await verify_camwall_token(stream_token) is False


class TestScopeValidation:
    async def test_unknown_scope_is_rejected_at_mint(self, async_client: AsyncClient):
        jwt = await _setup_admin(async_client, suffix="_badscope")
        response = await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {jwt}"},
            json={"name": "x", "expires_in_days": 30, "scope": "printers_write"},
        )
        assert response.status_code == 400
        assert "unsupported scope" in response.json()["detail"].lower()
