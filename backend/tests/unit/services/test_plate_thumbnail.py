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

    def test_injected_thumbnail_is_shaded_not_flat(self, distinct_surface_tones):
        """Injected plate renders must be lit, same as library thumbnails (#2816).

        The archive card and the File Manager tile show the same model through
        two different renderers; if only one of them is lit they disagree.
        """
        from backend.app.services.plate_thumbnail import inject_plate_thumbnails_if_missing

        fixture = _build_sliced_3mf(plate_ids=[1], with_thumbnails=set())
        result = inject_plate_thumbnails_if_missing(fixture)

        with zipfile.ZipFile(io.BytesIO(result), "r") as zf:
            large = zf.read("Metadata/plate_1.png")

        # _build_sliced_3mf embeds a cube: three faces visible, three tones.
        assert distinct_surface_tones(large) >= 3

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


# --- Bambu Studio / OrcaSlicer layout (#3135) ------------------------------
#
# Those slicers keep every mesh in ``3D/Objects/*.model`` and write each
# instance in ``3D/3dmodel.model`` as its own ``<object>`` holding one
# ``<component p:path=...>``. trimesh's reader re-parsed the referenced file for
# every such component and appended its meshes again each time, so N copies of
# a part came back with N² copies of its triangles — 25 bins took 8.4 GB to
# render and OOM-killed the server.

_NS = (
    'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
    'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/1015/06" requiredextensions="p"'
)
_IDENTITY = "1 0 0 0 1 0 0 0 1 0 0 0"


def _box_mesh_xml(size: float = 10.0) -> tuple[str, int]:
    import trimesh

    box = trimesh.creation.box(extents=(size, size, size))
    verts = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in box.vertices)
    tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in box.faces)
    return f"<mesh><vertices>{verts}</vertices><triangles>{tris}</triangles></mesh>", len(box.faces)


def _bambu_layout_3mf(
    placements: list[tuple[str, str]],
    parts: dict[str, float] | None = None,
    extra_root_objects: str = "",
    extra_items: str = "",
    mesh_xml: str | None = None,
) -> bytes:
    """A sliced 3MF in the Bambu/Orca layout.

    ``parts``: object id -> box size, all in ``3D/Objects/object_1.model``.
    ``placements``: (part id, build-item transform), one wrapper object each.
    ``mesh_xml``: a ``<mesh>`` to use for every part instead of a box.
    """
    parts = parts or {"1": 10.0}
    objects = "".join(
        f'<object id="{oid}" type="model">{mesh_xml or _box_mesh_xml(size)[0]}</object>' for oid, size in parts.items()
    )
    part_file = f'<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" {_NS}><resources>{objects}</resources><build/></model>'
    wrappers = "".join(
        f'<object id="{100 + i}" type="model"><components>'
        f'<component p:path="/3D/Objects/object_1.model" objectid="{part}" transform="{_IDENTITY}"/>'
        f"</components></object>"
        for i, (part, _t) in enumerate(placements)
    )
    items = "".join(f'<item objectid="{100 + i}" transform="{t}"/>' for i, (_p, t) in enumerate(placements))
    root = (
        f'<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" {_NS}>'
        f"<resources>{wrappers}{extra_root_objects}</resources><build>{items}{extra_items}</build></model>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/Objects/object_1.model", part_file)
        zf.writestr("3D/3dmodel.model", root)
        zf.writestr("Metadata/plate_1.gcode", b"; dummy gcode\n")
    return buf.getvalue()


def _grid(n: int) -> list[str]:
    return [f"1 0 0 0 1 0 0 0 1 {20 * (i % 5)} {20 * (i // 5)} 5" for i in range(n)]


def _geometry(blob: bytes):
    import trimesh

    from backend.app.services.plate_thumbnail import _load_plate_geometry

    with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
        return _load_plate_geometry(zf, trimesh, lambda *_a: None)


@pytest.mark.skipif(not _trimesh_available(), reason="trimesh not installed")
class TestBambuLayoutGeometry:
    def test_each_instance_is_placed_once_not_n_squared(self):
        _, box_faces = _box_mesh_xml()
        blob = _bambu_layout_3mf([("1", t) for t in _grid(25)])

        vertices, faces = _geometry(blob)

        # 25 boxes of 12 faces. trimesh returned 25 * 25 * 12 = 7500.
        assert len(faces) == 25 * box_faces
        # Laid out on the grid, not stacked: 5 columns 20 mm apart plus a 10 mm box.
        extent = vertices.max(axis=0) - vertices.min(axis=0)
        assert extent[0] == pytest.approx(4 * 20 + 10)
        assert extent[1] == pytest.approx(4 * 20 + 10)

    def test_a_component_places_only_the_object_it_names(self):
        # One object file holding three parts. trimesh appended all three to
        # every part it referenced, so each placement drew the whole file.
        _, box_faces = _box_mesh_xml()
        blob = _bambu_layout_3mf(
            [("1", _grid(1)[0]), ("3", "1 0 0 0 1 0 0 0 1 50 0 5")],
            parts={"1": 10.0, "2": 40.0, "3": 10.0},
        )

        vertices, faces = _geometry(blob)

        assert len(faces) == 2 * box_faces
        # Part 2 (40 mm) is never placed, so nothing is that tall.
        assert (vertices.max(axis=0) - vertices.min(axis=0))[2] == pytest.approx(10)

    def test_a_mirrored_instance_keeps_its_faces_pointing_out(self):
        import numpy as np
        import trimesh

        blob = _bambu_layout_3mf([("1", "-1 0 0 0 1 0 0 0 1 0 0 5")])

        vertices, faces = _geometry(blob)

        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        outward = mesh.triangles.mean(axis=1) - mesh.vertices.mean(axis=0)
        assert (np.einsum("ij,ij->i", mesh.face_normals, outward) > 0).all()

    def test_a_self_referencing_component_terminates(self):
        # Object 500 places the box and then itself; the walk must stop at the
        # loop rather than recurse, and still draw the box it reached once.
        loop = (
            '<object id="500" type="model"><components>'
            f'<component p:path="/3D/Objects/object_1.model" objectid="1" transform="{_IDENTITY}"/>'
            f'<component objectid="500" transform="1 0 0 0 1 0 0 0 1 30 0 0"/></components></object>'
        )
        blob = _bambu_layout_3mf(
            [],
            extra_root_objects=loop,
            extra_items=f'<item objectid="500" transform="{_IDENTITY}"/>',
        )

        _vertices, faces = _geometry(blob)

        assert len(faces) == _box_mesh_xml()[1]

    def test_a_build_item_can_name_the_file_its_object_lives_in(self):
        # Production extension: ``p:path`` on the item itself, no wrapper object.
        blob = _bambu_layout_3mf(
            [],
            extra_items=f'<item p:path="/3D/Objects/object_1.model" objectid="1" transform="{_IDENTITY}"/>',
        )

        _vertices, faces = _geometry(blob)

        assert len(faces) == _box_mesh_xml()[1]

    def test_a_component_pointing_at_a_missing_file_is_skipped(self):
        blob = _bambu_layout_3mf([("1", _grid(1)[0])])
        broken = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(broken, "w") as dst:
            for item in src.infolist():
                if item.filename != "3D/Objects/object_1.model":
                    dst.writestr(item, src.read(item.filename))

        assert _geometry(broken.getvalue()) is None

    def test_many_instances_are_decimated_to_the_face_budget(self, monkeypatch):
        import trimesh

        import backend.app.services.plate_thumbnail as pt

        # 25 spheres of 5120 faces against a budget of 1000 per copy.
        sphere = trimesh.creation.icosphere(subdivisions=4)
        verts = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in sphere.vertices)
        tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in sphere.faces)
        blob = _bambu_layout_3mf(
            [("1", t) for t in _grid(25)],
            mesh_xml=f"<mesh><vertices>{verts}</vertices><triangles>{tris}</triangles></mesh>",
        )
        monkeypatch.setattr(pt, "_RENDER_FACE_BUDGET", 25 * 1000)

        _vertices, faces = _geometry(blob)

        # Decimated once, to its share of the budget, then placed 25 times.
        assert len(faces) <= 25 * 1100
        assert len(faces) % 25 == 0

    def test_a_decimation_that_fails_is_still_held_to_the_ceiling(self, monkeypatch):
        import trimesh

        import backend.app.services.plate_thumbnail as pt

        # The budget would bring 25 spheres to 25k faces, well under the ceiling,
        # so the up-front check passes. Decimation then fails and leaves each
        # sphere whole: 128k faces, which the render must not be handed.
        sphere = trimesh.creation.icosphere(subdivisions=4)
        verts = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in sphere.vertices)
        tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in sphere.faces)
        blob = _bambu_layout_3mf(
            [("1", t) for t in _grid(25)],
            mesh_xml=f"<mesh><vertices>{verts}</vertices><triangles>{tris}</triangles></mesh>",
        )
        monkeypatch.setattr(pt, "_RENDER_FACE_BUDGET", 25 * 1000)
        monkeypatch.setattr(pt, "_MAX_PLACED_FACES", 50_000)

        def fail(*_a, **_k):
            raise RuntimeError("decimation failed")

        monkeypatch.setattr(trimesh.Trimesh, "simplify_quadric_decimation", fail)

        assert _geometry(blob) is None

    def test_over_the_ceiling_skips_the_thumbnail_and_keeps_the_3mf(self, monkeypatch):
        import backend.app.services.plate_thumbnail as pt
        from backend.app.services.plate_thumbnail import inject_plate_thumbnails_if_missing

        # 25 boxes can't go below 12 faces each, so a ceiling under 300 is
        # unreachable however hard the budget decimates.
        monkeypatch.setattr(pt, "_MIN_FACES_PER_MESH", 12)
        monkeypatch.setattr(pt, "_MAX_PLACED_FACES", 100)
        blob = _bambu_layout_3mf([("1", t) for t in _grid(25)])

        assert inject_plate_thumbnails_if_missing(blob) is blob

    def test_injects_thumbnails_for_a_bambu_layout_plate(self):
        from backend.app.services.plate_thumbnail import inject_plate_thumbnails_if_missing

        blob = _bambu_layout_3mf([("1", t) for t in _grid(25)])
        out = inject_plate_thumbnails_if_missing(blob)

        assert {"Metadata/plate_1.png", "Metadata/plate_1_small.png"} <= _names_in_zip(out)
