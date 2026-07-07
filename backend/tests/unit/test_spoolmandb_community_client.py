"""Unit tests for the SpoolmanDB-Community client.

Tests:
- canon() barcode canonicalization (shared algorithm, duplicated from ofd_client)
- _subtype_from_template() literal {color_name} placeholder removal
- _parse_manufacturer_file() expands raw source files into variant dicts
  (eans + eans_refill + codes/SKU, multi-color hexes, temp ranges)
- _build_index()/_all_codes_for() group every code sibling for a color
- get_gtin_index()/get_sku_index()/lookup()/lookup_sku() disk-cache TTL
  behavior (fresh cache used, stale triggers refresh, old cache-version
  shape triggers refresh)
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
                    "codes": ["ALZMNTABS01"],
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
        assert v["codes"] == ["ALZMNTABS01"]

    def test_eans_refill_present(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        v = next(v for v in variants if v["color_name"] == "Desert Tan")
        assert v["eans_refill"] == ["6975337035053"]
        assert v["eans"] == []
        assert v["codes"] == []

    def test_color_without_barcode_still_expanded(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        v = next(v for v in variants if v["color_name"] == "No Barcode Blue")
        assert v["eans"] == []
        assert v["eans_refill"] == []
        assert v["codes"] == []
        assert v["rgba"] == "0000FFFF"

    def test_multi_color_hexes_and_direction(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        v = next(v for v in variants if v["color_name"] == "Black/White")
        assert v["hexes"] == ["000000", "FFFFFF"]
        assert v["multi_color_direction"] == "coaxial"
        assert v["rgba"] == "000000FF"  # first hex used for rgba
        assert v["nozzle_temp_min"] is None  # no extruder_temp/_range on this filament


class TestAllCodesFor:
    def test_combines_eans_eans_refill_and_codes(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        v = next(v for v in variants if v["color_name"] == "Ivory White")
        codes = smdb._all_codes_for(v)
        assert {"code": "6975337031345", "kind": "gtin", "is_refill": False} in codes
        assert {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False} in codes

    def test_eans_refill_flagged_is_refill(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        v = next(v for v in variants if v["color_name"] == "Desert Tan")
        codes = smdb._all_codes_for(v)
        assert codes == [{"code": "6975337035053", "kind": "gtin", "is_refill": True}]

    def test_no_codes_returns_empty_list(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        v = next(v for v in variants if v["color_name"] == "No Barcode Blue")
        assert smdb._all_codes_for(v) == []


class TestBuildIndex:
    def test_indexes_eans_and_eans_refill_in_gtin_index(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        assert smdb.canon("6975337031345") in gtin_index
        assert smdb.canon("6975337035053") in gtin_index
        assert smdb.canon("1234567890128") in gtin_index
        # A color without any barcode/SKU contributes no index entries.
        assert len(gtin_index) == 3
        assert list(sku_index.keys()) == ["ALZMNTABS01"]

    def test_indexed_fields_match_barcode_field_keys(self):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, _ = smdb._build_index(variants)
        entry = gtin_index[smdb.canon("6975337031345")]
        assert set(entry["fields"].keys()) == set(smdb._BARCODE_FIELD_KEYS)
        assert entry["fields"]["brand"] == "Bambu Lab"
        assert entry["fields"]["color_name"] == "Ivory White"

    def test_gtin_and_sku_hit_share_all_codes(self):
        """A GTIN hit and its sibling SKU hit for the same color must return
        the same all_codes bundle (both codes present in each)."""
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        gtin_entry = gtin_index[smdb.canon("6975337031345")]
        sku_entry = sku_index["ALZMNTABS01"]
        assert gtin_entry["all_codes"] == sku_entry["all_codes"]
        codes = {c["code"] for c in gtin_entry["all_codes"]}
        assert codes == {smdb.canon("6975337031345"), "ALZMNTABS01"}


class TestCachingAndLookup:
    @pytest.fixture(autouse=True)
    def _reset_module_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(smdb, "_gtin_index", None)
        monkeypatch.setattr(smdb, "_sku_index", None)
        monkeypatch.setattr(smdb, "_brands", None)
        monkeypatch.setattr(smdb, "_variants", None)
        monkeypatch.setattr(smdb, "_index_loaded_at", 0.0)
        monkeypatch.setattr(smdb, "_cache_path", lambda: tmp_path / "spoolmandb_community_cache.json")
        yield

    def _write_cache(self, tmp_path, gtin_index, sku_index, brands, variants, built_at=None, version=None):
        cache_file = tmp_path / "spoolmandb_community_cache.json"
        cache_file.write_text(
            json.dumps(
                {
                    "cache_version": smdb._CACHE_VERSION if version is None else version,
                    "built_at": time.time() if built_at is None else built_at,
                    "gtin_index": gtin_index,
                    "sku_index": sku_index,
                    "brands": brands,
                    "variants": variants,
                }
            )
        )

    @pytest.mark.asyncio
    async def test_fresh_disk_cache_used_without_network_call(self, tmp_path):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        self._write_cache(tmp_path, gtin_index, sku_index, ["Bambu Lab"], variants)

        with patch("backend.app.services.spoolmandb_community_client._refresh", new=AsyncMock()) as mock_refresh:
            result = await smdb.get_gtin_index()
            mock_refresh.assert_not_called()
        assert smdb.canon("6975337031345") in result

    @pytest.mark.asyncio
    async def test_stale_disk_cache_triggers_refresh(self, tmp_path):
        stale_time = time.time() - smdb.SPOOLMANDB_COMMUNITY_TTL_SECONDS - 10
        self._write_cache(tmp_path, {}, {}, [], [], built_at=stale_time)

        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        with patch(
            "backend.app.services.spoolmandb_community_client._refresh",
            new=AsyncMock(return_value=(gtin_index, sku_index, ["Bambu Lab"], variants)),
        ) as mock_refresh:
            result = await smdb.get_gtin_index()
            mock_refresh.assert_awaited_once()
        assert smdb.canon("6975337031345") in result

    @pytest.mark.asyncio
    async def test_old_cache_version_triggers_refresh(self, tmp_path):
        """A cache file predating codes/SKU support must not be misread."""
        self._write_cache(tmp_path, {}, {}, [], [], version=1)

        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        with patch(
            "backend.app.services.spoolmandb_community_client._refresh",
            new=AsyncMock(return_value=(gtin_index, sku_index, ["Bambu Lab"], variants)),
        ) as mock_refresh:
            result = await smdb.get_gtin_index()
            mock_refresh.assert_awaited_once()
        assert smdb.canon("6975337031345") in result

    @pytest.mark.asyncio
    async def test_lookup_returns_none_for_unknown_barcode(self, tmp_path):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        self._write_cache(tmp_path, gtin_index, sku_index, ["Bambu Lab"], variants)

        result = await smdb.lookup("0000000000000")
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_returns_fields_and_codes_for_known_barcode(self, tmp_path):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        self._write_cache(tmp_path, gtin_index, sku_index, ["Bambu Lab"], variants)

        result = await smdb.lookup("6975337031345")
        assert result is not None
        fields, codes = result
        assert fields["brand"] == "Bambu Lab"
        assert fields["color_name"] == "Ivory White"
        assert any(c["kind"] == "sku" for c in codes)

    @pytest.mark.asyncio
    async def test_lookup_sku_returns_fields_and_codes(self, tmp_path):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        self._write_cache(tmp_path, gtin_index, sku_index, ["Bambu Lab"], variants)

        result = await smdb.lookup_sku("alzmntabs01")
        assert result is not None
        fields, codes = result
        assert fields["color_name"] == "Ivory White"
        assert any(c["kind"] == "gtin" for c in codes)

    @pytest.mark.asyncio
    async def test_lookup_sku_returns_none_for_unknown_code(self, tmp_path):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        self._write_cache(tmp_path, gtin_index, sku_index, ["Bambu Lab"], variants)

        assert await smdb.lookup_sku("NOPE") is None

    @pytest.mark.asyncio
    async def test_get_filaments_returns_cached_variants(self, tmp_path):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        self._write_cache(tmp_path, gtin_index, sku_index, ["Bambu Lab"], variants)

        result = await smdb.get_filaments()
        assert len(result) == 4

    @pytest.mark.asyncio
    async def test_refresh_database_forces_network_refresh_and_sums_both_indexes(self, tmp_path):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        with patch(
            "backend.app.services.spoolmandb_community_client._refresh",
            new=AsyncMock(return_value=(gtin_index, sku_index, ["Bambu Lab"], variants)),
        ) as mock_refresh:
            count = await smdb.refresh_database()
            mock_refresh.assert_awaited_once()
        assert count == 4  # 3 gtins + 1 sku
