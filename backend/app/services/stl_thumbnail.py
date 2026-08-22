"""STL Thumbnail Generation Service.

Generates thumbnail images from STL files using trimesh and matplotlib.
"""

import logging
import os
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Matplotlib's font_manager emits one INFO line per font on first import
# while it builds its cache, including a noisy "Failed to extract font
# properties from NotoColorEmoji.ttf" for the COLR/COLR1 emoji format it
# doesn't support. These are not actionable — demote to WARNING so real
# font issues still surface but the first STL upload doesn't produce a
# multi-line matplotlib preamble in the journal.
logging.getLogger("matplotlib.font_manager").setLevel(logging.WARNING)


def _configure_matplotlib_cache() -> None:
    """Point matplotlib's config/cache directory at a writable persistent path.

    Without this, matplotlib falls back to ``/tmp/matplotlib-XXXXXX`` whenever
    ``$HOME/.config/matplotlib`` isn't writable — which is the case under
    Bambuddy's container / systemd-service deployments where ``$HOME`` is set
    to a non-writable path. The fallback emits a WARNING on every cold start
    AND loses the font cache on host reboot, so font_manager rebuilds it
    every time → another batch of INFO lines.

    Setting ``MPLCONFIGDIR`` to ``settings.base_dir / .cache / matplotlib``
    eliminates both: the warning never fires, and the cache survives across
    restarts so the per-font scan only runs once per deployment.
    Idempotent — respects an externally-set ``MPLCONFIGDIR`` if the operator
    chose their own path.
    """
    if os.environ.get("MPLCONFIGDIR"):
        return
    try:
        from backend.app.core.config import settings

        cache_dir = Path(settings.base_dir) / ".cache" / "matplotlib"
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(cache_dir)
    except Exception as exc:
        # Best-effort. If settings isn't importable or the mkdir fails (read-only
        # FS, permission denied), let matplotlib fall back to /tmp with its
        # built-in warning — same as today's behaviour, no worse.
        logger.debug("Could not configure MPLCONFIGDIR: %s", exc)


# Bambu green color for rendering
BAMBU_GREEN = "#00AE42"
BACKGROUND_COLOR = "#1a1a1a"

# Direction of the synthetic light used to shade the mesh. Without a light
# source ``Poly3DCollection`` fills every triangle with the identical colour
# regardless of its normal, so the render comes out a flat silhouette and one
# model is indistinguishable from another (issue #2816).
#
# The azimuth is NOT free. matplotlib's light direction for (az, alt) is
# ``[cos(90-az)cos(alt), sin(90-az)cos(alt), sin(alt)]``, and the camera set by
# ``view_init(elev, azim)`` sits at ``[cos(elev)cos(azim), cos(elev)sin(azim),
# sin(elev)]``. The dot product of the two must be POSITIVE or the light is
# behind the model: at 225 it is -0.34, which lights the two hidden faces and
# gives both visible ones the identical 0.475 — a cube with no contrast down its
# front edge. At 315 it is +0.30, and the two visible sides come out 0.825 and
# 0.475. ``test_light_is_on_the_camera_side`` holds that invariant so the pair
# cannot drift apart again.
LIGHT_AZIMUTH_DEG = 315
LIGHT_ALTITUDE_DEG = 45

# The camera the light above is chosen against. Named because the two are a PAIR:
# move one without the other and the model goes back to being lit from behind.
VIEW_ELEV_DEG = 25
VIEW_AZIM_DEG = 45

# Maximum vertices before simplification
MAX_VERTICES = 100000

# Minimum STL file size that could possibly contain a usable mesh:
# - Binary STL with one triangle: 80B header + 4B count + 50B triangle = 134B
# - ASCII STL with one triangle: header + "facet ... endfacet" + footer ≈ 150B
# Files below this are stubs / placeholders / corrupted; trimesh would return an
# empty mesh anyway. Pre-skipping at the call sites suppresses the warning storm
# bulk-uploaded ZIPs of small test STLs used to produce.
MIN_USABLE_STL_BYTES = 200


def _repair_winding(mesh, trimesh, label: str) -> None:
    """Make every face wind the same way, and wind it OUTWARD, before shading.

    matplotlib derives its normals from vertex ORDER, so a triangle wound the
    wrong way shades as though it faced away and the model comes out patchy —
    camouflage rather than a surface. Unshaded this never showed, so lighting the
    render is what makes it matter, and the File Manager takes arbitrary user
    STLs. ``trimesh.load(force="mesh")`` does not repair winding; this does.

    ``trimesh.repair.fix_winding`` and NOT ``mesh.fix_normals()``: the latter
    reaches ``body_count`` -> ``scipy.csgraph``, and scipy is not a dependency of
    this project. fix_winding goes through networkx, which requirements.txt
    already pins.

    Three steps, because each one leaves something for the next:

    * ``fix_winding`` makes the winding agree but is free to settle on either
      orientation, and on a half-inverted sphere it picks INWARD — consistent,
      and consistently lit from inside.
    * ``fix_inversion`` corrects that off the sign of the volume, but only for a
      WATERTIGHT mesh. It returns early otherwise, because a volume measured
      across holes says nothing about which way is out.
    * Which leaves the common case, since a mesh with broken winding is usually
      not watertight either. With no usable volume, decide by whether the faces
      point away from the centroid. Measured on a punctured half-inverted
      icosphere: the first two steps alone left 0 of 1200 faces oriented like the
      correctly wound mesh, a mean render delta of 4.56; with this one it is
      1200 of 1200 and 0.00.

    The centroid test runs only on a mesh whose winding was already broken, and
    it leaves correct ones alone: closed and punctured spheres, a flat plate, an
    open tube, a non-convex L and two disjoint boxes all sum positive.

    Gated here rather than at the call sites so the two renderers cannot drift.
    The check is tens of ms where the repair is seconds on a large mesh, so only
    meshes that would otherwise render wrong pay for it.
    """
    import numpy as np

    if len(mesh.faces) == 0 or mesh.is_winding_consistent:
        return

    logger.debug("Repairing inconsistent winding before render: %s", label)
    trimesh.repair.fix_winding(mesh)
    trimesh.repair.fix_inversion(mesh)
    if mesh.is_watertight:
        return

    outward = mesh.triangles.mean(axis=1) - mesh.vertices.mean(axis=0)
    if float(np.einsum("ij,ij->i", mesh.face_normals, outward).sum()) < 0:
        logger.debug("Winding settled inward on a non-watertight mesh, inverting: %s", label)
        mesh.invert()


def _shade_kwargs(poly3d, LightSource) -> dict:
    """``shade=True`` and its light, or nothing when the mesh cannot be shaded.

    matplotlib's ``_shade_colors`` has a fallback for a mesh whose every face
    normal is degenerate, and that fallback returns the colour argument it was
    given, unchanged. Passing a colour STRING — which both renderers do — makes
    it hand back a 0-d ``<U7`` array, and ``to_rgba_array`` then calls ``len()``
    on it and raises ``TypeError: len() of unsized object``.

    So a file whose facets are all zero-area or collinear rendered fine while the
    output was flat, and would fail outright once lit. That population is real:
    stub and truncated STLs, and hand-written 3MFs with an empty ``<triangles/>``.
    Worse, ``batch_generate_stl_thumbnails`` walks a whole folder with no
    minimum-size pre-skip, so each one would count as a failure in the UI and put
    a traceback in the log — the exact noise ``stl_thumbnail``'s demoted logging
    exists to keep out.

    Deciding here rather than catching the TypeError keeps the flat render as a
    real outcome instead of an error path, and costs ~6 ms on a 227k-face mesh.
    Identical to matplotlib's own test: a cross product that is finite and
    non-zero for at least one face.
    """
    import numpy as np

    if len(poly3d) == 0:
        return {}
    tri = np.asarray(poly3d, dtype=float)
    normals = np.cross(tri[:, 0] - tri[:, 1], tri[:, 1] - tri[:, 2])
    lengths = np.linalg.norm(normals, axis=1)
    if not bool(np.any(np.isfinite(lengths) & (lengths > 0))):
        return {}
    return {
        "shade": True,
        "lightsource": LightSource(azdeg=LIGHT_AZIMUTH_DEG, altdeg=LIGHT_ALTITUDE_DEG),
    }


def generate_stl_thumbnail(
    stl_path: Path,
    thumbnails_dir: Path,
    size: int = 256,
) -> str | None:
    """Generate a thumbnail image from an STL file.

    Args:
        stl_path: Path to the STL file
        thumbnails_dir: Directory to save the thumbnail
        size: Thumbnail size in pixels (default 256x256)

    Returns:
        Path to the generated thumbnail, or None on failure
    """
    # Callers historically pass either Path or str; coerce so the `thumbnails_dir
    # / thumb_filename` join at the end of this function can't fail with the
    # str-divided-by-str TypeError (see #1299).
    stl_path = Path(stl_path)
    thumbnails_dir = Path(thumbnails_dir)

    try:
        # Must precede the matplotlib import — MPLCONFIGDIR is read at
        # matplotlib import time, not on subsequent attribute access.
        _configure_matplotlib_cache()

        import matplotlib
        import trimesh

        # Use Agg backend for headless rendering
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import LightSource
        from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection

        # Load the STL file
        mesh = trimesh.load(str(stl_path), force="mesh")

        if mesh is None or not hasattr(mesh, "vertices") or len(mesh.vertices) == 0:
            # Demoted from warning to debug: this is a per-file content
            # observation (the STL is empty / stub / corrupted), not an
            # actionable error. The caller proceeds correctly with no
            # thumbnail. The call sites also pre-skip files below
            # MIN_USABLE_STL_BYTES so the common stub-STL case never gets
            # this far — this branch now catches only the rare "large
            # enough but trimesh still can't parse it" case.
            logger.debug("Failed to load STL or empty mesh: %s", stl_path)
            return None

        # Simplify large meshes for performance
        if len(mesh.vertices) > MAX_VERTICES:
            logger.info("Simplifying mesh from %s vertices", len(mesh.vertices))
            try:
                # Calculate reduction ratio (0-1 range)
                # e.g., 124633 vertices -> 100000 means keep ~80%, so reduce by ~20%
                keep_ratio = MAX_VERTICES / len(mesh.vertices)
                target_reduction = 1.0 - keep_ratio
                # Clamp to valid range (0.01 to 0.99)
                target_reduction = max(0.01, min(0.99, target_reduction))
                mesh = mesh.simplify_quadric_decimation(target_reduction)
                logger.info("Simplified mesh to %s vertices", len(mesh.vertices))
            except Exception as e:
                logger.warning("Mesh simplification failed, using original: %s", e)

        # Wind every face the same way, and outward, or the shading turns the
        # model into camouflage. See ``_repair_winding``; it must run before the
        # vertices below are read, since a future repair step could move them.
        try:
            _repair_winding(mesh, trimesh, str(stl_path))
        except Exception as e:  # best-effort: a flat render beats no thumbnail
            logger.debug("Winding repair skipped (%s): %s", e, stl_path)

        # Get mesh bounds and center it
        vertices = mesh.vertices
        bounds_min = vertices.min(axis=0)
        bounds_max = vertices.max(axis=0)
        center = (bounds_min + bounds_max) / 2
        vertices_centered = vertices - center

        # Scale to fit in view
        max_extent = (bounds_max - bounds_min).max()
        if max_extent > 0:
            scale = 1.0 / max_extent
            vertices_scaled = vertices_centered * scale
        else:
            vertices_scaled = vertices_centered

        # Create figure with dark background
        fig = plt.figure(figsize=(size / 100, size / 100), dpi=100)
        fig.patch.set_facecolor(BACKGROUND_COLOR)

        ax = fig.add_subplot(111, projection="3d")
        ax.set_facecolor(BACKGROUND_COLOR)

        # Create polygon collection from mesh faces
        # Index with the face array rather than building a list of lists. Same
        # data, and Poly3DCollection accepts it directly — but shading walks this
        # structure to generate normals, and on an 82k-face mesh the list form
        # costs ~0.19s against ~0.007s for the ndarray. It speeds up the unshaded
        # path too.
        faces = mesh.faces
        poly3d = vertices_scaled[faces]

        # ``shade=True`` needs a real ``edgecolors``: matplotlib shades the edge
        # colours alongside the face colours, and an empty array (``"none"``)
        # makes it raise on the broadcast. Keep the two in step if either moves.
        collection = Poly3DCollection(
            poly3d,
            facecolors=BAMBU_GREEN,
            edgecolors=BAMBU_GREEN,
            linewidths=0.1,
            alpha=0.9,
            **_shade_kwargs(poly3d, LightSource),
        )
        ax.add_collection3d(collection)

        # Set axis limits
        ax.set_xlim(-0.6, 0.6)
        ax.set_ylim(-0.6, 0.6)
        ax.set_zlim(-0.6, 0.6)

        # Set view angle (isometric-ish)
        ax.view_init(elev=VIEW_ELEV_DEG, azim=VIEW_AZIM_DEG)

        # Remove axes and grid
        ax.set_axis_off()
        ax.grid(False)

        # Remove margins
        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

        # Save thumbnail
        thumb_filename = f"{uuid.uuid4().hex}.png"
        thumb_path = thumbnails_dir / thumb_filename  # SEC-PATH-OK: thumb_filename = uuid.uuid4().hex + ".png"

        fig.savefig(
            thumb_path,
            format="png",
            facecolor=BACKGROUND_COLOR,
            edgecolor="none",
            bbox_inches="tight",
            pad_inches=0.05,
            dpi=100,
        )
        plt.close(fig)

        logger.info("Generated STL thumbnail: %s", thumb_path)
        return str(thumb_path)

    except ImportError as e:
        logger.warning("STL thumbnail generation unavailable (missing dependencies): %s", e)
        return None
    except Exception as e:
        # Log the traceback, not just the message: a bare
        # "unsupported operand type(s) for /: 'str' and 'str'" gives no clue
        # which line failed, and the fault is data-/environment-specific
        # enough that it can't be reproduced from a clean STL — the traceback
        # in the next support bundle is what pinpoints it (#1480).
        logger.warning("Failed to generate STL thumbnail for %s: %s", stl_path, e, exc_info=True)
        return None
