"""Unit tests for the plate-thumbnail injection service.

The service backfills ``Metadata/plate_N.png`` when the sidecar CLI
(BS or Orca) skipped it in --slice --export-3mf. Each test builds a
synthetic sliced-3MF fixture: a trimesh-exported cube as
``3D/3dmodel.model`` plus dummy ``Metadata/plate_1.gcode`` so the
inject function sees it as "plate 1, no thumbnail."
"""

from __future__ import annotations

import io
import zipfile

import pytest


def _trimesh_available() -> bool:
    try:
        import trimesh  # noqa: F401

        return True
    except ImportError:
        return False


def _build_sliced_3mf(
    *,
    plate_ids: list[int],
    with_thumbnails: set[int] | None = None,
    with_model: bool = True,
) -> bytes:
    """Build a synthetic sliced .gcode.3mf for injection tests.

    - ``plate_ids``: which Metadata/plate_N.gcode entries to write
    - ``with_thumbnails``: subset of plate_ids that ALSO get plate_N.png +
      plate_N_small.png (simulates a desktop-Studio-style slice where the
      slicer did embed thumbnails)
    - ``with_model``: when True, embeds a trimesh-rendered cube as
      ``3D/3dmodel.model`` so the injector can reload + render it
    """
    import trimesh

    have_thumbs = with_thumbnails or set()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if with_model:
            # trimesh's primitives.Box exports cleanly to 3MF.
            mesh = trimesh.creation.box(extents=(10.0, 10.0, 10.0))
            model_bytes = mesh.export(file_type="3mf")
            # trimesh.export(file_type='3mf') returns a full 3MF zip; we
            # want just the embedded 3D/3dmodel.model XML so we can place
            # it under the sliced-3MF layout.
            with zipfile.ZipFile(io.BytesIO(model_bytes), "r") as inner:
                model_xml = inner.read("3D/3dmodel.model")
            zf.writestr("3D/3dmodel.model", model_xml)
        for n in plate_ids:
            # Dummy gcode is enough for the injector — it only matches the
            # filename to detect plate slots, not the content.
            zf.writestr(f"Metadata/plate_{n}.gcode", b"; dummy gcode\n")
            if n in have_thumbs:
                # 1x1 transparent PNG — pre-existing thumb sentinel; the
                # injector should preserve its bytes verbatim.
                zf.writestr(f"Metadata/plate_{n}.png", _PIXEL_PNG)
                zf.writestr(f"Metadata/plate_{n}_small.png", _PIXEL_PNG)
    return buf.getvalue()


# 1x1 transparent PNG used as a pre-existing thumbnail sentinel.
_PIXEL_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
    b"\x00\x00\x00\x0dIDATx\x9cc\xfc\xff\xff?\x03\x00\x05\xfe\x02\xfe"
    b"\xdc\xccY\xe7"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _names_in_zip(blob: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
        return set(zf.namelist())


@pytest.mark.skipif(not _trimesh_available(), reason="trimesh not installed")
class TestInjectPlateThumbnails:
    """Behaviour around when the injector renders vs returns the input."""

    def test_returns_input_unchanged_when_all_plates_have_thumbnails(self):
        """Desktop-Studio path: every plate already has plate_N.png — no work."""
        from backend.app.services.plate_thumbnail import inject_plate_thumbnails_if_missing

        fixture = _build_sliced_3mf(plate_ids=[1], with_thumbnails={1})
        result = inject_plate_thumbnails_if_missing(fixture)
        # Same object identity — the fast path returns the input verbatim
        # so the SliceResult._replace upstream never pays for a copy on the
        # common already-embedded case.
        assert result is fixture

    def test_injects_both_sizes_when_thumbnail_missing(self):
        """BS/Orca sidecar path: plate_1.gcode present, plate_1.png absent."""
        from backend.app.services.plate_thumbnail import inject_plate_thumbnails_if_missing

        fixture = _build_sliced_3mf(plate_ids=[1], with_thumbnails=set())
        before = _names_in_zip(fixture)
        assert "Metadata/plate_1.png" not in before

        result = inject_plate_thumbnails_if_missing(fixture)
        after = _names_in_zip(result)
        assert "Metadata/plate_1.png" in after
        assert "Metadata/plate_1_small.png" in after

    def test_injected_pngs_have_expected_dimensions(self):
        """Sanity-check the render geometry — 512x512 + 128x128, RGBA PNG."""
        from backend.app.services.plate_thumbnail import inject_plate_thumbnails_if_missing

        fixture = _build_sliced_3mf(plate_ids=[1], with_thumbnails=set())
        result = inject_plate_thumbnails_if_missing(fixture)

        with zipfile.ZipFile(io.BytesIO(result), "r") as zf:
            large = zf.read("Metadata/plate_1.png")
            small = zf.read("Metadata/plate_1_small.png")

        assert large.startswith(b"\x89PNG\r\n\x1a\n")
        assert small.startswith(b"\x89PNG\r\n\x1a\n")
        # PNG IHDR dimensions live at byte offsets 16..23 (big-endian width,
        # then big-endian height). matplotlib's bbox_inches='tight' shaves a
        # few pixels off, so assert "close to" rather than exact.
        import struct

        large_w, large_h = struct.unpack(">II", large[16:24])
        small_w, small_h = struct.unpack(">II", small[16:24])
        assert 480 <= large_w <= 540 and 480 <= large_h <= 540
        assert 100 <= small_w <= 140 and 100 <= small_h <= 140

    def test_injects_for_every_missing_plate_in_multi_plate_3mf(self):
        """Three plates, plate_2 already has a thumbnail; only plates 1 + 3 get rendered."""
        from backend.app.services.plate_thumbnail import inject_plate_thumbnails_if_missing

        fixture = _build_sliced_3mf(plate_ids=[1, 2, 3], with_thumbnails={2})
        result = inject_plate_thumbnails_if_missing(fixture)
        after = _names_in_zip(result)

        for n in (1, 2, 3):
            assert f"Metadata/plate_{n}.png" in after
            assert f"Metadata/plate_{n}_small.png" in after

        # Plate 2 had a pre-existing thumbnail — the inject must NOT clobber
        # it. The sentinel _PIXEL_PNG bytes should survive verbatim.
        with zipfile.ZipFile(io.BytesIO(result), "r") as zf:
            assert zf.read("Metadata/plate_2.png") == _PIXEL_PNG
            assert zf.read("Metadata/plate_2_small.png") == _PIXEL_PNG

    def test_returns_input_when_no_model_file_in_3mf(self):
        """No 3D/3dmodel.model → render is impossible; degrade gracefully."""
        from backend.app.services.plate_thumbnail import inject_plate_thumbnails_if_missing

        fixture = _build_sliced_3mf(plate_ids=[1], with_thumbnails=set(), with_model=False)
        result = inject_plate_thumbnails_if_missing(fixture)
        # Same object identity — early-out before render.
        assert result is fixture

    def test_returns_input_when_not_a_zip(self):
        """Non-zip input must not crash — degrade to passthrough."""
        from backend.app.services.plate_thumbnail import inject_plate_thumbnails_if_missing

        garbage = b"not a zip"
        assert inject_plate_thumbnails_if_missing(garbage) is garbage

    def test_idempotent_on_second_pass(self):
        """Re-running on a previously-injected 3MF must be a no-op."""
        from backend.app.services.plate_thumbnail import inject_plate_thumbnails_if_missing

        fixture = _build_sliced_3mf(plate_ids=[1], with_thumbnails=set())
        once = inject_plate_thumbnails_if_missing(fixture)
        twice = inject_plate_thumbnails_if_missing(once)
        # Same object identity — second pass hits the no-op fast path
        # because every plate now has its plate_N.png.
        assert twice is once
