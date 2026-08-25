"""Tests for main._format_hms_error_summary — the helper that turns MQTT hms_errors
into a human-readable PrintQueueItem.error_message on pre-print failures (#1111)."""


def _format(hms_errors):
    from backend.app.main import _format_hms_error_summary

    return _format_hms_error_summary(hms_errors)


def test_returns_none_for_empty_list():
    assert _format([]) is None
    assert _format(None or []) is None


def test_formats_known_nozzle_mismatch_code():
    """0500_4038 is the nozzle-size-mismatch code from the HMS table — the common
    trigger for issue #1111."""
    summary = _format([{"code": "0x4038", "attr": 0x05000000, "module": 0x5, "severity": 1}])
    assert summary is not None
    assert "0500_4038" in summary
    assert "nozzle diameter" in summary.lower()


def test_formats_unknown_code_as_bare_short_code():
    summary = _format([{"code": "0x9999", "attr": 0x99990000, "module": 0x99, "severity": 1}])
    assert summary == "[9999_9999]"


def test_joins_multiple_errors_with_semicolons():
    summary = _format(
        [
            {"code": "0x4038", "attr": 0x05000000, "module": 0x5, "severity": 1},
            {"code": "0x9999", "attr": 0x99990000, "module": 0x99, "severity": 1},
        ]
    )
    assert summary is not None
    assert "; " in summary
    assert summary.count("[") == 2


def test_tolerates_malformed_entry_and_skips_it():
    summary = _format(
        [
            {"code": "not-hex", "attr": "also-not-int"},
            {"code": "0x4038", "attr": 0x05000000, "module": 0x5, "severity": 1},
        ]
    )
    assert summary is not None
    assert "0500_4038" in summary


def test_all_malformed_returns_none():
    assert _format([{"code": "not-hex", "attr": "also-not-int"}]) is None


def test_masks_a_32_bit_code_into_a_four_digit_label():
    """An `hms[]` entry's code carries the alert level in its high 16 bits. The
    label used to be formatted from the unmasked value, producing "0500_3000A" —
    five digits in a group that has four, so it matched no catalogue key and was
    not a code anyone could look up either."""
    summary = _format([{"code": "0x3000a", "attr": 0x05000200, "module": 5, "severity": 2}])
    assert summary == "[0500_000A]"


def test_masking_lets_a_32_bit_code_resolve_its_description():
    """0500_4038 is the nozzle-size mismatch. Arriving as an `hms[]` entry with
    an alert-level group, it went undescribed purely because of the formatting
    above; now it reads the same as when it arrives via print_error."""
    summary = _format([{"code": "0x00024038", "attr": 0x05000200, "module": 5, "severity": 2}])
    assert summary is not None
    assert summary.startswith("[0500_4038] ")
    assert "nozzle diameter" in summary.lower()


def test_accepts_an_integer_code():
    """`_hms_short_code` takes both shapes; the raw MQTT payload carries ints."""
    assert _format([{"code": 0x4038, "attr": 0x05000000, "module": 5, "severity": 1}]).startswith("[0500_4038] ")
