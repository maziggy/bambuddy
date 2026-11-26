"""What a direct print sends to the Brother QL (#3612), for what the labels
learned since: Chinese, Japanese and Korean text (#3560), names that continue
on the next line (#3621) and project labels (#3655).

The PDF tests of those features say what is drawn. These look at the raster
lines the driver sends, which is what comes out of the printer.
"""

from __future__ import annotations

import io
import re
from datetime import date
from unittest.mock import patch

import pytest
from PIL import Image, ImageChops, ImageOps
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

from backend.app.api.routes.labels import print_pages
from backend.app.services import label_renderer
from backend.app.services.label_printers.brother_ql import fit_to_tape, rasterize
from backend.app.services.label_printers.brother_ql_media import HEAD_PINS, TAPES
from backend.app.services.label_renderer import (
    DEFAULT_LABEL_FIELDS,
    LabelData,
    RollLayout,
    _draw_label,
    _text_width,
    label_size_mm,
    render_labels,
)
from backend.app.services.project_label_renderer import (
    ALL_PROJECT_LABEL_FIELDS,
    ProjectLabelData,
    draw_project_label,
)

PROJECT_FIELDS = frozenset(ALL_PROJECT_LABEL_FIELDS)
# The narrowest tape, one that reads along the tape and the widest, which
# reads across the tape at 40 mm and along it at 90 mm.
ROLLS = [RollLayout("12", 60), RollLayout("29", 60), RollLayout("62", 40), RollLayout("62", 90)]
LONG_NAME = "Matte Charcoal Black Limited Edition Extra Tough Carbon Fibre Blend"
LONG_CJK_NAME = "拓竹哑光炭黑限量版超强韧碳纤维混合耗材第二批次"


def spool(name: str = "Red", **extra) -> LabelData:
    values = {
        "spool_id": 128,
        "name": name,
        "material": "PLA",
        "brand": "Brand",
        "rgba": "FF0000FF",
        "deeplink_url": "http://x/inventory?spool=128",
    }
    return LabelData(**(values | extra))


def project(name: str = "Banana Slugs", **extra) -> ProjectLabelData:
    values = {
        "project_id": 42,
        "name": name,
        "deeplink_url": "http://x/projects/42",
        "color": "#FFD700",
        "parent_name": "Spring Fair",
        "due_date": date(2026, 12, 5),
        "printed": date(2026, 11, 20),
        "completed_parts": 6,
        "target_parts": 10,
        "quantity": 12,
        "note": "Order 1042",
    }
    return ProjectLabelData(**(values | extra))


def sent_dots(roll: RollLayout, data, fields=DEFAULT_LABEL_FIELDS, draw=None) -> Image.Image:
    """The dots the printer prints for one label, white where it prints: the
    print path of ``enqueue_print`` (black and white, the printer's
    resolution, turned to the tape), decoded back from the driver's raster
    lines and cut to the tape's printable width."""
    pdf = render_labels(roll, [data], monochrome=True, fields=fields, draw=draw)
    [page] = print_pages(pdf, roll)
    lines = rasterize(fit_to_tape(page, roll.tape), roll.tape)
    spec = TAPES[roll.tape]
    head = Image.frombytes("1", (HEAD_PINS, len(lines)), b"".join(lines))
    left = spec.left_margin_pins
    return ImageOps.mirror(head.crop((left, 0, left + spec.printable_dots, len(lines)))).convert("L")


def same(a: Image.Image, b: Image.Image) -> bool:
    return a.size == b.size and ImageChops.difference(a, b).getbbox() is None


def base_fonts(pdf: bytes) -> set[bytes]:
    return set(re.findall(rb"/BaseFont /(?:[A-Z]{6}\+)?([^\s/>]+)", pdf))


def drawn_text(roll: RollLayout, data, fields=DEFAULT_LABEL_FIELDS, draw=_draw_label) -> list[tuple]:
    """Every piece of text drawn on one black-and-white label, as
    (x, baseline, text, font, size)."""
    drawn: list[tuple] = []
    real = rl_canvas.Canvas.drawString

    def record(self, x, y, text, *args, **kwargs):
        drawn.append((x, y, text, self._fontname, self._fontsize))
        return real(self, x, y, text, *args, **kwargs)

    w_mm, h_mm = label_size_mm(roll)
    c = rl_canvas.Canvas(io.BytesIO(), pagesize=(w_mm * mm, h_mm * mm))
    with patch.object(rl_canvas.Canvas, "drawString", record):
        draw(c, 0, 0, w_mm * mm, h_mm * mm, data, True, fields)
    return drawn


def text_lines(drawn: list[tuple]) -> list[str]:
    """The lines top to bottom; a line with Chinese is drawn in pieces, one
    per font, on one baseline."""
    lines: dict[float, str] = {}
    for _x, y, text, _font, _size in drawn:
        lines[round(y, 2)] = lines.get(round(y, 2), "") + text
    return [lines[y] for y in sorted(lines, reverse=True)]


def assert_text_is_on_the_label(roll: RollLayout, drawn: list[tuple]) -> None:
    """No text runs past the label's padding, and no line prints over another."""
    w_mm, h_mm = label_size_mm(roll)
    pad = 1.2 * mm
    boxes = []
    for x, y, text, font, size in drawn:
        right = x + _text_width(text, font, size)
        # From the baseline to the top of a capital letter.
        box = (x, y, right, y + 0.72 * size)
        assert x >= pad - 0.01 and right <= w_mm * mm - pad + 0.01, (roll, text)
        assert y >= pad - 0.01 and box[3] <= h_mm * mm - pad + 0.01, (roll, text)
        boxes.append((box, text))
    for i, (a, a_text) in enumerate(boxes):
        for b, b_text in boxes[i + 1 :]:
            if round(a[1], 2) == round(b[1], 2):
                continue  # pieces of one line
            apart = a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
            assert apart, (roll, a_text, b_text)


def assert_edges_are_blank(roll: RollLayout, dots: Image.Image) -> None:
    """Nothing is printed within half a millimetre of the tape's edges or the
    cut, where it could be cut off."""
    edge = 6  # dots at 300 dpi
    w, h = dots.size
    inked = dots.getbbox()
    assert inked is not None, roll
    assert inked[0] >= edge and inked[1] >= edge and inked[2] <= w - edge and inked[3] <= h - edge, (roll, inked)


# ── Chinese, Japanese and Korean (#3560) ──


@pytest.mark.parametrize("roll", ROLLS)
def test_cjk_text_reaches_the_printer_as_glyphs(roll):
    """A character the label font lacks printed as an empty box. Boxes are all
    alike, so two names of the same characters in another order printed the
    same dots; drawn as glyphs they differ."""
    name, reversed_name = "玉白ホワ흰색", "색흰ワホ白玉"
    pdf = render_labels(roll, [spool(name)], monochrome=True)
    assert {b"DroidSansFallback", b"NotoSansKR-Regular"} <= base_fonts(pdf)

    dots = sent_dots(roll, spool(name))
    assert dots.width == TAPES[roll.tape].printable_dots
    assert not same(dots, sent_dots(roll, spool(reversed_name)))
    assert not same(dots, sent_dots(roll, spool("")))  # the name is on the label at all
    assert_edges_are_blank(roll, dots)

    # The check above does tell boxes from glyphs: without the fallback fonts
    # the two names print the same.
    with patch.object(label_renderer, "_FALLBACK_FONTS", ()):
        assert same(sent_dots(roll, spool(name)), sent_dots(roll, spool(reversed_name)))


@pytest.mark.parametrize("roll", ROLLS)
def test_cjk_brand_and_subtype_reach_the_printer_as_glyphs(roll):
    """The lines every roll label has, the 12 mm one included."""
    label = spool(brand="拓竹白玉", subtype="흰색ホワ")
    other = spool(brand="玉白竹拓", subtype="ワホ색흰")
    assert not same(sent_dots(roll, label), sent_dots(roll, other))
    with patch.object(label_renderer, "_FALLBACK_FONTS", ()):
        assert same(sent_dots(roll, label), sent_dots(roll, other))


# ── Names over several lines (#3621) ──


@pytest.mark.parametrize(("roll", "name_lines"), [(RollLayout("62", 40), 3), (RollLayout("62", 90), 2)])
def test_long_name_continues_on_the_next_line_of_a_wide_roll(roll, name_lines):
    drawn = drawn_text(roll, spool(LONG_NAME))
    lines = text_lines(drawn)
    assert lines[:3] == ["Brand", "PLA", "#FF0000"] and lines[-1] == "#128"
    assert len(lines[3:-1]) == name_lines
    assert " ".join(lines[3:-1]) == LONG_NAME  # nothing is cut
    assert_text_is_on_the_label(roll, drawn)
    assert_edges_are_blank(roll, sent_dots(roll, spool(LONG_NAME)))


def test_long_name_continues_on_29_mm_tape():
    """The narrowest tape with the full layout: the name takes the lines the
    label has left and is cut on the last."""
    roll = RollLayout("29", 60)
    drawn = drawn_text(roll, spool(LONG_NAME))
    name_lines = text_lines(drawn)[3:-1]
    assert len(name_lines) == 2
    assert name_lines[-1].endswith("…")
    assert LONG_NAME.startswith(" ".join(name_lines).rstrip("…"))
    assert_text_is_on_the_label(roll, drawn)
    assert_edges_are_blank(roll, sent_dots(roll, spool(LONG_NAME)))


def test_long_chinese_name_breaks_between_characters_on_a_roll():
    roll = RollLayout("62", 40)
    drawn = drawn_text(roll, spool(LONG_CJK_NAME))
    name_lines = text_lines(drawn)[3:-1]
    assert len(name_lines) > 1
    assert "".join(name_lines) == LONG_CJK_NAME
    assert_text_is_on_the_label(roll, drawn)


def test_12_mm_tape_prints_the_name_on_one_line_beside_the_spool_id():
    """12 mm tape prints 9 mm: three small lines, with the spool ID beside
    them and not over them. The name has the third, cut where it ends."""
    roll = RollLayout("12", 60)
    drawn = drawn_text(roll, spool(LONG_NAME))
    by_text = {text: (x, y) for x, y, text, _font, _size in drawn}
    lines = [text for _x, _y, text, _font, _size in drawn]
    assert lines[0] == "#128"
    assert lines[1:3] == ["Brand", "PLA"]
    assert lines[3].startswith("Matte Charcoal Black") and lines[3].endswith("…")
    # The ID is right of every line, not under them.
    assert by_text["#128"][0] > max(
        x + _text_width(text, font, size) for x, _y, text, font, size in drawn if text != "#128"
    )
    assert_text_is_on_the_label(roll, drawn)
    assert_edges_are_blank(roll, sent_dots(roll, spool(LONG_NAME)))


def test_12_mm_tape_keeps_the_hex_code_when_the_name_repeats_the_brand():
    roll = RollLayout("12", 40)
    lines = [text for _x, _y, text, _font, _size in drawn_text(roll, spool("Brand"))]
    assert lines == ["#128", "Brand", "PLA", "#FF0000"]


def test_a_taller_tight_label_keeps_the_spool_id_under_the_lines():
    """Only a label too low for it moves the ID: at 15 mm it stays below."""
    w, h = 60 * mm, 15 * mm
    drawn: list[tuple[float, float, str]] = []
    real = rl_canvas.Canvas.drawString

    def record(self, x, y, text, *args, **kwargs):
        drawn.append((x, y, text))
        return real(self, x, y, text, *args, **kwargs)

    c = rl_canvas.Canvas(io.BytesIO(), pagesize=(w, h))
    with patch.object(rl_canvas.Canvas, "drawString", record):
        _draw_label(c, 0, 0, w, h, spool(LONG_NAME), True, DEFAULT_LABEL_FIELDS)
    assert [text for _x, _y, text in drawn] == ["Brand", "PLA", "#FF0000", "#128"]
    assert len({x for x, _y, _text in drawn}) == 1  # one column


@pytest.mark.parametrize("tape", list(TAPES))
@pytest.mark.parametrize("length", [20, 40, 100, 200])
@pytest.mark.parametrize("name", [LONG_NAME, LONG_CJK_NAME])
def test_no_roll_label_prints_text_off_the_tape_or_over_other_text(tape, length, name):
    roll = RollLayout(tape, length)
    label = spool(name, brand="A Rather Long Brand Name Ltd", subtype="Carbon Fibre", storage_location="Shelf B2")
    assert_text_is_on_the_label(roll, drawn_text(roll, label))
    dots = sent_dots(roll, label)
    assert dots.width == TAPES[tape].printable_dots
    assert_edges_are_blank(roll, dots)


# ── Project labels (#3655) ──


@pytest.mark.parametrize("roll", ROLLS)
def test_project_label_reaches_the_printer(roll):
    """A project label goes through the same print path, with its own drawing:
    the right width for the tape, the name on it, Chinese as glyphs."""
    name, reversed_name = "香蕉蛞蝓 흰색", "蝓蛞蕉香 색흰"
    pdf = render_labels(roll, [project(name)], monochrome=True, fields=PROJECT_FIELDS, draw=draw_project_label)
    assert {b"DroidSansFallback", b"NotoSansKR-Regular"} <= base_fonts(pdf)

    dots = sent_dots(roll, project(name), PROJECT_FIELDS, draw_project_label)
    assert dots.width == TAPES[roll.tape].printable_dots
    assert not same(dots, sent_dots(roll, project(reversed_name), PROJECT_FIELDS, draw_project_label))
    assert not same(dots, sent_dots(roll, spool(name)))  # not a spool label
    assert_edges_are_blank(roll, dots)
    with patch.object(label_renderer, "_FALLBACK_FONTS", ()):
        assert same(
            sent_dots(roll, project(name), PROJECT_FIELDS, draw_project_label),
            sent_dots(roll, project(reversed_name), PROJECT_FIELDS, draw_project_label),
        )


@pytest.mark.parametrize("roll", [RollLayout("29", 60), RollLayout("62", 40)])
def test_long_project_name_continues_on_the_next_line_of_a_roll(roll):
    name = "Banana Slugs for the Spring Fair, Second Batch of the Yellow Ones"
    drawn = drawn_text(roll, project(name), frozenset({"name", "qr", "quantity"}), draw_project_label)
    lines = text_lines(drawn)
    assert lines[-1] == "× 12"
    assert len(lines[:-1]) > 1
    assert name.startswith(" ".join(lines[:-1]).rstrip("…"))
    assert_text_is_on_the_label(roll, drawn)
