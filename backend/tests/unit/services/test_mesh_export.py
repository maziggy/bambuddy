"""Unit tests for the mesh export service."""

import struct
import tempfile
import zipfile
from pathlib import Path

import pytest

from backend.app.services.mesh_export import (
    MAX_VERTICES,
    MeshExportError,
    export_mesh_stl,
    is_exportable,
)


def _check_trimesh_available():
    """Check if trimesh is available for import."""
    try:
        import trimesh  # noqa: F401

        return True
    except ImportError:
        return False


def _write_binary_stl(path: Path, triangles):
    """Write a minimal binary STL so tests do not depend on a fixture file."""
    with path.open("wb") as fh:
        fh.write(b"\0" * 80)
        fh.write(struct.pack("<I", len(triangles)))
        for tri in triangles:
            fh.write(struct.pack("<3f", 0.0, 0.0, 1.0))
            for vertex in tri:
                fh.write(struct.pack("<3f", *vertex))
            fh.write(struct.pack("<H", 0))


def _tetrahedron():
    """Four facets enclosing a volume — the smallest mesh worth exporting."""
    a, b, c, d = (0, 0, 0), (10, 0, 0), (5, 9, 0), (5, 3, 8)
    return [(a, b, c), (a, b, d), (b, c, d), (c, a, d)]


class TestIsExportable:
    """Which filenames the service will accept."""

    def test_model_containers_are_exportable(self):
        for name in ("part.3mf", "part.stl", "part.obj", "part.ply"):
            assert is_exportable(name), name

    def test_extension_check_is_case_insensitive(self):
        assert is_exportable("PART.STL")
        assert is_exportable("Part.3MF")

    def test_a_sliced_three_mf_is_refused(self):
        """A `.gcode.3mf` ends in `.3mf` and must still be refused.

        It carries toolpaths, so a G-code viewer is the right way to look at it — and
        trimesh's 3MF loader raises `KeyError: 'world'` on Bambu Studio's sliced output
        anyway, so accepting it would only produce a confusing 422 later.
        """
        assert not is_exportable("part.gcode.3mf")
        assert not is_exportable("PART.GCODE.3MF")

    def test_unrelated_types_are_refused(self):
        for name in ("notes.txt", "archive.zip", "photo.png", "noextension"):
            assert not is_exportable(name), name


class TestExportMeshStl:
    """Exporting geometry, and refusing clearly when there is none."""

    def test_refuses_an_unsupported_type_without_touching_disk(self):
        """The type check precedes the existence check, so a `.txt` that does not
        exist is still reported as the wrong TYPE rather than as missing."""
        with pytest.raises(MeshExportError) as excinfo:
            export_mesh_stl(Path("/nonexistent/notes.txt"))
        assert "model container" in str(excinfo.value)

    def test_refuses_a_missing_file(self):
        with pytest.raises(MeshExportError) as excinfo:
            export_mesh_stl(Path("/nonexistent/part.stl"))
        assert "not found" in str(excinfo.value).lower()

    def test_refuses_a_file_too_small_to_hold_a_mesh(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stub = Path(tmpdir) / "stub.stl"
            stub.write_bytes(b"short")
            with pytest.raises(MeshExportError) as excinfo:
                export_mesh_stl(stub)
            assert "too small" in str(excinfo.value).lower()

    @pytest.mark.skipif(not _check_trimesh_available(), reason="trimesh not installed")
    def test_refuses_a_file_that_is_not_a_model(self):
        """Large enough to pass the size gate, but not parseable. The client gets a
        sentence, not a parser internal."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bogus = Path(tmpdir) / "bogus.stl"
            bogus.write_bytes(b"this is not an STL file " * 20)
            with pytest.raises(MeshExportError):
                export_mesh_stl(bogus)

    @pytest.mark.skipif(not _check_trimesh_available(), reason="trimesh not installed")
    def test_exports_a_binary_stl_from_an_stl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "tetra.stl"
            _write_binary_stl(src, _tetrahedron())

            blob = export_mesh_stl(src)

            assert isinstance(blob, bytes)
            # Binary STL: 80-byte header, a uint32 count, then 50 bytes per triangle.
            assert len(blob) >= 84
            (count,) = struct.unpack("<I", blob[80:84])
            assert count == 4
            assert len(blob) == 84 + count * 50

    @pytest.mark.skipif(not _check_trimesh_available(), reason="trimesh not installed")
    def test_the_export_is_loadable_again(self):
        """Round-trip: whatever we hand a client must parse as a mesh.

        The point of this endpoint is that a caller needs no container handling, so an
        output that only *looks* like an STL would defeat it.
        """
        import trimesh

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "tetra.stl"
            _write_binary_stl(src, _tetrahedron())
            out = Path(tmpdir) / "roundtrip.stl"
            out.write_bytes(export_mesh_stl(src))

            reloaded = trimesh.load(str(out), force="mesh")
            assert len(reloaded.vertices) > 0
            assert len(reloaded.faces) == 4

    @pytest.mark.skipif(not _check_trimesh_available(), reason="trimesh not installed")
    def test_refuses_a_three_mf_that_is_not_really_a_model(self):
        """A ZIP with a `.3mf` name and no model part inside.

        Worth its own case because the type check passes on the NAME — so this is the
        path where the loader, not the filename, has to be what refuses.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = Path(tmpdir) / "fake.3mf"
            with zipfile.ZipFile(fake, "w") as zf:
                zf.writestr("hello.txt", "not a model " * 40)
            with pytest.raises(MeshExportError):
                export_mesh_stl(fake)


class TestDecimation:
    """The vertex budget, which exists so a client is never handed an unusable mesh."""

    def test_a_small_mesh_is_left_alone(self):
        """Below the budget nothing is touched, so a simple model exports exactly."""
        from backend.app.services.mesh_export import _decimate_if_needed

        class FakeMesh:
            vertices = [(0, 0, 0)] * 10

        mesh = FakeMesh()
        assert _decimate_if_needed(mesh) is mesh

    def test_a_failed_simplification_returns_the_original(self):
        """A mesh too awkward to simplify is still better delivered whole than
        refused — the client can decide what to do with it."""
        from backend.app.services.mesh_export import _decimate_if_needed

        class ExplodingMesh:
            vertices = [(0, 0, 0)] * (MAX_VERTICES + 1)

            def simplify_quadric_decimation(self, _reduction):
                raise RuntimeError("no decimator available")

        mesh = ExplodingMesh()
        assert _decimate_if_needed(mesh) is mesh

    def test_a_simplification_that_empties_the_mesh_returns_the_original(self):
        """Guards the case that would otherwise export zero triangles: a decimator
        that "succeeds" with nothing left is worse than no decimation."""
        from backend.app.services.mesh_export import _decimate_if_needed

        class EmptyingMesh:
            vertices = [(0, 0, 0)] * (MAX_VERTICES + 1)

            def simplify_quadric_decimation(self, _reduction):
                class Empty:
                    vertices = []

                return Empty()

        mesh = EmptyingMesh()
        assert _decimate_if_needed(mesh) is mesh

    def test_a_successful_simplification_is_used(self):
        from backend.app.services.mesh_export import _decimate_if_needed

        class Reduced:
            vertices = [(0, 0, 0)] * 100

        class DenseMesh:
            vertices = [(0, 0, 0)] * (MAX_VERTICES * 2)

            def simplify_quadric_decimation(self, _reduction):
                return Reduced()

        result = _decimate_if_needed(DenseMesh())
        assert isinstance(result, Reduced)
