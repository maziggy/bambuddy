"""API routes for printer plate automation snippets."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.automation import Automation
from backend.app.models.user import User
from backend.app.schemas.automation import (
    AutomationCreate,
    AutomationResponse,
    AutomationUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/printers/{printer_id}/automation",
    response_model=list[AutomationResponse],
)
async def list_automation_for_printer(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
):
    """List automation rows for a printer."""
    # Check printer exists
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    result = await db.execute(select(Automation).where(Automation.printer_id == printer_id))
    return list(result.scalars().all())


@router.post(
    "/printers/{printer_id}/automation",
    response_model=AutomationResponse,
)
async def create_automation_for_printer(
    printer_id: int,
    data: AutomationCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
):
    """Create an automation row for a printer."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(404, "Printer not found")

    automation = Automation(**data.model_dump(), printer_id=printer_id)
    db.add(automation)
    await db.commit()
    await db.refresh(automation)
    return automation


@router.patch("/automation/{automation_id}", response_model=AutomationResponse)
async def update_automation(
    automation_id: int,
    data: AutomationUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
):
    """Update an existing automation row."""
    result = await db.execute(select(Automation).where(Automation.id == automation_id))
    automation = result.scalar_one_or_none()
    if not automation:
        raise HTTPException(404, "Automation not found")

    updates = data.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(automation, k, v)

    db.add(automation)
    await db.commit()
    await db.refresh(automation)
    return automation


@router.delete("/automation/{automation_id}", response_model=dict)
async def delete_automation(
    automation_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
):
    """Delete an automation row."""
    result = await db.execute(select(Automation).where(Automation.id == automation_id))
    automation = result.scalar_one_or_none()
    if not automation:
        raise HTTPException(404, "Automation not found")

    await db.delete(automation)
    await db.commit()
    return {"success": True}
