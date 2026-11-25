"""Integration tests for saved label templates (#3296).

``/label-templates`` lists, saves, replaces and deletes named sheet layouts.
Listing needs ``inventory:read``; the writes need ``inventory:update``, like
storage locations.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

# A 3 × 8 sheet of 70 × 36 mm labels on A4, edge to edge (Avery 3490).
LAYOUT_3490 = {
    "page": "a4",
    "label_w_mm": 70.0,
    "label_h_mm": 36.0,
    "cols": 3,
    "rows": 8,
    "top_mm": 4.5,
    "left_mm": 0.0,
}


def _template(name: str = "Avery 3490", **layout) -> dict:
    return {"name": name, "layout": {**LAYOUT_3490, **layout}}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_crud_round_trip(async_client: AsyncClient):
    created = await async_client.post("/api/v1/label-templates", json=_template())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Avery 3490"
    # Gaps omitted from the request are stored as zero and returned as such.
    assert body["layout"] == {**LAYOUT_3490, "col_gap_mm": 0.0, "row_gap_mm": 0.0}

    listed = await async_client.get("/api/v1/label-templates")
    assert [t["id"] for t in listed.json()] == [body["id"]]

    replaced = await async_client.put(
        f"/api/v1/label-templates/{body['id']}",
        json=_template("Avery 3490 (shop)", rows=7, row_gap_mm=2.0),
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["name"] == "Avery 3490 (shop)"
    assert replaced.json()["layout"]["rows"] == 7
    assert replaced.json()["layout"]["row_gap_mm"] == 2.0

    deleted = await async_client.delete(f"/api/v1/label-templates/{body['id']}")
    assert deleted.status_code == 204
    assert (await async_client.get("/api/v1/label-templates")).json() == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_sorts_naturally(async_client: AsyncClient):
    for name in ("Sheet 10", "sheet 2", "Sheet 1"):
        assert (await async_client.post("/api/v1/label-templates", json=_template(name))).status_code == 201
    names = [t["name"] for t in (await async_client.get("/api/v1/label-templates")).json()]
    assert names == ["Sheet 1", "sheet 2", "Sheet 10"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_names_are_unique_ignoring_case_and_spaces(async_client: AsyncClient):
    assert (await async_client.post("/api/v1/label-templates", json=_template("Avery 3490"))).status_code == 201
    dup = await async_client.post("/api/v1/label-templates", json=_template("  avery 3490 "))
    assert dup.status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rename_onto_another_template_is_refused(async_client: AsyncClient):
    await async_client.post("/api/v1/label-templates", json=_template("First"))
    second = (await async_client.post("/api/v1/label-templates", json=_template("Second"))).json()
    resp = await async_client.put(f"/api/v1/label-templates/{second['id']}", json=_template("FIRST"))
    assert resp.status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_changing_only_the_case_of_its_own_name_is_allowed(async_client: AsyncClient):
    created = (await async_client.post("/api/v1/label-templates", json=_template("avery 3490"))).json()
    resp = await async_client.put(f"/api/v1/label-templates/{created['id']}", json=_template("Avery 3490"))
    assert resp.status_code == 200
    assert resp.json()["name"] == "Avery 3490"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    "layout",
    [
        {"cols": 4},  # 4 × 70 mm is wider than A4
        {"rows": 9},  # 4.5 + 9 × 36 mm is taller than A4
        {"left_mm": 1.0},  # pushes the third column 1 mm off the page
        {"label_w_mm": 39.0},  # below the 40 mm a label needs for its text
        {"label_h_mm": 19.0},  # below the 20 mm of the full layout
        {"cols": 0},
        {"top_mm": -1.0},
        {"page": "a3"},
    ],
)
async def test_a_layout_that_cannot_print_is_refused(async_client: AsyncClient, layout):
    resp = await async_client.post("/api/v1/label-templates", json=_template(**layout))
    assert resp.status_code == 422, resp.text
    assert (await async_client.get("/api/v1/label-templates")).json() == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_names_match_whatever_the_unicode_composition(async_client: AsyncClient):
    composed = "Caf\u00e9 sheet"
    combining = "Cafe\u0301 sheet"
    assert (await async_client.post("/api/v1/label-templates", json=_template(composed))).status_code == 201
    assert (await async_client.post("/api/v1/label-templates", json=_template(combining))).status_code == 409
    assert (await async_client.post("/api/v1/label-templates", json=_template("CAFÉ SHEET"))).status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_name_that_casefolds_longer_still_fits(async_client: AsyncClient):
    # "İ" lowercases to two characters; 100 of them make a 200-character key.
    resp = await async_client.post("/api/v1/label-templates", json=_template("\u0130" * 100))
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_blank_name_is_refused(async_client: AsyncClient):
    resp = await async_client.post("/api/v1/label-templates", json=_template("   "))
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.integration
async def test_unknown_id_is_404(async_client: AsyncClient):
    assert (await async_client.put("/api/v1/label-templates/99999", json=_template())).status_code == 404
    assert (await async_client.delete("/api/v1/label-templates/99999")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_routes_require_auth_when_enabled(async_client: AsyncClient):
    """Every route fails closed without credentials once auth is on."""
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "labeltpl3296admin", "admin_password": "AdminPass1!"},
    )
    assert (await async_client.get("/api/v1/label-templates")).status_code == 401
    assert (await async_client.post("/api/v1/label-templates", json=_template())).status_code == 401
    # The auth gate runs before the not-found check, so a synthetic id is enough.
    assert (await async_client.put("/api/v1/label-templates/99999", json=_template())).status_code == 401
    assert (await async_client.delete("/api/v1/label-templates/99999")).status_code == 401


class TestPermissions:
    """Operators can manage saved templates; Viewers can only list them."""

    @pytest.fixture
    async def tokens(self, db_session):
        from sqlalchemy import select

        from backend.app.core.auth import create_access_token, get_password_hash
        from backend.app.models.group import Group
        from backend.app.models.settings import Settings
        from backend.app.models.user import User

        db_session.add(Settings(key="auth_enabled", value="true"))
        await db_session.commit()

        tokens = {}
        for group_name in ("Operators", "Viewers"):
            group = (await db_session.execute(select(Group).where(Group.name == group_name))).scalar_one()
            user = User(
                username=f"labeltpl_{group_name.lower()}",
                password_hash=get_password_hash("password"),
                is_active=True,
            )
            user.groups.append(group)
            db_session.add(user)
            await db_session.commit()
            tokens[group_name] = {"Authorization": f"Bearer {create_access_token(data={'sub': user.username})}"}
        return tokens

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_can_manage_templates(self, async_client: AsyncClient, tokens):
        headers = tokens["Operators"]
        created = await async_client.post("/api/v1/label-templates", json=_template(), headers=headers)
        assert created.status_code == 201, created.text
        template_id = created.json()["id"]
        put = await async_client.put(f"/api/v1/label-templates/{template_id}", json=_template("B"), headers=headers)
        assert put.status_code == 200
        delete = await async_client.delete(f"/api/v1/label-templates/{template_id}", headers=headers)
        assert delete.status_code == 204

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_viewer_can_list_but_not_change_templates(self, async_client: AsyncClient, tokens):
        created = await async_client.post("/api/v1/label-templates", json=_template(), headers=tokens["Operators"])
        template_id = created.json()["id"]

        viewer = tokens["Viewers"]
        listed = await async_client.get("/api/v1/label-templates", headers=viewer)
        assert listed.status_code == 200
        assert [t["id"] for t in listed.json()] == [template_id]

        assert (
            await async_client.post("/api/v1/label-templates", json=_template("C"), headers=viewer)
        ).status_code == 403
        put = await async_client.put(f"/api/v1/label-templates/{template_id}", json=_template("C"), headers=viewer)
        assert put.status_code == 403
        assert (await async_client.delete(f"/api/v1/label-templates/{template_id}", headers=viewer)).status_code == 403
