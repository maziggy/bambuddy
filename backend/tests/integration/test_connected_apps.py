"""Connected apps: sign-in to external applications with a Bambuddy account.

These lock down what a code is worth: single use, 60 seconds, one app, its
exact callback URL, the PKCE challenge, and only with the app's secret. And
that none of it works while Bambuddy authentication is disabled.
"""

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

CALLBACK = "http://orders.local:8090/auth/callback"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def admin_token(async_client: AsyncClient) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "connectadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "connectadmin", "password": "AdminPass1!"})
    return login.json()["access_token"]


@pytest.fixture
async def operator(async_client: AsyncClient, admin_token: str) -> dict:
    groups = (await async_client.get("/api/v1/groups/", headers=_auth(admin_token))).json()
    operators = next(g for g in groups if g["name"] == "Operators")
    user = (
        await async_client.post(
            "/api/v1/users/",
            headers=_auth(admin_token),
            json={"username": "shopworker", "password": "Operatorpass1!", "group_ids": [operators["id"]]},
        )
    ).json()
    login = await async_client.post("/api/v1/auth/login", json={"username": "shopworker", "password": "Operatorpass1!"})
    return {"id": user["id"], "token": login.json()["access_token"]}


async def _register(async_client: AsyncClient, admin_token: str, **overrides) -> dict:
    payload = {"name": "Bambuddy Orders", "redirect_uri": CALLBACK, **overrides}
    response = await async_client.post("/api/v1/connect/apps", headers=_auth(admin_token), json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _authorize(async_client: AsyncClient, user_token: str, app: dict, challenge: str, **overrides):
    payload = {
        "client_id": app["client_id"],
        "redirect_uri": app["redirect_uri"],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **overrides,
    }
    return await async_client.post("/api/v1/connect/authorize", headers=_auth(user_token), json=payload)


async def _code(async_client: AsyncClient, user_token: str, app: dict) -> tuple[str, str]:
    verifier, challenge = _pkce()
    response = await _authorize(async_client, user_token, app, challenge)
    assert response.status_code == 200, response.text
    return response.json()["code"], verifier


async def _exchange(async_client: AsyncClient, app: dict, code: str, verifier: str, **overrides):
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": app["redirect_uri"],
        "client_id": app["client_id"],
        "client_secret": app["client_secret"],
        "code_verifier": verifier,
        **overrides,
    }
    # No Authorization header: the app authenticates with its secret alone.
    return await async_client.post("/api/v1/connect/token", json=payload)


class TestRegistration:
    async def test_secret_is_shown_once(self, async_client, admin_token):
        app = await _register(async_client, admin_token)
        assert app["client_id"].startswith("bba_")
        assert app["client_secret"].startswith("bbs_")
        listed = (await async_client.get("/api/v1/connect/apps", headers=_auth(admin_token))).json()
        assert [a["client_id"] for a in listed] == [app["client_id"]]
        assert "client_secret" not in listed[0]

    async def test_refused_while_authentication_is_disabled(self, async_client):
        response = await async_client.post("/api/v1/connect/apps", json={"name": "Orders", "redirect_uri": CALLBACK})
        assert response.status_code == 400

    async def test_non_admin_cannot_register(self, async_client, operator):
        response = await async_client.post(
            "/api/v1/connect/apps",
            headers=_auth(operator["token"]),
            json={"name": "Orders", "redirect_uri": CALLBACK},
        )
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "redirect_uri",
        [
            "javascript:alert(1)",
            "/relative/callback",
            "http://orders.local/cb#frag",
            "http://user:pw@orders.local/cb",
            "ftp://orders.local/cb",
        ],
    )
    async def test_callback_must_be_a_plain_absolute_http_url(self, async_client, admin_token, redirect_uri):
        response = await async_client.post(
            "/api/v1/connect/apps",
            headers=_auth(admin_token),
            json={"name": "Orders", "redirect_uri": redirect_uri},
        )
        assert response.status_code == 422


class TestSignIn:
    async def test_full_flow_returns_identity_and_permissions(self, async_client, admin_token, operator):
        app = await _register(async_client, admin_token)

        info = await async_client.get(
            "/api/v1/connect/authorize/info",
            headers=_auth(operator["token"]),
            params={"client_id": app["client_id"], "redirect_uri": CALLBACK},
        )
        assert info.status_code == 200
        assert info.json() == {"app_name": "Bambuddy Orders", "username": "shopworker", "already_granted": False}

        code, verifier = await _code(async_client, operator["token"], app)
        response = await _exchange(async_client, app, code, verifier)
        assert response.status_code == 200, response.text
        user = response.json()["user"]
        assert user["id"] == operator["id"]
        assert user["username"] == "shopworker"
        assert user["is_admin"] is False
        assert user["groups"] == ["Operators"]
        assert "queue:create" in user["permissions"]

        info_after = await async_client.get(
            "/api/v1/connect/authorize/info",
            headers=_auth(operator["token"]),
            params={"client_id": app["client_id"], "redirect_uri": CALLBACK},
        )
        assert info_after.json()["already_granted"] is True

    async def test_authorize_needs_a_login_not_an_api_key(self, async_client, admin_token):
        app = await _register(async_client, admin_token)
        key = (
            await async_client.post("/api/v1/api-keys/", headers=_auth(admin_token), json={"name": "script"})
        ).json()["key"]
        _, challenge = _pkce()
        for headers in ({"X-API-Key": key}, _auth(key)):
            response = await async_client.post(
                "/api/v1/connect/authorize",
                headers=headers,
                json={
                    "client_id": app["client_id"],
                    "redirect_uri": CALLBACK,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                },
            )
            assert response.status_code == 401

    async def test_callback_must_match_exactly_at_authorize(self, async_client, admin_token, operator):
        app = await _register(async_client, admin_token)
        _, challenge = _pkce()
        for wrong in (CALLBACK + "/", CALLBACK + "?x=1", "http://evil.example/auth/callback"):
            response = await _authorize(async_client, operator["token"], app, challenge, redirect_uri=wrong)
            assert response.status_code == 400

    async def test_only_s256_is_accepted(self, async_client, admin_token, operator):
        app = await _register(async_client, admin_token)
        verifier, _ = _pkce()
        response = await _authorize(async_client, operator["token"], app, verifier[:43], code_challenge_method="plain")
        assert response.status_code == 422


class TestCodeIsWorthLittle:
    async def test_code_is_single_use(self, async_client, admin_token, operator):
        app = await _register(async_client, admin_token)
        code, verifier = await _code(async_client, operator["token"], app)
        assert (await _exchange(async_client, app, code, verifier)).status_code == 200
        replay = await _exchange(async_client, app, code, verifier)
        assert replay.status_code == 400
        assert replay.json()["detail"] == {"error": "invalid_grant"}

    async def test_expired_code_is_refused(self, async_client, admin_token, operator, db_session):
        from sqlalchemy import update

        from backend.app.models.auth_ephemeral import AuthEphemeralToken, TokenType

        app = await _register(async_client, admin_token)
        code, verifier = await _code(async_client, operator["token"], app)
        await db_session.execute(
            update(AuthEphemeralToken)
            .where(AuthEphemeralToken.token_type == TokenType.CONNECT_CODE)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await db_session.commit()
        assert (await _exchange(async_client, app, code, verifier)).status_code == 400

    async def test_code_is_not_stored_in_plain(self, async_client, admin_token, operator, db_session):
        from sqlalchemy import select

        from backend.app.models.auth_ephemeral import AuthEphemeralToken, TokenType

        app = await _register(async_client, admin_token)
        code, _ = await _code(async_client, operator["token"], app)
        stored = (
            await db_session.execute(
                select(AuthEphemeralToken.token).where(AuthEphemeralToken.token_type == TokenType.CONNECT_CODE)
            )
        ).scalar_one()
        assert stored != code

    async def test_wrong_verifier_spends_the_code(self, async_client, admin_token, operator):
        app = await _register(async_client, admin_token)
        code, verifier = await _code(async_client, operator["token"], app)
        other_verifier, _ = _pkce()
        assert (await _exchange(async_client, app, code, other_verifier)).status_code == 400
        # A failed attempt burns the code, so the right verifier can't be tried next.
        assert (await _exchange(async_client, app, code, verifier)).status_code == 400

    async def test_wrong_callback_at_exchange_is_refused(self, async_client, admin_token, operator):
        app = await _register(async_client, admin_token)
        code, verifier = await _code(async_client, operator["token"], app)
        response = await _exchange(async_client, app, code, verifier, redirect_uri=CALLBACK + "x")
        assert response.status_code == 400

    async def test_wrong_secret_is_invalid_client(self, async_client, admin_token, operator):
        app = await _register(async_client, admin_token)
        code, verifier = await _code(async_client, operator["token"], app)
        response = await _exchange(async_client, app, code, verifier, client_secret="bbs_wrong")
        assert response.status_code == 401
        assert response.json()["detail"] == {"error": "invalid_client"}
        # The code survives a caller that can't prove who it is.
        assert (await _exchange(async_client, app, code, verifier)).status_code == 200

    async def test_unknown_client_is_invalid_client(self, async_client, admin_token, operator):
        app = await _register(async_client, admin_token)
        code, verifier = await _code(async_client, operator["token"], app)
        response = await _exchange(async_client, app, code, verifier, client_id="bba_unknown")
        assert response.status_code == 401

    async def test_one_apps_code_is_useless_to_another(self, async_client, admin_token, operator):
        app_a = await _register(async_client, admin_token, name="A")
        app_b = await _register(async_client, admin_token, name="B")
        code, verifier = await _code(async_client, operator["token"], app_a)
        assert (await _exchange(async_client, app_b, code, verifier)).status_code == 400

    async def test_user_disabled_after_consent_is_refused(self, async_client, admin_token, operator):
        app = await _register(async_client, admin_token)
        code, verifier = await _code(async_client, operator["token"], app)
        await async_client.patch(
            f"/api/v1/users/{operator['id']}", headers=_auth(admin_token), json={"is_active": False}
        )
        assert (await _exchange(async_client, app, code, verifier)).status_code == 400


class TestAppLifecycle:
    async def test_disabled_app_can_neither_authorize_nor_exchange(self, async_client, admin_token, operator):
        app = await _register(async_client, admin_token)
        code, verifier = await _code(async_client, operator["token"], app)
        await async_client.patch(
            f"/api/v1/connect/apps/{app['id']}", headers=_auth(admin_token), json={"enabled": False}
        )
        _, challenge = _pkce()
        assert (await _authorize(async_client, operator["token"], app, challenge)).status_code == 400
        assert (await _exchange(async_client, app, code, verifier)).status_code == 401

    async def test_rotated_secret_replaces_the_old_one(self, async_client, admin_token, operator):
        app = await _register(async_client, admin_token)
        rotated = (
            await async_client.post(f"/api/v1/connect/apps/{app['id']}/rotate-secret", headers=_auth(admin_token))
        ).json()
        assert rotated["client_secret"] != app["client_secret"]
        code, verifier = await _code(async_client, operator["token"], app)
        assert (await _exchange(async_client, app, code, verifier)).status_code == 401
        assert (
            await _exchange(async_client, app, code, verifier, client_secret=rotated["client_secret"])
        ).status_code == 200

    async def test_changed_callback_invalidates_codes_for_the_old_one(self, async_client, admin_token, operator):
        app = await _register(async_client, admin_token)
        code, verifier = await _code(async_client, operator["token"], app)
        await async_client.patch(
            f"/api/v1/connect/apps/{app['id']}",
            headers=_auth(admin_token),
            json={"redirect_uri": "http://orders.local:9000/cb"},
        )
        assert (await _exchange(async_client, app, code, verifier)).status_code == 400

    async def test_deleting_the_app_removes_grants_and_codes(self, async_client, admin_token, operator, db_session):
        from sqlalchemy import func, select

        from backend.app.models.auth_ephemeral import AuthEphemeralToken, TokenType
        from backend.app.models.connected_app import ConnectedAppGrant

        app = await _register(async_client, admin_token)
        await _code(async_client, operator["token"], app)
        response = await async_client.delete(f"/api/v1/connect/apps/{app['id']}", headers=_auth(admin_token))
        assert response.status_code == 200
        grants = await db_session.execute(select(func.count()).select_from(ConnectedAppGrant))
        codes = await db_session.execute(
            select(func.count())
            .select_from(AuthEphemeralToken)
            .where(AuthEphemeralToken.token_type == TokenType.CONNECT_CODE)
        )
        assert grants.scalar_one() == 0
        assert codes.scalar_one() == 0


class TestAuthenticationDisabled:
    async def test_authorize_and_token_say_auth_disabled(self, async_client):
        info = await async_client.get(
            "/api/v1/connect/authorize/info", params={"client_id": "bba_x", "redirect_uri": CALLBACK}
        )
        assert info.status_code == 409
        assert info.json()["detail"] == "auth_disabled"
        verifier, _ = _pkce()
        token = await async_client.post(
            "/api/v1/connect/token",
            json={
                "grant_type": "authorization_code",
                "code": "x",
                "redirect_uri": CALLBACK,
                "client_id": "bba_x",
                "client_secret": "bbs_x",
                "code_verifier": verifier,
            },
        )
        assert token.status_code == 400
        assert token.json()["detail"] == {"error": "auth_disabled"}


class TestRateLimit:
    async def test_repeated_failures_lock_the_client_out(self, async_client, admin_token, operator):
        from backend.app.api.routes.connected_apps import MAX_FAILED_TOKEN_EXCHANGES

        app = await _register(async_client, admin_token)
        verifier, _ = _pkce()
        for _ in range(MAX_FAILED_TOKEN_EXCHANGES):
            response = await _exchange(async_client, app, "not-a-code", verifier)
            assert response.status_code == 400
        code, good_verifier = await _code(async_client, operator["token"], app)
        assert (await _exchange(async_client, app, code, good_verifier)).status_code == 429
