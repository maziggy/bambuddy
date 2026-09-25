"""Schema validation tests for the typed spool code fields.

Locks in the write-path rules: `gtin_code` accepts only checksum-valid
retail barcodes (canonicalized so a later scan matches the stored spool
regardless of UPC-A/EAN-13 form), `asin_code` accepts 10-char ASINs,
`sku_code` trims + uppercases verbatim, and `other_code` — the user's own
code space — is stored trimmed with case preserved.
"""

import pytest
from pydantic import ValidationError

from backend.app.schemas.spool import (
    SpoolCreate,
    SpoolResponse,
    SpoolUpdate,
    classify_code,
    looks_like_url_payload,
    normalize_barcode,
)


class TestNormalizeBarcode:
    def test_strips_leading_zeros(self):
        assert normalize_barcode("0012345678905") == "12345678905"

    def test_strips_non_digit_characters(self):
        assert normalize_barcode("012-345-678-905") == "12345678905"

    def test_upc_a_and_ean_13_forms_match(self):
        assert normalize_barcode("012345678905") == normalize_barcode("0012345678905")

    def test_none_stays_none(self):
        assert normalize_barcode(None) is None

    def test_empty_string_becomes_none(self):
        assert normalize_barcode("") is None

    def test_whitespace_only_becomes_none(self):
        assert normalize_barcode("   ") is None

    def test_all_zeros_returns_zero(self):
        assert normalize_barcode("0000") == "0"

    def test_alphanumeric_sku_is_not_digit_stripped(self):
        """A Code 128 manufacturer SKU/article number (e.g. Polymaker's
        inventory barcode with no UPC/EAN counterpart) must survive intact —
        stripping non-digits would mangle "ALZMNTABS01" down to "1"."""
        assert normalize_barcode("ALZMNTABS01") == "ALZMNTABS01"

    def test_sku_is_trimmed_and_uppercased(self):
        assert normalize_barcode("  alzmntabs01  ") == "ALZMNTABS01"


class TestLooksLikeUrlPayload:
    def test_bambu_qr_colonless_form(self):
        # HID keymaps that can't type ":" decode the Bambu spool QR like this.
        assert looks_like_url_payload("HTTPS//E.BAMBULAB.COM/T?C=SMY5WWK01KBZL4RN") is True

    def test_full_https_url(self):
        assert looks_like_url_payload("https://example.com/x") is True

    def test_www_form(self):
        assert looks_like_url_payload("www.example.com") is True

    def test_gtin_is_not_url(self):
        assert looks_like_url_payload("6938936716785") is False

    def test_sku_and_user_codes_are_not_urls(self):
        assert looks_like_url_payload("ALZMNTABS01") is False
        assert looks_like_url_payload("MyShelf-a42") is False

    def test_none_and_empty(self):
        assert looks_like_url_payload(None) is False
        assert looks_like_url_payload("") is False


class TestClassifyCode:
    def test_valid_upc_a_is_gtin(self):
        assert classify_code("012345678905") == ("12345678905", "gtin")

    def test_valid_ean_13_is_gtin(self):
        assert classify_code("06938936716785") == ("6938936716785", "gtin")

    def test_bad_checksum_falls_back_to_sku(self):
        # Right length (12 digits) but an invalid check digit.
        assert classify_code("099999999999") == ("99999999999", "sku")

    def test_modern_asin_is_asin(self):
        """B0 + 8 alphanumerics — the shape every filament ASIN in both
        community databases matches (91 in SpoolmanDB, 7 in OFD, all B0…)."""
        assert classify_code("B0CJLR62MF") == ("B0CJLR62MF", "asin")

    def test_asin_is_case_normalized(self):
        assert classify_code("b0cjlr62mf") == ("B0CJLR62MF", "asin")

    def test_ten_char_non_b0_code_is_sku(self):
        # 10 alphanumerics without the B0 prefix: SKU candidate, not ASIN.
        assert classify_code("X0CJLR62MF") == ("X0CJLR62MF", "sku")

    def test_alphanumeric_code_is_sku(self):
        """A Code 128 manufacturer SKU/article number — e.g. Polymaker's
        inventory barcode with no UPC/EAN counterpart."""
        assert classify_code("ALZMNTABS01") == ("ALZMNTABS01", "sku")

    def test_sku_is_stripped_and_uppercased(self):
        assert classify_code("  alzmntabs01  ") == ("ALZMNTABS01", "sku")

    def test_short_digit_string_below_floor_is_sku(self):
        assert classify_code("12345") == ("12345", "sku")

    def test_leading_zero_upc_a_still_classifies_gtin_after_stripping(self):
        """Regression for the exact bug found in PR #1895's review: a UPC-A
        with a leading zero must classify as gtin whether you feed it the raw
        scanned value or the already-normalize_barcode'd stored value — the
        GTIN checksum is invariant to leading-zero padding (a leading zero
        always lands in a weight-agnostic position relative to the check
        digit), so re-padding the stripped canonical form and checking there
        gives the same verdict the raw value would have."""
        raw = "036000291452"  # 12-digit UPC-A, one leading zero
        stored = normalize_barcode(raw)
        assert stored == "36000291452"
        assert classify_code(raw) == ("36000291452", "gtin")
        assert classify_code(stored) == ("36000291452", "gtin")

    def test_code128_symbology_skips_the_gtin_rung(self):
        """A Code 128 symbol wraps arbitrary data — a numeric that happens to
        pass the mod-10 checksum (a lot number, an internal article code) must
        not be promoted to gtin when the scanner says the symbol wasn't
        EAN/UPC/ITF."""
        assert classify_code("06938936716785", symbology="code128") == ("6938936716785", "sku")

    def test_gtin_symbologies_keep_the_checksum_gate(self):
        # A GTIN-carrying symbology changes nothing: valid checksums still
        # classify gtin, invalid ones still fall through.
        assert classify_code("06938936716785", symbology="ean-upc") == ("6938936716785", "gtin")
        assert classify_code("16938936716782", symbology="itf") == ("16938936716782", "gtin")
        assert classify_code("099999999999", symbology="ean-upc") == ("99999999999", "sku")

    def test_non_gtin_symbology_still_walks_the_rest_of_the_ladder(self):
        # ASINs live in Code 128 symbols — the ASIN rung must still fire.
        assert classify_code("B0CJLR62MF", symbology="code128") == ("B0CJLR62MF", "asin")

    def test_no_symbology_keeps_pure_heuristic(self):
        assert classify_code("06938936716785", symbology=None) == ("6938936716785", "gtin")

    def test_classification_is_stable_across_normalize_barcode(self):
        """classify_code(x) == classify_code(normalize_barcode(x)) for any x —
        this is what guarantees a scan (raw input) and a repeat lookup of the
        stored value (already normalized) can never disagree on kind."""
        for raw in (
            "0012345678905",
            "012345678905",
            "06938936716785",
            "0000000000123456",  # heavily zero-padded, still within GTIN-14
            "ALZMNTABS01",
            "  alzmntabs01  ",
            "B0CJLR62MF",
            "099999999999",
            "12345",
            "",
        ):
            stored = normalize_barcode(raw)
            assert classify_code(raw) == classify_code(stored), raw


class TestGtinCodeValidation:
    def test_canonicalizes_on_create(self):
        spool = SpoolCreate(material="PLA", gtin_code="0012345678905")
        assert spool.gtin_code == "12345678905"

    def test_accepts_null(self):
        spool = SpoolCreate(material="PLA", gtin_code=None)
        assert spool.gtin_code is None

    def test_blank_normalizes_to_none(self):
        spool = SpoolCreate(material="PLA", gtin_code="")
        assert spool.gtin_code is None

    def test_rejects_non_gtin_value(self):
        """Only true retail barcodes belong in gtin_code — a SKU typed into
        the field is rejected rather than silently reclassified."""
        with pytest.raises(ValidationError):
            SpoolCreate(material="PLA", gtin_code="ALZMNTABS01")

    def test_rejects_bad_checksum(self):
        with pytest.raises(ValidationError):
            SpoolCreate(material="PLA", gtin_code="099999999999")

    def test_canonicalizes_on_update(self):
        update = SpoolUpdate(gtin_code="0012345678905")
        assert update.gtin_code == "12345678905"

    def test_unset_stays_unset_on_update(self):
        update = SpoolUpdate()
        assert "gtin_code" not in update.model_fields_set


class TestAsinCodeValidation:
    def test_uppercases(self):
        spool = SpoolCreate(material="PLA", asin_code="b0cjlr62mf")
        assert spool.asin_code == "B0CJLR62MF"

    def test_rejects_wrong_length(self):
        with pytest.raises(ValidationError):
            SpoolCreate(material="PLA", asin_code="B0SHORT")

    def test_blank_normalizes_to_none(self):
        spool = SpoolCreate(material="PLA", asin_code="  ")
        assert spool.asin_code is None


class TestSkuCodeValidation:
    def test_trims_and_uppercases(self):
        spool = SpoolCreate(material="PLA", sku_code="  alzmntabs01  ")
        assert spool.sku_code == "ALZMNTABS01"

    def test_numeric_sku_is_not_gtin_canonicalized(self):
        """A numeric article number like Bambu's 17600 must store byte-for-byte
        as printed — no leading-zero stripping games."""
        spool = SpoolCreate(material="PLA", sku_code="17600")
        assert spool.sku_code == "17600"

    def test_rejects_over_64_chars(self):
        with pytest.raises(ValidationError):
            SpoolCreate(material="PLA", sku_code="A" * 65)


class TestOtherCodeValidation:
    def test_trims_but_preserves_case(self):
        """other_code is the user's own code space (self-printed barcodes
        welcome) — no case or format rules are imposed."""
        spool = SpoolCreate(material="PLA", other_code="  MyShelf-a42  ")
        assert spool.other_code == "MyShelf-a42"

    def test_blank_normalizes_to_none(self):
        spool = SpoolCreate(material="PLA", other_code="   ")
        assert spool.other_code is None


class TestBoughtAsRefillAndScannedCode:
    def test_bought_as_refill_defaults_false(self):
        spool = SpoolCreate(material="PLA")
        assert spool.bought_as_refill is False

    def test_scanned_code_is_create_only(self):
        """scanned_code is the write-only raw-scan hint the create route
        classifies and routes — it must not exist on SpoolUpdate (edits work
        on the explicit typed fields)."""
        assert "scanned_code" in SpoolCreate.model_fields
        assert "scanned_code" not in SpoolUpdate.model_fields

    def test_bought_as_refill_updatable(self):
        update = SpoolUpdate(bought_as_refill=True)
        assert update.bought_as_refill is True


class TestSpoolResponseCodesUnconstrained:
    def test_legacy_invalid_values_do_not_500(self):
        """rgba already has this same escape hatch (#1055) — a code written
        past validation (SQLite doesn't enforce VARCHAR length or our format
        rules) must still read back instead of 500ing the inventory list."""
        response = SpoolResponse.model_validate(
            {
                "id": 1,
                "material": "PLA",
                "gtin_code": "A" * 100,  # not even a GTIN — must still load
                "asin_code": "not-an-asin",
                "label_weight": 1000,
                "core_weight": 250,
                "weight_used": 0,
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
            }
        )
        assert response.gtin_code == "A" * 100
        assert response.asin_code == "not-an-asin"
        assert response.bought_as_refill is False
