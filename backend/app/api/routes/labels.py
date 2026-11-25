"""Spool label printing routes (#809).

Two endpoints, one per inventory backend:

- ``POST /inventory/labels``  — local-DB spools
- ``POST /spoolman/labels``   — Spoolman-backed spools

Both accept ``{spool_ids: [int], template: str, starting_position: int}`` plus
the lines to print (``fields``) and the output ``format``. ``template`` is a
built-in name, or ``"custom"`` with the sheet itself in ``layout`` (#3296): a PDF, or PNGs for
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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from functools import partial
from typing import Annotated, Literal, ParamSpec

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from PIL import Image
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes._label_printer_helpers import deliver_job, load_label_printer_config
from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool
from backend.app.api.routes.settings import get_setting
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.spool import Spool
from backend.app.models.user import User
from backend.app.schemas.label_template import RollSpec, SheetLayoutSpec
from backend.app.services.label_print_jobs import QUEUE_FULL_MESSAGE, PrintJob, PrintQueueFull, label_print_service
from backend.app.services.label_printers.base import LabelPrinterConfig, PreparedJob, describe_error, get_driver
from backend.app.services.label_renderer import (
    ALL_LABEL_FIELDS,
    DEFAULT_LABEL_FIELDS,
    LabelData,
    LabelField,
    RollLayout,
    Template,
    TemplateName,
    get_sheet_capacity,
    iter_pdf_images,
    pdf_to_pngs,
    render_label_preview_pdf,
    render_labels,
    sheet_page_count,
)
from backend.app.services.spoolman import get_spoolman_client
from backend.app.utils.http import build_content_disposition

logger = logging.getLogger(__name__)

router = APIRouter(tags=["labels"])

# Cap how many labels can be requested in one go. Sane upper bound for the
# largest realistic batch (an Avery sheet at 30/page × ~10 pages).
MAX_LABELS_PER_REQUEST = 500

# PNG export of sheets: pages × dpi. A sheet page is a whole A4 or Letter
# page, rasterised under the process-wide PDFium lock that PDF thumbnails also
# wait on, so a custom one-label-per-page sheet of 500 spools at 600 dpi would
# hold it for minutes. 15 000 is 25 pages at 600 dpi, 50 at 300 and 73 at 203;
# the built-in sheets need at most 24 pages (500 spools on an L7160), so they
# always fit. Mirrored in the dialog (labelSheetLayout.ts).
MAX_PNG_SHEET_PAGES_X_DPI = 15_000

# Printing straight to a label printer (#3612): fewer labels than a download, as
# every one is rasterised and sent in one connection, and the raster resolution
# of the QL printers.
MAX_PRINT_LABELS_PER_REQUEST = 100
# Total tape of one request, labels x length. The printer prints at a few
# centimetres a second and the request waits for it, so this bounds how long.
# 100 labels of 40 mm fit.
MAX_PRINT_TAPE_MM = 4000
PRINT_DPI = 300
# A label that reads along the tape is drawn with the tape width as its height.
# The printer wants the tape width as the image width, so that page is turned a
# quarter; flip this one constant if the text comes out upside down on a printer.
# A label that reads across the tape (RollLayout.across_tape) is printed as drawn.
TAPE_ROTATION = Image.Transpose.ROTATE_90

# Preview resolution. High enough that the browser's downscale stays sharp on
# a HiDPI screen; a label is a few centimetres, so the PNG stays small.
PREVIEW_DPI = 300


# Spoolman's mapping rejects an id below 1, so one never reaches it.
SpoolId = Annotated[int, Field(gt=0)]


class LabelLayoutOptions(BaseModel):
    """Which label stock to print on: shared by spool and project labels (#3655)."""

    template: TemplateName | Literal["custom"]
    # The sheet, when ``template`` is "custom" (#3296). A saved template is
    # sent this way too: the dialog holds its layout, so printing never reads
    # the saved-template table.
    layout: SheetLayoutSpec | None = None
    # Black-and-white thermal printers: drop the colour swatch (prints as a
    # muddy grey block) and widen the text column instead (#1870).
    monochrome: bool = False

    @model_validator(mode="after")
    def layout_only_for_custom(self) -> LabelLayoutOptions:
        if (self.template == "custom") != (self.layout is not None):
            raise ValueError('layout is required for template "custom" and not allowed otherwise')
        return self

    def resolved_template(self) -> Template:
        if self.layout is not None:
            return self.layout.to_layout()
        if self.template == "custom":
            # layout_only_for_custom refuses this, so a validated request never gets here.
            raise ValueError('layout is required for template "custom"')
        return self.template


class LabelOptions(LabelLayoutOptions):
    # Spool labels also print on a label printer's tape (#3612); project labels
    # keep the shared stock, so "roll" is added here and not on the base.
    template: TemplateName | Literal["custom", "roll"]  # pyright: ignore[reportIncompatibleVariableOverride]
    # The tape and label length, when ``template`` is "roll" (#3612): labels for
    # a label printer's continuous tape, one per page.
    roll: RollSpec | None = None
    # The lines to print (#2981). Omitted: what labels always carried.
    fields: list[LabelField] | None = Field(default=None, max_length=len(ALL_LABEL_FIELDS))

    @model_validator(mode="after")
    def roll_only_for_roll(self) -> LabelOptions:
        if (self.template == "roll") != (self.roll is not None):
            raise ValueError('roll is required for template "roll" and not allowed otherwise')
        return self

    def field_set(self) -> frozenset[LabelField]:
        return DEFAULT_LABEL_FIELDS if self.fields is None else frozenset(self.fields)

    def resolved_template(self) -> Template:
        if self.roll is not None:
            return self.roll.to_layout()
        if self.template == "roll":
            # roll_only_for_roll refuses this, so a validated request never gets here.
            raise ValueError('roll is required for template "roll"')
        return super().resolved_template()


def sheet_request_problem(
    template: Template, label_count: int, starting_position: int, format: str, dpi: int, template_name: str
) -> str | None:
    """Why a download of ``label_count`` labels can't be made, or ``None``: a
    starting position on a roll or past the sheet, or a PNG of too many sheet
    pages. Spool and project labels (#3655) check the same."""
    capacity = get_sheet_capacity(template)
    if capacity is None:
        if starting_position != 1:
            return "starting_position is only supported for sheet label templates"
        return None
    if starting_position > capacity:
        return f"starting_position must be between 1 and {capacity} for template {template_name}"
    if format == "png":
        pages = sheet_page_count(template, label_count, starting_position) or 0
        if pages * dpi > MAX_PNG_SHEET_PAGES_X_DPI:
            max_pages = MAX_PNG_SHEET_PAGES_X_DPI // dpi
            return (
                f"PNG export is limited to {max_pages} sheet pages at {dpi} dpi, and this is {pages}. "
                "Use PDF, a lower resolution or fewer labels."
            )
    return None


class LabelRequest(LabelOptions):
    spool_ids: list[SpoolId] = Field(..., min_length=1, max_length=MAX_LABELS_PER_REQUEST)
    starting_position: int = Field(default=1, ge=1)
    format: Literal["pdf", "png"] = "pdf"
    # PNG only. 203 and 300 are the common thermal-printer resolutions; an
    # image at the printer's own resolution is printed dot for dot.
    dpi: Literal[203, 300, 600] = 300

    @model_validator(mode="after")
    def validate_starting_position(self) -> LabelRequest:
        if (self.template == "custom") != (self.layout is not None) or (self.template == "roll") != (
            self.roll is not None
        ):
            return self  # layout_only_for_custom / roll_only_for_roll report this one
        problem = sheet_request_problem(
            self.resolved_template(), len(self.spool_ids), self.starting_position, self.format, self.dpi, self.template
        )
        if problem:
            raise ValueError(problem)
        return self


class LabelPrintRequest(LabelOptions):
    """The download's body without format, dpi and starting position (a roll has no sheet)."""

    spool_ids: list[SpoolId] = Field(..., min_length=1, max_length=MAX_PRINT_LABELS_PER_REQUEST)


class LabelPreviewRequest(LabelOptions):
    spool_id: SpoolId


def _split_extra_colors(raw: str | None) -> list[str] | None:
    """Parse ``Spool.extra_colors`` (comma-separated hex tokens) into a list."""
    if not raw:
        return None
    parts = [p.strip().lstrip("#") for p in raw.split(",") if p.strip()]
    return parts or None


async def resolve_deeplink_base(request: Request, db: AsyncSession) -> str:
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


# ReportLab shares the registered TrueType fonts across documents. Build one
# label PDF at a time, off the event loop, including single-label previews.
_PDF_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="label-pdf")
_P = ParamSpec("_P")


async def render_pdf_in_worker(render: Callable[_P, bytes], *args: _P.args, **kwargs: _P.kwargs) -> bytes:
    return await asyncio.get_running_loop().run_in_executor(_PDF_WORKER, partial(render, *args, **kwargs))


async def _render_response(body: LabelRequest, data_list: list[LabelData], filename_stem: str) -> Response:
    template = body.resolved_template()
    pdf = await render_pdf_in_worker(
        render_labels,
        template,
        data_list,
        monochrome=body.monochrome,
        starting_position=body.starting_position,
        fields=body.field_set(),
    )
    return await label_file_response(
        pdf, template, filename_stem, body.format, body.dpi, [f"label-{d.spool_id}.png" for d in data_list]
    )


async def label_file_response(
    pdf: bytes, template: Template, filename_stem: str, format: str, dpi: int, roll_names: list[str]
) -> Response:
    """The rendered labels as the download asked for: the PDF, one PNG, or a
    ZIP of PNGs. ``roll_names`` names a roll template's PNGs, one per label."""
    if format == "pdf":
        return _stream_pdf(pdf, f"{filename_stem}.pdf")

    # Rasterising a few hundred pages is CPU work; keep it off the event loop.
    pngs = await asyncio.to_thread(pdf_to_pngs, pdf, dpi)
    if len(pngs) == 1:
        return Response(
            content=pngs[0],
            media_type="image/png",
            headers={
                "Content-Disposition": build_content_disposition(f"{filename_stem}.png", disposition="attachment"),
                "Cache-Control": "no-store",
            },
        )

    # A roll template has one page per label, so its files are named after
    # what is on it; a sheet's pages are numbered.
    if get_sheet_capacity(template) is None:
        names = roll_names
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
    pdf = await render_pdf_in_worker(
        render_label_preview_pdf, body.resolved_template(), data, monochrome=body.monochrome, fields=body.field_set()
    )
    return await label_preview_response(pdf)


async def label_preview_response(pdf: bytes) -> Response:
    """A one-label PDF as the PNG the dialog's preview shows."""
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

    deeplink_base = await resolve_deeplink_base(request, db)
    return [_spool_to_label_data(s, deeplink_base) for s in ordered]


async def _spoolman_label_data(spool_ids: list[int], request: Request, db: AsyncSession) -> list[LabelData]:
    """Fetches the full spool list once and filters in-memory, rather than one
    request per id. Archived spools included: Spoolman leaves them out of its
    plain list, and the Inventory page can show and select them (SM-C12).
    """
    spoolman_on = (await get_setting(db, "spoolman_enabled") or "").lower() == "true"
    if not spoolman_on:
        raise HTTPException(400, "Spoolman integration is not enabled")

    client = await get_spoolman_client()
    if client is None or not client.is_connected:
        raise HTTPException(503, "Spoolman not reachable")

    try:
        all_spools = await client.get_all_spools(allow_archived=True)
    except Exception as exc:
        logger.warning("Spoolman fetch failed during label render: %s", exc)
        raise HTTPException(502, "Failed to fetch spools from Spoolman") from exc

    by_id = {int(s.get("id", 0)): s for s in all_spools if s.get("id") is not None}
    missing = [sid for sid in spool_ids if sid not in by_id]
    if missing:
        raise HTTPException(404, f"Spool(s) not found in Spoolman: {missing}")

    deeplink_base = await resolve_deeplink_base(request, db)
    return [_spoolman_dict_to_label_data(by_id[sid], deeplink_base) for sid in spool_ids]


def _enqueue_print(body: LabelPrintRequest, data_list: list[LabelData], config: LabelPrinterConfig) -> dict:
    """Queue the print behind the printer's earlier jobs and return its job id.
    Rendering, rasterising and delivering all happen in the background."""
    roll = body.resolved_template()
    assert isinstance(roll, RollLayout)  # _printer_for refused everything else
    driver = get_driver(config.type)
    fields = body.field_set()

    def rasterise(pdf: bytes) -> PreparedJob:
        # One page at a time: the driver takes the images lazily and drops each
        # one once it is rasterised, so memory stays at about a single label.
        images = iter_pdf_images(pdf, PRINT_DPI, grayscale=True)
        pages = images if roll.across_tape else (im.transpose(TAPE_ROTATION) for im in images)
        return driver.prepare(pages, config)

    async def work(job: PrintJob) -> None:
        # CPU work first, outside the printer's time limit and lock. The PDF is
        # built in the one PDF worker, like every label PDF (shared fonts).
        # Monochrome always: the printer has no grey, so the swatch would print as a block.
        pdf = await render_pdf_in_worker(render_labels, roll, data_list, monochrome=True, fields=fields)
        prepared = await asyncio.to_thread(rasterise, pdf)

        def confirmed(count: int) -> None:
            job.printed = min(count, job.total)  # the printer's own count, from the worker thread

        await deliver_job(driver, prepared, config, on_progress=confirmed)

    try:
        job = label_print_service.submit(
            (config.host, config.port),
            len(data_list),
            work,
            lambda exc: describe_error(exc, config)[1],
        )
    except PrintQueueFull as exc:
        raise HTTPException(503, QUEUE_FULL_MESSAGE) from exc
    return {"job_id": job.id, "total": job.total}


async def _printer_for(body: LabelPrintRequest, db: AsyncSession) -> LabelPrinterConfig:
    """The configured label printer, once the request can be printed on it."""
    config = await load_label_printer_config(db)
    if config is None:
        raise HTTPException(400, "No label printer is set up")
    template = body.resolved_template()
    if not isinstance(template, RollLayout):
        if get_sheet_capacity(template) is not None:
            raise HTTPException(400, "Sheet templates can't be printed on a label printer")
        raise HTTPException(400, "Choose the roll format to print on a label printer")
    if template.tape != config.tape:
        raise HTTPException(
            400, f"The label printer is set up for {config.tape} mm tape, but the labels are for {template.tape} mm"
        )
    tape_mm = len(body.spool_ids) * template.length_mm
    if tape_mm > MAX_PRINT_TAPE_MM:
        raise HTTPException(
            422,
            f"That is {tape_mm:g} mm of tape; at most {MAX_PRINT_TAPE_MM} mm can be printed at once. "
            "Print fewer labels or shorter ones.",
        )
    return config


@router.post("/inventory/labels")
async def render_local_inventory_labels(
    body: LabelRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> Response:
    """Render labels for spools in the local inventory."""
    data_list = await _local_label_data(body.spool_ids, request, db)
    return await _render_response(body, data_list, f"bambuddy-labels-{body.template}")


@router.post("/inventory/labels/preview")
async def preview_local_inventory_label(
    body: LabelPreviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> Response:
    """One label for a local spool, as a PNG, for the picker's preview."""
    data_list = await _local_label_data([body.spool_id], request, db)
    return await _render_preview(body, data_list[0])


@router.post("/spoolman/labels")
async def render_spoolman_labels(
    body: LabelRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> Response:
    """Render labels for spools tracked in Spoolman."""
    data_list = await _spoolman_label_data(body.spool_ids, request, db)
    return await _render_response(body, data_list, f"bambuddy-labels-spoolman-{body.template}")


@router.post("/spoolman/labels/preview")
async def preview_spoolman_label(
    body: LabelPreviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> Response:
    """One label for a Spoolman spool, as a PNG, for the picker's preview."""
    data_list = await _spoolman_label_data([body.spool_id], request, db)
    return await _render_preview(body, data_list[0])


@router.post("/inventory/labels/print", status_code=202)
async def print_local_inventory_labels(
    body: LabelPrintRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Queue labels for local spools on the configured label printer (#3612); poll
    ``GET /labels/print-jobs/{job_id}`` for the outcome. Needs ``inventory:update``,
    not the download's ``inventory:read``: a print uses up tape."""
    config = await _printer_for(body, db)
    data_list = await _local_label_data(body.spool_ids, request, db)
    return _enqueue_print(body, data_list, config)


@router.post("/spoolman/labels/print", status_code=202)
async def print_spoolman_labels(
    body: LabelPrintRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_UPDATE),
) -> dict:
    """Queue labels for Spoolman spools on the configured label printer (#3612).
    Needs ``inventory:update``, like the local route."""
    config = await _printer_for(body, db)
    data_list = await _spoolman_label_data(body.spool_ids, request, db)
    return _enqueue_print(body, data_list, config)


@router.get("/labels/print-jobs/{job_id}")
async def get_label_print_job(
    job_id: str,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
) -> dict:
    """State of a queued label print job (#3612). Jobs are kept for 15 minutes after they finish."""
    job = label_print_service.store.get(job_id)
    if job is None:
        raise HTTPException(404, "Print job not found or expired")
    return job.to_dict()
