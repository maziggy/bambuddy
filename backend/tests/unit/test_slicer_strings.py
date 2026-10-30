"""slicer_strings: slicer string-list options as a JSON array or an INI-style string (#3380)."""

import pytest

from backend.app.utils.slicer_strings import first_slicer_string, slicer_strings, unescape_strings_cstyle


def test_ini_string():
    assert slicer_strings('"Bambu Lab X1 Carbon 0.4 nozzle";"Bambu Lab H2C 0.4 nozzle"') == [
        "Bambu Lab X1 Carbon 0.4 nozzle",
        "Bambu Lab H2C 0.4 nozzle",
    ]
    assert slicer_strings("PLA;PETG") == ["PLA", "PETG"]
    assert slicer_strings('"Printer \\"A\\""') == ['Printer "A"']


def test_quoted_items_decode_like_orca():
    assert slicer_strings('"a\\nb";"c\\rd";"e\\\\f"') == ["a\nb", "c\rd", "e\\f"]
    assert slicer_strings(' \t"X1C" \t; "P1S"') == ["X1C", "P1S"]


def test_unquoted_items_are_read_as_is():
    assert slicer_strings("C:\\x") == ["C:\\x"]
    assert slicer_strings("Bambu Lab P2S 0.4 nozzle") == ["Bambu Lab P2S 0.4 nozzle"]


def test_json_array_items_are_kept_as_they_are():
    """A JSON array is never INI-encoded: a ``;``, ``\\`` or ``"`` in a name stays."""
    assert slicer_strings(["X1C", "", 5, "A1"]) == ["X1C", "A1"]
    assert slicer_strings(["My X1C; tuned", '"C:\\profiles\\X1C"']) == ["My X1C; tuned", '"C:\\profiles\\X1C"']


def test_the_slicer_parse_keeps_empty_items():
    """Per-extruder lists keep their positions."""
    assert unescape_strings_cstyle('"";"#FF0000"') == ["", "#FF0000"]
    assert unescape_strings_cstyle('""') == [""]
    assert unescape_strings_cstyle('"#FF0000";') == ["#FF0000", ""]
    assert unescape_strings_cstyle("") == []
    assert unescape_strings_cstyle('"X1C" P1S') is None


def test_first_item():
    assert first_slicer_string('"";" #FF0000 "') == "#FF0000"
    assert first_slicer_string(["PLA"]) == "PLA"
    assert first_slicer_string('""') is None


@pytest.mark.parametrize("raw", ['"X1C" P1S', '"X1C', '"X1C\\', '"X1C";"P1S'])
def test_string_orca_cannot_parse_is_no_data(raw):
    assert slicer_strings(raw) == []


@pytest.mark.parametrize("raw", [None, "", '""', "  ", ";", [], ["", "  "], {"a": 1}, 5])
def test_empty_and_malformed(raw):
    assert slicer_strings(raw) == []
