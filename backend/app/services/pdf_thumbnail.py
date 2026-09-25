"""PDF thumbnail generation service (#2976).

Renders the first page of a PDF to a PNG with pypdfium2 so a PDF card in the
File Manager gets its thumbnail on upload, like STL, instead of waiting for
someone to open the preview. Same output shape as the client-rendered
thumbnails the preview posts back (longest edge 256 px, opaque PNG), so
nothing downstream tells the two apart.
"""

import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_pdf_thumbnail(
    pdf_path: Path | str,
    thumbnails_dir: Path | str,
    size: int = 256,
) -> str | None:
    """Render page 1 of ``pdf_path`` into ``thumbnails_dir`` as a PNG.

    Mirrors ``generate_stl_thumbnail``'s contract so the upload, ZIP-extract,
    batch and external-scan call sites treat the two alike: the file is named
    ``<uuid>.png`` inside ``thumbnails_dir`` and the absolute path comes back
    as a string, or ``None`` when nothing was written.

    Never raises. A missing pypdfium2 (no wheel for the platform, or an install
    that predates the dependency), an encrypted or corrupt file and an empty
    document all return ``None`` and log at INFO — the browser still posts its
    own render the first time the preview opens, so this is not an error path.
    """
    pdf_path = Path(pdf_path)
    thumbnails_dir = Path(thumbnails_dir)

    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        logger.info("PDF thumbnail skipped for %s (pypdfium2 unavailable: %s)", pdf_path.name, exc)
        return None

    try:
        doc = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:  # pdfium raises its own error type for anything it cannot open
        logger.info("PDF thumbnail skipped for %s (cannot open: %s)", pdf_path.name, exc)
        return None

    try:
        if len(doc) == 0:
            logger.info("PDF thumbnail skipped for %s (no pages)", pdf_path.name)
            return None
        page = doc[0]
        try:
            width, height = page.get_size()
            longest = max(width, height)
            if longest <= 0:
                logger.info("PDF thumbnail skipped for %s (degenerate page size)", pdf_path.name)
                return None
            bitmap = page.render(scale=size / longest)
            image = bitmap.to_pil().convert("RGB")
        finally:
            page.close()

        thumb_filename = f"{uuid.uuid4().hex}.png"
        thumb_path = thumbnails_dir / thumb_filename  # SEC-PATH-OK: thumb_filename = uuid.uuid4().hex + ".png"
        image.save(thumb_path, "PNG", optimize=True)
        logger.info("Generated PDF thumbnail: %s", thumb_path)
        return str(thumb_path)
    except Exception as exc:
        logger.info("PDF thumbnail skipped for %s (render failed: %s)", pdf_path.name, exc)
        return None
    finally:
        doc.close()
