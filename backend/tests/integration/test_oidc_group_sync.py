"""Integration tests for OIDC group sync (#3107).

Same regression contract as the LDAP group sync (test_ldap_group_sync.py,
#1292): the sync manages only the Bambuddy groups named in the provider's
group_mapping values, and every login replaces exactly that slice. Manual
assignments to groups outside the mapping survive; revocation at the IdP
propagates on the next login.

One deliberate difference from LDAP: there is no default-group fallback in
the OIDC sync. The provider's default_group_id is applied once at account
creation (routes/mfa.py) and never re-asserted, so promoting an auto-created
user out of Viewers is a manual action that sticks.
"""

import logging
from typing import NoReturn

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.group import Group
from backend.app.models.oidc_provider import OIDCProvider
from backend.app.models.user import User
from backend.app.schemas.auth import OIDCProviderCreate
from backend.app.services.oidc_group_sync import (
    _MAX_CLAIM_ITEMS,
    extract_idp_groups,
    resolve_oidc_group_mapping,
    sync_oidc_user_groups,
)


async def _make_group(db: AsyncSession, name: str) -> Group:
    group = Group(name=name, description=f"Test group {name}")
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return group


async def _make_user(db: AsyncSession, username: str, groups: list[Group]) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash=None,
        role="user",
        auth_source="oidc",
        is_active=True,
    )
    user.groups = groups
    db.add(user)
    await db.commit()
    await db.refresh(user, attribute_names=["groups"])
    return user


# ─── claim-shape helpers ──────────────────────────────────────────────────────


class TestExtractIdpGroups:
    """Providers disagree on the groups-claim shape (#3107). All accepted."""

    def test_json_array(self):
        assert extract_idp_groups(["fablab-staff", "students"]) == ["fablab-staff", "students"]

    def test_space_separated_string(self):
        assert extract_idp_groups("fablab-staff students") == ["fablab-staff", "students"]

    def test_comma_separated_string(self):
        assert extract_idp_groups("fablab-staff, students") == ["fablab-staff", "students"]

    def test_single_group_string(self):
        assert extract_idp_groups("fablab-staff") == ["fablab-staff"]

    def test_duplicates_removed(self):
        assert extract_idp_groups(["a", "b", "a", "b "]) == ["a", "b"]

    def test_none_and_non_group_payloads(self):
        assert extract_idp_groups(None) == []
        assert extract_idp_groups(42) == []
        assert extract_idp_groups({"odd": "shape"}) == []
        assert extract_idp_groups(["ok", 7, None, ""]) == ["ok"]

    def test_list_claim_is_bounded(self):
        # Review on #3122: the bound is the only defence against a hostile
        # oversized token, and it must hold for every accepted shape, not
        # just the list one.
        oversized = [f"g{i}" for i in range(_MAX_CLAIM_ITEMS + 100)]
        result = extract_idp_groups(oversized)
        assert len(result) == _MAX_CLAIM_ITEMS
        assert result[0] == "g0"
        assert result[-1] == f"g{_MAX_CLAIM_ITEMS - 1}"

    def test_space_separated_claim_is_bounded(self):
        # The shape the original bound missed: a string claim splits into
        # arbitrarily many fragments, so the slice has to apply after
        # splitting, not only on the list path.
        oversized = " ".join(f"g{i}" for i in range(_MAX_CLAIM_ITEMS + 100))
        result = extract_idp_groups(oversized)
        assert len(result) == _MAX_CLAIM_ITEMS
        assert result[-1] == f"g{_MAX_CLAIM_ITEMS - 1}"

    def test_comma_separated_claim_is_bounded(self):
        # No spaces around the commas: the split happens on raw fragments,
        # so ", "-joined input would spend half the budget on empty
        # fragments. The contract being pinned is the upper bound.
        oversized = ",".join(f"g{i}" for i in range(_MAX_CLAIM_ITEMS + 100))
        result = extract_idp_groups(oversized)
        assert len(result) == _MAX_CLAIM_ITEMS


class TestResolveMapping:
    def test_case_insensitive_on_idp_side(self):
        assert resolve_oidc_group_mapping(["IDP-STAFF"], {"idp-staff": "Operators"}) == ["Operators"]

    def test_unmapped_groups_ignored(self):
        assert resolve_oidc_group_mapping(["nope", "idp-staff"], {"idp-staff": "Operators"}) == ["Operators"]

    def test_empty_mapping_disables(self):
        assert resolve_oidc_group_mapping(["idp-staff"], {}) == []

    def test_two_idp_groups_to_one_bambuddy_group(self):
        mapping = {"staff": "Operators", "admins": "Operators"}
        assert resolve_oidc_group_mapping(["admins", "staff"], mapping) == ["Operators"]


# ─── sync semantics ───────────────────────────────────────────────────────────


class TestSyncOidcUserGroups:
    @pytest.mark.asyncio
    async def test_adds_mapped_group_on_login(self, db_session: AsyncSession):
        operators = await _make_group(db_session, "Operators")
        user = await _make_user(db_session, "alice", [])

        await sync_oidc_user_groups(
            db_session,
            user,
            group_claim="groups",
            group_mapping={"idp-staff": "Operators"},
            claims={"groups": ["idp-staff"]},
        )
        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.id for g in user.groups} == {operators.id}

    @pytest.mark.asyncio
    async def test_manual_group_survives_login(self, db_session: AsyncSession):
        """The #1292 contract: a group outside the mapping is a manual
        assignment and must never be touched by the sync."""
        admins = await _make_group(db_session, "Administrators")
        await _make_group(db_session, "Operators")

        user = await _make_user(db_session, "alice", [admins])

        await sync_oidc_user_groups(
            db_session,
            user,
            group_claim="groups",
            group_mapping={"idp-staff": "Operators"},
            claims={"groups": ["idp-staff"]},
        )
        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.name for g in user.groups} == {"Administrators", "Operators"}

    @pytest.mark.asyncio
    async def test_revocation_at_idp_propagates(self, db_session: AsyncSession):
        """Losing the IdP group must remove the mapped Bambuddy group on the
        next login — otherwise IdP-side revocation would be decorative."""
        operators = await _make_group(db_session, "Operators")
        user = await _make_user(db_session, "bob", [operators])

        await sync_oidc_user_groups(
            db_session,
            user,
            group_claim="groups",
            group_mapping={"idp-staff": "Operators"},
            claims={"groups": []},
        )
        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.name for g in user.groups} == set()

    @pytest.mark.asyncio
    async def test_revocation_keeps_manual_groups(self, db_session: AsyncSession):
        admins = await _make_group(db_session, "Administrators")
        operators = await _make_group(db_session, "Operators")

        user = await _make_user(db_session, "carol", [admins, operators])

        await sync_oidc_user_groups(
            db_session,
            user,
            group_claim="groups",
            group_mapping={"idp-staff": "Operators"},
            claims={"groups": []},
        )
        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.name for g in user.groups} == {"Administrators"}

    @pytest.mark.asyncio
    async def test_manual_assignment_to_managed_group_overridden(self, db_session: AsyncSession):
        """An admin who manually grants a mapped group is overridden by IdP
        truth, same as LDAP: revocation must work for those users too."""
        operators = await _make_group(db_session, "Operators")
        user = await _make_user(db_session, "dave", [operators])

        await sync_oidc_user_groups(
            db_session,
            user,
            group_claim="groups",
            group_mapping={"idp-staff": "Operators"},
            claims={"groups": []},
        )
        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.name for g in user.groups} == set()

    @pytest.mark.asyncio
    async def test_no_mapping_is_a_noop(self, db_session: AsyncSession):
        """Default state for every upgraded install: nothing configured, so
        nothing changes — including groups that would have matched a mapping
        if one existed."""
        admins = await _make_group(db_session, "Administrators")
        user = await _make_user(db_session, "eve", [admins])

        await sync_oidc_user_groups(
            db_session,
            user,
            group_claim="groups",
            group_mapping={},
            claims={"groups": ["idp-staff"]},
        )
        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.name for g in user.groups} == {"Administrators"}

    @pytest.mark.asyncio
    async def test_missing_claim_is_not_fatal(self, db_session: AsyncSession):
        """A provider that never sends the claim means 'no mapped groups',
        not an error: the login must proceed and the managed slice clears."""
        operators = await _make_group(db_session, "Operators")
        user = await _make_user(db_session, "frank", [operators])

        await sync_oidc_user_groups(
            db_session,
            user,
            group_claim="groups",
            group_mapping={"idp-staff": "Operators"},
            claims={},  # claim absent entirely
        )
        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.name for g in user.groups} == set()

    @pytest.mark.asyncio
    async def test_mapping_to_deleted_group_skipped(self, db_session: AsyncSession):
        """A dangling mapping value (group deleted after the mapping was saved)
        is skipped at sync time, mirroring default_group_id's SQLite story."""
        await _make_group(db_session, "Operators")
        user = await _make_user(db_session, "grace", [])

        await sync_oidc_user_groups(
            db_session,
            user,
            group_claim="groups",
            group_mapping={"idp-staff": "Operators", "idp-ghost": "DeletedGroup"},
            claims={"groups": ["idp-staff", "idp-ghost"]},
        )
        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.name for g in user.groups} == {"Operators"}

    @pytest.mark.asyncio
    async def test_custom_claim_name(self, db_session: AsyncSession):
        """group_claim='roles' reads the roles claim and ignores a groups
        claim that happens to be present."""
        operators = await _make_group(db_session, "Operators")
        user = await _make_user(db_session, "heidi", [])

        await sync_oidc_user_groups(
            db_session,
            user,
            group_claim="roles",
            group_mapping={"op": "Operators"},
            claims={"roles": ["op"], "groups": ["unrelated"]},
        )
        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.id for g in user.groups} == {operators.id}

    @pytest.mark.asyncio
    async def test_sync_failure_never_blocks_login(self, db_session: AsyncSession, monkeypatch, caplog):
        """The service's contract with oidc_callback: never raise. A failure
        mid-sync is logged and the user keeps the groups they had — the login
        already authenticated, so the sync must not take it down with it.

        The commit is the interesting failure point: by then the user object
        is dirty, so the except path's rollback has real work to do and the
        in-memory relationship is post-rollback state. Database truth is
        re-selected rather than read off the expired instance."""
        admins = await _make_group(db_session, "Administrators")
        await _make_group(db_session, "Operators")
        user = await _make_user(db_session, "ivan", [admins])
        user_id = user.id  # captured pre-sync: the rollback expires the whole instance, PK included

        async def _failing_commit() -> NoReturn:
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(db_session, "commit", _failing_commit)

        with caplog.at_level(logging.ERROR):
            await sync_oidc_user_groups(  # must not raise
                db_session,
                user,
                group_claim="groups",
                group_mapping={"idp-staff": "Operators"},
                claims={"groups": ["idp-staff"]},
            )

        fresh = (
            await db_session.execute(select(User).where(User.id == user_id).options(selectinload(User.groups)))
        ).scalar_one()
        assert {g.name for g in fresh.groups} == {"Administrators"}
        assert "OIDC group sync failed for user ivan" in caplog.text


# ─── schema validation ────────────────────────────────────────────────────────


class TestProviderSchema:
    def test_create_defaults(self):
        provider = OIDCProviderCreate(name="t", issuer_url="https://id.example.com", client_id="a", client_secret="b")
        assert provider.group_claim == "groups"
        assert provider.group_mapping == {}

    def test_create_with_mapping(self):
        provider = OIDCProviderCreate(
            name="t",
            issuer_url="https://id.example.com",
            client_id="a",
            client_secret="b",
            group_claim="roles",
            group_mapping={"op": "Operators"},
        )
        assert provider.group_claim == "roles"
        assert provider.group_mapping == {"op": "Operators"}

    def test_invalid_group_claim_rejected(self):
        with pytest.raises(ValidationError):
            OIDCProviderCreate(
                name="t",
                issuer_url="https://id.example.com",
                client_id="a",
                client_secret="b",
                group_claim="not a claim!",
            )

    def test_non_object_mapping_rejected(self):
        with pytest.raises(ValidationError):
            OIDCProviderCreate(
                name="t",
                issuer_url="https://id.example.com",
                client_id="a",
                client_secret="b",
                group_mapping=["not", "an", "object"],
            )

    def test_empty_mapping_values_rejected(self):
        with pytest.raises(ValidationError):
            OIDCProviderCreate(
                name="t",
                issuer_url="https://id.example.com",
                client_id="a",
                client_secret="b",
                group_mapping={"op": "   "},
            )

    def test_update_none_leaves_unchanged(self):
        from backend.app.schemas.auth import OIDCProviderUpdate

        update = OIDCProviderUpdate()
        assert update.group_claim is None
        assert update.group_mapping is None


# ─── model column round-trip ──────────────────────────────────────────────────


class TestProviderModelRoundTrip:
    @pytest.mark.asyncio
    async def test_columns_persist(self, db_session: AsyncSession):
        provider = OIDCProvider(
            name="idp-test",
            issuer_url="https://id.example.com",
            client_id="a",
            client_secret="b",
            group_claim="roles",
            group_mapping={"op": "Operators"},
        )
        db_session.add(provider)
        await db_session.commit()
        await db_session.refresh(provider)
        assert provider.group_claim == "roles"
        assert provider.group_mapping == {"op": "Operators"}
