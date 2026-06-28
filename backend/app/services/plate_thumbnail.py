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
import zipfile

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

# Above this vertex count, trimesh.simplify_quadric_decimation runs first.
# Same cap stl_thumbnail.py uses; matplotlib's Poly3DCollection slows down
# nonlinearly past ~100k faces and a plate thumbnail doesn't need detail
# beyond what a 512x512 PNG can resolve.
_MAX_VERTICES = 100_000

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
            if "3D/3dmodel.model" not in names:
                logger.debug(
                    "plate_thumbnail: sliced 3MF has no 3D/3dmodel.model — skipping (plates %s)",
                    sorted(missing),
                )
                return threemf_bytes
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("plate_thumbnail: input is not a readable zip: %s", exc)
        return threemf_bytes

    try:
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
    from backend.app.services.stl_thumbnail import _configure_matplotlib_cache

    _configure_matplotlib_cache()

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import trimesh
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    loaded = trimesh.load(io.BytesIO(threemf_bytes), file_type="3mf", force="mesh")
    if loaded is None or not hasattr(loaded, "vertices") or len(loaded.vertices) == 0:
        logger.debug("plate_thumbnail: trimesh produced empty mesh from 3MF")
        return None, None

    mesh = loaded
    if len(mesh.vertices) > _MAX_VERTICES:
        try:
            keep_ratio = _MAX_VERTICES / len(mesh.vertices)
            target_reduction = max(0.01, min(0.99, 1.0 - keep_ratio))
            mesh = mesh.simplify_quadric_decimation(target_reduction)
        except Exception as exc:
            logger.debug("plate_thumbnail: mesh simplification failed, using original: %s", exc)

    vertices = mesh.vertices
    bounds_min = vertices.min(axis=0)
    bounds_max = vertices.max(axis=0)
    centered = vertices - (bounds_min + bounds_max) / 2
    max_extent = (bounds_max - bounds_min).max()
    scaled = centered / max_extent if max_extent > 0 else centered

    faces = mesh.faces
    poly3d = [[scaled[v] for v in face] for face in faces]

    large = _render_at_size(poly3d, _PLATE_PNG_SIZE, plt, Poly3DCollection)
    small = _render_at_size(poly3d, _PLATE_PNG_SMALL_SIZE, plt, Poly3DCollection)
    return large, small


def _render_at_size(poly3d, size: int, plt, Poly3DCollection) -> bytes:
    """Render the prepared poly3d collection to an in-memory PNG."""
    fig = plt.figure(figsize=(size / 100, size / 100), dpi=100)
    fig.patch.set_facecolor(_BACKGROUND_COLOR)
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor(_BACKGROUND_COLOR)
    ax.add_collection3d(
        Poly3DCollection(
            poly3d,
            facecolors=_BAMBU_GREEN,
            edgecolors=_BAMBU_GREEN,
            linewidths=0.1,
            alpha=0.9,
        )
    )
    ax.set_xlim(-0.6, 0.6)
    ax.set_ylim(-0.6, 0.6)
    ax.set_zlim(-0.6, 0.6)
    ax.view_init(elev=25, azim=45)
    ax.set_axis_off()
    ax.grid(False)
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

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
    plt.close(fig)
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
