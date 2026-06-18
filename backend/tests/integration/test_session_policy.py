"""Integration tests for the admin-set session-lifetime ceiling (#1706).

Covers the four token-issuance sites that read ``session_max_hours``:
plain login, 2FA backup-code login, 2FA TOTP/email login, OIDC login.
Only the first is exercised end-to-end via ``async_client``; the helper
``resolve_session_max_minutes`` itself is unit-tested below so the MFA
and OIDC paths inherit the same clamping behaviour by construction.
"""

import time

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    SECRET_KEY,
    SESSION_MAX_HOURS_HARD_CEILING,
    resolve_session_max_minutes,
)
from backend.app.models.settings import Settings


async def _set_session_max_hours(db: AsyncSession, value: str | None) -> None:
    """Upsert the session_max_hours setting row (value=None deletes it)."""
    result = await db.execute(select(Settings).where(Settings.key == "session_max_hours"))
    existing = result.scalar_one_or_none()
    if value is None:
        if existing is not None:
            await db.delete(existing)
            await db.commit()
        return
    if existing is None:
        db.add(Settings(key="session_max_hours", value=value))
    else:
        existing.value = value
    await db.commit()


class TestResolveSessionMaxMinutes:
    """Unit-style tests for the clamping resolver."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_missing_row_returns_24h_default(self, db_session: AsyncSession):
        await _set_session_max_hours(db_session, None)
        assert await resolve_session_max_minutes(db_session) == ACCESS_TOKEN_EXPIRE_MINUTES
        assert ACCESS_TOKEN_EXPIRE_MINUTES == 60 * 24

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_empty_string_returns_24h_default(self, db_session: AsyncSession):
        await _set_session_max_hours(db_session, "")
        assert await resolve_session_max_minutes(db_session) == ACCESS_TOKEN_EXPIRE_MINUTES

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unparseable_value_returns_24h_default(self, db_session: AsyncSession):
        await _set_session_max_hours(db_session, "not-a-number")
        assert await resolve_session_max_minutes(db_session) == ACCESS_TOKEN_EXPIRE_MINUTES

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_zero_or_negative_returns_24h_default(self, db_session: AsyncSession):
        await _set_session_max_hours(db_session, "0")
        assert await resolve_session_max_minutes(db_session) == ACCESS_TOKEN_EXPIRE_MINUTES
        await _set_session_max_hours(db_session, "-5")
        assert await resolve_session_max_minutes(db_session) == ACCESS_TOKEN_EXPIRE_MINUTES

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_one_hour_minimum(self, db_session: AsyncSession):
        await _set_session_max_hours(db_session, "1")
        assert await resolve_session_max_minutes(db_session) == 60

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_seven_days_passes_through(self, db_session: AsyncSession):
        await _set_session_max_hours(db_session, "168")
        assert await resolve_session_max_minutes(db_session) == 168 * 60

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_thirty_days_passes_through(self, db_session: AsyncSession):
        await _set_session_max_hours(db_session, str(SESSION_MAX_HOURS_HARD_CEILING))
        assert await resolve_session_max_minutes(db_session) == SESSION_MAX_HOURS_HARD_CEILING * 60

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_above_ceiling_is_clamped_to_30_days(self, db_session: AsyncSession):
        """Defense-in-depth: a tampered settings row above 720h must be clamped."""
        await _set_session_max_hours(db_session, "99999")
        assert await resolve_session_max_minutes(db_session) == SESSION_MAX_HOURS_HARD_CEILING * 60


class TestLoginRespectsSessionPolicy:
    """The /auth/login route must honour the resolved ceiling."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_login_uses_default_24h_when_unset(self, async_client: AsyncClient, db_session: AsyncSession):
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "sessiontest1",
                "admin_password": "SessionPass1!",
            },
        )
        await _set_session_max_hours(db_session, None)

        before = int(time.time())
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "sessiontest1", "password": "SessionPass1!"},
        )
        after = int(time.time())

        assert response.status_code == 200
        token = response.json()["access_token"]
        decoded = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # exp should be ~24h ahead. Allow generous bounds for clock drift.
        expected_min = before + 24 * 3600 - 60
        expected_max = after + 24 * 3600 + 60
        assert expected_min <= decoded["exp"] <= expected_max

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_login_uses_configured_7d_ceiling(self, async_client: AsyncClient, db_session: AsyncSession):
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "sessiontest2",
                "admin_password": "SessionPass2!",
            },
        )
        await _set_session_max_hours(db_session, "168")  # 7 days

        before = int(time.time())
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "sessiontest2", "password": "SessionPass2!"},
        )
        after = int(time.time())

        assert response.status_code == 200
        token = response.json()["access_token"]
        decoded = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        expected_min = before + 168 * 3600 - 60
        expected_max = after + 168 * 3600 + 60
        assert expected_min <= decoded["exp"] <= expected_max

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_login_clamps_above_ceiling(self, async_client: AsyncClient, db_session: AsyncSession):
        """A settings row above the 720h ceiling must be clamped at login time."""
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "sessiontest3",
                "admin_password": "SessionPass3!",
            },
        )
        await _set_session_max_hours(db_session, "5000")  # would be ~208 days

        before = int(time.time())
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "sessiontest3", "password": "SessionPass3!"},
        )
        after = int(time.time())

        assert response.status_code == 200
        token = response.json()["access_token"]
        decoded = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # Clamped to 30 days, not 5000 hours.
        expected_min = before + SESSION_MAX_HOURS_HARD_CEILING * 3600 - 60
        expected_max = after + SESSION_MAX_HOURS_HARD_CEILING * 3600 + 60
        assert expected_min <= decoded["exp"] <= expected_max


class TestSettingsAPIExposesSessionMaxHours:
    """The /settings API must round-trip session_max_hours as an int."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_default_is_24(self, async_client: AsyncClient, db_session: AsyncSession):
        await _set_session_max_hours(db_session, None)
        response = await async_client.get("/api/v1/settings/")
        assert response.status_code == 200
        assert response.json()["session_max_hours"] == 24

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_accepts_valid_value(self, async_client: AsyncClient, db_session: AsyncSession):
        response = await async_client.patch(
            "/api/v1/settings/",
            json={"session_max_hours": 168},
        )
        assert response.status_code == 200
        assert response.json()["session_max_hours"] == 168
        # Persisted as the int's string form so the resolver round-trips.
        result = await db_session.execute(select(Settings).where(Settings.key == "session_max_hours"))
        row = result.scalar_one()
        assert row.value == "168"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_rejects_zero(self, async_client: AsyncClient):
        response = await async_client.patch(
            "/api/v1/settings/",
            json={"session_max_hours": 0},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_rejects_above_ceiling(self, async_client: AsyncClient):
        response = await async_client.patch(
            "/api/v1/settings/",
            json={"session_max_hours": SESSION_MAX_HOURS_HARD_CEILING + 1},
        )
        assert response.status_code == 422
