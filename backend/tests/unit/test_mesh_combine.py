"""Unit tests for combining STLs into one multi-object 3MF."""

import io
import re
import zipfile

import pytest
import trimesh

from backend.app.services import mesh_combine, stl_thumbnail
from backend.app.services.mesh_combine import (
    LAYOUT_GAP_MM,
    MAX_COMBINE_INSTANCES,
    THUMBNAIL_FACE_BUDGET,
    THUMBNAIL_PATH,
    CombinePart,
    MeshCombineError,
    combine_parts_to_3mf,
    layout_offsets,
    thumbnail_faces_per_copy,
)


@pytest.fixture
def box_stl(tmp_path):
    path = tmp_path / "box.stl"
    path.write_bytes(trimesh.creation.box((30, 20, 10)).export(file_type="stl"))
    return path


@pytest.fixture
def cyl_stl(tmp_path):
    path = tmp_path / "cyl.stl"
    path.write_bytes(trimesh.creation.cylinder(radius=12, height=25).export(file_type="stl"))
    return path


def _model_xml(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"} <= set(zf.namelist())
        return zf.read("3D/3dmodel.model").decode("utf-8")


def test_one_object_per_source_one_item_per_copy(box_stl, cyl_stl):
    data = combine_parts_to_3mf([CombinePart("box.stl", box_stl, 2), CombinePart("cyl.stl", cyl_stl, 3)])
    xml = _model_xml(data)

    # Geometry is stored once per source; copies are build items.
    assert re.findall(r'<object id="(\d+)" name="([^"]+)"', xml) == [("1", "box"), ("2", "cyl")]
    assert re.findall(r'<item objectid="(\d+)"', xml) == ["1", "1", "2", "2", "2"]


def test_output_reloads_with_every_copy_and_sits_on_the_bed(box_stl, cyl_stl):
    data = combine_parts_to_3mf([CombinePart("box.stl", box_stl, 2), CombinePart("cyl.stl", cyl_stl, 1)])
    scene = trimesh.load(io.BytesIO(data), file_type="3mf")
    meshes = scene.dump()
    assert len(meshes) == 3
    for mesh in meshes:
        assert mesh.bounds[0][2] == pytest.approx(0.0, abs=1e-6)

    # Pre-placed copies must not overlap each other.
    boxes = [m.bounds for m in meshes]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            separated = a[1][0] <= b[0][0] or b[1][0] <= a[0][0] or a[1][1] <= b[0][1] or b[1][1] <= a[0][1]
            assert separated


def test_layout_is_centred_and_gapped():
    offsets = layout_offsets([(10.0, 10.0), (10.0, 10.0)])
    (ax, ay), (bx, by) = offsets
    assert abs(bx - ax) == pytest.approx(10.0 + LAYOUT_GAP_MM) or abs(by - ay) == pytest.approx(10.0 + LAYOUT_GAP_MM)
    xs = [x for x, _ in offsets] + [x + 10.0 for x, _ in offsets]
    ys = [y for _, y in offsets] + [y + 10.0 for _, y in offsets]
    assert (min(xs) + max(xs)) / 2 == pytest.approx(0.0)
    assert (min(ys) + max(ys)) / 2 == pytest.approx(0.0)


def test_layout_empty():
    assert layout_offsets([]) == []


def test_object_names_are_xml_escaped(box_stl):
    # The name is the library filename, not the on-disk path.
    xml = _model_xml(combine_parts_to_3mf([CombinePart('a&b "q".stl', box_stl, 1)]))
    assert 'name="a&amp;b &quot;q&quot;"' in xml


def test_rejects_no_parts():
    with pytest.raises(MeshCombineError):
        combine_parts_to_3mf([])


def test_rejects_too_many_instances(box_stl):
    with pytest.raises(MeshCombineError, match="Too many objects"):
        combine_parts_to_3mf([CombinePart("box.stl", box_stl, MAX_COMBINE_INSTANCES + 1)])


def test_rejects_zero_copies(box_stl):
    with pytest.raises(MeshCombineError):
        combine_parts_to_3mf([CombinePart("box.stl", box_stl, 0)])


def test_rejects_empty_mesh(tmp_path):
    empty = tmp_path / "empty.stl"
    empty.write_bytes(b"solid empty\nendsolid empty\n")
    with pytest.raises(MeshCombineError, match="empty.stl"):
        combine_parts_to_3mf([CombinePart("empty.stl", empty, 1)])


# --- embedded thumbnail --------------------------------------------------


def test_thumbnail_is_embedded_and_found_by_the_library_parser(tmp_path, box_stl, cyl_stl):
    from backend.app.services.archive import ThreeMFParser

    data = combine_parts_to_3mf([CombinePart("box.stl", box_stl, 2), CombinePart("cyl.stl", cyl_stl, 1)])
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        png = zf.read(THUMBNAIL_PATH)
        rels = zf.read("_rels/.rels").decode()
        types = zf.read("[Content_Types].xml").decode()
    assert png.startswith(b"\x89PNG")
    assert f'Target="/{THUMBNAIL_PATH}"' in rels and "relationships/metadata/thumbnail" in rels
    assert 'Extension="png"' in types

    out = tmp_path / "combined.3mf"
    out.write_bytes(data)
    assert ThreeMFParser(out).parse().get("_thumbnail_data") == png


def test_preview_stays_within_the_face_budget_however_many_copies(tmp_path, monkeypatch):
    # 20,480 faces per source: 100 copies would be 2M faces unsimplified.
    ball = tmp_path / "ball.stl"
    ball.write_bytes(trimesh.creation.icosphere(subdivisions=5).export(file_type="stl"))
    drawn = []
    monkeypatch.setattr(stl_thumbnail, "render_mesh_png", lambda mesh, **_: drawn.append(len(mesh.faces)) or b"png")

    combine_parts_to_3mf([CombinePart("ball.stl", ball, 100)])

    assert drawn and drawn[0] <= THUMBNAIL_FACE_BUDGET
    assert thumbnail_faces_per_copy(100) * 100 <= THUMBNAIL_FACE_BUDGET


def test_preview_does_not_reload_the_combined_file(tmp_path, box_stl, monkeypatch):
    # The expensive path was loading the finished 3MF back, which expands
    # every build item. Only the source STL may be loaded.
    loaded = []
    real_load = trimesh.load
    monkeypatch.setattr(trimesh, "load", lambda path, **kw: loaded.append(str(path)) or real_load(path, **kw))

    combine_parts_to_3mf([CombinePart("box.stl", box_stl, 5)])

    assert loaded == [str(box_stl)]


def test_a_failed_preview_does_not_fail_the_combine(box_stl, monkeypatch):
    def boom(*_, **__):
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr(stl_thumbnail, "render_mesh_png", boom)
    data = combine_parts_to_3mf([CombinePart("box.stl", box_stl, 1)])
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert THUMBNAIL_PATH not in zf.namelist()
        assert "thumbnail" not in zf.read("_rels/.rels").decode()
    assert "<item " in _model_xml(data)


# --- source caps ---------------------------------------------------------


def test_rejects_sources_over_the_byte_cap_before_loading(box_stl, cyl_stl, monkeypatch):
    monkeypatch.setattr(mesh_combine, "MAX_COMBINE_SOURCE_BYTES", box_stl.stat().st_size)
    monkeypatch.setattr(mesh_combine, "_load_part", lambda part: pytest.fail("loaded despite the byte cap"))
    with pytest.raises(MeshCombineError, match="too large"):
        combine_parts_to_3mf([CombinePart("box.stl", box_stl, 1), CombinePart("cyl.stl", cyl_stl, 1)])


def test_rejects_sources_over_the_face_cap(box_stl, cyl_stl, monkeypatch):
    monkeypatch.setattr(mesh_combine, "MAX_COMBINE_SOURCE_FACES", 20)
    with pytest.raises(MeshCombineError, match="too detailed"):
        combine_parts_to_3mf([CombinePart("box.stl", box_stl, 1), CombinePart("cyl.stl", cyl_stl, 1)])


def test_copies_do_not_count_against_the_face_cap(box_stl, monkeypatch):
    # A box has 12 faces; the mesh is stored once however many copies there are.
    monkeypatch.setattr(mesh_combine, "MAX_COMBINE_SOURCE_FACES", 12)
    combine_parts_to_3mf([CombinePart("box.stl", box_stl, 50)])


# --- streamed model XML --------------------------------------------------


def test_streamed_model_spans_chunks_intact(tmp_path, monkeypatch):
    monkeypatch.setattr(mesh_combine, "_XML_CHUNK_ROWS", 7)
    ball = tmp_path / "ball.stl"
    source = trimesh.creation.icosphere(subdivisions=2)
    ball.write_bytes(source.export(file_type="stl"))

    data = combine_parts_to_3mf([CombinePart("ball.stl", ball, 2)])

    xml = _model_xml(data)
    assert xml.count("<triangle ") == len(source.faces)
    assert xml.count("<vertex ") == len(trimesh.load(ball, force="mesh").vertices)
    reloaded = trimesh.load(io.BytesIO(data), file_type="3mf").dump()
    assert [len(m.faces) for m in reloaded] == [len(source.faces)] * 2
