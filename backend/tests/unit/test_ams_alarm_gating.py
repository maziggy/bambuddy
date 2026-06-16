"""Tests for the empty-AMS alarm gate (#1619).

Empty AMS units still emit humidity/temperature sensor readings, but those
readings are ambient and not actionable — there's no filament to dry. Without
the gate every empty AMS spammed an hourly alarm. ``_ams_has_filament``
inspects the firmware-reported ``tray_exist_bits`` bitmap (fallback: ``tray``
array's ``tray_type`` strings) so the alarm dispatch in ``record_ams_history``
can skip empty units while still alarming on loaded ones in the same printer.
"""

from backend.app.main import _ams_has_filament


class TestAmsHasFilament:
    def test_tray_exist_bits_zero_means_empty(self):
        assert _ams_has_filament({"tray_exist_bits": "0"}) is False
        # Real firmware sometimes pads with extra zeros or prefixes; all
        # parseable forms of zero should resolve to "empty".
        assert _ams_has_filament({"tray_exist_bits": "00"}) is False
        assert _ams_has_filament({"tray_exist_bits": "0x0"}) is False

    def test_tray_exist_bits_nonzero_means_loaded(self):
        # Single tray loaded — e.g. AMS-Lite or AMS-HT.
        assert _ams_has_filament({"tray_exist_bits": "1"}) is True
        # Four-slot AMS with all slots full (bitmap 0xf == 0b1111).
        assert _ams_has_filament({"tray_exist_bits": "f"}) is True
        # Mixed — 0xa == 0b1010, two slots loaded.
        assert _ams_has_filament({"tray_exist_bits": "a"}) is True
        # The exact bitmap seen in #1622 / #1602 logs.
        assert _ams_has_filament({"tray_exist_bits": "ed"}) is True

    def test_falls_back_to_tray_array_when_bits_missing(self):
        # Empty tray_type strings across the whole tray array → empty AMS.
        ams_empty = {
            "tray": [
                {"id": 0, "tray_type": ""},
                {"id": 1, "tray_type": ""},
            ]
        }
        assert _ams_has_filament(ams_empty) is False
        # Any non-empty tray_type → loaded AMS.
        ams_loaded = {
            "tray": [
                {"id": 0, "tray_type": ""},
                {"id": 1, "tray_type": "PLA"},
            ]
        }
        assert _ams_has_filament(ams_loaded) is True

    def test_missing_both_signals_returns_false(self):
        # No tray_exist_bits AND no tray array — early-pushall shape; we
        # treat it as "no info → don't alarm" rather than guessing loaded.
        assert _ams_has_filament({}) is False

    def test_unparseable_bitmap_falls_back_to_tray_array(self):
        # Garbage in tray_exist_bits — must not raise and must fall through
        # to the tray array check.
        loaded = {"tray_exist_bits": "garbage", "tray": [{"id": 0, "tray_type": "PETG"}]}
        assert _ams_has_filament(loaded) is True
        empty = {"tray_exist_bits": "garbage", "tray": []}
        assert _ams_has_filament(empty) is False

    def test_empty_bits_string_falls_back_to_tray_array(self):
        # Some pre-handshake pushall shapes set the field but leave it blank.
        loaded = {"tray_exist_bits": "", "tray": [{"id": 0, "tray_type": "ABS"}]}
        assert _ams_has_filament(loaded) is True

    def test_whitespace_tray_type_is_not_loaded(self):
        # A tray_type that's all whitespace doesn't count as a real material.
        assert _ams_has_filament({"tray": [{"id": 0, "tray_type": "   "}]}) is False

    def test_non_dict_tray_entries_are_skipped(self):
        # Defensive: malformed tray array shouldn't crash the helper.
        assert _ams_has_filament({"tray": [None, "junk", 42]}) is False

    def test_non_string_bits_falls_back(self):
        # Some MQTT shapes send tray_exist_bits as int; we only parse strings,
        # so an int falls through to the tray array.
        loaded = {"tray_exist_bits": 0xED, "tray": [{"id": 0, "tray_type": "PLA"}]}
        assert _ams_has_filament(loaded) is True
        empty_int = {"tray_exist_bits": 0xED}  # no tray array, int ignored
        assert _ams_has_filament(empty_int) is False
