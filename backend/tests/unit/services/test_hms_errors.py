"""Tests for HMS error code translations."""

from backend.app.services.hms_errors import HMS_ERROR_DESCRIPTIONS, describe_fault, get_error_description


class TestHMSErrorDescriptions:
    """Tests for the HMS error descriptions dictionary."""

    def test_dictionary_is_not_empty(self):
        """Verify the error descriptions dictionary has entries."""
        assert len(HMS_ERROR_DESCRIPTIONS) > 0

    def test_dictionary_has_expected_count(self):
        """Verify we have the expected number of error codes."""
        # Should have 853 error codes from the frontend
        assert len(HMS_ERROR_DESCRIPTIONS) == 853

    def test_all_keys_are_valid_format(self):
        """Verify all keys follow the XXXX_YYYY format."""
        import re

        pattern = re.compile(r"^[0-9A-F]{4}_[0-9A-F]{4}$")
        for code in HMS_ERROR_DESCRIPTIONS:
            assert pattern.match(code), f"Invalid error code format: {code}"

    def test_all_values_are_non_empty_strings(self):
        """Verify all descriptions are non-empty strings."""
        for code, description in HMS_ERROR_DESCRIPTIONS.items():
            assert isinstance(description, str), f"Description for {code} is not a string"
            assert len(description) > 0, f"Description for {code} is empty"


class TestGetErrorDescription:
    """Tests for the get_error_description function."""

    def test_returns_description_for_known_code(self):
        """Verify known error codes return their descriptions."""
        # 0300_400C = "The task was canceled."
        result = get_error_description("0300_400C")
        assert result == "The task was canceled."

    def test_returns_description_for_ams_error(self):
        """Verify AMS error codes return their descriptions."""
        # 0700_8010 = AMS assist motor overloaded
        result = get_error_description("0700_8010")
        assert "AMS assist motor" in result

    def test_returns_none_for_unknown_code(self):
        """Verify unknown error codes return None."""
        result = get_error_description("XXXX_YYYY")
        assert result is None

    def test_handles_lowercase_input(self):
        """Verify function handles lowercase input."""
        result = get_error_description("0300_400c")
        assert result == "The task was canceled."

    def test_handles_mixed_case_input(self):
        """Verify function handles mixed case input."""
        result = get_error_description("0300_400C")
        assert result == "The task was canceled."

    def test_common_error_codes_have_descriptions(self):
        """Verify common error codes have descriptions."""
        common_codes = [
            "0300_4000",  # Z axis homing failed
            "0300_4006",  # Nozzle clogged
            "0300_8004",  # Filament ran out
            "0500_4001",  # Failed to connect to Bambu Cloud
            "0700_8010",  # AMS assist motor overloaded
        ]
        for code in common_codes:
            result = get_error_description(code)
            assert result is not None, f"Missing description for common code: {code}"


class TestDescribeFault:
    """`describe_fault` maps a fault's canonical `full_code` onto the catalogue,
    so every surface that reports a fault resolves it the same way (#2926)."""

    def test_resolves_an_eight_char_print_error_code(self):
        """The parser derives full_code and the catalogue key from the same
        32-bit value, so the split is exact rather than a guess."""
        assert describe_fault("03008004") == "Filament ran out. Please load new filament."

    def test_resolves_regardless_of_case(self):
        """Firmware-facing code is uppercase, but a client echoing a value back
        from its own store may not be."""
        assert describe_fault("0300400c") == "The task was canceled."

    def test_tolerates_surrounding_whitespace(self):
        assert describe_fault("  03008004  ") == "Filament ran out. Please load new filament."

    def test_returns_none_for_an_hms_code_outside_the_catalogue(self):
        """A real P2S fault from #2728. Neither the whole 16-char key nor its
        G1_G4 collapse ("0500_000A") is in the catalogue — no catalogue key has
        an error group below 0x4000, and this family's is 0x000A."""
        assert describe_fault("050002000003000A") is None

    def test_collapses_a_sixteen_char_code_to_its_g1_g4_short_key(self):
        """Lossy, and kept deliberately: this is how the notification path, the
        queue's failure-reason helper and the frontend modal have always
        resolved `hms[]` faults, and it resolves real ones. Refusing would stop
        describing faults that are described today (see the module docstring)."""
        key = next(iter(HMS_ERROR_DESCRIPTIONS))  # e.g. "0300_4000"
        module, error = key.split("_")
        forced = f"{module}02000003{error}"  # four 4-hex groups; G1 and G4 are the key
        assert len(forced) == 16
        assert describe_fault(forced) == HMS_ERROR_DESCRIPTIONS[key]

    def test_prefers_the_whole_sixteen_char_key_over_the_collapse(self):
        """The full identifier is lossless, so it wins when the catalogue has
        both. No 16-char keys ship today; this pins the order for when they do."""
        key = next(iter(HMS_ERROR_DESCRIPTIONS))
        module, error = key.split("_")
        forced = f"{module}02000003{error}"
        HMS_ERROR_DESCRIPTIONS[forced] = "specific variant"
        try:
            assert describe_fault(forced) == "specific variant"
        finally:
            del HMS_ERROR_DESCRIPTIONS[forced]

    def test_matches_the_derivation_it_replaced(
        self,
    ):
        """The regression guard for the consolidation: for every fault shape the
        codebase can produce, `describe_fault` returns exactly what the
        attr/code short-code lookup in the notification path returned before it.
        Covers both families and all three alert levels a real `hms[]` code
        carries — a divergence here means notifications silently stop firing for
        faults that used to raise them."""
        for key, expected in HMS_ERROR_DESCRIPTIONS.items():
            module, error = int(key[:4], 16), int(key[5:], 16)

            # print_error: attr is the whole 32-bit value, code its low half.
            print_error = (module << 16) | error
            assert describe_fault(f"{print_error:08X}") == expected

            # hms[]: attr is groups 1-2, code is groups 3-4 (alert level + id).
            for alert_level in (0x0000, 0x0002, 0x0003):
                attr = (module << 16) | 0x0200
                code = (alert_level << 16) | error
                legacy = get_error_description(f"{(attr >> 16) & 0xFFFF:04X}_{code & 0xFFFF:04X}")
                assert describe_fault(f"{attr:08X}{code:08X}") == legacy == expected

    def test_returns_none_for_an_unknown_eight_char_code(self):
        assert describe_fault("99999999") is None

    def test_returns_none_for_empty_or_missing(self):
        """The dataclass default is "" and the field is optional on the wire."""
        assert describe_fault("") is None
        assert describe_fault(None) is None

    def test_returns_none_for_a_malformed_length(self):
        """Neither 8 nor 16 chars — no shape to interpret, so no guess."""
        assert describe_fault("0300") is None
        assert describe_fault("030080040") is None

    def test_agrees_with_the_short_code_lookup_for_print_error_codes(self):
        """Pins the equivalence the consolidation rests on: for every 8-char
        code the catalogue covers, describe_fault returns exactly what the
        pre-existing short-code lookup did."""
        for key, expected in HMS_ERROR_DESCRIPTIONS.items():
            assert describe_fault(key.replace("_", "")) == expected
            assert get_error_description(key) == expected
