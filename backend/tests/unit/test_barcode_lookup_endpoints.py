"""Unit tests for the scan-to-add barcode/SKU lookup endpoints.

Tests:
- `_classify_code`: GTIN (checksummed, standard length) vs. SKU/article-number
  classification, the gate that routes a scanned/typed code down the GTIN or
  SKU resolution path (e.g. a Code 128 "inventory barcode" with no UPC/EAN).
- GET /inventory/barcode/{barcode}: native-inventory hit (via the SpoolCode
  table), OFD hit, SpoolmanDB-Community hit, disabled setting, unmatched.
- SKU path: same chain via lookup_article/lookup_sku instead of lookup.
- Cross-database enrichment: a hit in one external database that's missing
  fields (e.g. nozzle temps) gets them filled in from a probe of the other
  database against a sibling code; all_codes is the union of both.
- POST /inventory/barcode/parse-label: heuristic parse with and without an
  embedded barcode.
- Spoolman-mode awareness: when Spoolman is the active inventory backend,
  "the user's own inventory" means Spoolman's spools (via
  SpoolmanClient.find_spool_by_barcode), not the local Spool table.
"""

from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes.inventory import LabelParseRequest, _classify_code, lookup_barcode, parse_label


def _make_mock_spool(**overrides):
    spool = MagicMock()
    defaults = {
        "material": "PLA",
        "brand": "Sunlu",
        "subtype": "Plus",
        "color_name": "Black",
        "rgba": "000000FF",
        "label_weight": 1000,
        "nozzle_temp_min": 190,
        "nozzle_temp_max": 230,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(spool, key, value)
    return spool


def _spool_code_row(spool_id=1, code="", kind="gtin", is_refill=False):
    row = MagicMock()
    row.spool_id = spool_id
    row.code = code
    row.kind = kind
    row.is_refill = is_refill
    return row


def _result_first(value):
    result = MagicMock()
    result.scalars.return_value.first.return_value = value
    return result


def _result_all(values):
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def _make_settings_row(key: str, value: str):
    row = MagicMock()
    row.key = key
    row.value = value
    return row


def _db(settings_rows=None, results=None):
    """Sequential db.execute mock.

    The first call always serves `_load_settings_map`. Every call after that
    pops the next canned result off `results` in order (own-inventory
    resolution issues up to 3 queries: the SpoolCode hit, the owning Spool,
    then all SpoolCode rows for that spool — see `_resolve_barcode`). Once
    `results` is exhausted, an empty result (no match) is returned, so tests
    that never reach the local-table query don't need an exact call count.
    """
    db = AsyncMock()
    settings_result = MagicMock()
    settings_result.scalars.return_value.all.return_value = settings_rows or []
    remaining = list(results or [])
    empty = _result_first(None)
    empty.scalars.return_value.all.return_value = []

    call_count = {"n": 0}

    async def _execute(*_args, **_kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return settings_result
        return remaining.pop(0) if remaining else empty

    db.execute = _execute
    return db


@contextmanager
def _no_op_external():
    """Patch both external clients' GTIN+SKU lookup functions to miss, for
    tests that don't care about the external-fallback chain at all."""
    with ExitStack() as stack:
        stack.enter_context(patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=None)))
        stack.enter_context(patch("backend.app.services.ofd_client.lookup_article", new=AsyncMock(return_value=None)))
        stack.enter_context(
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=None))
        )
        stack.enter_context(
            patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock(return_value=None))
        )
        yield


class TestClassifyCode:
    def test_valid_upc_a_is_gtin(self):
        assert _classify_code("012345678905") == ("12345678905", "gtin")

    def test_valid_ean_13_is_gtin(self):
        assert _classify_code("06938936716785") == ("6938936716785", "gtin")

    def test_bad_checksum_falls_back_to_sku(self):
        # Right length (12 digits) but an invalid check digit.
        assert _classify_code("099999999999") == ("099999999999", "sku")

    def test_alphanumeric_code_is_sku(self):
        """A Code 128 manufacturer SKU/article number — e.g. Polymaker's
        inventory barcode with no UPC/EAN counterpart (issue that motivated
        this feature)."""
        assert _classify_code("ALZMNTABS01") == ("ALZMNTABS01", "sku")

    def test_sku_is_stripped_and_uppercased(self):
        assert _classify_code("  alzmntabs01  ") == ("ALZMNTABS01", "sku")

    def test_wrong_length_digit_string_is_sku(self):
        assert _classify_code("12345") == ("12345", "sku")


class TestLookupBarcodeEndpointGtinPath:
    @pytest.mark.asyncio
    async def test_native_inventory_hit_takes_priority_over_external(self):
        code_hit = _spool_code_row(spool_id=1, code="6938936716785", kind="gtin")
        spool = _make_mock_spool()
        all_codes = [_spool_code_row(1, "6938936716785", "gtin", False)]
        db = _db(results=[_result_first(code_hit), _result_first(spool), _result_all(all_codes)])

        with patch("backend.app.services.ofd_client.lookup", new=AsyncMock()) as mock_ofd_lookup:
            result = await lookup_barcode(barcode="06938936716785", db=db, _=None)

        mock_ofd_lookup.assert_not_called()
        assert result.matched is True
        assert result.source == "inventory"
        assert result.material == "PLA"
        assert result.brand == "Sunlu"
        assert result.linked_codes == []

    @pytest.mark.asyncio
    async def test_native_inventory_hit_reports_sibling_codes_as_linked(self):
        code_hit = _spool_code_row(spool_id=1, code="6938936716785", kind="gtin")
        spool = _make_mock_spool()
        all_codes = [
            _spool_code_row(1, "6938936716785", "gtin", False),
            _spool_code_row(1, "6938936716786", "gtin", True),
            _spool_code_row(1, "ALZMNTABS01", "sku", False),
        ]
        db = _db(results=[_result_first(code_hit), _result_first(spool), _result_all(all_codes)])

        result = await lookup_barcode(barcode="06938936716785", db=db, _=None)

        assert {c.code for c in result.linked_codes} == {"6938936716786", "ALZMNTABS01"}

    @pytest.mark.asyncio
    async def test_ofd_hit_when_no_native_match(self):
        db = _db()
        ofd_fields = {"material": "PETG", "brand": "Overture", "label_weight": 1000}
        ofd_codes = [{"code": "12345678905", "kind": "gtin", "is_refill": False}]

        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=(ofd_fields, ofd_codes))),
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=None)),
        ):
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        assert result.matched is True
        assert result.source == "ofd"
        assert result.material == "PETG"
        assert result.brand == "Overture"

    @pytest.mark.asyncio
    async def test_disabled_setting_skips_external_lookup_but_reports_disabled(self):
        db = _db(settings_rows=[_make_settings_row("barcode_lookup_enabled", "false")])

        with patch("backend.app.services.ofd_client.lookup", new=AsyncMock()) as mock_ofd_lookup:
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        mock_ofd_lookup.assert_not_called()
        assert result.enabled is False
        assert result.matched is False

    @pytest.mark.asyncio
    async def test_unmatched_barcode_returns_matched_false(self):
        db = _db()

        with _no_op_external():
            result = await lookup_barcode(barcode="111111111117", db=db, _=None)

        assert result.matched is False
        assert result.source is None
        assert result.material is None
        assert result.linked_codes == []

    @pytest.mark.asyncio
    async def test_barcode_is_canonicalized_in_response(self):
        db = _db()

        with _no_op_external():
            result = await lookup_barcode(barcode="0012345678905", db=db, _=None)

        assert result.barcode == "12345678905"


class TestLookupBarcodeEndpointSkuPath:
    """A Code 128-decoded manufacturer SKU/article number, routed through
    lookup_article (OFD) / lookup_sku (SpoolmanDB-Community) instead of the
    GTIN path — the feature that lets a Polymaker box with no UPC/EAN, only
    an inventory barcode, still resolve."""

    @pytest.mark.asyncio
    async def test_native_inventory_hit_for_sku(self):
        code_hit = _spool_code_row(spool_id=5, code="ALZMNTABS01", kind="sku")
        spool = _make_mock_spool(material="ASA", brand="Polymaker")
        all_codes = [_spool_code_row(5, "ALZMNTABS01", "sku", False)]
        db = _db(results=[_result_first(code_hit), _result_first(spool), _result_all(all_codes)])

        with patch("backend.app.services.ofd_client.lookup_article", new=AsyncMock()) as mock_article:
            result = await lookup_barcode(barcode="ALZMNTABS01", db=db, _=None)

        mock_article.assert_not_called()
        assert result.matched is True
        assert result.source == "inventory"
        assert result.brand == "Polymaker"

    @pytest.mark.asyncio
    async def test_ofd_article_hit_when_no_native_match(self):
        db = _db()
        ofd_fields = {"material": "ASA", "brand": "Polymaker", "label_weight": 750}
        ofd_codes = [{"code": "ALZMNTABS01", "kind": "sku", "is_refill": False}]

        with (
            patch(
                "backend.app.services.ofd_client.lookup_article", new=AsyncMock(return_value=(ofd_fields, ofd_codes))
            ),
            patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock(return_value=None)),
        ):
            result = await lookup_barcode(barcode="alzmntabs01", db=db, _=None)

        assert result.matched is True
        assert result.source == "ofd"
        assert result.material == "ASA"
        assert result.brand == "Polymaker"
        assert result.barcode == "ALZMNTABS01"

    @pytest.mark.asyncio
    async def test_spoolmandb_community_sku_hit_when_ofd_misses(self):
        db = _db()
        smdb_fields = {"material": "PLA", "brand": "Bambu Lab"}
        smdb_codes = [{"code": "SKU123", "kind": "sku", "is_refill": False}]

        with (
            patch("backend.app.services.ofd_client.lookup_article", new=AsyncMock(return_value=None)),
            patch(
                "backend.app.services.spoolmandb_community_client.lookup_sku",
                new=AsyncMock(return_value=(smdb_fields, smdb_codes)),
            ),
        ):
            result = await lookup_barcode(barcode="SKU123", db=db, _=None)

        assert result.matched is True
        assert result.source == "spoolmandb-community"
        assert result.material == "PLA"

    @pytest.mark.asyncio
    async def test_unmatched_sku_returns_matched_false(self):
        db = _db()

        with _no_op_external():
            result = await lookup_barcode(barcode="NOPE-999", db=db, _=None)

        assert result.matched is False
        assert result.source is None


class TestCrossReferenceEnrichment:
    """Whichever database resolves a code first, its sibling codes are probed
    against the *other* database to fill missing fields and union all_codes."""

    @pytest.mark.asyncio
    async def test_secondary_probe_fills_missing_fields_and_unions_codes(self):
        db = _db()
        # OFD resolves the scanned GTIN but has no nozzle-temp data; its sibling
        # SKU (from the same variant) isn't tried directly by the client.
        ofd_fields = {"material": "PLA", "brand": "Sunlu", "label_weight": 1000}
        ofd_codes = [
            {"code": "6938936716785", "kind": "gtin", "is_refill": False},
            {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
        ]
        # SpoolmanDB-Community misses the GTIN directly, but resolves the
        # sibling SKU with the nozzle temps OFD lacked.
        smdb_probe_fields = {"nozzle_temp_min": 190, "nozzle_temp_max": 220}
        smdb_probe_codes = [
            {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
            {"code": "6938936716786", "kind": "gtin", "is_refill": True},
        ]

        mock_smdb_lookup = AsyncMock(return_value=None)
        mock_smdb_lookup_sku = AsyncMock(return_value=(smdb_probe_fields, smdb_probe_codes))
        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=(ofd_fields, ofd_codes))),
            patch("backend.app.services.spoolmandb_community_client.lookup", new=mock_smdb_lookup),
            patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=mock_smdb_lookup_sku),
        ):
            result = await lookup_barcode(barcode="06938936716785", db=db, _=None)

        assert result.source == "ofd"
        assert result.material == "PLA"
        assert result.nozzle_temp_min == 190
        assert result.nozzle_temp_max == 220
        mock_smdb_lookup_sku.assert_awaited_once_with("ALZMNTABS01")
        linked = {c.code for c in result.linked_codes}
        assert linked == {"ALZMNTABS01", "6938936716786"}

    @pytest.mark.asyncio
    async def test_both_databases_hit_directly_skips_sibling_probing(self):
        db = _db()
        ofd_fields = {"material": "PLA", "brand": "Sunlu"}
        ofd_codes = [{"code": "6938936716785", "kind": "gtin", "is_refill": False}]
        smdb_fields = {"nozzle_temp_min": 190}
        smdb_codes = [{"code": "6938936716785", "kind": "gtin", "is_refill": False}]

        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=(ofd_fields, ofd_codes))),
            patch(
                "backend.app.services.spoolmandb_community_client.lookup",
                new=AsyncMock(return_value=(smdb_fields, smdb_codes)),
            ),
            patch("backend.app.services.ofd_client.lookup_article", new=AsyncMock()) as mock_ofd_article,
            patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock()) as mock_smdb_sku,
        ):
            result = await lookup_barcode(barcode="06938936716785", db=db, _=None)

        mock_ofd_article.assert_not_called()
        mock_smdb_sku.assert_not_called()
        assert result.source == "ofd"
        assert result.nozzle_temp_min == 190  # filled in from the SpoolmanDB-Community hit

    @pytest.mark.asyncio
    async def test_external_lookup_error_degrades_to_unmatched(self):
        """A connectivity failure on one external client must not error the request."""
        db = _db()

        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(side_effect=RuntimeError("unreachable"))),
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=None)),
        ):
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        assert result.matched is False
        assert result.source is None


class TestLookupBarcodeSpoolmanMode:
    """When Spoolman is the active inventory backend, resolution checks
    Spoolman's spools instead of the local Spool/SpoolCode tables."""

    @pytest.mark.asyncio
    async def test_spoolman_hit_takes_priority_and_skips_local_tables(self):
        db = _db()
        spoolman_spool = {
            "id": 7,
            "filament": {
                "material": "PLA",
                "name": "PLA Basic",
                "vendor": {"name": "Bambu Lab"},
                "color_hex": "FF0000",
            },
            "extra": {},
        }
        mock_client = AsyncMock()
        mock_client.find_spool_by_barcode = AsyncMock(return_value=spoolman_spool)

        with (
            patch(
                "backend.app.api.routes.inventory._ensure_spoolman_client",
                new=AsyncMock(return_value=mock_client),
            ),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock()) as mock_ofd_lookup,
        ):
            result = await lookup_barcode(barcode="06938936716785", db=db, _=None)

        mock_client.find_spool_by_barcode.assert_called_once_with("6938936716785")
        mock_ofd_lookup.assert_not_called()
        assert result.matched is True
        assert result.source == "inventory"
        assert result.material == "PLA"
        assert result.brand == "Bambu Lab"

    @pytest.mark.asyncio
    async def test_spoolman_miss_falls_back_to_ofd(self):
        db = _db()
        mock_client = AsyncMock()
        mock_client.find_spool_by_barcode = AsyncMock(return_value=None)
        ofd_fields = {"material": "PETG", "brand": "Overture", "label_weight": 1000}
        ofd_codes = [{"code": "12345678905", "kind": "gtin", "is_refill": False}]

        with (
            patch(
                "backend.app.api.routes.inventory._ensure_spoolman_client",
                new=AsyncMock(return_value=mock_client),
            ),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=(ofd_fields, ofd_codes))),
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=None)),
        ):
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        assert result.matched is True
        assert result.source == "ofd"
        assert result.material == "PETG"

    @pytest.mark.asyncio
    async def test_spoolman_lookup_error_falls_back_to_ofd(self):
        """A Spoolman connectivity failure degrades to OFD instead of erroring the request."""
        db = _db()
        mock_client = AsyncMock()
        mock_client.find_spool_by_barcode = AsyncMock(side_effect=RuntimeError("unreachable"))
        ofd_fields = {"material": "ABS", "brand": "Generic"}
        ofd_codes = [{"code": "12345678905", "kind": "gtin", "is_refill": False}]

        with (
            patch(
                "backend.app.api.routes.inventory._ensure_spoolman_client",
                new=AsyncMock(return_value=mock_client),
            ),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=(ofd_fields, ofd_codes))),
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=None)),
        ):
            result = await lookup_barcode(barcode="012345678905", db=db, _=None)

        assert result.matched is True
        assert result.source == "ofd"

    @pytest.mark.asyncio
    async def test_spoolman_own_inventory_linked_codes_surfaced(self):
        db = _db()
        spoolman_spool = {
            "id": 7,
            "filament": {"material": "PLA", "name": "PLA Basic", "vendor": {}, "color_hex": "FF0000"},
            "extra": {
                "bambu_barcode": '"6938936716785"',
                "bambu_linked_codes": (
                    '[{"code": "6938936716785", "kind": "gtin", "is_refill": false}, '
                    '{"code": "ALZMNTABS01", "kind": "sku", "is_refill": false}]'
                ),
            },
        }
        mock_client = AsyncMock()
        mock_client.find_spool_by_barcode = AsyncMock(return_value=spoolman_spool)

        with patch(
            "backend.app.api.routes.inventory._ensure_spoolman_client",
            new=AsyncMock(return_value=mock_client),
        ):
            result = await lookup_barcode(barcode="06938936716785", db=db, _=None)

        assert result.source == "inventory"
        assert [c.code for c in result.linked_codes] == ["ALZMNTABS01"]


class TestParseLabelEndpoint:
    @pytest.mark.asyncio
    async def test_parses_text_without_barcode(self):
        db = _db()

        with patch("backend.app.services.ofd_client.get_brands", new=AsyncMock(return_value=[])):
            result = await parse_label(
                payload=LabelParseRequest(text="SUNLU PLA+ Filament 1.75mm Black 1KG"),
                db=db,
                _=None,
            )

        assert result.material == "PLA"
        assert result.brand == "Sunlu"
        assert result.barcode is None
        assert result.source == "parsed"
        assert result.matched is False
        assert result.linked_codes == []

    @pytest.mark.asyncio
    async def test_embedded_barcode_resolved_and_overrides_guesses(self):
        db = _db()
        ofd_fields = {"material": "PETG", "brand": "Overture", "label_weight": 500}
        ofd_codes = [{"code": "6938936716785", "kind": "gtin", "is_refill": False}]

        with (
            patch("backend.app.services.ofd_client.get_brands", new=AsyncMock(return_value=[])),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=(ofd_fields, ofd_codes))),
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=None)),
        ):
            result = await parse_label(
                payload=LabelParseRequest(text="Generic PLA Black EAN: 6938936716785"),
                db=db,
                _=None,
            )

        # OFD-resolved fields win over the "PLA"/"Black" text guesses.
        assert result.material == "PETG"
        assert result.brand == "Overture"
        assert result.source == "ofd"
        assert result.matched is True
        assert result.barcode == "6938936716785"

    @pytest.mark.asyncio
    async def test_embedded_barcode_resolved_via_spoolmandb_community_when_ofd_misses(self):
        """A SpoolmanDB-Community-only hit must still report matched=True — regression
        guard for the `matched = source in (...)` check needing the new source value."""
        db = _db()
        smdb_fields = {"material": "PLA", "brand": "Bambu Lab", "label_weight": 1000}
        smdb_codes = [{"code": "6975337031345", "kind": "gtin", "is_refill": False}]

        with (
            patch("backend.app.services.ofd_client.get_brands", new=AsyncMock(return_value=[])),
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=None)),
            patch(
                "backend.app.services.spoolmandb_community_client.lookup",
                new=AsyncMock(return_value=(smdb_fields, smdb_codes)),
            ),
        ):
            result = await parse_label(
                payload=LabelParseRequest(text="Generic PLA Ivory EAN: 6975337031345"),
                db=db,
                _=None,
            )

        assert result.material == "PLA"
        assert result.brand == "Bambu Lab"
        assert result.source == "spoolmandb-community"
        assert result.matched is True
