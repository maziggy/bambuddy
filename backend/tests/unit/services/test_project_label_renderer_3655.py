"""Project labels (#3655): what a bag label for a project's parts shows.

Text is checked in the content stream of an uncompressed PDF, as in
``test_label_renderer_fields.py``.
"""

from __future__ import annotations

import io
import re
from datetime import date
from unittest.mock import patch

import pytest
from PIL import Image
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

from backend.app.services import project_label_renderer
from backend.app.services.label_renderer import (
    SheetLayout,
    _text_width,
    label_size_mm,
    pdf_to_pngs,
    render_label_preview_pdf,
    render_labels,
)
from backend.app.services.project_label_renderer import (
    ALL_PROJECT_LABEL_FIELDS,
    DEFAULT_PROJECT_LABEL_FIELDS,
    ProjectLabelData,
    draw_project_label,
)

ALL = frozenset(ALL_PROJECT_LABEL_FIELDS)


def _data(**overrides) -> ProjectLabelData:
    values = {
        "project_id": 42,
        "name": "Banana Slugs",
        "deeplink_url": "https://example.test/projects/42",
        "color": "#FFD700",
        "parent_name": "Spring Fair",
        "due_date": date(2026, 12, 5),
        "printed": date(2026, 11, 20),
        "completed_parts": 7,
        "target_parts": 10,
        "quantity": 12,
        "note": "Order 1042",
    }
    values.update(overrides)
    return ProjectLabelData(**values)


def _text(template, data: ProjectLabelData, fields=DEFAULT_PROJECT_LABEL_FIELDS, monochrome=False) -> bytes:
    """The page's content stream: only the text and shapes drawn."""
    w_mm, h_mm = label_size_mm(template)
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(w_mm * mm, h_mm * mm), pageCompression=0)
    draw_project_label(c, 0, 0, w_mm * mm, h_mm * mm, data, monochrome, fields)
    c.showPage()
    c.save()
    pdf = buf.getvalue()
    refs = re.findall(rb"/Contents (\d+) 0 R", pdf)
    assert len(refs) == 1
    m = re.search(rb"(?<!\d)" + refs[0] + rb" 0 obj.*?stream\r?\n(.*?)endstream", pdf, re.S)
    assert m is not None
    return m.group(1)


def test_default_fields_are_a_bag_label():
    assert {"name", "note", "due_date", "color", "qr", "quantity"} == DEFAULT_PROJECT_LABEL_FIELDS


def test_default_label_shows_what_is_in_the_bag():
    pdf = _text("box_62x29", _data())
    for present in (b"Banana Slugs", b"Order 1042", b"2026-12-05", b" 12) Tj"):
        assert present in pdf
    for absent in (b"Spring Fair", b"7/10", b"2026-11-20", b"#42"):
        assert absent not in pdf
    assert b" Do" in pdf  # the QR code, drawn as an image


def test_every_field_prints_on_a_label_with_room():
    pdf = _text("ams_holder_75x55", _data(), ALL)
    for present in (b"Spring Fair", b"Banana Slugs", b"Order 1042", b"7/10", b"2026-12-05", b"2026-11-20", b"#42"):
        assert present in pdf


def test_parts_without_a_target_print_the_count():
    assert b"(7)" in _text("ams_holder_75x55", _data(target_parts=None), frozenset({"parts"}))
    # Nothing printed yet and no target: no line at all, not a lone 0.
    pdf = _text("ams_holder_75x55", _data(target_parts=None, completed_parts=0), frozenset({"parts", "name"}))
    assert b"(0)" not in pdf


def test_without_quantity_or_note_those_lines_are_left_out():
    pdf = _text("box_62x29", _data(quantity=None, note=None))
    assert b"Banana Slugs" in pdf
    assert b"Order" not in pdf
    assert b" 12) Tj" not in pdf


def test_unticked_lines_and_qr_are_left_out():
    pdf = _text("box_62x29", _data(), DEFAULT_PROJECT_LABEL_FIELDS - {"qr", "due_date"})
    assert b" Do" not in pdf
    assert b"2026-12-05" not in pdf


def test_monochrome_and_unticked_colour_drop_the_stripe():
    # The stripe is the only filled shape in the project colour (FFD700 → 1 .843137 0 rg).
    colour = b"1 .843137 0 rg"
    assert colour in _text("box_62x29", _data())
    assert colour not in _text("box_62x29", _data(), monochrome=True)
    assert colour not in _text("box_62x29", _data(), DEFAULT_PROJECT_LABEL_FIELDS - {"color"})
    assert colour not in _text("box_62x29", _data(color=None))


def test_a_long_name_wraps_instead_of_running_off():
    name = "A very long project name that cannot fit on one line of a small label"
    pdf = _text("ams_holder_75x55", _data(name=name), frozenset({"name"}))
    assert pdf.count(b"Tj") >= 2  # drawn as more than one line


def test_renders_through_the_shared_templates():
    data = [_data(), _data(project_id=43, name="Nunu")]
    for template in ("box_62x29", "avery_l7160", SheetLayout("a4", 90, 29, 2, 9, top_mm=10, left_mm=10)):
        pdf = render_labels(template, data, draw=draw_project_label, subject="project", fields=ALL)
        assert pdf.startswith(b"%PDF")
        assert b"Bambuddy project labels" in pdf


def test_preview_png_is_the_label_size():
    pdf = render_label_preview_pdf("box_62x29", _data(), draw=draw_project_label, fields=DEFAULT_PROJECT_LABEL_FIELDS)
    (png,) = pdf_to_pngs(pdf, 300)
    img = Image.open(io.BytesIO(png))
    assert abs(img.width - round(62 / 25.4 * 300)) <= 1
    assert abs(img.height - round(29 / 25.4 * 300)) <= 1


@pytest.mark.parametrize(
    "template",
    ["box_40x30", "avery_5160", SheetLayout("a4", 40, 20, 1, 1, 0, 0), SheetLayout("a4", 95, 20, 1, 1, 0, 0)],
)
def test_all_fields_fit_on_short_stock(template):
    data = _data(project_id=2147483647, quantity=100000, name="部品" * 100, note="注文" * 100)
    with patch.object(project_label_renderer, "_draw_text", wraps=project_label_renderer._draw_text) as draw:
        _text(template, data, ALL)
    w, h = label_size_mm(template)
    texts = []
    for call in draw.call_args_list:
        _, x, y, text, font, size = call.args
        assert size > 0
        assert 0 <= x < x + _text_width(text, font, size) <= w * mm
        assert 0 < y < y + size <= h * mm
        texts.append(text)
    assert {"× 100000", "#2147483647", "7/10", "2026-12-05", "2026-11-20"}.issubset(texts)
    assert any(t.startswith("部") for t in texts)
    assert any(t.startswith("注") for t in texts)
    assert any(t.startswith("Spring") for t in texts)
