"""Tests for resolve_display_stem — Bambu Studio filename normalisation (#1152).

Bambu Studio's "Send to printer" dialog typically writes ``Plate_1.gcode.3mf``
(a sliced gcode payload wrapped in a 3MF container). ``Path(name).stem`` only
strips the last suffix and leaves ``Plate_1.gcode``, which then surfaces in
the archive UI as a confusing ``Plate_1.gcode`` rather than ``Plate_1``.

Pin the canonicalisation rules so a future refactor can't silently regress
this path. We don't need a dedicated test for ``archive_print``'s consumption
of the helper — the existing test suite covers that flow end-to-end via the
integration tests and a behaviour change there would surface as a different
``archive.print_name`` value.
"""

import pytest

from backend.app.services.archive import resolve_display_stem


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # The headline case: Bambu Studio's default name for a sliced 3MF.
        ("Plate_1.gcode.3mf", "Plate_1"),
        # User-renamed file with the double-suffix pattern.
        ("MyAwesomeBenchy.gcode.3mf", "MyAwesomeBenchy"),
        # Plain .3mf (already-clean export from Bambu Studio's Save As).
        ("Benchy.3mf", "Benchy"),
        # Standalone gcode upload — rare but supported.
        ("standalone.gcode", "standalone"),
        # Mixed-case suffix — many slicers / OSes preserve user-typed case.
        ("UPPERCASE.GCODE.3MF", "UPPERCASE"),
        ("mixed.GCode.3mf", "mixed"),
        # Names that contain dots in the middle should keep them.
        ("my.cool.model.gcode.3mf", "my.cool.model"),
        ("v1.2.3-prototype.3mf", "v1.2.3-prototype"),
        # No recognised suffix → fall through to Path.stem.
        ("Cura_export.zip", "Cura_export"),
        ("README.md", "README"),
        # Edge: just the suffix with nothing in front. Strip honestly — the
        # caller is responsible for sanity-checking empty stems.
        (".gcode.3mf", ""),
        (".3mf", ""),
        # Path components must not leak in. The helper takes a filename, but
        # callers occasionally pass a full path string.
        ("/some/dir/Plate_1.gcode.3mf", "Plate_1"),
        ("subdir/MyModel.3mf", "MyModel"),
    ],
)
def test_resolve_display_stem(filename: str, expected: str) -> None:
    assert resolve_display_stem(filename) == expected
