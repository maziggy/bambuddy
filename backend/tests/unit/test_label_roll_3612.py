"""RollLayout in the label renderer (#3612)."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

from backend.app.services.label_printers.brother_ql_media import TAPES
from backend.app.services.label_renderer import (
    DEFAULT_LABEL_FIELDS,
    ROLL_MAX_LENGTH_MM,
    ROLL_MIN_LENGTH_MM,
    LabelData,
    RollLayout,
    _draw_label,
    get_sheet_capacity,
    iter_pdf_images,
    label_size_mm,
    render_label_preview_pdf,
    render_labels,
)


def data(i: int = 1) -> LabelData:
    return LabelData(
        spool_id=i,
        name="Red",
        material="PLA",
        brand="Brand",
        subtype=None,
        rgba="FF0000FF",
        extra_colors=None,
        storage_location=None,
        deeplink_url=f"http://x/inventory?spool={i}",
    )


def test_long_label_reads_along_the_tape():
    layout = RollLayout("50", 60)
    w, h = label_size_mm(layout)
    assert not layout.across_tape
    assert w == 60
    assert h == pytest.approx(554 / 300 * 25.4)


def test_short_label_reads_across_the_tape():
    """A 20 mm cut of 50 mm tape is a landscape 47 x 20 mm label whose page
    already has the tape width as its width, so it is printed unturned."""
    layout = RollLayout("50", 20)
    w, h = label_size_mm(layout)
    assert layout.across_tape
    assert w == pytest.approx(554 / 300 * 25.4)
    assert h == 20
    image = next(iter_pdf_images(render_labels(layout, [data()], monochrome=True), 300))
    assert abs(image.width - 554) <= 1
    assert abs(image.height - round(20 / 25.4 * 300)) <= 1


@pytest.mark.parametrize("tape", list(TAPES))
def test_every_tape_renders_at_exact_dots(tape):
    layout = RollLayout(tape, 80)  # type: ignore[arg-type]
    images = list(iter_pdf_images(render_labels(layout, [data(1), data(2)], monochrome=True), 300))
    assert len(images) == 2
    # Landscape: width = length, height = printable dots (rendering may be a pixel off).
    assert abs(images[0].height - TAPES[tape].printable_dots) <= 1
    assert abs(images[0].width - round(80 / 25.4 * 300)) <= 1


def test_no_sheet_capacity():
    assert get_sheet_capacity(RollLayout("62", 50)) is None


def test_preview_page_size():
    images = list(iter_pdf_images(render_label_preview_pdf(RollLayout("62", 50), data()), 72))
    assert images[0].height == pytest.approx(50 * mm, abs=1)


def test_limits():
    assert RollLayout("50", ROLL_MIN_LENGTH_MM).problem() is None
    assert RollLayout("50", ROLL_MAX_LENGTH_MM).problem() is None
    assert RollLayout("50", ROLL_MIN_LENGTH_MM - 1).problem()
    assert RollLayout("50", ROLL_MAX_LENGTH_MM + 1).problem()
    assert RollLayout("99", 40).problem()  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        render_labels(RollLayout("50", 5), [data()])


def test_starting_position_refused():
    with pytest.raises(ValueError):
        render_labels(RollLayout("50", 40), [data()], starting_position=2)


def _lines(template, label: LabelData) -> list[str]:
    """The text drawn on one black-and-white label with the default lines,
    one entry per line, top to bottom. A line with Chinese is drawn in
    pieces, one per font, on one baseline."""
    drawn: dict[float, str] = {}
    real = rl_canvas.Canvas.drawString

    def record(self, x, y, text, *args, **kwargs):
        drawn[round(y, 2)] = drawn.get(round(y, 2), "") + text
        return real(self, x, y, text, *args, **kwargs)

    w_mm, h_mm = label_size_mm(template)
    c = rl_canvas.Canvas(io.BytesIO(), pagesize=(w_mm * mm, h_mm * mm))
    with patch.object(rl_canvas.Canvas, "drawString", record):
        _draw_label(c, 0, 0, w_mm * mm, h_mm * mm, label, True, DEFAULT_LABEL_FIELDS)
    return [drawn[y] for y in sorted(drawn, reverse=True)]


LONG_NAME = "Matte Charcoal Black Limited Edition Extra"


def test_short_label_keeps_the_name_over_the_hex_code():
    """A 20 mm label has three lines above the spool ID; the colour name
    takes the third, not the hex code."""
    label = data()
    label.name = "Lava Red"
    assert _lines(RollLayout("50", 20), label) == ["Brand", "PLA", "Lava Red", "#1"]


def test_label_with_room_shows_hex_code_and_name():
    label = data()
    label.name = "Lava Red"
    assert _lines(RollLayout("50", 40), label) == ["Brand", "PLA", "#FF0000", "Lava Red", "#1"]


def test_short_label_without_a_name_keeps_the_hex_code():
    label = data()
    label.name = ""
    assert _lines(RollLayout("50", 20), label) == ["Brand", "PLA", "#FF0000", "#1"]


def test_short_label_cuts_a_long_name_on_its_one_line():
    """A long name continues on further lines only where the label has them
    (#3621). The 20 mm label has one line left, so the name is cut there; its
    beginning still tells more than the hex code."""
    label = data()
    label.name = LONG_NAME
    lines = _lines(RollLayout("50", 20), label)
    assert len(lines) == 4
    assert lines[2].startswith("Matte Charcoal") and lines[2].endswith("…")
    assert "#FF0000" not in lines


def test_hex_code_stays_when_the_name_has_its_first_line():
    """The hex code gives way only to a name that would not be drawn at all,
    not to give a long name more lines: with the brand unset the name has a
    line, and is cut on it like on any other label."""
    label = data()
    label.name = LONG_NAME
    label.brand = ""
    lines = _lines(RollLayout("50", 20), label)
    assert lines[:2] == ["PLA", "#FF0000"]
    assert lines[2].startswith("Matte Charcoal") and lines[2].endswith("…")


@pytest.mark.parametrize("layout", [RollLayout("62", 30), RollLayout("50", 40), RollLayout("29", 40)])
def test_long_name_continues_on_the_next_line_of_a_roll_label(layout):
    """Across the tape (the first two) and along it: with room below, the
    name continues as on every other label (#3621) and the hex code stays."""
    label = data()
    label.name = LONG_NAME
    lines = _lines(layout, label)
    assert lines[:3] == ["Brand", "PLA", "#FF0000"]
    assert lines[-1] == "#1"
    name_lines = lines[3:-1]
    assert len(name_lines) > 1
    # Nothing is lost but what no longer fits on the last line.
    assert LONG_NAME.startswith(" ".join(name_lines).rstrip("…"))


@pytest.mark.parametrize(
    "template", ["box_40x30", "box_62x29", "ams_holder_74x33", "ams_holder_75x55", "avery_5160", "avery_l7160"]
)
@pytest.mark.parametrize("name", ["Lava Red", LONG_NAME])
def test_built_in_templates_keep_the_hex_code(template, name):
    """Every built-in label has a line for the name, so none loses its hex code."""
    label = data()
    label.name = name
    lines = _lines(template, label)
    assert "#FF0000" in lines
    assert lines[lines.index("#FF0000") + 1].startswith(name.split()[0])
