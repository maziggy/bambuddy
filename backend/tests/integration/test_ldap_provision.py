"""Integration tests for the manual LDAP user provisioning routes (#1298).

Reporter @Fuechslein noted that BamBuddy forced admins to leave auto-provision
on because there was no UI path to create an LDAP user by hand. The new
endpoints are GET /auth/ldap/search (admin types a partial name, picks a
candidate) and POST /auth/ldap/provision (server re-resolves and creates the
user).

These tests cover:

- Permission gating (only USERS_CREATE can search/provision)
- LDAP-disabled and short-query rejections
- Service-unreachable surfaces as 503, not 200 empty
- Provision creates the user with auth_source=ldap, password_hash=None
- Provision applies the same group mapping as the auto-provision login path
- Duplicate-username protection (409 with explanation)
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.ldap_service import LDAPSearchResult, LDAPUserInfo

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _seed_ldap_settings(db: AsyncSession, **overrides) -> None:
    """Write a minimal but valid LDAP config to the settings table."""
    defaults = {
        "ldap_enabled": "true",
        "ldap_server_url": "ldaps://ldap.test.example:636",  # pragma: allowlist secret — test fixture
        "ldap_bind_dn": "cn=admin,dc=test,dc=com",  # pragma: allowlist secret — test fixture
        "ldap_bind_password": "x",  # pragma: allowlist secret — test fixture
        "ldap_search_base": "dc=test,dc=com",
        "ldap_user_filter": "(uid={username})",
        "ldap_security": "ldaps",
        "ldap_group_mapping": "{}",
        "ldap_auto_provision": "false",
        "ldap_ca_cert_path": "",
        "ldap_default_group": "",
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        db.add(Settings(key=key, value=value))
    await db.commit()


@pytest.fixture
async def admin_token(async_client: AsyncClient) -> str:
    """Enable auth, create an admin, return a valid bearer token."""
    # pragma: allowlist secret — test fixture only, not a real credential
    test_password = "AdminPass1!"  # noqa: S105
    await async_client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": "ldapadmin",
            "admin_password": test_password,
        },
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "ldapadmin", "password": test_password},
    )
    return login.json()["access_token"]


# ---------------------------------------------------------------------------
# /auth/ldap/search
# ---------------------------------------------------------------------------


class TestLdapSearchRoute:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_requires_auth(self, async_client: AsyncClient, db_session: AsyncSession):
        """Anonymous access is rejected when auth is enabled."""
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "x",
                "admin_password": "AdminPass1!",
            },  # pragma: allowlist secret — test fixture
        )

        response = await async_client.get("/api/v1/auth/ldap/search?q=jdoe")

        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_short_query(self, async_client: AsyncClient, admin_token: str, db_session: AsyncSession):
        """Single-char queries would be effectively unbounded against a large directory."""
        await _seed_ldap_settings(db_session)

        response = await async_client.get(
            "/api/v1/auth/ldap/search?q=j",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 400
        assert "at least 2 characters" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_when_ldap_disabled(
        self, async_client: AsyncClient, admin_token: str, db_session: AsyncSession
    ):
        """No LDAP config in settings → 400 with a clear message."""
        response = await async_client.get(
            "/api/v1/auth/ldap/search?q=jdoe",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 400
        assert "LDAP is not enabled" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_surfaces_unreachable_as_503(
        self, async_client: AsyncClient, admin_token: str, db_session: AsyncSession
    ):
        """When the underlying search fails (network/auth), the admin gets 503 — not
        a silent empty list (which would look like 'no matches')."""
        await _seed_ldap_settings(db_session)

        with patch(
            "backend.app.services.ldap_service.search_ldap_users",
            side_effect=RuntimeError("simulated outage"),
        ):
            response = await async_client.get(
                "/api/v1/auth/ldap/search?q=jdoe",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 503
        # Detail now includes the underlying exception class + message so the
        # admin can see why (e.g. "LDAP search failed: RuntimeError: simulated outage").
        detail = response.json()["detail"].lower()
        assert "ldap search failed" in detail
        assert "simulated outage" in detail

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_returns_results_annotated_with_already_provisioned(
        self, async_client: AsyncClient, admin_token: str, db_session: AsyncSession
    ):
        """Results that match an existing local row must come back with the flag set."""
        await _seed_ldap_settings(db_session)

        # Seed an existing local user that shares a username with one LDAP result.
        db_session.add(User(username="existing", email="x@test.com", password_hash="$x$", role="user"))
        await db_session.commit()

        fake_results = [
            LDAPSearchResult(
                username="jdoe",
                email="jdoe@test.com",
                display_name="John Doe",
                dn="cn=John Doe,dc=test,dc=com",
            ),
            LDAPSearchResult(
                username="existing",
                email="existing@test.com",
                display_name="Already Provisioned",
                dn="cn=existing,dc=test,dc=com",
            ),
        ]

        with patch(
            "backend.app.services.ldap_service.search_ldap_users",
            return_value=fake_results,
        ):
            response = await async_client.get(
                "/api/v1/auth/ldap/search?q=jdoe",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        by_user = {r["username"]: r for r in body}
        assert by_user["jdoe"]["already_provisioned"] is False
        assert by_user["existing"]["already_provisioned"] is True


# ---------------------------------------------------------------------------
# /auth/ldap/provision
# ---------------------------------------------------------------------------


class TestLdapProvisionRoute:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_requires_auth(self, async_client: AsyncClient):
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "x",
                "admin_password": "AdminPass1!",
            },  # pragma: allowlist secret — test fixture
        )

        response = await async_client.post(
            "/api/v1/auth/ldap/provision",
            json={"username": "jdoe"},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_404_when_directory_lookup_misses(
        self, async_client: AsyncClient, admin_token: str, db_session: AsyncSession
    ):
        await _seed_ldap_settings(db_session)

        with patch("backend.app.services.ldap_service.lookup_ldap_user", return_value=None):
            response = await async_client.post(
                "/api/v1/auth/ldap/provision",
                json={"username": "nobody"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 404
        assert "not found in LDAP directory" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_409_when_local_user_exists(
        self, async_client: AsyncClient, admin_token: str, db_session: AsyncSession
    ):
        """A local user with the same username must block provision — the admin has
        to resolve the collision manually rather than silently coexisting."""
        await _seed_ldap_settings(db_session)

        db_session.add(User(username="jdoe", password_hash="$x$", role="user", auth_source="local"))
        await db_session.commit()

        fake_ldap = LDAPUserInfo(username="jdoe", email="jdoe@test.com", display_name=None, groups=[])
        with patch("backend.app.services.ldap_service.lookup_ldap_user", return_value=fake_ldap):
            response = await async_client.post(
                "/api/v1/auth/ldap/provision",
                json={"username": "jdoe"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 409
        assert "local user" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_409_when_already_provisioned(
        self, async_client: AsyncClient, admin_token: str, db_session: AsyncSession
    ):
        """Re-provisioning an existing LDAP user must give a distinct error so the
        UI can suggest 'they exist already, just have them log in' rather than
        the more alarming 'local conflict' message."""
        await _seed_ldap_settings(db_session)

        db_session.add(User(username="alice", password_hash=None, role="user", auth_source="ldap"))
        await db_session.commit()

        fake_ldap = LDAPUserInfo(username="alice", email="alice@test.com", display_name=None, groups=[])
        with patch("backend.app.services.ldap_service.lookup_ldap_user", return_value=fake_ldap):
            response = await async_client.post(
                "/api/v1/auth/ldap/provision",
                json={"username": "alice"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 409
        assert "already provisioned" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_503_when_directory_unreachable(
        self, async_client: AsyncClient, admin_token: str, db_session: AsyncSession
    ):
        await _seed_ldap_settings(db_session)

        with patch(
            "backend.app.services.ldap_service.lookup_ldap_user",
            side_effect=RuntimeError("simulated outage"),
        ):
            response = await async_client.post(
                "/api/v1/auth/ldap/provision",
                json={"username": "jdoe"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 503

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_happy_path_creates_user_with_ldap_auth_source(
        self, async_client: AsyncClient, admin_token: str, db_session: AsyncSession
    ):
        """Verifies the full provision: response shape + DB state."""
        await _seed_ldap_settings(db_session)

        fake_ldap = LDAPUserInfo(
            username="newuser",
            email="newuser@test.com",
            display_name="New User",
            groups=[],
        )

        with patch("backend.app.services.ldap_service.lookup_ldap_user", return_value=fake_ldap):
            response = await async_client.post(
                "/api/v1/auth/ldap/provision",
                json={"username": "newuser"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 201
        body = response.json()
        assert body["username"] == "newuser"
        assert body["email"] == "newuser@test.com"
        assert body["auth_source"] == "ldap"

        # Verify DB state: password_hash MUST be None (LDAP has no local credential)
        from sqlalchemy import select

        row = (await db_session.execute(select(User).where(User.username == "newuser"))).scalar_one()
        assert row.auth_source == "ldap"
        assert row.password_hash is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_happy_path_applies_group_mapping(
        self, async_client: AsyncClient, admin_token: str, db_session: AsyncSession
    ):
        """Provision must run the same group-mapping logic as the auto-provision
        login path — so an admin who provisions Alice gets the exact same group
        memberships as if Alice had logged in herself with auto-provision on."""
        await _seed_ldap_settings(
            db_session,
            ldap_group_mapping='{"cn=staff,ou=groups,dc=test,dc=com": "Operators"}',
        )

        # Operators group is auto-seeded by the test harness — no need to create it.
        fake_ldap = LDAPUserInfo(
            username="alice",
            email="alice@test.com",
            display_name="Alice",
            groups=["cn=staff,ou=groups,dc=test,dc=com"],
        )

        with patch("backend.app.services.ldap_service.lookup_ldap_user", return_value=fake_ldap):
            response = await async_client.post(
                "/api/v1/auth/ldap/provision",
                json={"username": "alice"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 201
        body = response.json()
        group_names = {g["name"] for g in body["groups"]}
        assert "Operators" in group_names
