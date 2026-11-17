"""Free text in the maintenance log CSV stays text in a spreadsheet.

Comments, printer, task and user names are typed by people and may be opened
in Excel or LibreOffice by someone else; a cell a spreadsheet would run as a
formula gets a leading quote. Old comments copied in on upgrade were never
trimmed, so leading whitespace and line breaks are looked past too.
"""

from __future__ import annotations

import pytest

from backend.app.services.export import _spreadsheet_text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain note", "plain note"),
        ("a = b", "a = b"),
        ("=1+1", "'=1+1"),
        ("+49 123", "'+49 123"),
        ("-0.2 mm offset", "'-0.2 mm offset"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("  =1+1", "'  =1+1"),
        ("\n=1+1", "'\n=1+1"),
        ("\r\nnote", "'\r\nnote"),
        ("\tnote", "'\tnote"),
        ("＝HYPERLINK(1)", "'＝HYPERLINK(1)"),
        ("＠SUM(A1)", "'＠SUM(A1)"),
        ("", ""),
        (None, None),  # written as an empty cell by the csv writer
    ],
)
def test_a_formula_like_cell_gets_a_leading_quote(value, expected):
    assert _spreadsheet_text(value) == expected
