"""Moving a user out of Administrators takes their admin rights (#3533).

The setup admin is created with role "admin" and in Administrators, and
``is_admin`` honours either. Moving them to Operators changed only the groups,
the role kept them an admin, and the UI had no field for it. The groups decide
now and the role follows them, from every path that changes them.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.models.group import Group
from backend.app.models.settings import Settings
from backend.app.models.user import User


async def _groups(db_session) -> tuple[Group, Group]:
    """The seeded system groups, created when the database has none yet."""
    found = []
    for name in ("Administrators", "Operators"):
        group = await db_session.scalar(select(Group).where(Group.name == name))
        if group is None:
            group = Group(name=name, permissions=[], is_system=True)
            db_session.add(group)
            await db_session.commit()
        found.append(group)
    return found[0], found[1]


async def _user(db_session, name: str, role: str, groups: list[Group], active: bool = True) -> int:
    from backend.app.core.auth import get_password_hash

    user = User(username=name, password_hash=get_password_hash("Whatever1!"), role=role, is_active=active)
    user.groups = list(groups)
    db_session.add(user)
    await db_session.commit()
    return user.id


async def _load(db_session, user_id: int) -> User:
    db_session.expire_all()
    return (
        await db_session.execute(select(User).where(User.id == user_id).options(selectinload(User.groups)))
    ).scalar_one()


@pytest.mark.asyncio
async def test_moving_an_admin_to_operators_takes_the_admin_rights(async_client: AsyncClient, db_session):
    admins, operators = await _groups(db_session)
    await _user(db_session, "admin", "admin", [admins])
    demoted = await _user(db_session, "mike", "admin", [admins])

    # What the edit form sent: the new groups, and the old role back.
    response = await async_client.patch(f"/api/v1/users/{demoted}", json={"group_ids": [operators.id], "role": "admin"})

    assert response.status_code == 200, response.text
    assert response.json()["is_admin"] is False
    user = await _load(db_session, demoted)
    assert user.role == "user"
    assert not user.is_admin


@pytest.mark.asyncio
async def test_moving_a_user_into_administrators_makes_them_admin(async_client: AsyncClient, db_session):
    admins, operators = await _groups(db_session)
    promoted = await _user(db_session, "jane", "user", [operators])

    response = await async_client.patch(f"/api/v1/users/{promoted}", json={"group_ids": [admins.id]})

    assert response.status_code == 200, response.text
    assert (await _load(db_session, promoted)).role == "admin"


@pytest.mark.asyncio
async def test_role_alone_moves_the_user_out_of_administrators(async_client: AsyncClient, db_session):
    admins, operators = await _groups(db_session)
    await _user(db_session, "admin", "admin", [admins])
    demoted = await _user(db_session, "mike", "admin", [admins, operators])

    response = await async_client.patch(f"/api/v1/users/{demoted}", json={"role": "user"})

    assert response.status_code == 200, response.text
    user = await _load(db_session, demoted)
    assert [g.name for g in user.groups] == ["Operators"]
    assert not user.is_admin


@pytest.mark.asyncio
async def test_the_last_admin_cannot_be_moved_out_of_administrators(async_client: AsyncClient, db_session):
    admins, operators = await _groups(db_session)
    only = await _user(db_session, "admin", "admin", [admins])

    response = await async_client.patch(f"/api/v1/users/{only}", json={"group_ids": [operators.id]})

    assert response.status_code == 400
    assert "last admin" in response.json()["detail"]
    assert (await _load(db_session, only)).is_admin


@pytest.mark.asyncio
async def test_leaving_administrators_through_the_group_takes_the_admin_rights(async_client: AsyncClient, db_session):
    admins, _ = await _groups(db_session)
    await _user(db_session, "admin", "admin", [admins])
    demoted = await _user(db_session, "mike", "admin", [admins])

    response = await async_client.delete(f"/api/v1/groups/{admins.id}/users/{demoted}")

    assert response.status_code == 204, response.text
    user = await _load(db_session, demoted)
    assert user.role == "user"
    assert not user.is_admin


@pytest.mark.asyncio
async def test_the_last_admin_cannot_leave_administrators_through_the_group(async_client: AsyncClient, db_session):
    admins, _ = await _groups(db_session)
    only = await _user(db_session, "admin", "admin", [admins])

    response = await async_client.delete(f"/api/v1/groups/{admins.id}/users/{only}")

    assert response.status_code == 400
    assert (await _load(db_session, only)).is_admin


@pytest.mark.asyncio
async def test_joining_administrators_through_the_group_sets_the_role(async_client: AsyncClient, db_session):
    admins, operators = await _groups(db_session)
    promoted = await _user(db_session, "jane", "user", [operators])

    response = await async_client.post(f"/api/v1/groups/{admins.id}/users/{promoted}")

    assert response.status_code == 204, response.text
    assert (await _load(db_session, promoted)).role == "admin"


@pytest.mark.asyncio
async def test_a_new_user_gets_the_role_of_their_groups(async_client: AsyncClient, db_session):
    admins, operators = await _groups(db_session)

    as_operator = await async_client.post(
        "/api/v1/users", json={"username": "op", "password": "Whatever1!", "role": "admin", "group_ids": [operators.id]}
    )
    as_admin = await async_client.post(
        "/api/v1/users", json={"username": "ad", "password": "Whatever1!", "role": "admin"}
    )

    assert as_operator.status_code == 201, as_operator.text
    assert as_operator.json()["is_admin"] is False
    assert as_admin.status_code == 201, as_admin.text
    assert [g["name"] for g in as_admin.json()["groups"]] == ["Administrators"]


class TestBackfill:
    """Users demoted before the fix are lined up once at startup."""

    FLAG = "_backfill_3533_admin_role_follows_groups_done"

    @classmethod
    async def _rearm(cls, db_session):
        """The app's own startup in the fixture has already run it once."""
        from sqlalchemy import delete

        await db_session.execute(delete(Settings).where(Settings.key == cls.FLAG))
        await db_session.commit()

    @staticmethod
    async def _run(db_session):
        from backend.app.core.database import seed_default_groups

        await seed_default_groups()
        db_session.expire_all()

    @pytest.mark.asyncio
    async def test_a_demoted_admin_loses_the_admin_role(self, async_client: AsyncClient, db_session):
        admins, operators = await _groups(db_session)
        await _user(db_session, "admin", "admin", [admins])
        demoted = await _user(db_session, "mike", "admin", [operators])
        await self._rearm(db_session)

        await self._run(db_session)

        user = await _load(db_session, demoted)
        assert user.role == "user"
        assert not user.is_admin
        flag = await db_session.scalar(select(Settings.value).where(Settings.key == self.FLAG))
        assert flag == "true"

    @pytest.mark.asyncio
    async def test_the_only_admin_joins_administrators_instead(self, async_client: AsyncClient, db_session):
        _, operators = await _groups(db_session)
        only = await _user(db_session, "admin", "admin", [operators])
        await self._rearm(db_session)

        await self._run(db_session)

        user = await _load(db_session, only)
        assert user.is_admin
        assert "Administrators" in [g.name for g in user.groups]

    @pytest.mark.asyncio
    async def test_runs_once(self, async_client: AsyncClient, db_session):
        admins, operators = await _groups(db_session)
        await _user(db_session, "admin", "admin", [admins])
        await self._rearm(db_session)
        db_session.add(Settings(key=self.FLAG, value="true"))
        await db_session.commit()
        kept = await _user(db_session, "mike", "admin", [operators])

        await self._run(db_session)

        assert (await _load(db_session, kept)).role == "admin"


class TestIdentityProviderSync:
    """LDAP and OIDC logins set the groups too, so the role follows there."""

    @pytest.mark.asyncio
    async def test_an_ldap_login_that_drops_administrators_drops_the_role(self, db_session):
        from types import SimpleNamespace

        from backend.app.api.routes.auth import _sync_ldap_user

        admins, operators = await _groups(db_session)
        user_id = await _user(db_session, "alice", "admin", [admins])
        user = await _load(db_session, user_id)
        user.auth_source = "ldap"
        user.email = "alice@example.invalid"
        await db_session.commit()

        await _sync_ldap_user(
            db_session,
            user,
            SimpleNamespace(username="alice", email="alice@example.invalid", groups=["cn=ops"]),
            SimpleNamespace(group_mapping={"cn=admins": "Administrators", "cn=ops": "Operators"}, default_group=""),
        )

        user = await _load(db_session, user_id)
        assert [g.name for g in user.groups] == ["Operators"]
        assert user.role == "user"
        assert not user.is_admin

    @pytest.mark.asyncio
    async def test_an_oidc_login_into_administrators_sets_the_role(self, db_session):
        from backend.app.services.oidc_group_sync import sync_oidc_user_groups

        admins, operators = await _groups(db_session)
        user_id = await _user(db_session, "bob", "user", [operators])
        user = await _load(db_session, user_id)

        await sync_oidc_user_groups(
            db_session,
            user,
            group_claim="groups",
            group_mapping={"staff": "Administrators"},
            claims={"groups": ["staff"]},
        )

        assert (await _load(db_session, user_id)).role == "admin"

    @pytest.mark.asyncio
    async def test_an_ldap_user_created_into_administrators_gets_the_role(self, db_session):
        from types import SimpleNamespace

        from backend.app.api.routes.auth import _provision_ldap_user

        await _groups(db_session)

        user = await _provision_ldap_user(
            db_session,
            SimpleNamespace(username="carol", email="carol@example.invalid", groups=["cn=admins"]),
            SimpleNamespace(group_mapping={"cn=admins": "Administrators"}, default_group=""),
        )

        assert (await _load(db_session, user.id)).role == "admin"
