"""Integration tests for the token-authenticated streaming-overlay feed (#2613).

Like the Cam Wall feed, the overlay endpoint exists as its own scope-gated
route because a kiosk/OBS URL is not a secret. But it is deliberately *wider*
than the Cam Wall: it names the file being printed (the overlay draws the part
on screen). So the tests that matter are the scope boundaries — an overlay
token must not reach the Cam Wall feed and vice versa, a camwall token must not
reach the overlay feed (that would leak the filename it is trusted to hide) —
plus the positive path and the disconnected-printer shape.
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
            "admin_username": f"overlayadmin{suffix}",
            "admin_password": "AdminPass1!",
        },
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": f"overlayadmin{suffix}", "password": "AdminPass1!"},
    )
    return login.json()["access_token"]


async def _mint(async_client: AsyncClient, jwt: str, *, scope: str, name: str = "obs") -> str:
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
        name="Stream P1S",
        ip_address="192.168.1.88",
        access_code="12345678",
        serial_number="01P00A000000002",
        model="P1S",
    )
    db_session.add(printer)
    await db_session.commit()
    return printer


class TestOverlayFeedAuth:
    async def test_no_token_is_rejected(self, async_client: AsyncClient, printer_row):
        await _setup_admin(async_client, suffix="_notoken")
        response = await async_client.get(f"/api/v1/printers/{printer_row.id}/overlay-status")
        assert response.status_code == 401

    async def test_garbage_token_is_rejected(self, async_client: AsyncClient, printer_row):
        await _setup_admin(async_client, suffix="_garbage")
        response = await async_client.get(f"/api/v1/printers/{printer_row.id}/overlay-status?token=bblt_aaaaaaaa_nope")
        assert response.status_code == 401

    async def test_camera_stream_token_cannot_reach_the_feed(self, async_client: AsyncClient, printer_row):
        """A ``camera_stream`` token was handed out for video alone — it must not
        acquire the live print status (and filename) just because a new feature
        shipped.
        """
        jwt = await _setup_admin(async_client, suffix="_streamscope")
        stream_token = await _mint(async_client, jwt, scope="camera_stream")

        response = await async_client.get(f"/api/v1/printers/{printer_row.id}/overlay-status?token={stream_token}")
        assert response.status_code == 401

    async def test_camwall_token_cannot_reach_the_feed(self, async_client: AsyncClient, printer_row):
        """The crux of a *separate* scope from camwall.

        A Cam Wall token is trusted precisely because it can never name the part
        being printed. The overlay feed does name it, so a camwall token must be
        rejected here — otherwise every wall token silently gains filename
        visibility.
        """
        jwt = await _setup_admin(async_client, suffix="_camwallscope")
        camwall_token = await _mint(async_client, jwt, scope="camwall")

        response = await async_client.get(f"/api/v1/printers/{printer_row.id}/overlay-status?token={camwall_token}")
        assert response.status_code == 401

    async def test_overlay_token_reaches_the_feed(self, async_client: AsyncClient, printer_row):
        jwt = await _setup_admin(async_client, suffix="_rightscope")
        overlay_token = await _mint(async_client, jwt, scope="overlay")

        response = await async_client.get(f"/api/v1/printers/{printer_row.id}/overlay-status?token={overlay_token}")
        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Stream P1S"

    async def test_revoked_overlay_token_is_rejected(self, async_client: AsyncClient, printer_row):
        jwt = await _setup_admin(async_client, suffix="_revoked")
        created = await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {jwt}"},
            json={"name": "obs", "expires_in_days": 30, "scope": "overlay"},
        )
        overlay_token = created.json()["token"]
        await async_client.delete(
            f"/api/v1/auth/tokens/{created.json()['id']}",
            headers={"Authorization": f"Bearer {jwt}"},
        )

        response = await async_client.get(f"/api/v1/printers/{printer_row.id}/overlay-status?token={overlay_token}")
        assert response.status_code == 401


class TestOverlayFeedPayload:
    async def test_payload_shape_includes_filename_fields(self, async_client: AsyncClient, printer_row):
        """Unlike the Cam Wall, the overlay *does* carry the filename fields —
        that is what distinguishes the scope. Assert the exact key set so the
        payload can't silently grow to leak more than the overlay draws.
        """
        jwt = await _setup_admin(async_client, suffix="_payload")
        overlay_token = await _mint(async_client, jwt, scope="overlay")

        response = await async_client.get(f"/api/v1/printers/{printer_row.id}/overlay-status?token={overlay_token}")
        assert response.status_code == 200
        entry = response.json()

        # Never the secrets — the URL is on a public stream.
        for leaked in ("serial_number", "ip_address", "access_code"):
            assert leaked not in entry, f"{leaked} must not be served to an overlay token"

        assert set(entry) == {
            "id",
            "name",
            "camera_rotation",
            "connected",
            "state",
            "current_print",
            "gcode_file",
            "progress",
            "remaining_time",
            "layer_num",
            "total_layers",
            "stg_cur_name",
            "time_format",
        }

    async def test_disconnected_printer_reports_connected_false(self, async_client: AsyncClient, printer_row):
        """No MQTT client runs in tests, so the printer has no state — the
        overlay must render its offline state rather than erroring.
        """
        jwt = await _setup_admin(async_client, suffix="_offline")
        overlay_token = await _mint(async_client, jwt, scope="overlay")

        response = await async_client.get(f"/api/v1/printers/{printer_row.id}/overlay-status?token={overlay_token}")
        entry = response.json()
        assert entry["connected"] is False
        assert entry["state"] is None
        assert entry["current_print"] is None

    async def test_unknown_printer_is_404_not_401(self, async_client: AsyncClient):
        """A valid token for a printer id that doesn't exist is a 404 — the token
        passed the gate, the resource simply isn't there.
        """
        jwt = await _setup_admin(async_client, suffix="_404")
        overlay_token = await _mint(async_client, jwt, scope="overlay")

        response = await async_client.get(f"/api/v1/printers/99999/overlay-status?token={overlay_token}")
        assert response.status_code == 404


class TestOverlayTokenReachesTheVideo:
    """The overlay draws the camera feed, so the same token has to satisfy the
    camera-stream gate.
    """

    async def test_overlay_token_passes_the_camera_stream_gate(self, async_client: AsyncClient):
        from backend.app.core.auth import verify_camera_stream_token

        jwt = await _setup_admin(async_client, suffix="_video")
        overlay_token = await _mint(async_client, jwt, scope="overlay")

        assert await verify_camera_stream_token(overlay_token) is True

    async def test_overlay_gate_rejects_camera_stream_and_camwall(self, async_client: AsyncClient):
        from backend.app.core.auth import verify_overlay_token

        jwt = await _setup_admin(async_client, suffix="_gate")
        stream_token = await _mint(async_client, jwt, scope="camera_stream")
        camwall_token = await _mint(async_client, jwt, scope="camwall", name="wall")

        assert await verify_overlay_token(stream_token) is False
        assert await verify_overlay_token(camwall_token) is False

    async def test_camwall_gate_rejects_an_overlay_token(self, async_client: AsyncClient):
        """Symmetric guard: the new scope must not widen the Cam Wall either."""
        from backend.app.core.auth import verify_camwall_token

        jwt = await _setup_admin(async_client, suffix="_gate_camwall")
        overlay_token = await _mint(async_client, jwt, scope="overlay")

        assert await verify_camwall_token(overlay_token) is False
