"""Unit tests for the Spoolman-mode side of the typed code fields.

Covers the read mapping (`_map_spoolman_spool`'s typed code outputs, with
read-tolerance for the interim branch's single bambu_barcode extra) and
`SpoolmanClient.find_spool_by_barcode` — Spoolman has no native code fields,
so everything round-trips through the spool's extra dict as JSON-encoded
values under bambu_gtin_code / bambu_asin_code / bambu_sku_code /
bambu_other_code / bambu_bought_as_refill (see the writes in
`routes/spoolman_inventory.py`).
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool
from backend.app.services.spoolman import SpoolmanClient

MINIMAL_SPOOL = {
    "id": 1,
    "filament": {
        "material": "PLA",
        "name": "PLA Basic",
        "color_hex": "FF0000",
        "weight": 1000.0,
        "vendor": {"name": "Bambu Lab"},
    },
    "used_weight": 250.0,
    "archived": False,
    "registered": "2024-01-01T00:00:00Z",
}


class TestMapSpoolmanSpoolCodes:
    def test_no_code_extras_read_back_as_none(self):
        result = _map_spoolman_spool(MINIMAL_SPOOL)
        assert result["gtin_code"] is None
        assert result["asin_code"] is None
        assert result["sku_code"] is None
        assert result["other_code"] is None
        assert result["bought_as_refill"] is False
        assert result["linked_codes"] == []

    def test_typed_codes_read_from_extras(self):
        spool = {
            **MINIMAL_SPOOL,
            "extra": {
                "bambu_gtin_code": json.dumps("6938936716785"),
                "bambu_sku_code": json.dumps("17600"),
                "bambu_asin_code": json.dumps("B0CJLR62MF"),
                "bambu_other_code": json.dumps("MyShelf-a42"),
                "bambu_bought_as_refill": "true",
            },
        }
        result = _map_spoolman_spool(spool)
        assert result["gtin_code"] == "6938936716785"
        assert result["sku_code"] == "17600"
        assert result["asin_code"] == "B0CJLR62MF"
        assert result["other_code"] == "MyShelf-a42"
        assert result["bought_as_refill"] is True
        # linked_codes = the spool's own codes in lookup shape, sharing the
        # roll's purchase-form flag.
        by_code = {c["code"]: c for c in result["linked_codes"]}
        assert set(by_code) == {"6938936716785", "17600", "B0CJLR62MF", "MyShelf-a42"}
        assert by_code["6938936716785"]["kind"] == "gtin"
        assert all(c["is_refill"] for c in result["linked_codes"])

    def test_legacy_bambu_barcode_classifies_into_typed_slot(self):
        """The interim branch wrote a single bambu_barcode extra whose value
        could be either kind — a GTIN routes to gtin_code, an alphanumeric
        SKU to sku_code, an ASIN shape to asin_code."""
        gtin = {**MINIMAL_SPOOL, "extra": {"bambu_barcode": json.dumps("6938936716785")}}
        assert _map_spoolman_spool(gtin)["gtin_code"] == "6938936716785"

        sku = {**MINIMAL_SPOOL, "extra": {"bambu_barcode": json.dumps("ALZMNTABS01")}}
        result = _map_spoolman_spool(sku)
        assert result["sku_code"] == "ALZMNTABS01"
        assert result["gtin_code"] is None

        asin = {**MINIMAL_SPOOL, "extra": {"bambu_barcode": json.dumps("B0CJLR62MF")}}
        assert _map_spoolman_spool(asin)["asin_code"] == "B0CJLR62MF"

    def test_typed_extras_win_over_legacy_barcode(self):
        spool = {
            **MINIMAL_SPOOL,
            "extra": {
                "bambu_gtin_code": json.dumps("6938936716785"),
                "bambu_barcode": json.dumps("999999999993"),
            },
        }
        result = _map_spoolman_spool(spool)
        assert result["gtin_code"] == "6938936716785"

    def test_legacy_refill_flag_key_still_reads(self):
        spool = {**MINIMAL_SPOOL, "extra": {"bambu_barcode_is_refill": "true"}}
        assert _map_spoolman_spool(spool)["bought_as_refill"] is True

    def test_refill_flag_tolerates_raw_bool_and_garbage(self):
        spool = {**MINIMAL_SPOOL, "extra": {"bambu_bought_as_refill": True}}
        assert _map_spoolman_spool(spool)["bought_as_refill"] is True
        spool = {**MINIMAL_SPOOL, "extra": {"bambu_bought_as_refill": "True"}}
        assert _map_spoolman_spool(spool)["bought_as_refill"] is True
        spool = {**MINIMAL_SPOOL, "extra": {"bambu_bought_as_refill": "garbage"}}
        assert _map_spoolman_spool(spool)["bought_as_refill"] is False


class TestFindSpoolByBarcode:
    """SpoolmanClient.find_spool_by_barcode — 'the user's own inventory' for
    barcode resolution when Spoolman mode is active."""

    @pytest.fixture
    def client(self):
        return SpoolmanClient("http://localhost:7912")

    async def test_matches_gtin_extra_with_cached_spools(self, client):
        cached = [
            {"id": 1, "extra": {"bambu_gtin_code": json.dumps("6938936716785")}},
            {"id": 2, "extra": {"bambu_gtin_code": json.dumps("12345678905")}},
        ]
        with patch.object(client, "get_all_spools", AsyncMock()) as mock_get:
            result = await client.find_spool_by_barcode("6938936716785", cached_spools=cached)
        assert result["id"] == 1
        mock_get.assert_not_called()

    async def test_matches_any_typed_code_extra(self, client):
        cached = [
            {"id": 1, "extra": {"bambu_sku_code": json.dumps("17600")}},
            {"id": 2, "extra": {"bambu_asin_code": json.dumps("B0CJLR62MF")}},
            {"id": 3, "extra": {"bambu_other_code": json.dumps("MyShelf-a42")}},
        ]
        assert (await client.find_spool_by_barcode("17600", cached_spools=cached))["id"] == 1
        assert (await client.find_spool_by_barcode("B0CJLR62MF", cached_spools=cached))["id"] == 2
        assert (await client.find_spool_by_barcode("MyShelf-a42", cached_spools=cached))["id"] == 3

    async def test_matches_legacy_bambu_barcode_extra(self, client):
        """Spools written by the interim branch still resolve."""
        cached = [{"id": 1, "extra": {"bambu_barcode": json.dumps("6938936716785")}}]
        result = await client.find_spool_by_barcode("6938936716785", cached_spools=cached)
        assert result["id"] == 1

    async def test_legacy_linked_codes_no_longer_match(self, client):
        """Deliberate behavior change: the interim bambu_linked_codes bundles
        included other package sizes' codes (cross-size contamination), so
        they are no longer consulted — only the typed per-package extras."""
        cached = [
            {
                "id": 1,
                "extra": {
                    "bambu_barcode": json.dumps("6938936716785"),
                    "bambu_linked_codes": json.dumps([{"code": "ALZMNTABS01", "kind": "sku", "is_refill": False}]),
                },
            }
        ]
        assert await client.find_spool_by_barcode("ALZMNTABS01", cached_spools=cached) is None

    async def test_fetches_including_archived_when_no_cache(self, client):
        """A repeat scan must resolve even if the original spool was later
        archived — matching the local-inventory lookup's behavior."""
        mock_spools = [{"id": 1, "extra": {"bambu_gtin_code": json.dumps("6938936716785")}}]
        with patch.object(client, "get_all_spools", AsyncMock(return_value=mock_spools)) as mock_get:
            result = await client.find_spool_by_barcode("6938936716785")
        assert result["id"] == 1
        mock_get.assert_called_once_with(allow_archived=True)

    async def test_no_match_returns_none(self, client):
        cached = [{"id": 1, "extra": {"bambu_gtin_code": '"999"'}}]
        assert await client.find_spool_by_barcode("6938936716785", cached_spools=cached) is None

    async def test_bare_numeric_string_still_matches(self, client):
        """Our writers always json.dumps a string, but a hand-edited extra
        field holding an unquoted digit string json-decodes to an int — it
        must coerce back to a string and match, not silently never resolve."""
        cached = [{"id": 1, "extra": {"bambu_gtin_code": "6938936716785"}}]
        result = await client.find_spool_by_barcode("6938936716785", cached_spools=cached)
        assert result["id"] == 1

    async def test_ignores_spools_without_extra(self, client):
        cached = [{"id": 1, "extra": {}}, {"id": 2}, {"id": 3, "extra": None}]
        assert await client.find_spool_by_barcode("6938936716785", cached_spools=cached) is None

    async def test_most_recently_registered_wins(self, client):
        cached = [
            {
                "id": 1,
                "extra": {"bambu_gtin_code": json.dumps("6938936716785")},
                "registered": "2024-01-01T00:00:00+00:00",
            },
            {
                "id": 2,
                "extra": {"bambu_gtin_code": json.dumps("6938936716785")},
                "registered": "2024-06-01T00:00:00+00:00",
            },
        ]
        result = await client.find_spool_by_barcode("6938936716785", cached_spools=cached)
        assert result["id"] == 2

    async def test_bare_string_extra_still_matches(self, client):
        """Tolerate a value written without JSON encoding (manual edits via
        the Spoolman UI) — when the value isn't valid JSON the raw string is
        compared directly."""
        cached = [{"id": 1, "extra": {"bambu_sku_code": "ALZMNTABS01"}}]
        result = await client.find_spool_by_barcode("ALZMNTABS01", cached_spools=cached)
        assert result["id"] == 1
