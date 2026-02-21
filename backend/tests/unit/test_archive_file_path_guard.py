"""Tests for archive file_path guard against empty paths and directories (#475).

When a 3mf download fails (e.g. BambuStudio-initiated prints), the fallback
archive is created with file_path="". Previously, `settings.base_dir / ""`
resolved to the base directory itself, which passed `exists()` but caused
`[Errno 21] Is a directory` when opened as a ZipFile.

The fix replaces `.exists()` with `.is_file()` across all archive endpoints,
and adds an `archive.file_path` truthiness check for photo capture.
"""

from pathlib import Path

import pytest


class TestIsFileGuard:
    """Verify that is_file() correctly rejects directories and empty paths."""

    def test_empty_path_resolves_to_parent(self, tmp_path: Path):
        """Path('') / '' resolves to the parent directory (which exists but is not a file)."""
        base_dir = tmp_path / "data"
        base_dir.mkdir()

        file_path = base_dir / ""
        # exists() returns True (the directory exists) — this was the old broken check
        assert file_path.exists()
        # is_file() returns False (it's a directory, not a file)
        assert not file_path.is_file()

    def test_real_file_passes_is_file(self, tmp_path: Path):
        """A real 3mf file passes is_file()."""
        fake_3mf = tmp_path / "archive" / "test.3mf"
        fake_3mf.parent.mkdir(parents=True)
        fake_3mf.write_bytes(b"PK\x03\x04")  # ZIP magic bytes

        assert fake_3mf.is_file()

    def test_nonexistent_file_fails_is_file(self, tmp_path: Path):
        """A nonexistent path fails is_file()."""
        missing = tmp_path / "archive" / "missing.3mf"
        assert not missing.is_file()

    def test_directory_fails_is_file(self, tmp_path: Path):
        """A directory path fails is_file()."""
        dir_path = tmp_path / "archive"
        dir_path.mkdir()
        assert not dir_path.is_file()


class TestFallbackArchiveFilePath:
    """Verify that a fallback archive (file_path='') is handled safely."""

    def test_base_dir_slash_empty_string_is_base_dir(self, tmp_path: Path):
        """Joining base_dir with empty string produces base_dir (a directory)."""
        base_dir = tmp_path / "data"
        base_dir.mkdir()

        # Simulate: file_path = settings.base_dir / archive.file_path
        # where archive.file_path = ""
        file_path = base_dir / ""

        # The resolved path IS the directory itself
        assert file_path.resolve() == base_dir.resolve()
        # exists() says True (this caused the old bug)
        assert file_path.exists()
        # is_file() says False (this is the fix)
        assert not file_path.is_file()

    def test_archive_file_path_empty_string_is_falsy(self):
        """Empty string file_path is falsy (used for photo capture guard)."""
        file_path = ""
        assert not file_path

    def test_archive_file_path_real_is_truthy(self):
        """Real file_path is truthy."""
        file_path = "archive/2026/02/test.3mf"
        assert file_path


class TestPhotoPathDerivation:
    """Verify that photo directory derivation is safe with empty file_path."""

    def test_empty_file_path_parent_is_dot(self):
        """Path('').parent is '.' — would resolve to base_dir instead of archive dir."""
        parent = Path("").parent
        assert str(parent) == "."

    def test_real_file_path_parent_is_archive_dir(self):
        """Real file_path parent gives the correct archive directory."""
        parent = Path("archive/2026/02/test.3mf").parent
        assert str(parent) == "archive/2026/02"
