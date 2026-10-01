"""Combine several STL files into one multi-object 3MF.

The slicer sidecar takes exactly one model file per slice, so slicing several
separate STLs onto one plate means building that one file first. A plain 3MF
(core spec, no Bambu/Orca ``Metadata/`` entries) is enough: both CLIs load it
as a project with one object per build item, and ``--arrange`` lays them out
on the target bed.

Each source mesh is written once as a 3MF ``<object>`` and every copy of it is
a ``<build><item>`` pointing at that object with its own transform, so ten
copies of a 5 MB STL cost 5 MB, not 50. trimesh's own 3MF exporter duplicates
the mesh per scene node, which is why this writes the XML directly, streamed
into the zip entry so the model never exists as one string in memory.

Copies are pre-placed on a simple shelf grid with a gap between footprints.
The slicer re-arranges them anyway when arrange is on, but a grid means the
file also opens sensibly in a desktop slicer and renders a readable
thumbnail, rather than every object stacked on the origin.

The preview is rendered here, from the meshes already in memory, and embedded
as ``Metadata/thumbnail.png``. Loading the finished 3MF back to render it would
expand every build item into its own copy of the mesh: 100 copies of a
327k-face STL peaked at 13.6 GB that way. Here each source is simplified once,
to a share of a fixed face budget, before its copies are placed.
"""

from __future__ import annotations

import io
import logging
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Hard cap on build items (sum of copies). Every item is a full object for the
# slicer to arrange, support and slice; past this a single plate cannot hold
# them anyway and the request is more likely a typo than a real print.
MAX_COMBINE_INSTANCES = 100

# Caps on the sources themselves, so a request of many large STLs can't hold
# the whole lot in memory. Bytes are checked from the file sizes before
# anything is loaded; faces after each load, so the request stops at the first
# model that crosses the line. A typical printable STL is 1 to 20 MB; a binary
# STL spends 50 bytes per face, so the two caps sit close to each other.
MAX_COMBINE_SOURCE_BYTES = 300 * 1024 * 1024
MAX_COMBINE_SOURCE_FACES = 5_000_000

# Faces drawn in the embedded preview, shared across every copy on the plate.
# Each source is simplified once to its per-copy share; the floor keeps a
# 100-copy plate recognisable (100 x 1500 faces stays inside the budget).
THUMBNAIL_FACE_BUDGET = 200_000
THUMBNAIL_MIN_FACES_PER_COPY = 1_500
THUMBNAIL_PATH = "Metadata/thumbnail.png"

# Space between neighbouring footprints in the pre-placed grid, in mm.
LAYOUT_GAP_MM = 5.0

# Rows per chunk written to the model entry.
_XML_CHUNK_ROWS = 20_000

_CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"

_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    "</Types>"
)

_MODEL_REL = (
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
)
# The OPC package thumbnail relationship: how a 3MF names its preview image.
_THUMBNAIL_REL = (
    f'<Relationship Target="/{THUMBNAIL_PATH}" Id="rel1" '
    'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>'
)


def _rels_xml(with_thumbnail: bool) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + _MODEL_REL
        + (_THUMBNAIL_REL if with_thumbnail else "")
        + "</Relationships>"
    )


class MeshCombineError(ValueError):
    """A source could not be combined (unreadable, empty, or over the limits).

    The message is user-facing: the route returns it as the 400 detail.
    """


@dataclass(frozen=True)
class CombinePart:
    """One source model and how many copies of it go on the plate."""

    name: str
    path: Path
    copies: int = 1


@dataclass(frozen=True)
class _LoadedPart:
    name: str
    vertices: object  # numpy (N, 3) float array, footprint min at (0, 0), z min at 0
    faces: object  # numpy (M, 3) int array
    width: float
    depth: float
    copies: int


def _object_name(filename: str) -> str:
    """Object name shown in the slicer's object list: the filename sans extension."""
    stem = Path(filename).stem or "object"
    # Control characters are not valid in XML attribute values.
    return re.sub(r"[\x00-\x1f]", "", stem)[:200] or "object"


def _load_part(part: CombinePart) -> _LoadedPart:
    import trimesh

    try:
        mesh = trimesh.load(str(part.path), force="mesh")
    except Exception as exc:
        raise MeshCombineError(f"Could not read {part.name}: {exc}") from exc
    if mesh is None or not hasattr(mesh, "vertices") or len(mesh.vertices) == 0 or len(mesh.faces) == 0:
        raise MeshCombineError(f"{part.name} contains no printable geometry")

    vertices = mesh.vertices.copy()
    lo = vertices.min(axis=0)
    hi = vertices.max(axis=0)
    # Normalise so the footprint starts at the origin and the model sits on
    # the bed; the layout below then only has to translate in X/Y.
    vertices -= lo
    return _LoadedPart(
        name=_object_name(part.name),
        vertices=vertices,
        faces=mesh.faces,
        width=float(hi[0] - lo[0]),
        depth=float(hi[1] - lo[1]),
        copies=part.copies,
    )


def _check_source_bytes(parts: list[CombinePart]) -> None:
    total = 0
    for part in parts:
        try:
            total += part.path.stat().st_size
        except OSError as exc:
            raise MeshCombineError(f"Could not read {part.name}: {exc}") from exc
    if total > MAX_COMBINE_SOURCE_BYTES:
        raise MeshCombineError(
            f"The selected models are too large to combine: {total / 1024**2:.0f} MB, "
            f"at most {MAX_COMBINE_SOURCE_BYTES // 1024**2} MB in total"
        )


def _load_parts(parts: list[CombinePart]) -> list[_LoadedPart]:
    loaded: list[_LoadedPart] = []
    faces = 0
    for part in parts:
        item = _load_part(part)
        faces += len(item.faces)
        if faces > MAX_COMBINE_SOURCE_FACES:
            raise MeshCombineError(
                f"The selected models are too detailed to combine: more than "
                f"{MAX_COMBINE_SOURCE_FACES:,} triangles in total"
            )
        loaded.append(item)
    return loaded


def layout_offsets(footprints: list[tuple[float, float]], gap: float = LAYOUT_GAP_MM) -> list[tuple[float, float]]:
    """Shelf-pack footprints ``(width, depth)``; return each one's min-corner offset.

    Rows are filled left to right up to a width that keeps the overall layout
    roughly square, tallest footprints first so rows don't waste depth. The
    result is centred on the origin. Offsets come back in input order.
    """
    if not footprints:
        return []
    area = sum((w + gap) * (d + gap) for w, d in footprints)
    row_limit = max(max(w for w, _ in footprints), math.sqrt(area))

    order = sorted(range(len(footprints)), key=lambda i: footprints[i][1], reverse=True)
    offsets: list[tuple[float, float]] = [(0.0, 0.0)] * len(footprints)
    x = y = row_depth = 0.0
    total_w = 0.0
    for i in order:
        w, d = footprints[i]
        if x > 0 and x + w > row_limit:
            y += row_depth + gap
            x = row_depth = 0.0
        offsets[i] = (x, y)
        total_w = max(total_w, x + w)
        x += w + gap
        row_depth = max(row_depth, d)
    total_d = y + row_depth

    cx, cy = total_w / 2, total_d / 2
    return [(ox - cx, oy - cy) for ox, oy in offsets]


def thumbnail_faces_per_copy(total_copies: int) -> int:
    """Face budget for one copy in the preview."""
    return max(THUMBNAIL_MIN_FACES_PER_COPY, THUMBNAIL_FACE_BUDGET // max(total_copies, 1))


def _preview_mesh(loaded: list[_LoadedPart], owners: list[int], offsets: list[tuple[float, float]]):
    """The plate as one mesh for the thumbnail, within the face budget.

    Each source is simplified once, before its copies are placed, so the
    work and the memory scale with the budget rather than with copies times
    source faces.
    """
    import numpy as np
    import trimesh

    per_copy = thumbnail_faces_per_copy(len(owners))
    reduced = []
    for part in loaded:
        mesh = trimesh.Trimesh(vertices=part.vertices, faces=part.faces, process=False)
        if len(mesh.faces) > per_copy:
            try:
                mesh = mesh.simplify_quadric_decimation(face_count=per_copy)
            except Exception as exc:
                # Without simplification the budget can't be held, so no preview.
                logger.warning("Thumbnail skipped: could not simplify %s: %s", part.name, exc)
                return None
        reduced.append(mesh)

    vertex_blocks = []
    face_blocks = []
    base = 0
    for owner, (ox, oy) in zip(owners, offsets, strict=True):
        mesh = reduced[owner]
        vertex_blocks.append(mesh.vertices + np.array([ox, oy, 0.0]))
        face_blocks.append(mesh.faces + base)
        base += len(mesh.vertices)
    return trimesh.Trimesh(vertices=np.vstack(vertex_blocks), faces=np.vstack(face_blocks), process=False)


def _render_thumbnail(loaded: list[_LoadedPart], owners: list[int], offsets: list[tuple[float, float]]) -> bytes | None:
    """PNG preview of the plate, or None. Never fails the combine."""
    from backend.app.services.stl_thumbnail import render_mesh_png

    try:
        mesh = _preview_mesh(loaded, owners, offsets)
        if mesh is None:
            return None
        return render_mesh_png(mesh, label="combined 3MF")
    except Exception as exc:
        logger.warning("Could not render the combined 3MF thumbnail: %s", exc, exc_info=True)
        return None


def _fmt(value: float) -> str:
    # 6 significant decimals is well below slicer resolution and keeps the
    # XML compact; strip trailing zeros so "10.000000" becomes "10".
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _write_rows(out, rows, render) -> None:
    """Write ``rows`` (a numpy array) through ``render`` in fixed-size chunks."""
    for start in range(0, len(rows), _XML_CHUNK_ROWS):
        chunk = rows[start : start + _XML_CHUNK_ROWS].tolist()
        out.write("".join(render(*row) for row in chunk).encode("utf-8"))


def _write_model(out, loaded: list[_LoadedPart], owners: list[int], offsets: list[tuple[float, float]]) -> None:
    """Stream the 3MF model XML into the open zip entry ``out``."""
    out.write(
        (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<model unit="millimeter" xml:lang="en-US" xmlns="{_CORE_NS}">'
            '<metadata name="Application">Bambuddy</metadata>'
            "<resources>"
        ).encode()
    )
    for idx, part in enumerate(loaded):
        out.write(f'<object id="{idx + 1}" name="{_attr(part.name)}" type="model"><mesh><vertices>'.encode())
        _write_rows(out, part.vertices, lambda x, y, z: f'<vertex x="{_fmt(x)}" y="{_fmt(y)}" z="{_fmt(z)}"/>')
        out.write(b"</vertices><triangles>")
        _write_rows(out, part.faces, lambda a, b, c: f'<triangle v1="{a}" v2="{b}" v3="{c}"/>')
        out.write(b"</triangles></mesh></object>")
    out.write(b"</resources><build>")
    out.write(
        "".join(
            f'<item objectid="{owner + 1}" transform="1 0 0 0 1 0 0 0 1 {_fmt(ox)} {_fmt(oy)} 0"/>'
            for owner, (ox, oy) in zip(owners, offsets, strict=True)
        ).encode("utf-8")
    )
    out.write(b"</build></model>")


def combine_parts_to_3mf(parts: list[CombinePart]) -> bytes:
    """Build a plain multi-object 3MF from ``parts``; return the zip bytes.

    Blocking (mesh parsing, the preview render and XML writing are CPU-bound):
    call it through ``asyncio.to_thread`` from request handlers.

    Raises:
        MeshCombineError: no parts, a copy count outside 1..MAX, too many
            instances in total, sources over the size or face caps, or a
            source that can't be read as a mesh.
    """
    if not parts:
        raise MeshCombineError("Select at least one model to combine")
    for part in parts:
        if part.copies < 1:
            raise MeshCombineError(f"{part.name}: copies must be at least 1")
    total = sum(p.copies for p in parts)
    if total > MAX_COMBINE_INSTANCES:
        raise MeshCombineError(f"Too many objects: {total} requested, at most {MAX_COMBINE_INSTANCES} per plate")

    _check_source_bytes(parts)
    loaded = _load_parts(parts)

    footprints: list[tuple[float, float]] = []
    owners: list[int] = []
    for idx, part in enumerate(loaded):
        for _ in range(part.copies):
            footprints.append((part.width, part.depth))
            owners.append(idx)
    offsets = layout_offsets(footprints)

    thumbnail = _render_thumbnail(loaded, owners, offsets)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", _rels_xml(with_thumbnail=thumbnail is not None))
        with zf.open("3D/3dmodel.model", "w", force_zip64=True) as out:
            _write_model(out, loaded, owners, offsets)
        if thumbnail is not None:
            zf.writestr(THUMBNAIL_PATH, thumbnail)
    logger.info(
        "Combined %d model(s) into %d object(s) on one plate (%d bytes, thumbnail %s)",
        len(loaded),
        total,
        buf.tell(),
        "embedded" if thumbnail is not None else "skipped",
    )
    return buf.getvalue()
