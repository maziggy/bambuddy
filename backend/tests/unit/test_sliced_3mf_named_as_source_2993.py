"""A sliced 3MF is sliced whatever it is called (#2993).

Downloading an archive that shows the green GCODE badge and re-importing it
produced, for some archives, a source-only project with no Print button. The
G-code was never lost -- the download serves the stored file byte for byte.
What differed was who was asked: the Archives card looked inside the zip, while
the library decided from the filename alone.

That splits on how the print reached the printer, which is why it looked
random. Bambu Studio's LAN send names a file ``Foo.gcode.3mf``; a per-plate
export or a cloud-dispatched print arrives as ``Foo.3mf``, G-code and all. Both
archive fine, both badge fine, and only the second one came back as a project.

These tests pin the two halves to one answer, and pin the escape hatch that
keeps ingest cheap: the file is opened only when the name has not already
settled it.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from backend.app.api.routes.library import classify_file_type
from backend.app.utils.threemf_tools import carries_gcode, names_carry_gcode

SLICED = ["3D/3dmodel.model", "Metadata/plate_3.gcode", "Metadata/plate_3.gcode.md5"]
SOURCE = ["3D/3dmodel.model", "Metadata/plate_1.png", "Metadata/project_settings.config"]


def _write_3mf(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"x")
    return path


class TestTheSharedAnswer:
    def test_plate_gcode_makes_it_sliced(self):
        assert names_carry_gcode(SLICED) is True

    def test_a_project_export_is_not(self):
        assert names_carry_gcode(SOURCE) is False

    def test_a_slicer_that_does_not_use_plate_naming_still_counts(self):
        """Deferring to default_plate_gcode_name rather than matching
        ``Metadata/plate_<n>.gcode`` is the point: a file that is executable on
        a printer must not be filed as a model because of where its G-code
        member sits."""
        assert names_carry_gcode(["3D/3dmodel.model", "output.gcode"]) is True

    def test_an_unreadable_file_reads_as_not_sliced(self, tmp_path):
        """The pre-existing behaviour for anything that cannot be opened. An
        ingest path must not fail on a truncated upload."""
        broken = tmp_path / "broken.3mf"
        broken.write_bytes(b"PK\x03\x04not-a-zip")

        assert carries_gcode(broken) is False
        assert carries_gcode(tmp_path / "absent.3mf") is False


class TestClassification:
    def test_the_reported_file(self, tmp_path):
        """The whole bug in one line: same bytes, name says project."""
        sliced = _write_3mf(tmp_path / "Labyrinth.3mf", SLICED)

        assert classify_file_type("Labyrinth.3mf") == "3mf"
        assert classify_file_type("Labyrinth.3mf", sliced) == "gcode.3mf"

    def test_a_genuine_project_stays_a_project(self, tmp_path):
        """The guard that keeps this from swallowing the model library: an
        unsliced 3MF must not gain a Print button."""
        source = _write_3mf(tmp_path / "Labyrinth.3mf", SOURCE)

        assert classify_file_type("Labyrinth.3mf", source) == "3mf"

    def test_a_name_that_already_says_sliced_needs_no_file(self):
        """Not a micro-optimisation: the upload path classifies before the
        bytes are anywhere, and the external scan runs over a mount. Neither
        may depend on the file being openable when the name is enough."""
        assert classify_file_type("Labyrinth.gcode.3mf", Path("/does/not/exist.3mf")) == "gcode.3mf"

    @pytest.mark.parametrize("filename", ["model.stl", "preview.png", "README", "model.gcode"])
    def test_nothing_else_is_sniffed(self, filename, tmp_path):
        """Only `.3mf` is ambiguous. Handing a path for anything else must not
        change its type or open the file."""
        before = classify_file_type(filename)

        assert classify_file_type(filename, _write_3mf(tmp_path / "z.3mf", SLICED)) == before


class TestTheTwoSidesAgree:
    def test_the_archives_badge_and_the_library_now_say_the_same_thing(self, tmp_path):
        """The card promised G-code and the File Manager denied it. Asserted
        against the archive endpoint's own expression, so this fails if that
        side is ever pointed back at a private copy of the rule."""
        from backend.app.api.routes.archives import names_carry_gcode as archives_predicate

        sliced = _write_3mf(tmp_path / "Labyrinth.3mf", SLICED)
        with zipfile.ZipFile(sliced) as zf:
            badge = archives_predicate(zf.namelist())

        assert badge is True
        assert classify_file_type("Labyrinth.3mf", sliced) == "gcode.3mf"
