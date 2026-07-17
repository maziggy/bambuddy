"""Integration tests for the Orca Cloud device-pairing routes.

Covers the /device/start -> /device/poll pairing loop, its terminal outcomes,
token persistence, and status/logout — all in auth-disabled mode (the global
Settings-table fallback), with the service's network calls patched out.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from backend.app.services import orca_cloud as orca_service
from backend.app.services.orca_cloud import DevicePoll, OrcaCloudService

AUTH_DISABLED = "backend.app.core.auth.is_auth_enabled"


@pytest.fixture(autouse=True)
def _dummy_shared_client():
    """Register a throwaway shared HTTP client so per-request
    OrcaCloudService() instances don't spin up (and leak) a real one — the
    network methods are patched anyway."""
    from unittest.mock import MagicMock

    orca_service.set_shared_http_client(MagicMock())
    yield
    orca_service.set_shared_http_client(None)


_DEVICE_CODE_RESPONSE = {
    "device_code": "DEV-SECRET-1",
    "user_code": "ABCD-EF12",
    "verification_uri": "https://cloud.orcaslicer.com/app/settings",
    "verification_uri_complete": "https://cloud.orcaslicer.com/app/settings?user_code=ABCD-EF12",
    "expires_in": 600,
    "interval": 5,
}


async def _start(async_client: AsyncClient):
    with (
        patch(AUTH_DISABLED, return_value=False),
        patch.object(OrcaCloudService, "request_device_code", return_value=dict(_DEVICE_CODE_RESPONSE)),
    ):
        return await async_client.post("/api/v1/orca-cloud/device/start")


class TestDeviceStart:
    @pytest.mark.asyncio
    async def test_start_returns_user_code_and_hides_device_code(self, async_client: AsyncClient):
        resp = await _start(async_client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_code"] == "ABCD-EF12"
        assert body["interval"] == 5
        assert body["verification_uri_complete"].endswith("user_code=ABCD-EF12")
        # The device_code is a secret and must NOT be echoed to the client.
        assert "device_code" not in body


class TestDevicePoll:
    @pytest.mark.asyncio
    async def test_poll_without_pending_is_400(self, async_client: AsyncClient):
        with patch(AUTH_DISABLED, return_value=False):
            resp = await async_client.post("/api/v1/orca-cloud/device/poll")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_poll_pending_reports_in_progress(self, async_client: AsyncClient):
        await _start(async_client)
        with (
            patch(AUTH_DISABLED, return_value=False),
            patch.object(OrcaCloudService, "poll_token", return_value=(DevicePoll.PENDING, None)),
        ):
            resp = await async_client.post("/api/v1/orca-cloud/device/poll")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == DevicePoll.PENDING
        assert body["connected"] is False

    @pytest.mark.asyncio
    async def test_poll_complete_persists_tokens_and_connects(self, async_client: AsyncClient):
        await _start(async_client)

        async def fake_complete(self, device_code):
            assert device_code == "DEV-SECRET-1"  # the stored secret is used
            self.access_token = "oc_ext_new"
            self.refresh_token = "oc_ext_rt_new"
            self.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=86400)
            return DevicePoll.COMPLETE, {"access_token": "oc_ext_new"}

        with (
            patch(AUTH_DISABLED, return_value=False),
            patch.object(OrcaCloudService, "poll_token", new=fake_complete),
            patch.object(OrcaCloudService, "introspect", return_value={"user_id": "user-123"}),
        ):
            resp = await async_client.post("/api/v1/orca-cloud/device/poll")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == DevicePoll.COMPLETE
            assert body["connected"] is True
            assert body["user_id"] == "user-123"

            # Status now reflects the connection, and the pending state is
            # cleared (a fresh poll finds nothing pending -> 400).
            status = await async_client.get("/api/v1/orca-cloud/status")
            assert status.json()["connected"] is True
            again = await async_client.post("/api/v1/orca-cloud/device/poll")
            assert again.status_code == 400

    @pytest.mark.asyncio
    async def test_poll_denied_clears_pending(self, async_client: AsyncClient):
        await _start(async_client)
        with (
            patch(AUTH_DISABLED, return_value=False),
            patch.object(OrcaCloudService, "poll_token", return_value=(DevicePoll.DENIED, None)),
        ):
            resp = await async_client.post("/api/v1/orca-cloud/device/poll")
        assert resp.json()["status"] == DevicePoll.DENIED
        # Pending cleared -> next poll has nothing to poll.
        with patch(AUTH_DISABLED, return_value=False):
            again = await async_client.post("/api/v1/orca-cloud/device/poll")
        assert again.status_code == 400

    @pytest.mark.asyncio
    async def test_poll_expires_by_ttl_without_network(self, async_client: AsyncClient):
        """A pending code older than DEVICE_CODE_TTL is reported expired
        without even calling the token endpoint. Shrinking the TTL to a
        negative window makes any just-created pending state 'stale'."""
        await _start(async_client)

        # poll_token must NOT be called; if it were, this would blow up.
        def _boom(*a, **k):
            raise AssertionError("poll_token should not be called for an expired code")

        with (
            patch(AUTH_DISABLED, return_value=False),
            patch("backend.app.api.routes.orca_cloud.DEVICE_CODE_TTL", timedelta(seconds=-1)),
            patch.object(OrcaCloudService, "poll_token", new=_boom),
        ):
            resp = await async_client.post("/api/v1/orca-cloud/device/poll")
        assert resp.status_code == 200
        assert resp.json()["status"] == DevicePoll.EXPIRED
        # And the expired pending state is cleared.
        with patch(AUTH_DISABLED, return_value=False):
            again = await async_client.post("/api/v1/orca-cloud/device/poll")
        assert again.status_code == 400


class TestLogout:
    @pytest.mark.asyncio
    async def test_logout_clears_connection(self, async_client: AsyncClient):
        await _start(async_client)

        async def fake_complete(self, device_code):
            self.access_token = "oc_ext_new"
            self.refresh_token = "oc_ext_rt_new"
            self.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=86400)
            return DevicePoll.COMPLETE, {"access_token": "oc_ext_new"}

        with (
            patch(AUTH_DISABLED, return_value=False),
            patch.object(OrcaCloudService, "poll_token", new=fake_complete),
            patch.object(OrcaCloudService, "introspect", return_value={"user_id": "u"}),
        ):
            await async_client.post("/api/v1/orca-cloud/device/poll")

        with patch(AUTH_DISABLED, return_value=False):
            out = await async_client.post("/api/v1/orca-cloud/logout")
            assert out.status_code == 200
            status = await async_client.get("/api/v1/orca-cloud/status")
            assert status.json()["connected"] is False
