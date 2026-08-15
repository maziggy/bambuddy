"""A display name is not a filename (#2832).

A print's display name comes from the ``print_name`` embedded in the 3MF, which
is whatever the model's author typed. The slice-to-archive sink built both the
output directory and the output file straight out of it, so the MakerWorld title
"Planter Pot with Drip Tray, 12 cm / 5 inches" put a path separator in the
middle of a filename:

    .../20260814_090539_Planter Pot with Drip Tray, 12 cm / 5 inches_sliced/
        Planter Pot with Drip Tray, 12 cm / 5 inches.gcode.3mf

``mkdir(parents=True)`` created the two directories that first join implies --
which is why the reporter could ``cd`` into it -- and the write then failed on a
third level nobody had made. The same arithmetic with ``..`` in the name steers
the write out of the archive directory altogether.
"""

import pytest

from backend.app.utils.filename import MAX_FILENAME_BYTES, clean_display_name, safe_path_component

pytestmark = pytest.mark.unit

REPORTED = "Planter Pot with Drip Tray, 12 cm / 5 inches"


class TestTheReportedName:
    def test_the_slash_stops_being_a_separator(self):
        assert "/" not in safe_path_component(REPORTED, fallback="x")

    def test_and_the_name_is_still_recognisable(self):
        """Replaced rather than dropped: this string names the folder the user
        browses to, so it should still read like the model's title."""
        assert safe_path_component(REPORTED, fallback="x") == "Planter Pot with Drip Tray, 12 cm - 5 inches"

    def test_the_comma_is_left_alone(self):
        """Only what the filesystem cannot take is touched. A comma is fine,
        and the reporter's title has one."""
        assert "," in safe_path_component(REPORTED, fallback="x")


class TestItCannotEscape:
    @pytest.mark.parametrize(
        "name",
        [
            "../../../../etc/cron.d/x",
            "..",
            "../..",
            "/etc/passwd",
            "..\\..\\windows\\system32",
            "a/../../b",
        ],
    )
    def test_no_separator_survives(self, name):
        """One component in, one component out. Nothing that follows can
        rejoin a directory it was not given."""
        result = safe_path_component(name, fallback="fallback")

        assert "/" not in result
        assert "\\" not in result
        assert result not in (".", "..")

    def test_a_name_that_reduces_to_nothing_falls_back(self):
        """An empty component would make the join collapse onto the parent."""
        assert safe_path_component("", fallback="archive_42") == "archive_42"
        assert safe_path_component("   ", fallback="archive_42") == "archive_42"
        assert safe_path_component("...", fallback="archive_42") == "archive_42"

    def test_a_name_of_pure_separators_reduces_to_a_usable_component(self):
        """Not the fallback -- the separators become ordinary characters, which
        is already a single valid component. What matters is that it is neither
        empty nor a relative path."""
        result = safe_path_component("/..", fallback="archive_42")

        assert result and "/" not in result and result not in (".", "..")


class TestWindowsReservedCharacters:
    """A Windows install fails on the same shape for a different set. Bambuddy
    ships a Windows installer, and "Model: v2" is an ordinary title."""

    @pytest.mark.parametrize("char", list('<>:"|?*'))
    def test_reserved_punctuation_is_replaced(self, char):
        assert char not in safe_path_component(f"Model{char}v2", fallback="x")

    def test_control_characters_go_too(self):
        assert safe_path_component("Model\x00\x1bv2", fallback="x") == "Model--v2"

    def test_trailing_dots_and_spaces_go(self):
        """Windows cannot create either, and a trailing dot is how ".." would
        sneak back in."""
        assert safe_path_component("Model v2. ", fallback="x") == "Model v2"


class TestLengthBudget:
    def test_a_long_name_is_capped(self):
        assert len(safe_path_component("A" * 400, fallback="x").encode()) == MAX_FILENAME_BYTES

    def test_the_caller_can_reserve_room_for_its_affixes(self):
        """The archive sink wraps the result in a timestamp and "_sliced", so
        the composed component would otherwise overrun the cap it just met."""
        result = safe_path_component("A" * 400, fallback="x", max_bytes=MAX_FILENAME_BYTES - 23)

        assert len(f"20260814_090539_{result}_sliced".encode()) <= MAX_FILENAME_BYTES

    def test_a_multibyte_name_is_not_cut_mid_character(self):
        """Truncating UTF-8 on a byte boundary can leave half a character,
        which does not decode."""
        result = safe_path_component("ü" * 200, fallback="x")

        assert len(result.encode()) <= MAX_FILENAME_BYTES
        result.encode().decode("utf-8")  # must not raise


class TestDisplayNamesKeepTheirPunctuation:
    """The name in the database is a title, not a path. Refusing the slash
    would reject the very name this issue is about."""

    def test_the_reported_title_survives_intact(self):
        assert clean_display_name(REPORTED) == REPORTED

    def test_control_characters_are_removed(self):
        assert clean_display_name("Piggy\x00 bank\x07") == "Piggy bank"

    def test_surrounding_whitespace_goes(self):
        assert clean_display_name("  Benchy  ") == "Benchy"

    def test_an_empty_name_becomes_none(self):
        """Rather than an empty string, which would read as a name of nothing
        and defeat every ``print_name or fallback`` in the codebase."""
        assert clean_display_name("   ") is None
        assert clean_display_name("\x00") is None

    def test_none_stays_none(self):
        assert clean_display_name(None) is None

    @pytest.mark.parametrize("value", [123, ["a"], {"x": 1}, True])
    def test_a_non_string_is_handed_back_for_the_schema_to_reject(self, value):
        """It runs in front of the field's own type check. Iterating the value
        here would turn ["a"] into the name "a" and answer a bare int with a
        500, where both should be a 422."""
        assert clean_display_name(value) is value

    @pytest.mark.parametrize("value", [123, ["a"], {"x": 1}, True])
    def test_and_the_schema_does_reject_it(self, value):
        from pydantic import ValidationError

        from backend.app.schemas.archive import ArchiveUpdate

        with pytest.raises(ValidationError) as excinfo:
            ArchiveUpdate(print_name=value)

        assert excinfo.value.errors()[0]["type"] == "string_type"

    def test_a_title_with_punctuation_reaches_the_database_intact(self):
        from backend.app.schemas.archive import ArchiveUpdate

        assert ArchiveUpdate(print_name=REPORTED).print_name == REPORTED

    def test_an_embedded_name_of_only_whitespace_falls_back_to_the_filename(self):
        """Cleaning has to happen before the fallback, not after it: "   " is
        truthy, so cleaning afterwards would leave the archive with no name at
        all instead of the filename it used to get."""
        embedded, stem = "   ", "Benchy"

        assert (clean_display_name(embedded) or clean_display_name(stem)) == "Benchy"
