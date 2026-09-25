"""Integration tests for typed-code persistence on the Spoolman inventory proxy.

Spoolman has no native code fields, so create/update own the round-trip via
the spool's extra dict: bambu_gtin_code / bambu_asin_code / bambu_sku_code /
bambu_other_code plus bambu_bought_as_refill (all JSON-encoded, same pattern
as bambu_slicer_filament / bambu_color_name). The SpoolmanClient is mocked —
these tests pin what the routes write, not Spoolman itself; the external
OFD/SpoolmanDB-Community lookups are patched so nothing touches the network.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

SAMPLE_SPOOLMAN_SPOOL = {
    "id": 42,
    "filament": {
        "id": 7,
        "name": "PLA Basic",
        "material": "PLA",
        "color_hex": "FF0000",
        "weight": 1000,
        "vendor": {"id": 3, "name": "Bambu Lab"},
    },
    "remaining_weight": 750.0,
    "used_weight": 250.0,
    "location": "Printer1 - AMS A1",
    "comment": "test note",
    "first_used": "2024-01-01T00:00:00+00:00",
    "last_used": "2024-02-01T00:00:00+00:00",
    "registered": "2024-01-01T00:00:00+00:00",
    "archived": False,
    "price": None,
    "extra": {"tag": '"AABBCCDDEEFF0011AABBCCDDEEFF0011"'},
}

_EXTERNAL_TARGETS = (
    "backend.app.services.ofd_client.lookup",
    "backend.app.services.ofd_client.lookup_article",
    "backend.app.services.spoolmandb_community_client.lookup",
    "backend.app.services.spoolmandb_community_client.lookup_sku",
)


def _patch_external(ofd_result=None):
    return (
        patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=ofd_result)),
        patch("backend.app.services.ofd_client.lookup_article", new=AsyncMock(return_value=None)),
        patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=None)),
        patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock(return_value=None)),
    )


def _forbid_external():
    boom = AssertionError("external barcode lookup must not be called")
    return tuple(patch(target, new=AsyncMock(side_effect=boom)) for target in _EXTERNAL_TARGETS)


@pytest.fixture
async def spoolman_settings(db_session):
    """Create Spoolman settings in the database (enabled with URL)."""
    from backend.app.models.settings import Settings

    enabled_setting = Settings(key="spoolman_enabled", value="true")
    url_setting = Settings(key="spoolman_url", value="http://localhost:7912")
    db_session.add(enabled_setting)
    db_session.add(url_setting)
    await db_session.commit()
    return {"enabled": enabled_setting, "url": url_setting}


@pytest.fixture
def mock_spoolman_client():
    """Mock the Spoolman client with a sample spool."""
    mock_client = MagicMock()
    mock_client.base_url = "http://localhost:7912"
    mock_client.health_check = AsyncMock(return_value=True)
    mock_client.get_all_spools = AsyncMock(return_value=[SAMPLE_SPOOLMAN_SPOOL])
    mock_client.get_spool = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock_client.create_spool = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock_client.update_spool_full = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock_client.merge_spool_extra = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock_client.find_or_create_filament = AsyncMock(return_value=7)
    mock_client.find_or_create_vendor = AsyncMock(return_value=3)
    mock_client.patch_filament = AsyncMock(return_value={"id": 7})
    mock_client.is_filament_shared = AsyncMock(return_value=False)
    mock_client.ensure_extra_field = AsyncMock(return_value=True)
    mock_client.get_distinct_locations = AsyncMock(return_value=[])

    with (
        patch(
            "backend.app.api.routes.spoolman_inventory.get_spoolman_client",
            AsyncMock(return_value=mock_client),
        ),
        patch(
            "backend.app.api.routes.spoolman_inventory.init_spoolman_client",
            AsyncMock(return_value=mock_client),
        ),
    ):
        yield mock_client


def _merged_extra(mock_client) -> dict:
    mock_client.merge_spool_extra.assert_called_once()
    return mock_client.merge_spool_extra.call_args.args[1]


class TestCreateWritesTypedCodeExtras:
    async def test_scanned_gtin_routes_and_cross_fills_sku(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """A create with scanned_code routes it down the ladder into the typed
        extras and cross-fills the same-package SKU from the community DBs."""
        smdb_hit = (
            {"material": "PLA"},
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "17600", "kind": "sku", "is_refill": False},
            ],
        )
        payload = {
            "material": "PLA",
            "label_weight": 1000,
            "weight_used": 0,
            "scanned_code": "6938936716785",
            "bought_as_refill": True,
        }
        with (
            patch("backend.app.services.ofd_client.same_package_code", new=AsyncMock(return_value=(False, None))),
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=smdb_hit)),
            patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock(return_value=None)),
        ):
            response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)

        assert response.status_code == 200
        for field in ("bambu_gtin_code", "bambu_sku_code", "bambu_bought_as_refill"):
            mock_spoolman_client.ensure_extra_field.assert_any_call(field)
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_gtin_code"]) == "6938936716785"
        assert json.loads(extra_patch["bambu_sku_code"]) == "17600"
        assert json.loads(extra_patch["bambu_bought_as_refill"]) is True
        assert "bambu_linked_codes" not in extra_patch

    async def test_explicit_typed_fields_write_directly(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        payload = {
            "material": "PLA",
            "label_weight": 1000,
            "weight_used": 0,
            "gtin_code": "06938936716785",  # canonicalizes like local mode
            "sku_code": "17600",
        }
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)

        assert response.status_code == 200
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_gtin_code"]) == "6938936716785"
        assert json.loads(extra_patch["bambu_sku_code"]) == "17600"
        assert json.loads(extra_patch["bambu_bought_as_refill"]) is False

    async def test_unknown_scanned_code_lands_in_other_code(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        payload = {"material": "PLA", "label_weight": 1000, "weight_used": 0, "scanned_code": "MyShelf-a42"}
        with (
            patch("backend.app.services.ofd_client.same_package_code", new=AsyncMock(return_value=(False, None))),
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=None)),
            patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock(return_value=None)),
        ):
            response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)

        assert response.status_code == 200
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_other_code"]) == "MyShelf-a42"
        assert "bambu_gtin_code" not in extra_patch

    async def test_create_with_lookup_disabled_writes_structural_routing_only(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client, db_session
    ):
        """The barcode_lookup_enabled toggle gates Spoolman-mode writes too —
        saving must not download anything; a scanned GTIN still lands
        structurally in bambu_gtin_code with no cross-fill."""
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="barcode_lookup_enabled", value="false"))
        await db_session.commit()

        payload = {"material": "PLA", "label_weight": 1000, "weight_used": 0, "scanned_code": "6938936716785"}
        boom = AssertionError("external barcode lookup must not be called")
        with (
            patch("backend.app.services.ofd_client.same_package_code", new=AsyncMock(side_effect=boom)),
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(side_effect=boom)),
            patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock(side_effect=boom)),
        ):
            response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)

        assert response.status_code == 200
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_gtin_code"]) == "6938936716785"
        assert "bambu_sku_code" not in extra_patch


class TestUpdateWritesTypedCodeExtras:
    async def test_update_writes_only_set_fields(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        payload = {"sku_code": "17600"}
        p1, p2, p3, p4 = _forbid_external()
        with p1, p2, p3, p4:
            response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)

        assert response.status_code == 200
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_sku_code"]) == "17600"
        assert "bambu_gtin_code" not in extra_patch
        assert "bambu_bought_as_refill" not in extra_patch

    async def test_update_clears_with_empty_string(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        payload = {"gtin_code": ""}
        p1, p2, p3, p4 = _forbid_external()
        with p1, p2, p3, p4:
            response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)

        assert response.status_code == 200
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_gtin_code"]) == ""

    async def test_update_bought_as_refill_flag(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        payload = {"bought_as_refill": True}
        p1, p2, p3, p4 = _forbid_external()
        with p1, p2, p3, p4:
            response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)

        assert response.status_code == 200
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_bought_as_refill"]) is True

    async def test_omitting_code_fields_skips_code_extra_write(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        payload = {"note": "just a note"}
        p1, p2, p3, p4 = _forbid_external()
        with p1, p2, p3, p4:
            response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)

        assert response.status_code == 200
        for call in mock_spoolman_client.merge_spool_extra.call_args_list:
            merged = call.args[1]
            assert not any(k.startswith("bambu_") and "code" in k for k in merged)
