"""Cloud preset lookups belong to the cloud account they were read with."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _empty_cache():
    from backend.app.api.routes import cloud

    cloud.forget_all_cloud_presets()
    yield
    cloud.forget_all_cloud_presets()


def _cloud(presets: dict[str, str]):
    details = {
        f"S{i}": {"filament_id": fid, "name": f"{name} @Bambu Lab X1C"} for i, (fid, name) in enumerate(presets.items())
    }
    return SimpleNamespace(
        is_authenticated=True,
        get_slicer_settings=AsyncMock(return_value={"filament": {"private": [{"setting_id": s} for s in details]}}),
        get_setting_detail=AsyncMock(side_effect=lambda setting_id: details[setting_id]),
        close=AsyncMock(),
    )


async def test_each_caller_gets_the_map_of_their_own_cloud_account(async_client: AsyncClient):
    token = await _admin_token(async_client)
    group = await _group(async_client, token, "readers", permissions=["filaments:read"])
    anna, anna_id = await _user(async_client, token, "anna", [group])
    ben, _ = await _user(async_client, token, "ben", [group])
    cara, cara_id = await _user(async_client, token, "cara", [group])
    accounts = {anna_id: {"P1": "Anna Private PLA"}, cara_id: {"P2": "Cara Silk"}}

    async def build(db, user):
        return _cloud(accounts[user.id]) if user is not None and user.id in accounts else None

    with patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=build):
        maps = {
            name: (await async_client.get("/api/v1/cloud/filament-id-map", headers=_auth(jwt))).json()
            for name, jwt in (("anna", anna), ("ben", ben), ("cara", cara), ("anna again", anna))
        }

    assert maps == {
        "anna": {"P1": "Anna Private PLA"},
        "ben": {},
        "cara": {"P2": "Cara Silk"},
        "anna again": {"P1": "Anna Private PLA"},
    }


async def test_signing_out_of_the_cloud_drops_the_account_map(async_client: AsyncClient):
    token = await _admin_token(async_client)
    group = await _group(async_client, token, "readers", permissions=["filaments:read", "cloud:auth"])
    anna, _ = await _user(async_client, token, "anna", [group])
    signed_in = {"yes": True}

    async def build(db, user):
        return _cloud({"P1": "Anna Private PLA"}) if signed_in["yes"] else None

    with patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=build):
        before = (await async_client.get("/api/v1/cloud/filament-id-map", headers=_auth(anna))).json()
        signed_in["yes"] = False
        logout = await async_client.post("/api/v1/cloud/logout", headers=_auth(anna))
        after = (await async_client.get("/api/v1/cloud/filament-id-map", headers=_auth(anna))).json()

    assert logout.status_code == 200, logout.text
    assert (before, after) == ({"P1": "Anna Private PLA"}, {})


async def test_signing_in_again_drops_the_account_map(db_session):
    from backend.app.api.routes import cloud

    cloud._filament_id_name_cache[None] = (0.0, {"P1": "Old account"})
    cloud._filament_id_name_cache[7] = (0.0, {"P9": "Someone else"})
    cloud._filament_cache[(None, "P1")] = {"name": "Old account", "k": None}
    cloud._filament_cache[(7, "P9")] = {"name": "Someone else", "k": None}

    await cloud.store_token(db_session, "new-token", "a@example.com", "global", None)

    assert list(cloud._filament_id_name_cache) == [7]
    assert list(cloud._filament_cache) == [(7, "P9")]


async def test_a_preset_one_account_resolved_is_not_served_to_another(async_client: AsyncClient):
    token = await _admin_token(async_client)
    group = await _group(async_client, token, "readers", permissions=["filaments:read"])
    anna, anna_id = await _user(async_client, token, "anna", [group])
    ben, _ = await _user(async_client, token, "ben", [group])
    preset = {"name": "Anna Private PLA @Bambu Lab X1C", "setting": {"pressure_advance": "0.031"}}

    async def build(db, user):
        if user is None or user.id != anna_id:
            return None
        return SimpleNamespace(
            is_authenticated=True, get_setting_detail=AsyncMock(return_value=preset), close=AsyncMock()
        )

    with patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=build):
        for_anna = (
            await async_client.post("/api/v1/cloud/filament-info", headers=_auth(anna), json=["P5a1b2c3"])
        ).json()
        for_ben = (await async_client.post("/api/v1/cloud/filament-info", headers=_auth(ben), json=["P5a1b2c3"])).json()

    assert for_anna["P5a1b2c3"]["name"] == "Anna Private PLA @Bambu Lab X1C"
    assert for_ben["P5a1b2c3"]["name"] != for_anna["P5a1b2c3"]["name"]
    assert for_ben["P5a1b2c3"]["k"] is None


async def test_a_filament_id_inside_the_preset_json_is_read_too(async_client: AsyncClient):
    """Same fallback as the slicer filament resolver: the preset JSON under
    ``setting`` can carry the id when the envelope does not."""
    token = await _admin_token(async_client)
    group = await _group(async_client, token, "readers", permissions=["filaments:read"])
    anna, _ = await _user(async_client, token, "anna", [group])
    cloud = SimpleNamespace(
        is_authenticated=True,
        get_slicer_settings=AsyncMock(return_value={"filament": {"private": [{"setting_id": "S0"}]}}),
        get_setting_detail=AsyncMock(
            return_value={"filament_id": None, "name": "Nested PLA @Bambu Lab X1C", "setting": {"filament_id": "P9"}}
        ),
        close=AsyncMock(),
    )

    async def build(db, user):
        return cloud

    with patch("backend.app.api.routes.cloud.build_authenticated_cloud", new=build):
        body = (await async_client.get("/api/v1/cloud/filament-id-map", headers=_auth(anna))).json()

    assert body == {"P9": "Nested PLA"}
