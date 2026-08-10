"""GET /api/v1/users/slim -- the id -> username mapping for API clients (#1894).

An API key could already read global archive stats and filter them by
``created_by_id`` (for API-keyed requests the permission deps return None as
``current_user``, so the ``stats:filter_by_user`` guard short-circuits), but
had no way to discover which id belonged to whom: the full listing is gated on
``users:read``, which is unmapped in the API-key scope allowlist and therefore
administrative.

The slim listing closes that gap without handing keys the full user objects.
These tests pin both halves: that it answers for a key, and that it stays
narrow while the full listing stays admin-only.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core.auth import generate_api_key
from backend.app.models.api_key import APIKey
from backend.app.models.group import Group
from backend.app.models.user import User


async def _setup_and_login(async_client: AsyncClient) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "slimadmin", "admin_password": "SlimPass1!"},
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "slimadmin", "password": "SlimPass1!"},
    )
    return login.json()["access_token"]


async def _add_key(db_session, *, user_id: int | None = None, **scopes) -> str:
    full_key, key_hash, key_prefix = generate_api_key()
    db_session.add(
        APIKey(name="slim-test", key_hash=key_hash, key_prefix=key_prefix, enabled=True, user_id=user_id, **scopes)
    )
    await db_session.commit()
    return full_key


async def _add_user(db_session, username: str, **kwargs) -> User:
    from backend.app.core.auth import get_password_hash

    user = User(
        username=username,
        password_hash=get_password_hash("Whatever1!"),
        email=f"{username}@example.invalid",
        role="user",
        is_active=True,
        **kwargs,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.mark.asyncio
@pytest.mark.integration
async def test_slim_returns_only_id_and_username(async_client: AsyncClient, db_session):
    """The response shape is the contract -- no emails, roles, or permissions."""
    token = await _setup_and_login(async_client)
    await _add_user(db_session, "bob")

    response = await async_client.get("/api/v1/users/slim", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    rows = response.json()
    assert rows, "expected at least the admin created by setup"
    for row in rows:
        assert set(row) == {"id", "username"}
    assert "bob" in [row["username"] for row in rows]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_slim_is_reachable_with_an_api_key(async_client: AsyncClient, db_session):
    """The point of the issue: a key can resolve the ids it already filters on."""
    await _setup_and_login(async_client)
    owner = (await db_session.execute(select(User).where(User.username == "slimadmin"))).scalar_one()
    full_key = await _add_key(db_session, user_id=owner.id, can_read_status=True)

    header = await async_client.get("/api/v1/users/slim", headers={"X-API-Key": full_key})
    bearer = await async_client.get("/api/v1/users/slim", headers={"Authorization": f"Bearer {full_key}"})

    assert header.status_code == 200
    assert bearer.status_code == 200
    assert {row["username"] for row in header.json()} == {"slimadmin"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_slim_needs_can_read_status(async_client: AsyncClient, db_session):
    """A key without the read scope gets nothing, same as any other read route."""
    await _setup_and_login(async_client)
    owner = (await db_session.execute(select(User).where(User.username == "slimadmin"))).scalar_one()
    full_key = await _add_key(db_session, user_id=owner.id, can_read_status=False)

    response = await async_client.get("/api/v1/users/slim", headers={"X-API-Key": full_key})

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.integration
async def test_full_listing_stays_admin_only_for_api_keys(async_client: AsyncClient, db_session):
    """Regression guard: widening the slim route must not widen the full one.

    ``users:read`` returns emails, group membership and the complete permission
    set for every account. It has to stay unmapped in the scope allowlist.
    """
    await _setup_and_login(async_client)
    owner = (await db_session.execute(select(User).where(User.username == "slimadmin"))).scalar_one()
    full_key = await _add_key(db_session, user_id=owner.id, can_read_status=True)

    response = await async_client.get("/api/v1/users", headers={"X-API-Key": full_key})

    assert response.status_code == 403
    assert "administrative" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_slim_is_not_parsed_as_a_user_id(async_client: AsyncClient, db_session):
    """Route ordering. Declared after /{user_id}, "slim" would 422 as an int."""
    token = await _setup_and_login(async_client)

    response = await async_client.get("/api/v1/users/slim", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code != 422


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_group_with_only_users_read_still_reaches_slim(async_client: AsyncClient, db_session):
    """``users:read`` is strictly broader, so it must pass the any-of gate.

    Without this, every existing custom group holding ``users:read`` would need
    a permission backfill before the frontend could ever move to this route.
    """
    await _setup_and_login(async_client)
    group = Group(name="readers", description="t", permissions=["users:read"], is_system=False)
    db_session.add(group)
    await db_session.flush()
    await _add_user(db_session, "reader", groups=[group])

    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "reader", "password": "Whatever1!"},
    )
    token = login.json()["access_token"]

    response = await async_client.get("/api/v1/users/slim", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_group_with_only_slim_cannot_read_the_full_listing(async_client: AsyncClient, db_session):
    """The narrow grant has to actually be narrower for JWT users too."""
    await _setup_and_login(async_client)
    group = Group(name="slim-only", description="t", permissions=["users:read_slim"], is_system=False)
    db_session.add(group)
    await db_session.flush()
    await _add_user(db_session, "slimonly", groups=[group])

    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "slimonly", "password": "Whatever1!"},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert (await async_client.get("/api/v1/users/slim", headers=headers)).status_code == 200
    assert (await async_client.get("/api/v1/users", headers=headers)).status_code == 403
