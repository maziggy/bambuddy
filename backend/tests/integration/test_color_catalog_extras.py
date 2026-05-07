"""Integration tests for the multi-colour + effect extensions on the colour
catalog routes (#1154).

End-to-end coverage that the new fields on `ColorEntryCreate` / `ColorEntryUpdate`
round-trip through the database, that catalog GET surfaces them in the response,
and that paste-style values from 3dfilamentprofiles.com are normalized.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_color_entry_with_extras(async_client: AsyncClient):
    """POST /inventory/colors stores extra_colors + effect_type."""
    payload = {
        "manufacturer": "3dfilamentprofiles",
        "color_name": "Aurora Tetracolour",
        "hex_color": "#EC984C",
        "material": "PLA",
        "extra_colors": "EC984C,#6CD4BC,A66EB9,D87694",
        "effect_type": "Sparkle",
    }
    response = await async_client.post("/api/v1/inventory/colors", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    # Canonical form: lowercase, no `#`, comma-joined.
    assert body["extra_colors"] == "ec984c,6cd4bc,a66eb9,d87694"
    assert body["effect_type"] == "sparkle"
    assert body["hex_color"] == "#EC984C"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_color_entry_accepts_8char_hex(async_client: AsyncClient):
    """Catalog hex_color may include alpha (#RRGGBBAA) post-#1154."""
    payload = {
        "manufacturer": "Bambu Lab",
        "color_name": "Translucent Galaxy",
        "hex_color": "#1A2B3C80",
        "material": "PETG",
    }
    response = await async_client.post("/api/v1/inventory/colors", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["hex_color"] == "#1A2B3C80"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_color_entry_clears_extras(async_client: AsyncClient):
    """PUT with empty extra_colors clears the field (server normalizes "" → null)."""
    create = await async_client.post(
        "/api/v1/inventory/colors",
        json={
            "manufacturer": "Test",
            "color_name": "Fade",
            "hex_color": "#FF0000",
            "extra_colors": "FF0000,00FF00",
            "effect_type": "wood",
        },
    )
    assert create.status_code == 200
    entry_id = create.json()["id"]

    update = await async_client.put(
        f"/api/v1/inventory/colors/{entry_id}",
        json={
            "manufacturer": "Test",
            "color_name": "Fade",
            "hex_color": "#FF0000",
            "extra_colors": "",
            "effect_type": None,
        },
    )
    assert update.status_code == 200, update.text
    body = update.json()
    assert body["extra_colors"] is None
    assert body["effect_type"] is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_color_entry_rejects_bad_extra_colors(async_client: AsyncClient):
    response = await async_client.post(
        "/api/v1/inventory/colors",
        json={
            "manufacturer": "Test",
            "color_name": "Bad",
            "hex_color": "#FF0000",
            "extra_colors": "not-hex,GGHHII",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_color_entry_rejects_bad_effect_type(async_client: AsyncClient):
    response = await async_client.post(
        "/api/v1/inventory/colors",
        json={
            "manufacturer": "Test",
            "color_name": "Bad",
            "hex_color": "#FF0000",
            "effect_type": "not-a-real-variant",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_color_catalog_returns_extras(async_client: AsyncClient):
    """GET /inventory/colors response shape includes the new fields."""
    await async_client.post(
        "/api/v1/inventory/colors",
        json={
            "manufacturer": "Test",
            "color_name": "Glitter Black",
            "hex_color": "#101010",
            "extra_colors": "101010,303030",
            "effect_type": "sparkle",
        },
    )
    response = await async_client.get("/api/v1/inventory/colors")
    assert response.status_code == 200
    rows = response.json()
    glitter = next((r for r in rows if r["color_name"] == "Glitter Black"), None)
    assert glitter is not None
    assert glitter["extra_colors"] == "101010,303030"
    assert glitter["effect_type"] == "sparkle"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_spool_with_color_extras(async_client: AsyncClient):
    """POST /inventory/spools threads the new spool-side fields end-to-end."""
    payload = {
        "material": "PLA",
        "subtype": "Multicolor",
        "rgba": "EC984CFF",
        "extra_colors": "#EC984C,#6CD4BC,#A66EB9,#D87694",
        "effect_type": "matte",
    }
    response = await async_client.post("/api/v1/inventory/spools", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extra_colors"] == "ec984c,6cd4bc,a66eb9,d87694"
    assert body["effect_type"] == "matte"

    # PATCH clears via empty string + null.
    patch = await async_client.patch(
        f"/api/v1/inventory/spools/{body['id']}",
        json={"extra_colors": "", "effect_type": None},
    )
    assert patch.status_code == 200
    assert patch.json()["extra_colors"] is None
    assert patch.json()["effect_type"] is None
