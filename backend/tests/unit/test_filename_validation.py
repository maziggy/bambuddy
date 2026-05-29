"""Validator tests for FAT32/exFAT-safe print filenames (#1540)."""

import pytest

from backend.app.utils.filename import (
    INVALID_FILENAME_CHARS,
    InvalidFilenameError,
    derive_remote_filename,
    validate_print_filename,
)


class TestValidatePrintFilename:
    @pytest.mark.parametrize(
        "name",
        [
            "model.3mf",
            "Bersaglio.gcode.3mf",
            "Plate 1.3mf",
            "プリント.3mf",
            "model_v2-final.3mf",
            "a.3mf",
        ],
    )
    def test_valid_names_accepted(self, name: str) -> None:
        validate_print_filename(name)

    @pytest.mark.parametrize("char", list(INVALID_FILENAME_CHARS))
    def test_each_invalid_char_rejected(self, char: str) -> None:
        with pytest.raises(InvalidFilenameError) as exc_info:
            validate_print_filename(f"L{char}R.3mf")
        assert exc_info.value.char == char

    def test_pipe_from_issue_1540(self) -> None:
        """The exact reproducer from the bug report."""
        with pytest.raises(InvalidFilenameError) as exc_info:
            validate_print_filename("L|R.3mf")
        assert exc_info.value.char == "|"

    @pytest.mark.parametrize("name", ["", " ", "   "])
    def test_empty_rejected(self, name: str) -> None:
        with pytest.raises(InvalidFilenameError, match="empty"):
            validate_print_filename(name)

    @pytest.mark.parametrize("name", [".", ".."])
    def test_dot_names_rejected(self, name: str) -> None:
        with pytest.raises(InvalidFilenameError):
            validate_print_filename(name)

    def test_control_char_rejected(self) -> None:
        with pytest.raises(InvalidFilenameError, match="control"):
            validate_print_filename("file\x01.3mf")

    @pytest.mark.parametrize("name", ["file.3mf.", "file.3mf "])
    def test_trailing_space_or_dot_rejected(self, name: str) -> None:
        with pytest.raises(InvalidFilenameError, match="space or dot"):
            validate_print_filename(name)

    def test_too_long_rejected(self) -> None:
        with pytest.raises(InvalidFilenameError, match="bytes"):
            validate_print_filename("a" * 256)

    def test_unicode_byte_length_not_codepoint(self) -> None:
        """255 multi-byte codepoints exceeds 255 bytes — must reject."""
        # 'ä' is 2 bytes in UTF-8
        with pytest.raises(InvalidFilenameError, match="bytes"):
            validate_print_filename("ä" * 200)


class TestDeriveRemoteFilename:
    """SD-card upload-name derivation must match what the cleanup deletes (#1542)."""

    def test_strips_gcode_3mf(self) -> None:
        assert derive_remote_filename("Cube.gcode.3mf") == "Cube.3mf"

    def test_strips_3mf(self) -> None:
        assert derive_remote_filename("Cube.3mf") == "Cube.3mf"

    def test_bare_stem_appends_3mf(self) -> None:
        assert derive_remote_filename("Cube") == "Cube.3mf"

    def test_replaces_spaces_with_underscores(self) -> None:
        # firmware parses ftp://{filename} as a URL, spaces break it
        assert derive_remote_filename("Cube (1).gcode.3mf") == "Cube_(1).3mf"

    def test_doubled_gcode_3mf_fully_stripped(self) -> None:
        # The literal reproducer from #1542: library row had .gcode.3mf appended twice
        assert derive_remote_filename("Cube (1).gcode.3mf.gcode.3mf") == "Cube_(1).3mf"

    def test_doubled_3mf_fully_stripped(self) -> None:
        assert derive_remote_filename("Cube.3mf.3mf") == "Cube.3mf"

    def test_mixed_double_extensions_fully_stripped(self) -> None:
        assert derive_remote_filename("Cube.gcode.3mf.3mf") == "Cube.3mf"

    def test_raw_gcode_unchanged_stem(self) -> None:
        # Bare .gcode (no .3mf wrapper) is a valid sliced file — only the
        # .3mf wrapper gets stripped; .gcode survives and the result is
        # the printer's expected ftp:// target.
        assert derive_remote_filename("Cube.gcode") == "Cube.gcode.3mf"

    def test_idempotent(self) -> None:
        once = derive_remote_filename("Cube (1).gcode.3mf.gcode.3mf")
        assert derive_remote_filename(once) == once

    def test_unicode_stem_preserved(self) -> None:
        assert derive_remote_filename("プリント.gcode.3mf") == "プリント.3mf"

    def test_non_string_input_raises_typeerror(self) -> None:
        """A duck-typed object whose endswith always returns truthy must not be
        allowed to enter the strip loop — that's how a test mock OOM'd the
        container at 61 GB before the type guard was added."""
        from unittest.mock import MagicMock

        with pytest.raises(TypeError, match="requires str"):
            derive_remote_filename(MagicMock())
        with pytest.raises(TypeError, match="requires str"):
            derive_remote_filename(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="requires str"):
            derive_remote_filename(123)  # type: ignore[arg-type]
