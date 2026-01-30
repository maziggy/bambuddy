"""STL Thumbnail Generation Service.

Generates thumbnail images from STL files using trimesh and matplotlib.
"""

import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Bambu green color for rendering
BAMBU_GREEN = "#00AE42"
BACKGROUND_COLOR = "#1a1a1a"

# Maximum vertices before simplification
MAX_VERTICES = 100000


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
    try:
        import matplotlib
        import trimesh

        # Use Agg backend for headless rendering
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection

        # Load the STL file
        mesh = trimesh.load(str(stl_path), force="mesh")

        if mesh is None or not hasattr(mesh, "vertices") or len(mesh.vertices) == 0:
            logger.warning(f"Failed to load STL or empty mesh: {stl_path}")
            return None

        # Simplify large meshes for performance
        if len(mesh.vertices) > MAX_VERTICES:
            logger.info(f"Simplifying mesh from {len(mesh.vertices)} vertices")
            try:
                # Calculate reduction ratio (0-1 range)
                # e.g., 124633 vertices -> 100000 means keep ~80%, so reduce by ~20%
                keep_ratio = MAX_VERTICES / len(mesh.vertices)
                target_reduction = 1.0 - keep_ratio
                # Clamp to valid range (0.01 to 0.99)
                target_reduction = max(0.01, min(0.99, target_reduction))
                mesh = mesh.simplify_quadric_decimation(target_reduction)
                logger.info(f"Simplified mesh to {len(mesh.vertices)} vertices")
            except Exception as e:
                logger.warning(f"Mesh simplification failed, using original: {e}")

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
        faces = mesh.faces
        poly3d = [[vertices_scaled[vertex] for vertex in face] for face in faces]

        collection = Poly3DCollection(
            poly3d,
            facecolors=BAMBU_GREEN,
            edgecolors=BAMBU_GREEN,
            linewidths=0.1,
            alpha=0.9,
        )
        ax.add_collection3d(collection)

        # Set axis limits
        ax.set_xlim(-0.6, 0.6)
        ax.set_ylim(-0.6, 0.6)
        ax.set_zlim(-0.6, 0.6)

        # Set view angle (isometric-ish)
        ax.view_init(elev=25, azim=45)

        # Remove axes and grid
        ax.set_axis_off()
        ax.grid(False)

        # Remove margins
        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

        # Save thumbnail
        thumb_filename = f"{uuid.uuid4().hex}.png"
        thumb_path = thumbnails_dir / thumb_filename

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

        logger.info(f"Generated STL thumbnail: {thumb_path}")
        return str(thumb_path)

    except ImportError as e:
        logger.warning(f"STL thumbnail generation unavailable (missing dependencies): {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to generate STL thumbnail for {stl_path}: {e}")
        return None
