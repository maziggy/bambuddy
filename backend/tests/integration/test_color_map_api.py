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
    assert body == {"colors": {}, "by_material": {}}


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


@pytest.mark.asyncio
@pytest.mark.integration
async def test_by_material_keeps_the_name_collapsing_loses(async_client: AsyncClient, db_session):
    """#2875: a hex is not one colour.

    #FFFFFF is Jade White in PLA Basic and Ivory White in PLA Matte. Both are
    Bambu Lab and both are seeded defaults, so the flat map's priority order
    cannot separate them and falls back to insertion order -- which is why an
    ivory spool showed as "Jade White" on the AMS slot popover. The material-
    qualified map carries the name the flat one has to drop.
    """
    await _seed(
        db_session,
        [
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Jade White",
                "hex_color": "#FFFFFF",
                "material": "PLA Basic",
                "is_default": True,
            },
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Ivory White",
                "hex_color": "#FFFFFF",
                "material": "PLA Matte",
                "is_default": True,
            },
        ],
    )
    body = (await async_client.get("/api/v1/inventory/colors/map")).json()

    assert body["colors"]["ffffff"] == "Jade White"
    assert body["by_material"]["pla matte|ffffff"] == "Ivory White"
    # The row the flat map already answers correctly is not repeated.
    assert "pla basic|ffffff" not in body["by_material"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_by_material_is_empty_when_nothing_is_ambiguous(async_client: AsyncClient, db_session):
    """The qualified map costs only what the ambiguity costs.

    It ships on every page load beside the full catalog, so an entry that says
    the same thing as the flat map is pure weight.
    """
    await _seed(
        db_session,
        [
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Scarlet Red",
                "hex_color": "#DE4343",
                "material": "PLA Matte",
                "is_default": True,
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
    body = (await async_client.get("/api/v1/inventory/colors/map")).json()

    assert len(body["colors"]) == 2
    assert body["by_material"] == {}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_by_material_keys_are_normalized(async_client: AsyncClient, db_session):
    """Same normalization as the flat map: lowercase, no '#'.

    The frontend builds the lookup key from the printer's own
    ``tray_sub_brands`` ("PLA Matte"), so both halves have to be case-folded
    or the slot that needs this most never matches.
    """
    await _seed(
        db_session,
        [
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Black",
                "hex_color": "#000000",
                "material": "PLA Basic",
                "is_default": True,
            },
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Charcoal",
                "hex_color": "000000",
                "material": "PLA Matte",
                "is_default": True,
            },
        ],
    )
    body = (await async_client.get("/api/v1/inventory/colors/map")).json()

    assert body["by_material"] == {"pla matte|000000": "Charcoal"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_by_material_respects_the_same_priority_as_the_flat_map(async_client: AsyncClient, db_session):
    """Two brands can share a hex *and* a material name. Bambu still wins.

    The PLA Basic row comes first so the flat map keeps Jade White -- otherwise
    the Matte answer would already be the flat one and the qualified entry
    would be dropped as a duplicate, which proves nothing about the tie-break.
    """
    await _seed(
        db_session,
        [
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Jade White",
                "hex_color": "#FFFFFF",
                "material": "PLA Basic",
                "is_default": True,
            },
            {
                "manufacturer": "Generic",
                "color_name": "Off White",
                "hex_color": "#FFFFFF",
                "material": "PLA Matte",
                "is_default": False,
            },
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Ivory White",
                "hex_color": "#FFFFFF",
                "material": "PLA Matte",
                "is_default": True,
            },
        ],
    )
    body = (await async_client.get("/api/v1/inventory/colors/map")).json()

    assert body["colors"]["ffffff"] == "Jade White"
    assert body["by_material"]["pla matte|ffffff"] == "Ivory White"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rows_without_a_material_stay_out_of_the_qualified_map(async_client: AsyncClient, db_session):
    """A row with no material cannot answer a material-qualified question."""
    await _seed(
        db_session,
        [
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Jade White",
                "hex_color": "#FFFFFF",
                "material": "PLA Basic",
                "is_default": True,
            },
            {
                "manufacturer": "Generic",
                "color_name": "Some White",
                "hex_color": "#FFFFFF",
                "material": None,
                "is_default": False,
            },
        ],
    )
    body = (await async_client.get("/api/v1/inventory/colors/map")).json()

    assert body["by_material"] == {}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_by_material_does_not_hand_one_brands_name_to_another(async_client: AsyncClient, db_session):
    """A qualified entry must recover a name, not substitute one.

    The shipped catalog has Prusament "Pristine White" under material "PLA" on
    the same #FFFFFF that Bambu's "Jade White" holds. A slot reporting plain
    "PLA" — any third-party spool — would otherwise stop saying Jade White and
    start saying Pristine White, trading one arbitrary answer for another for a
    case nobody asked about. Only same-manufacturer recoveries are emitted.
    """
    await _seed(
        db_session,
        [
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Jade White",
                "hex_color": "#FFFFFF",
                "material": "PLA Basic",
                "is_default": True,
            },
            {
                "manufacturer": "Prusament",
                "color_name": "Pristine White",
                "hex_color": "#FFFFFF",
                "material": "PLA",
                "is_default": True,
            },
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Ivory White",
                "hex_color": "#FFFFFF",
                "material": "PLA Matte",
                "is_default": True,
            },
        ],
    )
    body = (await async_client.get("/api/v1/inventory/colors/map")).json()

    assert body["colors"]["ffffff"] == "Jade White"
    # The Bambu variant is recovered; the other brand's name is not offered.
    assert body["by_material"] == {"pla matte|ffffff": "Ivory White"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_material_containing_the_separator_is_still_read_correctly(async_client: AsyncClient, db_session):
    """Material is free text — users edit the catalog — so it can contain '|'.

    The hex is everything after the LAST separator, never the first.
    """
    await _seed(
        db_session,
        [
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Jade White",
                "hex_color": "#FFFFFF",
                "material": "PLA Basic",
                "is_default": True,
            },
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Ivory White",
                "hex_color": "#FFFFFF",
                "material": "PLA|Matte",
                "is_default": True,
            },
        ],
    )
    body = (await async_client.get("/api/v1/inventory/colors/map")).json()

    assert body["by_material"] == {"pla|matte|ffffff": "Ivory White"}
