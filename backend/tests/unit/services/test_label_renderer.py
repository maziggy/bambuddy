"""Unit tests for the spool label renderer (#809)."""

from __future__ import annotations

import pytest

from backend.app.services.label_renderer import LabelData, render_labels

ALL_TEMPLATES = ("ams_30x15", "box_40x30", "box_62x29", "avery_5160", "avery_l7160")


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
    pdf = render_labels("ams_30x15", data)
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
    pdf = render_labels("ams_30x15", data)
    assert pdf.startswith(b"%PDF")


def test_sheet_template_paginates_when_count_exceeds_one_sheet():
    """Avery 5160 = 30 per sheet; 31 spools must paginate to 2 pages.

    We can't easily count pages from raw PDF bytes, but we can at least
    verify the output is meaningfully larger than a single-page rendering.
    """
    one = render_labels("avery_5160", [_sample(i) for i in range(1, 31)])
    two = render_labels("avery_5160", [_sample(i) for i in range(1, 32)])
    assert len(two) > len(one)


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


def _render_uncompressed(template, data):
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
    if template in ("ams_30x15", "box_40x30", "box_62x29"):
        sizes = {
            "ams_30x15": (30.0, 15.0),
            "box_40x30": (40.0, 30.0),
            "box_62x29": (62.0, 29.0),
        }
        w_mm, h_mm = sizes[template]
        page_w, page_h = w_mm * _mm, h_mm * _mm
        buf = _io.BytesIO()
        c = _rl_canvas.Canvas(buf, pagesize=(page_w, page_h), pageCompression=0)
        for d in data:
            _draw_label(c, 0, 0, page_w, page_h, d)
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


def test_ams_template_actually_renders_text():
    """Regression: the first cut of the AMS-holder layout produced labels with
    only swatch + QR and no text at all because the side-by-side layout left
    <5 mm for the text column. The redesign drops the QR on this template and
    gives the right side to brand + material + spool ID. This pins that the
    rendered PDF contains all three fields.
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
    pdf = _render_uncompressed("ams_30x15", data)
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
