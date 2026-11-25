"""Group filament rules over the API (#3522)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


async def _admin_token(async_client: AsyncClient) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "filadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "filadmin", "password": "AdminPass1!"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


class TestGroupFilamentRules:
    async def test_defaults_on_a_new_group(self, async_client):
        admin = await _admin_token(async_client)
        created = await async_client.post("/api/v1/groups/", headers=_auth(admin), json={"name": "Plain"})
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["restrict_filaments"] is False
        assert body["filament_rule_mode"] == "block"
        assert body["filament_rules"] == []

    async def test_round_trip(self, async_client):
        admin = await _admin_token(async_client)
        created = await async_client.post(
            "/api/v1/groups/",
            headers=_auth(admin),
            json={
                "name": "Friends",
                "restrict_filaments": True,
                "filament_rule_mode": "block",
                "filament_rules": [
                    {"match": "material", "value": " paht-cf "},
                    {"match": "filament_id", "value": "GFG03"},
                    {"match": "material", "value": "PAHT-CF"},
                ],
            },
        )
        assert created.status_code == 201, created.text
        group_id = created.json()["id"]
        assert created.json()["filament_rules"] == [
            {"match": "material", "value": "PAHT-CF"},
            {"match": "filament_id", "value": "GFG03"},
        ]

        updated = await async_client.patch(
            f"/api/v1/groups/{group_id}",
            headers=_auth(admin),
            json={"filament_rule_mode": "allow", "filament_rules": [{"match": "material", "value": "PLA"}]},
        )
        assert updated.status_code == 200, updated.text
        detail = (await async_client.get(f"/api/v1/groups/{group_id}", headers=_auth(admin))).json()
        assert detail["restrict_filaments"] is True
        assert detail["filament_rule_mode"] == "allow"
        assert detail["filament_rules"] == [{"match": "material", "value": "PLA"}]

    async def test_turning_off_keeps_the_rules(self, async_client):
        admin = await _admin_token(async_client)
        created = await async_client.post(
            "/api/v1/groups/",
            headers=_auth(admin),
            json={
                "name": "Keep",
                "restrict_filaments": True,
                "filament_rules": [{"match": "material", "value": "PETG"}],
            },
        )
        group_id = created.json()["id"]
        assert created.status_code == 201, created.text
        off = await async_client.patch(
            f"/api/v1/groups/{group_id}", headers=_auth(admin), json={"restrict_filaments": False}
        )
        assert off.status_code == 200, off.text
        assert off.json()["restrict_filaments"] is False
        assert off.json()["filament_rules"] == [{"match": "material", "value": "PETG"}]

    async def test_bad_rule_kind_is_rejected(self, async_client):
        admin = await _admin_token(async_client)
        response = await async_client.post(
            "/api/v1/groups/",
            headers=_auth(admin),
            json={"name": "Bad", "filament_rules": [{"match": "colour", "value": "red"}]},
        )
        assert response.status_code == 422

    async def test_whitespace_only_rule_is_rejected(self, async_client):
        """Not stored as nothing: a blank allow-list rule would block every filament."""
        admin = await _admin_token(async_client)
        response = await async_client.post(
            "/api/v1/groups/",
            headers=_auth(admin),
            json={"name": "Blank", "filament_rules": [{"match": "material", "value": "   "}]},
        )
        assert response.status_code == 422, response.text

    async def test_administrators_cannot_be_restricted(self, async_client):
        admin = await _admin_token(async_client)
        groups = (await async_client.get("/api/v1/groups/", headers=_auth(admin))).json()
        admins = next(g for g in groups if g["name"] == "Administrators")
        response = await async_client.patch(
            f"/api/v1/groups/{admins['id']}", headers=_auth(admin), json={"restrict_filaments": True}
        )
        assert response.status_code == 400
