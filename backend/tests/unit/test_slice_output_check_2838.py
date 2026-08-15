"""A slice with no printer start G-code must not become a print (#2838).

The start block is where a Bambu printer's AMS load (``M620``) and its
preparation-stage announcements (``M1002 gcode_claim_action``) live. Sliced
without it, a job dispatches and looks alive — bed at temperature, toolhead
moving — while extruding nothing and reporting no stage, which is
indistinguishable from a print that simply has not started yet.

The defect that produced such files was in the sidecar: Bambuddy sends a
bundled preset by name, and a resolver that walks only ``inherits`` never
finds the companion file holding the real start G-code, falling through to a
577-character generic stub. Bambuddy cannot see that resolution happen, so
this checks the one thing it can see — the bytes that came back.
"""

import io
import json
import zipfile

import pytest

from backend.app.services.slice_output_check import (
    missing_start_gcode_message,
    start_gcode_is_missing,
)

pytestmark = pytest.mark.unit

# Abridged from `fdm_machine_common`, the root template every Bambu machine
# preset falls through to when the companion is not read. What matters is
# what it lacks.
GENERIC = "M17 X1.2 Y1.2 Z0.75\nG28 X\nM104 S140\nG29.2 S0\n"

REAL = "M1002 gcode_claim_action : 1\nM620 M\nM620.10 A0 F74.8347 H0.4 C\nM1002 gcode_claim_action : 14\n"


def _3mf(settings: dict | None, *, valid_zip: bool = True) -> bytes:
    if not valid_zip:
        return b"not a zip file at all"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "; sliced\nG1 X0 Y0\n")
        if settings is not None:
            archive.writestr("Metadata/project_settings.config", json.dumps(settings))
    return buffer.getvalue()


class TestTheDefect:
    def test_the_generic_fallback_is_caught(self):
        assert start_gcode_is_missing(_3mf({"machine_start_gcode": GENERIC}), export_3mf=True)

    def test_a_real_start_block_passes(self):
        assert not start_gcode_is_missing(_3mf({"machine_start_gcode": REAL}), export_3mf=True)

    def test_the_value_may_be_a_list(self):
        """Slicer config values arrive as a bare string or a one-element list
        depending on the setting's declared type; both shapes are real."""
        assert not start_gcode_is_missing(_3mf({"machine_start_gcode": [REAL]}), export_3mf=True)
        assert start_gcode_is_missing(_3mf({"machine_start_gcode": [GENERIC]}), export_3mf=True)

    def test_an_empty_start_block_is_caught(self):
        assert start_gcode_is_missing(_3mf({"machine_start_gcode": ""}), export_3mf=True)


class TestPlainGcodeExport:
    """Not every slice exports a 3MF, and a raw .gcode file has no config to
    read — the emitted text is all there is."""

    def test_a_real_start_block_passes(self):
        assert not start_gcode_is_missing(f"; header\n{REAL}G1 X0\n".encode(), export_3mf=False)

    def test_the_generic_fallback_is_caught(self):
        assert start_gcode_is_missing(f"; header\n{GENERIC}G1 X0\n".encode(), export_3mf=False)

    def test_undecodable_bytes_do_not_crash_it(self):
        """Thumbnails and comments can carry anything; the marker is ASCII."""
        assert not start_gcode_is_missing(b"\xff\xfe binary junk " + REAL.encode(), export_3mf=False)


class TestItDeclinesToJudgeWhatItCannotRead:
    """Blocking a slice is a strong action. Anything this check cannot settle
    has to pass — the caller knows no more than it does, and refusing an
    unusual-but-fine file would be worse than the defect it guards against."""

    def test_an_unreadable_archive_passes(self):
        assert not start_gcode_is_missing(_3mf(None, valid_zip=False), export_3mf=True)

    def test_an_archive_without_the_config_passes(self):
        assert not start_gcode_is_missing(_3mf(None), export_3mf=True)

    def test_a_config_without_the_key_passes(self):
        """A slicer that does not write the key at all is not evidence that
        the printer will get no start G-code."""
        assert not start_gcode_is_missing(_3mf({"layer_height": "0.2"}), export_3mf=True)

    def test_a_malformed_config_passes(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Metadata/project_settings.config", "{ not json")
        assert not start_gcode_is_missing(buffer.getvalue(), export_3mf=True)

    def test_empty_content_passes(self):
        assert not start_gcode_is_missing(b"", export_3mf=True)
        assert not start_gcode_is_missing(b"", export_3mf=False)


class TestTheMessage:
    def test_it_names_the_preset_and_the_actual_fix(self):
        message = missing_start_gcode_message("Bambu Lab X2D 0.4 nozzle")

        assert "Bambu Lab X2D 0.4 nozzle" in message
        # The fix is a sidecar image, not anything the user can change in
        # Bambuddy — saying so is the whole point of failing loudly.
        assert "sidecar" in message

    def test_it_says_the_file_was_not_kept(self):
        assert "not saved" in missing_start_gcode_message("Bambu Lab P1S 0.4 nozzle")
