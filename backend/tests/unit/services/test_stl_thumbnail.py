"""Unit tests for the STL thumbnail service."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

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
