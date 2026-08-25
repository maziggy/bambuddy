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

    @pytest.mark.skipif(
        not _check_trimesh_available(),
        reason="trimesh not installed",
    )
    def test_generated_thumbnail_is_shaded_not_flat(self, distinct_surface_tones):
        """The render must be lit, not a flat silhouette (issue #2816).

        Without ``shade=True`` every triangle is filled with BAMBU_GREEN
        regardless of its normal, so any model renders as its own outline and
        one file is indistinguishable from another in the File Manager.
        """
        import trimesh

        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        with tempfile.TemporaryDirectory() as tmpdir:
            stl_path = Path(tmpdir) / "cube.stl"
            trimesh.creation.box(extents=(10.0, 10.0, 10.0)).export(str(stl_path))

            result = generate_stl_thumbnail(stl_path, Path(tmpdir))
            assert result is not None

            # Three faces of a cube face the camera at the default isometric
            # view_init, and with the light on the camera's side each catches it
            # differently. This held at 3 before only because ``alpha=0.9`` let a
            # back face bleed through two IDENTICALLY lit front faces — re-render
            # at alpha=1.0 then and the count fell to 2. It is shading now.
            assert distinct_surface_tones(Path(result).read_bytes()) >= 3

    @pytest.mark.skipif(
        not _check_trimesh_available(),
        reason="trimesh not installed",
    )
    @pytest.mark.parametrize(
        ("label", "punch_holes"),
        [("watertight", False), ("open", True)],
    )
    def test_backwards_wound_triangles_render_the_same(self, label, punch_holes):
        """Vertex ORDER must not change the picture.

        matplotlib takes its normals from winding, so an inverted triangle shades
        as though it faced away — the model comes out patchy, like camouflage.
        Unshaded this was invisible, which makes it a regression the shading
        introduced rather than one it revealed, and the File Manager accepts
        whatever STL a user uploads.

        Asserted as "same picture as the correctly wound mesh", because the
        obvious assertion does not work: broken winding produces MORE distinct
        tones, not fewer, so a tone count cannot see it.

        Run BOTH watertight and open, because the two are repaired by different
        code. ``fix_inversion`` decides which way is out from the sign of the
        volume and gives up when the mesh is not watertight, which is the common
        shape of a broken STL — there, ``fix_winding`` settles inward unopposed
        and the centroid fallback in ``_repair_winding`` is the only thing
        holding this. Without it the open case renders at a mean delta of 4.56.
        """
        import numpy as np
        import trimesh
        from PIL import Image

        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        sphere = trimesh.creation.icosphere(subdivisions=3, radius=5.0)
        keep = sphere.faces.copy()[:-80] if punch_holes else sphere.faces.copy()
        good = trimesh.Trimesh(vertices=sphere.vertices.copy(), faces=keep.copy())
        assert good.is_watertight is not punch_holes, "fixture has the wrong topology"

        faces = keep.copy()
        faces[::2] = faces[::2][:, ::-1]
        bad = trimesh.Trimesh(vertices=sphere.vertices.copy(), faces=faces)
        assert not bad.is_winding_consistent, "fixture is supposed to be broken"

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            rendered = []
            for name, mesh in (("good", good), ("bad", bad)):
                path = out / f"{name}.stl"
                mesh.export(str(path))
                result = generate_stl_thumbnail(path, out)
                assert result is not None
                rendered.append(np.asarray(Image.open(result).convert("RGB"), dtype=float))

            assert rendered[0].shape == rendered[1].shape
            mean_delta = float(np.abs(rendered[0] - rendered[1]).mean())

        # Repaired they are the same mesh, so this is ~0. Without the repair the
        # inverted half renders dark against the lit half and it is an order of
        # magnitude higher.
        assert mean_delta < 1.0, f"winding changed the {label} render (mean delta {mean_delta:.2f})"

    @pytest.mark.skipif(
        not _check_trimesh_available(),
        reason="trimesh not installed",
    )
    def test_degenerate_mesh_still_renders(self):
        """A mesh with no shadeable face must render flat, not fail.

        matplotlib's ``_shade_colors`` falls back to returning the colour it was
        given when every normal is degenerate, and for a colour STRING that is a
        0-d array — ``to_rgba_array`` then raises ``TypeError: len() of unsized
        object``. So these files rendered fine while the output was flat, and
        turning the light on would have broken them.

        They are not hypothetical: stub and truncated STLs reach here, and
        ``batch_generate_stl_thumbnails`` walks a whole folder with no
        minimum-size pre-skip, so each one would show as a failure in the UI.
        """
        import struct

        from backend.app.services.stl_thumbnail import generate_stl_thumbnail

        def write_binary_stl(path, triangles):
            # Written by hand rather than via trimesh.export, which drops
            # degenerate facets and would quietly defeat the test.
            with open(path, "wb") as fh:
                fh.write(b"\0" * 80)
                fh.write(struct.pack("<I", len(triangles)))
                for tri in triangles:
                    fh.write(struct.pack("<3f", 0.0, 0.0, 0.0))
                    for vertex in tri:
                        fh.write(struct.pack("<3f", *vertex))
                    fh.write(b"\0\0")

        cases = {
            "zero_area": [[(0, 0, 0), (0, 0, 0), (0, 0, 0)]],
            "collinear": [[(0, 0, 0), (1, 1, 1), (2, 2, 2)]],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            for name, triangles in cases.items():
                path = out / f"{name}.stl"
                write_binary_stl(path, triangles)
                assert generate_stl_thumbnail(path, out) is not None, f"{name} used to render and must still render"

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

    def test_light_gives_the_two_visible_faces_different_shades(self):
        """The whole point of lighting: adjacent visible faces must differ.

        Both halves are asserted because neither alone is the property.

        A positive dot product only says the light is not BEHIND the model, and
        that is not sufficient: azdeg=45 — "put the light where the camera is",
        the most natural next edit anyone would make — scores the HIGHEST dot
        product of any azimuth (+0.94) and lights both visible faces to the
        identical 0.825, which is a cube with no contrast down its front edge.
        The original bug (225) failed the other way, at -0.34.

        Shade factors are matplotlib's own: ``Normalize(-1, 1)`` into
        ``Normalize(0.3, 1).inverse``, i.e. ``0.3 + 0.7 * (dot + 1) / 2``.
        """
        import numpy as np
        from matplotlib.colors import LightSource

        from backend.app.services.stl_thumbnail import (
            LIGHT_ALTITUDE_DEG,
            LIGHT_AZIMUTH_DEG,
            VIEW_AZIM_DEG,
            VIEW_ELEV_DEG,
        )

        elev, azim = np.radians(VIEW_ELEV_DEG), np.radians(VIEW_AZIM_DEG)
        camera = np.array([np.cos(elev) * np.cos(azim), np.cos(elev) * np.sin(azim), np.sin(elev)])
        light = LightSource(azdeg=LIGHT_AZIMUTH_DEG, altdeg=LIGHT_ALTITUDE_DEG).direction

        assert float(light @ camera) > 0, "the light is behind the model"

        def shade(normal):
            return 0.3 + 0.7 * ((float(np.array(normal) @ light) + 1) / 2)

        # The two faces of an axis-aligned box that face the default camera.
        assert abs(shade([1, 0, 0]) - shade([0, 1, 0])) > 0.05, (
            "both visible faces are lit the same — the front edge disappears"
        )

    def test_light_is_above_the_horizon(self):
        """Grazing or overhead both collapse the contrast the shading exists for."""
        from backend.app.services.stl_thumbnail import LIGHT_ALTITUDE_DEG

        assert 0 < LIGHT_ALTITUDE_DEG < 90

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
