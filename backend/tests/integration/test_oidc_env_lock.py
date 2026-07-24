"""The env-managed provider is read-only through the API (#2593).

Startup rewrites this row from BAMBUDDY_OIDC_* on every boot, so a UI edit
would silently disappear at the next restart -- the operator would see their
change accepted and then reverted, with nothing explaining why. Refusing the
write is the honest answer.

Locking it out is safe because BAMBUDDY_LOCAL_LOGIN (#1589) is the documented
recovery path if the provider itself becomes unusable.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from backend.app.models.oidc_provider import OIDCProvider
from backend.tests.integration.test_mfa_api import _auth_header, _setup_and_login


async def _env_managed_provider(db_session) -> int:
    provider = OIDCProvider(
        name="Env Keycloak",
        issuer_url="https://sso.example.com/realms/main",
        client_id="bambuddy",
        icon_url="https://sso.example.com/logo.png",
        is_env_managed=True,
    )
    provider.client_secret = "s3cr3t"
    db_session.add(provider)
    await db_session.commit()
    await db_session.refresh(provider)
    return provider.id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_put_is_refused(async_client: AsyncClient, db_session):
    provider_id = await _env_managed_provider(db_session)
    token = await _setup_and_login(async_client, "envlockput", "envlockput123")

    response = await async_client.put(
        f"/api/v1/auth/oidc/providers/{provider_id}",
        json={"name": "hijacked"},
        headers=_auth_header(token),
    )

    assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_is_refused(async_client: AsyncClient, db_session):
    provider_id = await _env_managed_provider(db_session)
    token = await _setup_and_login(async_client, "envlockdel", "envlockdel123")

    response = await async_client.delete(
        f"/api/v1/auth/oidc/providers/{provider_id}",
        headers=_auth_header(token),
    )

    assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_icon_delete_is_refused(async_client: AsyncClient, db_session):
    """The icon is part of the env config too -- BAMBUDDY_OIDC_ICON_URL."""
    provider_id = await _env_managed_provider(db_session)
    token = await _setup_and_login(async_client, "envlockicondel", "envlockicondel123")

    response = await async_client.delete(
        f"/api/v1/auth/oidc/providers/{provider_id}/icon",
        headers=_auth_header(token),
    )

    assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_icon_refresh_is_refused(async_client: AsyncClient, db_session):
    provider_id = await _env_managed_provider(db_session)
    token = await _setup_and_login(async_client, "envlockiconref", "envlockiconref123")

    response = await async_client.post(
        f"/api/v1/auth/oidc/providers/{provider_id}/icon/refresh",
        headers=_auth_header(token),
    )

    assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_ui_provider_is_still_editable(async_client: AsyncClient):
    """The lock must not leak onto providers the operator created themselves --
    they coexist with the env one and stay fully editable."""
    token = await _setup_and_login(async_client, "envlockui", "envlockui123")
    created = await async_client.post(
        "/api/v1/auth/oidc/providers",
        json={
            "name": "UI provider",
            "issuer_url": "https://other.example.com",
            "client_id": "ui",
            "client_secret": "ui-secret",
            "scopes": "openid",
            "is_enabled": True,
            "auto_create_users": False,
        },
        headers=_auth_header(token),
    )
    provider_id = created.json()["id"]

    response = await async_client.put(
        f"/api/v1/auth/oidc/providers/{provider_id}",
        json={"name": "Renamed"},
        headers=_auth_header(token),
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_response_says_which_provider_is_env_managed(async_client: AsyncClient, db_session):
    """The frontend needs this to render the lock; without it the UI would show
    editable fields whose writes the API then refuses."""
    await _env_managed_provider(db_session)
    token = await _setup_and_login(async_client, "envlockflag", "envlockflag123")

    response = await async_client.get("/api/v1/auth/oidc/providers/all", headers=_auth_header(token))

    assert response.status_code == 200
    providers = response.json()
    assert any(p["is_env_managed"] for p in providers)
