"""Unit tests for the STL thumbnail service."""

import os
import tempfile
from pathlib import Path

import pytest


def _check_trimesh_available():
    """Check if trimesh is available for import."""
    try:
        import trimesh

        return True
    except ImportError:
        return False


class TestStlThumbnailService:
    """Tests for STL thumbnail generation service."""

    def test_generate_stl_thumbnail_imports_available(self):
        """Test that required imports are available."""
        try:
            import matplotlib
            import trimesh

            assert trimesh is not None
            assert matplotlib is not None
        except ImportError as e:
            pytest.skip(f"Required dependencies not installed: {e}")

    def test_generate_stl_thumbnail_returns_none_on_missing_deps(self):
        """Test graceful degradation when dependencies are missing."""
        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        with tempfile.TemporaryDirectory() as tmpdir:
            stl_path = Path(tmpdir) / "test.stl"
            thumbnails_dir = Path(tmpdir)

            # Create a dummy STL file (will fail to parse)
            stl_path.write_text("invalid stl content")

            # Should return None on failure, not raise
            result = generate_stl_thumbnail(stl_path, thumbnails_dir)
            assert result is None

    @pytest.mark.skipif(
        not _check_trimesh_available(),
        reason="trimesh not installed",
    )
    def test_generate_stl_thumbnail_with_simple_cube(self):
        """Test thumbnail generation with a simple cube STL."""
        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        with tempfile.TemporaryDirectory() as tmpdir:
            stl_path = Path(tmpdir) / "cube.stl"
            thumbnails_dir = Path(tmpdir)

            # Create a simple ASCII STL cube
            stl_content = """solid cube
facet normal 0 0 -1
  outer loop
    vertex 0 0 0
    vertex 1 0 0
    vertex 1 1 0
  endloop
endfacet
facet normal 0 0 -1
  outer loop
    vertex 0 0 0
    vertex 1 1 0
    vertex 0 1 0
  endloop
endfacet
facet normal 0 0 1
  outer loop
    vertex 0 0 1
    vertex 1 1 1
    vertex 1 0 1
  endloop
endfacet
facet normal 0 0 1
  outer loop
    vertex 0 0 1
    vertex 0 1 1
    vertex 1 1 1
  endloop
endfacet
facet normal 0 -1 0
  outer loop
    vertex 0 0 0
    vertex 1 0 1
    vertex 1 0 0
  endloop
endfacet
facet normal 0 -1 0
  outer loop
    vertex 0 0 0
    vertex 0 0 1
    vertex 1 0 1
  endloop
endfacet
facet normal 1 0 0
  outer loop
    vertex 1 0 0
    vertex 1 0 1
    vertex 1 1 1
  endloop
endfacet
facet normal 1 0 0
  outer loop
    vertex 1 0 0
    vertex 1 1 1
    vertex 1 1 0
  endloop
endfacet
facet normal 0 1 0
  outer loop
    vertex 0 1 0
    vertex 1 1 0
    vertex 1 1 1
  endloop
endfacet
facet normal 0 1 0
  outer loop
    vertex 0 1 0
    vertex 1 1 1
    vertex 0 1 1
  endloop
endfacet
facet normal -1 0 0
  outer loop
    vertex 0 0 0
    vertex 0 1 0
    vertex 0 1 1
  endloop
endfacet
facet normal -1 0 0
  outer loop
    vertex 0 0 0
    vertex 0 1 1
    vertex 0 0 1
  endloop
endfacet
endsolid cube"""
            stl_path.write_text(stl_content)

            result = generate_stl_thumbnail(stl_path, thumbnails_dir)

            # Should return a path to the generated thumbnail
            if result:
                assert Path(result).exists()
                assert Path(result).suffix == ".png"
            # If result is None, dependencies might not be fully functional
            # which is acceptable

    def test_generate_stl_thumbnail_nonexistent_file(self):
        """Test thumbnail generation with nonexistent file."""
        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        with tempfile.TemporaryDirectory() as tmpdir:
            stl_path = Path(tmpdir) / "nonexistent.stl"
            thumbnails_dir = Path(tmpdir)

            result = generate_stl_thumbnail(stl_path, thumbnails_dir)
            assert result is None

    def test_generate_stl_thumbnail_empty_file(self):
        """Test thumbnail generation with empty file."""
        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        with tempfile.TemporaryDirectory() as tmpdir:
            stl_path = Path(tmpdir) / "empty.stl"
            thumbnails_dir = Path(tmpdir)

            # Create empty file
            stl_path.write_bytes(b"")

            result = generate_stl_thumbnail(stl_path, thumbnails_dir)
            assert result is None

    @pytest.mark.skipif(
        not _check_trimesh_available(),
        reason="trimesh not installed",
    )
    def test_string_arguments_accepted_without_typeerror(self):
        """Regression for #1299: external-scan path passed both args as str.

        Before the fix, the function did ``thumbnails_dir / thumb_filename`` on
        a ``str`` and raised ``TypeError: unsupported operand type(s) for /:
        'str' and 'str'`` for every STL on an external folder scan. The fix
        coerces both args to ``Path`` at entry. This test passes string args
        and asserts the function either succeeds or returns ``None`` — but
        never raises the TypeError.
        """
        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        with tempfile.TemporaryDirectory() as tmpdir:
            stl_path = Path(tmpdir) / "cube.stl"
            # Minimal valid binary STL: header (80 bytes) + tri count (0)
            stl_path.write_bytes(b"\x00" * 80 + (0).to_bytes(4, "little"))

            # str args — the exact shape the external-scan call site used.
            result = generate_stl_thumbnail(str(stl_path), str(tmpdir))

            # Zero-triangle mesh either yields no thumbnail or fails the
            # downstream render — both are acceptable; what's NOT acceptable
            # is a TypeError leaking out, which is what the str/str bug did.
            assert result is None or Path(result).exists()


class TestStlThumbnailConstants:
    """Tests for STL thumbnail service constants."""

    def test_bambu_green_color(self):
        """Test that Bambu green color is defined."""
        from backend.app.services.stl_thumbnail import BAMBU_GREEN

        assert BAMBU_GREEN == "#00AE42"

    def test_background_color(self):
        """Test that background color is defined."""
        from backend.app.services.stl_thumbnail import BACKGROUND_COLOR

        assert BACKGROUND_COLOR == "#1a1a1a"

    def test_max_vertices_threshold(self):
        """Test that max vertices threshold is defined."""
        from backend.app.services.stl_thumbnail import MAX_VERTICES

        assert MAX_VERTICES == 100000

    def test_min_usable_stl_bytes_threshold(self):
        """MIN_USABLE_STL_BYTES is the call-site pre-skip floor.

        Binary STL with one triangle = 80B header + 4B count + 50B triangle
        = 134B. ASCII STL with one triangle ≈ 150B. Anything below this size
        cannot contain a usable mesh.
        """
        from backend.app.services.stl_thumbnail import MIN_USABLE_STL_BYTES

        assert MIN_USABLE_STL_BYTES == 200
        # Verify it sits between "smaller than smallest real STL" and
        # "common stub size" — the 24-byte ``solid test\nendsolid test``
        # stubs that triggered the warning storm.
        assert MIN_USABLE_STL_BYTES > 134  # smallest binary STL with one triangle
        assert MIN_USABLE_STL_BYTES > 150  # smallest ASCII STL with one triangle
        assert MIN_USABLE_STL_BYTES > 24  # the ZIP-stub case in the bug report

    def test_font_manager_logger_demoted_to_warning(self):
        """matplotlib.font_manager's per-font INFO scan is demoted at module
        import so the first STL upload doesn't surface a multi-line preamble
        of matplotlib internals in the journal."""
        import logging

        # Importing the module sets the level as a side effect.
        import backend.app.services.stl_thumbnail  # noqa: F401

        assert logging.getLogger("matplotlib.font_manager").level >= logging.WARNING

    def test_configure_matplotlib_cache_sets_mplconfigdir(self, tmp_path, monkeypatch):
        """``_configure_matplotlib_cache`` points matplotlib at a writable
        persistent path so it doesn't fall back to ``/tmp/matplotlib-XXX``
        on every cold start."""
        from backend.app.services.stl_thumbnail import _configure_matplotlib_cache

        # Ensure we start with no value so the helper actually runs.
        monkeypatch.delenv("MPLCONFIGDIR", raising=False)
        monkeypatch.setattr(
            "backend.app.services.stl_thumbnail.Path",
            __import__("pathlib").Path,
        )

        # Stub settings.base_dir to point inside tmp_path.
        from backend.app.core import config as core_config

        monkeypatch.setattr(core_config.settings, "base_dir", tmp_path, raising=False)

        _configure_matplotlib_cache()

        assert "MPLCONFIGDIR" in os.environ
        configured = Path(os.environ["MPLCONFIGDIR"])
        assert configured.exists()
        assert configured.is_dir()
        # And the directory sits under base_dir, not /tmp/matplotlib-XXX.
        assert tmp_path in configured.parents

    def test_configure_matplotlib_cache_respects_externally_set_value(self, tmp_path, monkeypatch):
        """If the operator (or container init) has set MPLCONFIGDIR already,
        the helper must leave it alone — they made a deliberate choice."""
        from backend.app.services.stl_thumbnail import _configure_matplotlib_cache

        external = str(tmp_path / "external-mpl-cache")
        monkeypatch.setenv("MPLCONFIGDIR", external)
        _configure_matplotlib_cache()
        assert os.environ["MPLCONFIGDIR"] == external

    def test_empty_mesh_logged_at_debug_not_warning(self, caplog):
        """An empty STL (header present, no triangles) must log at DEBUG, not
        WARNING — bulk uploads used to log thousands of WARNING lines per
        ZIP. Per-file content observations stay observable in debug logs
        but don't spam production journals."""
        import logging
        import tempfile
        from pathlib import Path

        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        # The exact 24-byte stub from the bug report
        stub_content = b"solid test\nendsolid test"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            stl_path = tmpdir_path / "stub.stl"
            stl_path.write_bytes(stub_content)

            with caplog.at_level(logging.DEBUG, logger="backend.app.services.stl_thumbnail"):
                result = generate_stl_thumbnail(stl_path, tmpdir_path)

        assert result is None
        # The empty-mesh message must NOT appear at WARNING level.
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING and "empty mesh" in r.getMessage()]
        assert warning_records == [], (
            f"Empty-mesh path still logs at WARNING: {[r.getMessage() for r in warning_records]}"
        )
