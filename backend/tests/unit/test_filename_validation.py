"""Validator tests for FAT32/exFAT-safe print filenames (#1540)."""

import pytest

from backend.app.utils.filename import (
    INVALID_FILENAME_CHARS,
    InvalidFilenameError,
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
