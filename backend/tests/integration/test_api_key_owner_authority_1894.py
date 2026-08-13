"""An API key must not out-rank the user it belongs to (#1894 follow-on).

``_check_apikey_permissions`` gated purely on the scope flags stored on the key
row and never looked at the owner. Scope flags are chosen at creation time by
whoever holds ``api_keys:create`` -- admin-only in the default groups, but a
custom group can grant it -- so a user with, say, queue permissions could mint
themselves a key with ``can_control_printer`` and stop other people's prints
through it. Deactivating that user did not help either: their keys kept working
with full scope authority, because nothing re-checked the owner.

The gate now narrows the scope flags to what the owner may do. Two cases must
NOT be conflated, and each has a test below:

- ``user_id IS NULL`` -- legacy key from before per-user ownership. No owner
  exists to narrow against, so the flags stand alone and the key keeps working.
- ``user_id`` set but the row is gone or deactivated -- the key's authority came
  from a user who has none. Fails closed.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core.auth import generate_api_key, get_password_hash
from backend.app.models.api_key import APIKey
from backend.app.models.group import Group
from backend.app.models.user import User

# A route gated on PRINTERS_READ (can_read_status) and one gated on
# PRINTERS_CONTROL (can_control_printer). Both scope flags are set on every key
# built below, so any denial comes from the owner check rather than the flags.
READ_ROUTE = "/api/v1/printers/"
CONTROL_ROUTE = "/api/v1/printers/1/print/stop"


async def _setup(async_client: AsyncClient) -> None:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "owneradmin", "admin_password": "OwnerPass1!"},
    )


async def _key_for(db_session, owner: User | None, **scopes) -> str:
    defaults = {"can_read_status": True, "can_control_printer": True, "can_queue": True}
    defaults.update(scopes)
    full_key, key_hash, key_prefix = generate_api_key()
    db_session.add(
        APIKey(
            name=f"key-{owner.username if owner else 'legacy'}",
            key_hash=key_hash,
            key_prefix=key_prefix,
            enabled=True,
            user_id=owner.id if owner else None,
            **defaults,
        )
    )
    await db_session.commit()
    return full_key


async def _user(db_session, username: str, permissions: list[str], *, is_active: bool = True) -> User:
    group = Group(name=f"grp-{username}", description="t", permissions=permissions, is_system=False)
    db_session.add(group)
    await db_session.flush()
    user = User(
        username=username,
        password_hash=get_password_hash("Whatever1!"),  # noqa: S106
        role="user",
        is_active=is_active,
        groups=[group],
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_owned_key_keeps_full_scope_authority(async_client: AsyncClient, db_session):
    """The common case must not regress -- almost every key is admin-owned."""
    await _setup(async_client)
    admin = (await db_session.execute(select(User).where(User.username == "owneradmin"))).scalar_one()
    key = await _key_for(db_session, admin)

    response = await async_client.get(READ_ROUTE, headers={"X-API-Key": key})

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_legacy_ownerless_key_still_works(async_client: AsyncClient, db_session):
    """No owner to narrow against is not the same as a failed owner lookup."""
    await _setup(async_client)
    key = await _key_for(db_session, None)

    response = await async_client.get(READ_ROUTE, headers={"X-API-Key": key})

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_key_cannot_exceed_its_owners_permissions(async_client: AsyncClient, db_session):
    """The escalation: control flags ticked, owner who may not control."""
    await _setup(async_client)
    owner = await _user(db_session, "readonly", ["printers:read"])
    key = await _key_for(db_session, owner)

    allowed = await async_client.get(READ_ROUTE, headers={"X-API-Key": key})
    denied = await async_client.post(CONTROL_ROUTE, headers={"X-API-Key": key})

    assert allowed.status_code == 200
    assert denied.status_code == 403
    assert "owner does not have" in denied.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_deactivating_the_owner_disables_the_key(async_client: AsyncClient, db_session):
    """Previously the key kept working -- nothing re-checked the owner."""
    await _setup(async_client)
    owner = await _user(db_session, "gone", ["printers:read"], is_active=False)
    key = await _key_for(db_session, owner)

    response = await async_client.get(READ_ROUTE, headers={"X-API-Key": key})

    assert response.status_code == 403
    assert "deactivated" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_deleted_owner_does_not_fall_back_to_anonymous(async_client: AsyncClient, db_session):
    """The dangling-row case fails closed rather than reverting to flags-only.

    CASCADE should prevent this, but "should" is not a gate -- if the row is
    ever orphaned the key must not silently regain full scope authority.
    """
    await _setup(async_client)
    owner = await _user(db_session, "doomed", ["printers:read"])
    key = await _key_for(db_session, owner)
    api_key = (await db_session.execute(select(APIKey).where(APIKey.user_id == owner.id))).scalar_one()
    api_key.user_id = 999999  # owner row that does not exist
    await db_session.commit()

    response = await async_client.get(READ_ROUTE, headers={"X-API-Key": key})

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bearer_path_is_gated_the_same_as_the_header(async_client: AsyncClient, db_session):
    """Both credential paths run the same gate; only one was ever tested."""
    await _setup(async_client)
    owner = await _user(db_session, "bearer-readonly", ["printers:read"])
    key = await _key_for(db_session, owner)

    denied = await async_client.post(CONTROL_ROUTE, headers={"Authorization": f"Bearer {key}"})

    assert denied.status_code == 403


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_routes_are_not_a_way_around_the_owner_check(async_client: AsyncClient, db_session):
    """/webhook/* reaches its scope flags by a different route than the rest.

    It gates on ``check_permission``, not ``_check_apikey_permissions``, so it
    does not inherit the owner narrowing for free. If it is missed, the same key
    that is refused on /printers/{id}/print/stop simply stops the print here
    instead, and the whole gate is decorative.
    """
    await _setup(async_client)
    owner = await _user(db_session, "webhook-readonly", ["printers:read"])
    key = await _key_for(db_session, owner)

    denied = await async_client.post("/api/v1/webhook/printer/1/stop", headers={"X-API-Key": key})

    assert denied.status_code == 403
    assert "owner does not have" in denied.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_still_works_for_a_permitted_owner(async_client: AsyncClient, db_session):
    """The narrowing must not simply break every webhook caller."""
    await _setup(async_client)
    owner = await _user(db_session, "webhook-operator", ["printers:read", "printers:control"])
    key = await _key_for(db_session, owner)

    response = await async_client.post("/api/v1/webhook/printer/1/stop", headers={"X-API-Key": key})

    # There is no connected printer 1, so the handler itself fails. What
    # matters is that the request got that far: neither gate rejected it.
    assert response.status_code != 403


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_rejects_a_deactivated_owner(async_client: AsyncClient, db_session):
    """Fail-closed applies on this path too."""
    await _setup(async_client)
    owner = await _user(db_session, "webhook-gone", ["printers:read", "printers:control"], is_active=False)
    key = await _key_for(db_session, owner)

    response = await async_client.post("/api/v1/webhook/printer/1/stop", headers={"X-API-Key": key})

    assert response.status_code == 403
    assert "deactivated" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_me_reports_the_narrowed_set(async_client: AsyncClient, db_session):
    """/auth/me and the gate must agree, including about the owner."""
    await _setup(async_client)
    owner = await _user(db_session, "narrow", ["printers:read"])
    key = await _key_for(db_session, owner)

    result = (await async_client.get("/api/v1/auth/me", headers={"X-API-Key": key})).json()

    assert result["id"] == owner.id
    assert "printers:read" in result["permissions"]
    # can_control_printer is ticked on the key, but the owner cannot control.
    assert "printers:control" not in result["permissions"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_me_is_rejected_once_the_owner_is_deactivated(async_client: AsyncClient, db_session):
    """A dead key identifies as nothing, rather than as an anonymous key."""
    await _setup(async_client)
    owner = await _user(db_session, "me-gone", ["printers:read"], is_active=False)
    key = await _key_for(db_session, owner)

    response = await async_client.get("/api/v1/auth/me", headers={"X-API-Key": key})

    assert response.status_code == 403
