"""Unit tests for the PDF thumbnail service (#2976).

The renderer is real pypdfium2; the document is built by hand so no fixture
file is needed and the page geometry is known exactly.
"""

import builtins
import io
from pathlib import Path

import pytest
from PIL import Image

from backend.app.services.pdf_thumbnail import generate_pdf_thumbnail


def minimal_pdf(width: int = 200, height: int = 400) -> bytes:
    """A valid one-page PDF with a filled rectangle and the given MediaBox."""
    content = b"0 0 1 rg 20 20 100 100 re f"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents 4 0 R >>" % (width, height),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


@pytest.fixture
def thumbnails_dir(tmp_path: Path) -> Path:
    out = tmp_path / "thumbs"
    out.mkdir()
    return out


def test_portrait_page_renders_to_256px_longest_edge(tmp_path: Path, thumbnails_dir: Path):
    pdf_path = tmp_path / "drawing.pdf"
    pdf_path.write_bytes(minimal_pdf(200, 400))

    result = generate_pdf_thumbnail(pdf_path, thumbnails_dir)

    assert result is not None
    thumb = Path(result)
    assert thumb.parent == thumbnails_dir
    assert thumb.suffix == ".png"
    with Image.open(thumb) as img:
        assert img.format == "PNG"
        assert img.mode == "RGB"
        assert img.size == (128, 256)
        # The blue rectangle sits bottom-left in PDF space, so top-right is paper.
        assert img.getpixel((120, 5)) == (255, 255, 255)
        assert img.getpixel((30, 220)) == (0, 0, 255)


def test_landscape_page_scales_by_width(tmp_path: Path, thumbnails_dir: Path):
    pdf_path = tmp_path / "wide.pdf"
    pdf_path.write_bytes(minimal_pdf(800, 200))

    result = generate_pdf_thumbnail(pdf_path, thumbnails_dir)

    assert result is not None
    with Image.open(result) as img:
        assert img.size == (256, 64)


def test_size_parameter_sets_longest_edge(tmp_path: Path, thumbnails_dir: Path):
    pdf_path = tmp_path / "small.pdf"
    pdf_path.write_bytes(minimal_pdf(300, 300))

    result = generate_pdf_thumbnail(pdf_path, thumbnails_dir, size=64)

    assert result is not None
    with Image.open(result) as img:
        assert img.size == (64, 64)


def test_garbage_bytes_return_none_without_raising(tmp_path: Path, thumbnails_dir: Path, caplog):
    pdf_path = tmp_path / "garbage.pdf"
    pdf_path.write_bytes(b"this is not a pdf at all")

    with caplog.at_level("INFO", logger="backend.app.services.pdf_thumbnail"):
        result = generate_pdf_thumbnail(pdf_path, thumbnails_dir)

    assert result is None
    assert list(thumbnails_dir.iterdir()) == []
    assert any(record.levelname == "INFO" and "garbage.pdf" in record.message for record in caplog.records)
    assert not any(record.levelname == "WARNING" for record in caplog.records)


def test_missing_file_returns_none(tmp_path: Path, thumbnails_dir: Path):
    assert generate_pdf_thumbnail(tmp_path / "absent.pdf", thumbnails_dir) is None


def test_missing_renderer_returns_none(tmp_path: Path, thumbnails_dir: Path, monkeypatch, caplog):
    """An install without a pypdfium2 wheel degrades to no thumbnail, no error."""
    pdf_path = tmp_path / "drawing.pdf"
    pdf_path.write_bytes(minimal_pdf())
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pypdfium2":
            raise ImportError("No module named 'pypdfium2'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with caplog.at_level("INFO", logger="backend.app.services.pdf_thumbnail"):
        result = generate_pdf_thumbnail(pdf_path, thumbnails_dir)

    assert result is None
    assert list(thumbnails_dir.iterdir()) == []
    assert any("pypdfium2 unavailable" in record.message for record in caplog.records)


def test_accepts_string_paths(tmp_path: Path, thumbnails_dir: Path):
    pdf_path = tmp_path / "drawing.pdf"
    pdf_path.write_bytes(minimal_pdf())

    result = generate_pdf_thumbnail(str(pdf_path), str(thumbnails_dir))

    assert result is not None
    assert Path(result).exists()
