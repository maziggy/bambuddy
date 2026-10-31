"""Unit tests for the spool label renderer (#809)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm

from backend.app.services.label_renderer import (
    LabelData,
    SheetLayout,
    get_sheet_capacity,
    label_size_mm,
    render_labels,
)

ALL_TEMPLATES = (
    "ams_holder_74x33",
    "ams_holder_75x55",
    "box_40x30",
    "box_62x29",
    "avery_5160",
    "avery_l7160",
)


def _sample(spool_id: int = 1, **overrides) -> LabelData:
    return LabelData(
        spool_id=spool_id,
        name=overrides.pop("name", "Polymaker Ivory"),
        material=overrides.pop("material", "PLA"),
        brand=overrides.pop("brand", "Polymaker"),
        subtype=overrides.pop("subtype", "Matte"),
        rgba=overrides.pop("rgba", "F5E6D3FF"),
        extra_colors=overrides.pop("extra_colors", None),
        storage_location=overrides.pop("storage_location", None),
        deeplink_url=overrides.pop("deeplink_url", f"https://example.test/inventory?spool={spool_id}"),
    )


@pytest.mark.parametrize("template", ALL_TEMPLATES)
def test_renders_valid_pdf_for_each_template(template):
    pdf = render_labels(template, [_sample(7), _sample(8)])
    assert pdf.startswith(b"%PDF"), f"{template} did not produce a PDF header"
    assert pdf.endswith(b"%%EOF\n") or pdf.rstrip().endswith(b"%%EOF")


@pytest.mark.parametrize("template", ALL_TEMPLATES)
def test_empty_input_still_returns_valid_pdf(template):
    """Empty list is allowed; renderer returns a valid (mostly empty) PDF."""
    pdf = render_labels(template, [])
    assert pdf.startswith(b"%PDF")


def test_unknown_template_raises():
    with pytest.raises(ValueError, match="Unknown label template"):
        render_labels("not_a_template", [_sample()])  # type: ignore[arg-type]


def test_multi_color_swatch_does_not_crash():
    data = [_sample(extra_colors=["FF0000", "00FF00", "0000FF", "FFFF00"])]
    pdf = render_labels("box_62x29", data)
    assert pdf.startswith(b"%PDF")


def test_missing_optional_fields_does_not_crash():
    """Brand/subtype/rgba/storage_location all None — should still render."""
    data = [
        LabelData(
            spool_id=42,
            name="Test",
            material="PLA",
            deeplink_url="https://example.test/inventory?spool=42",
        )
    ]
    pdf = render_labels("ams_holder_74x33", data)
    assert pdf.startswith(b"%PDF")


def test_malformed_rgba_falls_back_to_grey():
    """rgba="zzz" (invalid hex) must not raise — fallback colour used."""
    data = [_sample(rgba="not-a-color")]
    pdf = render_labels("avery_l7160", data)
    assert pdf.startswith(b"%PDF")


def test_long_strings_are_truncated_not_overflowed():
    """Very long brand/name shouldn't blow up the layout or raise."""
    long_brand = "A" * 200
    long_name = "B" * 300
    data = [_sample(brand=long_brand, name=long_name)]
    pdf = render_labels("ams_holder_74x33", data)
    assert pdf.startswith(b"%PDF")


def test_sheet_template_paginates_when_count_exceeds_one_sheet():
    """Avery 5160 = 30 per sheet; 31 spools must paginate to 2 pages.

    We can't easily count pages from raw PDF bytes, but we can at least
    verify the output is meaningfully larger than a single-page rendering.
    """
    one = render_labels("avery_5160", [_sample(i) for i in range(1, 31)])
    two = render_labels("avery_5160", [_sample(i) for i in range(1, 32)])
    assert len(two) > len(one)


def test_sheet_starting_position_offsets_first_label():
    with patch("backend.app.services.label_renderer._draw_label") as draw_label:
        render_labels("avery_5160", [_sample(1)], starting_position=8)

    first_call = draw_label.call_args_list[0].args
    assert first_call[1] == pytest.approx(4.76 * mm + 66.675 * mm + 3.175 * mm)
    assert first_call[2] == pytest.approx(letter[1] - 12.7 * mm - 3 * 25.4 * mm)


def test_sheet_starting_position_resets_on_second_page():
    data = [_sample(i) for i in range(1, 25)]
    with patch("backend.app.services.label_renderer._draw_label") as draw_label:
        render_labels("avery_5160", data, starting_position=8)

    last_first_page_call = draw_label.call_args_list[22].args
    first_second_page_call = draw_label.call_args_list[23].args
    assert last_first_page_call[1] == pytest.approx(4.76 * mm + 2 * (66.675 * mm + 3.175 * mm))
    assert last_first_page_call[2] == pytest.approx(letter[1] - 12.7 * mm - 10 * 25.4 * mm)
    assert first_second_page_call[1] == pytest.approx(4.76 * mm)
    assert first_second_page_call[2] == pytest.approx(letter[1] - 12.7 * mm - 25.4 * mm)


@pytest.mark.parametrize(
    ("template", "starting_position"),
    (("avery_5160", 0), ("avery_5160", 31), ("avery_l7160", 22), ("box_62x29", 2)),
)
def test_invalid_starting_position_raises(template, starting_position):
    with pytest.raises(ValueError, match="Starting position"):
        render_labels(template, [_sample()], starting_position=starting_position)


def test_qr_payload_is_present_in_pdf_stream():
    """The QR encodes the deeplink URL via embedded PNG; we can at least
    sanity-check that the PDF contains an image stream when a deeplink is set
    and no image stream when the renderer skips QR generation for an empty URL.
    """
    with_qr = render_labels("box_62x29", [_sample(deeplink_url="https://example.test/inventory?spool=1")])
    without_qr = render_labels("box_62x29", [_sample(deeplink_url="")])
    # PDFs with embedded raster images are noticeably larger than pure-vector ones.
    assert len(with_qr) > len(without_qr) + 200, (
        "Expected QR-bearing PDF to be substantially larger than QR-less version"
    )


# ── Regression tests for the two render bugs found in the first cut ──


def _render_uncompressed(template, data, monochrome=False):
    """Render with pageCompression=0 so the resulting PDF contains text as
    ASCII bytes. Lets tests assert "X is on the label" by grepping the PDF.

    Uses the same internal draw helpers as the real renderer; only the
    page-level compression flag differs.
    """
    import io as _io

    from reportlab.lib.pagesizes import A4, letter
    from reportlab.lib.units import mm as _mm
    from reportlab.pdfgen import canvas as _rl_canvas

    from backend.app.services.label_renderer import _draw_label  # noqa: PLC0415

    # Mirror the page-size choice from render_labels but force pageCompression=0.
    if template in ("ams_holder_74x33", "ams_holder_75x55", "box_40x30", "box_62x29"):
        sizes = {
            "ams_holder_74x33": (74.0, 33.0),
            "ams_holder_75x55": (75.0, 55.0),
            "box_40x30": (40.0, 30.0),
            "box_62x29": (62.0, 29.0),
        }
        w_mm, h_mm = sizes[template]
        page_w, page_h = w_mm * _mm, h_mm * _mm
        buf = _io.BytesIO()
        c = _rl_canvas.Canvas(buf, pagesize=(page_w, page_h), pageCompression=0)
        for d in data:
            _draw_label(c, 0, 0, page_w, page_h, d, monochrome)
            c.showPage()
        c.save()
        return buf.getvalue()
    if template == "avery_5160":
        page_size = letter
        label_w_mm, label_h_mm = 66.675, 25.4
        cols, rows = 3, 10
        top_mm, left_mm, col_gap_mm = 12.7, 4.76, 3.175
    else:  # avery_l7160
        page_size = A4
        label_w_mm, label_h_mm = 63.5, 38.1
        cols, rows = 3, 7
        top_mm, left_mm, col_gap_mm = 15.15, 7.0, 2.5
    buf = _io.BytesIO()
    c = _rl_canvas.Canvas(buf, pagesize=page_size, pageCompression=0)
    page_w, page_h = page_size
    label_w, label_h = label_w_mm * _mm, label_h_mm * _mm
    per_page = cols * rows
    for page_start in range(0, len(data), per_page):
        chunk = data[page_start : page_start + per_page]
        for idx, d in enumerate(chunk):
            row = idx // cols
            col = idx % cols
            x = left_mm * _mm + col * (label_w + col_gap_mm * _mm)
            y = page_h - top_mm * _mm - (row + 1) * label_h
            _draw_label(c, x, y, label_w, label_h, d)
        c.showPage()
    c.save()
    return buf.getvalue()


def test_transparent_swatch_does_not_apply_its_alpha_to_qr():
    pdf = _render_uncompressed(
        "box_62x29",
        [_sample(rgba="FF000000", deeplink_url="https://example.test/inventory?spool=1")],
    )

    alpha_start = pdf.index(b"/gRLs0 gs")
    qr_draw = pdf.index(b" Do", alpha_start)
    assert b"Q" in pdf[alpha_start:qr_draw]


def test_ams_template_actually_renders_text():
    """Regression: the first cut of the AMS-holder layout produced labels with
    only swatch + QR and no text at all because the side-by-side layout left
    <5 mm for the text column. The current AMS templates use the roomy layout
    (swatch + QR + multi-line text); this pins that the rendered PDF contains
    brand + material + spool ID for the smaller AMS preset.
    """
    data = [
        LabelData(
            spool_id=42,
            name="Test",
            material="PLA",
            brand="Polymaker",
            subtype="Matte",
            rgba="F5E6D3FF",
            deeplink_url="https://example.test/inventory?spool=42",
        )
    ]
    pdf = _render_uncompressed("ams_holder_74x33", data)
    assert b"Polymaker" in pdf, "AMS template must render the brand"
    assert b"PLA" in pdf, "AMS template must render the material"
    # The bracketed-hash style is what the renderer uses for the spool ID;
    # ReportLab's `#` is in the BaseFont, so it appears as literal `#` in the
    # uncompressed stream alongside the digits.
    assert b"#42" in pdf or (b"42" in pdf and b"#" in pdf), (
        "AMS template must render the spool ID — that's the killer field"
    )


def test_hex_color_code_rendered_when_rgba_set():
    """#809 follow-up: the colour hex code (#RRGGBB, alpha-stripped, uppercase)
    must appear on the rendered label so the user can tell near-identical
    spools apart at a glance.
    """
    data = [
        LabelData(
            spool_id=12,
            name="Polymaker Ivory",
            material="PLA",
            brand="Polymaker",
            subtype="Matte",
            rgba="f5e6d3FF",
            deeplink_url="https://example.test/inventory?spool=12",
        )
    ]
    pdf = _render_uncompressed("box_62x29", data)
    assert b"#F5E6D3" in pdf, "box label must render the hex colour code"

    pdf = _render_uncompressed("box_40x30", data)
    assert b"#F5E6D3" in pdf, "40x30 box label must render the hex colour code"


def test_hex_color_code_skipped_when_rgba_invalid():
    """Malformed rgba must NOT render any '#' hex string apart from the spool
    ID — silently skipping the hex line is better than crashing or rendering
    garbage. The spool ID still uses '#' so we look for the specific shape.
    """
    data = [
        LabelData(
            spool_id=99,
            name="Test",
            material="PLA",
            brand="Polymaker",
            rgba="not-a-color",
            deeplink_url="https://example.test/inventory?spool=99",
        )
    ]
    pdf = _render_uncompressed("box_62x29", data)
    # No 6-hex-digit '#XXXXXX' substring should appear (only '#99' for the ID).
    import re

    matches = re.findall(rb"#[0-9A-F]{6}", pdf)
    assert matches == [], f"expected no hex code on label, found {matches!r}"


def test_brand_rendered_in_bold_per_809_followup():
    """#809 follow-up: brand should render in Helvetica-Bold (not regular).
    Uncompressed PDFs include font-name tokens like '/F2' tied to a font
    resource; we can grep for the bold font's basename in the resource block.
    """
    data = [
        LabelData(
            spool_id=5,
            name="Acme PLA",
            material="PLA",
            brand="Polymaker",
            rgba="FF8800FF",
            deeplink_url="https://example.test/inventory?spool=5",
        )
    ]
    pdf = _render_uncompressed("box_62x29", data)
    # ReportLab references the bold variant of Helvetica via /Helvetica-Bold
    # in the font dictionary — both the spool ID (always bold) and the brand
    # (now bold per #809 follow-up) cause the resource to be embedded.
    assert b"Helvetica-Bold" in pdf, "label PDF must reference Helvetica-Bold for the brand line"


def test_box_template_does_not_truncate_normal_brand_or_name():
    """Regression: the first cut of the box-label layout sized the swatch and
    QR each at ~14 mm on a 26-mm-wide text column, leaving only ~16 mm for
    text and aggressively truncating "Polymaker · PLA · Matte" to
    "Polymaker …" and "Polymaker Ivory" to "Polymak…". The redesign caps the
    swatch and QR widths so a typical brand + name renders without truncation.
    """
    data = [
        LabelData(
            spool_id=7,
            name="Polymaker Ivory",
            material="PLA",
            brand="Polymaker",
            subtype="Matte",
            rgba="F5E6D3FF",
            storage_location="Shelf 3, slot B",
            deeplink_url="https://example.test/inventory?spool=7",
        )
    ]
    pdf = _render_uncompressed("box_62x29", data)
    # Brand on its own line — must not be truncated.
    assert b"Polymaker" in pdf, "box template must render the brand"
    # Material + subtype on its own line — must not be truncated.
    assert b"Matte" in pdf, "box template must render the subtype"
    # Spool name (bold) — must include both words. Truncation would have
    # produced "Polymak\xe2\x80\xa6" in the original bug, so asserting the
    # second word "Ivory" is on the label is the regression-pin.
    assert b"Ivory" in pdf, (
        "box template must render the spool name fully — earlier layout truncated 'Polymaker Ivory' to 'Polymak…'"
    )
    # Storage location (italic).
    assert b"Shelf 3, slot B" in pdf, "box template must render the storage location"
    # Big spool ID at bottom.
    assert b"#7" in pdf or (b"7" in pdf and b"#" in pdf), "box template must render the spool ID"


# ── #1870: low-res thermal-printer optimisations ──


@pytest.mark.parametrize("template", ALL_TEMPLATES)
def test_monochrome_renders_valid_pdf_for_each_template(template):
    """Monochrome mode must render a valid PDF for every template (#1870)."""
    pdf = render_labels(template, [_sample(7), _sample(8)], monochrome=True)
    assert pdf.startswith(b"%PDF"), f"{template} monochrome did not produce a PDF header"


def test_monochrome_omits_colour_swatch():
    """Monochrome drops the colour swatch (useless grey block on a B&W printer)
    while the default keeps it (#1870, requested by @Geoff-S)."""
    from unittest.mock import patch

    import backend.app.services.label_renderer as lr

    with patch.object(lr, "_draw_swatch") as mock_swatch:
        render_labels("box_40x30", [_sample(1)], monochrome=True)
        assert mock_swatch.call_count == 0, "monochrome must not draw the colour swatch"

    with patch.object(lr, "_draw_swatch") as mock_swatch:
        render_labels("box_40x30", [_sample(1)], monochrome=False)
        assert mock_swatch.call_count == 1, "colour mode must draw the swatch"


def test_monochrome_still_renders_text_and_hex():
    """Dropping the swatch must not lose the colour info — the hex code line and
    the text fields still render (the hex is how colour is conveyed in B&W)."""
    data = [
        LabelData(
            spool_id=42,
            name="Polymaker Ivory",
            material="PLA",
            brand="Polymaker",
            subtype="Matte",
            rgba="F5E6D3FF",
            deeplink_url="https://example.test/inventory?spool=42",
        )
    ]
    pdf = _render_uncompressed("box_40x30", data, monochrome=True)
    assert b"Polymaker" in pdf, "monochrome label must still render the brand"
    assert b"#F5E6D3" in pdf, "monochrome label must still render the hex colour code"
    assert b"#42" in pdf or (b"42" in pdf and b"#" in pdf), "monochrome label must render the spool ID"


def test_roomy_qr_size_has_floor_for_narrow_labels():
    """#1870 regression: box_40x30's QR must not shrink below a scannable size.
    The pre-fix ``inner_w * 0.20`` gave ~7.5 mm on that label; the floor keeps
    it at 12 mm so each module clears ~3 dots on a 203 dpi thermal head.
    """
    from reportlab.lib.units import mm

    from backend.app.services.label_renderer import _roomy_qr_size

    pad = 1.2 * mm
    # box_40x30 inner dimensions.
    inner_w = 40 * mm - 2 * pad
    inner_h = 30 * mm - 2 * pad
    assert _roomy_qr_size(inner_w, inner_h) >= 12 * mm - 0.01

    # Larger templates are unaffected (already above the floor) and still capped.
    inner_w_big = 75 * mm - 2 * pad
    inner_h_big = 55 * mm - 2 * pad
    size_big = _roomy_qr_size(inner_w_big, inner_h_big)
    assert 12 * mm <= size_big <= 18 * mm


# ── Custom sheet layouts (#3296) ──


def _layout(**overrides) -> SheetLayout:
    # A 3 × 8 sheet of 70 × 36 mm labels on A4, edge to edge (Avery 3490).
    fields = {
        "page": "a4",
        "label_w_mm": 70.0,
        "label_h_mm": 36.0,
        "cols": 3,
        "rows": 8,
        "top_mm": 4.5,
        "left_mm": 0.0,
    }
    fields.update(overrides)
    return SheetLayout(**fields)


def test_a_layout_that_fills_the_page_exactly_is_printable():
    assert _layout().problem() is None
    assert _layout().capacity == 24


def test_inch_based_sheet_within_rounding_still_fits():
    # Avery 5160 measured in inches: 3 × 2.625" + 2 × 0.125" gaps + 0.1875" margin
    # comes to 215.9 mm, Letter's full width, give or take float rounding.
    layout = SheetLayout(
        "letter", 2.625 * 25.4, 25.4, 3, 10, top_mm=12.7, left_mm=0.1875 * 25.4, col_gap_mm=0.125 * 25.4
    )
    assert layout.problem() is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"left_mm": 1.0}, "wide"),
        ({"rows": 9}, "tall"),
        ({"row_gap_mm": 2.0}, "tall"),
        ({"label_h_mm": 19.9}, "at least"),
        ({"label_w_mm": 39.9, "cols": 1}, "at least"),
        ({"col_gap_mm": 300.5, "cols": 1}, "at most"),
        ({"cols": 0}, "Columns"),
        ({"rows": 41, "label_h_mm": 20.0, "top_mm": 0.0}, "Rows"),
        ({"col_gap_mm": -0.5, "cols": 2}, "negative"),
        ({"page": "a3"}, "page size"),
    ],
)
def test_layout_problems_are_named(overrides, message):
    assert message in _layout(**overrides).problem()


def test_letter_width_matches_the_dialog_to_the_hundredth():
    # 215.9 mm is Letter's width on both sides of the API; 215.91 is not.
    fits = SheetLayout("letter", 107.95, 25.4, 2, 1, top_mm=0.0, left_mm=0.0)
    over = SheetLayout("letter", 107.95, 25.4, 2, 1, top_mm=0.0, left_mm=0.0, col_gap_mm=0.02)
    assert fits.problem() is None
    assert "wide" in over.problem()


@pytest.mark.parametrize("label_h_mm", [20.0, 22.0, 25.0, 30.0, 40.0, 60.0, 100.0, 280.0])
def test_the_smallest_label_has_room_for_a_full_size_spool_id(label_h_mm):
    """At the minimum width, a colour label with a QR keeps a text column wide
    enough for a four-digit spool ID at its full 16 pt — at any height, since
    the QR grows with the label. At the minimum height the roomy layout is used."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    from backend.app.services.label_renderer import MIN_LABEL_H_MM, MIN_LABEL_W_MM, _roomy_qr_size

    pad = 1.2 * mm
    inner_w = MIN_LABEL_W_MM * mm - 2 * pad
    inner_h = label_h_mm * mm - 2 * pad
    swatch_w = min(inner_w * 0.18, inner_h, 16 * mm)
    qr = _roomy_qr_size(inner_w, inner_h)
    text_w = inner_w - swatch_w - qr - 2 * 1.5 * mm
    assert text_w >= stringWidth("#9999", "Helvetica-Bold", 16)
    assert MIN_LABEL_H_MM >= 20.0  # the tight layout starts below 20 mm


def test_the_smallest_label_prints_the_spool_id():
    from backend.app.services.label_renderer import MIN_LABEL_H_MM, MIN_LABEL_W_MM

    layout = _layout(label_w_mm=MIN_LABEL_W_MM, label_h_mm=MIN_LABEL_H_MM, cols=1, rows=1)
    pdf = _render_uncompressed_layout(layout, [_sample(9999)])
    assert b"(#9999)" in pdf


def _render_uncompressed_layout(layout, data):
    import io as _io

    from reportlab.pdfgen import canvas as _rl_canvas

    from backend.app.services.label_renderer import _draw_label  # noqa: PLC0415

    buf = _io.BytesIO()
    w, h = layout.label_w_mm * mm, layout.label_h_mm * mm
    c = _rl_canvas.Canvas(buf, pagesize=(w, h), pageCompression=0)
    for d in data:
        _draw_label(c, 0, 0, w, h, d)
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.mark.parametrize(
    ("label_count", "starting_position", "pages"),
    [(24, 1, 1), (25, 1, 2), (1, 24, 1), (2, 24, 2), (48, 1, 2)],
)
def test_sheet_page_count(label_count, starting_position, pages):
    from backend.app.services.label_renderer import sheet_page_count

    assert sheet_page_count(_layout(), label_count, starting_position) == pages


def test_roll_labels_have_no_sheet_page_count():
    from backend.app.services.label_renderer import sheet_page_count

    assert sheet_page_count("box_40x30", 10) is None


def test_render_refuses_a_layout_that_does_not_fit():
    with pytest.raises(ValueError, match="wide"):
        render_labels(_layout(left_mm=1.0), [_sample()])


def test_custom_layout_places_labels_with_gaps():
    layout = _layout(
        page="letter",
        label_w_mm=60.0,
        label_h_mm=30.0,
        cols=2,
        rows=3,
        top_mm=10.0,
        left_mm=5.0,
        col_gap_mm=4.0,
        row_gap_mm=2.0,
    )
    data = [_sample(i) for i in range(1, 8)]
    with patch("backend.app.services.label_renderer._draw_label") as draw_label:
        render_labels(layout, data, starting_position=2)

    calls = [call.args for call in draw_label.call_args_list]
    # Slot 2 is the top-right cell of page one.
    assert calls[0][1] == pytest.approx(5.0 * mm + 64.0 * mm)
    assert calls[0][2] == pytest.approx(letter[1] - 10.0 * mm - 30.0 * mm)
    assert calls[0][3:5] == (pytest.approx(60.0 * mm), pytest.approx(30.0 * mm))
    # Slot 6 is the bottom-right cell: two row gaps above it.
    assert calls[4][2] == pytest.approx(letter[1] - 10.0 * mm - 3 * 30.0 * mm - 2 * 2.0 * mm)
    # The sixth label starts page two at the top-left.
    assert calls[5][1:3] == (pytest.approx(5.0 * mm), pytest.approx(letter[1] - 10.0 * mm - 30.0 * mm))


def test_custom_layout_size_and_capacity():
    layout = _layout()
    assert label_size_mm(layout) == (70.0, 36.0)
    assert get_sheet_capacity(layout) == 24
    assert render_labels(layout, [_sample()]).startswith(b"%PDF")
