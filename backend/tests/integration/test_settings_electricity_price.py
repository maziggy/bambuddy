"""Integration tests for #1356 — API keys writing electricity price.

The contract these tests pin:

  ``POST /settings/electricity-price`` is the *only* settings field writable
  via API key, gated by an opt-in ``can_update_energy_cost`` scope. Full
  ``PATCH /settings`` remains denied for API keys because it can rewrite
  SMTP/LDAP/MQTT credentials. Two independent fences must pass:

    1. Caller is a JWT user with SETTINGS_UPDATE permission, OR
    2. Caller is an API key with ``can_update_energy_cost = True``.

  Tests also confirm: (a) API keys without the flag get 403 with a
  recognizable error, (b) the deny-list for ``PATCH /settings`` still fires
  for keys that flipped only ``can_update_energy_cost`` on, so flipping the
  narrow flag doesn't accidentally widen settings-write capability.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import generate_api_key
from backend.app.models.api_key import APIKey
from backend.app.models.settings import Settings
from backend.app.models.user import User


async def _setup_auth_with_admin(client: AsyncClient) -> str:
    """Enable auth + return an admin bearer token. Same pattern as #1182 tests."""
    await client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": "energyadmin",
            "admin_password": "AdminPass1!",  # pragma: allowlist secret
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "energyadmin", "password": "AdminPass1!"},  # pragma: allowlist secret
    )
    return login.json()["access_token"]


async def _make_api_key(
    db: AsyncSession,
    *,
    owner_id: int | None,
    can_update_energy_cost: bool,
) -> str:
    full_key, key_hash, key_prefix = generate_api_key()
    api_key = APIKey(
        name="energy-tariff",
        key_hash=key_hash,
        key_prefix=key_prefix,
        user_id=owner_id,
        can_update_energy_cost=can_update_energy_cost,
    )
    db.add(api_key)
    await db.commit()
    return full_key


async def _read_setting(db: AsyncSession, key: str) -> str | None:
    result = await db.execute(select(Settings).where(Settings.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else None


class TestCreateAPIKeyWithEnergyScope:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_stamps_energy_flag(self, async_client: AsyncClient):
        token = await _setup_auth_with_admin(async_client)
        resp = await async_client.post(
            "/api/v1/api-keys/",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "tariff-push", "can_update_energy_cost": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["can_update_energy_cost"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_without_flag_defaults_off(self, async_client: AsyncClient):
        token = await _setup_auth_with_admin(async_client)
        resp = await async_client.post(
            "/api/v1/api-keys/",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "no-energy"},
        )
        assert resp.status_code == 200
        assert resp.json()["can_update_energy_cost"] is False


class TestElectricityPriceEndpoint:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_api_key_with_flag_updates_price(self, async_client: AsyncClient, db_session: AsyncSession):
        """Happy path: API key with ``can_update_energy_cost=True`` POSTs a new
        price and the setting persists."""
        await _setup_auth_with_admin(async_client)
        result = await db_session.execute(select(User).where(User.username == "energyadmin"))
        admin = result.scalar_one()
        full_key = await _make_api_key(db_session, owner_id=admin.id, can_update_energy_cost=True)

        resp = await async_client.post(
            "/api/v1/settings/electricity-price",
            headers={"X-API-Key": full_key},
            json={"energy_cost_per_kwh": 0.42},
        )
        assert resp.status_code == 200, resp.json()
        # The route returns the full settings response — confirm the new value
        # is reflected (the rest of the body is the standard scrubbed response).
        assert resp.json()["energy_cost_per_kwh"] == 0.42

        # Persisted in the settings table.
        db_session.expire_all()
        assert await _read_setting(db_session, "energy_cost_per_kwh") == "0.42"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_api_key_without_flag_rejected(self, async_client: AsyncClient, db_session: AsyncSession):
        """Default API key (can_update_energy_cost=False) → 403."""
        await _setup_auth_with_admin(async_client)
        result = await db_session.execute(select(User).where(User.username == "energyadmin"))
        admin = result.scalar_one()
        full_key = await _make_api_key(db_session, owner_id=admin.id, can_update_energy_cost=False)

        resp = await async_client.post(
            "/api/v1/settings/electricity-price",
            headers={"X-API-Key": full_key},
            json={"energy_cost_per_kwh": 0.42},
        )
        assert resp.status_code == 403
        # Don't pin the exact detail string — just that it identifies the
        # missing permission. Keeps the test from being noise on copy tweaks.
        assert "energy" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_user_with_settings_update_allowed(self, async_client: AsyncClient, db_session: AsyncSession):
        """JWT user with SETTINGS_UPDATE permission can still hit this route."""
        token = await _setup_auth_with_admin(async_client)
        resp = await async_client.post(
            "/api/v1/settings/electricity-price",
            headers={"Authorization": f"Bearer {token}"},
            json={"energy_cost_per_kwh": 0.19},
        )
        assert resp.status_code == 200
        assert resp.json()["energy_cost_per_kwh"] == 0.19

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unauthenticated_rejected(self, async_client: AsyncClient):
        """No credentials when auth is enabled → 401."""
        await _setup_auth_with_admin(async_client)
        resp = await async_client.post(
            "/api/v1/settings/electricity-price",
            json={"energy_cost_per_kwh": 0.19},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_negative_price_rejected(self, async_client: AsyncClient, db_session: AsyncSession):
        """The Pydantic ``ge=0`` constraint catches obviously-wrong values
        before they reach the settings table — a negative tariff is never
        valid in any real market."""
        await _setup_auth_with_admin(async_client)
        result = await db_session.execute(select(User).where(User.username == "energyadmin"))
        admin = result.scalar_one()
        full_key = await _make_api_key(db_session, owner_id=admin.id, can_update_energy_cost=True)

        resp = await async_client.post(
            "/api/v1/settings/electricity-price",
            headers={"X-API-Key": full_key},
            json={"energy_cost_per_kwh": -0.05},
        )
        assert resp.status_code == 422  # FastAPI validation


class TestPatchSettingsStillBlocked:
    """Regression guard: flipping the narrow energy-cost flag must NOT widen
    full ``PATCH /settings`` access. The general settings-update deny for
    API keys (which protects SMTP/LDAP/MQTT credentials) stays in place."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_settings_still_denied_with_energy_flag(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        await _setup_auth_with_admin(async_client)
        result = await db_session.execute(select(User).where(User.username == "energyadmin"))
        admin = result.scalar_one()
        full_key = await _make_api_key(db_session, owner_id=admin.id, can_update_energy_cost=True)

        resp = await async_client.patch(
            "/api/v1/settings/",
            headers={"X-API-Key": full_key},
            json={"energy_cost_per_kwh": 0.99},
        )
        # Still denied — the wider route uses the deny-list path.
        assert resp.status_code == 403
        assert "administrative" in resp.json()["detail"].lower()
