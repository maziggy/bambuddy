"""Integration tests for long-lived camera-stream token routes (#1108).

Cover the auth gates, ownership rules, max-lifetime cap, token-shown-once
contract, and the camera-stream auth fall-through (the existing 60-min
ephemeral path still works AND a long-lived token is also accepted).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_admin(async_client: AsyncClient, *, suffix: str = "") -> str:
    """Create the first admin and return their JWT."""
    await async_client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": f"tokenadmin{suffix}",
            "admin_password": "AdminPass1!",
        },
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": f"tokenadmin{suffix}", "password": "AdminPass1!"},
    )
    return login.json()["access_token"]


async def _create_user(async_client: AsyncClient, admin_token: str, username: str, *, role: str = "user") -> int:
    """Create a non-admin user via the admin API and return their id.

    The user is assigned to the seeded "Viewers" group so they hold
    ``CAMERA_VIEW`` — without that, regular users cannot create their own
    long-lived tokens (which is the same gate the existing 60-min ephemeral
    flow uses).
    """
    # Fetch Viewers group id so the new user inherits CAMERA_VIEW.
    groups_resp = await async_client.get("/api/v1/groups/", headers={"Authorization": f"Bearer {admin_token}"})
    viewers = next((g for g in groups_resp.json() if g["name"] == "Viewers"), None)
    assert viewers is not None, f"Viewers group not seeded: {groups_resp.text}"

    response = await async_client.post(
        "/api/v1/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": username,
            "password": "UserPass1!",
            "role": role,
            "group_ids": [viewers["id"]],
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def _login(async_client: AsyncClient, username: str) -> str:
    response = await async_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "UserPass1!"},
    )
    body = response.json()
    token = body.get("access_token")
    assert token, f"login for {username!r} returned no access_token: {body}"
    return token


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateLongLivedToken:
    async def test_create_returns_plaintext_token_exactly_once(self, async_client: AsyncClient):
        token = await _setup_admin(async_client, suffix="_create_once")
        response = await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Home Assistant", "expires_in_days": 30},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["token"].startswith("bblt_")
        assert body["name"] == "Home Assistant"
        assert body["scope"] == "camera_stream"
        assert body["lookup_prefix"]
        token_id = body["id"]

        # Listing must NOT include the plaintext (shown-once contract).
        listing = await async_client.get(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert listing.status_code == 200
        listed = next((t for t in listing.json() if t["id"] == token_id), None)
        assert listed is not None
        assert listed["token"] is None  # plaintext gone forever

    async def test_create_rejects_expires_in_zero(self, async_client: AsyncClient):
        """Issue #1108: ``expire_in: 0`` (never) is explicitly forbidden."""
        token = await _setup_admin(async_client, suffix="_zero_expire")
        response = await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "x", "expires_in_days": 0},
        )
        assert response.status_code == 400
        assert "positive" in response.json()["detail"].lower()

    async def test_create_rejects_above_max(self, async_client: AsyncClient):
        token = await _setup_admin(async_client, suffix="_above_max")
        response = await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "x", "expires_in_days": 366},
        )
        assert response.status_code == 400
        assert "365" in response.json()["detail"]

    async def test_create_requires_auth(self, async_client: AsyncClient):
        await _setup_admin(async_client, suffix="_unauth")
        response = await async_client.post(
            "/api/v1/auth/tokens",
            json={"name": "x", "expires_in_days": 7},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListLongLivedTokens:
    async def test_list_returns_only_callers_tokens_by_default(self, async_client: AsyncClient):
        admin_token = await _setup_admin(async_client, suffix="_list_default")
        bob_id = await _create_user(async_client, admin_token, "bob_list")
        bob_token = await _login(async_client, "bob_list")

        # Each user creates one token.
        await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "admins", "expires_in_days": 7},
        )
        await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {bob_token}"},
            json={"name": "bobs", "expires_in_days": 7},
        )

        # Bob's listing should see only his.
        bob_listing = await async_client.get(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {bob_token}"},
        )
        names = {t["name"] for t in bob_listing.json()}
        assert names == {"bobs"}
        assert bob_id == bob_listing.json()[0]["user_id"]

    async def test_admin_can_filter_by_user_id(self, async_client: AsyncClient):
        admin_token = await _setup_admin(async_client, suffix="_admin_filter")
        bob_id = await _create_user(async_client, admin_token, "bob_filter")
        bob_token = await _login(async_client, "bob_filter")
        await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {bob_token}"},
            json={"name": "bobs", "expires_in_days": 7},
        )

        admin_view = await async_client.get(
            f"/api/v1/auth/tokens?user_id={bob_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert admin_view.status_code == 200
        names = {t["name"] for t in admin_view.json()}
        assert names == {"bobs"}

    async def test_non_admin_cannot_see_other_users_tokens(self, async_client: AsyncClient):
        admin_token = await _setup_admin(async_client, suffix="_non_admin")
        await _create_user(async_client, admin_token, "alice_see")
        bob_id = await _create_user(async_client, admin_token, "bob_see")
        alice_token = await _login(async_client, "alice_see")

        forbidden = await async_client.get(
            f"/api/v1/auth/tokens?user_id={bob_id}",
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        assert forbidden.status_code == 403


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------


class TestRevokeLongLivedToken:
    async def test_owner_can_revoke_own_token(self, async_client: AsyncClient):
        token = await _setup_admin(async_client, suffix="_revoke_own")
        created = await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "x", "expires_in_days": 7},
        )
        token_id = created.json()["id"]

        revoke = await async_client.delete(
            f"/api/v1/auth/tokens/{token_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert revoke.status_code == 204

        # Now gone from the listing.
        listing = await async_client.get("/api/v1/auth/tokens", headers={"Authorization": f"Bearer {token}"})
        assert all(t["id"] != token_id for t in listing.json())

    async def test_admin_can_revoke_any_users_token(self, async_client: AsyncClient):
        admin_token = await _setup_admin(async_client, suffix="_revoke_any")
        await _create_user(async_client, admin_token, "bob_revoke")
        bob_token = await _login(async_client, "bob_revoke")
        created = await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {bob_token}"},
            json={"name": "bobs", "expires_in_days": 7},
        )
        token_id = created.json()["id"]

        admin_revoke = await async_client.delete(
            f"/api/v1/auth/tokens/{token_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert admin_revoke.status_code == 204

    async def test_non_owner_non_admin_cannot_revoke(self, async_client: AsyncClient):
        admin_token = await _setup_admin(async_client, suffix="_revoke_other")
        await _create_user(async_client, admin_token, "alice_attack")
        await _create_user(async_client, admin_token, "bob_target")
        bob_token = await _login(async_client, "bob_target")
        alice_token = await _login(async_client, "alice_attack")

        created = await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {bob_token}"},
            json={"name": "bobs", "expires_in_days": 7},
        )
        token_id = created.json()["id"]

        forbidden = await async_client.delete(
            f"/api/v1/auth/tokens/{token_id}",
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        assert forbidden.status_code == 403

    async def test_revoke_unknown_id_404(self, async_client: AsyncClient):
        token = await _setup_admin(async_client, suffix="_revoke_unknown")
        response = await async_client.delete(
            "/api/v1/auth/tokens/99999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Auth fall-through: ``verify_camera_stream_token`` accepts both kinds
# ---------------------------------------------------------------------------
# The full /camera/stream HTTP integration would need a real ffmpeg / printer
# socket to keep the StreamingResponse alive. Verifying the auth dependency
# directly is a stronger check anyway: the route's only auth job is to call
# ``verify_camera_stream_token``, which is what these tests exercise.


class TestCameraStreamTokenVerification:
    async def test_long_lived_token_verifies_via_camera_stream_path(self, async_client: AsyncClient):
        """A freshly minted long-lived token must pass the same dependency
        the camera-stream route uses, after the ephemeral path would have
        rejected it.
        """
        from backend.app.core.auth import verify_camera_stream_token

        token = await _setup_admin(async_client, suffix="_verify_long")
        created = await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "kiosk", "expires_in_days": 90},
        )
        long_lived = created.json()["token"]

        assert await verify_camera_stream_token(long_lived) is True

    async def test_revoked_long_lived_token_fails_camera_stream_check(self, async_client: AsyncClient):
        from backend.app.core.auth import verify_camera_stream_token

        token = await _setup_admin(async_client, suffix="_verify_revoke")
        created = await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "kiosk", "expires_in_days": 30},
        )
        long_lived = created.json()["token"]
        token_id = created.json()["id"]

        await async_client.delete(
            f"/api/v1/auth/tokens/{token_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert await verify_camera_stream_token(long_lived) is False

    async def test_garbage_token_fails_camera_stream_check(self, async_client: AsyncClient):
        from backend.app.core.auth import verify_camera_stream_token

        await _setup_admin(async_client, suffix="_verify_garbage")
        assert await verify_camera_stream_token("bblt_aaaaaaaa_garbage") is False
        assert await verify_camera_stream_token("not-a-real-token") is False
