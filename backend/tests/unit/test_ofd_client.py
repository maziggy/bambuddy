"""Unit tests for the Open Filament Database (OFD) client.

Tests:
- canon() barcode canonicalization (leading-zero stripping)
- _build_index() joins brands/filaments/variants/sizes correctly
- get_index()/lookup() disk-cache TTL behavior (fresh cache used, stale triggers refresh)
"""

import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services import ofd_client


class TestCanon:
    def test_strips_leading_zeros(self):
        assert ofd_client.canon("0012345678905") == "12345678905"

    def test_strips_non_digits(self):
        assert ofd_client.canon("012-345-678-905") == "12345678905"

    def test_upc_a_and_ean_13_forms_match(self):
        upc_a = "012345678905"
        ean_13 = "0012345678905"
        assert ofd_client.canon(upc_a) == ofd_client.canon(ean_13)

    def test_all_zeros_returns_zero(self):
        assert ofd_client.canon("0000") == "0"

    def test_empty_string(self):
        assert ofd_client.canon("") == "0"


SAMPLE_ALL_JSON = {
    "brands": [{"id": 1, "name": "Sunlu"}],
    "filaments": [
        {
            "id": 10,
            "brand_id": 1,
            "name": "PLA+",
            "material": "PLA",
            "min_print_temperature": 190,
            "max_print_temperature": 230,
        }
    ],
    "variants": [{"id": 100, "filament_id": 10, "name": "Black", "color_hex": "#000000"}],
    "sizes": [{"gtin": "06938936716785", "variant_id": 100, "filament_weight": 1000}],
}


class TestBuildIndex:
    def test_joins_brand_filament_variant_size(self):
        index = ofd_client._build_index(SAMPLE_ALL_JSON)
        canonical = ofd_client.canon("06938936716785")
        assert canonical in index
        fields = index[canonical]
        assert fields["material"] == "PLA"
        assert fields["brand"] == "Sunlu"
        assert fields["color_name"] == "Black"
        assert fields["rgba"] == "000000FF"
        assert fields["label_weight"] == 1000
        assert fields["nozzle_temp_min"] == 190
        assert fields["nozzle_temp_max"] == 230

    def test_missing_gtin_skipped(self):
        broken = {**SAMPLE_ALL_JSON, "sizes": [{"variant_id": 100, "filament_weight": 1000}]}
        assert ofd_client._build_index(broken) == {}

    def test_orphaned_variant_skipped(self):
        broken = {**SAMPLE_ALL_JSON, "variants": []}
        assert ofd_client._build_index(broken) == {}

    def test_upc_a_and_ean_13_gtin_produce_same_key(self):
        variant_a = {
            **SAMPLE_ALL_JSON,
            "sizes": [{"gtin": "6938936716785", "variant_id": 100, "filament_weight": 1000}],
        }
        variant_b = {
            **SAMPLE_ALL_JSON,
            "sizes": [{"gtin": "06938936716785", "variant_id": 100, "filament_weight": 1000}],
        }
        assert list(ofd_client._build_index(variant_a).keys()) == list(ofd_client._build_index(variant_b).keys())


class TestCachingAndLookup:
    @pytest.fixture(autouse=True)
    def _reset_module_cache(self, tmp_path, monkeypatch):
        """Isolate each test from the module-level in-memory cache and disk path."""
        monkeypatch.setattr(ofd_client, "_index", None)
        monkeypatch.setattr(ofd_client, "_brands", None)
        monkeypatch.setattr(ofd_client, "_index_loaded_at", 0.0)
        monkeypatch.setattr(ofd_client, "_cache_path", lambda: tmp_path / "ofd_cache.json")
        yield

    @pytest.mark.asyncio
    async def test_fresh_disk_cache_used_without_network_call(self, tmp_path):
        cache_file = tmp_path / "ofd_cache.json"
        index = ofd_client._build_index(SAMPLE_ALL_JSON)
        cache_file.write_text(json.dumps({"built_at": time.time(), "index": index, "brands": ["Sunlu"]}))

        with patch("backend.app.services.ofd_client._refresh", new=AsyncMock()) as mock_refresh:
            result = await ofd_client.get_index()
            mock_refresh.assert_not_called()
        assert ofd_client.canon("06938936716785") in result

    @pytest.mark.asyncio
    async def test_stale_disk_cache_triggers_refresh(self, tmp_path):
        cache_file = tmp_path / "ofd_cache.json"
        stale_time = time.time() - ofd_client.OFD_TTL_SECONDS - 10
        cache_file.write_text(json.dumps({"built_at": stale_time, "index": {}, "brands": []}))

        fresh_index = ofd_client._build_index(SAMPLE_ALL_JSON)
        with patch(
            "backend.app.services.ofd_client._refresh",
            new=AsyncMock(return_value=(fresh_index, ["Sunlu"])),
        ) as mock_refresh:
            result = await ofd_client.get_index()
            mock_refresh.assert_awaited_once()
        assert ofd_client.canon("06938936716785") in result

    @pytest.mark.asyncio
    async def test_lookup_returns_none_for_unknown_barcode(self, tmp_path):
        cache_file = tmp_path / "ofd_cache.json"
        index = ofd_client._build_index(SAMPLE_ALL_JSON)
        cache_file.write_text(json.dumps({"built_at": time.time(), "index": index, "brands": ["Sunlu"]}))

        result = await ofd_client.lookup("0000000000000")
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_returns_fields_for_known_barcode(self, tmp_path):
        cache_file = tmp_path / "ofd_cache.json"
        index = ofd_client._build_index(SAMPLE_ALL_JSON)
        cache_file.write_text(json.dumps({"built_at": time.time(), "index": index, "brands": ["Sunlu"]}))

        result = await ofd_client.lookup("6938936716785")
        assert result is not None
        assert result["brand"] == "Sunlu"

    @pytest.mark.asyncio
    async def test_refresh_database_forces_network_refresh(self, tmp_path):
        fresh_index = ofd_client._build_index(SAMPLE_ALL_JSON)
        with patch(
            "backend.app.services.ofd_client._refresh",
            new=AsyncMock(return_value=(fresh_index, ["Sunlu"])),
        ) as mock_refresh:
            count = await ofd_client.refresh_database()
            mock_refresh.assert_awaited_once()
        assert count == 1
