"""The app shell can read install configuration without settings:read (#3023).

Reporter @lonix: a user holding `cost_centers:read_own` never saw the Finance
entry in the sidebar. The permission map was right and the route guard was
right -- navigating to /finance directly worked and showed their balance. What
hid it was an extra condition, `billing_enabled !== true`, read from
GET /settings, which requires SETTINGS_READ. A non-admin gets 403 there, so the
value arrived undefined and the entry was hidden from exactly the users the
permission exists to serve.

SETTINGS_READ cannot be the price of knowing whether billing is on: it also
grants sight of the SMTP, LDAP and MQTT credentials. Hence /settings/ui-flags,
which asks only that the caller be signed in.

It is deliberately not more fields on /settings/ui-preferences. That endpoint is
served to anyone at all, on the recorded grounds that its contents are "public
defaults that ship with the app" (test_route_auth_coverage.py), and its field
set is pinned by a test written to stop exactly this kind of addition. These
fields are not defaults -- they say how this deployment is configured -- so the
last test here pins that they did not leak into it.
"""

import secrets

import pytest
from httpx import AsyncClient

from backend.app.models.settings import Settings

FLAGS_URL = "/api/v1/settings/ui-flags"
_FIXTURE_PW = "Aa1!" + secrets.token_urlsafe(12)  # pragma: allowlist secret


async def _setup_admin(async_client: AsyncClient, username: str) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": username, "admin_password": _FIXTURE_PW},
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": _FIXTURE_PW},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _create_operator(
    async_client: AsyncClient,
    admin_token: str,
    *,
    username: str,
    permissions: list[str],
) -> str:
    """A non-admin holding exactly `permissions` -- never settings:read."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    grp = await async_client.post(
        "/api/v1/groups/",
        headers=headers,
        json={"name": f"ui_flags_test_{username}", "permissions": permissions},
    )
    assert grp.status_code == 201, grp.text
    user = await async_client.post(
        "/api/v1/users/",
        headers=headers,
        json={
            "username": username,
            "password": _FIXTURE_PW,
            "role": "user",
            "group_ids": [grp.json()["id"]],
        },
    )
    assert user.status_code == 201, user.text
    assert user.json()["is_admin"] is False
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": _FIXTURE_PW},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


@pytest.mark.integration
class TestTheUserTheEndpointExistsFor:
    """A non-admin with cost_centers:read_own and nothing else."""

    @pytest.mark.asyncio
    async def test_they_can_read_the_flags(self, async_client: AsyncClient):
        admin = await _setup_admin(async_client, "flagadmin1")
        op = await _create_operator(async_client, admin, username="flagop1", permissions=["cost_centers:read_own"])

        resp = await async_client.get(FLAGS_URL, headers={"Authorization": f"Bearer {op}"})
        assert resp.status_code == 200, resp.text
        assert "billing_enabled" in resp.json()

    @pytest.mark.asyncio
    async def test_they_still_cannot_read_settings(self, async_client: AsyncClient):
        """The fix must not have widened SETTINGS_READ to get there."""
        admin = await _setup_admin(async_client, "flagadmin2")
        op = await _create_operator(async_client, admin, username="flagop2", permissions=["cost_centers:read_own"])

        resp = await async_client.get("/api/v1/settings/", headers={"Authorization": f"Bearer {op}"})
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_billing_enabled_carries_the_configured_value(self, async_client: AsyncClient, db_session):
        """The whole point: the sidebar tests this for `true`, so it has to be
        the real value and a real bool, not a truthy string."""
        admin = await _setup_admin(async_client, "flagadmin3")
        op = await _create_operator(async_client, admin, username="flagop3", permissions=["cost_centers:read_own"])
        db_session.add(Settings(key="billing_enabled", value="true"))
        await db_session.commit()

        resp = await async_client.get(FLAGS_URL, headers={"Authorization": f"Bearer {op}"})
        assert resp.json()["billing_enabled"] is True


@pytest.mark.integration
class TestTheBoundaryItDraws:
    """Signed in is required; settings:read is not."""

    @pytest.mark.asyncio
    async def test_an_anonymous_caller_is_refused_when_auth_is_on(self, async_client: AsyncClient):
        """This is the reason it is a separate endpoint rather than four more
        fields on the public one."""
        await _setup_admin(async_client, "flagadmin4")

        resp = await async_client.get(FLAGS_URL)
        assert resp.status_code in (401, 403), resp.text

    @pytest.mark.asyncio
    async def test_it_answers_when_auth_is_switched_off(self, async_client: AsyncClient):
        """An install with no auth has no user to authenticate, and the shell
        still has to render. require_auth_if_enabled returns None there."""
        resp = await async_client.get(FLAGS_URL)
        assert resp.status_code == 200, resp.text


@pytest.mark.integration
class TestWhatItExposes:
    @pytest.mark.asyncio
    async def test_the_field_set_is_exactly_these_four(self, async_client: AsyncClient):
        """Pinned like the /ui-preferences set: anything added here is readable
        by every signed-in user, so adding one should require editing this."""
        resp = await async_client.get(FLAGS_URL)
        assert set(resp.json().keys()) == {
            "billing_enabled",
            "user_notifications_enabled",
            "currency",
            "check_updates",
        }

    @pytest.mark.asyncio
    async def test_no_credential_ever_appears(self, async_client: AsyncClient, db_session):
        for i, key in enumerate(
            ("smtp_password", "ldap_bind_password", "mqtt_password", "ha_token", "prometheus_token")
        ):
            db_session.add(Settings(key=key, value=f"SECRET_VALUE_{i}_DO_NOT_LEAK"))
        await db_session.commit()

        body = (await async_client.get(FLAGS_URL)).text
        assert "DO_NOT_LEAK" not in body

    @pytest.mark.asyncio
    async def test_the_public_endpoint_did_not_gain_them(self, async_client: AsyncClient):
        """These describe the deployment, not app defaults, so they must not
        have been added to the endpoint that serves anyone at all."""
        public = (await async_client.get("/api/v1/settings/ui-preferences")).json()
        assert "billing_enabled" not in public
        assert "user_notifications_enabled" not in public
