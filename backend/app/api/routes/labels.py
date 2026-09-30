"""Spool label printing routes (#809).

Two endpoints, one per inventory backend:

- ``POST /inventory/labels``  — local-DB spools
- ``POST /spoolman/labels``   — Spoolman-backed spools

Both accept ``{spool_ids: [int], template: str, starting_position: int}`` plus
the lines to print (``fields``) and the output ``format``: a PDF, or PNGs for
label-printer software that takes images — one PNG as is, several in a ZIP.
Each has a ``/preview`` sibling that renders one label as a PNG for the
picker (#2981).

The QR code on each label deep-links to ``/inventory?spool=<id>`` so a phone
scan jumps straight back into Bambuddy at that spool's row.
"""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool
from backend.app.api.routes.settings import get_setting
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.spool import Spool
from backend.app.models.user import User
from backend.app.services.label_renderer import (
    ALL_LABEL_FIELDS,
    DEFAULT_LABEL_FIELDS,
    LabelData,
    LabelField,
    TemplateName,
    get_sheet_capacity,
    pdf_to_pngs,
    render_label_preview_pdf,
    render_labels,
)
from backend.app.services.spoolman import get_spoolman_client
from backend.app.utils.http import build_content_disposition

logger = logging.getLogger(__name__)

router = APIRouter(tags=["labels"])

# Cap how many labels can be requested in one go. Sane upper bound for the
# largest realistic batch (an Avery sheet at 30/page × ~10 pages).
MAX_LABELS_PER_REQUEST = 500

# Preview resolution. High enough that the browser's downscale stays sharp on
# a HiDPI screen; a label is a few centimetres, so the PNG stays small.
PREVIEW_DPI = 300


# Spoolman's mapping rejects an id below 1, so one never reaches it.
SpoolId = Annotated[int, Field(gt=0)]


class LabelOptions(BaseModel):
    template: TemplateName
    # Black-and-white thermal printers: drop the colour swatch (prints as a
    # muddy grey block) and widen the text column instead (#1870).
    monochrome: bool = False
    # The lines to print (#2981). Omitted: what labels always carried.
    fields: list[LabelField] | None = Field(default=None, max_length=len(ALL_LABEL_FIELDS))

    def field_set(self) -> frozenset[LabelField]:
        return DEFAULT_LABEL_FIELDS if self.fields is None else frozenset(self.fields)


class LabelRequest(LabelOptions):
    spool_ids: list[SpoolId] = Field(..., min_length=1, max_length=MAX_LABELS_PER_REQUEST)
    starting_position: int = Field(default=1, ge=1)
    format: Literal["pdf", "png"] = "pdf"
    # PNG only. 203 and 300 are the common thermal-printer resolutions; an
    # image at the printer's own resolution is printed dot for dot.
    dpi: Literal[203, 300, 600] = 300

    @model_validator(mode="after")
    def validate_starting_position(self) -> LabelRequest:
        capacity = get_sheet_capacity(self.template)
        if capacity is None:
            if self.starting_position != 1:
                raise ValueError("starting_position is only supported for sheet label templates")
            return self
        if self.starting_position > capacity:
            raise ValueError(f"starting_position must be between 1 and {capacity} for template {self.template}")
        return self


class LabelPreviewRequest(LabelOptions):
    spool_id: SpoolId


def _split_extra_colors(raw: str | None) -> list[str] | None:
    """Parse ``Spool.extra_colors`` (comma-separated hex tokens) into a list."""
    if not raw:
        return None
    parts = [p.strip().lstrip("#") for p in raw.split(",") if p.strip()]
    return parts or None


async def _resolve_deeplink_base(request: Request, db: AsyncSession) -> str:
    """Where the QR codes should point. Prefers `external_url` when set so a
    phone scan reaches the user's public Bambuddy URL rather than an internal
    address; falls back to the request's own scheme+host when no setting is
    configured.
    """
    external = (await get_setting(db, "external_url") or "").strip().rstrip("/")
    if external:
        return external
    return f"{request.url.scheme}://{request.url.netloc}"


def _spool_to_label_data(spool: Spool, deeplink_base: str) -> LabelData:
    name = spool.color_name or spool.slicer_filament_name or f"{spool.brand or ''} {spool.material}".strip()
    return LabelData(
        spool_id=spool.id,
        name=name or spool.material,
        material=spool.material,
        brand=spool.brand,
        subtype=spool.subtype,
        rgba=spool.rgba,
        extra_colors=_split_extra_colors(spool.extra_colors),
        storage_location=getattr(spool, "storage_location", None),
        deeplink_url=f"{deeplink_base}/inventory?spool={spool.id}",
        material_number=spool.material_number,
        nozzle_temp_min=spool.nozzle_temp_min,
        nozzle_temp_max=spool.nozzle_temp_max,
        label_weight=spool.label_weight,
        note=spool.note,
        added=spool.created_at.date() if spool.created_at else None,
    )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _spoolman_dict_to_label_data(s: dict, deeplink_base: str) -> LabelData:
    """Build LabelData from a raw Spoolman /spool response dict.

    Goes through ``_map_spoolman_spool``, the mapping the inventory page shows,
    so a Spoolman label carries what a built-in-inventory label does: the same
    subtype, colour name, material number, temperature and weight.
    """
    m = _map_spoolman_spool(s)
    material = m["material"] or ""
    # Same precedence as a built-in spool: a colour name the user set, then the
    # filament (slicer) name. A synthesised colour name is only the subtype.
    color_name = None if m["color_name_is_synthesized"] else m["color_name"]
    name = color_name or m["slicer_filament_name"] or f"{m['brand'] or ''} {material}".strip()

    return LabelData(
        spool_id=m["id"],
        name=name or material or "Spool",
        material=material,
        brand=m["brand"],
        subtype=m["subtype"],
        rgba=m["rgba"],
        extra_colors=_split_extra_colors(m["extra_colors"]),
        storage_location=m["storage_location"],
        deeplink_url=f"{deeplink_base}/inventory?spool={m['id']}",
        material_number=m["material_number"],
        nozzle_temp_min=m["nozzle_temp_min"],
        nozzle_temp_max=m["nozzle_temp_max"],
        label_weight=m["label_weight"],
        note=m["note"],
        added=_parse_date(m["created_at"]),
    )


def _stream_pdf(pdf: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={
            "Content-Disposition": build_content_disposition(filename, disposition="inline"),
            "Content-Length": str(len(pdf)),
            # PDFs are deterministic per request; tell the browser not to cache
            # so re-printing after edits picks up the new data.
            "Cache-Control": "no-store",
        },
    )


async def _render_response(body: LabelRequest, data_list: list[LabelData], filename_stem: str) -> Response:
    pdf = render_labels(
        body.template,
        data_list,
        monochrome=body.monochrome,
        starting_position=body.starting_position,
        fields=body.field_set(),
    )
    if body.format == "pdf":
        return _stream_pdf(pdf, f"{filename_stem}.pdf")

    # Rasterising a few hundred pages is CPU work; keep it off the event loop.
    pngs = await asyncio.to_thread(pdf_to_pngs, pdf, body.dpi)
    if len(pngs) == 1:
        return Response(
            content=pngs[0],
            media_type="image/png",
            headers={
                "Content-Disposition": build_content_disposition(f"{filename_stem}.png", disposition="attachment"),
                "Cache-Control": "no-store",
            },
        )

    # A roll template has one page per spool, so its files are named after
    # the spool; a sheet's pages are numbered.
    if get_sheet_capacity(body.template) is None:
        names = [f"label-{d.spool_id}.png" for d in data_list]
    else:
        names = [f"sheet-{n}.png" for n in range(1, len(pngs) + 1)]
    buf = io.BytesIO()
    # PNG is already compressed; deflating it again only costs time.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, png in zip(names, pngs, strict=True):
            zf.writestr(name, png)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": build_content_disposition(f"{filename_stem}.zip", disposition="attachment"),
            "Cache-Control": "no-store",
        },
    )


async def _render_preview(body: LabelPreviewRequest, data: LabelData) -> Response:
    pdf = render_label_preview_pdf(body.template, data, monochrome=body.monochrome, fields=body.field_set())
    pngs = await asyncio.to_thread(pdf_to_pngs, pdf, PREVIEW_DPI)
    return Response(content=pngs[0], media_type="image/png", headers={"Cache-Control": "no-store"})


async def _local_label_data(spool_ids: list[int], request: Request, db: AsyncSession) -> list[LabelData]:
    result = await db.execute(select(Spool).where(Spool.id.in_(spool_ids)))
    spools = list(result.scalars().all())

    found_ids = {s.id for s in spools}
    missing = [sid for sid in spool_ids if sid not in found_ids]
    if missing:
        raise HTTPException(404, f"Spool(s) not found: {missing}")

    # Preserve caller's order so an Avery sheet print matches the on-screen list.
    ordered = sorted(spools, key=lambda s: spool_ids.index(s.id))

    deeplink_base = await _resolve_deeplink_base(request, db)
    return [_spool_to_label_data(s, deeplink_base) for s in ordered]


async def _spoolman_label_data(spool_ids: list[int], request: Request, db: AsyncSession) -> list[LabelData]:
    """The Spoolman client doesn't expose a per-id endpoint, so this fetches the
    full spool list and filters in-memory. For typical libraries (~50 spools)
    that's negligible; for very large libraries this is the trade-off until
    Spoolman gains a bulk filter.
    """
    spoolman_on = (await get_setting(db, "spoolman_enabled") or "").lower() == "true"
    if not spoolman_on:
        raise HTTPException(400, "Spoolman integration is not enabled")

    client = await get_spoolman_client()
    if client is None or not client.is_connected:
        raise HTTPException(503, "Spoolman not reachable")

    try:
        all_spools = await client.get_spools()
    except Exception as exc:
        logger.warning("Spoolman fetch failed during label render: %s", exc)
        raise HTTPException(502, "Failed to fetch spools from Spoolman") from exc

    by_id = {int(s.get("id", 0)): s for s in all_spools if s.get("id") is not None}
    missing = [sid for sid in spool_ids if sid not in by_id]
    if missing:
        raise HTTPException(404, f"Spool(s) not found in Spoolman: {missing}")

    deeplink_base = await _resolve_deeplink_base(request, db)
    return [_spoolman_dict_to_label_data(by_id[sid], deeplink_base) for sid in spool_ids]


@router.post("/inventory/labels")
async def render_local_inventory_labels(
    body: LabelRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> Response:
    """Render labels for spools in the local inventory."""
    data_list = await _local_label_data(body.spool_ids, request, db)
    return await _render_response(body, data_list, f"bambuddy-labels-{body.template}")


@router.post("/inventory/labels/preview")
async def preview_local_inventory_label(
    body: LabelPreviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> Response:
    """One label for a local spool, as a PNG, for the picker's preview."""
    data_list = await _local_label_data([body.spool_id], request, db)
    return await _render_preview(body, data_list[0])


@router.post("/spoolman/labels")
async def render_spoolman_labels(
    body: LabelRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> Response:
    """Render labels for spools tracked in Spoolman."""
    data_list = await _spoolman_label_data(body.spool_ids, request, db)
    return await _render_response(body, data_list, f"bambuddy-labels-spoolman-{body.template}")


@router.post("/spoolman/labels/preview")
async def preview_spoolman_label(
    body: LabelPreviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> Response:
    """One label for a Spoolman spool, as a PNG, for the picker's preview."""
    data_list = await _spoolman_label_data([body.spool_id], request, db)
    return await _render_preview(body, data_list[0])
