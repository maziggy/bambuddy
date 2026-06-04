"""Tests for ``backend.app.utils.safe_path.safe_join_under``.

Cover every escape vector documented in the helper plus the legitimate
nested-path use case so the helper's behaviour is locked in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.utils.safe_path import (
    PathTraversalError,
    assert_under,
    safe_join_under,
)


@pytest.fixture()
def library(tmp_path: Path) -> Path:
    """A real on-disk directory that mimics the "trusted parent" role."""
    lib = tmp_path / "library"
    lib.mkdir()
    return lib


class TestSafeJoinUnder:
    def test_simple_filename_is_joined(self, library: Path):
        result = safe_join_under(library, "model.3mf")
        assert result == (library / "model.3mf").resolve()

    def test_nested_path_components_are_joined(self, library: Path):
        result = safe_join_under(library, "myfolder", "sub", "file.3mf")
        assert result == (library / "myfolder" / "sub" / "file.3mf").resolve()

    def test_absolute_path_rejected(self, library: Path):
        # The exact shape that produced the original CVE — ``Path("/lib") / "/etc/passwd"``
        # collapses to ``Path("/etc/passwd")`` in Python's pathlib.
        with pytest.raises(HTTPException) as exc:
            safe_join_under(library, "/etc/passwd")
        assert exc.value.status_code == 400

    def test_absolute_windows_path_rejected(self, library: Path):
        with pytest.raises(HTTPException):
            safe_join_under(library, "\\\\evil\\share\\x")

    def test_parent_traversal_rejected(self, library: Path):
        with pytest.raises(HTTPException):
            safe_join_under(library, "..", "etc", "passwd")

    def test_embedded_parent_traversal_rejected(self, library: Path):
        # ``library/foo/../../etc/passwd`` resolves outside ``library``.
        with pytest.raises(HTTPException):
            safe_join_under(library, "foo", "..", "..", "etc", "passwd")

    def test_null_byte_rejected(self, library: Path):
        with pytest.raises(HTTPException):
            safe_join_under(library, "evil\x00.3mf")

    def test_empty_string_part_rejected(self, library: Path):
        with pytest.raises(HTTPException):
            safe_join_under(library, "")

    def test_no_parts_rejected(self, library: Path):
        with pytest.raises(HTTPException):
            safe_join_under(library)

    def test_non_string_part_rejected(self, library: Path):
        with pytest.raises(HTTPException):
            safe_join_under(library, 42)  # type: ignore[arg-type]

    def test_http_false_raises_path_traversal_error(self, library: Path):
        with pytest.raises(PathTraversalError):
            safe_join_under(library, "/etc/passwd", http=False)

    def test_http_false_allows_clean_join(self, library: Path):
        result = safe_join_under(library, "ok.txt", http=False)
        assert result == (library / "ok.txt").resolve()

    def test_returned_path_is_resolved(self, library: Path):
        # The helper returns a resolved path so callers don't need to do it
        # themselves — every downstream is_relative_to/parent check assumes
        # a canonical form.
        result = safe_join_under(library, "x.txt")
        assert result == result.resolve()


class TestAssertUnder:
    def test_inside_passes(self, library: Path):
        candidate = library / "x" / "y" / "z.txt"
        out = assert_under(library, candidate)
        assert out == candidate.resolve()

    def test_outside_rejects(self, library: Path, tmp_path: Path):
        outside = tmp_path / "elsewhere" / "evil.txt"
        with pytest.raises(HTTPException):
            assert_under(library, outside)

    def test_outside_raises_path_traversal_error_with_http_false(self, library: Path, tmp_path: Path):
        outside = tmp_path / "elsewhere" / "evil.txt"
        with pytest.raises(PathTraversalError):
            assert_under(library, outside, http=False)


class TestPocReproducer:
    """The exact attacker payload from the advisory.

    A directly attacker-controlled folder name pointing at a venv's
    site-packages directory used to land a ``.pth`` file on disk. With the
    helper in place the join now raises before any write.
    """

    def test_advisory_poc_target_dir_rejected(self, library: Path):
        # Verbatim shape from the advisory POC.
        target_dir = "BAMBUDDY_BASE_DIR/bambuddy/venv/lib/python3.14/site-packages"
        # Leading slash → absolute → rejected up-front.
        with pytest.raises(HTTPException):
            safe_join_under(library, "/" + target_dir)
        # No leading slash but with ``..`` traversal embedded in the
        # follow-up file path — also rejected.
        with pytest.raises(HTTPException):
            safe_join_under(library, "innocent", "..", "..", "evil.pth")
