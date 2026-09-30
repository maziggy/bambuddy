"""Choosing what a spool label shows, and PNG output (#2981).

Text is checked in an uncompressed PDF, where ReportLab writes each string as
it was drawn.
"""

from __future__ import annotations

import io
from datetime import date
from unittest.mock import patch

import pytest
from PIL import Image
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

from backend.app.services.label_renderer import (
    ALL_LABEL_FIELDS,
    DEFAULT_LABEL_FIELDS,
    LabelData,
    _draw_label,
    label_size_mm,
    pdf_to_pngs,
    render_label_preview_pdf,
    render_labels,
)

ALL = frozenset(ALL_LABEL_FIELDS)


def _data(**overrides) -> LabelData:
    values = {
        "spool_id": 128,
        "name": "Jade White",
        "material": "PLA",
        "brand": "Bambu Lab",
        "subtype": "Matte",
        "rgba": "E8F0E0FF",
        "storage_location": "Shelf 2",
        "deeplink_url": "https://example.test/inventory?spool=128",
        "material_number": "MN-15",
        "nozzle_temp_min": 190,
        "nozzle_temp_max": 230,
        "label_weight": 1000,
        "note": "Dry first",
        "added": date(2026, 9, 30),
    }
    values.update(overrides)
    return LabelData(**values)


def _text(template: str, data: LabelData, fields=DEFAULT_LABEL_FIELDS) -> bytes:
    w_mm, h_mm = label_size_mm(template)
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(w_mm * mm, h_mm * mm), pageCompression=0)
    _draw_label(c, 0, 0, w_mm * mm, h_mm * mm, data, False, fields)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_default_fields_are_what_labels_always_carried():
    assert {"brand", "material", "hex", "name", "location", "qr", "spool_id"} == DEFAULT_LABEL_FIELDS


def test_default_fields_leave_the_new_lines_off():
    pdf = _text("ams_holder_75x55", _data())
    for present in (b"Bambu Lab", b"Jade White", b"Shelf 2", b"#E8F0E0", b"#128"):
        assert present in pdf
    for absent in (b"MN-15", b"190", b"1000 g", b"Dry first", b"2026-09-30"):
        assert absent not in pdf


def test_every_field_prints_on_a_label_with_room():
    pdf = _text("ams_holder_75x55", _data(), ALL)
    for present in (b"MN-15", b"190", b"230", b"1000 g", b"Dry first", b"2026-09-30", b"#128"):
        assert present in pdf


def test_a_single_temperature_prints_alone():
    pdf = _text("ams_holder_75x55", _data(nozzle_temp_max=None), frozenset({"temps"}))
    assert b"190 " in pdf
    assert b"230" not in pdf


def test_unticked_lines_are_left_out():
    pdf = _text("ams_holder_75x55", _data(), ALL - {"brand", "spool_id", "hex"})
    assert b"Bambu Lab" not in pdf
    assert b"#128" not in pdf
    assert b"#E8F0E0" not in pdf
    assert b"Jade White" in pdf


def test_no_qr_without_the_qr_field():
    with patch("backend.app.services.label_renderer._draw_qr") as draw_qr:
        render_labels("box_40x30", [_data()], fields=ALL - {"qr"})
    draw_qr.assert_not_called()
    with patch("backend.app.services.label_renderer._draw_qr") as draw_qr:
        render_labels("box_40x30", [_data()])
    draw_qr.assert_called_once()


def test_lines_that_do_not_fit_stop_above_the_spool_id():
    """A 40 x 30 label has no room for all ten lines. The ones that don't fit
    are dropped rather than drawn over the spool ID at the bottom."""
    drawn: list[tuple[float, str]] = []
    real = rl_canvas.Canvas.drawString

    def record(self, x, y, text, *args, **kwargs):
        drawn.append((y, text))
        return real(self, x, y, text, *args, **kwargs)

    with patch.object(rl_canvas.Canvas, "drawString", record):
        _text("box_40x30", _data(), ALL)

    id_y = next(y for y, text in drawn if text == "#128")
    other = [y for y, text in drawn if text != "#128"]
    assert other, "some lines must still print"
    # The ID is 16 pt tall from its baseline; every other baseline sits above it.
    assert min(other) >= id_y + 16
    assert "2026-09-30" not in [text for _, text in drawn]


def test_name_is_not_repeated_when_it_is_the_subtype():
    """A Spoolman filament named after its colour gives subtype == name."""
    pdf = _text("ams_holder_75x55", _data(name="Matte"))
    assert pdf.count(b"Matte") == 1


def test_name_prints_when_the_material_line_is_off():
    pdf = _text("ams_holder_75x55", _data(name="Matte"), DEFAULT_LABEL_FIELDS - {"material"})
    assert b"Matte" in pdf


def test_tight_layout_honours_the_fields():
    """The small layout only has brand, material, hex and ID; they obey the choice too."""
    w, h = 40 * mm, 15 * mm
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(w, h), pageCompression=0)
    _draw_label(c, 0, 0, w, h, _data(), False, frozenset({"hex"}))
    c.showPage()
    c.save()
    pdf = buf.getvalue()
    assert b"#E8F0E0" in pdf
    assert b"Bambu Lab" not in pdf
    assert b"#128" not in pdf


def _assert_size(image: Image.Image, w_mm: float, h_mm: float, dpi: int) -> None:
    """PDFium rounds a fractional pixel up, so allow one either way."""
    expected = (w_mm / 25.4 * dpi, h_mm / 25.4 * dpi)
    assert all(abs(got - want) <= 1 for got, want in zip(image.size, expected, strict=True)), (image.size, expected)


@pytest.mark.parametrize(
    ("template", "size_mm"),
    [("box_40x30", (40.0, 30.0)), ("avery_l7160", (63.5, 38.1)), ("avery_5160", (66.675, 25.4))],
)
def test_label_size_is_the_cell_for_a_sheet(template, size_mm):
    assert label_size_mm(template) == size_mm


def test_preview_is_one_label_of_the_templates_size():
    """A sheet's preview is one cell, not a whole A4 page."""
    pngs = pdf_to_pngs(render_label_preview_pdf("avery_l7160", _data(), fields=ALL), 300)
    assert len(pngs) == 1
    image = Image.open(io.BytesIO(pngs[0]))
    _assert_size(image, 63.5, 38.1, 300)


@pytest.mark.parametrize("dpi", [203, 300, 600])
def test_png_is_printed_at_the_requested_resolution(dpi):
    pngs = pdf_to_pngs(render_labels("box_40x30", [_data()]), dpi)
    image = Image.open(io.BytesIO(pngs[0]))
    assert image.format == "PNG"
    _assert_size(image, 40, 30, dpi)
    assert tuple(round(v) for v in image.info["dpi"]) == (dpi, dpi)


def test_one_png_per_page():
    roll = pdf_to_pngs(render_labels("box_40x30", [_data(spool_id=i) for i in range(1, 4)]), 203)
    assert len(roll) == 3
    sheets = pdf_to_pngs(render_labels("avery_l7160", [_data(spool_id=i) for i in range(1, 23)]), 72)
    assert len(sheets) == 2


def test_qr_stays_black_and_white_in_the_png():
    """Rasterised without image smoothing, so the QR's modules keep hard edges
    instead of grey fringes a 203 dpi head can't print (#1870)."""
    png = pdf_to_pngs(render_label_preview_pdf("box_40x30", _data(), fields=frozenset({"qr"})), 203)[0]
    image = Image.open(io.BytesIO(png)).convert("L")
    # Only the QR is drawn besides the swatch and hairline border; crop to it.
    w, h = image.size
    qr_region = image.crop((w // 2, 0, w, h))
    greys = sum(1 for v in qr_region.getdata() if 40 < v < 215)
    assert greys / (qr_region.width * qr_region.height) < 0.02
