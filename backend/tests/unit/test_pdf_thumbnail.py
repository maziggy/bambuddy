"""Unit tests for server-side PDF thumbnails (#2976).

generate_pdf_thumbnail renders page one with PDFium into the same square,
white-backed PNG the browser preview posts back, and returns None rather than
raising for anything it cannot render.
"""

from pathlib import Path

import pytest
from PIL import Image
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from backend.app.services.pdf_thumbnail import generate_pdf_thumbnail


def _make_pdf(path: Path, pagesize=A4, pages: int = 1) -> Path:
    c = canvas.Canvas(str(path), pagesize=pagesize)
    for n in range(pages):
        # A solid black block in the top-left corner of each page, so the
        # render can be told apart from a blank white square.
        c.setFillColorRGB(0, 0, 0)
        c.rect(0, pagesize[1] - 200, 200, 200, fill=1, stroke=0)
        c.drawString(72, 72, f"page {n + 1}")
        c.showPage()
    c.save()
    return path


def test_renders_page_one_as_square_png(tmp_path):
    pdf = _make_pdf(tmp_path / "doc.pdf", pages=3)
    thumb = generate_pdf_thumbnail(pdf, tmp_path)

    assert thumb is not None
    with Image.open(thumb) as img:
        assert img.format == "PNG"
        assert img.size == (256, 256)
        assert img.mode == "RGB"
        # Something was drawn: not a blank white square.
        assert img.getextrema() != ((255, 255), (255, 255), (255, 255))


def test_portrait_page_is_centred_on_white(tmp_path):
    pdf = _make_pdf(tmp_path / "portrait.pdf")
    thumb = generate_pdf_thumbnail(pdf, tmp_path)

    with Image.open(thumb) as img:
        # A4 portrait is narrower than tall: the side margins are padding.
        assert img.getpixel((2, 128)) == (255, 255, 255)
        assert img.getpixel((253, 128)) == (255, 255, 255)


def test_landscape_page_is_centred_on_white(tmp_path):
    pdf = _make_pdf(tmp_path / "landscape.pdf", pagesize=landscape(A4))
    thumb = generate_pdf_thumbnail(pdf, tmp_path)

    with Image.open(thumb) as img:
        assert img.size == (256, 256)
        assert img.getpixel((128, 2)) == (255, 255, 255)
        assert img.getpixel((128, 253)) == (255, 255, 255)


def test_huge_mediabox_still_yields_bounded_thumbnail(tmp_path):
    # 5 m x 5 m: the render scale comes from the page size, so this must not
    # allocate a page-sized bitmap.
    pdf = _make_pdf(tmp_path / "huge.pdf", pagesize=(14400, 14400))
    thumb = generate_pdf_thumbnail(pdf, tmp_path)

    with Image.open(thumb) as img:
        assert img.size == (256, 256)


@pytest.mark.parametrize("content", [b"", b"not a pdf at all", b"%PDF-1.4\n%truncated"])
def test_unreadable_pdf_returns_none(tmp_path, content):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(content)

    assert generate_pdf_thumbnail(bad, tmp_path) is None
    assert list(tmp_path.glob("*.png")) == []


def test_missing_file_returns_none(tmp_path):
    assert generate_pdf_thumbnail(tmp_path / "missing.pdf", tmp_path) is None
