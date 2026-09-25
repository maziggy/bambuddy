"""Unit tests for the Open Filament Database (OFD) client.

Tests:
- canon() barcode canonicalization (leading-zero stripping)
- _build_index() joins brands/filaments/variants/sizes correctly, including
  the article_number (SKU)/spool_refill fields and variant-code grouping
- lookup()/lookup_article() against a hand-written disk cache in the shared
  wrapper shape ({"cache_version", "built_at", "payload": {...}})
- a couple of integration-style checks that the client is correctly wired
  onto the shared cache spine (fresh cache served without network, refresh
  failure falls back to stale) — the full cache/TTL/seed matrix is locked
  once in test_external_catalog_cache.py against the base class

The client's download path is stubbed by monkeypatching ``stream_download``
as imported into the ofd_client module.
"""

import json
import time

import pytest

from backend.app.services import ofd_client


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Isolate each test from the module-level client's in-memory cache and
    point its disk cache at the test's own tmp_path (resolve_data_dir reads
    DATA_DIR fresh per call)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ofd_client._client._payload = None
    ofd_client._client._loaded_at = 0.0
    yield
    ofd_client._client._payload = None
    ofd_client._client._loaded_at = 0.0


def _no_network(monkeypatch):
    """Fail loudly if the client attempts a download."""

    async def _boom(url, **kwargs):
        raise AssertionError("unexpected network download")

    monkeypatch.setattr("backend.app.services.ofd_client.stream_download", _boom)


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

    def test_non_string_gtin_and_article_skipped_not_crashed(self):
        """An upstream row shipping a numeric gtin/article (e.g. 6938936716785
        as a JSON number) must be skipped like a missing one — canon() would
        raise TypeError on a non-string and abort the whole refresh."""
        broken = {
            **SAMPLE_ALL_JSON,
            "sizes": [{"gtin": 6938936716785, "article_number": 12345, "variant_id": 100, "filament_weight": 1000}],
        }
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
    def _write_cache(self, tmp_path, gtin_index, article_index, variant_codes, built_at=None, version=None):
        cache_file = tmp_path / "ofd_cache.json"
        cache_file.write_text(
            json.dumps(
                {
                    "cache_version": ofd_client._OfdClient.cache_version if version is None else version,
                    "built_at": time.time() if built_at is None else built_at,
                    "payload": {
                        "gtin_index": gtin_index,
                        "article_index": article_index,
                        "variant_codes": variant_codes,
                    },
                }
            )
        )

    @pytest.mark.asyncio
    async def test_fresh_disk_cache_used_without_network_call(self, tmp_path, monkeypatch):
        """Integration wiring check: the client reads the shared wrapper shape
        off disk and serves it without touching the network."""
        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        self._write_cache(tmp_path, gtin_index, article_index, variant_codes)
        _no_network(monkeypatch)

        result = await ofd_client.get_gtin_index()

        assert ofd_client.canon("06938936716785") in result

    @pytest.mark.asyncio
    async def test_refresh_failure_falls_back_to_stale_disk_cache(self, tmp_path, monkeypatch):
        """Offline/upstream-down must not discard an otherwise-usable, if old,
        index — a stale hit beats reporting no match for every barcode."""
        stale_time = time.time() - ofd_client._client.ttl_seconds - 10
        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        self._write_cache(tmp_path, gtin_index, article_index, variant_codes, built_at=stale_time)

        async def _offline(url, **kwargs):
            raise RuntimeError("offline")

        monkeypatch.setattr("backend.app.services.ofd_client.stream_download", _offline)

        result = await ofd_client.get_gtin_index()

        assert ofd_client.canon("06938936716785") in result

    @pytest.mark.asyncio
    async def test_empty_refresh_result_with_no_cache_raises(self, tmp_path, monkeypatch):
        """A 200 that parses to zero gtin/article entries (e.g. upstream's
        dump shape changes) must not be treated as a successful, cacheable
        refresh — with nothing to fall back to, the caller must learn the
        refresh effectively failed."""

        async def _empty(url, **kwargs):
            return b"{}"

        monkeypatch.setattr("backend.app.services.ofd_client.stream_download", _empty)

        with pytest.raises(RuntimeError, match="zero entries"):
            await ofd_client.get_gtin_index()

        assert not (tmp_path / "ofd_cache.json").exists()

    @pytest.mark.asyncio
    async def test_lookup_returns_none_for_unknown_barcode(self, tmp_path, monkeypatch):
        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        self._write_cache(tmp_path, gtin_index, article_index, variant_codes)
        _no_network(monkeypatch)

        result = await ofd_client.lookup("0000000000000")
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_returns_fields_and_codes_for_known_barcode(self, tmp_path, monkeypatch):
        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        self._write_cache(tmp_path, gtin_index, article_index, variant_codes)
        _no_network(monkeypatch)

        result = await ofd_client.lookup("6938936716785")
        assert result is not None
        fields, codes = result
        assert fields["brand"] == "Sunlu"
        assert codes == [{"code": ofd_client.canon("6938936716785"), "kind": "gtin", "is_refill": False}]

    @pytest.mark.asyncio
    async def test_lookup_article_returns_fields_and_codes(self, tmp_path, monkeypatch):
        data = {
            **SAMPLE_ALL_JSON,
            "sizes": [
                {"gtin": "06938936716785", "article_number": "ALZMNTABS01", "variant_id": 100, "filament_weight": 1000}
            ],
        }
        gtin_index, article_index, variant_codes = ofd_client._build_index(data)
        self._write_cache(tmp_path, gtin_index, article_index, variant_codes)
        _no_network(monkeypatch)

        result = await ofd_client.lookup_article("alzmntabs01")
        assert result is not None
        fields, codes = result
        assert fields["brand"] == "Sunlu"
        assert any(c["kind"] == "gtin" for c in codes)
        assert any(c["kind"] == "sku" for c in codes)

    @pytest.mark.asyncio
    async def test_lookup_article_returns_none_for_unknown_code(self, tmp_path, monkeypatch):
        gtin_index, article_index, variant_codes = ofd_client._build_index(SAMPLE_ALL_JSON)
        self._write_cache(tmp_path, gtin_index, article_index, variant_codes)
        _no_network(monkeypatch)

        assert await ofd_client.lookup_article("NOPE") is None


class TestDownloadCap:
    def test_max_all_json_bytes_is_16_mib(self):
        """The real all.json dump is ~1.5 MB; 16 MiB is ~10x headroom —
        generous enough for organic growth without letting a malicious or
        broken upstream buffer an effectively unbounded body (a review of the
        original PR asked for proportionate, tight caps). The cap's
        enforcement (streaming abort) is locked in
        test_external_catalog_cache.py::TestStreamDownload."""
        assert ofd_client._MAX_ALL_JSON_BYTES == 16 * 1024 * 1024
