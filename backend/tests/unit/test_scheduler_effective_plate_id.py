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

import zipfile

from backend.app.services.print_scheduler import _effective_plate_id


def _write_gcode_members(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, "")
    return path


class TestEffectivePlateId:
    def test_explicit_plate_id_wins_without_touching_the_file(self, tmp_path):
        # A queue item's own choice always wins, and must not require the
        # file to even exist to return it.
        assert _effective_plate_id(3, tmp_path / "does-not-exist.3mf") == 3

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
