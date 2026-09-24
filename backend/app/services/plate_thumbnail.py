"""Plate thumbnail injection for sliced 3MFs.

When the slicer CLI (Bambu Studio or OrcaSlicer in the docker sidecar)
produces a ``.gcode.3mf`` without ``Metadata/plate_N.png``, the archive
card has nothing to show. Both CLIs skip the plate-thumbnail render when
invoked with ``--slice --export-3mf`` headlessly — that render is a
GUI-side action that only fires in the desktop Studio. The
``--export-png`` flag exists but is mutually exclusive with
``--export-3mf`` and additionally needs a Wayland compositor in the
container, so we can't reach it from the sidecar's current invocation
shape.

This module fills the gap server-side: it parses the sliced 3MF, and
for every ``plate_N.gcode`` entry that doesn't have a matching
``plate_N.png`` it renders one from the embedded 3D model using the
same trimesh + matplotlib path as :mod:`backend.app.services.stl_thumbnail`,
then injects ``Metadata/plate_N.png`` (512x512) + ``Metadata/plate_N_small.png``
(128x128) into the zip. Best-effort: any failure (no model file,
trimesh can't parse, matplotlib render fails) returns the input bytes
unchanged so the slice flow itself never breaks.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# Bambu Studio's plate covers. Match the dimensions BS uses on desktop so
# the rendered images flow through the same archive UI code paths without
# special-casing.
_PLATE_PNG_SIZE = 512
_PLATE_PNG_SMALL_SIZE = 128

# Mirror stl_thumbnail.py's palette so archive cards rendered through
# this path are visually consistent with the rest of Bambuddy's library
# thumbnails — same Bambu green on the same dark background.
_BAMBU_GREEN = "#00AE42"
_BACKGROUND_COLOR = "#1a1a1a"

# Faces the whole plate is rendered with, every instance counted. Render cost
# is faces, not vertices: matplotlib's Poly3DCollection slows down nonlinearly
# past ~200k of them, and a 512x512 PNG resolves nothing finer. Roughly what
# stl_thumbnail's 100k-vertex cap comes to on a closed mesh.
_RENDER_FACE_BUDGET = 200_000

# A mesh is never decimated below this, however many times it is placed, or a
# plate of small parts renders as a field of blobs.
_MIN_FACES_PER_MESH = 200

# Past this many faces after decimation (a plate of thousands of parts, each
# already at the floor above) the thumbnail is skipped. It is best-effort, and
# the render's memory grows with every face it is handed (#3135).
_MAX_PLACED_FACES = 1_000_000

# Bounds on the object graph: components nest, and a file that references
# itself, or places one part a million times, must not be walked forever.
_MAX_COMPONENT_DEPTH = 16
_MAX_PLACEMENTS = 20_000

_MODEL_ROOT = "3D/3dmodel.model"

# One plate render at a time. The slice routes run this off the event loop, and
# a render holds the whole placed plate in memory; two slices finishing
# together must not hold two. pyplot is NOT what this guards — the renderer
# below never touches it (see ``_render_at_size``).
_render_lock = threading.Lock()

# Plate-gcode entries look like ``Metadata/plate_1.gcode``,
# ``Metadata/plate_12.gcode`` — anything else is a md5 / json sidecar.
_PLATE_GCODE_RE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")


def inject_plate_thumbnails_if_missing(threemf_bytes: bytes) -> bytes:
    """Return ``threemf_bytes`` with ``plate_N.png`` injected for every
    plate that's missing one.

    No-op fast path when every plate already has a thumbnail — the input
    bytes are returned verbatim (same object identity), so the common
    case of a desktop-Studio-sliced 3MF flowing through this function
    is essentially free.

    On any failure the input bytes are returned unchanged. A missing
    thumbnail is a visual degradation; failing the slice would be worse.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(threemf_bytes), "r") as zf:
            names = set(zf.namelist())
            missing = _missing_plate_ids(names)
            if not missing:
                return threemf_bytes
            if _MODEL_ROOT not in names:
                logger.debug(
                    "plate_thumbnail: sliced 3MF has no 3D/3dmodel.model — skipping (plates %s)",
                    sorted(missing),
                )
                return threemf_bytes
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("plate_thumbnail: input is not a readable zip: %s", exc)
        return threemf_bytes

    try:
        with _render_lock:
            large_png, small_png = _render_model_thumbnails(threemf_bytes)
    except Exception as exc:
        logger.warning(
            "plate_thumbnail: render failed, returning sliced 3MF without injected thumbs: %s",
            exc,
            exc_info=True,
        )
        return threemf_bytes

    if large_png is None or small_png is None:
        return threemf_bytes

    try:
        return _inject_pngs(threemf_bytes, missing, large_png, small_png)
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("plate_thumbnail: zip re-pack failed: %s", exc)
        return threemf_bytes


def _missing_plate_ids(names: set[str]) -> list[int]:
    """Plate IDs that have a ``plate_N.gcode`` but no ``plate_N.png``.

    Multi-plate slices produce one gcode per plate; we render the model
    once and reuse it for every missing plate. The visual is identical
    across plates of the same model, which matches what users see today
    for desktop-Studio-sliced multi-plate projects — Studio also reuses
    the model render across plates that share geometry.
    """
    plate_ids: list[int] = []
    for name in names:
        m = _PLATE_GCODE_RE.match(name)
        if not m:
            continue
        n = int(m.group(1))
        if f"Metadata/plate_{n}.png" not in names:
            plate_ids.append(n)
    return sorted(plate_ids)


def _render_model_thumbnails(threemf_bytes: bytes) -> tuple[bytes | None, bytes | None]:
    """Render an isometric view of the 3MF's model at both plate sizes.

    Returns (large, small) PNG bytes, or (None, None) if the model
    couldn't be loaded. Mirrors stl_thumbnail.py's style (Bambu green
    mesh on dark background, ~25deg elev / 45deg azim) so this output
    blends into Bambuddy's existing library/archive cards.
    """
    # Local imports so a `import backend.app.services.plate_thumbnail` from
    # an environment without matplotlib/trimesh doesn't fail at import time —
    # the function will simply degrade to no-op via the exception branch.
    #
    # The light angle is IMPORTED rather than mirrored like the palette above.
    # "A plate card and a library thumbnail of the same model look alike" is the
    # whole reason these two renderers share a look, and a second copy of the
    # angle is exactly how that silently stops being true. A palette can afford a
    # copy; a number nobody would notice drifting cannot.
    from backend.app.services.stl_thumbnail import (
        _configure_matplotlib_cache,
        _repair_winding,
        _shade_kwargs,
    )

    _configure_matplotlib_cache()

    import trimesh
    from matplotlib.colors import LightSource
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    with zipfile.ZipFile(io.BytesIO(threemf_bytes), "r") as zf:
        placed = _load_plate_geometry(zf, trimesh, _repair_winding)
    if placed is None:
        return None, None
    vertices, faces = placed
    bounds_min = vertices.min(axis=0)
    bounds_max = vertices.max(axis=0)
    centered = vertices - (bounds_min + bounds_max) / 2
    max_extent = (bounds_max - bounds_min).max()
    scaled = centered / max_extent if max_extent > 0 else centered

    # ndarray, not a list of lists — shading walks this to build normals, and the
    # list form is ~30x slower to construct. Paid twice per plate: once per size.
    poly3d = scaled[faces]

    # Resolved once and shared: both sizes must be lit identically or the 128px
    # card and the 512px view disagree. Empty for a mesh matplotlib cannot shade,
    # which keeps such a plate rendering flat instead of failing — see
    # ``_shade_kwargs``.
    shade_kw = _shade_kwargs(poly3d, LightSource)

    large = _render_at_size(poly3d, _PLATE_PNG_SIZE, Poly3DCollection, shade_kw)
    small = _render_at_size(poly3d, _PLATE_PNG_SMALL_SIZE, Poly3DCollection, shade_kw)
    return large, small


@dataclass
class _Object3MF:
    """One ``<object>``: its own mesh, and the objects it places as components."""

    vertices: object = None  # np.ndarray (n, 3) or None
    faces: object = None  # np.ndarray (m, 3) or None
    # (model path or None for "same file", object id, 4x4 transform)
    components: list = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _transform(attr: str | None):
    """A 3MF ``transform`` attribute as a 4x4 matrix for column vectors.

    3MF lists the 3x4 matrix row by row for ROW vectors (``m00 m01 m02 m10 ...
    m32``, the last three being the translation); transposing it gives the usual
    column-vector form. Same reading as trimesh's ``_attrib_to_transform``.
    """
    import numpy as np

    matrix = np.eye(4)
    if attr:
        values = [float(x) for x in attr.split()]
        if len(values) == 12:
            matrix[:3, :4] = np.array(values).reshape(4, 3).T
    return matrix


def _parse_model_file(zf: zipfile.ZipFile, path: str) -> tuple[dict[str, _Object3MF], list]:
    """Every object in one model file, and its build items (root file only).

    Streams the file and drops each element as soon as it is read, so memory
    stays at the numbers collected rather than an XML tree — one Bambu model
    file seen in the wild is a single 163 MB mesh. lxml rather than the stdlib
    parser: ElementTree builds a Python object per vertex and took ~3x as long
    on that file. The input is untrusted, so entities, DTDs and network access
    are all off; trimesh, which this replaces here, parses the same files with
    lxml already.
    """
    import numpy as np
    from lxml import etree

    objects: dict[str, _Object3MF] = {}
    build: list = []
    vertices: list = []
    triangles: list = []
    components: list = []

    parse = etree.iterparse(
        io.BytesIO(zf.read(path)),
        events=("end",),
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
    )
    for _event, elem in parse:
        name = _local(elem.tag) if isinstance(elem.tag, str) else ""
        if name == "vertex":
            try:
                vertices.append((float(elem.get("x")), float(elem.get("y")), float(elem.get("z"))))
            except (TypeError, ValueError):
                vertices.append((0.0, 0.0, 0.0))  # keeps the indices of later vertices right
        elif name == "triangle":
            try:
                triangles.append((int(elem.get("v1")), int(elem.get("v2")), int(elem.get("v3"))))
            except (TypeError, ValueError):
                pass
        elif name == "component" and elem.get("objectid") is not None:
            # ``p:path`` (production extension): the object lives in another
            # model file. Bambu Studio and OrcaSlicer put every mesh in
            # ``3D/Objects/`` and place it this way.
            ref = next((val for key, val in elem.attrib.items() if _local(key) == "path"), None)
            components.append(
                (ref.lstrip("/") if ref else None, elem.get("objectid"), _transform(elem.get("transform")))
            )
        elif name == "object":
            obj = _Object3MF(components=components)
            if triangles:
                v = np.array(vertices, dtype=float).reshape(-1, 3)
                f = np.array(triangles, dtype=np.int64).reshape(-1, 3)
                # A triangle naming a vertex that isn't there would index past
                # the array at render time; drop it here instead.
                obj.vertices, obj.faces = v, f[(f >= 0).all(axis=1) & (f < len(v)).all(axis=1)]
            if elem.get("id") is not None:
                objects[elem.get("id")] = obj
            vertices, triangles, components = [], [], []
        elif name == "item" and elem.get("objectid") is not None:
            # The production extension allows ``p:path`` here too, naming the
            # file the object lives in; the root file when absent.
            ref = next((val for key, val in elem.attrib.items() if _local(key) == "path"), None)
            build.append((ref.lstrip("/") if ref else None, elem.get("objectid"), _transform(elem.get("transform"))))
        else:
            continue
        # Free what has been read: the element, and the siblings before it that
        # lxml would otherwise keep attached to the parent.
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]
    return objects, build


def _load_plate_geometry(zf: zipfile.ZipFile, trimesh, repair_winding):
    """The plate as one (vertices, faces) pair, every instance placed, within budget.

    Not ``trimesh.load``: its 3MF reader re-parses a ``p:path`` component's file
    for EVERY component that references it and appends the meshes again each
    time. Bambu Studio and OrcaSlicer write each instance as its own object with
    one such component, so N copies of a part came back as one mesh holding N
    copies of every triangle — N² of them once placed — while the vertex count,
    merged back down, looked normal. 25 bins of 10k faces loaded as 6.4M faces
    and took 8.4 GB to render (#3135; trimesh 4.12 and 5.1 alike).

    Here each model file is parsed once and each mesh is kept once, decimated
    once to its share of the face budget, and only then placed per instance.
    Returns None when there is nothing to draw or the plate is over the ceiling.
    """
    import numpy as np

    files: dict[str, dict[str, _Object3MF]] = {}
    names = set(zf.namelist())

    def objects_in(path: str) -> dict[str, _Object3MF]:
        if path not in files:
            files[path] = _parse_model_file(zf, path)[0] if path in names else {}
        return files[path]

    root_objects, build = _parse_model_file(zf, _MODEL_ROOT)
    files[_MODEL_ROOT] = root_objects

    placements: dict[tuple[str, str], list] = defaultdict(list)
    count = 0

    def place(path: str, object_id: str, matrix, depth: int, trail: frozenset) -> None:
        nonlocal count
        key = (path, object_id)
        if depth > _MAX_COMPONENT_DEPTH or key in trail or count > _MAX_PLACEMENTS:
            return
        obj = objects_in(path).get(object_id)
        if obj is None:
            return
        if obj.faces is not None and len(obj.faces):
            placements[key].append(matrix)
            count += 1
        for ref, child_id, child_matrix in obj.components:
            place(ref or path, child_id, matrix @ child_matrix, depth + 1, trail | {key})

    for ref, object_id, matrix in build:
        place(ref or _MODEL_ROOT, object_id, matrix, 0, frozenset())

    if count > _MAX_PLACEMENTS:
        logger.info("plate_thumbnail: over %d placed parts, skipping the thumbnail", _MAX_PLACEMENTS)
        return None
    if not placements:
        logger.debug("plate_thumbnail: 3MF places no mesh")
        return None

    def faces_of(key) -> int:
        return len(files[key[0]][key[1]].faces)

    def over_ceiling(faces: int) -> bool:
        if faces <= _MAX_PLACED_FACES:
            return False
        logger.info(
            "plate_thumbnail: %d faces even after decimation (ceiling %d), skipping the thumbnail",
            faces,
            _MAX_PLACED_FACES,
        )
        return True

    total = sum(faces_of(key) * len(ms) for key, ms in placements.items())
    scale = min(1.0, _RENDER_FACE_BUDGET / total)
    targets = {key: max(_MIN_FACES_PER_MESH, int(faces_of(key) * scale)) for key in placements}
    # What decimation can actually reach: it removes at most 99% of a mesh, so a
    # part needing more keeps 1% of its faces rather than its target. Checked
    # before any mesh is built, so a hopeless plate costs nothing but the parse.
    reachable = sum(
        min(faces_of(key), max(targets[key], -(-faces_of(key) // 100))) * len(ms) for key, ms in placements.items()
    )
    if over_ceiling(reachable):
        return None

    prepared = []
    for key, matrices in placements.items():
        obj = files[key[0]][key[1]]
        mesh = trimesh.Trimesh(vertices=obj.vertices, faces=obj.faces, process=True)
        if targets[key] < len(mesh.faces):
            try:
                # ``percent`` (the share to REMOVE), the form this module has always
                # called. ``face_count`` reaches the same size, but on a real 2M-face
                # model it left the winding inconsistent where ``percent`` did not,
                # which costs the repair below ~14 s.
                reduction = 1.0 - targets[key] / len(mesh.faces)
                mesh = mesh.simplify_quadric_decimation(max(0.01, min(0.99, reduction)))
            except Exception as exc:
                logger.debug("plate_thumbnail: mesh simplification failed, using original: %s", exc)
        prepared.append((mesh, matrices))

    # Again on what decimation delivered: it can stop short of its target, or
    # fail and leave the mesh whole, and the render's memory follows the faces
    # it is actually handed.
    if over_ceiling(sum(len(mesh.faces) * len(ms) for mesh, ms in prepared)):
        return None

    all_vertices = []
    all_faces = []
    offset = 0
    for mesh, matrices in prepared:
        # Once per mesh, before it is placed: ``faces`` below index these vertices,
        # so a repair that ever moves one would leave the two out of step. Shared
        # with stl_thumbnail rather than copied — the renderers agree because they
        # run the same code.
        try:
            repair_winding(mesh, trimesh, "plate_thumbnail")
        except Exception as e:  # best-effort, as the whole module is
            logger.debug("plate_thumbnail: winding repair skipped (%s)", e)
        vertices = np.asarray(mesh.vertices, dtype=float)
        faces = np.asarray(mesh.faces, dtype=np.int64)
        for matrix in matrices:
            all_vertices.append(vertices @ matrix[:3, :3].T + matrix[:3, 3])
            # A mirroring transform turns every triangle inside out; flip the
            # winding back so shading still sees the outside.
            placed = faces[:, ::-1] if np.linalg.det(matrix[:3, :3]) < 0 else faces
            all_faces.append(placed + offset)
            offset += len(vertices)

    return np.vstack(all_vertices), np.vstack(all_faces)


def _render_at_size(poly3d, size: int, Poly3DCollection, shade_kw: dict) -> bytes:
    """Render the prepared poly3d collection to an in-memory PNG.

    Matplotlib's object API, not pyplot. This runs in a worker thread (#3135)
    while stl_thumbnail renders through pyplot on the event loop, and pyplot's
    figure registry and "current figure" are process-global: its
    ``subplots_adjust`` would lay out whichever figure the other thread made
    last, and neither lock placement is acceptable — held on the loop it stalls
    the server for the whole plate render. A ``Figure`` with its own Agg canvas
    shares nothing, so the two can run at once.
    """
    # Local, like every other import in this module, so importing plate_thumbnail
    # in an environment without matplotlib still works. stl_thumbnail's own
    # module level is import-light, so this costs nothing after the first call.
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from backend.app.services.stl_thumbnail import VIEW_AZIM_DEG, VIEW_ELEV_DEG

    fig = Figure(figsize=(size / 100, size / 100), dpi=100)
    FigureCanvasAgg(fig)
    fig.patch.set_facecolor(_BACKGROUND_COLOR)
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor(_BACKGROUND_COLOR)
    # ``shade=True`` needs a real ``edgecolors``: matplotlib shades the edge
    # colours alongside the face colours, and an empty array (``"none"``) makes
    # it raise on the broadcast. Keep the two in step if either moves.
    ax.add_collection3d(
        Poly3DCollection(
            poly3d,
            facecolors=_BAMBU_GREEN,
            edgecolors=_BAMBU_GREEN,
            linewidths=0.1,
            alpha=0.9,
            **shade_kw,
        )
    )
    ax.set_xlim(-0.6, 0.6)
    ax.set_ylim(-0.6, 0.6)
    ax.set_zlim(-0.6, 0.6)
    ax.view_init(elev=VIEW_ELEV_DEG, azim=VIEW_AZIM_DEG)
    ax.set_axis_off()
    ax.grid(False)
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        facecolor=_BACKGROUND_COLOR,
        edgecolor="none",
        bbox_inches="tight",
        pad_inches=0.05,
        dpi=100,
    )
    return buf.getvalue()


def _inject_pngs(
    threemf_bytes: bytes,
    plate_ids: list[int],
    large_png: bytes,
    small_png: bytes,
) -> bytes:
    """Copy every entry from the input zip to a new one, then append the
    plate PNGs. Re-pack rather than mutate-in-place because zipfile doesn't
    support adding entries to an existing archive read from bytes."""
    out_buf = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(threemf_bytes), "r") as src,
        zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as dst,
    ):
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        for n in plate_ids:
            dst.writestr(f"Metadata/plate_{n}.png", large_png)
            dst.writestr(f"Metadata/plate_{n}_small.png", small_png)
    return out_buf.getvalue()
