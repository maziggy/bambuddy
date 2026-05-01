"""Integration tests for #1182 — API keys reading cloud presets on the owner's behalf.

The contract these tests pin:

  Three independent fences must all pass for an API-keyed call to reach
  /cloud/* successfully:
    1. The key has an owner (``user_id IS NOT NULL``) — legacy keys created
       before #1182 are forced to be recreated.
    2. The key has ``can_access_cloud=True`` — opt-in scope so existing
       automation doesn't quietly start reading cloud data.
    3. The owner has a stored ``cloud_token`` — the existing requirement,
       unchanged.

  Plus the model-level invariants: deleting the owner CASCADEs the key,
  and the route-level guards reject impossible config (cloud access without
  auth enabled, cloud access on an ownerless legacy key).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import generate_api_key
from backend.app.models.api_key import APIKey
from backend.app.models.user import User


async def _setup_auth_with_admin(client: AsyncClient) -> str:
    """Enable auth + return an admin bearer token."""
    await client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": "cloudadmin",
            "admin_password": "AdminPass1!",
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "cloudadmin", "password": "AdminPass1!"},
    )
    return login.json()["access_token"]


async def _store_admin_cloud_token(db: AsyncSession, username: str, token: str) -> User:
    """Stash a fake cloud_token on a User so /cloud/* has something to find.

    The actual token value never reaches Bambu Cloud in these tests — every
    test that hits a /cloud/* route mocks the upstream HTTP call. We only
    need the column populated for ``build_authenticated_cloud`` to return a
    service instead of None.
    """
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one()
    user.cloud_token = token
    user.cloud_email = "owner@example.com"
    user.cloud_region = "global"
    await db.commit()
    await db.refresh(user)
    return user


class TestAPIKeyCreationFlags:
    """The new can_access_cloud flag is correctly stamped at create time and
    correctly rejected when the deployment can't satisfy it."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_stamps_owner_and_cloud_flag(self, async_client: AsyncClient):
        token = await _setup_auth_with_admin(async_client)

        resp = await async_client.post(
            "/api/v1/api-keys/",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "automation", "can_access_cloud": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] is not None  # owner stamped from creator
        assert body["can_access_cloud"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_with_cloud_flag_rejected_when_auth_disabled(self, async_client: AsyncClient):
        """can_access_cloud needs per-user cloud_token storage, which only
        exists in auth-enabled deployments — fail loudly at create time
        rather than silently producing a non-functional key."""
        # No setup_auth call → auth is disabled
        resp = await async_client.post(
            "/api/v1/api-keys/",
            json={"name": "should-fail", "can_access_cloud": True},
        )
        assert resp.status_code == 400
        assert "auth" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_without_cloud_flag_defaults_off(self, async_client: AsyncClient):
        """Default is opt-out — existing automation that doesn't pass the
        flag must not silently gain cloud access on upgrade."""
        token = await _setup_auth_with_admin(async_client)

        resp = await async_client.post(
            "/api/v1/api-keys/",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "no-cloud"},
        )
        assert resp.status_code == 200
        assert resp.json()["can_access_cloud"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_cloud_flag_rejected_on_legacy_key(self, async_client: AsyncClient, db_session: AsyncSession):
        """A legacy key (user_id NULL) cannot be flipped to can_access_cloud=True
        because there's no owner whose cloud_token to read; force recreate."""
        token = await _setup_auth_with_admin(async_client)

        # Create a legacy key directly in the DB (user_id NULL, mimicking
        # a row that predates the migration).
        full_key, key_hash, key_prefix = generate_api_key()
        legacy = APIKey(
            name="legacy",
            key_hash=key_hash,
            key_prefix=key_prefix,
            user_id=None,
        )
        db_session.add(legacy)
        await db_session.commit()
        await db_session.refresh(legacy)

        resp = await async_client.patch(
            f"/api/v1/api-keys/{legacy.id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"can_access_cloud": True},
        )
        assert resp.status_code == 400
        assert "recreate" in resp.json()["detail"].lower()


class TestCloudRouteGating:
    """The /cloud/* router-level dependency rejects API keys that don't satisfy
    all three fences."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_legacy_key_rejected_with_recreate_message(self, async_client: AsyncClient, db_session: AsyncSession):
        """Legacy ownerless key → /cloud/* responds 401 with explicit recreate copy."""
        await _setup_auth_with_admin(async_client)

        full_key, key_hash, key_prefix = generate_api_key()
        legacy = APIKey(
            name="legacy",
            key_hash=key_hash,
            key_prefix=key_prefix,
            user_id=None,
            can_access_cloud=False,  # irrelevant — owner check fires first
        )
        db_session.add(legacy)
        await db_session.commit()

        resp = await async_client.get(
            "/api/v1/cloud/status",
            headers={"X-API-Key": full_key},
        )
        assert resp.status_code == 401
        assert "recreate" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_owned_key_without_cloud_flag_rejected(self, async_client: AsyncClient, db_session: AsyncSession):
        """Owner is set but can_access_cloud=False → 403 with 'enable cloud access'."""
        await _setup_auth_with_admin(async_client)
        # Look up the admin we just created so we can stamp ownership.
        result = await db_session.execute(select(User).where(User.username == "cloudadmin"))
        admin = result.scalar_one()

        full_key, key_hash, key_prefix = generate_api_key()
        owned = APIKey(
            name="no-cloud-scope",
            key_hash=key_hash,
            key_prefix=key_prefix,
            user_id=admin.id,
            can_access_cloud=False,
        )
        db_session.add(owned)
        await db_session.commit()

        resp = await async_client.get(
            "/api/v1/cloud/status",
            headers={"X-API-Key": full_key},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code} with body {resp.json()}"
        assert "cloud" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_owned_key_with_cloud_flag_passes_gate(self, async_client: AsyncClient, db_session: AsyncSession):
        """Owner + can_access_cloud=True + owner has cloud_token → /cloud/status
        returns 200. Token verification with Bambu happens further downstream
        and is mocked — we only assert the gate let the request through."""
        await _setup_auth_with_admin(async_client)
        admin = await _store_admin_cloud_token(db_session, "cloudadmin", token="fake-bambu-token")

        full_key, key_hash, key_prefix = generate_api_key()
        owned = APIKey(
            name="cloud-reader",
            key_hash=key_hash,
            key_prefix=key_prefix,
            user_id=admin.id,
            can_access_cloud=True,
        )
        db_session.add(owned)
        await db_session.commit()

        # /cloud/status reads token presence from the user record — no upstream
        # HTTP call, so we can assert directly on the response shape.
        resp = await async_client.get(
            "/api/v1/cloud/status",
            headers={"X-API-Key": full_key},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code} with body {resp.json()}"
        body = resp.json()
        # The gate let us through and the route resolved the owner's token —
        # status route reports token presence regardless of upstream availability.
        assert body.get("authenticated") is True or body.get("token_present") is True or "email" in body

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_jwt_caller_unaffected_by_api_key_gate(self, async_client: AsyncClient, db_session: AsyncSession):
        """The router-level gate must be a no-op for JWT callers — they're
        already gated by Permission.CLOUD_AUTH on the user record."""
        admin_token = await _setup_auth_with_admin(async_client)
        await _store_admin_cloud_token(db_session, "cloudadmin", token="fake-bambu-token")

        resp = await async_client.get(
            "/api/v1/cloud/status",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200


class TestOwnerDeletionCleanup:
    """Deleting the owner User must drop their API keys — orphan keys that
    point at a vanished user are a security hazard. The model declares
    ON DELETE CASCADE (Postgres enforces it), but SQLite ships with FK
    enforcement off, so the user-delete route also runs an explicit
    ``DELETE FROM api_keys WHERE user_id = ?`` for cross-backend safety.
    This test pins the route's behaviour."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_deleting_owner_removes_their_api_keys(self, async_client: AsyncClient, db_session: AsyncSession):
        # Set up: admin + a victim user + an API key owned by the victim.
        await _setup_auth_with_admin(async_client)
        admin_login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "cloudadmin", "password": "AdminPass1!"},
        )
        admin_token = admin_login.json()["access_token"]

        victim = User(
            username="cascade-victim",
            password_hash="x",
            role="user",
            is_active=True,
        )
        db_session.add(victim)
        await db_session.commit()
        await db_session.refresh(victim)

        _full_key, key_hash, key_prefix = generate_api_key()
        owned = APIKey(
            name="owned-by-victim",
            key_hash=key_hash,
            key_prefix=key_prefix,
            user_id=victim.id,
        )
        db_session.add(owned)
        await db_session.commit()
        key_id = owned.id
        victim_id = victim.id

        # Act: admin deletes the victim user via the API.
        del_resp = await async_client.delete(
            f"/api/v1/users/{victim_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert del_resp.status_code in (200, 204), f"User delete failed: {del_resp.status_code} {del_resp.json()}"

        # Assert: the API key is gone. Refresh session state — the route
        # commits via its own session, so our session needs to re-read.
        db_session.expire_all()
        result = await db_session.execute(select(APIKey).where(APIKey.id == key_id))
        assert result.scalar_one_or_none() is None, "API key should have been removed when its owner was deleted"
