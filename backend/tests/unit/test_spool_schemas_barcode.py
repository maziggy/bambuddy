"""Schema validation tests for the spool `barcode` field.

Locks in the write-path canonicalization: a manually-typed barcode must
normalize to the same digits-only, leading-zeros-stripped form the
scan-to-add lookup already produces, so a later scan of the same physical
barcode matches the stored spool via `_resolve_barcode`'s native-inventory
check regardless of which UPC-A/EAN-13 form was typed or scanned.
"""

from backend.app.schemas.spool import SpoolCreate, SpoolUpdate, normalize_barcode


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


class TestSpoolCreateBarcodeValidation:
    def test_canonicalizes_on_create(self):
        spool = SpoolCreate(material="PLA", barcode="0012345678905")
        assert spool.barcode == "12345678905"

    def test_accepts_null_barcode(self):
        spool = SpoolCreate(material="PLA", barcode=None)
        assert spool.barcode is None

    def test_blank_barcode_normalizes_to_none(self):
        spool = SpoolCreate(material="PLA", barcode="")
        assert spool.barcode is None

    def test_sku_barcode_survives_create(self):
        spool = SpoolCreate(material="PLA", barcode="ALZMNTABS01")
        assert spool.barcode == "ALZMNTABS01"


class TestSpoolUpdateBarcodeValidation:
    def test_canonicalizes_on_update(self):
        update = SpoolUpdate(barcode="0012345678905")
        assert update.barcode == "12345678905"

    def test_unset_barcode_stays_unset(self):
        update = SpoolUpdate()
        assert "barcode" not in update.model_fields_set
