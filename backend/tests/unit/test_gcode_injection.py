"""Unit tests for G-code injection into 3MF files (#422)."""

import tempfile
import zipfile
from pathlib import Path

import pytest

from backend.app.utils.threemf_tools import inject_gcode_into_3mf


def _make_temp_path(suffix=".3mf") -> Path:
    """Create a temp file path without leaving it open (avoids SIM115)."""
    fd, name = tempfile.mkstemp(suffix=suffix)
    import os

    os.close(fd)
    return Path(name)


def _make_test_3mf(gcode_content: str = "G28\nG1 X0 Y0\nM400\n", plate_id: int = 1) -> Path:
    """Create a minimal 3MF file with embedded G-code for testing."""
    tmp_path = _make_temp_path()

    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"Metadata/plate_{plate_id}.gcode", gcode_content)
        zf.writestr("Metadata/slice_info.config", "<config></config>")
        zf.writestr("3D/3dmodel.model", "<model></model>")

    return tmp_path


class TestInjectGcodeInto3mf:
    """Tests for inject_gcode_into_3mf()."""

    def test_inject_start_gcode(self):
        """Start G-code is prepended before the original content."""
        source = _make_test_3mf("G28\nM400\n")
        try:
            result = inject_gcode_into_3mf(source, 1, "M117 Start\nG92 E0", None)
            assert result is not None

            with zipfile.ZipFile(result, "r") as zf:
                gcode = zf.read("Metadata/plate_1.gcode").decode("utf-8")

            assert gcode.startswith("M117 Start\nG92 E0\n")
            assert "G28\nM400\n" in gcode
        finally:
            source.unlink(missing_ok=True)
            if result:
                result.unlink(missing_ok=True)

    def test_inject_end_gcode(self):
        """End G-code is appended after the original content."""
        source = _make_test_3mf("G28\nM400")
        try:
            result = inject_gcode_into_3mf(source, 1, None, "M104 S0\nG28 X")
            assert result is not None

            with zipfile.ZipFile(result, "r") as zf:
                gcode = zf.read("Metadata/plate_1.gcode").decode("utf-8")

            assert gcode.endswith("M104 S0\nG28 X\n")
            assert gcode.startswith("G28\nM400")
        finally:
            source.unlink(missing_ok=True)
            if result:
                result.unlink(missing_ok=True)

    def test_inject_both_start_and_end(self):
        """Both start and end G-code are injected."""
        source = _make_test_3mf("G28\n")
        try:
            result = inject_gcode_into_3mf(source, 1, "; START", "; END")
            assert result is not None

            with zipfile.ZipFile(result, "r") as zf:
                gcode = zf.read("Metadata/plate_1.gcode").decode("utf-8")

            assert gcode.startswith("; START\n")
            assert gcode.endswith("; END\n")
            assert "G28" in gcode
        finally:
            source.unlink(missing_ok=True)
            if result:
                result.unlink(missing_ok=True)

    def test_no_injection_returns_none(self):
        """Returns None when both start and end are None."""
        source = _make_test_3mf()
        try:
            result = inject_gcode_into_3mf(source, 1, None, None)
            assert result is None
        finally:
            source.unlink(missing_ok=True)

    def test_empty_strings_returns_none(self):
        """Returns None when both start and end are empty strings."""
        source = _make_test_3mf()
        try:
            result = inject_gcode_into_3mf(source, 1, "", "")
            assert result is None
        finally:
            source.unlink(missing_ok=True)

    def test_plate_id_selection(self):
        """Injects into the correct plate's G-code file."""
        source = _make_temp_path()

        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Metadata/plate_1.gcode", "PLATE1\n")
            zf.writestr("Metadata/plate_2.gcode", "PLATE2\n")

        try:
            result = inject_gcode_into_3mf(source, 2, "; INJECTED", None)
            assert result is not None

            with zipfile.ZipFile(result, "r") as zf:
                plate1 = zf.read("Metadata/plate_1.gcode").decode("utf-8")
                plate2 = zf.read("Metadata/plate_2.gcode").decode("utf-8")

            # Only plate 2 should be modified
            assert plate1 == "PLATE1\n"
            assert plate2.startswith("; INJECTED\n")
        finally:
            source.unlink(missing_ok=True)
            if result:
                result.unlink(missing_ok=True)

    def test_preserves_other_files(self):
        """Non-gcode files in the 3MF are preserved unchanged."""
        source = _make_test_3mf()
        try:
            result = inject_gcode_into_3mf(source, 1, "; START", None)
            assert result is not None

            with zipfile.ZipFile(result, "r") as zf:
                names = zf.namelist()
                assert "Metadata/slice_info.config" in names
                assert "3D/3dmodel.model" in names
                config = zf.read("Metadata/slice_info.config").decode("utf-8")
                assert config == "<config></config>"
        finally:
            source.unlink(missing_ok=True)
            if result:
                result.unlink(missing_ok=True)

    def test_no_gcode_file_returns_none(self):
        """Returns None when the 3MF has no gcode files."""
        source = _make_temp_path()

        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("3D/3dmodel.model", "<model></model>")

        try:
            result = inject_gcode_into_3mf(source, 1, "; START", None)
            assert result is None
        finally:
            source.unlink(missing_ok=True)

    def test_invalid_file_returns_none(self):
        """Returns None for a non-ZIP file."""
        source = _make_temp_path()
        source.write_bytes(b"not a zip file")

        try:
            result = inject_gcode_into_3mf(source, 1, "; START", None)
            assert result is None
        finally:
            source.unlink(missing_ok=True)

    def test_fallback_to_first_gcode(self):
        """Falls back to first gcode file when plate-specific not found."""
        source = _make_temp_path()

        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Metadata/plate_1.gcode", "ORIGINAL\n")

        try:
            # Request plate 5 which doesn't exist — should fall back to plate_1
            result = inject_gcode_into_3mf(source, 5, "; INJECTED", None)
            assert result is not None

            with zipfile.ZipFile(result, "r") as zf:
                gcode = zf.read("Metadata/plate_1.gcode").decode("utf-8")

            assert gcode.startswith("; INJECTED\n")
        finally:
            source.unlink(missing_ok=True)
            if result:
                result.unlink(missing_ok=True)

    def test_original_file_unchanged(self):
        """The source 3MF is never modified."""
        source = _make_test_3mf("ORIGINAL\n")
        try:
            result = inject_gcode_into_3mf(source, 1, "; START", "; END")
            assert result is not None

            # Verify original is untouched
            with zipfile.ZipFile(source, "r") as zf:
                original = zf.read("Metadata/plate_1.gcode").decode("utf-8")
            assert original == "ORIGINAL\n"
        finally:
            source.unlink(missing_ok=True)
            if result:
                result.unlink(missing_ok=True)
