"""Matching a completion event to the queue row it belongs to (#2829).

``on_print_complete`` finds its row by printer and ``status='printing'`` alone,
so #b5a34b7ba added a check that the completion's subtask name agrees with the
file the row was dispatched with -- otherwise the printer's own calibration
runs close whoever's job happens to be printing.

The check compared the two names with plain equality, and the printer does not
echo the name back verbatim. Three days later two users had queues that would
not advance: the row stayed ``printing``, ``check_queue`` counts every such row
as a busy printer, and nothing anywhere ever closes it. Cancelling by hand was
the only way out.

The strings below are the real ones from the maintainer's own H2D, queue item
649, and from a support bundle showing the truncation case.
"""

import pytest

from backend.app.main import _normalise_subtask_name, _subtask_name_from_filename, _subtask_names_match

pytestmark = pytest.mark.unit


class TestTheReportedCase:
    def test_spaces_come_back_as_underscores(self):
        """Queue item 649, verbatim from the warning it logged twice."""
        dispatched = "H2D_Carbon_Filter_(V2)_Body & Solid Lid"
        reported = "H2D_Carbon_Filter_(V2)_Body_&_Solid_Lid"

        assert _subtask_names_match(dispatched, reported)

    def test_from_the_archive_filename_it_was_dispatched_with(self):
        """End to end from the stored filename, which is where the check
        actually gets its side of the comparison."""
        expected = _subtask_name_from_filename("H2D_Carbon_Filter_(V2)_Body & Solid Lid.gcode.3mf")

        assert _subtask_names_match(expected, "H2D_Carbon_Filter_(V2)_Body_&_Solid_Lid")


class TestTruncation:
    """The printer cuts long names and marks the cut with '...'.

    Observed at ~100 characters, but not a fixed count -- a name with multibyte
    characters came back at 98 -- so the marker is what is matched, not a
    length. Without this every print with a long name strands its row the same
    way the space substitution did.
    """

    def test_a_truncated_echo_matches_the_full_name(self):
        full = (
            "169356_204314.STEP + 169356_204314.STEP + 169356_204314.STEP + "
            "169356_204314.STEP + 169356_204314.STEP + 169356_204314.STEP"
        )
        truncated = (
            "169356_204314.STEP + 169356_204314.STEP + 169356_204314.STEP + 169356_204314.STEP + 169356_204314..."
        )

        assert _subtask_names_match(full, truncated)

    def test_a_truncated_name_on_the_archive_side_matches_too(self):
        """An archive whose filename was recorded from an earlier truncated
        echo carries the marker itself, so the cut can be on either side."""
        stored = "EXXXX-A001-Barriere Mundstück.STEP + EXXXX-A001-Barriere M..."
        reported = "EXXXX-A001-Barriere_Mundstück.STEP_+_EXXXX-A001-Barriere_Mundstück.STEP"

        assert _subtask_names_match(stored, reported)

    def test_truncation_does_not_match_a_different_print(self):
        """The prefix still has to agree -- '...' is not a wildcard."""
        assert not _subtask_names_match("Benchy_Calibration_Cube_Large", "Something_Else_Entirely...")


class TestItStillRefusesADifferentPrint:
    """The check has to keep doing its job, or #b5a34b7ba's bug comes back:
    a completion for another print closing a job that is still running.
    """

    def test_the_printers_own_calibration_run(self):
        """The second rejection on queue item 649, and a correct one."""
        assert not _subtask_names_match("H2D_Carbon_Filter_(V2)_Body & Solid Lid", "auto_pa_line_calib_mode")

    def test_an_unrelated_print(self):
        assert not _subtask_names_match("Benchy", "Calibration Cube")

    def test_a_name_that_merely_starts_the_same(self):
        assert not _subtask_names_match("Bracket_v1", "Bracket_v2")


class TestNormalisation:
    def test_case_is_ignored(self):
        assert _subtask_names_match("BENCHY BOAT", "benchy_boat")

    def test_surrounding_whitespace_is_ignored(self):
        assert _subtask_names_match("  Benchy  ", "Benchy")

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("A B", "a_b"),
            ("A_B", "a_b"),
            (" A  B ", "a__b"),
            ("Mundstück", "mundstück"),
        ],
    )
    def test_canonical_form(self, raw, expected):
        assert _normalise_subtask_name(raw) == expected

    def test_spaces_and_underscores_are_the_same_rule_the_3mf_lookup_uses(self):
        """The 3MF lookup has always built space-to-underscore variants of its
        candidates. The completion check growing its own comparison instead of
        reading the same rule is how the two came to disagree."""
        assert _normalise_subtask_name("My Model") == _normalise_subtask_name("My_Model")


class TestFilenameDerivation:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("Benchy.gcode.3mf", "Benchy"),
            ("Benchy.3mf", "Benchy"),
            ("My.Model.3mf", "My.Model"),
            ("/cache/Nested Path/Benchy.gcode.3mf", "Benchy"),
        ],
    )
    def test_extensions_come_off_and_nothing_else_does(self, filename, expected):
        assert _subtask_name_from_filename(filename) == expected
