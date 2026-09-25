"""Unit tests for the SpoolmanDB-Community client.

Tests:
- canon() barcode canonicalization (shared algorithm from the cache spine)
- _subtype_from_template() literal {color_name} placeholder removal
- _parse_manufacturer_file() expands raw source files into variant dicts
  (eans + eans_refill + codes/SKU, multi-color hexes, temp ranges), plus the
  container-type guards (a string where a list belongs contributes nothing
  instead of iterating character by character)
- _build_index()/_all_codes_for() group every code sibling for a color
- _parse_tarball() member/total size caps, malformed-member and
  non-filaments-path skipping
- lookup()/lookup_sku()/get_filaments() against a hand-written disk cache in
  the shared wrapper shape; empty-refresh raises from
  download_and_build_payload — the full cache/TTL/seed matrix is locked once
  in test_external_catalog_cache.py against the base class
"""

import io
import json
import tarfile
import time

import pytest

from backend.app.services import spoolmandb_community_client as smdb


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Isolate each test from the module-level client's in-memory cache,
    point its disk cache at the test's own tmp_path (resolve_data_dir reads
    DATA_DIR fresh per call), and neutralize the baked-in seed — a real
    generated seed may exist at backend/seeds/, and it must never mask a
    test's "raises with nothing on disk" expectations."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    smdb._client._payload = None
    smdb._client._loaded_at = 0.0
    monkeypatch.setattr(smdb._client, "seed_path", lambda: tmp_path / "nonexistent_seed.json")
    yield
    smdb._client._payload = None
    smdb._client._loaded_at = 0.0


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

    def test_filaments_as_string_parses_to_zero_variants(self):
        """Container-type guard: a source file shipping ``"filaments"`` as a
        plain string must contribute nothing — iterating the string would
        yield characters, none of them dicts, but the guard makes the
        intent explicit and survives shape changes."""
        broken = {"manufacturer": "Broken Co", "filaments": "not-a-list"}
        assert smdb._parse_manufacturer_file("Broken Co", broken) == []

    def test_colors_and_weights_as_strings_do_not_crash(self):
        """Same guard one level down: string ``colors``/``weights`` on one
        filament must not abort parsing (or index one-character garbage)."""
        broken = {
            "manufacturer": "Broken Co",
            "filaments": [{"name": "PLA {color_name}", "material": "PLA", "weights": "1000", "colors": "Red"}],
        }
        assert smdb._parse_manufacturer_file("Broken Co", broken) == []


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

    def test_non_string_ean_is_skipped_not_raised(self):
        """Covers the review finding: canon() assumes a string and raises
        TypeError on anything else - eans/eans_refill weren't type-guarded the
        way the SKU (codes) list already is, so a single malformed upstream
        source file with a numeric EAN would abort the whole refresh instead
        of just skipping that one bad entry."""
        v = {"eans": [6975337031345, "6975337031346"], "eans_refill": [None], "codes": []}
        codes = smdb._all_codes_for(v)
        assert codes == [{"code": "6975337031346", "kind": "gtin", "is_refill": False}]

    def test_non_string_sku_is_skipped_not_raised(self):
        v = {"eans": [], "eans_refill": [], "codes": [12345, "  ", "REAL-SKU"]}
        codes = smdb._all_codes_for(v)
        assert codes == [{"code": "REAL-SKU", "kind": "sku", "is_refill": False}]

    def test_eans_as_string_produces_zero_codes_not_per_character_garbage(self):
        """Container-type guard: ``"eans": "6938936716785"`` (a string where a
        list belongs) would iterate character by character, and every single
        character passes the entry-level string check — indexing 13
        one-character garbage codes. The list guard must drop it whole."""
        v = {"eans": "6938936716785", "eans_refill": [], "codes": []}
        assert smdb._all_codes_for(v) == []

    def test_eans_refill_as_string_produces_zero_codes(self):
        v = {"eans": [], "eans_refill": "6975337035053", "codes": []}
        assert smdb._all_codes_for(v) == []

    def test_codes_as_string_produces_zero_codes(self):
        v = {"eans": [], "eans_refill": [], "codes": "ALZMNTABS01"}
        assert smdb._all_codes_for(v) == []

    def test_public_codes_for_variant_matches_internal(self):
        """codes_for_variant is the public accessor the SpoolBuddy catalog
        endpoints use on get_filaments() rows — it must stay a faithful
        passthrough to _all_codes_for."""
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        v = next(v for v in variants if v["color_name"] == "Ivory White")
        assert smdb.codes_for_variant(v) == smdb._all_codes_for(v)


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


def _manufacturer_json(name: str, ean: str) -> bytes:
    return json.dumps(
        {
            "manufacturer": name,
            "filaments": [{"name": "Test {color_name}", "material": "PLA", "colors": [{"name": "Red", "eans": [ean]}]}],
        }
    ).encode()


def _build_tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class TestParseTarball:
    """Covers the review finding: per-member reads and the decompressed total
    were both unbounded, so a malformed or huge upstream tarball could OOM
    the backend on the 24h auto-refresh. (The raw download byte cap lives in
    stream_download, locked in test_external_catalog_cache.py.)"""

    def test_parses_all_filaments_members(self):
        tarball = _build_tarball(
            {
                "SpoolmanDB-Community-main/filaments/a.json": _manufacturer_json("A Co", "1111111111111"),
                "SpoolmanDB-Community-main/filaments/b.json": _manufacturer_json("B Co", "2222222222222"),
            }
        )
        variants = smdb._parse_tarball(tarball)
        assert {v["manufacturer"] for v in variants} == {"A Co", "B Co"}

    def test_exact_duplicate_variants_deduped(self):
        """Upstream lists some colors twice (33 exact duplicates in the
        2026-08 snapshot) — identical variants surfaced as indistinguishable
        twin rows in catalog search."""
        content = json.dumps(
            {
                "manufacturer": "A Co",
                "filaments": [
                    {"name": "Dup", "material": "PLA", "colors": [{"name": "Red", "eans": ["1111111111111"]}]},
                    {"name": "Dup", "material": "PLA", "colors": [{"name": "Red", "eans": ["1111111111111"]}]},
                ],
            }
        ).encode()
        tarball = _build_tarball({"SpoolmanDB-Community-main/filaments/a.json": content})
        variants = smdb._parse_tarball(tarball)
        assert len(variants) == 1

    def test_non_filaments_paths_skipped(self):
        """Only filaments/*.json source files are catalog data — READMEs,
        compiled output, and files elsewhere in the repo must not be parsed."""
        tarball = _build_tarball(
            {
                "SpoolmanDB-Community-main/README.md": b"# readme",
                "SpoolmanDB-Community-main/other/x.json": _manufacturer_json("Elsewhere Co", "3333333333333"),
                "SpoolmanDB-Community-main/filaments/not-json.txt": b"nope",
                "SpoolmanDB-Community-main/filaments/a.json": _manufacturer_json("A Co", "1111111111111"),
            }
        )
        variants = smdb._parse_tarball(tarball)
        assert {v["manufacturer"] for v in variants} == {"A Co"}

    def test_malformed_member_skipped_others_still_parsed(self):
        """One broken upstream source file must not abort the whole refresh
        for every other manufacturer."""
        tarball = _build_tarball(
            {
                "SpoolmanDB-Community-main/filaments/broken.json": b"{not valid json",
                "SpoolmanDB-Community-main/filaments/a.json": _manufacturer_json("A Co", "1111111111111"),
            }
        )
        variants = smdb._parse_tarball(tarball)
        assert {v["manufacturer"] for v in variants} == {"A Co"}

    def test_oversized_member_is_skipped_others_still_parsed(self, monkeypatch):
        small_file = _manufacturer_json("Small Co", "1111111111111")
        huge_file = _manufacturer_json("Huge Co", "2222222222222") + b" " * 1000
        monkeypatch.setattr(smdb, "_MAX_MEMBER_BYTES", len(small_file) + 10)
        assert len(huge_file) > smdb._MAX_MEMBER_BYTES

        tarball = _build_tarball(
            {
                "SpoolmanDB-Community-main/filaments/small.json": small_file,
                "SpoolmanDB-Community-main/filaments/huge.json": huge_file,
            }
        )
        variants = smdb._parse_tarball(tarball)
        assert {v["manufacturer"] for v in variants} == {"Small Co"}

    def test_total_decompressed_size_over_cap_raises(self, monkeypatch):
        """Covers the review finding: the per-member cap alone doesn't bound
        the sum across many members - many small files, each individually
        under _MAX_MEMBER_BYTES, must still be rejected once their combined
        decompressed size crosses _MAX_TOTAL_DECOMPRESSED_BYTES (a residual
        decompression-bomb angle)."""
        files = {
            f"SpoolmanDB-Community-main/filaments/co{i}.json": _manufacturer_json(f"Co {i}", f"{1111111111110 + i}")
            for i in range(5)
        }
        per_member_total = sum(len(content) for content in files.values())
        # Each file individually passes the per-member cap, but their sum
        # exceeds a total cap set just below that sum.
        monkeypatch.setattr(smdb, "_MAX_TOTAL_DECOMPRESSED_BYTES", per_member_total - 1)
        tarball = _build_tarball(files)

        with pytest.raises(ValueError, match="decompressed contents exceeded"):
            smdb._parse_tarball(tarball)


class TestCachingAndLookup:
    def _write_cache(self, tmp_path, gtin_index, sku_index, variants, built_at=None, version=None):
        cache_file = tmp_path / "spoolmandb_community_cache.json"
        cache_file.write_text(
            json.dumps(
                {
                    "cache_version": smdb._SpoolmanDbCommunityClient.cache_version if version is None else version,
                    "built_at": time.time() if built_at is None else built_at,
                    "payload": {"gtin_index": gtin_index, "sku_index": sku_index, "variants": variants},
                }
            )
        )

    def _no_network(self, monkeypatch):
        async def _boom(url, **kwargs):
            raise AssertionError("unexpected network download")

        monkeypatch.setattr("backend.app.services.spoolmandb_community_client.stream_download", _boom)

    @pytest.mark.asyncio
    async def test_lookup_returns_none_for_unknown_barcode(self, tmp_path, monkeypatch):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        self._write_cache(tmp_path, gtin_index, sku_index, variants)
        self._no_network(monkeypatch)

        result = await smdb.lookup("0000000000000")
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_returns_fields_and_codes_for_known_barcode(self, tmp_path, monkeypatch):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        self._write_cache(tmp_path, gtin_index, sku_index, variants)
        self._no_network(monkeypatch)

        result = await smdb.lookup("6975337031345")
        assert result is not None
        fields, codes = result
        assert fields["brand"] == "Bambu Lab"
        assert fields["color_name"] == "Ivory White"
        assert any(c["kind"] == "sku" for c in codes)

    @pytest.mark.asyncio
    async def test_lookup_sku_returns_fields_and_codes(self, tmp_path, monkeypatch):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        self._write_cache(tmp_path, gtin_index, sku_index, variants)
        self._no_network(monkeypatch)

        result = await smdb.lookup_sku("alzmntabs01")
        assert result is not None
        fields, codes = result
        assert fields["color_name"] == "Ivory White"
        assert any(c["kind"] == "gtin" for c in codes)

    @pytest.mark.asyncio
    async def test_lookup_sku_returns_none_for_unknown_code(self, tmp_path, monkeypatch):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        self._write_cache(tmp_path, gtin_index, sku_index, variants)
        self._no_network(monkeypatch)

        assert await smdb.lookup_sku("NOPE") is None

    @pytest.mark.asyncio
    async def test_get_filaments_returns_cached_variants(self, tmp_path, monkeypatch):
        variants = smdb._parse_manufacturer_file("Bambu Lab", SAMPLE_MANUFACTURER_FILE)
        gtin_index, sku_index = smdb._build_index(variants)
        self._write_cache(tmp_path, gtin_index, sku_index, variants)
        self._no_network(monkeypatch)

        result = await smdb.get_filaments()
        assert len(result) == 4

    @pytest.mark.asyncio
    async def test_empty_refresh_raises_instead_of_building_empty_payload(self, monkeypatch):
        """A tarball with zero filaments/*.json members (e.g. the repo layout
        changes and the path filter matches nothing) must not be treated as a
        successful, cacheable refresh — download_and_build_payload raises so
        the shared spine's stale-cache/seed fallback takes over instead of
        caching an empty index for a full TTL."""
        tarball = _build_tarball({"SpoolmanDB-Community-main/README.md": b"# no filaments here"})

        async def _serve_tarball(url, **kwargs):
            return tarball

        monkeypatch.setattr("backend.app.services.spoolmandb_community_client.stream_download", _serve_tarball)

        with pytest.raises(RuntimeError, match="zero manufacturer files"):
            await smdb.download_and_build_payload()
