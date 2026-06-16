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


# ---- /colors/by-material — disambiguated lookup (#1718) -------------------


async def _seed_black_collision(client: AsyncClient) -> None:
    """Seed the #000000 ambiguity the endpoint was built to resolve.

    PLA Matte → Charcoal, PLA Basic → Black, both at #000000 — same shape as
    Bambu's production catalog.
    """
    for entry in (
        {
            "manufacturer": "Bambu Lab",
            "color_name": "Charcoal",
            "hex_color": "#000000",
            "material": "PLA Matte",
        },
        {
            "manufacturer": "Bambu Lab",
            "color_name": "Black",
            "hex_color": "#000000",
            "material": "PLA Basic",
        },
    ):
        response = await client.post("/api/v1/inventory/colors", json=entry)
        assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_by_material_returns_material_specific_name(async_client: AsyncClient):
    """Same hex + different material → returns the correctly-paired name."""
    await _seed_black_collision(async_client)

    matte = await async_client.get(
        "/api/v1/inventory/colors/by-material", params={"hex": "#000000", "material": "PLA Matte"}
    )
    assert matte.status_code == 200, matte.text
    assert matte.json() == {"color_name": "Charcoal"}

    basic = await async_client.get(
        "/api/v1/inventory/colors/by-material", params={"hex": "#000000", "material": "PLA Basic"}
    )
    assert basic.status_code == 200, basic.text
    assert basic.json() == {"color_name": "Black"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_by_material_falls_back_to_first_when_material_unknown(async_client: AsyncClient):
    """Unknown / unsupplied material → priority-order fallback, same as
    ``/colors/map`` so existing flat-map callers don't regress."""
    await _seed_black_collision(async_client)

    # Unknown material → first Bambu Lab entry wins (matches /map's priority).
    unknown = await async_client.get(
        "/api/v1/inventory/colors/by-material", params={"hex": "#000000", "material": "PLA-Nope"}
    )
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()["color_name"] in {"Charcoal", "Black"}

    # No material at all → same fallback.
    nomat = await async_client.get("/api/v1/inventory/colors/by-material", params={"hex": "#000000"})
    assert nomat.status_code == 200, nomat.text
    assert nomat.json()["color_name"] in {"Charcoal", "Black"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_by_material_returns_null_when_hex_missing(async_client: AsyncClient):
    """Hex not present in the catalog → color_name=None (do NOT 404)."""
    response = await async_client.get(
        "/api/v1/inventory/colors/by-material",
        params={"hex": "#abcdef", "material": "PLA Matte"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"color_name": None}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_by_material_case_insensitive_on_both_inputs(async_client: AsyncClient):
    """Lookup must tolerate mixed-case hex (legacy imports stored ``#B39B84``-
    style upper-case) and material (frontend derives material from sub-brand
    names whose casing isn't pinned). The endpoint uses ``func.lower`` on
    ``hex_color`` and lower-cases ``material`` before equality, so both
    directions of the case mismatch must round-trip."""
    # Seed an upper-case stored hex to exercise the lower-cased comparison.
    seed = await async_client.post(
        "/api/v1/inventory/colors",
        json={
            "manufacturer": "Bambu Lab",
            "color_name": "Iridium Gold Metallic",
            "hex_color": "#B39B84",  # stored upper-case
            "material": "PLA Metal",
        },
    )
    assert seed.status_code == 200, seed.text

    # Query the upper-case hex with lower-case input — must still match.
    lower_query = await async_client.get(
        "/api/v1/inventory/colors/by-material",
        params={"hex": "#b39b84", "material": "PLA Metal"},
    )
    assert lower_query.status_code == 200
    assert lower_query.json() == {"color_name": "Iridium Gold Metallic"}

    # Material is matched case-insensitively too.
    await _seed_black_collision(async_client)
    mixed_mat = await async_client.get(
        "/api/v1/inventory/colors/by-material",
        params={"hex": "#000000", "material": "pla matte"},
    )
    assert mixed_mat.json() == {"color_name": "Charcoal"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_by_material_rejects_short_hex(async_client: AsyncClient):
    """Invalid hex (< 6 chars after stripping '#') → color_name=None, no crash."""
    response = await async_client.get(
        "/api/v1/inventory/colors/by-material",
        params={"hex": "#abc", "material": "PLA Matte"},
    )
    assert response.status_code == 200
    assert response.json() == {"color_name": None}
