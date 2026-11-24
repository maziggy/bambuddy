"""Project labels (#3655): a label for a project's finished parts.

When a project's prints are done the parts go into a bag or a box, and this
label says what is in it: the project, how many, a line the user types (an
order number, "2/3"), its dates and a QR code back to the project page.

Only the drawing is new. The templates, custom and saved sheets, page layout,
PDF and PNG output are the spool labels' (``label_renderer``): this module
hands ``draw_project_label`` to ``render_labels`` and
``render_label_preview_pdf``.

As on spool labels the text is data only (names, numbers, dates), never words,
because the PDF is not translated: the quantity prints as ``× 12``, dates as
ISO dates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from reportlab.lib.colors import HexColor, black
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

from backend.app.services.label_renderer import (
    FONT_BOLD,
    FONT_ITALIC,
    FONT_REGULAR,
    _color_from_hex,
    _draw_qr,
    _draw_text,
    _roomy_qr_size,
    _text_width,
    _wrap_to_width,
)

ProjectLabelField = Literal[
    "parent",
    "name",
    "note",
    "parts",
    "due_date",
    "printed",
    "color",
    "qr",
    "quantity",
    "project_id",
]

# Top-to-bottom order of the lines, and the order the dialog lists them.
ALL_PROJECT_LABEL_FIELDS: tuple[ProjectLabelField, ...] = (
    "parent",
    "name",
    "note",
    "parts",
    "due_date",
    "printed",
    "color",
    "qr",
    "quantity",
    "project_id",
)

# What a bag label needs: what's inside, how many, and the way back.
DEFAULT_PROJECT_LABEL_FIELDS: frozenset[ProjectLabelField] = frozenset(
    {"name", "note", "due_date", "color", "qr", "quantity"}
)

# Longest free-text line the dialog sends; a label shows a few words of it.
MAX_NOTE_LENGTH = 200

# The most lines the project name may wrap to.
_NAME_MAX_LINES = 3


@dataclass
class ProjectLabelData:
    """One project label: the project's own data plus what the user typed."""

    project_id: int
    name: str
    deeplink_url: str = ""  # what the QR encodes; the caller composes it
    color: str | None = None  # "#RRGGBB" as projects store it; None → no stripe
    parent_name: str | None = None
    due_date: date | None = None
    printed: date | None = None  # last completed print of the project
    completed_parts: int = 0
    target_parts: int | None = None
    quantity: int | None = None  # parts in this bag, typed in the dialog
    note: str | None = None  # free text typed in the dialog


def _parts_text(data: ProjectLabelData) -> str:
    """``4/10`` against the parts target, or just the count without one."""
    if data.target_parts:
        return f"{data.completed_parts}/{data.target_parts}"
    return str(data.completed_parts) if data.completed_parts else ""


def draw_project_label(
    c: rl_canvas.Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    data: ProjectLabelData,
    monochrome: bool = False,
    fields: frozenset[ProjectLabelField] = DEFAULT_PROJECT_LABEL_FIELDS,
) -> None:
    """Render one project label inside the box (x, y, w, h). Origin is bottom-left.

    A colour stripe on the left (the project colour), the QR on the right, and
    in between the lines top to bottom with the quantity big at the bottom:
    from across a shelf "what" and "how many" are what a bag label is read for.
    """
    pad = 1.2 * mm
    inner_x, inner_y = x + pad, y + pad
    inner_w, inner_h = w - 2 * pad, h - 2 * pad

    # Hairline border, as on spool labels, for cutting labels out of blank stock.
    c.setStrokeColor(HexColor(0xCCCCCC))
    c.setLineWidth(0.4)
    c.rect(x, y, w, h, stroke=1, fill=0)

    # Monochrome drops the stripe: it would print as a grey bar (#1870).
    stripe_w = 0.0
    if "color" in fields and data.color and not monochrome:
        stripe_w = min(3 * mm, inner_w * 0.06)
        c.saveState()
        c.setFillColor(_color_from_hex(data.color))
        c.setStrokeColor(black)
        c.setLineWidth(0.3)
        c.rect(inner_x, inner_y, stripe_w, inner_h, stroke=1, fill=1)
        c.restoreState()

    qty_text = f"× {data.quantity}" if "quantity" in fields and data.quantity else ""
    id_text = f"#{data.project_id}" if "project_id" in fields else ""
    qr_size = _roomy_qr_size(inner_w, inner_h) if "qr" in fields and data.deeplink_url else 0
    if id_text:
        qr_size = min(qr_size, inner_h - 1.3 * 7)
    text_right = inner_x + inner_w
    qr_x = text_right - qr_size
    if qr_size:
        text_right = qr_x - 1.5 * mm

    text_x = inner_x + stripe_w + (1.5 * mm if stripe_w else 0)
    text_w = text_right - text_x
    id_size = 7.0 if id_text else 0.0
    if qr_size:
        # Put the ID beneath the QR to leave the full text column for the quantity.
        if id_text:
            id_size = min(id_size, qr_size / _text_width(id_text, FONT_REGULAR, 1))
        qr_y = inner_y + max((inner_h - qr_size) / 2, 1.3 * id_size)
        _draw_qr(c, qr_x, qr_y, qr_size, data.deeplink_url)

    qty_size = 16.0 if qty_text else 0.0
    footer_width = _text_width(qty_text, FONT_BOLD, qty_size)
    if id_text and not qr_size:
        footer_width += _text_width(id_text, FONT_REGULAR, id_size)
    footer_gap = 2 * mm if qty_text and id_text and not qr_size else 0
    if footer_width > text_w - footer_gap:
        factor = (text_w - footer_gap) / footer_width
        qty_size *= factor
        if not qr_size:
            id_size *= factor
    footer_height = 1.2 * max(qty_size, id_size if not qr_size else 0)

    # (field, text, font, size, gap below)
    rows: list[tuple[ProjectLabelField, str, str, float, float]] = [
        ("parent", data.parent_name or "", FONT_REGULAR, 6.5, 1.0),
        ("name", data.name, FONT_BOLD, 10, 1.4),
        ("note", data.note or "", FONT_ITALIC, 7.5, 1.2),
        ("parts", _parts_text(data), FONT_REGULAR, 6.5, 1.2),
        ("due_date", data.due_date.isoformat() if data.due_date else "", FONT_REGULAR, 6.5, 1.2),
        ("printed", data.printed.isoformat() if data.printed else "", FONT_REGULAR, 6.5, 1.2),
    ]
    active = [row for row in rows if row[0] in fields and row[1]]

    # Reserve one line per selected value before assigning spare lines to names
    # and notes. Short sheets scale the whole block, including the footer.
    required = sum(size + gap for _, _, _, size, gap in active) + footer_height
    scale = min(1.0, inner_h / required) if required else 1.0
    floor_y = inner_y + footer_height * scale
    cursor_y = inner_y + inner_h
    c.setFillColor(black)
    for i, (field, text, font, size, gap) in enumerate(active):
        size *= scale
        gap *= scale
        below = sum((s + g) * scale for _, _, _, s, g in active[i + 1 :])
        if field in ("parent", "name", "note"):
            most = 1 if field == "parent" else _NAME_MAX_LINES
            available = cursor_y - floor_y - below
            room = max(1, min(most, int(available / (size + gap))))
            lines = _wrap_to_width(c, text, font, size, text_w, room)
        else:
            # Dates and counts must remain exact even on the narrowest stock.
            size = min(size, text_w / _text_width(text, font, 1))
            lines = [text]
        for line in lines:
            cursor_y -= size
            _draw_text(c, text_x, cursor_y, line, font, size)
            cursor_y -= gap

    qty_size *= scale
    baseline = inner_y + 0.2 * max(qty_size, id_size * scale if not qr_size else 0)
    if qty_text:
        _draw_text(c, text_x, baseline, qty_text, FONT_BOLD, qty_size)
    if id_text:
        if qr_size:
            id_x = qr_x + (qr_size - _text_width(id_text, FONT_REGULAR, id_size)) / 2
            _draw_text(c, id_x, inner_y + 0.2 * id_size, id_text, FONT_REGULAR, id_size)
        else:
            id_size *= scale
            _draw_text(
                c, text_right - _text_width(id_text, FONT_REGULAR, id_size), baseline, id_text, FONT_REGULAR, id_size
            )


__all__ = [
    "ALL_PROJECT_LABEL_FIELDS",
    "DEFAULT_PROJECT_LABEL_FIELDS",
    "MAX_NOTE_LENGTH",
    "ProjectLabelData",
    "ProjectLabelField",
    "draw_project_label",
]
