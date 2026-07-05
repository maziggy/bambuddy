"""Integration tests for POST /api/v1/inventory/colors/sync-spoolmandb-community.

Unlike the FilamentColors.xyz sync (a paginated live API, streamed via SSE),
SpoolmanDB-Community is fetched as one bounded download, so this endpoint
returns a plain JSON summary instead.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.api.routes.inventory import _derive_effect_type
from backend.app.models.color_catalog import ColorCatalogEntry


class TestDeriveEffectType:
    """Priority: structural multi-color split > glow > pattern > translucent > finish."""

    def test_no_signals_returns_none(self):
        assert _derive_effect_type({}) is None

    def test_coaxial_two_hexes_is_dual_color(self):
        v = {"multi_color_direction": "coaxial", "hexes": ["000000", "FFFFFF"]}
        assert _derive_effect_type(v) == "dual-color"

    def test_coaxial_three_hexes_is_tri_color(self):
        v = {"multi_color_direction": "coaxial", "hexes": ["000000", "FFFFFF", "FF0000"]}
        assert _derive_effect_type(v) == "tri-color"

    def test_coaxial_four_plus_hexes_is_multicolor(self):
        v = {"multi_color_direction": "coaxial", "hexes": ["000000", "FFFFFF", "FF0000", "00FF00"]}
        assert _derive_effect_type(v) == "multicolor"

    def test_longitudinal_direction_is_gradient(self):
        v = {"multi_color_direction": "longitudinal", "hexes": ["000000", "FFFFFF"]}
        assert _derive_effect_type(v) == "gradient"

    def test_multi_color_direction_without_enough_hexes_falls_through(self):
        """A direction flag with <2 hexes isn't a real multi-color split — falls through."""
        v = {"multi_color_direction": "coaxial", "hexes": ["000000"], "glow": True}
        assert _derive_effect_type(v) == "glow"

    def test_glow_wins_over_pattern(self):
        v = {"glow": True, "pattern": "sparkle"}
        assert _derive_effect_type(v) == "glow"

    def test_sparkle_pattern(self):
        assert _derive_effect_type({"pattern": "sparkle"}) == "sparkle"

    def test_marble_pattern(self):
        assert _derive_effect_type({"pattern": "marble"}) == "marble"

    def test_pattern_wins_over_translucent(self):
        v = {"pattern": "marble", "translucent": True}
        assert _derive_effect_type(v) == "marble"

    def test_translucent(self):
        assert _derive_effect_type({"translucent": True}) == "translucent"

    def test_translucent_wins_over_matte_finish(self):
        v = {"translucent": True, "finish": "matte"}
        assert _derive_effect_type(v) == "translucent"

    def test_matte_finish(self):
        assert _derive_effect_type({"finish": "matte"}) == "matte"

    def test_glossy_finish_has_no_mapping(self):
        assert _derive_effect_type({"finish": "glossy"}) is None


SAMPLE_VARIANTS = [
    {
        "manufacturer": "Bambu Lab",
        "material": "PLA",
        "brand": "Bambu Lab",
        "subtype": "Matte",
        "color_name": "Ivory White",
        "rgba": "FFFFFFFF",
        "hexes": None,
        "label_weight": 1000,
        "nozzle_temp_min": 220,
        "nozzle_temp_max": 240,
        "finish": "matte",
        "pattern": None,
        "translucent": None,
        "glow": None,
        "multi_color_direction": None,
        "eans": ["6975337031345"],
        "eans_refill": [],
    },
    {
        "manufacturer": "Bambu Lab",
        "material": "PLA",
        "brand": "Bambu Lab",
        "subtype": "Dual",
        "color_name": "Black/White",
        "rgba": "000000FF",
        "hexes": ["000000", "FFFFFF"],
        "label_weight": 1000,
        "nozzle_temp_min": None,
        "nozzle_temp_max": None,
        "finish": None,
        "pattern": None,
        "translucent": None,
        "glow": None,
        "multi_color_direction": "coaxial",
        "eans": [],
        "eans_refill": [],
    },
    {
        # Missing color_name — must be skipped, not crash.
        "manufacturer": "NoName Brand",
        "material": "PETG",
        "brand": "NoName Brand",
        "subtype": None,
        "color_name": None,
        "rgba": "FF0000FF",
        "hexes": None,
        "label_weight": 1000,
        "nozzle_temp_min": None,
        "nozzle_temp_max": None,
        "finish": None,
        "pattern": None,
        "translucent": None,
        "glow": None,
        "multi_color_direction": None,
        "eans": [],
        "eans_refill": [],
    },
]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_adds_new_colors(async_client: AsyncClient, db_session):
    with patch(
        "backend.app.services.spoolmandb_community_client.get_filaments",
        new=AsyncMock(return_value=SAMPLE_VARIANTS),
    ):
        response = await async_client.post("/api/v1/inventory/colors/sync-spoolmandb-community")

    assert response.status_code == 200
    body = response.json()
    assert body["added"] == 2  # the third variant (no color_name) is skipped
    assert body["skipped"] == 1
    assert body["total"] == 3

    result = await db_session.execute(select(ColorCatalogEntry).where(ColorCatalogEntry.manufacturer == "Bambu Lab"))
    rows = {row.color_name: row for row in result.scalars().all()}
    assert "Ivory White" in rows
    assert rows["Ivory White"].hex_color == "#FFFFFF"
    assert rows["Ivory White"].effect_type == "matte"
    assert "Black/White" in rows
    assert rows["Black/White"].effect_type == "dual-color"
    assert rows["Black/White"].extra_colors == "ffffff"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_is_idempotent_on_rerun(async_client: AsyncClient, db_session):
    with patch(
        "backend.app.services.spoolmandb_community_client.get_filaments",
        new=AsyncMock(return_value=SAMPLE_VARIANTS),
    ):
        first = await async_client.post("/api/v1/inventory/colors/sync-spoolmandb-community")
        second = await async_client.post("/api/v1/inventory/colors/sync-spoolmandb-community")

    assert first.json()["added"] == 2
    assert second.json()["added"] == 0
    assert second.json()["skipped"] == 3


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_dedupes_repeated_variants_in_process(async_client: AsyncClient, db_session):
    """Two variants with the same (manufacturer, color_name, material) — e.g. two
    weight/diameter source rows for the same color — only produce one catalog row."""
    duplicate_variants = [SAMPLE_VARIANTS[0], {**SAMPLE_VARIANTS[0]}]
    with patch(
        "backend.app.services.spoolmandb_community_client.get_filaments",
        new=AsyncMock(return_value=duplicate_variants),
    ):
        response = await async_client.post("/api/v1/inventory/colors/sync-spoolmandb-community")

    assert response.status_code == 200
    body = response.json()
    assert body["added"] == 1
    assert body["skipped"] == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_returns_502_on_fetch_failure(async_client: AsyncClient):
    with patch(
        "backend.app.services.spoolmandb_community_client.get_filaments",
        new=AsyncMock(side_effect=RuntimeError("network down")),
    ):
        response = await async_client.post("/api/v1/inventory/colors/sync-spoolmandb-community")

    assert response.status_code == 502
