"""Removing a linked SSO account never removes a user's last way to sign in.

A user Bambuddy created from an SSO login has no password, so their last
link stays; a user with a password, or with another link, can remove it.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core.auth import create_access_token, get_password_hash
from backend.app.models.oidc_provider import OIDCProvider, UserOIDCLink
from backend.app.models.user import User
from backend.tests.integration.test_mfa_api import _setup_and_login


async def _provider(db_session, name: str) -> int:
    provider = OIDCProvider(name=name, issuer_url=f"https://{name}.example.com", client_id="bambuddy")
    provider.client_secret = "provider-secret"
    db_session.add(provider)
    await db_session.commit()
    return provider.id


async def _user(db_session, *, oidc_only: bool, providers: list[int]) -> tuple[User, dict]:
    user = User(
        username="sso-user",
        password_hash=None if oidc_only else get_password_hash("Whatever1!"),
        auth_source="oidc" if oidc_only else "local",
        role="user",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    for i, provider_id in enumerate(providers):
        db_session.add(UserOIDCLink(user_id=user.id, provider_id=provider_id, provider_user_id=f"sub-{i}"))
    await db_session.commit()
    return user, {"Authorization": f"Bearer {create_access_token(data={'sub': user.username})}"}


async def _links(db_session, user_id: int) -> int:
    db_session.expire_all()
    return len((await db_session.execute(select(UserOIDCLink).where(UserOIDCLink.user_id == user_id))).all())


@pytest.mark.asyncio
async def test_the_last_link_of_a_user_without_password_stays(async_client: AsyncClient, db_session):
    await _setup_and_login(async_client, "ssoadmin", "SsoAdmin12!")
    provider = await _provider(db_session, "alpha")
    user, headers = await _user(db_session, oidc_only=True, providers=[provider])

    response = await async_client.delete(f"/api/v1/auth/oidc/links/{provider}", headers=headers)

    assert response.status_code == 400
    assert await _links(db_session, user.id) == 1


@pytest.mark.asyncio
async def test_a_user_without_password_can_remove_one_of_two_links(async_client: AsyncClient, db_session):
    await _setup_and_login(async_client, "ssoadmin", "SsoAdmin12!")
    first, second = await _provider(db_session, "alpha"), await _provider(db_session, "beta")
    user, headers = await _user(db_session, oidc_only=True, providers=[first, second])

    response = await async_client.delete(f"/api/v1/auth/oidc/links/{first}", headers=headers)

    assert response.status_code == 200, response.text
    assert await _links(db_session, user.id) == 1


@pytest.mark.asyncio
async def test_a_user_with_a_password_can_remove_the_last_link(async_client: AsyncClient, db_session):
    await _setup_and_login(async_client, "ssoadmin", "SsoAdmin12!")
    provider = await _provider(db_session, "alpha")
    user, headers = await _user(db_session, oidc_only=False, providers=[provider])

    response = await async_client.delete(f"/api/v1/auth/oidc/links/{provider}", headers=headers)

    assert response.status_code == 200, response.text
    assert await _links(db_session, user.id) == 0
