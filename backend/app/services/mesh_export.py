"""Export a library file's geometry as an STL mesh.

Bambuddy can already turn a model into a *picture* — `stl_thumbnail` and
`plate_thumbnail` both load a mesh and render it server-side. What it cannot do is
hand the geometry itself to a client, so any UI that wants to let a user orbit a
model has to parse the container on its own. For a `.3mf` that means shipping a ZIP
reader and a 3MF XML parser into every client, which is a lot of duplicated work to
reach a mesh the server can already read.

This service exports that mesh as a binary STL, which is the most widely readable
mesh format there is, so a client needs no container handling at all.

Deliberately STL rather than a bespoke JSON vertex payload: STL is roughly half the
bytes of an equivalent JSON array, every 3D toolkit already reads it, and it makes
this endpoint's output the same shape as the `.stl` files already in the library —
so a caller has one code path rather than two.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Half of `stl_thumbnail.MAX_VERTICES`, because this budget bounds bytes crossing a
# network rather than a one-off server-side render. STL costs 50 bytes per triangle with
# every vertex tripled, so the export is larger than its container: measured on a 3.9 MB
# 3MF, 100k vertices gives a 10 MB STL and there is no GZipMiddleware here. 50k gives
# about five, and the lost detail is invisible when orbiting.
MAX_VERTICES = 50000

# A sliced `.gcode.3mf` is deliberately absent: trimesh's loader raises
# `KeyError: 'world'` on Bambu Studio's sliced output, and that file has toolpaths
# anyway, so a G-code viewer is the right way to look at it.
SUPPORTED_SUFFIXES = (".3mf", ".stl", ".obj", ".ply")

# Below this a file cannot hold a usable mesh; see `stl_thumbnail.MIN_USABLE_STL_BYTES`.
# Pre-skipping keeps trimesh's parser warnings out of the log for stub uploads.
MIN_USABLE_BYTES = 200


class MeshExportError(Exception):
    """Raised when a file cannot be turned into a mesh.

    Carries a sentence fit to show a user rather than a stack trace: the caller is
    an HTTP route, and "this file has no geometry" and "this file is not a model" are
    different things a person can act on.
    """


def is_exportable(filename: str) -> bool:
    """Whether `filename` names a container this service can export a mesh from.

    Checked on the NAME rather than by opening the file, so a route can refuse early
    without touching disk. `.gcode.3mf` is excluded before `.3mf` matches it, which
    is why the test is on the full lowered name and not on `Path.suffix`.
    """
    name = filename.lower()
    if name.endswith(".gcode.3mf"):
        return False
    return name.endswith(SUPPORTED_SUFFIXES)


def export_mesh_stl(model_path: Path) -> bytes:
    """Load `model_path` and return its geometry as a binary STL.

    Raises:
        MeshExportError: the file is missing, too small to hold a mesh, of a type
            this cannot read, or parses to no geometry.
    """
    model_path = Path(model_path)

    if not is_exportable(model_path.name):
        raise MeshExportError(
            f"{model_path.suffix or 'This file type'} is not a model container Bambuddy can export a mesh from."
        )

    if not model_path.exists():
        raise MeshExportError("File not found on disk.")

    if model_path.stat().st_size < MIN_USABLE_BYTES:
        raise MeshExportError("This file is too small to contain a mesh.")

    # Imported here, matching `stl_thumbnail`: trimesh pulls in numpy, networkx and lxml,
    # which the route table should not pay for on import.
    try:
        import trimesh
    except ImportError as e:  # pragma: no cover - dependency is pinned
        raise MeshExportError("Mesh export is unavailable on this server.") from e

    try:
        # `force="mesh"` collapses a multi-object scene into one mesh — a 3MF routinely
        # holds several, and a viewer wants the whole plate rather than a Scene to assemble.
        mesh = trimesh.load(str(model_path), force="mesh")
    except Exception as e:
        # trimesh raises a wide variety of errors on malformed containers. The
        # message is logged for an operator and NOT forwarded to the client, which
        # gets a sentence instead of a parser internal.
        logger.info("Mesh load failed for %s: %s", model_path.name, e)
        raise MeshExportError("This file could not be read as a 3D model.") from e

    if mesh is None or not hasattr(mesh, "vertices") or len(mesh.vertices) == 0:
        raise MeshExportError("This file contains no 3D geometry.")

    mesh = _decimate_if_needed(mesh)

    try:
        blob = mesh.export(file_type="stl")
    except Exception as e:
        logger.warning("Mesh export failed for %s: %s", model_path.name, e)
        raise MeshExportError("This model could not be converted to STL.") from e

    # trimesh returns `bytes` for binary STL, but has historically returned `str`
    # for some export paths. Normalise so the route can always set a byte length.
    if isinstance(blob, str):
        blob = blob.encode()
    return blob


def _decimate_if_needed(mesh):
    """Reduce a very dense mesh, returning the original if reduction fails.

    Same budget and same failure posture as `stl_thumbnail`: a mesh too big to
    simplify is still better delivered whole than refused, because the client can
    decide for itself whether to render it.
    """
    vertex_count = len(mesh.vertices)
    if vertex_count <= MAX_VERTICES:
        return mesh

    logger.info("Simplifying mesh from %s vertices for export", vertex_count)
    try:
        keep_ratio = MAX_VERTICES / vertex_count
        target_reduction = max(0.01, min(0.99, 1.0 - keep_ratio))
        simplified = mesh.simplify_quadric_decimation(target_reduction)
    except Exception as e:
        logger.warning("Mesh simplification failed, exporting original: %s", e)
        return mesh

    if simplified is None or len(getattr(simplified, "vertices", [])) == 0:
        logger.warning("Mesh simplification produced no geometry, exporting original")
        return mesh

    logger.info("Simplified mesh to %s vertices", len(simplified.vertices))
    return simplified
