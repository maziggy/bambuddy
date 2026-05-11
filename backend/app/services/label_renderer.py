"""PDF spool label rendering.

Five fixed templates:

- ``ams_30x15``  — 30×15 mm single label, fits the popular Makerworld AMS
  Filament Label Holder (model 752566). One label per page.
- ``box_40x30``  — 40×30 mm single label, common DK/Brother roll size and a
  good fit for filament-bag/storage-bin labels (#809 follow-up). Roomy
  layout — swatch, QR, full text column with hex code.
- ``box_62x29``  — 62×29 mm single label, sized for Brother PT/QL and Dymo
  generic small labels. One label per page.
- ``avery_5160`` — US Letter sheet, 25.4×66.7 mm × 30 per sheet.
- ``avery_l7160`` — A4 sheet, 38.1×63.5 mm × 21 per sheet.

The renderer is decoupled from the Spool model: callers build a ``LabelData``
list from whatever source (local DB, Spoolman, future) so the same code path
works in both modes.

Layout principle, taken from the issue's user need (`#809`): the **spool ID**
is the most-recognisable field at arm's length and dominates the layout. Other
fields (brand, material, name, storage location) fill remaining space; the QR
code provides the round-trip back to ``/inventory?spool=<id>``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Literal

import qrcode
from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

TemplateName = Literal["ams_30x15", "box_40x30", "box_62x29", "avery_5160", "avery_l7160"]


@dataclass
class LabelData:
    """Per-spool data needed to render a label.

    Decoupled from the SQLAlchemy model so the same renderer serves the local
    inventory and the Spoolman-backed inventory.
    """

    spool_id: int
    name: str
    material: str
    brand: str | None = None
    subtype: str | None = None
    rgba: str | None = None  # "RRGGBB" or "RRGGBBAA"; None → neutral grey
    extra_colors: list[str] | None = None  # additional hex colours (no '#')
    storage_location: str | None = None
    deeplink_url: str = ""  # what the QR encodes; caller composes it


# ── Colour helpers ───────────────────────────────────────────────────────────


def _color_from_hex(hex_str: str | None, fallback: Color = HexColor(0x808080)) -> Color:
    """Parse an RRGGBB or RRGGBBAA string (no '#') into a ReportLab Color.

    Alpha is honoured so multi-colour spools with translucent overlays render
    correctly. Falls back to ``fallback`` for None / malformed input rather
    than raising — labels should always print.
    """
    if not hex_str:
        return fallback
    h = hex_str.lstrip("#").strip()
    if len(h) not in (6, 8):
        return fallback
    try:
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
        a = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
        return Color(r, g, b, alpha=a)
    except ValueError:
        return fallback


def _luminance(color: Color) -> float:
    """Perceived luminance of a ReportLab Color (0–1, WCAG-style approximation)."""
    return 0.299 * color.red + 0.587 * color.green + 0.114 * color.blue


def _hex_code_label(rgba: str | None) -> str:
    """Format ``data.rgba`` as a printable ``#RRGGBB`` string for the label.

    Drops the alpha channel (printed labels can't show transparency) and
    upper-cases the hex digits to match the colour-picker convention used in
    the inventory UI. Returns an empty string for None / malformed input so
    the caller can ``if hex_code:`` skip drawing without an exception.
    """
    if not rgba:
        return ""
    h = rgba.lstrip("#").strip()
    if len(h) not in (6, 8):
        return ""
    rgb = h[:6]
    if not all(c in "0123456789abcdefABCDEF" for c in rgb):
        return ""
    return f"#{rgb.upper()}"


# ── QR generation ────────────────────────────────────────────────────────────


def _qr_png_bytes(payload: str, *, box_size: int = 4, border: int = 2) -> bytes:
    """Render ``payload`` as a tight QR PNG. Empty payload returns empty bytes
    so callers can skip drawing without checking ahead of time.
    """
    if not payload:
        return b""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Single-label drawing ─────────────────────────────────────────────────────


def _draw_swatch(c: rl_canvas.Canvas, x: float, y: float, w: float, h: float, data: LabelData) -> None:
    """Draw the colour swatch. Multi-colour spools use vertical stripes
    (matching the FilamentSwatch convention in the frontend)."""
    primary = _color_from_hex(data.rgba)
    extras = [_color_from_hex(h) for h in (data.extra_colors or []) if h]
    colors = [primary, *extras]

    if not colors:
        c.setFillColor(HexColor(0x808080))
        c.rect(x, y, w, h, stroke=0, fill=1)
        return

    stripe_w = w / len(colors)
    for i, col in enumerate(colors):
        c.setFillColor(col)
        c.rect(x + i * stripe_w, y, stripe_w, h, stroke=0, fill=1)

    # Thin black border so light-colour swatches stay visible on white labels.
    c.setStrokeColor(black)
    c.setLineWidth(0.3)
    c.rect(x, y, w, h, stroke=1, fill=0)


def _draw_qr(c: rl_canvas.Canvas, x: float, y: float, size: float, payload: str) -> None:
    """Embed a square QR at (x, y) with edge length ``size`` (in points)."""
    png = _qr_png_bytes(payload)
    if not png:
        return
    from reportlab.lib.utils import ImageReader

    img = ImageReader(io.BytesIO(png))
    c.drawImage(img, x, y, width=size, height=size, mask="auto")


def _truncate_to_width(c: rl_canvas.Canvas, text: str, font: str, size: float, max_w: float) -> str:
    """Truncate ``text`` with an ellipsis so it fits within ``max_w`` points."""
    if c.stringWidth(text, font, size) <= max_w:
        return text
    ell = "…"
    while text and c.stringWidth(text + ell, font, size) > max_w:
        text = text[:-1]
    return text + ell if text else ell


def _draw_label(c: rl_canvas.Canvas, x: float, y: float, w: float, h: float, data: LabelData) -> None:
    """Render one label inside the box (x, y, w, h). Origin is bottom-left.

    Two layouts, picked by available height:

    - **Tight** (h < 20 mm — AMS holder): swatch on the left, three lines of
      text on the right (brand, material+subtype, big spool ID). No QR — at
      30×15 mm there is not enough horizontal room for swatch + text + QR
      without truncating away the user-need fields, and the AMS holder is an
      at-a-glance identifier where the spool ID is the killer field. The
      box-label and Avery templates carry the QR for the other use cases.

    - **Roomy** (h >= 20 mm — box label, Avery sheets): swatch on the left,
      QR on the right, multi-line text in the middle column. Large spool ID
      anchored at bottom-left under the swatch so it stays readable when the
      label is on a box on a shelf at arm's length.
    """
    pad = 1.2 * mm
    inner_x, inner_y = x + pad, y + pad
    inner_w = w - 2 * pad
    inner_h = h - 2 * pad

    # Outer hairline border so labels are easy to cut out from blank stock.
    c.setStrokeColor(HexColor(0xCCCCCC))
    c.setLineWidth(0.4)
    c.rect(x, y, w, h, stroke=1, fill=0)

    is_tight = h < 20 * mm

    if is_tight:
        _draw_label_tight(c, x, y, w, h, inner_x, inner_y, inner_w, inner_h, pad, data)
    else:
        _draw_label_roomy(c, x, y, w, h, inner_x, inner_y, inner_w, inner_h, pad, data)


def _draw_label_tight(
    c: rl_canvas.Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    inner_x: float,
    inner_y: float,
    inner_w: float,
    inner_h: float,
    pad: float,
    data: LabelData,
) -> None:
    """AMS-holder layout (e.g. 30×15 mm). Swatch + brand/material/hex/ID, no QR."""
    swatch_w = min(inner_h, inner_w * 0.35)
    swatch_y = inner_y + (inner_h - swatch_w) / 2
    _draw_swatch(c, inner_x, swatch_y, swatch_w, swatch_w, data)

    text_x = inner_x + swatch_w + pad
    text_w = inner_w - swatch_w - pad
    if text_w < 5 * mm:
        return  # Pathological — even the swatch barely fits.

    c.setFillColor(black)

    # Top: brand — bumped to bold + larger per the #809 follow-up so it's the
    # easiest thing to read on a small AMS holder at arm's length.
    brand_size = 6.5
    if data.brand:
        c.setFont("Helvetica-Bold", brand_size)
        brand = _truncate_to_width(c, data.brand, "Helvetica-Bold", brand_size, text_w)
        c.drawString(text_x, y + h - pad - brand_size, brand)

    # Second line: material + subtype, small
    sub_size = 5
    sub_line = " ".join(filter(None, [data.material, data.subtype]))
    sub_y_baseline = y + h - pad - brand_size - 0.6 - sub_size
    if sub_line:
        c.setFont("Helvetica", sub_size)
        sub_line = _truncate_to_width(c, sub_line, "Helvetica", sub_size, text_w)
        c.drawString(text_x, sub_y_baseline, sub_line)

    # Third line (when there's room): hex code, tiny — useful when the user
    # has multiple near-identical colours in the same material family.
    hex_code = _hex_code_label(data.rgba)
    if hex_code:
        hex_size = 4.5
        hex_y = sub_y_baseline - 0.4 - hex_size
        # Don't render if it'd collide with the spool ID at the bottom.
        if hex_y > inner_y + 13:
            c.setFont("Helvetica", hex_size)
            c.drawString(text_x, hex_y, hex_code)

    # Bottom: BIG spool ID — the killer field at-a-glance.
    id_size = 13
    c.setFont("Helvetica-Bold", id_size)
    id_text = _truncate_to_width(c, f"#{data.spool_id}", "Helvetica-Bold", id_size, text_w)
    c.drawString(text_x, inner_y + 0.5, id_text)


def _draw_label_roomy(
    c: rl_canvas.Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    inner_x: float,
    inner_y: float,
    inner_w: float,
    inner_h: float,
    pad: float,
    data: LabelData,
) -> None:
    """Box-label / Avery layout. Swatch left, QR right, text middle."""
    # Swatch: full inner height, ~18% of inner width but capped so we never
    # eat the text column on extreme aspect ratios.
    swatch_w = min(inner_w * 0.18, inner_h, 16 * mm)
    swatch_h = inner_h
    _draw_swatch(c, inner_x, inner_y, swatch_w, swatch_h, data)

    # QR: square, capped at the smaller of (a fraction of width, the inner
    # height, or 18 mm — beyond that the QR is overkill for the print size).
    qr_size = min(inner_w * 0.20, inner_h, 18 * mm)
    qr_x = x + w - pad - qr_size
    qr_y = inner_y + (inner_h - qr_size) / 2
    _draw_qr(c, qr_x, qr_y, qr_size, data.deeplink_url)

    text_x = inner_x + swatch_w + 1.5 * mm
    text_w = qr_x - text_x - 1.5 * mm
    if text_w < 8 * mm:
        return

    c.setFillColor(black)

    # Build the text rows we want to render, in top→bottom order.
    line1 = data.brand or ""
    line2 = " · ".join(filter(None, [data.material, data.subtype]))
    name = data.name or ""
    hex_code = _hex_code_label(data.rgba)

    # Layout from the top of the text column.
    cursor_y = y + h - pad

    # Brand — bumped to bold + larger per the #809 follow-up.
    if line1:
        size = 8
        c.setFont("Helvetica-Bold", size)
        text = _truncate_to_width(c, line1, "Helvetica-Bold", size, text_w)
        cursor_y -= size
        c.drawString(text_x, cursor_y, text)
        cursor_y -= 1.2

    if line2:
        size = 7
        c.setFont("Helvetica", size)
        text = _truncate_to_width(c, line2, "Helvetica", size, text_w)
        cursor_y -= size
        c.drawString(text_x, cursor_y, text)
        cursor_y -= 1.5

    # Hex colour code — useful for telling near-identical material+colour
    # spools apart when the swatch is small or the user is colour-blind.
    if hex_code:
        size = 6.5
        c.setFont("Helvetica", size)
        cursor_y -= size
        c.drawString(text_x, cursor_y, hex_code)
        cursor_y -= 1.2

    if name and name != line1:
        size = 9
        c.setFont("Helvetica-Bold", size)
        text = _truncate_to_width(c, name, "Helvetica-Bold", size, text_w)
        cursor_y -= size
        c.drawString(text_x, cursor_y, text)
        cursor_y -= 1.2

    if data.storage_location:
        size = 6.5
        c.setFont("Helvetica-Oblique", size)
        text = _truncate_to_width(c, data.storage_location, "Helvetica-Oblique", size, text_w)
        cursor_y -= size
        c.drawString(text_x, cursor_y, text)

    # Spool ID — anchored at the bottom of the text column, big and bold.
    id_size = 16
    c.setFont("Helvetica-Bold", id_size)
    id_text = _truncate_to_width(c, f"#{data.spool_id}", "Helvetica-Bold", id_size, text_w)
    c.drawString(text_x, inner_y + 0.5, id_text)


# ── Template entry points ────────────────────────────────────────────────────

# (label_w_mm, label_h_mm) for single-label-per-page templates.
_SINGLE_LABEL_SIZES_MM: dict[str, tuple[float, float]] = {
    "ams_30x15": (30.0, 15.0),
    "box_40x30": (40.0, 30.0),
    "box_62x29": (62.0, 29.0),
}

# Sheet template parameters: (page_size, label_w_mm, label_h_mm,
#                              cols, rows, top_margin_mm, left_margin_mm,
#                              col_gap_mm, row_gap_mm)
_SHEET_TEMPLATES: dict[str, tuple] = {
    "avery_5160": (letter, 66.675, 25.4, 3, 10, 12.7, 4.76, 3.175, 0.0),
    "avery_l7160": (A4, 63.5, 38.1, 3, 7, 15.15, 7.0, 2.5, 0.0),
}


def _render_single_label_pdf(template: TemplateName, data_list: list[LabelData]) -> bytes:
    w_mm, h_mm = _SINGLE_LABEL_SIZES_MM[template]
    page_w, page_h = w_mm * mm, h_mm * mm

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.setTitle(f"Bambuddy spool labels ({template})")

    for data in data_list:
        _draw_label(c, 0, 0, page_w, page_h, data)
        c.showPage()

    c.save()
    return buf.getvalue()


def _render_sheet_pdf(template: TemplateName, data_list: list[LabelData]) -> bytes:
    page_size, w_mm, h_mm, cols, rows, top_mm, left_mm, col_gap_mm, row_gap_mm = _SHEET_TEMPLATES[template]
    page_w, page_h = page_size

    label_w = w_mm * mm
    label_h = h_mm * mm
    top_margin = top_mm * mm
    left_margin = left_mm * mm
    col_gap = col_gap_mm * mm
    row_gap = row_gap_mm * mm

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=page_size)
    c.setTitle(f"Bambuddy spool labels ({template})")

    per_page = cols * rows
    for page_start in range(0, len(data_list), per_page):
        chunk = data_list[page_start : page_start + per_page]
        for idx, data in enumerate(chunk):
            row = idx // cols
            col = idx % cols
            x = left_margin + col * (label_w + col_gap)
            y = page_h - top_margin - (row + 1) * label_h - row * row_gap
            _draw_label(c, x, y, label_w, label_h, data)
        c.showPage()

    c.save()
    return buf.getvalue()


def render_labels(template: TemplateName, data_list: list[LabelData]) -> bytes:
    """Render ``data_list`` to a PDF using the named template. Returns bytes.

    Empty ``data_list`` still produces a valid (empty) PDF — callers should
    short-circuit beforehand if that's not desired.
    """
    if template in _SINGLE_LABEL_SIZES_MM:
        return _render_single_label_pdf(template, data_list)
    if template in _SHEET_TEMPLATES:
        return _render_sheet_pdf(template, data_list)
    raise ValueError(f"Unknown label template: {template!r}")


__all__ = ["LabelData", "TemplateName", "render_labels"]
# white re-exported for completeness; future templates may need a paper-tone variant.
_ = white
