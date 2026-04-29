"""Macro system API routes."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.macro import Macro, MacroCfgFile, MacroRun
from backend.app.schemas.macro import (
    ExecLineRequest,
    ExecLineResponse,
    FunctionSpecResponse,
    MacroCfgFileCreate,
    MacroCfgFileResponse,
    MacroCfgFileSave,
    MacroResponse,
    MacroRunResponse,
    RunMacroRequest,
)
from backend.app.services import macro_files
from backend.app.services.gcode_whitelist import GCODE_WHITELIST
from backend.app.services.macro_cfg_watcher import delete_file_from_db, sync_file
from backend.app.services.macro_runner import macro_runner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/macros", tags=["macros"])


# ── Static / utility routes (must come before /{macro_id}) ────────────────────


@router.get("/gcode-whitelist", response_model=list[str])
async def get_gcode_whitelist(
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_READ),
):
    return sorted(GCODE_WHITELIST)


@router.get("/functions", response_model=list[FunctionSpecResponse])
async def get_function_catalogue(
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_READ),
):
    from backend.app.services.macro_functions import all_specs

    return [
        FunctionSpecResponse(
            name=s.name,
            description=s.description,
            args={
                k: {"description": v.description, "required": v.required, "default": v.default}
                for k, v in s.args.items()
            },
            context_var=s.context_var,
            requires_printer=s.requires_printer,
            allowed_in_embed=s.allowed_in_embed,
        )
        for s in all_specs()
    ]


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
        run.status = "error"
        run.log = (run.log or "") + "[CANCELLED] Cancelled via API (task already done)\n"
        from datetime import datetime, timezone

        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
    return {"ok": True, "cancelled": cancelled}


@router.post("/exec", response_model=ExecLineResponse)
async def exec_line(
    body: ExecLineRequest,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_RUN),
    db: AsyncSession = Depends(get_db),
):
    from backend.app.schemas.macro import HMSErrorInfo as HMSErrorInfoSchema

    line = body.line.strip()
    if not line:
        raise HTTPException(422, "line must not be empty")

    # Check if line matches a macro name (allows running macros by name from terminal)
    token = line.split()[0]
    macro_result = await db.execute(select(Macro).where(Macro.name == token))
    macro = macro_result.scalar_one_or_none()
    if macro is None:
        from sqlalchemy import func as sa_func

        macro_result = await db.execute(select(Macro).where(sa_func.lower(Macro.name) == token.lower()))
        macro = macro_result.scalar_one_or_none()

    if macro is not None:
        printer_id = body.printer_id if body.printer_id is not None else macro.printer_id
        run = MacroRun(
            macro_id=macro.id,
            printer_id=printer_id,
            status="pending",
            trigger="terminal",
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()
        asyncio.create_task(macro_runner.run_macro(macro.id, printer_id, "terminal", run_id=run_id))
        return ExecLineResponse(
            status="success",
            log=f"[MACRO] Running macro '{macro.name}' (run #{run_id})\n",
            run_id=run_id,
        )

    result = await macro_runner.exec_line(line, body.printer_id)
    return ExecLineResponse(
        status="success" if result.ok else "error",
        log=result.log,
        hms_errors=[
            HMSErrorInfoSchema(code=e.code, severity=e.severity, message=e.message) for e in result.new_hms_errors
        ],
        printer_state=result.printer_state,
    )


# ── Cfg file CRUD ─────────────────────────────────────────────────────────────


@router.get("/cfg-files", response_model=list[MacroCfgFileResponse])
async def list_cfg_files(
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_READ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(MacroCfgFile).order_by(MacroCfgFile.name))
    return result.scalars().all()


@router.post("/cfg-files", response_model=MacroCfgFileResponse)
async def create_cfg_file(
    data: MacroCfgFileCreate,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_CREATE),
):
    relative_path = macro_files.create(data.name, data.content)
    cfg_file = await sync_file(relative_path)
    return cfg_file


@router.get("/cfg-files/{file_id}", response_model=MacroCfgFileResponse)
async def get_cfg_file(
    file_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_READ),
    db: AsyncSession = Depends(get_db),
):
    cfg_file = await db.get(MacroCfgFile, file_id)
    if not cfg_file:
        raise HTTPException(404, "Cfg file not found")
    return cfg_file


@router.get("/cfg-files/{file_id}/content")
async def get_cfg_file_content(
    file_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_READ),
    db: AsyncSession = Depends(get_db),
):
    cfg_file = await db.get(MacroCfgFile, file_id)
    if not cfg_file:
        raise HTTPException(404, "Cfg file not found")
    try:
        content = macro_files.read(cfg_file.file_path)
    except FileNotFoundError:
        content = ""
    return {"content": content}


@router.put("/cfg-files/{file_id}", response_model=MacroCfgFileResponse)
async def save_cfg_file(
    file_id: int,
    data: MacroCfgFileSave,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    cfg_file = await db.get(MacroCfgFile, file_id)
    if not cfg_file:
        raise HTTPException(404, "Cfg file not found")
    macro_files.write(cfg_file.file_path, data.content)
    updated = await sync_file(cfg_file.file_path)
    return updated


@router.delete("/cfg-files/{file_id}")
async def delete_cfg_file(
    file_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_DELETE),
    db: AsyncSession = Depends(get_db),
):
    cfg_file = await db.get(MacroCfgFile, file_id)
    if not cfg_file:
        raise HTTPException(404, "Cfg file not found")
    relative_path = cfg_file.file_path
    macro_files.delete(relative_path)
    await delete_file_from_db(relative_path)
    return {"ok": True}


# ── Macro routes ──────────────────────────────────────────────────────────────


@router.get("", response_model=list[MacroResponse])
async def list_macros(
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_READ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Macro).order_by(Macro.name))
    return result.scalars().all()


@router.get("/{macro_id}", response_model=MacroResponse)
async def get_macro(
    macro_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.MACROS_READ),
    db: AsyncSession = Depends(get_db),
):
    macro = await db.get(Macro, macro_id)
    if not macro:
        raise HTTPException(404, "Macro not found")
    return macro


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
