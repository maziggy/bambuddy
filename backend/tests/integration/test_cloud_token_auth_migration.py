"""Cloud-credential migration across the auth on/off boundary (#2530).

``get_stored_token`` reads global ``Settings`` rows when auth is disabled and
``User.cloud_token`` when it's enabled. Toggling auth therefore switches which
store the ``/cloud/*`` routes consult. Without an explicit hand-off the token
is stranded in the store nobody reads: ``build_authenticated_cloud`` returns
``None``, Phase 2 of ``get_filament_info`` is skipped entirely, and the caller
sees a ``200`` full of local-preset fallbacks with no sign the cloud was never
contacted. That silent degradation is what #2530 actually reported.

These tests pin the hand-off in both directions, and — just as importantly —
pin the two cases where Bambuddy must refuse to guess who owns a credential.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cloud import (
    CLOUD_EMAIL_KEY,
    CLOUD_REGION_KEY,
    CLOUD_TOKEN_KEY,
    get_stored_token,
)
from backend.app.core.auth import get_password_hash
from backend.app.models.settings import Settings
from backend.app.models.user import User


async def _seed_global_token(db: AsyncSession, token: str = "tok-global", region: str = "china") -> None:
    db.add(Settings(key=CLOUD_TOKEN_KEY, value=token))
    db.add(Settings(key=CLOUD_EMAIL_KEY, value="owner@example.com"))
    db.add(Settings(key=CLOUD_REGION_KEY, value=region))
    await db.commit()


async def _global_rows(db: AsyncSession) -> dict[str, str]:
    rows = (
        (
            await db.execute(
                select(Settings).where(Settings.key.in_([CLOUD_TOKEN_KEY, CLOUD_EMAIL_KEY, CLOUD_REGION_KEY]))
            )
        )
        .scalars()
        .all()
    )
    return {r.key: r.value for r in rows}


async def _make_admin(db: AsyncSession, username: str) -> User:
    user = User(
        username=username,
        password_hash=get_password_hash("AdminPass1!"),
        role="admin",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# auth OFF -> ON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_migrates_global_token_to_created_admin(async_client: AsyncClient, db_session: AsyncSession):
    """The reporter's exact path: link cloud with auth off, then enable auth."""
    await _seed_global_token(db_session)

    resp = await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "admin", "admin_password": "AdminPass1!"},
    )
    assert resp.status_code == 200, resp.text

    admin = (await db_session.execute(select(User).where(User.role == "admin"))).scalar_one()
    token, email, region = await get_stored_token(db_session, admin)
    assert token == "tok-global"
    assert email == "owner@example.com"
    assert region == "china", "region must survive the hop, not silently reset to global"

    # Credential must not be left at rest in a table nothing reads any more.
    assert await _global_rows(db_session) == {}


@pytest.mark.asyncio
async def test_setup_migrates_to_sole_pre_existing_admin(async_client: AsyncClient, db_session: AsyncSession):
    """Re-enabling auth when exactly one admin already exists has one obvious owner."""
    admin = await _make_admin(db_session, "solo")
    await _seed_global_token(db_session, token="tok-solo")

    resp = await async_client.post("/api/v1/auth/setup", json={"auth_enabled": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["admin_created"] is False

    await db_session.refresh(admin)
    token, _, _ = await get_stored_token(db_session, admin)
    assert token == "tok-solo"
    assert await _global_rows(db_session) == {}


@pytest.mark.asyncio
async def test_setup_refuses_to_guess_owner_when_multiple_admins(async_client: AsyncClient, db_session: AsyncSession):
    """Two admins, one credential: handing it to either is a security decision we don't make."""
    a = await _make_admin(db_session, "admin_a")
    b = await _make_admin(db_session, "admin_b")
    await _seed_global_token(db_session, token="tok-ambiguous")

    resp = await async_client.post("/api/v1/auth/setup", json={"auth_enabled": True})
    assert resp.status_code == 200, resp.text

    await db_session.refresh(a)
    await db_session.refresh(b)
    assert a.cloud_token is None
    assert b.cloud_token is None
    # Left intact so the operator can re-link rather than lose it.
    assert (await _global_rows(db_session))[CLOUD_TOKEN_KEY] == "tok-ambiguous"


@pytest.mark.asyncio
async def test_setup_with_auth_disabled_leaves_global_token_untouched(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Completing setup while declining auth must not move anything."""
    await _seed_global_token(db_session, token="tok-stay")

    resp = await async_client.post("/api/v1/auth/setup", json={"auth_enabled": False})
    assert resp.status_code == 200, resp.text

    assert (await _global_rows(db_session))[CLOUD_TOKEN_KEY] == "tok-stay"


# ---------------------------------------------------------------------------
# auth ON -> OFF
# ---------------------------------------------------------------------------


async def _admin_bearer(async_client: AsyncClient, username: str = "admin") -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": username, "admin_password": "AdminPass1!"},
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "AdminPass1!"},
    )
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_disable_auth_migrates_admin_token_to_global(async_client: AsyncClient, db_session: AsyncSession):
    bearer = await _admin_bearer(async_client)
    admin = (await db_session.execute(select(User).where(User.role == "admin"))).scalar_one()
    admin.cloud_token = "tok-user"
    admin.cloud_email = "user@example.com"
    admin.cloud_region = "china"
    await db_session.commit()

    resp = await async_client.post("/api/v1/auth/disable", headers={"Authorization": f"Bearer {bearer}"})
    assert resp.status_code == 200, resp.text

    rows = await _global_rows(db_session)
    assert rows[CLOUD_TOKEN_KEY] == "tok-user"
    assert rows[CLOUD_REGION_KEY] == "china"

    await db_session.refresh(admin)
    assert admin.cloud_token is None, "credential must not be duplicated across both stores"

    # And the no-auth read path now finds it.
    token, _, _ = await get_stored_token(db_session, None)
    assert token == "tok-user"


@pytest.mark.asyncio
async def test_disable_auth_does_not_clobber_existing_global_token(async_client: AsyncClient, db_session: AsyncSession):
    """A stale global row is still somebody's credential — refuse rather than overwrite."""
    bearer = await _admin_bearer(async_client)
    admin = (await db_session.execute(select(User).where(User.role == "admin"))).scalar_one()
    admin.cloud_token = "tok-user"
    await db_session.commit()
    await _seed_global_token(db_session, token="tok-preexisting")

    resp = await async_client.post("/api/v1/auth/disable", headers={"Authorization": f"Bearer {bearer}"})
    assert resp.status_code == 200, resp.text

    assert (await _global_rows(db_session))[CLOUD_TOKEN_KEY] == "tok-preexisting"
    await db_session.refresh(admin)
    assert admin.cloud_token == "tok-user", "admin keeps their token when we decline to migrate"


@pytest.mark.asyncio
async def test_disable_auth_with_no_cloud_token_is_a_noop(async_client: AsyncClient, db_session: AsyncSession):
    bearer = await _admin_bearer(async_client)

    resp = await async_client.post("/api/v1/auth/disable", headers={"Authorization": f"Bearer {bearer}"})
    assert resp.status_code == 200, resp.text
    assert await _global_rows(db_session) == {}
