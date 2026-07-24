"""Unit tests for the Open Filament Database (OFD) client.

Tests:
- canon() barcode canonicalization (leading-zero stripping)
- _build_index() joins brands/filaments/variants/sizes correctly, including
  the article_number (SKU)/spool_refill fields and variant-code grouping
- get_gtin_index()/get_article_index()/lookup()/lookup_article() disk-cache
  TTL behavior (fresh cache used, stale triggers refresh, old cache-version
  shape triggers refresh)
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

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
        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        canonical = ofd_client.canon("06938936716785")
        assert canonical in gtin_index
        fields = gtin_index[canonical]["fields"]
        assert fields["material"] == "PLA"
        assert fields["brand"] == "Sunlu"
        assert fields["color_name"] == "Black"
        assert fields["rgba"] == "000000FF"
        assert fields["label_weight"] == 1000
        assert fields["nozzle_temp_min"] == 190
        assert fields["nozzle_temp_max"] == 230
        assert article_index == {}
        assert variant_codes["100"] == [{"code": canonical, "kind": "gtin", "is_refill": False}]

    def test_missing_gtin_and_article_skipped(self):
        broken = {**SAMPLE_ALL_JSON, "sizes": [{"variant_id": 100, "filament_weight": 1000}]}
        gtin_index, article_index, variant_codes = ofd_client._build_index(broken)
        assert gtin_index == {}
        assert article_index == {}
        assert variant_codes == {}

    def test_orphaned_variant_skipped(self):
        broken = {**SAMPLE_ALL_JSON, "variants": []}
        gtin_index, article_index, variant_codes = ofd_client._build_index(broken)
        assert gtin_index == {}

    def test_upc_a_and_ean_13_gtin_produce_same_key(self):
        variant_a = {
            **SAMPLE_ALL_JSON,
            "sizes": [{"gtin": "6938936716785", "variant_id": 100, "filament_weight": 1000}],
        }
        variant_b = {
            **SAMPLE_ALL_JSON,
            "sizes": [{"gtin": "06938936716785", "variant_id": 100, "filament_weight": 1000}],
        }
        gtin_index_a, _, _ = ofd_client._build_index(variant_a)
        gtin_index_b, _, _ = ofd_client._build_index(variant_b)
        assert list(gtin_index_a.keys()) == list(gtin_index_b.keys())

    def test_article_number_indexed_and_normalized(self):
        data = {
            **SAMPLE_ALL_JSON,
            "sizes": [{"article_number": " alzmntabs01 ", "variant_id": 100, "filament_weight": 1000}],
        }
        gtin_index, article_index, variant_codes = ofd_client._build_index(data)
        assert gtin_index == {}
        assert "ALZMNTABS01" in article_index
        assert article_index["ALZMNTABS01"]["fields"]["material"] == "PLA"
        assert variant_codes["100"] == [{"code": "ALZMNTABS01", "kind": "sku", "is_refill": False}]

    def test_gtin_and_article_on_same_size_are_both_siblings(self):
        data = {
            **SAMPLE_ALL_JSON,
            "sizes": [
                {
                    "gtin": "06938936716785",
                    "article_number": "ALZMNTABS01",
                    "variant_id": 100,
                    "filament_weight": 1000,
                }
            ],
        }
        gtin_index, article_index, variant_codes = ofd_client._build_index(data)
        canonical = ofd_client.canon("06938936716785")
        assert canonical in gtin_index
        assert "ALZMNTABS01" in article_index
        codes = variant_codes["100"]
        assert {"code": canonical, "kind": "gtin", "is_refill": False} in codes
        assert {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False} in codes

    def test_multiple_sizes_share_variant_codes_and_keep_per_size_fields(self):
        """Different package sizes of the same colour share a variant_id — the
        code list must include every sibling, but each code's own `fields`
        (e.g. label_weight) stays specific to the size it came from."""
        data = {
            **SAMPLE_ALL_JSON,
            "sizes": [
                {"gtin": "06938936716785", "variant_id": 100, "filament_weight": 1000},
                {
                    "gtin": "06938936716786",
                    "article_number": "ALZMNTABS01",
                    "variant_id": 100,
                    "filament_weight": 250,
                    "spool_refill": True,
                },
            ],
        }
        gtin_index, article_index, variant_codes = ofd_client._build_index(data)
        big = ofd_client.canon("06938936716785")
        small = ofd_client.canon("06938936716786")
        assert gtin_index[big]["fields"]["label_weight"] == 1000
        assert gtin_index[small]["fields"]["label_weight"] == 250
        codes = variant_codes["100"]
        assert {"code": big, "kind": "gtin", "is_refill": False} in codes
        assert {"code": small, "kind": "gtin", "is_refill": True} in codes
        assert {"code": "ALZMNTABS01", "kind": "sku", "is_refill": True} in codes


class TestCachingAndLookup:
    @pytest.fixture(autouse=True)
    def _reset_module_cache(self, tmp_path, monkeypatch):
        """Isolate each test from the module-level in-memory cache and disk path."""
        monkeypatch.setattr(ofd_client, "_gtin_index", None)
        monkeypatch.setattr(ofd_client, "_article_index", None)
        monkeypatch.setattr(ofd_client, "_variant_codes", None)
        monkeypatch.setattr(ofd_client, "_brands", None)
        monkeypatch.setattr(ofd_client, "_index_loaded_at", 0.0)
        monkeypatch.setattr(ofd_client, "_cache_path", lambda: tmp_path / "ofd_cache.json")
        yield

    def _write_cache(self, tmp_path, gtin_index, article_index, variant_codes, brands, built_at=None, version=None):
        cache_file = tmp_path / "ofd_cache.json"
        cache_file.write_text(
            json.dumps(
                {
                    "cache_version": ofd_client._CACHE_VERSION if version is None else version,
                    "built_at": time.time() if built_at is None else built_at,
                    "gtin_index": gtin_index,
                    "article_index": article_index,
                    "variant_codes": variant_codes,
                    "brands": brands,
                }
            )
        )

    @pytest.mark.asyncio
    async def test_fresh_disk_cache_used_without_network_call(self, tmp_path):
        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        self._write_cache(tmp_path, gtin_index, article_index, variant_codes, ["Sunlu"])

        with patch("backend.app.services.ofd_client._refresh", new=AsyncMock()) as mock_refresh:
            result = await ofd_client.get_gtin_index()
            mock_refresh.assert_not_called()
        assert ofd_client.canon("06938936716785") in result

    @pytest.mark.asyncio
    async def test_stale_disk_cache_triggers_refresh(self, tmp_path):
        stale_time = time.time() - ofd_client.OFD_TTL_SECONDS - 10
        self._write_cache(tmp_path, {}, {}, {}, [], built_at=stale_time)

        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        with patch(
            "backend.app.services.ofd_client._refresh",
            new=AsyncMock(return_value=(gtin_index, article_index, variant_codes, ["Sunlu"])),
        ) as mock_refresh:
            result = await ofd_client.get_gtin_index()
            mock_refresh.assert_awaited_once()
        assert ofd_client.canon("06938936716785") in result

    @pytest.mark.asyncio
    async def test_old_cache_version_triggers_refresh(self, tmp_path):
        """A cache file predating article_number/variant-code support must not be misread."""
        self._write_cache(tmp_path, {}, {}, {}, [], version=1)

        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        with patch(
            "backend.app.services.ofd_client._refresh",
            new=AsyncMock(return_value=(gtin_index, article_index, variant_codes, ["Sunlu"])),
        ) as mock_refresh:
            result = await ofd_client.get_gtin_index()
            mock_refresh.assert_awaited_once()
        assert ofd_client.canon("06938936716785") in result

    @pytest.mark.asyncio
    async def test_refresh_failure_falls_back_to_stale_disk_cache(self, tmp_path):
        """Offline/upstream-down must not discard an otherwise-usable, if old,
        index — a stale hit beats reporting no match for every barcode."""
        stale_time = time.time() - ofd_client.OFD_TTL_SECONDS - 10
        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        self._write_cache(tmp_path, gtin_index, article_index, variant_codes, ["Sunlu"], built_at=stale_time)

        with patch(
            "backend.app.services.ofd_client._refresh",
            new=AsyncMock(side_effect=RuntimeError("offline")),
        ):
            result = await ofd_client.get_gtin_index()
        assert ofd_client.canon("06938936716785") in result

    @pytest.mark.asyncio
    async def test_refresh_failure_with_no_cache_at_all_raises(self, tmp_path):
        """No stale fallback exists (first-ever startup, no network) — the
        caller must still learn the lookup couldn't be attempted."""
        with (
            patch(
                "backend.app.services.ofd_client._refresh",
                new=AsyncMock(side_effect=RuntimeError("offline")),
            ),
            pytest.raises(RuntimeError),
        ):
            await ofd_client.get_gtin_index()

    @pytest.mark.asyncio
    async def test_refresh_writes_cache_atomically(self, tmp_path):
        """Cache writes go through a temp file + rename, never a partial file
        at the real path — even if a write is interrupted mid-way."""
        cache_path = tmp_path / "ofd_cache.json"
        all_json_response = MagicMock()
        all_json_response.raise_for_status = MagicMock()
        all_json_response.json = MagicMock(return_value=SAMPLE_ALL_JSON)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=all_json_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.app.services.ofd_client.httpx.AsyncClient", return_value=mock_client):
            await ofd_client._refresh()

        assert cache_path.exists()
        assert not cache_path.with_suffix(".json.tmp").exists()
        data = json.loads(cache_path.read_text())
        assert data["cache_version"] == ofd_client._CACHE_VERSION

    @pytest.mark.asyncio
    async def test_empty_refresh_result_with_no_cache_raises(self, tmp_path):
        """A 200 that parses to zero gtin/article entries (e.g. upstream's
        dump shape changes) must not be treated as a successful, cacheable
        refresh — with nothing to fall back to, the caller must learn the
        refresh effectively failed."""
        empty_response = MagicMock()
        empty_response.raise_for_status = MagicMock()
        empty_response.json = MagicMock(return_value={})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=empty_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.app.services.ofd_client.httpx.AsyncClient", return_value=mock_client),
            pytest.raises(RuntimeError, match="zero entries"),
        ):
            await ofd_client._refresh()

        assert not (tmp_path / "ofd_cache.json").exists()

    @pytest.mark.asyncio
    async def test_empty_refresh_result_falls_back_to_stale_cache_untouched(self, tmp_path):
        """An empty refresh must not clobber a good stale cache - the stale
        entries keep serving lookups instead of "no match" for a full TTL."""
        stale_time = time.time() - ofd_client.OFD_TTL_SECONDS - 10
        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        self._write_cache(tmp_path, gtin_index, article_index, variant_codes, ["Sunlu"], built_at=stale_time)
        cache_path = tmp_path / "ofd_cache.json"
        before = cache_path.read_text()

        empty_response = MagicMock()
        empty_response.raise_for_status = MagicMock()
        empty_response.json = MagicMock(return_value={})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=empty_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.app.services.ofd_client.httpx.AsyncClient", return_value=mock_client):
            result = await ofd_client.get_gtin_index()

        assert ofd_client.canon("06938936716785") in result
        assert cache_path.read_text() == before

    @pytest.mark.asyncio
    async def test_lookup_returns_none_for_unknown_barcode(self, tmp_path):
        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        self._write_cache(tmp_path, gtin_index, article_index, variant_codes, ["Sunlu"])

        result = await ofd_client.lookup("0000000000000")
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_returns_fields_and_codes_for_known_barcode(self, tmp_path):
        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        self._write_cache(tmp_path, gtin_index, article_index, variant_codes, ["Sunlu"])

        result = await ofd_client.lookup("6938936716785")
        assert result is not None
        fields, codes = result
        assert fields["brand"] == "Sunlu"
        assert codes == [{"code": ofd_client.canon("6938936716785"), "kind": "gtin", "is_refill": False}]

    @pytest.mark.asyncio
    async def test_lookup_article_returns_fields_and_codes(self, tmp_path):
        data = {
            **SAMPLE_ALL_JSON,
            "sizes": [
                {"gtin": "06938936716785", "article_number": "ALZMNTABS01", "variant_id": 100, "filament_weight": 1000}
            ],
        }
        gtin_index, article_index, variant_codes = ofd_client._build_index(data)
        self._write_cache(tmp_path, gtin_index, article_index, variant_codes, ["Sunlu"])

        result = await ofd_client.lookup_article("alzmntabs01")
        assert result is not None
        fields, codes = result
        assert fields["brand"] == "Sunlu"
        assert any(c["kind"] == "gtin" for c in codes)
        assert any(c["kind"] == "sku" for c in codes)

    @pytest.mark.asyncio
    async def test_lookup_article_returns_none_for_unknown_code(self, tmp_path):
        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        self._write_cache(tmp_path, gtin_index, article_index, variant_codes, ["Sunlu"])

        assert await ofd_client.lookup_article("NOPE") is None

    @pytest.mark.asyncio
    async def test_refresh_database_forces_network_refresh_and_sums_both_indexes(self, tmp_path):
        data = {
            **SAMPLE_ALL_JSON,
            "sizes": [
                {"gtin": "06938936716785", "variant_id": 100, "filament_weight": 1000},
                {"article_number": "ALZMNTABS01", "variant_id": 100, "filament_weight": 250},
            ],
        }
        gtin_index, article_index, variant_codes = ofd_client._build_index(data)
        with patch(
            "backend.app.services.ofd_client._refresh",
            new=AsyncMock(return_value=(gtin_index, article_index, variant_codes, ["Sunlu"])),
        ) as mock_refresh:
            count = await ofd_client.refresh_database()
            mock_refresh.assert_awaited_once()
        assert count == 2  # 1 gtin + 1 article number
