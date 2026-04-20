"""Macro system API routes."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.macro import Macro, MacroRun
from backend.app.schemas.macro import (
    MacroCreate,
    MacroResponse,
    MacroRunResponse,
    MacroUpdate,
    RunMacroRequest,
)
from backend.app.services import macro_files
from backend.app.services.gcode_whitelist import GCODE_WHITELIST
from backend.app.services.macro_runner import macro_runner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/macros", tags=["macros"])


def _macro_to_response(macro: Macro) -> MacroResponse:
    try:
        script = macro_files.read(macro.file_path)
    except FileNotFoundError:
        script = ""
    return MacroResponse(
        id=macro.id,
        name=macro.name,
        description=macro.description,
        script=script,
        file_path=macro.file_path,
        trigger_type=macro.trigger_type,
        cron_expression=macro.cron_expression,
        printer_id=macro.printer_id,
        created_at=macro.created_at,
        updated_at=macro.updated_at,
    )


# NOTE: These routes MUST be declared before /{macro_id} to avoid FastAPI
# treating literal path segments as macro_id values.


@router.get("/gcode-whitelist", response_model=list[str])
async def get_gcode_whitelist(
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_READ),
):
    return sorted(GCODE_WHITELIST)


@router.get("/runs/{run_id}", response_model=MacroRunResponse)
async def get_run(
    run_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_READ),
    db: AsyncSession = Depends(get_db),
):
    run = await db.get(MacroRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_RUN),
    db: AsyncSession = Depends(get_db),
):
    run = await db.get(MacroRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status not in ("pending", "running"):
        raise HTTPException(409, "Run is not active")
    cancelled = macro_runner.cancel_run(run_id)
    if not cancelled:
        # Task already finished between check and cancel; mark error anyway
        run.status = "error"
        run.log = (run.log or "") + "[CANCELLED] Cancelled via API (task already done)\n"
        from datetime import datetime, timezone

        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
    return {"ok": True, "cancelled": cancelled}


@router.get("", response_model=list[MacroResponse])
async def list_macros(
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_READ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Macro).order_by(Macro.name))
    return [_macro_to_response(m) for m in result.scalars()]


@router.post("", response_model=MacroResponse)
async def create_macro(
    data: MacroCreate,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_CREATE),
    db: AsyncSession = Depends(get_db),
):
    if data.trigger_type == "schedule":
        _validate_cron(data.cron_expression)

    # Check name uniqueness
    existing = await db.execute(select(Macro).where(Macro.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Macro named '{data.name}' already exists")

    file_path = macro_files.write(data.name, data.script)
    macro = Macro(
        name=data.name,
        description=data.description,
        file_path=file_path,
        trigger_type=data.trigger_type,
        cron_expression=data.cron_expression,
        printer_id=data.printer_id,
    )
    db.add(macro)
    await db.flush()
    await db.refresh(macro)
    await db.commit()
    return _macro_to_response(macro)


@router.get("/{macro_id}", response_model=MacroResponse)
async def get_macro(
    macro_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_READ),
    db: AsyncSession = Depends(get_db),
):
    macro = await db.get(Macro, macro_id)
    if not macro:
        raise HTTPException(404, "Macro not found")
    return _macro_to_response(macro)


@router.put("/{macro_id}", response_model=MacroResponse)
async def update_macro(
    macro_id: int,
    data: MacroUpdate,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    macro = await db.get(Macro, macro_id)
    if not macro:
        raise HTTPException(404, "Macro not found")

    if data.trigger_type == "schedule" or (data.trigger_type is None and macro.trigger_type == "schedule"):
        _validate_cron(data.cron_expression or macro.cron_expression)

    if data.name is not None and data.name != macro.name:
        existing = await db.execute(select(Macro).where(Macro.name == data.name))
        if existing.scalar_one_or_none():
            raise HTTPException(409, f"Macro named '{data.name}' already exists")
        macro.name = data.name

    if data.script is not None:
        macro_files.write(data.name or macro.name, data.script, existing_path=macro.file_path)

    if data.description is not None:
        macro.description = data.description
    if data.trigger_type is not None:
        macro.trigger_type = data.trigger_type
    if data.cron_expression is not None:
        macro.cron_expression = data.cron_expression
    if data.printer_id is not None:
        macro.printer_id = data.printer_id

    await db.commit()
    await db.refresh(macro)
    return _macro_to_response(macro)


@router.delete("/{macro_id}")
async def delete_macro(
    macro_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_DELETE),
    db: AsyncSession = Depends(get_db),
):
    macro = await db.get(Macro, macro_id)
    if not macro:
        raise HTTPException(404, "Macro not found")
    macro_files.delete(macro.file_path)
    await db.delete(macro)
    await db.commit()
    return {"ok": True}


@router.post("/{macro_id}/run", response_model=MacroRunResponse)
async def run_macro(
    macro_id: int,
    body: RunMacroRequest,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_RUN),
    db: AsyncSession = Depends(get_db),
):
    macro = await db.get(Macro, macro_id)
    if not macro:
        raise HTTPException(404, "Macro not found")

    printer_id = body.printer_id if body.printer_id is not None else macro.printer_id
    run = MacroRun(
        macro_id=macro_id,
        printer_id=printer_id,
        status="pending",
        trigger="manual",
    )
    db.add(run)
    await db.flush()
    run_id = run.id
    await db.commit()

    # Launch background; pass run_id so the task reuses the record we just created
    asyncio.create_task(macro_runner.run_macro(macro_id, printer_id, "manual", run_id=run_id))

    run = await db.get(MacroRun, run_id)
    return run


@router.get("/{macro_id}/runs", response_model=list[MacroRunResponse])
async def list_runs(
    macro_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_READ),
    db: AsyncSession = Depends(get_db),
):
    macro = await db.get(Macro, macro_id)
    if not macro:
        raise HTTPException(404, "Macro not found")
    result = await db.execute(
        select(MacroRun).where(MacroRun.macro_id == macro_id).order_by(MacroRun.started_at.desc()).limit(50)
    )
    return result.scalars().all()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _validate_cron(expr: str | None) -> None:
    if not expr:
        raise HTTPException(422, "cron_expression is required for schedule trigger")
    try:
        from croniter import croniter

        if not croniter.is_valid(expr):
            raise HTTPException(422, f"Invalid cron expression: {expr}")
    except ImportError:
        pass  # croniter not installed; skip validation
