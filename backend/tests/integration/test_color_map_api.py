"""Integration tests for GET /api/v1/inventory/colors/map — the lean color-name
lookup endpoint the frontend uses to resolve hex → name synchronously (see #857).

Regression guards for the behaviors the fix relies on:
 - Not gated on INVENTORY_READ (anyone authenticated can call it, otherwise the
   login page and read-only views would fail to render color names).
 - Keys are normalized to lowercase 6-char hex without the '#' prefix.
 - When multiple catalog rows share a hex, Bambu Lab wins over generic brands so
   the display name matches what users see in the slicer.
 - Default-seeded rows outrank user-added non-default rows on the same hex.
 - A17-R1 / F5B6CD resolves to "Cherry Pink" when catalog is seeded, the exact
   scenario that triggered #857 on @lightmaster's install.
"""

import pytest
from httpx import AsyncClient

from backend.app.models.color_catalog import ColorCatalogEntry


async def _seed(db_session, entries):
    for kwargs in entries:
        db_session.add(ColorCatalogEntry(**kwargs))
    await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_color_map_empty_catalog(async_client: AsyncClient):
    """Returns an empty mapping when the catalog has no rows."""
    response = await async_client.get("/api/v1/inventory/colors/map")
    assert response.status_code == 200
    body = response.json()
    assert body == {"colors": {}}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_color_map_returns_lowercase_hex_without_hash(async_client: AsyncClient, db_session):
    """Catalog rows can store hex with or without '#' and in any case; the map
    endpoint always emits lowercase 6-char hex without the '#' prefix so the
    frontend can do direct dict lookups."""
    await _seed(
        db_session,
        [
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Cherry Pink",
                "hex_color": "#F5B6CD",
                "material": "PLA Translucent",
                "is_default": True,
            },
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Scarlet Red",
                "hex_color": "#DE4343",
                "material": "PLA Matte",
                "is_default": True,
            },
        ],
    )
    response = await async_client.get("/api/v1/inventory/colors/map")
    assert response.status_code == 200
    colors = response.json()["colors"]
    assert "f5b6cd" in colors
    assert "de4343" in colors
    assert colors["f5b6cd"] == "Cherry Pink"
    assert colors["de4343"] == "Scarlet Red"
    # No uppercase, no '#' keys
    assert "F5B6CD" not in colors
    assert "#f5b6cd" not in colors


@pytest.mark.asyncio
@pytest.mark.integration
async def test_color_map_bambu_wins_over_generic_on_same_hex(async_client: AsyncClient, db_session):
    """When a generic brand happens to share a hex with Bambu Lab, Bambu wins —
    the canonical Bambu name is what the user expects to see on the AMS popup."""
    await _seed(
        db_session,
        [
            {
                "manufacturer": "Generic",
                "color_name": "Pinkish",
                "hex_color": "#F5B6CD",
                "material": "PLA",
                "is_default": False,
            },
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Cherry Pink",
                "hex_color": "#F5B6CD",
                "material": "PLA Translucent",
                "is_default": True,
            },
        ],
    )
    response = await async_client.get("/api/v1/inventory/colors/map")
    assert response.status_code == 200
    assert response.json()["colors"]["f5b6cd"] == "Cherry Pink"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_color_map_default_wins_over_user_added(async_client: AsyncClient, db_session):
    """Within the same manufacturer, default-seeded rows outrank user-added rows
    — the defaults are trusted and a user's custom alias shouldn't shadow the
    canonical catalog entry."""
    await _seed(
        db_session,
        [
            {
                "manufacturer": "Bambu Lab",
                "color_name": "My Custom Name",
                "hex_color": "#F5B6CD",
                "material": "PLA",
                "is_default": False,
            },
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Cherry Pink",
                "hex_color": "#F5B6CD",
                "material": "PLA Translucent",
                "is_default": True,
            },
        ],
    )
    response = await async_client.get("/api/v1/inventory/colors/map")
    assert response.status_code == 200
    assert response.json()["colors"]["f5b6cd"] == "Cherry Pink"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_color_map_skips_invalid_entries(async_client: AsyncClient, db_session):
    """Rows with missing hex or name must be silently dropped rather than crashing
    the endpoint. Malformed data shouldn't take down every color name in the UI."""
    await _seed(
        db_session,
        [
            # Too short to normalize to 6-char hex
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Weird",
                "hex_color": "#FFF",
                "material": None,
                "is_default": False,
            },
            # Valid row that must still appear
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Cherry Pink",
                "hex_color": "#F5B6CD",
                "material": "PLA Translucent",
                "is_default": True,
            },
        ],
    )
    response = await async_client.get("/api/v1/inventory/colors/map")
    assert response.status_code == 200
    colors = response.json()["colors"]
    assert "f5b6cd" in colors
    assert colors["f5b6cd"] == "Cherry Pink"
    # 3-char hex was dropped
    assert "fff" not in colors
