"""Route-level and end-to-end coverage for OIDC group sync (#3107).

The original suite (test_oidc_group_sync.py) covers the helpers, the service
and the schema. This file covers the three gaps from PR #3122 review:

1. The public provider list must not leak group sync config — an anonymous
   visitor who can reach the login page must not learn which IdP group name
   maps to which Bambuddy group (the review's blocker).
2. The 422s on create/update when a mapping value names no existing group.
3. The env path: BAMBUDDY_OIDC_GROUP_CLAIM / BAMBUDDY_OIDC_GROUP_MAPPING
   applied at startup, refused on unknown group names, refused on bad JSON.
4. oidc_callback end to end with a mapping configured: the auto-created
   user lands in the mapped group, and a second login applies revocation.
"""

from __future__ import annotations

import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt as pyjwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.oidc_env import apply_env_oidc_provider
from backend.app.models.group import Group
from backend.app.models.oidc_provider import OIDCProvider
from backend.app.models.user import User
from backend.tests.integration.test_mfa_api import (
    _auth_header,
    _make_test_rsa_key,
    _setup_and_login,
)

PROVIDER_BASE = {
    "name": "GroupSyncIdP",
    "issuer_url": "https://gs.test.example.com",
    "client_id": "gs-client",
    "client_secret": "gs-secret",
    "scopes": "openid email profile",
    "is_enabled": True,
    "auto_create_users": True,
}


async def _get_or_make_group(db: AsyncSession, name: str):
    """The conftest seeds the system groups, so anything named like one of
    those is fetched rather than created (its UNIQUE(name) would reject us)."""
    from sqlalchemy import select as sa_select

    row = (await db.execute(sa_select(Group).where(Group.name == name))).scalar_one_or_none()
    if row is not None:
        return row
    group = Group(name=name, description=f"Test group {name}")
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return group


_ADMIN_TOKEN: dict[str, str] = {}


async def _admin_token(async_client: AsyncClient) -> str:
    """One admin per test database: /auth/setup enables auth and mints THE
    admin account, so repeated _setup_and_login calls with different
    usernames 401 (the second setup is refused). Cache the token per client.
    """
    key = str(id(async_client))
    if key not in _ADMIN_TOKEN:
        _ADMIN_TOKEN[key] = await _setup_and_login(async_client, "gsadmin", "gsadmin1")
    return _ADMIN_TOKEN[key]


async def _create_provider(async_client: AsyncClient, **overrides):
    token = await _admin_token(async_client)
    body = {**PROVIDER_BASE, **overrides}
    resp = await async_client.post(
        "/api/v1/auth/oidc/providers",
        json=body,
        headers=_auth_header(token),
    )
    return resp


class TestPublicListDoesNotLeakGroupSyncConfig:
    """The review's blocker: GET /oidc/providers is public and must serve the
    slim shape only. group_claim / group_mapping tell an attacker which IdP
    group to aim for (possibly Administrators)."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_public_list_omits_group_fields(self, async_client: AsyncSession, db_session: AsyncSession):
        await _get_or_make_group(db_session, "Operators")
        resp = await _create_provider(
            async_client,
            group_claim="roles",
            group_mapping={"idp-ops": "Operators"},
        )
        assert resp.status_code == 201, resp.text

        public = await async_client.get("/api/v1/auth/oidc/providers")
        assert public.status_code == 200
        entry = next(p for p in public.json() if p["name"] == "GroupSyncIdP")
        assert set(entry.keys()) == {"id", "name", "has_icon", "is_autologin"}, (
            f"public provider response must stay slim, got keys: {sorted(entry.keys())}"
        )
        assert "group_claim" not in entry
        assert "group_mapping" not in entry

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_list_still_carries_group_fields(self, async_client: AsyncSession, db_session: AsyncSession):
        await _get_or_make_group(db_session, "Operators")
        resp = await _create_provider(
            async_client,
            group_claim="roles",
            group_mapping={"idp-ops": "Operators"},
        )
        assert resp.status_code == 201

        token = await _admin_token(async_client)
        admin_list = await async_client.get("/api/v1/auth/oidc/providers/all", headers=_auth_header(token))
        entry = next(p for p in admin_list.json() if p["name"] == "GroupSyncIdP")
        assert entry["group_claim"] == "roles"
        assert entry["group_mapping"] == {"idp-ops": "Operators"}


class TestMappingGroupValidation:
    """The 422s: mapping values must name existing groups, on create and update."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_rejects_unknown_group(self, async_client: AsyncSession):
        resp = await _create_provider(async_client, group_mapping={"idp-ops": "NoSuchGroup"})
        assert resp.status_code == 422, resp.text
        assert "NoSuchGroup" in resp.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_accepts_existing_group(self, async_client: AsyncSession, db_session: AsyncSession):
        await _get_or_make_group(db_session, "Operators")
        resp = await _create_provider(async_client, group_mapping={"idp-ops": "Operators"})
        assert resp.status_code == 201
        assert resp.json()["group_mapping"] == {"idp-ops": "Operators"}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_rejects_case_variant_of_existing_group(
        self, async_client: AsyncSession, db_session: AsyncSession
    ):
        """Exact match only (review point 8): a case variant would pass the
        check and then silently never resolve in the sync's exact lookup."""
        await _get_or_make_group(db_session, "Operators")
        resp = await _create_provider(async_client, group_mapping={"idp-ops": "operators"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_rejects_unknown_group(self, async_client: AsyncSession, db_session: AsyncSession):
        created = await _create_provider(async_client)
        assert created.status_code == 201
        provider_id = created.json()["id"]

        token = await _admin_token(async_client)
        resp = await async_client.put(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            json={"group_mapping": {"idp-ops": "NoSuchGroup"}},
            headers=_auth_header(token),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_clears_mapping_with_empty_object(self, async_client: AsyncSession, db_session: AsyncSession):
        await _get_or_make_group(db_session, "Operators")
        created = await _create_provider(async_client, group_mapping={"idp-ops": "Operators"})
        provider_id = created.json()["id"]

        token = await _admin_token(async_client)
        resp = await async_client.put(
            f"/api/v1/auth/oidc/providers/{provider_id}",
            json={"group_mapping": {}},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        assert resp.json()["group_mapping"] == {}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_namespaced_group_claim_accepted(self, async_client: AsyncSession):
        """Auth0-style namespaced claim names must be configurable (review note)."""
        resp = await _create_provider(async_client, group_claim="app/roles")
        assert resp.status_code == 201, resp.text
        assert resp.json()["group_claim"] == "app/roles"


class TestEnvGroupMapping:
    """The env path: the code most likely to strand an operator at boot."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_env_mapping_applied(self, db_session: AsyncSession, monkeypatch):
        await _get_or_make_group(db_session, "Operators")
        await db_session.commit()
        monkeypatch.setenv("BAMBUDDY_OIDC_NAME", "EnvIdP")
        monkeypatch.setenv("BAMBUDDY_OIDC_ISSUER_URL", "https://env.test.example.com")
        monkeypatch.setenv("BAMBUDDY_OIDC_CLIENT_ID", "env-client")
        monkeypatch.setenv("BAMBUDDY_OIDC_CLIENT_SECRET", "env-secret")
        monkeypatch.setenv("BAMBUDDY_OIDC_GROUP_CLAIM", "roles")
        monkeypatch.setenv("BAMBUDDY_OIDC_GROUP_MAPPING", '{"idp-ops": "Operators"}')
        for key in ("BAMBUDDY_OIDC_DEFAULT_GROUP", "BAMBUDDY_OIDC_SCOPES", "BAMBUDDY_OIDC_ENABLED"):
            monkeypatch.delenv(key, raising=False)

        await apply_env_oidc_provider(db_session)
        row = (await db_session.execute(select(OIDCProvider).where(OIDCProvider.name == "EnvIdP"))).scalar_one()
        assert row.group_claim == "roles"
        assert row.group_mapping == {"idp-ops": "Operators"}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_env_mapping_unknown_group_refuses_provider(self, db_session: AsyncSession, monkeypatch, caplog):
        """Unknown names must refuse the whole config (matching DEFAULT_GROUP):
        no provider row may be created carrying a mapping that never resolves."""
        monkeypatch.setenv("BAMBUDDY_OIDC_NAME", "EnvIdP-Refused")
        monkeypatch.setenv("BAMBUDDY_OIDC_ISSUER_URL", "https://env2.test.example.com")
        monkeypatch.setenv("BAMBUDDY_OIDC_CLIENT_ID", "env-client")
        monkeypatch.setenv("BAMBUDDY_OIDC_CLIENT_SECRET", "env-secret")
        monkeypatch.setenv("BAMBUDDY_OIDC_GROUP_MAPPING", '{"idp-ops": "NoSuchGroup"}')
        for key in (
            "BAMBUDDY_OIDC_DEFAULT_GROUP",
            "BAMBUDDY_OIDC_SCOPES",
            "BAMBUDDY_OIDC_ENABLED",
            "BAMBUDDY_OIDC_GROUP_CLAIM",
        ):
            monkeypatch.delenv(key, raising=False)

        await apply_env_oidc_provider(db_session)
        row = (
            await db_session.execute(select(OIDCProvider).where(OIDCProvider.name == "EnvIdP-Refused"))
        ).scalar_one_or_none()
        assert row is None, "provider must not be created when a mapping value matches no group"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_env_mapping_bad_json_refuses_provider(self, db_session: AsyncSession, monkeypatch):
        monkeypatch.setenv("BAMBUDDY_OIDC_NAME", "EnvIdP-BadJson")
        monkeypatch.setenv("BAMBUDDY_OIDC_ISSUER_URL", "https://env3.test.example.com")
        monkeypatch.setenv("BAMBUDDY_OIDC_CLIENT_ID", "env-client")
        monkeypatch.setenv("BAMBUDDY_OIDC_CLIENT_SECRET", "env-secret")
        monkeypatch.setenv("BAMBUDDY_OIDC_GROUP_MAPPING", "{not json")
        for key in (
            "BAMBUDDY_OIDC_DEFAULT_GROUP",
            "BAMBUDDY_OIDC_SCOPES",
            "BAMBUDDY_OIDC_ENABLED",
            "BAMBUDDY_OIDC_GROUP_CLAIM",
        ):
            monkeypatch.delenv(key, raising=False)

        await apply_env_oidc_provider(db_session)  # must not raise
        row = (
            await db_session.execute(select(OIDCProvider).where(OIDCProvider.name == "EnvIdP-BadJson"))
        ).scalar_one_or_none()
        assert row is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_env_mapping_removed_clears_it(self, db_session: AsyncSession, monkeypatch):
        """The environment is the whole truth: dropping the variable clears
        the mapping on the next boot."""
        await _get_or_make_group(db_session, "Operators")
        await db_session.commit()
        env = {
            "BAMBUDDY_OIDC_NAME": "EnvIdP-Clear",
            "BAMBUDDY_OIDC_ISSUER_URL": "https://env4.test.example.com",
            "BAMBUDDY_OIDC_CLIENT_ID": "env-client",
            "BAMBUDDY_OIDC_CLIENT_SECRET": "env-secret",
        }
        for key in (
            "BAMBUDDY_OIDC_DEFAULT_GROUP",
            "BAMBUDDY_OIDC_SCOPES",
            "BAMBUDDY_OIDC_ENABLED",
            "BAMBUDDY_OIDC_GROUP_CLAIM",
        ):
            monkeypatch.delenv(key, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("BAMBUDDY_OIDC_GROUP_MAPPING", '{"idp-ops": "Operators"}')
        await apply_env_oidc_provider(db_session)
        row = (await db_session.execute(select(OIDCProvider).where(OIDCProvider.name == "EnvIdP-Clear"))).scalar_one()
        assert row.group_mapping == {"idp-ops": "Operators"}

        monkeypatch.delenv("BAMBUDDY_OIDC_GROUP_MAPPING")
        await apply_env_oidc_provider(db_session)
        await db_session.refresh(row)
        assert row.group_mapping == {}


def _mock_oidc_httpx(discovery_doc, token_response, jwks_data):
    """An httpx.AsyncClient stand-in for oidc_callback: serves the discovery
    document, the JWKS payload and the token response, nothing else."""

    class _MockResp:
        def __init__(self, data):
            self._data = data
            self.status_code = 200
            self.is_success = True
            self.text = str(data)

        def json(self):
            return self._data

        def raise_for_status(self):
            pass

    class _MockHttpxClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            if "jwks" in url:
                return _MockResp(jwks_data)
            return _MockResp(discovery_doc)

        async def post(self, url, **kwargs):
            return _MockResp(token_response)

    return _MockHttpxClient


class TestCallbackAppliesMappingEndToEnd:
    """oidc_callback with a mapping configured: creation grants the mapped
    group, a later login applies revocation, and a manual group survives."""

    @staticmethod
    def _id_token(private_pem, issuer, client_id, nonce, groups, sub, email):
        now = int(time.time())
        return pyjwt.encode(
            {
                "sub": sub,
                "iss": issuer,
                "aud": client_id,
                "nonce": nonce,
                "email": email,
                "email_verified": True,
                "groups": groups,
                "iat": now,
                "exp": now + 300,
            },
            private_pem,
            algorithm="RS256",
            headers={"kid": "test-kid-1"},
        )

    async def _run_callback(self, async_client, db_session, provider_id, id_token, nonce, jwks, issuer):
        from backend.app.models.auth_ephemeral import AuthEphemeralToken

        state = secrets.token_urlsafe(32)
        db_session.add(
            AuthEphemeralToken(
                token=state,
                token_type="oidc_state",
                provider_id=provider_id,
                nonce=nonce,
                code_verifier=secrets.token_urlsafe(48),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )
        await db_session.commit()

        discovery = {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/auth",
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": f"{issuer}/.well-known/jwks.json",
        }
        token_response = {"access_token": "mock", "token_type": "Bearer", "id_token": id_token}
        client_cls = _mock_oidc_httpx(discovery, token_response, jwks)

        with patch("backend.app.api.routes.mfa.httpx.AsyncClient", client_cls):
            resp = await async_client.get(
                f"/api/v1/auth/oidc/callback?code=x&state={state}",
                follow_redirects=False,
            )
        return resp

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_callback_grants_mapped_group_on_creation(self, async_client: AsyncClient, db_session: AsyncSession):
        operators = await _get_or_make_group(db_session, "Operators")
        await _get_or_make_group(db_session, "ManualGroup")
        private_pem, jwks = _make_test_rsa_key()
        issuer = "https://e2e-gs.test.example.com"
        nonce = secrets.token_urlsafe(16)

        resp = await _create_provider(
            async_client,
            name="E2E-GroupSync-IdP",
            issuer_url=issuer,
            client_id="gs-e2e-client",
            client_secret="sec",
            group_claim="groups",
            group_mapping={"idp-ops": "Operators"},
        )
        assert resp.status_code == 201, resp.text
        provider_id = resp.json()["id"]

        id_token = self._id_token(
            private_pem, issuer, "gs-e2e-client", nonce, ["idp-ops"], "gs-sub-1", "gse2e@example.com"
        )
        callback = await self._run_callback(async_client, db_session, provider_id, id_token, nonce, jwks, issuer)
        assert callback.status_code == 302, callback.text
        assert "oidc_token=" in callback.headers.get("location", "")

        user = (await db_session.execute(select(User).where(User.email == "gse2e@example.com"))).scalar_one()
        group_ids = {g.id for g in user.groups}
        assert operators.id in group_ids, "auto-created user must land in the mapped group"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_second_login_applies_revocation_and_keeps_manual(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Login 1 grants Operators via the mapping. An admin then adds
        ManualGroup by hand and the IdP revokes idp-ops. Login 2 must remove
        Operators and keep ManualGroup — the #1292 contract, end to end."""

        operators = await _get_or_make_group(db_session, "Operators2")
        manual = await _get_or_make_group(db_session, "ManualGroup2")
        private_pem, jwks = _make_test_rsa_key()
        issuer = "https://e2e-gs2.test.example.com"
        client_id = "gs-e2e-client-2"
        nonce = secrets.token_urlsafe(16)

        resp = await _create_provider(
            async_client,
            name="E2E-GroupSync-IdP-2",
            issuer_url=issuer,
            client_id=client_id,
            client_secret="sec",
            group_claim="groups",
            group_mapping={"idp-ops": "Operators2"},
        )
        assert resp.status_code == 201, resp.text
        provider_id = resp.json()["id"]

        # Login 1: IdP says idp-ops -> Operators2 granted at creation.
        id_token = self._id_token(private_pem, issuer, client_id, nonce, ["idp-ops"], "gs-sub-2", "gse2e2@example.com")
        cb = await self._run_callback(async_client, db_session, provider_id, id_token, nonce, jwks, issuer)
        assert cb.status_code == 302

        user = (await db_session.execute(select(User).where(User.email == "gse2e2@example.com"))).scalar_one()
        # Creation assigns the default group (Viewers) per the existing
        # auto-create path; the sync adds the mapped group on top. Viewers is
        # NOT in the mapping, so it is a creation-time assignment and must
        # survive login 2 alongside the manual group.
        viewers = (await db_session.execute(select(Group).where(Group.name == "Viewers"))).scalar_one()
        assert {g.id for g in user.groups} == {operators.id, viewers.id}

        # Admin assigns ManualGroup2 by hand; IdP revokes idp-ops.
        manual_group = (await db_session.execute(select(Group).where(Group.name == "ManualGroup2"))).scalar_one()
        user.groups = list(user.groups) + [manual_group]
        db_session.add(user)
        await db_session.commit()

        # Login 2: new nonce, no idp-ops in the claim.
        nonce2 = secrets.token_urlsafe(16)
        id_token2 = self._id_token(private_pem, issuer, client_id, nonce2, [], "gs-sub-2", "gse2e2@example.com")
        cb2 = await self._run_callback(async_client, db_session, provider_id, id_token2, nonce2, jwks, issuer)
        assert cb2.status_code == 302, cb2.text

        await db_session.refresh(user, attribute_names=["groups"])
        assert {g.id for g in user.groups} == {manual.id, viewers.id}, (
            "revoked mapped group must be removed; the creation-default group "
            "and the manual assignment must both survive"
        )
