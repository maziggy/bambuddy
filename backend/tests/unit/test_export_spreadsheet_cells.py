"""Archive and statistics exports show typed text as it was typed in a spreadsheet.

Print names, notes, tags, project names and failure reasons are typed by
people, and a spreadsheet reads a cell that starts like a formula as one. In
CSV such a cell gets a leading quote; in Excel it is stored as text. Numbers
and empty cells are written as they are.
"""

from __future__ import annotations

import csv
import io

import pytest
from openpyxl import load_workbook

from backend.app.services.export import ExportService, _spreadsheet_text

LINK = '=HYPERLINK("https://elsewhere.example","open")'


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Benchy", "Benchy"),
        ("a = b", "a = b"),
        (LINK, "'" + LINK),
        ("+1 extra", "'+1 extra"),
        ("-x", "'-x"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("  =1+1", "'  =1+1"),
        ("\t=1+1", "'\t=1+1"),
        ("\nnote", "'\nnote"),
        ("＝1+1", "'＝1+1"),
        ("", ""),
        (None, None),
        (-5, -5),
        (2.5, 2.5),
    ],
)
def test_cell_values(value, expected):
    assert _spreadsheet_text(value) == expected


def _service() -> ExportService:
    return ExportService(db=None)  # type: ignore[arg-type]


def test_archive_csv_keeps_typed_text_as_text():
    data = _service()._generate_csv(["print_name", "notes", "cost"], [["Benchy", LINK, -1.5]])

    rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))

    assert rows[1] == ["Benchy", "'" + LINK, "-1.5"]


def test_stats_csv_keeps_typed_text_as_text():
    data = _service()._generate_csv_simple([["Failures by Reason", ""], ["+spaghetti", 3]])

    rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))

    assert rows == [["Failures by Reason", ""], ["'+spaghetti", "3"]]


def test_archive_xlsx_stores_typed_text_as_text():
    data = _service()._generate_xlsx(["print_name", "notes", "cost"], [["Benchy", LINK, -1.5]], ["a", "b", "c"])

    sheet = load_workbook(io.BytesIO(data)).active
    assert sheet is not None

    assert (sheet["B2"].value, sheet["B2"].data_type) == (LINK, "s")
    assert sheet["C2"].value == -1.5


def test_stats_xlsx_stores_typed_text_as_text():
    data = _service()._generate_xlsx_simple([["Failures by Reason", ""], [LINK, 3]])

    sheet = load_workbook(io.BytesIO(data)).active
    assert sheet is not None

    assert (sheet["A2"].value, sheet["A2"].data_type) == (LINK, "s")
    assert sheet["B2"].value == 3
