"""Unit tests for the STL thumbnail service."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestSTLThumbnailService:
    """Tests for STL thumbnail generation."""

    def test_generate_thumbnail_ascii_stl(self):
        """Test generating thumbnail from ASCII STL file."""
        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        # Create a simple ASCII STL cube
        ascii_stl = """solid cube
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
      vertex 1 1 1
      vertex 1 1 0
    endloop
  endfacet
  facet normal 1 0 0
    outer loop
      vertex 1 0 0
      vertex 1 0 1
      vertex 1 1 1
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
endsolid cube
"""

        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False, mode="w") as stl_file:
            stl_file.write(ascii_stl)
            stl_path = stl_file.name

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as png_file:
            png_path = png_file.name

        try:
            result = generate_stl_thumbnail(stl_path, png_path)
            assert result is True
            assert os.path.exists(png_path)
            # Check it's a valid PNG (starts with PNG magic bytes)
            with open(png_path, "rb") as f:
                header = f.read(8)
                assert header[:4] == b"\x89PNG"
        finally:
            os.unlink(stl_path)
            if os.path.exists(png_path):
                os.unlink(png_path)

    def test_generate_thumbnail_binary_stl(self):
        """Test generating thumbnail from binary STL file."""
        import struct

        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        # Create a simple binary STL cube (minimal version)
        # Binary STL format:
        # - 80 bytes header
        # - 4 bytes number of triangles (uint32)
        # - For each triangle:
        #   - 12 bytes normal (3 floats)
        #   - 36 bytes vertices (9 floats, 3 vertices x 3 coords)
        #   - 2 bytes attribute byte count (usually 0)

        header = b"\x00" * 80  # Empty header
        num_triangles = 12  # A cube has 12 triangles (2 per face)

        # Define cube vertices
        vertices = [
            # Bottom face (z=0)
            ((0, 0, -1), [(0, 0, 0), (1, 0, 0), (1, 1, 0)]),
            ((0, 0, -1), [(0, 0, 0), (1, 1, 0), (0, 1, 0)]),
            # Top face (z=1)
            ((0, 0, 1), [(0, 0, 1), (1, 1, 1), (1, 0, 1)]),
            ((0, 0, 1), [(0, 0, 1), (0, 1, 1), (1, 1, 1)]),
            # Front face (y=0)
            ((0, -1, 0), [(0, 0, 0), (1, 0, 1), (1, 0, 0)]),
            ((0, -1, 0), [(0, 0, 0), (0, 0, 1), (1, 0, 1)]),
            # Back face (y=1)
            ((0, 1, 0), [(0, 1, 0), (1, 1, 0), (1, 1, 1)]),
            ((0, 1, 0), [(0, 1, 0), (1, 1, 1), (0, 1, 1)]),
            # Left face (x=0)
            ((-1, 0, 0), [(0, 0, 0), (0, 1, 0), (0, 1, 1)]),
            ((-1, 0, 0), [(0, 0, 0), (0, 1, 1), (0, 0, 1)]),
            # Right face (x=1)
            ((1, 0, 0), [(1, 0, 0), (1, 1, 1), (1, 1, 0)]),
            ((1, 0, 0), [(1, 0, 0), (1, 0, 1), (1, 1, 1)]),
        ]

        binary_data = header + struct.pack("<I", num_triangles)
        for normal, verts in vertices:
            binary_data += struct.pack("<fff", *normal)  # Normal
            for v in verts:
                binary_data += struct.pack("<fff", *v)  # Vertex
            binary_data += struct.pack("<H", 0)  # Attribute byte count

        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False, mode="wb") as stl_file:
            stl_file.write(binary_data)
            stl_path = stl_file.name

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as png_file:
            png_path = png_file.name

        try:
            result = generate_stl_thumbnail(stl_path, png_path)
            assert result is True
            assert os.path.exists(png_path)
        finally:
            os.unlink(stl_path)
            if os.path.exists(png_path):
                os.unlink(png_path)

    def test_generate_thumbnail_invalid_file(self):
        """Test handling of invalid STL file."""
        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        # Create invalid STL content
        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False, mode="w") as stl_file:
            stl_file.write("This is not valid STL content")
            stl_path = stl_file.name

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as png_file:
            png_path = png_file.name

        try:
            result = generate_stl_thumbnail(stl_path, png_path)
            # Should return False for invalid file
            assert result is False
        finally:
            os.unlink(stl_path)
            if os.path.exists(png_path):
                os.unlink(png_path)

    def test_generate_thumbnail_nonexistent_file(self):
        """Test handling of nonexistent file."""
        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        result = generate_stl_thumbnail("/nonexistent/path/file.stl", "/tmp/output.png")
        assert result is False

    def test_generate_thumbnail_bytes_ascii(self):
        """Test generating thumbnail from ASCII STL bytes."""
        from backend.app.services.stl_thumbnail import generate_stl_thumbnail_bytes

        # Simple ASCII STL cube (same as above)
        ascii_stl = b"""solid cube
  facet normal 0 0 -1
    outer loop
      vertex 0 0 0
      vertex 1 0 0
      vertex 1 1 0
    endloop
  endfacet
  facet normal 0 0 1
    outer loop
      vertex 0 0 1
      vertex 1 0 1
      vertex 1 1 1
    endloop
  endfacet
endsolid cube
"""

        result = generate_stl_thumbnail_bytes(ascii_stl)
        assert result is not None
        # Check it's a valid PNG
        assert result[:4] == b"\x89PNG"

    def test_generate_thumbnail_bytes_invalid(self):
        """Test handling of invalid STL bytes."""
        from backend.app.services.stl_thumbnail import generate_stl_thumbnail_bytes

        result = generate_stl_thumbnail_bytes(b"not valid stl data")
        assert result is None

    def test_generate_thumbnail_custom_size(self):
        """Test generating thumbnail with custom size."""
        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        ascii_stl = """solid cube
  facet normal 0 0 -1
    outer loop
      vertex 0 0 0
      vertex 1 0 0
      vertex 1 1 0
    endloop
  endfacet
endsolid cube
"""

        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False, mode="w") as stl_file:
            stl_file.write(ascii_stl)
            stl_path = stl_file.name

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as png_file:
            png_path = png_file.name

        try:
            result = generate_stl_thumbnail(stl_path, png_path, size=(128, 128))
            assert result is True
            assert os.path.exists(png_path)
        finally:
            os.unlink(stl_path)
            if os.path.exists(png_path):
                os.unlink(png_path)


class TestMeshSimplification:
    """Tests for mesh simplification with large files."""

    def test_simplification_threshold(self):
        """Test that MAX_VERTICES constant is defined."""
        from backend.app.services.stl_thumbnail import MAX_VERTICES

        assert MAX_VERTICES == 100000

    def test_large_mesh_handling(self):
        """Test that large meshes are simplified (mocked)."""
        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        # Create a mock mesh with many vertices
        with patch("trimesh.load") as mock_load:
            mock_mesh = MagicMock()
            mock_mesh.vertices = MagicMock()
            mock_mesh.vertices.__len__ = MagicMock(return_value=200000)  # Over threshold
            mock_mesh.faces = MagicMock()
            mock_mesh.faces.__len__ = MagicMock(return_value=400000)
            mock_simplified = MagicMock()
            mock_simplified.vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
            mock_simplified.faces = [[0, 1, 2]]
            mock_mesh.simplify_quadric_decimation.return_value = mock_simplified
            mock_load.return_value = mock_mesh

            with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as stl_file:
                stl_file.write(b"dummy")
                stl_path = stl_file.name

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as png_file:
                png_path = png_file.name

            try:
                # This will fail but we can verify simplification was called
                generate_stl_thumbnail(stl_path, png_path)
                # The mock should have been called for simplification
                mock_mesh.simplify_quadric_decimation.assert_called()
            finally:
                os.unlink(stl_path)
                if os.path.exists(png_path):
                    os.unlink(png_path)
