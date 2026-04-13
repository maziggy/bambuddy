"""Tests for to_absolute_path() path-traversal guard in library routes.

Covers three behaviours added/changed by the GCode viewer PR:

1. Relative paths that escape base_dir are rejected with ValueError.
2. Path.is_relative_to() is used instead of startswith(str(base)),
   avoiding the /data/app vs /data/app_evil prefix-confusion bug.
3. Legacy absolute paths (pre-migration DB rows) are returned verbatim
   instead of raising ValueError.
"""

from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call(relative_path, base_dir):
    """Call to_absolute_path with base_dir patched to *base_dir*."""
    from backend.app.api.routes.library import to_absolute_path

    with patch("backend.app.api.routes.library.app_settings") as mock_settings:
        mock_settings.base_dir = str(base_dir)
        return to_absolute_path(relative_path)


# ---------------------------------------------------------------------------
# None / empty guard
# ---------------------------------------------------------------------------

class TestNullInputs:
    def test_none_returns_none(self, tmp_path):
        assert _call(None, tmp_path) is None

    def test_empty_string_returns_none(self, tmp_path):
        assert _call("", tmp_path) is None


# ---------------------------------------------------------------------------
# Relative path traversal guard
# ---------------------------------------------------------------------------

class TestRelativePathTraversal:
    def test_normal_relative_path_resolves(self, tmp_path):
        """A safe relative path resolves to base_dir / rel."""
        base = tmp_path / "data"
        base.mkdir()
        result = _call("files/model.gcode", base)
        assert result == (base / "files" / "model.gcode").resolve()

    def test_traversal_via_dotdot_raises(self, tmp_path):
        """../etc/passwd must be rejected."""
        base = tmp_path / "data"
        base.mkdir()
        with pytest.raises(ValueError, match="escapes base directory"):
            _call("../etc/passwd", base)

    def test_traversal_via_nested_dotdot_raises(self, tmp_path):
        """files/../../etc/passwd must be rejected."""
        base = tmp_path / "data"
        base.mkdir()
        with pytest.raises(ValueError, match="escapes base directory"):
            _call("files/../../etc/passwd", base)

    def test_prefix_confusion_is_blocked(self, tmp_path):
        """Ensure /data/app_evil/secret is not permitted when base is /data/app.

        A naive startswith(str(base)) check would allow this because
        '/data/app_evil'.startswith('/data/app') is True.
        Path.is_relative_to() must be used instead.
        """
        # Simulate: base = /tmp/.../data_app, sibling = /tmp/.../data_app_evil
        base = tmp_path / "data_app"
        sibling = tmp_path / "data_app_evil"
        base.mkdir()
        sibling.mkdir()

        # Construct a relative path that resolves into the *sibling* dir.
        # from base: ../data_app_evil/secret
        with pytest.raises(ValueError, match="escapes base directory"):
            _call("../data_app_evil/secret", base)


# ---------------------------------------------------------------------------
# Legacy absolute path pass-through
# ---------------------------------------------------------------------------

class TestLegacyAbsolutePaths:
    def test_absolute_path_inside_base_is_returned(self, tmp_path):
        """An absolute path that happens to be inside base_dir is returned as-is."""
        base = tmp_path / "data"
        base.mkdir()
        abs_path = str(base / "archive" / "old.3mf")
        result = _call(abs_path, base)
        assert result == Path(abs_path).resolve()

    def test_absolute_path_outside_base_is_returned(self, tmp_path):
        """An absolute path outside base_dir is returned verbatim (legacy compat).

        Pre-migration DB rows may store absolute paths that predate the
        base_dir layout. These must NOT raise ValueError; callers are
        responsible for further existence checks.
        """
        base = tmp_path / "data"
        base.mkdir()
        outside = tmp_path / "old_archive" / "legacy.3mf"
        result = _call(str(outside), base)
        assert result == outside.resolve()

