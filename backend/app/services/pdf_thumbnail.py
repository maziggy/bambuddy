"""PDF Thumbnail Generation Service.

Renders the first page of a PDF with PDFium (pypdfium2) so a library PDF gets
its grid thumbnail at upload/scan time. Before this, PDF thumbnails only
existed once somebody had opened the in-browser preview, which posted its
first render back (#2976); that route stays as the fallback for a PDF this
renderer cannot read.
"""

import logging
import threading
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# PDFium is not thread-safe, and callers run this through asyncio.to_thread,
# so two uploads landing together would otherwise enter it concurrently.
_PDFIUM_LOCK = threading.Lock()

# Rendered at twice the output size and downsampled, so small print on the
# page stays legible instead of breaking up into aliased specks.
_SUPERSAMPLE = 2


def generate_pdf_thumbnail(pdf_path: Path, thumbnails_dir: Path, size: int = 256) -> str | None:
    """Render page one of a PDF into a square, white-backed PNG thumbnail.

    Same shape as the thumbnail the browser preview posts back (a ``size``
    square with the page centred on white), so a grid mixing both kinds
    looks uniform. Returns the thumbnail path, or None if the file cannot be
    rendered - an encrypted, empty or damaged PDF simply gets no thumbnail.
    """
    try:
        import pypdfium2 as pdfium
        from PIL import Image
    except ImportError:
        logger.warning("pypdfium2 not installed, skipping PDF thumbnail for %s", pdf_path.name)
        return None

    try:
        with _PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(str(pdf_path))
            try:
                if len(pdf) == 0:
                    return None
                page = pdf[0]
                try:
                    width, height = page.get_size()
                    if width <= 0 or height <= 0:
                        return None
                    # Scale from the page's own size, so the bitmap is bounded
                    # by the output size whatever MediaBox the file declares.
                    scale = (size * _SUPERSAMPLE) / max(width, height)
                    rendered = page.render(scale=scale).to_pil()
                finally:
                    page.close()
            finally:
                pdf.close()

        rendered = rendered.convert("RGB")
        rendered.thumbnail((size, size), Image.Resampling.LANCZOS)
        thumbnail = Image.new("RGB", (size, size), (255, 255, 255))
        thumbnail.paste(rendered, ((size - rendered.width) // 2, (size - rendered.height) // 2))

        thumb_filename = f"{uuid.uuid4().hex}.png"
        thumb_path = thumbnails_dir / thumb_filename  # SEC-PATH-OK: thumb_filename = uuid.uuid4().hex + ".png"
        thumbnail.save(thumb_path, "PNG", optimize=True)
        return str(thumb_path)
    except Exception as e:
        logger.warning("Failed to create PDF thumbnail for %s: %s", pdf_path.name, e)
        return None
