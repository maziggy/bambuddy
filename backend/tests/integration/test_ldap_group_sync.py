"""Regression tests for LDAP user group sync behavior (#1292).

Reporter @Fuechslein: when an admin manually assigned a BamBuddy group to an
LDAP user, the assignment was silently wiped on the user's next login. Cause
was that _sync_ldap_user used to replace `user.groups` entirely on every login,
overwriting anything not derived from LDAP state.

The fix partitions the user's groups into "LDAP-managed" (anything in the
ldap_group_mapping config values + the default_group) and "manual". Only the
LDAP-managed slice is rebuilt from LDAP truth; manual assignments survive.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.auth import _sync_ldap_user
from backend.app.models.group import Group
from backend.app.models.user import User


@dataclass
class _FakeLdapUser:
    """Stand-in for backend.app.services.ldap_service.LDAPUserInfo."""

    username: str
    email: str | None
    groups: list[str]


@dataclass
class _FakeLdapConfig:
    """Stand-in for backend.app.services.ldap_service.LDAPConfig — only the
    fields _sync_ldap_user actually reads."""

    group_mapping: dict[str, str]
    default_group: str = ""


async def _make_group(db: AsyncSession, name: str) -> Group:
    group = Group(name=name, description=f"Test group {name}")
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return group


async def _make_ldap_user(db: AsyncSession, username: str, groups: list[Group]) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash=None,
        role="user",
        auth_source="ldap",
        is_active=True,
    )
    user.groups = groups
    db.add(user)
    await db.commit()
    await db.refresh(user, attribute_names=["groups"])
    return user


class TestLdapGroupSyncPreservesManualAssignments:
    """The #1292 fix: groups outside the LDAP-managed set must survive logins."""

    @pytest.mark.asyncio
    async def test_manual_group_survives_login(self, db_session: AsyncSession):
        """Admin assigns 'Administrators' to an LDAP user. 'Administrators' is
        NOT in the LDAP group_mapping. Next login must keep it."""
        admins = await _make_group(db_session, "Administrators")
        users = await _make_group(db_session, "Users")

        user = await _make_ldap_user(db_session, "alice", [admins])
        assert {g.name for g in user.groups} == {"Administrators"}

        ldap_user = _FakeLdapUser(
            username="alice", email="alice@example.com", groups=["cn=staff,ou=groups,dc=example,dc=com"]
        )
        ldap_config = _FakeLdapConfig(
            group_mapping={"cn=staff,ou=groups,dc=example,dc=com": "Users"},
            default_group="",
        )

        await _sync_ldap_user(db_session, user, ldap_user, ldap_config)
        await db_session.refresh(user, attribute_names=["groups"])

        assert {g.name for g in user.groups} == {"Administrators", "Users"}, (
            "Manual 'Administrators' assignment must be preserved; LDAP-mapped 'Users' must be added"
        )
        # Use the local refs to silence linters about unused locals
        assert admins.id != users.id

    @pytest.mark.asyncio
    async def test_default_group_not_treated_as_manual(self, db_session: AsyncSession):
        """The default_group is LDAP-managed even though it's not in the mapping
        values — it gets added when no mapped groups resolve. So if LDAP later
        revokes all group memberships, the default group stays; if a different
        default_group is configured, the old one is dropped from the user."""
        guest = await _make_group(db_session, "Guests")
        await _make_group(db_session, "Users")

        # User has the (LDAP-managed) Guests group as their default — no manual groups.
        user = await _make_ldap_user(db_session, "bob", [guest])

        ldap_user = _FakeLdapUser(username="bob", email="bob@example.com", groups=[])
        ldap_config = _FakeLdapConfig(group_mapping={}, default_group="Guests")

        await _sync_ldap_user(db_session, user, ldap_user, ldap_config)
        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.name for g in user.groups} == {"Guests"}, "Default group should persist"

    @pytest.mark.asyncio
    async def test_revocation_in_ldap_still_propagates(self, db_session: AsyncSession):
        """The original design intent — revocation in LDAP must flow through — must
        still work for LDAP-managed groups. User was in 'Users' (LDAP-mapped); LDAP
        no longer reports the mapped group; sync must remove 'Users'."""
        users = await _make_group(db_session, "Users")

        user = await _make_ldap_user(db_session, "charlie", [users])
        assert {g.name for g in user.groups} == {"Users"}

        ldap_user = _FakeLdapUser(username="charlie", email="charlie@example.com", groups=[])
        ldap_config = _FakeLdapConfig(
            group_mapping={"cn=staff,ou=groups,dc=example,dc=com": "Users"},
            default_group="",
        )

        await _sync_ldap_user(db_session, user, ldap_user, ldap_config)
        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.name for g in user.groups} == set(), (
            "LDAP-managed groups must be removed when LDAP no longer reports the user in them"
        )

    @pytest.mark.asyncio
    async def test_manual_assignment_to_managed_group_still_overridden(self, db_session: AsyncSession):
        """If an admin manually assigns a group that IS in the LDAP mapping, LDAP
        truth still wins — otherwise revoking access in LDAP wouldn't work for
        users who happened to have manual assignments to the same group. Cannot
        distinguish manual-but-mapped from LDAP-derived once the assignment is
        in the DB; resolved by treating any group in the LDAP-managed set as
        authoritative-by-LDAP."""
        users = await _make_group(db_session, "Users")

        # Manually assign 'Users' (which IS in the LDAP mapping) to an LDAP user.
        user = await _make_ldap_user(db_session, "dave", [users])

        # LDAP says the user is in no mapped groups.
        ldap_user = _FakeLdapUser(username="dave", email="dave@example.com", groups=[])
        ldap_config = _FakeLdapConfig(
            group_mapping={"cn=staff,ou=groups,dc=example,dc=com": "Users"},
            default_group="",
        )

        await _sync_ldap_user(db_session, user, ldap_user, ldap_config)
        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.name for g in user.groups} == set(), (
            "Manual assignment to an LDAP-managed group is overridden by LDAP state"
        )

    @pytest.mark.asyncio
    async def test_mixed_manual_and_ldap_groups(self, db_session: AsyncSession):
        """Most realistic scenario: user has multiple manual assignments AND LDAP
        mapped groups. Manual groups survive; LDAP-managed slice gets rebuilt."""
        admins = await _make_group(db_session, "Administrators")
        ops = await _make_group(db_session, "PrintOps")
        users = await _make_group(db_session, "Users")
        await _make_group(db_session, "Power Users")

        # User has two manual groups (Administrators, PrintOps) plus one LDAP
        # group (Users) at the start.
        user = await _make_ldap_user(db_session, "eve", [admins, ops, users])

        # LDAP login: user is now in two LDAP-mapped groups.
        ldap_user = _FakeLdapUser(
            username="eve",
            email="eve@example.com",
            groups=["cn=staff,ou=groups,dc=example,dc=com", "cn=power,ou=groups,dc=example,dc=com"],
        )
        ldap_config = _FakeLdapConfig(
            group_mapping={
                "cn=staff,ou=groups,dc=example,dc=com": "Users",
                "cn=power,ou=groups,dc=example,dc=com": "Power Users",
            },
            default_group="",
        )

        await _sync_ldap_user(db_session, user, ldap_user, ldap_config)
        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.name for g in user.groups} == {
            "Administrators",  # manual, preserved
            "PrintOps",  # manual, preserved
            "Users",  # LDAP-managed, retained from LDAP
            "Power Users",  # LDAP-managed, newly added from LDAP
        }
