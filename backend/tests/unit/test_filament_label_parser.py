"""Unit tests for the filament label OCR heuristic parser.

Tests:
- parse_title() field extraction (brand, material, subtype, color, weight, temps)
- extract_barcode() labelled and bare-digit-run extraction
"""

from backend.app.services.filament_label_parser import extract_barcode, parse_title


class TestParseTitle:
    def test_empty_title_returns_empty_dict(self):
        assert parse_title("") == {}
        assert parse_title(None) == {}

    def test_sunlu_pla_plus_black_1kg(self):
        fields = parse_title("SUNLU PLA+ Filament 1.75mm Black 1KG")
        assert fields["brand"] == "Sunlu"
        assert fields["material"] == "PLA"
        assert "Plus" in fields["subtype"]
        assert fields["color_name"] == "Black"
        assert fields["rgba"] == "000000FF"
        assert fields["label_weight"] == 1000
        assert fields["diameter_mm"] == 1.75

    def test_overture_petg_matte_gray_1kg(self):
        fields = parse_title("Overture PETG 1.75 mm Matte Gray 1kg Spool")
        assert fields["brand"] == "Overture"
        assert fields["material"] == "PETG"
        assert fields["subtype"] == "Matte"
        assert fields["color_name"] == "Gray"
        assert fields["label_weight"] == 1000

    def test_esun_abs_plus_red_1000g(self):
        fields = parse_title("eSUN ABS+ 3D Printer Filament 1.75mm Red 1000g")
        assert fields["brand"] == "eSUN"
        assert fields["material"] == "ABS"
        assert fields["color_name"] == "Red"
        assert fields["label_weight"] == 1000

    def test_polymaker_polyterra_carbon_fiber_500g(self):
        fields = parse_title("Polymaker PolyTerra PLA Carbon Fiber 1.75mm 500g")
        assert fields["brand"] == "Polymaker"
        assert fields["material"] == "PLA"
        assert fields["subtype"] == "Carbon Fiber"
        assert fields["label_weight"] == 500

    def test_short_material_token_does_not_false_match_inside_brand(self):
        # "PA" (Nylon) must not fire on "PAnchroma" / similar; Panchroma isn't a
        # known brand here, but the guard applies generally to brand-name overlap.
        fields = parse_title("Overture PETG 1.75mm Black 1kg")
        assert fields["material"] == "PETG"

    def test_extra_brands_augment_builtin_list(self):
        fields = parse_title("Panchroma PLA White 1kg", extra_brands=["Panchroma"])
        assert fields["brand"] == "Panchroma"

    def test_nozzle_temp_range(self):
        fields = parse_title("Generic PLA 190-220C Black 1kg")
        assert fields["nozzle_temp_min"] == 190
        assert fields["nozzle_temp_max"] == 220

    def test_explicit_hex_overrides_color_name_guess(self):
        fields = parse_title("Generic PLA Black #FF00FF 1kg")
        assert fields["rgba"] == "FF00FFFF"

    def test_no_material_no_brand_returns_partial_fields(self):
        fields = parse_title("Mystery Spool 1kg")
        assert "material" not in fields
        assert "brand" not in fields
        assert fields["label_weight"] == 1000


class TestExtractBarcode:
    def test_labelled_ean(self):
        assert extract_barcode("EAN: 6938936716785") == "6938936716785"

    def test_labelled_upc_with_hash(self):
        assert extract_barcode("UPC#012345678905") == "012345678905"

    def test_bare_digit_run(self):
        assert extract_barcode("Some label text 6938936716785 more text") == "6938936716785"

    def test_no_barcode_present(self):
        assert extract_barcode("SUNLU PLA+ Black 1KG") is None

    def test_too_short_digit_run_rejected(self):
        assert extract_barcode("Order #12345") is None

    def test_too_long_digit_run_rejected(self):
        assert extract_barcode("Serial 123456789012345678") is None
