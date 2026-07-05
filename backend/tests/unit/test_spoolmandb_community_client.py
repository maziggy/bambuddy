"""Unit tests for the SpoolmanDB-Community client.

Tests:
- canon() barcode canonicalization (shared algorithm, duplicated from ofd_client)
- _subtype_from_template() literal {color_name} placeholder removal
- _parse_manufacturer_file() / _build_index() expand raw source files into a
  barcode index correctly (eans + eans_refill, multi-color hexes, temp ranges)
- get_index()/lookup() disk-cache TTL behavior (fresh cache used, stale triggers refresh)
"""

import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services import spoolmandb_community_client as smdb


class TestCanon:
    def test_strips_leading_zeros(self):
        assert smdb.canon("0012345678905") == "12345678905"

    def test_strips_non_digits(self):
        assert smdb.canon("012-345-678-905") == "12345678905"

    def test_upc_a_and_ean_13_forms_match(self):
        assert smdb.canon("012345678905") == smdb.canon("0012345678905")

    def test_all_zeros_returns_zero(self):
        assert smdb.canon("0000") == "0"

    def test_empty_string(self):
        assert smdb.canon("") == "0"


class TestSubtypeFromTemplate:
    def test_placeholder_at_end(self):
        assert smdb._subtype_from_template("PLA Basic {color_name}") == "PLA Basic"

    def test_placeholder_at_start(self):
        assert smdb._subtype_from_template("{color_name} PLA Basic") == "PLA Basic"

    def test_placeholder_in_middle(self):
        assert smdb._subtype_from_template("Matte {color_name} PLA") == "Matte PLA"

    def test_no_placeholder_present(self):
        assert smdb._subtype_from_template("PLA Basic") == "PLA Basic"

    def test_empty_string_returns_none(self):
        assert smdb._subtype_from_template("") is None

    def test_placeholder_only_returns_none(self):
        assert smdb._subtype_from_template("{color_name}") is None


SAMPLE_MANUFACTURER_FILE = {
    "manufacturer": "Bambu Lab",
    "filaments": [
        {
            "name": "Matte {color_name} PLA",
            "material": "PLA",
            "density": 1.24,
            "weights": [{"weight": 1000, "spool_weight": 250, "spool_type": "plastic"}],
            "diameters": [1.75],
            "extruder_temp_range": [220, 240],
            "colors": [
                {
                    "name": "Ivory White",
                    "hex": "FFFFFF",
                    "eans": ["6975337031345"],
                },
                {
                    "name": "Desert Tan",
                    "hex": "C19A6B",
                    "eans_refill": ["6975337035053"],
                },
                {
                    "name": "No Barcode Blue",
                    "hex": "0000FF",
                },
            ],
        },
        {
            "name": "{color_name} Dual PLA",
            "material": "PLA",
            "density": 1.24,
            "weights": [{"weight": 1000}],
            "diameters": [1.75],
            "colors": [
                {
                    "name": "Black/White",
                    "hexes": ["000000", "FFFFFF"],
                    "multi_color_direction": "coaxial",
                    "eans": ["1234567890128"],
                }
            ],
        },
    ],
}


class TestParseManufacturerFile:
    def test_expands_one_variant_per_color(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        assert len(variants) == 4

    def test_maps_fields_for_eans_color(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        v = next(v for v in variants if v["color_name"] == "Ivory White")
        assert v["manufacturer"] == "Bambu Lab"
        assert v["brand"] == "Bambu Lab"
        assert v["material"] == "PLA"
        assert v["subtype"] == "Matte PLA"
        assert v["rgba"] == "FFFFFFFF"
        assert v["label_weight"] == 1000
        assert v["nozzle_temp_min"] == 220
        assert v["nozzle_temp_max"] == 240
        assert v["eans"] == ["6975337031345"]

    def test_eans_refill_present(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        v = next(v for v in variants if v["color_name"] == "Desert Tan")
        assert v["eans_refill"] == ["6975337035053"]
        assert v["eans"] == []

    def test_color_without_barcode_still_expanded(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        v = next(v for v in variants if v["color_name"] == "No Barcode Blue")
        assert v["eans"] == []
        assert v["eans_refill"] == []
        assert v["rgba"] == "0000FFFF"

    def test_multi_color_hexes_and_direction(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        v = next(v for v in variants if v["color_name"] == "Black/White")
        assert v["hexes"] == ["000000", "FFFFFF"]
        assert v["multi_color_direction"] == "coaxial"
        assert v["rgba"] == "000000FF"  # first hex used for rgba
        assert v["nozzle_temp_min"] is None  # no extruder_temp/_range on this filament


class TestBuildIndex:
    def test_indexes_eans_and_eans_refill(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        index = smdb._build_index(variants)
        assert smdb.canon("6975337031345") in index
        assert smdb.canon("6975337035053") in index
        assert smdb.canon("1234567890128") in index
        # A color without any barcode contributes no index entries.
        assert len(index) == 3

    def test_indexed_fields_match_barcode_field_keys(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        index = smdb._build_index(variants)
        fields = index[smdb.canon("6975337031345")]
        assert set(fields.keys()) == set(smdb._BARCODE_FIELD_KEYS)
        assert fields["brand"] == "Bambu Lab"
        assert fields["color_name"] == "Ivory White"


class TestCachingAndLookup:
    @pytest.fixture(autouse=True)
    def _reset_module_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(smdb, "_index", None)
        monkeypatch.setattr(smdb, "_brands", None)
        monkeypatch.setattr(smdb, "_variants", None)
        monkeypatch.setattr(smdb, "_index_loaded_at", 0.0)
        monkeypatch.setattr(smdb, "_cache_path", lambda: tmp_path / "spoolmandb_community_cache.json")
        yield

    @pytest.mark.asyncio
    async def test_fresh_disk_cache_used_without_network_call(self, tmp_path):
        cache_file = tmp_path / "spoolmandb_community_cache.json"
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        index = smdb._build_index(variants)
        cache_file.write_text(
            json.dumps({"built_at": time.time(), "index": index, "brands": ["Bambu Lab"], "variants": variants})
        )

        with patch("backend.app.services.spoolmandb_community_client._refresh", new=AsyncMock()) as mock_refresh:
            result = await smdb.get_index()
            mock_refresh.assert_not_called()
        assert smdb.canon("6975337031345") in result

    @pytest.mark.asyncio
    async def test_stale_disk_cache_triggers_refresh(self, tmp_path):
        cache_file = tmp_path / "spoolmandb_community_cache.json"
        stale_time = time.time() - smdb.SPOOLMANDB_COMMUNITY_TTL_SECONDS - 10
        cache_file.write_text(json.dumps({"built_at": stale_time, "index": {}, "brands": [], "variants": []}))

        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        fresh_index = smdb._build_index(variants)
        with patch(
            "backend.app.services.spoolmandb_community_client._refresh",
            new=AsyncMock(return_value=(fresh_index, ["Bambu Lab"], variants)),
        ) as mock_refresh:
            result = await smdb.get_index()
            mock_refresh.assert_awaited_once()
        assert smdb.canon("6975337031345") in result

    @pytest.mark.asyncio
    async def test_missing_variants_key_treated_as_stale(self, tmp_path):
        """Older cache files predate the `variants` key — must rebuild, not crash."""
        cache_file = tmp_path / "spoolmandb_community_cache.json"
        cache_file.write_text(json.dumps({"built_at": time.time(), "index": {}, "brands": []}))

        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        fresh_index = smdb._build_index(variants)
        with patch(
            "backend.app.services.spoolmandb_community_client._refresh",
            new=AsyncMock(return_value=(fresh_index, ["Bambu Lab"], variants)),
        ) as mock_refresh:
            result = await smdb.get_index()
            mock_refresh.assert_awaited_once()
        assert smdb.canon("6975337031345") in result

    @pytest.mark.asyncio
    async def test_lookup_returns_none_for_unknown_barcode(self, tmp_path):
        cache_file = tmp_path / "spoolmandb_community_cache.json"
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        index = smdb._build_index(variants)
        cache_file.write_text(
            json.dumps({"built_at": time.time(), "index": index, "brands": ["Bambu Lab"], "variants": variants})
        )

        result = await smdb.lookup("0000000000000")
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_returns_fields_for_known_barcode(self, tmp_path):
        cache_file = tmp_path / "spoolmandb_community_cache.json"
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        index = smdb._build_index(variants)
        cache_file.write_text(
            json.dumps({"built_at": time.time(), "index": index, "brands": ["Bambu Lab"], "variants": variants})
        )

        result = await smdb.lookup("6975337031345")
        assert result is not None
        assert result["brand"] == "Bambu Lab"
        assert result["color_name"] == "Ivory White"

    @pytest.mark.asyncio
    async def test_get_filaments_returns_cached_variants(self, tmp_path):
        cache_file = tmp_path / "spoolmandb_community_cache.json"
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        index = smdb._build_index(variants)
        cache_file.write_text(
            json.dumps({"built_at": time.time(), "index": index, "brands": ["Bambu Lab"], "variants": variants})
        )

        result = await smdb.get_filaments()
        assert len(result) == 4

    @pytest.mark.asyncio
    async def test_refresh_database_forces_network_refresh(self, tmp_path):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        fresh_index = smdb._build_index(variants)
        with patch(
            "backend.app.services.spoolmandb_community_client._refresh",
            new=AsyncMock(return_value=(fresh_index, ["Bambu Lab"], variants)),
        ) as mock_refresh:
            count = await smdb.refresh_database()
            mock_refresh.assert_awaited_once()
        assert count == 3
