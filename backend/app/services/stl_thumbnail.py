"""STL thumbnail generation service.

Generates PNG thumbnails from STL files using trimesh and matplotlib.
Supports both ASCII and binary STL formats, handles large meshes via simplification.
"""

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Maximum vertices before simplification is applied
MAX_VERTICES = 100000

# Default thumbnail size
DEFAULT_SIZE = (256, 256)


def generate_stl_thumbnail(
    stl_path: str | Path,
    output_path: str | Path,
    size: tuple[int, int] = DEFAULT_SIZE,
) -> bool:
    """Generate a PNG thumbnail from an STL file.

    Args:
        stl_path: Path to the input STL file
        output_path: Path where the PNG thumbnail will be saved
        size: Tuple of (width, height) for the output image

    Returns:
        True if thumbnail was generated successfully, False otherwise
    """
    try:
        import matplotlib

        matplotlib.use("Agg")  # Use non-interactive backend
        import matplotlib.pyplot as plt
        import numpy as np
        import trimesh

        # Load the STL file
        mesh = trimesh.load(str(stl_path), file_type="stl")

        if mesh is None or not hasattr(mesh, "vertices") or len(mesh.vertices) == 0:
            logger.warning(f"Failed to load STL or empty mesh: {stl_path}")
            return False

        # Simplify if mesh is too large
        if len(mesh.vertices) > MAX_VERTICES:
            logger.info(f"Simplifying mesh with {len(mesh.vertices)} vertices to ~{MAX_VERTICES}")
            # Calculate target face count based on vertex ratio
            target_faces = int(len(mesh.faces) * (MAX_VERTICES / len(mesh.vertices)))
            try:
                mesh = mesh.simplify_quadric_decimation(target_faces)
            except Exception as e:
                logger.warning(f"Mesh simplification failed, using original: {e}")

        # Create figure with transparent background
        fig = plt.figure(figsize=(size[0] / 100, size[1] / 100), dpi=100)
        ax = fig.add_subplot(111, projection="3d")

        # Get mesh vertices and faces
        vertices = mesh.vertices
        faces = mesh.faces

        # Center the mesh
        center = vertices.mean(axis=0)
        vertices = vertices - center

        # Scale to fit in view
        max_extent = np.abs(vertices).max()
        if max_extent > 0:
            vertices = vertices / max_extent

        # Create triangles for plotting
        triangles = vertices[faces]

        # Plot the mesh with a nice color scheme
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection

        collection = Poly3DCollection(
            triangles,
            alpha=1.0,
            facecolor="#00AE42",  # Bambu green
            edgecolor="#008833",  # Darker green for edges
            linewidths=0.1,
        )
        ax.add_collection3d(collection)

        # Set axis limits
        ax.set_xlim(-1, 1)
        ax.set_ylim(-1, 1)
        ax.set_zlim(-1, 1)

        # Set viewing angle (isometric-ish)
        ax.view_init(elev=25, azim=45)

        # Remove axes for cleaner look
        ax.set_axis_off()

        # Set background color
        ax.set_facecolor("#1a1a1a")
        fig.patch.set_facecolor("#1a1a1a")

        # Tight layout to minimize whitespace
        plt.tight_layout(pad=0)

        # Save the figure
        plt.savefig(
            str(output_path),
            format="png",
            dpi=100,
            facecolor="#1a1a1a",
            bbox_inches="tight",
            pad_inches=0.05,
        )
        plt.close(fig)

        logger.info(f"Generated STL thumbnail: {output_path}")
        return True

    except ImportError as e:
        logger.error(f"Missing dependency for STL thumbnails: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to generate STL thumbnail for {stl_path}: {e}")
        return False


def generate_stl_thumbnail_bytes(
    stl_data: bytes,
    size: tuple[int, int] = DEFAULT_SIZE,
) -> bytes | None:
    """Generate a PNG thumbnail from STL data in memory.

    Args:
        stl_data: Raw STL file data (binary or ASCII)
        size: Tuple of (width, height) for the output image

    Returns:
        PNG image data as bytes, or None on failure
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        import trimesh

        # Load from bytes
        mesh = trimesh.load(
            file_obj=io.BytesIO(stl_data),
            file_type="stl",
        )

        if mesh is None or not hasattr(mesh, "vertices") or len(mesh.vertices) == 0:
            logger.warning("Failed to load STL from bytes or empty mesh")
            return None

        # Simplify if mesh is too large
        if len(mesh.vertices) > MAX_VERTICES:
            target_faces = int(len(mesh.faces) * (MAX_VERTICES / len(mesh.vertices)))
            try:
                mesh = mesh.simplify_quadric_decimation(target_faces)
            except Exception:
                pass  # Use original if simplification fails

        # Create figure
        fig = plt.figure(figsize=(size[0] / 100, size[1] / 100), dpi=100)
        ax = fig.add_subplot(111, projection="3d")

        vertices = mesh.vertices
        faces = mesh.faces

        # Center and scale
        center = vertices.mean(axis=0)
        vertices = vertices - center
        max_extent = np.abs(vertices).max()
        if max_extent > 0:
            vertices = vertices / max_extent

        triangles = vertices[faces]

        from mpl_toolkits.mplot3d.art3d import Poly3DCollection

        collection = Poly3DCollection(
            triangles,
            alpha=1.0,
            facecolor="#00AE42",
            edgecolor="#008833",
            linewidths=0.1,
        )
        ax.add_collection3d(collection)

        ax.set_xlim(-1, 1)
        ax.set_ylim(-1, 1)
        ax.set_zlim(-1, 1)
        ax.view_init(elev=25, azim=45)
        ax.set_axis_off()
        ax.set_facecolor("#1a1a1a")
        fig.patch.set_facecolor("#1a1a1a")
        plt.tight_layout(pad=0)

        # Save to bytes buffer
        buf = io.BytesIO()
        plt.savefig(
            buf,
            format="png",
            dpi=100,
            facecolor="#1a1a1a",
            bbox_inches="tight",
            pad_inches=0.05,
        )
        plt.close(fig)

        buf.seek(0)
        return buf.read()

    except ImportError as e:
        logger.error(f"Missing dependency for STL thumbnails: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to generate STL thumbnail from bytes: {e}")
        return None
