"""Spool label printing routes (#809).

Two endpoints, one per inventory backend:

- ``POST /inventory/labels``  — local-DB spools
- ``POST /spoolman/labels``   — Spoolman-backed spools

Both accept ``{spool_ids: [int], template: str}`` and return a PDF stream.
The QR code on each label deep-links to ``/inventory?spool=<id>`` so a phone
scan jumps straight back into Bambuddy at that spool's row.
"""

from __future__ import annotations

import io
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_setting
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.spool import Spool
from backend.app.models.user import User
from backend.app.services.label_renderer import LabelData, TemplateName, render_labels
from backend.app.services.spoolman import get_spoolman_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["labels"])

_VALID_TEMPLATES: tuple[TemplateName, ...] = (
    "ams_30x15",
    "box_62x29",
    "avery_5160",
    "avery_l7160",
)

# Cap how many labels can be requested in one go. Sane upper bound for the
# largest realistic batch (an Avery sheet at 30/page × ~10 pages).
MAX_LABELS_PER_REQUEST = 500


class LabelRequest(BaseModel):
    spool_ids: list[int] = Field(..., min_length=1, max_length=MAX_LABELS_PER_REQUEST)
    template: Literal["ams_30x15", "box_62x29", "avery_5160", "avery_l7160"]


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
    )


def _spoolman_dict_to_label_data(s: dict, deeplink_base: str) -> LabelData:
    """Build LabelData from a raw Spoolman /spool response dict.

    Spoolman models don't have a native 'spool name' — we derive it from the
    embedded filament. Material and brand come from filament/vendor.
    """
    filament = s.get("filament") or {}
    vendor = filament.get("vendor") or {}
    fname = filament.get("name") or ""
    material = filament.get("material") or ""
    brand = vendor.get("name")
    color_hex = filament.get("color_hex")
    rgba = color_hex.lstrip("#") if isinstance(color_hex, str) else None

    multi_colors = filament.get("multi_color_hexes")
    extra: list[str] | None = None
    if isinstance(multi_colors, str) and multi_colors.strip():
        extra = [tok.strip().lstrip("#") for tok in multi_colors.split(",") if tok.strip()]
    elif isinstance(multi_colors, list):
        extra = [str(t).strip().lstrip("#") for t in multi_colors if str(t).strip()]

    return LabelData(
        spool_id=int(s.get("id", 0)),
        name=fname or material or "Spool",
        material=material or "",
        brand=brand,
        subtype=None,
        rgba=rgba,
        extra_colors=extra,
        storage_location=s.get("location"),
        deeplink_url=f"{deeplink_base}/inventory?spool={int(s.get('id', 0))}",
    )


def _stream_pdf(pdf: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(len(pdf)),
            # PDFs are deterministic per request; tell the browser not to cache
            # so re-printing after edits picks up the new data.
            "Cache-Control": "no-store",
        },
    )


@router.post("/inventory/labels")
async def render_local_inventory_labels(
    body: LabelRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> StreamingResponse:
    """Render labels for spools in the local inventory."""
    if body.template not in _VALID_TEMPLATES:
        raise HTTPException(400, f"Unknown template: {body.template}")

    result = await db.execute(select(Spool).where(Spool.id.in_(body.spool_ids)))
    spools = list(result.scalars().all())

    found_ids = {s.id for s in spools}
    missing = [sid for sid in body.spool_ids if sid not in found_ids]
    if missing:
        raise HTTPException(404, f"Spool(s) not found: {missing}")

    # Preserve caller's order so an Avery sheet print matches the on-screen list.
    ordered = sorted(spools, key=lambda s: body.spool_ids.index(s.id))

    deeplink_base = await _resolve_deeplink_base(request, db)
    data_list = [_spool_to_label_data(s, deeplink_base) for s in ordered]

    pdf = render_labels(body.template, data_list)
    filename = f"bambuddy-labels-{body.template}.pdf"
    return _stream_pdf(pdf, filename)


@router.post("/spoolman/labels")
async def render_spoolman_labels(
    body: LabelRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> StreamingResponse:
    """Render labels for spools tracked in Spoolman.

    The Spoolman client doesn't expose a per-id endpoint, so this fetches the
    full spool list and filters in-memory. For typical libraries (~50 spools)
    that's negligible; for very large libraries this is the trade-off until
    Spoolman gains a bulk filter.
    """
    if body.template not in _VALID_TEMPLATES:
        raise HTTPException(400, f"Unknown template: {body.template}")

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
    missing = [sid for sid in body.spool_ids if sid not in by_id]
    if missing:
        raise HTTPException(404, f"Spool(s) not found in Spoolman: {missing}")

    deeplink_base = await _resolve_deeplink_base(request, db)
    data_list = [_spoolman_dict_to_label_data(by_id[sid], deeplink_base) for sid in body.spool_ids]

    pdf = render_labels(body.template, data_list)
    filename = f"bambuddy-labels-spoolman-{body.template}.pdf"
    return _stream_pdf(pdf, filename)
