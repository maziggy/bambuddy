"""Combine several STL files into one multi-object 3MF.

The slicer sidecar takes exactly one model file per slice, so slicing several
separate STLs onto one plate means building that one file first. A plain 3MF
(core spec, no Bambu/Orca ``Metadata/`` entries) is enough: both CLIs load it
as a project with one object per build item, and ``--arrange`` lays them out
on the target bed.

Each source mesh is written once as a 3MF ``<object>`` and every copy of it is
a ``<build><item>`` pointing at that object with its own transform, so ten
copies of a 5 MB STL cost 5 MB, not 50. trimesh's own 3MF exporter duplicates
the mesh per scene node, which is why this writes the XML directly.

Copies are pre-placed on a simple shelf grid with a gap between footprints.
The slicer re-arranges them anyway when arrange is on, but a grid means the
file also opens sensibly in a desktop slicer and renders a readable
thumbnail, rather than every object stacked on the origin.
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

# Space between neighbouring footprints in the pre-placed grid, in mm.
LAYOUT_GAP_MM = 5.0

_CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"

_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    "</Types>"
)

_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
    "</Relationships>"
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


def _fmt(value: float) -> str:
    # 6 significant decimals is well below slicer resolution and keeps the
    # XML compact; strip trailing zeros so "10.000000" becomes "10".
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _mesh_xml(part: _LoadedPart) -> str:
    vertex_rows = "".join(f'<vertex x="{_fmt(x)}" y="{_fmt(y)}" z="{_fmt(z)}"/>' for x, y, z in part.vertices.tolist())
    triangle_rows = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in part.faces.tolist())
    return f"<mesh><vertices>{vertex_rows}</vertices><triangles>{triangle_rows}</triangles></mesh>"


def _attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def combine_parts_to_3mf(parts: list[CombinePart]) -> bytes:
    """Build a plain multi-object 3MF from ``parts``; return the zip bytes.

    Blocking (mesh parsing and XML building are CPU-bound) — call it through
    ``asyncio.to_thread`` from request handlers.

    Raises:
        MeshCombineError: no parts, a copy count outside 1..MAX, too many
            instances in total, or a source that can't be read as a mesh.
    """
    if not parts:
        raise MeshCombineError("Select at least one model to combine")
    for part in parts:
        if part.copies < 1:
            raise MeshCombineError(f"{part.name}: copies must be at least 1")
    total = sum(p.copies for p in parts)
    if total > MAX_COMBINE_INSTANCES:
        raise MeshCombineError(f"Too many objects: {total} requested, at most {MAX_COMBINE_INSTANCES} per plate")

    loaded = [_load_part(p) for p in parts]

    footprints: list[tuple[float, float]] = []
    owners: list[int] = []
    for idx, part in enumerate(loaded):
        for _ in range(part.copies):
            footprints.append((part.width, part.depth))
            owners.append(idx)
    offsets = layout_offsets(footprints)

    objects_xml = "".join(
        f'<object id="{idx + 1}" name="{_attr(part.name)}" type="model">{_mesh_xml(part)}</object>'
        for idx, part in enumerate(loaded)
    )
    items_xml = "".join(
        f'<item objectid="{owner + 1}" transform="1 0 0 0 1 0 0 0 1 {_fmt(ox)} {_fmt(oy)} 0"/>'
        for owner, (ox, oy) in zip(owners, offsets, strict=True)
    )
    model_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{_CORE_NS}">'
        '<metadata name="Application">Bambuddy</metadata>'
        f"<resources>{objects_xml}</resources>"
        f"<build>{items_xml}</build>"
        "</model>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", _RELS_XML)
        zf.writestr("3D/3dmodel.model", model_xml)
    logger.info(
        "Combined %d model(s) into %d object(s) on one plate (%d bytes)",
        len(loaded),
        total,
        buf.tell(),
    )
    return buf.getvalue()
