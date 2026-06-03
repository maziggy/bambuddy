"""Regression tests for the unified file_type classifier (#1600).

Pre-#1600 each ingest path classified `LibraryFile.file_type` differently
for the same on-disk file family — only the external-folder scan stored
`gcode.3mf` for sliced outputs, while upload / ZIP-extract / in-process
all stripped to the trailing extension and stored `3mf`. The frontend
had to accept both per #1543, the gcode-download endpoint only handled
`3mf`, and the external-scan thumbnail gate skipped `gcode.3mf` entirely
(#1600 itself). `classify_file_type` is now the single source of truth
across every ingest path + a one-shot DB migration backfills legacy rows.
"""

from __future__ import annotations

import pytest

from backend.app.api.routes.library import classify_file_type


@pytest.mark.parametrize(
    "filename, expected",
    [
        # Sliced output — compound extension preserved
        ("model.gcode.3mf", "gcode.3mf"),
        ("Multi.Plate.gcode.3mf", "gcode.3mf"),
        ("MIXED_CASE.GCODE.3MF", "gcode.3mf"),
        # Plain 3MF — unchanged
        ("model.3mf", "3mf"),
        ("model.3MF", "3mf"),
        # Raw gcode — not a sliced-3mf, classified as gcode (matches existing
        # gcode-thumbnail branch and the gcode-download endpoint).
        ("model.gcode", "gcode"),
        ("model.GCODE", "gcode"),
        # STL — used by the stats query, thumbnail backfill, and the
        # `file_type == "stl"` filter. Must not change.
        ("model.stl", "stl"),
        # Common image extensions used for thumbnails
        ("preview.png", "png"),
        ("preview.JPG", "jpg"),
        # Files without an extension classify as `unknown` so the downstream
        # `unknown` branches still see them.
        ("README", "unknown"),
        # Files that LOOK like sliced output but aren't — confidence guard.
        ("not.gcode.3mf.bak", "bak"),
    ],
)
def test_classify_file_type_returns_canonical_value(filename, expected):
    assert classify_file_type(filename) == expected


def test_gcode_3mf_classification_is_stable_across_extension_casing():
    """The migration's `LOWER(filename) LIKE '%.gcode.3mf'` predicate matches
    every casing the classifier accepts, so unify on a single canonical
    lowercase value."""
    for variant in ("foo.gcode.3mf", "foo.Gcode.3mf", "foo.gcode.3MF", "FOO.GCODE.3MF"):
        assert classify_file_type(variant) == "gcode.3mf"
