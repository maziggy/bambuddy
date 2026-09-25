"""Unit tests for services/barcode_resolver.py — the shared resolution +
code-routing engine.

Covers, in order:
- the barcode_lookup_enabled gate,
- external_all_codes: routing by kind (ASINs file under the DBs' SKU fields),
  field merging/priority, sibling cross-probing, failure degradation, and the
  hard toggle-off guarantee (no external activity at all),
- resolve_barcode: own-inventory-first via the typed code columns (any
  column matches, newest roll donates the template), Spoolman-mode routing,
  external fallback,
- route_scanned_code: the classification ladder + size-consistent cross-fill
  (OFD same-size pairing, SpoolmanDB per-package variants, unknown codes
  landing verbatim in other_code),
- codes_for_spool: the typed columns rendered as lookup/display code dicts.

DB-touching tests run against a real in-memory SQLite engine — PR #1895's
review specifically flagged MagicMock-DB tests as exercising no SQL at all.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.app.models.spool import Spool
from backend.app.services.barcode_resolver import (
    barcode_lookup_enabled,
    codes_for_spool,
    external_all_codes,
    resolve_barcode,
    route_scanned_code,
)

ENABLED: dict[str, str] = {}  # missing key defaults to enabled
DISABLED = {"barcode_lookup_enabled": "false"}


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Spool.__table__.create)
    yield eng
    await eng.dispose()


async def _insert_spool(session: AsyncSession, spool_id: int, **overrides) -> None:
    fields = {
        "material": "PLA",
        "label_weight": 1000,
        "core_weight": 250,
        "weight_used": 0,
        "weight_used_baseline": 0,
        "weight_locked": False,
        "bought_as_refill": False,
    }
    fields.update(overrides)
    session.add(Spool(id=spool_id, **fields))
    await session.commit()


@contextmanager
def _forbid_external():
    """Patch every external client function to fail the test if called at all —
    the strong form of 'no external activity' used by the toggle-gating and
    own-inventory-priority tests."""
    boom = AssertionError("external barcode lookup must not be called")
    with ExitStack() as stack:
        for target in (
            "backend.app.services.ofd_client.lookup",
            "backend.app.services.ofd_client.lookup_article",
            "backend.app.services.ofd_client.same_package_code",
            "backend.app.services.spoolmandb_community_client.lookup",
            "backend.app.services.spoolmandb_community_client.lookup_sku",
        ):
            stack.enter_context(patch(target, new=AsyncMock(side_effect=boom)))
        yield


@contextmanager
def _patch_external(ofd=None, ofd_article=None, smdb=None, smdb_sku=None, ofd_paired=(False, None)):
    with ExitStack() as stack:
        mocks = {}
        for name, target, value in (
            ("ofd", "backend.app.services.ofd_client.lookup", ofd),
            ("ofd_article", "backend.app.services.ofd_client.lookup_article", ofd_article),
            ("ofd_paired", "backend.app.services.ofd_client.same_package_code", ofd_paired),
            ("smdb", "backend.app.services.spoolmandb_community_client.lookup", smdb),
            ("smdb_sku", "backend.app.services.spoolmandb_community_client.lookup_sku", smdb_sku),
        ):
            mock = AsyncMock(return_value=value)
            stack.enter_context(patch(target, new=mock))
            mocks[name] = mock
        yield mocks


class TestBarcodeLookupEnabled:
    def test_defaults_to_enabled(self):
        assert barcode_lookup_enabled({}) is True

    def test_false_disables(self):
        assert barcode_lookup_enabled(DISABLED) is False

    def test_explicit_true_enables(self):
        assert barcode_lookup_enabled({"barcode_lookup_enabled": "true"}) is True


class TestExternalAllCodes:
    async def test_disabled_setting_returns_none_without_any_lookup(self):
        """THE toggle gate (review-5's second bug): with barcode_lookup_enabled
        off, external_all_codes must return None before touching either client —
        on a first-ever offline instance the OFD/tarball timeouts would
        otherwise block spool saves for minutes."""
        with _forbid_external():
            assert await external_all_codes("6938936716785", "gtin", DISABLED) is None

    async def test_gtin_kind_routes_to_gtin_lookups(self):
        with _patch_external() as mocks:
            assert await external_all_codes("6938936716785", "gtin", ENABLED) is None
        mocks["ofd"].assert_awaited_once_with("6938936716785")
        mocks["smdb"].assert_awaited_once_with("6938936716785")
        mocks["ofd_article"].assert_not_called()
        mocks["smdb_sku"].assert_not_called()

    async def test_sku_kind_routes_to_sku_lookups(self):
        with _patch_external() as mocks:
            assert await external_all_codes("ALZMNTABS01", "sku", ENABLED) is None
        mocks["ofd_article"].assert_awaited_once_with("ALZMNTABS01")
        mocks["smdb_sku"].assert_awaited_once_with("ALZMNTABS01")
        mocks["ofd"].assert_not_called()
        mocks["smdb"].assert_not_called()

    async def test_asin_kind_routes_to_sku_lookups(self):
        """Both community DBs file ASINs under their SKU/article fields, so an
        asin-classified code must take the SKU lookup path."""
        with _patch_external() as mocks:
            assert await external_all_codes("B0CJLR62MF", "asin", ENABLED) is None
        mocks["ofd_article"].assert_awaited_once_with("B0CJLR62MF")
        mocks["smdb_sku"].assert_awaited_once_with("B0CJLR62MF")
        mocks["ofd"].assert_not_called()
        mocks["smdb"].assert_not_called()

    async def test_ofd_only_hit_returns_its_fields_and_codes(self):
        ofd_hit = (
            {"material": "PETG", "brand": "Overture"},
            [{"code": "12345678905", "kind": "gtin", "is_refill": False}],
        )
        with _patch_external(ofd=ofd_hit):
            result = await external_all_codes("12345678905", "gtin", ENABLED)

        assert result is not None
        fields, source, all_codes = result
        assert source == "ofd"
        assert fields["material"] == "PETG"
        assert [c["code"] for c in all_codes] == ["12345678905"]

    async def test_both_direct_hits_merge_fields_and_union_codes_without_probing(self):
        """When both databases resolve the code directly, OFD's values win where
        both are set, SpoolmanDB-Community fills OFD's gaps, all_codes dedupes
        on code, and no sibling probing happens."""
        ofd_hit = (
            {"material": "PLA", "brand": "Sunlu", "nozzle_temp_min": None},
            [{"code": "6938936716785", "kind": "gtin", "is_refill": False}],
        )
        smdb_hit = (
            {"material": "PLA+", "nozzle_temp_min": 190},
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
            ],
        )
        with _patch_external(ofd=ofd_hit, smdb=smdb_hit) as mocks:
            result = await external_all_codes("6938936716785", "gtin", ENABLED)

        fields, source, all_codes = result
        assert source == "ofd"
        assert fields["material"] == "PLA"  # OFD's value wins over SpoolmanDB's
        assert fields["nozzle_temp_min"] == 190  # gap filled from SpoolmanDB
        assert {c["code"] for c in all_codes} == {"6938936716785", "ALZMNTABS01"}
        mocks["ofd_article"].assert_not_called()
        mocks["smdb_sku"].assert_not_called()

    async def test_sibling_probe_fills_missing_fields_from_other_database(self):
        """A hit in one database probes its sibling codes against the *other*
        database to recover cross-referenced fields and codes (the enrichment
        that makes 'either database knows this product' good enough)."""
        ofd_hit = (
            {"material": "PLA", "brand": "Sunlu"},
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
            ],
        )
        smdb_probe = (
            {"nozzle_temp_min": 190, "nozzle_temp_max": 220},
            [
                {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
                {"code": "6938936716786", "kind": "gtin", "is_refill": True},
            ],
        )
        with _patch_external(ofd=ofd_hit, smdb_sku=smdb_probe) as mocks:
            result = await external_all_codes("6938936716785", "gtin", ENABLED)

        fields, source, all_codes = result
        assert source == "ofd"
        assert fields["nozzle_temp_min"] == 190
        assert fields["nozzle_temp_max"] == 220
        mocks["smdb_sku"].assert_awaited_once_with("ALZMNTABS01")
        assert {c["code"] for c in all_codes} == {"6938936716785", "ALZMNTABS01", "6938936716786"}

    async def test_one_client_erroring_degrades_to_other_hit(self):
        smdb_hit = ({"material": "PLA"}, [{"code": "6938936716785", "kind": "gtin", "is_refill": False}])
        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(side_effect=RuntimeError("unreachable"))),
            patch(
                "backend.app.services.spoolmandb_community_client.lookup",
                new=AsyncMock(return_value=smdb_hit),
            ),
            patch("backend.app.services.ofd_client.lookup_article", new=AsyncMock(side_effect=RuntimeError("boom"))),
            patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock(return_value=None)),
        ):
            result = await external_all_codes("6938936716785", "gtin", ENABLED)

        assert result is not None
        assert result[1] == "spoolmandb-community"

    async def test_both_miss_returns_none(self):
        with _patch_external():
            assert await external_all_codes("111111111117", "gtin", ENABLED) is None


class TestCodesForSpool:
    def test_all_columns_render_with_shared_refill_flag(self):
        spool = Spool(
            material="PLA",
            gtin_code="6938936716785",
            sku_code="17600",
            asin_code="B0CJLR62MF",
            other_code="MyShelf-a42",
            bought_as_refill=True,
        )
        codes = codes_for_spool(spool)
        by_code = {c["code"]: c for c in codes}
        assert set(by_code) == {"6938936716785", "17600", "B0CJLR62MF", "MyShelf-a42"}
        assert by_code["6938936716785"]["kind"] == "gtin"
        assert by_code["17600"]["kind"] == "sku"
        assert all(c["is_refill"] is True for c in codes)

    def test_empty_columns_render_nothing(self):
        assert codes_for_spool(Spool(material="PLA")) == []


class TestResolveBarcode:
    async def test_own_inventory_hit_skips_external_and_returns_stored_codes(self, engine):
        """A code stored in ANY typed column resolves from the local table with
        zero external calls, returning the owning roll's fields and its full
        stored code set."""
        async with AsyncSession(engine) as session:
            await _insert_spool(
                session,
                1,
                material="ASA",
                brand="Polymaker",
                gtin_code="6938936716785",
                sku_code="ALZMNTABS01",
                bought_as_refill=True,
            )

            with _forbid_external():
                fields, source, all_codes = await resolve_barcode(session, "6938936716785", "gtin", ENABLED)

        assert source == "inventory"
        assert fields["material"] == "ASA"
        assert fields["brand"] == "Polymaker"
        by_code = {c["code"]: c for c in all_codes}
        assert set(by_code) == {"6938936716785", "ALZMNTABS01"}
        assert by_code["ALZMNTABS01"]["is_refill"] is True

    async def test_sku_column_matches_too(self, engine):
        """Scanning the article code printed on the box matches a roll stored
        by its SKU column — the requirement behind searchable sku_code."""
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1, brand="Bambu Lab", sku_code="17600")
            with _forbid_external():
                fields, source, _ = await resolve_barcode(session, "17600", "sku", ENABLED)
        assert source == "inventory"
        assert fields["brand"] == "Bambu Lab"

    async def test_other_code_matches_case_insensitively(self, engine):
        """A user-owned other_code (self-printed barcode) stores the user's
        casing verbatim but must still resolve on re-scan — the scanned side
        arrives canonicalized (uppercased), so the match is case-insensitive."""
        from backend.app.schemas.spool import classify_code

        canonical, kind = classify_code("MyShelf-a42")  # what the route passes in
        assert canonical == "MYSHELF-A42"
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1, brand="Custom", other_code="MyShelf-a42")
            with _forbid_external():
                fields, source, _ = await resolve_barcode(session, canonical, kind, ENABLED)
        assert source == "inventory"
        assert fields["brand"] == "Custom"

    async def test_newest_roll_wins_when_two_spools_share_a_code(self, engine):
        """Six identical boxes = six rolls sharing one GTIN — resolution picks
        the newest roll, so the freshest user edits become the template."""
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1, brand="Old", gtin_code="6938936716785", created_at=datetime(2024, 1, 1))
            await _insert_spool(session, 2, brand="New", gtin_code="6938936716785", created_at=datetime(2024, 6, 1))

            with _forbid_external():
                fields, source, _ = await resolve_barcode(session, "6938936716785", "gtin", ENABLED)

        assert source == "inventory"
        assert fields["brand"] == "New"

    async def test_spoolman_client_hit_resolves_from_spoolman_not_local_tables(self, engine):
        """With a Spoolman client supplied, 'own inventory' means Spoolman's
        spools (typed bambu_* extras) — the local table is not consulted and
        external lookups are skipped."""
        spoolman_spool = {
            "id": 7,
            "filament": {
                "material": "PLA",
                "name": "PLA Basic",
                "vendor": {"name": "Bambu Lab"},
                "color_hex": "FF0000",
            },
            "extra": {
                "bambu_gtin_code": '"6938936716785"',
                "bambu_sku_code": '"ALZMNTABS01"',
            },
        }
        client = AsyncMock()
        client.find_spool_by_barcode = AsyncMock(return_value=spoolman_spool)

        async with AsyncSession(engine) as session:
            with _forbid_external():
                fields, source, all_codes = await resolve_barcode(
                    session, "6938936716785", "gtin", ENABLED, spoolman_client=client
                )

        client.find_spool_by_barcode.assert_awaited_once_with("6938936716785")
        assert source == "inventory"
        assert fields["material"] == "PLA"
        assert fields["brand"] == "Bambu Lab"
        assert {c["code"] for c in all_codes} == {"6938936716785", "ALZMNTABS01"}

    async def test_spoolman_error_falls_back_to_external(self, engine):
        client = AsyncMock()
        client.find_spool_by_barcode = AsyncMock(side_effect=RuntimeError("unreachable"))
        ofd_hit = ({"material": "PETG"}, [{"code": "12345678905", "kind": "gtin", "is_refill": False}])

        async with AsyncSession(engine) as session:
            with _patch_external(ofd=ofd_hit):
                fields, source, _ = await resolve_barcode(
                    session, "12345678905", "gtin", ENABLED, spoolman_client=client
                )

        assert source == "ofd"
        assert fields["material"] == "PETG"

    async def test_no_match_anywhere_returns_empty(self, engine):
        async with AsyncSession(engine) as session:
            with _patch_external():
                assert await resolve_barcode(session, "111111111117", "gtin", ENABLED) == ({}, None, [])

    async def test_disabled_setting_returns_empty_without_external_calls(self, engine):
        async with AsyncSession(engine) as session:
            with _forbid_external():
                assert await resolve_barcode(session, "111111111117", "gtin", DISABLED) == ({}, None, [])


class TestRouteScannedCode:
    async def test_empty_scan_routes_nothing(self):
        with _forbid_external():
            routed = await route_scanned_code("  ", ENABLED)
        assert routed == {"gtin_code": None, "asin_code": None, "sku_code": None, "other_code": None}

    async def test_gtin_scan_fills_gtin_and_pairs_sku_from_ofd(self):
        """Scan the box GTIN: gtin_code = the canonical scan, sku_code = the
        article printed on the SAME size row (OFD pairing) — never a sibling
        from another package size."""
        with _patch_external(ofd_paired=(True, "17600")):
            routed = await route_scanned_code("06938936716785", ENABLED)
        assert routed["gtin_code"] == "6938936716785"
        assert routed["sku_code"] == "17600"
        assert routed["other_code"] is None

    async def test_gtin_scan_falls_back_to_smdb_same_package_codes(self):
        """No OFD pairing: SpoolmanDB variants are per-package, so their code
        list may fill the SKU/ASIN slots."""
        smdb_hit = (
            {"material": "PLA"},
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "17600", "kind": "sku", "is_refill": False},
                {"code": "B0CJLR62MF", "kind": "sku", "is_refill": False},
            ],
        )
        with _patch_external(smdb=smdb_hit):
            routed = await route_scanned_code("6938936716785", ENABLED)
        assert routed["gtin_code"] == "6938936716785"
        assert routed["sku_code"] == "17600"
        assert routed["asin_code"] == "B0CJLR62MF"

    async def test_asin_scan_fills_asin_and_cross_fills_gtin(self):
        smdb_hit = (
            {"material": "PLA"},
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "B0CJLR62MF", "kind": "sku", "is_refill": False},
            ],
        )
        with _patch_external(smdb_sku=smdb_hit):
            routed = await route_scanned_code("B0CJLR62MF", ENABLED)
        assert routed["asin_code"] == "B0CJLR62MF"
        assert routed["gtin_code"] == "6938936716785"
        assert routed["other_code"] is None

    async def test_refill_flag_picks_the_matching_gtin(self):
        """A SpoolmanDB variant lists the with-spool EAN and the refill EAN
        side by side — bought_as_refill selects the right one."""
        smdb_hit = (
            {"material": "PLA"},
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "6938936716786", "kind": "gtin", "is_refill": True},
                {"code": "17600", "kind": "sku", "is_refill": False},
            ],
        )
        with _patch_external(smdb_sku=smdb_hit):
            refill = await route_scanned_code("17600", ENABLED, bought_as_refill=True)
            with_spool = await route_scanned_code("17600", ENABLED, bought_as_refill=False)
        assert refill["gtin_code"] == "6938936716786"
        assert with_spool["gtin_code"] == "6938936716785"

    async def test_known_sku_scan_lands_in_sku_code(self):
        with _patch_external(ofd_paired=(True, "6938936716785")):
            routed = await route_scanned_code("17600", ENABLED)
        assert routed["sku_code"] == "17600"
        assert routed["gtin_code"] == "6938936716785"
        assert routed["other_code"] is None

    async def test_code128_symbology_demotes_lucky_checksum_at_routing(self):
        """The scan-time AIM hint must survive into create-time routing: a
        Code 128 numeric with a valid mod-10 checksum, unknown to both DBs,
        lands in other_code — NOT re-promoted into gtin_code."""
        with _patch_external():
            routed = await route_scanned_code("06938936716785", ENABLED, symbology="code128")
        assert routed == {
            "gtin_code": None,
            "asin_code": None,
            "sku_code": None,
            "other_code": "06938936716785",
        }

    async def test_ean_upc_symbology_routes_gtin_normally(self):
        with _patch_external(ofd_paired=(True, "17600")):
            routed = await route_scanned_code("06938936716785", ENABLED, symbology="ean-upc")
        assert routed["gtin_code"] == "6938936716785"
        assert routed["sku_code"] == "17600"

    async def test_unknown_code_lands_verbatim_in_other_code(self):
        """Not a GTIN, not an ASIN, unknown to both DBs: the user's own code
        space — trimmed, case preserved, no other column touched."""
        with _patch_external():
            routed = await route_scanned_code("  MyShelf-a42  ", ENABLED)
        assert routed == {
            "gtin_code": None,
            "asin_code": None,
            "sku_code": None,
            "other_code": "MyShelf-a42",
        }

    async def test_lookup_disabled_still_routes_structurally(self):
        """With the toggle off there is no cross-fill and no sku/other
        promotion evidence — a GTIN still lands structurally, an alphanumeric
        candidate conservatively lands in other_code, and no external client
        is touched."""
        with _forbid_external():
            gtin = await route_scanned_code("6938936716785", ENABLED | DISABLED)
            unknown = await route_scanned_code("17600", ENABLED | DISABLED)
        assert gtin["gtin_code"] == "6938936716785"
        assert gtin["sku_code"] is None
        assert unknown["other_code"] == "17600"
        assert unknown["sku_code"] is None
