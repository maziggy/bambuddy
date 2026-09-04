"""``_effective_plate_id``, the plate resolved once in ``_start_print`` and
reused at every dispatch call site: G-code injection, rack-plan lookup,
slot-extruder lookup, and the actual print command (#2947).

A single-plate 3MF exported out of a multi-plate project keeps its ORIGINAL
plate number: cutting the right-side plate out of a two-plate project leaves
a file whose only G-code member is ``Metadata/plate_2.gcode``, never
``plate_1.gcode``. A caller that hardcodes ``item.plate_id or 1`` sends a
print command for a plate the archive doesn't hold. The printer accepts the
command, can't find the G-code, and wedges until power-cycled.

The four call sites agreed on this fallback only by accident before this fix
— see the function's own docstring for how a plate mismatch could sneak past
G-code injection specifically.
"""

import logging
import zipfile

from backend.app.services.print_scheduler import _effective_plate_id


def _write_gcode_members(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, "")
    return path


class TestEffectivePlateId:
    def test_explicit_plate_id_wins_even_when_the_file_cannot_be_read(self, tmp_path):
        # A queue item's own choice always wins, and an archive that can't be
        # opened must not stop it from being returned.
        assert _effective_plate_id(3, tmp_path / "does-not-exist.3mf") == 3

    def test_plate_id_zero_is_treated_as_unset_and_resolved_from_the_archive(self, tmp_path):
        # Nothing validates the field as positive (no ge= on any plate_id in
        # schemas/print_queue.py) and the rest of the queue code reads it
        # truthily, so a 0 means "not set" here too. Returning it would
        # dispatch Metadata/plate_0.gcode, the exact wedge this fixes.
        path = _write_gcode_members(tmp_path / "right.gcode.3mf", ["Metadata/plate_2.gcode"])
        assert _effective_plate_id(0, path) == 2

    def test_negative_plate_id_is_treated_as_unset_too(self, tmp_path):
        path = _write_gcode_members(tmp_path / "right.gcode.3mf", ["Metadata/plate_2.gcode"])
        assert _effective_plate_id(-1, path) == 2

    def test_single_plate_file_numbered_two_resolves_to_two(self, tmp_path):
        # The exact shape of the wedged printer in #2947: one G-code member,
        # keeping its original (non-1) plate number.
        path = _write_gcode_members(tmp_path / "right.gcode.3mf", ["Metadata/plate_2.gcode"])
        assert _effective_plate_id(None, path) == 2

    def test_single_plate_file_numbered_one_resolves_to_one(self, tmp_path):
        path = _write_gcode_members(tmp_path / "left.gcode.3mf", ["Metadata/plate_1.gcode"])
        assert _effective_plate_id(None, path) == 1

    def test_reversed_zip_order_picks_the_lowest_plate_not_first_member(self, tmp_path):
        # Bambu Studio does not write plates in zip order: a real two-plate
        # export stores plate_2.gcode ahead of plate_1.gcode.
        path = _write_gcode_members(
            tmp_path / "reversed.gcode.3mf",
            ["Metadata/plate_2.gcode", "Metadata/plate_1.gcode"],
        )
        assert _effective_plate_id(None, path) == 1

    def test_gcode_md5_sidecar_is_not_mistaken_for_the_toolpath(self, tmp_path):
        path = _write_gcode_members(
            tmp_path / "sidecar.gcode.3mf",
            ["Metadata/plate_1.gcode.md5", "Metadata/plate_2.gcode", "Metadata/plate_1.gcode"],
        )
        assert _effective_plate_id(None, path) == 1

    def test_unsliced_file_falls_back_to_plate_one(self, tmp_path):
        path = _write_gcode_members(tmp_path / "unsliced.3mf", ["3D/3dmodel.model"])
        assert _effective_plate_id(None, path) == 1

    def test_gcode_member_without_plate_naming_falls_back_to_plate_one(self, tmp_path):
        # default_plate_number returns None here (no number to dispatch);
        # this is the one place that turns that None into the actual
        # fallback a print command needs.
        path = _write_gcode_members(tmp_path / "custom.gcode.3mf", ["Metadata/print.gcode"])
        assert _effective_plate_id(None, path) == 1

    def test_unreadable_file_falls_back_to_plate_one(self, tmp_path):
        path = tmp_path / "broken.3mf"
        path.write_bytes(b"not a zip")
        assert _effective_plate_id(None, path) == 1

    def test_missing_file_falls_back_to_plate_one(self, tmp_path):
        assert _effective_plate_id(None, tmp_path / "does-not-exist.3mf") == 1


class TestWhatGetsLogged:
    """The fallbacks are silent recoveries from something that is wrong with
    the file, so each one has to leave a trace naming the archive. None of
    them may raise: this runs on a dispatch that is otherwise fine.
    """

    def test_an_unreadable_archive_is_logged_with_the_path_and_the_reason(self, tmp_path, caplog):
        path = tmp_path / "broken.3mf"
        path.write_bytes(b"not a zip")

        with caplog.at_level(logging.WARNING, logger="backend.app.services.print_scheduler"):
            assert _effective_plate_id(None, path) == 1

        assert "broken.3mf" in caplog.text
        assert "BadZipFile" in caplog.text or "not a zip file" in caplog.text.lower()

    def test_an_explicit_plate_the_archive_does_not_hold_is_logged_not_redirected(self, tmp_path, caplog):
        # The wedge of #2947 seen from the other side: the operator named
        # plate 1, the file only has plate 2. Redirecting to 2 would print a
        # model nobody asked for, so the command goes out as asked.
        path = _write_gcode_members(tmp_path / "right.gcode.3mf", ["Metadata/plate_2.gcode"])

        with caplog.at_level(logging.WARNING, logger="backend.app.services.print_scheduler"):
            assert _effective_plate_id(1, path) == 1

        assert "right.gcode.3mf" in caplog.text
        assert "Metadata/plate_2.gcode" in caplog.text

    def test_a_plate_the_archive_does_hold_logs_nothing(self, tmp_path, caplog):
        path = _write_gcode_members(
            tmp_path / "two.gcode.3mf",
            ["Metadata/plate_1.gcode", "Metadata/plate_2.gcode"],
        )

        with caplog.at_level(logging.WARNING, logger="backend.app.services.print_scheduler"):
            assert _effective_plate_id(2, path) == 2

        assert caplog.text == ""

    def test_a_slicer_that_does_not_number_its_plates_is_not_warned_about(self, tmp_path, caplog):
        # There is no plate numbering to contradict here, so an explicit plate
        # is not evidence of a mismatch and must not be reported as one.
        path = _write_gcode_members(tmp_path / "custom.gcode.3mf", ["Metadata/print.gcode"])

        with caplog.at_level(logging.WARNING, logger="backend.app.services.print_scheduler"):
            assert _effective_plate_id(1, path) == 1

        assert caplog.text == ""

    def test_an_unsliced_archive_is_not_warned_about_either(self, tmp_path, caplog):
        path = _write_gcode_members(tmp_path / "unsliced.3mf", ["3D/3dmodel.model"])

        with caplog.at_level(logging.WARNING, logger="backend.app.services.print_scheduler"):
            assert _effective_plate_id(1, path) == 1

        assert caplog.text == ""
