"""/auth/me tells the browser the caller's filament rules (#3522)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


async def _admin_token(async_client: AsyncClient) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "meadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "meadmin", "password": "AdminPass1!"})
    return login.json()["access_token"]


async def _login(async_client: AsyncClient, admin: str, username: str, group_ids: list[int]) -> str:
    created = await async_client.post(
        "/api/v1/users/",
        headers=_auth(admin),
        json={"username": username, "password": "UserPass1!", "group_ids": group_ids},
    )
    assert created.status_code in (200, 201), created.text
    login = await async_client.post("/api/v1/auth/login", json={"username": username, "password": "UserPass1!"})
    return login.json()["access_token"]


async def _friends_group(async_client: AsyncClient, admin: str) -> int:
    friends = await async_client.post(
        "/api/v1/groups/",
        headers=_auth(admin),
        json={
            "name": "friends",
            "permissions": ["api_keys:create", "users:read"],
            "restrict_filaments": True,
            "filament_rule_mode": "block",
            "filament_rules": [{"match": "filament_id", "value": "GFG03"}],
        },
    )
    assert friends.status_code == 201, friends.text
    return friends.json()["id"]


FRIENDS_POLICY = [{"group": "friends", "mode": "block", "rules": [{"match": "filament_id", "value": "GFG03"}]}]


async def _my_policies(async_client: AsyncClient, headers: dict[str, str]) -> list[dict] | None:
    me = await async_client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    return me.json()["filament_policies"]


class TestMeFilamentPolicies:
    async def test_admin_is_unrestricted(self, async_client):
        admin = await _admin_token(async_client)
        assert await _my_policies(async_client, _auth(admin)) is None

    async def test_user_in_no_restricted_group_is_unrestricted(self, async_client):
        admin = await _admin_token(async_client)
        plain = await async_client.post("/api/v1/groups/", headers=_auth(admin), json={"name": "plain"})
        jwt = await _login(async_client, admin, "plainuser", [plain.json()["id"]])
        assert await _my_policies(async_client, _auth(jwt)) is None

    async def test_restricted_member_gets_their_groups_rules(self, async_client):
        admin = await _admin_token(async_client)
        jwt = await _login(async_client, admin, "friend", [await _friends_group(async_client, admin)])
        assert await _my_policies(async_client, _auth(jwt)) == FRIENDS_POLICY


class TestOtherUserResponses:
    """null means unrestricted, so every builder of the response must fill it (#3522)."""

    async def test_an_api_key_reports_its_owners_rules(self, async_client):
        admin = await _admin_token(async_client)
        jwt = await _login(async_client, admin, "friend", [await _friends_group(async_client, admin)])
        created = await async_client.post("/api/v1/api-keys/", headers=_auth(jwt), json={"name": "native"})
        assert created.status_code in (200, 201), created.text

        assert await _my_policies(async_client, {"X-API-Key": created.json()["key"]}) == FRIENDS_POLICY

    async def test_the_user_list_reports_each_users_rules(self, async_client):
        admin = await _admin_token(async_client)
        await _login(async_client, admin, "friend", [await _friends_group(async_client, admin)])

        users = (await async_client.get("/api/v1/users/", headers=_auth(admin))).json()
        by_name = {u["username"]: u for u in users}
        assert by_name["friend"]["filament_policies"] == FRIENDS_POLICY
        assert by_name["meadmin"]["filament_policies"] is None
