"""RollLayout in the label renderer (#3612)."""

from __future__ import annotations

import pytest
from reportlab.lib.units import mm

from backend.app.services.label_printers.brother_ql_media import TAPES
from backend.app.services.label_renderer import (
    ROLL_MAX_LENGTH_MM,
    ROLL_MIN_LENGTH_MM,
    LabelData,
    RollLayout,
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


def test_size_is_length_by_printable_width():
    w, h = label_size_mm(RollLayout("50", 40))
    assert w == 40
    assert h == pytest.approx(554 / 300 * 25.4)


@pytest.mark.parametrize("tape", list(TAPES))
def test_every_tape_renders_at_exact_dots(tape):
    layout = RollLayout(tape, 40)  # type: ignore[arg-type]
    images = list(iter_pdf_images(render_labels(layout, [data(1), data(2)], monochrome=True), 300))
    assert len(images) == 2
    # Landscape: width = length, height = printable dots (rendering may be a pixel off).
    assert abs(images[0].height - TAPES[tape].printable_dots) <= 1
    assert abs(images[0].width - round(40 / 25.4 * 300)) <= 1


def test_no_sheet_capacity():
    assert get_sheet_capacity(RollLayout("62", 50)) is None


def test_preview_page_size():
    images = list(iter_pdf_images(render_label_preview_pdf(RollLayout("62", 50), data()), 72))
    assert images[0].width == pytest.approx(50 * mm, abs=1)


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
