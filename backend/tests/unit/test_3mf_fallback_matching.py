"""A no-3MF archive must not borrow a stranger's 3MF (#2843).

H2-series and P2S firmware keeps a slicer-sent file on internal eMMC, which
Bambuddy cannot read, so the print becomes an archive with no file and its
``filename`` stays the path the printer is executing:
``/data/Metadata/plate_1.gcode``.

Measured on the maintainer's H2D, 2026-08-17. The stem of that path is
``plate_1``, the old matcher searched ``filename ILIKE '%plate_1.%'``, and
``lid_plate_1.gcode.3mf`` matched — so a 1.6 g Cube was costed from a 207 g
four-colour ABS print:

    [UsageTracker] 3MF fallback: found previous archive 287 file for archive 345
    [UsageTracker] 3MF: slot_id=2 -> global_tray=4 -> AMS1-T0 (used_g=204.9 ...)

Every Bambu print has a ``plate_N``, so this was not a near-miss between similar
names — it was a name that matches everything.
"""

import pytest

from backend.app.services.usage_tracker import (
    _like_escape,
    _stem_matches,
    _threemf_search_stem,
)


class TestSearchStem:
    def test_a_real_filename_still_wins(self):
        """Unchanged for every archive that has a 3MF of its own."""
        assert _threemf_search_stem("Cube.gcode.3mf", "Cube") == "Cube"
        assert _threemf_search_stem("benchy.3mf", None) == "benchy"

    def test_the_plate_path_is_refused(self):
        """The reported case: fall through to the model name instead."""
        assert _threemf_search_stem("/data/Metadata/plate_1.gcode", "Cube") == "Cube"

    @pytest.mark.parametrize("plate", ["plate_1", "plate_12", "plate1", "PLATE_3"])
    def test_every_plate_spelling_is_refused(self, plate):
        assert _threemf_search_stem(f"/data/Metadata/{plate}.gcode", None) is None

    def test_no_usable_name_matches_nothing(self):
        """Better to skip tracking than to charge a print for another model."""
        assert _threemf_search_stem("/data/Metadata/plate_1.gcode", None) is None
        assert _threemf_search_stem(None, None) is None
        assert _threemf_search_stem("", "") is None

    def test_a_model_named_after_a_plate_survives(self):
        """`lid_plate_1` names a model — only a bare plate stem is generic."""
        assert _threemf_search_stem("lid_plate_1.gcode.3mf", None) == "lid_plate_1"

    def test_whitespace_before_the_extension_is_preserved(self):
        """A real archive on the maintainer's install is named
        "…Face Down .gcode.3mf". Trimming the stem to "…Face Down" would stop it
        matching the very file it was derived from."""
        assert _threemf_search_stem("Steelers 6 Color Face Down .gcode.3mf", None) == "Steelers 6 Color Face Down "

    def test_surrounding_whitespace_is_still_ignored(self):
        assert _threemf_search_stem("  Cube.3mf  ", None) == "Cube"


class TestLikeEscaping:
    def test_underscores_are_literal(self):
        """``_`` is a single-character LIKE wildcard, and model names are full
        of them — unescaped, `Cube_v1` also matches `CubeXv1`."""
        assert _like_escape("Cube_v1") == "Cube\\_v1"

    def test_percent_and_backslash(self):
        assert _like_escape("100%_scale") == "100\\%\\_scale"
        assert _like_escape("a\\b") == "a\\\\b"


class TestStemMatchesAtABoundary:
    """The SQL the matcher builds, checked by rendering it."""

    @staticmethod
    def _patterns(stem):
        from backend.app.models.archive import PrintArchive

        clause = _stem_matches(PrintArchive.filename, stem)
        return str(clause.compile(compile_kwargs={"literal_binds": True}))

    def test_it_no_longer_matches_a_suffix_of_a_longer_name(self):
        """The whole bug in one assertion: `%plate_1.%` is gone."""
        assert "%plate_1.%" not in self._patterns("plate_1")

    def test_it_anchors_the_basename(self):
        sql = self._patterns("Cube")
        assert "Cube.%" in sql
        assert "%/Cube.%" in sql

    def test_it_escapes_the_stem(self):
        assert "Cube\\_v1.%" in self._patterns("Cube_v1")


@pytest.mark.asyncio
async def test_the_h2d_collision_no_longer_resolves(db_session, tmp_path):
    """End to end against the real rows: a Cube on eMMC must not resolve to
    `lid_plate_1.gcode.3mf`."""
    from backend.app.models.archive import PrintArchive
    from backend.app.services.usage_tracker import _resolve_3mf_fallback

    donor_file = tmp_path / "archive" / "1" / "lid_plate_1.gcode.3mf"
    donor_file.parent.mkdir(parents=True)
    donor_file.write_bytes(b"PK\x03\x04not-really-a-3mf")

    donor = PrintArchive(
        printer_id=1,
        print_name="lid_plate_1",
        filename="lid_plate_1.gcode.3mf",
        file_path="archive/1/lid_plate_1.gcode.3mf",
        file_size=1,
        status="completed",
    )
    # The eMMC print: no file of its own, filename is the plate path.
    orphan = PrintArchive(
        printer_id=1,
        print_name="Cube",
        filename="/data/Metadata/plate_1.gcode",
        file_path="",
        file_size=0,
        status="completed",
    )
    db_session.add_all([donor, orphan])
    await db_session.commit()

    assert await _resolve_3mf_fallback(orphan, db_session, tmp_path) is None


@pytest.mark.asyncio
async def test_a_genuine_same_model_reprint_still_resolves(db_session, tmp_path):
    """The fallback's actual purpose must survive the fix."""
    from backend.app.models.archive import PrintArchive
    from backend.app.services.usage_tracker import _resolve_3mf_fallback

    donor_file = tmp_path / "archive" / "1" / "Cube.gcode.3mf"
    donor_file.parent.mkdir(parents=True)
    donor_file.write_bytes(b"PK\x03\x04not-really-a-3mf")

    donor = PrintArchive(
        printer_id=1,
        print_name="Cube",
        filename="Cube.gcode.3mf",
        file_path="archive/1/Cube.gcode.3mf",
        file_size=1,
        status="completed",
    )
    orphan = PrintArchive(
        printer_id=1,
        print_name="Cube",
        filename="/data/Metadata/plate_1.gcode",
        file_path="",
        file_size=0,
        status="completed",
    )
    db_session.add_all([donor, orphan])
    await db_session.commit()

    assert await _resolve_3mf_fallback(orphan, db_session, tmp_path) == donor_file
