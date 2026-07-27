"""API routes for Slicer Pipelines (#1425, PR A — definitions only).

A pipeline bundles printer / process / filament(s) / bed-type picks so the
SliceModal can apply them in one click. PR A surfaces only CRUD + an
``apply`` helper that returns the pipeline as the four ``PresetRef`` slots a
``SliceRequest`` expects. PR B adds single-target dispatch; PR C adds
multi-copy fanout and the run dashboard.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.slicer_pipeline import SlicerPipeline
from backend.app.models.user import User
from backend.app.schemas.slicer import PresetRef
from backend.app.schemas.slicer_pipeline import (
    SlicerPipelineCreate,
    SlicerPipelineListResponse,
    SlicerPipelineResponse,
    SlicerPipelineUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slicer-pipelines", tags=["Slicer Pipelines"])


def _to_response(row: SlicerPipeline) -> SlicerPipelineResponse:
    """Materialise the JSON filament list back into PresetRef objects so the
    response shape matches the create/update input shape exactly."""
    try:
        raw = json.loads(row.filament_presets_json) if row.filament_presets_json else []
    except (json.JSONDecodeError, TypeError):
        # Row was hand-edited or corrupted — return an empty list rather than
        # 500ing on a list endpoint. Edit/run paths will surface the problem.
        logger.warning("slicer_pipeline %d has invalid filament_presets_json", row.id)
        raw = []
    filament_presets = [PresetRef(**f) for f in raw if isinstance(f, dict)]

    return SlicerPipelineResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        printer_preset=PresetRef(source=row.printer_preset_source, id=row.printer_preset_id),
        process_preset=PresetRef(source=row.process_preset_source, id=row.process_preset_id),
        filament_presets=filament_presets,
        bed_type=row.bed_type,
        target_kind=row.target_kind,  # type: ignore[arg-type]
        target_printer_id=row.target_printer_id,
        target_model_class=row.target_model_class,
        fanout_strategy=row.fanout_strategy,  # type: ignore[arg-type]
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/", response_model=SlicerPipelineListResponse)
async def list_pipelines(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PIPELINES_READ),
    db: AsyncSession = Depends(get_db),
):
    """List all pipelines, newest first. Soft-deleted rows are hidden."""
    result = await db.execute(
        select(SlicerPipeline).where(SlicerPipeline.is_deleted.is_(False)).order_by(SlicerPipeline.id.desc())
    )
    rows = result.scalars().all()
    return SlicerPipelineListResponse(pipelines=[_to_response(r) for r in rows])


@router.post("/", response_model=SlicerPipelineResponse, status_code=201)
async def create_pipeline(
    data: SlicerPipelineCreate,
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.PIPELINES_WRITE),
    db: AsyncSession = Depends(get_db),
):
    """Create a new pipeline."""
    row = SlicerPipeline(
        name=data.name.strip(),
        description=data.description,
        printer_preset_source=data.printer_preset.source,
        printer_preset_id=data.printer_preset.id,
        process_preset_source=data.process_preset.source,
        process_preset_id=data.process_preset.id,
        filament_presets_json=json.dumps([f.model_dump() for f in data.filament_presets]),
        bed_type=data.bed_type,
        created_by=current_user.id if current_user else None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_response(row)


@router.get("/{pipeline_id}", response_model=SlicerPipelineResponse)
async def get_pipeline(
    pipeline_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PIPELINES_READ),
    db: AsyncSession = Depends(get_db),
):
    """Read one pipeline by id."""
    result = await db.execute(
        select(SlicerPipeline).where(
            SlicerPipeline.id == pipeline_id,
            SlicerPipeline.is_deleted.is_(False),
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Pipeline not found")
    return _to_response(row)


@router.put("/{pipeline_id}", response_model=SlicerPipelineResponse)
async def update_pipeline(
    pipeline_id: int,
    data: SlicerPipelineUpdate,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PIPELINES_WRITE),
    db: AsyncSession = Depends(get_db),
):
    """Update a pipeline. Only fields present in the payload are written."""
    result = await db.execute(
        select(SlicerPipeline).where(
            SlicerPipeline.id == pipeline_id,
            SlicerPipeline.is_deleted.is_(False),
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Pipeline not found")

    if data.name is not None:
        row.name = data.name.strip()
    if data.description is not None:
        row.description = data.description
    if data.printer_preset is not None:
        row.printer_preset_source = data.printer_preset.source
        row.printer_preset_id = data.printer_preset.id
    if data.process_preset is not None:
        row.process_preset_source = data.process_preset.source
        row.process_preset_id = data.process_preset.id
    if data.filament_presets is not None:
        row.filament_presets_json = json.dumps([f.model_dump() for f in data.filament_presets])
    if data.bed_type is not None:
        row.bed_type = data.bed_type

    # PR B target binding. The schema accepts ``target_kind=specific_printer``
    # without ``target_printer_id`` (operator may be saving the kind first),
    # but a 'specific_printer' kind with a printer_id of 0 is rejected since
    # printer ids are always positive — guard against the JSON-coerced
    # empty-string case from the frontend.
    if data.target_kind is not None:
        row.target_kind = data.target_kind
    if data.target_printer_id is not None:
        # ``target_printer_id=0`` from the frontend means "clear the target"
        # (the <option value=""> case). Anything positive must reference an
        # actual printer row.
        if data.target_printer_id == 0:
            row.target_printer_id = None
        else:
            row.target_printer_id = data.target_printer_id
    # PR C — class targeting + fanout strategy. Empty string from the frontend
    # also clears the class (radio toggled away).
    if data.target_model_class is not None:
        row.target_model_class = data.target_model_class or None
    if data.fanout_strategy is not None:
        row.fanout_strategy = data.fanout_strategy

    await db.commit()
    await db.refresh(row)
    return _to_response(row)


@router.delete("/{pipeline_id}", status_code=204)
async def delete_pipeline(
    pipeline_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PIPELINES_WRITE),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a pipeline (sets is_deleted=True so PR B+ run history can
    still resolve pipeline metadata)."""
    result = await db.execute(
        select(SlicerPipeline).where(
            SlicerPipeline.id == pipeline_id,
            SlicerPipeline.is_deleted.is_(False),
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Pipeline not found")
    row.is_deleted = True
    await db.commit()
