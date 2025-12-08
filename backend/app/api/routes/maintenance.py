"""Maintenance tracking API routes."""

import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.database import get_db
from backend.app.models.maintenance import MaintenanceType, PrinterMaintenance, MaintenanceHistory
from backend.app.models.printer import Printer
from backend.app.models.archive import PrintArchive
from backend.app.services.notification_service import notification_service
from backend.app.schemas.maintenance import (
    MaintenanceTypeCreate,
    MaintenanceTypeUpdate,
    MaintenanceTypeResponse,
    PrinterMaintenanceCreate,
    PrinterMaintenanceUpdate,
    PrinterMaintenanceResponse,
    MaintenanceHistoryResponse,
    MaintenanceStatus,
    PrinterMaintenanceOverview,
    PerformMaintenanceRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maintenance", tags=["maintenance"])

# Default maintenance types
DEFAULT_MAINTENANCE_TYPES = [
    {
        "name": "Lubricate Linear Rails",
        "description": "Apply lubricant to linear rails and rods for smooth motion",
        "default_interval_hours": 50.0,
        "icon": "Droplet",
    },
    {
        "name": "Clean Nozzle/Hotend",
        "description": "Clean nozzle exterior and perform cold pull if needed",
        "default_interval_hours": 100.0,
        "icon": "Flame",
    },
    {
        "name": "Check Belt Tension",
        "description": "Verify and adjust belt tension for X/Y axes",
        "default_interval_hours": 200.0,
        "icon": "Ruler",
    },
    {
        "name": "Clean Carbon Rods",
        "description": "Wipe carbon rods with a dry cloth",
        "default_interval_hours": 100.0,
        "icon": "Sparkles",
    },
    {
        "name": "Clean Build Plate",
        "description": "Deep clean build plate with IPA or soap",
        "default_interval_hours": 25.0,
        "icon": "Square",
    },
    {
        "name": "Check PTFE Tube",
        "description": "Inspect PTFE tube for wear or discoloration",
        "default_interval_hours": 500.0,
        "icon": "Cable",
    },
]


async def get_printer_total_hours(db: AsyncSession, printer_id: int) -> float:
    """Calculate total print hours for a printer from archives plus offset."""
    # Get archive hours
    result = await db.execute(
        select(func.sum(PrintArchive.print_time_seconds))
        .where(PrintArchive.printer_id == printer_id)
        .where(PrintArchive.status == "completed")
    )
    total_seconds = result.scalar() or 0
    archive_hours = total_seconds / 3600.0

    # Get printer offset
    result = await db.execute(
        select(Printer.print_hours_offset).where(Printer.id == printer_id)
    )
    offset = result.scalar() or 0.0

    return archive_hours + offset


async def ensure_default_types(db: AsyncSession) -> None:
    """Ensure default maintenance types exist."""
    result = await db.execute(
        select(MaintenanceType).where(MaintenanceType.is_system == True)
    )
    existing = result.scalars().all()
    existing_names = {t.name for t in existing}

    for type_def in DEFAULT_MAINTENANCE_TYPES:
        if type_def["name"] not in existing_names:
            new_type = MaintenanceType(
                name=type_def["name"],
                description=type_def["description"],
                default_interval_hours=type_def["default_interval_hours"],
                icon=type_def["icon"],
                is_system=True,
            )
            db.add(new_type)

    await db.commit()


# ============== Maintenance Types ==============

@router.get("/types", response_model=List[MaintenanceTypeResponse])
async def get_maintenance_types(db: AsyncSession = Depends(get_db)):
    """Get all maintenance types."""
    await ensure_default_types(db)
    result = await db.execute(
        select(MaintenanceType).order_by(MaintenanceType.is_system.desc(), MaintenanceType.name)
    )
    return result.scalars().all()


@router.post("/types", response_model=MaintenanceTypeResponse)
async def create_maintenance_type(
    data: MaintenanceTypeCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a custom maintenance type."""
    new_type = MaintenanceType(
        name=data.name,
        description=data.description,
        default_interval_hours=data.default_interval_hours,
        interval_type=data.interval_type,
        icon=data.icon,
        is_system=False,
    )
    db.add(new_type)
    await db.commit()
    await db.refresh(new_type)
    return new_type


@router.patch("/types/{type_id}", response_model=MaintenanceTypeResponse)
async def update_maintenance_type(
    type_id: int,
    data: MaintenanceTypeUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a maintenance type."""
    result = await db.execute(
        select(MaintenanceType).where(MaintenanceType.id == type_id)
    )
    maint_type = result.scalar_one_or_none()
    if not maint_type:
        raise HTTPException(status_code=404, detail="Maintenance type not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(maint_type, key, value)

    await db.commit()
    await db.refresh(maint_type)
    return maint_type


@router.delete("/types/{type_id}")
async def delete_maintenance_type(
    type_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a custom maintenance type."""
    result = await db.execute(
        select(MaintenanceType).where(MaintenanceType.id == type_id)
    )
    maint_type = result.scalar_one_or_none()
    if not maint_type:
        raise HTTPException(status_code=404, detail="Maintenance type not found")

    if maint_type.is_system:
        raise HTTPException(status_code=400, detail="Cannot delete system maintenance type")

    await db.delete(maint_type)
    await db.commit()
    return {"status": "deleted"}


# ============== Printer Maintenance ==============

async def _get_printer_maintenance_internal(
    printer_id: int,
    db: AsyncSession,
    commit: bool = True,
) -> PrinterMaintenanceOverview:
    """Internal helper to get maintenance overview for a specific printer."""
    await ensure_default_types(db)

    # Get printer
    result = await db.execute(
        select(Printer).where(Printer.id == printer_id)
    )
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    total_hours = await get_printer_total_hours(db, printer_id)

    # Get all maintenance types
    result = await db.execute(select(MaintenanceType))
    all_types = result.scalars().all()

    # Get printer's maintenance items
    result = await db.execute(
        select(PrinterMaintenance)
        .where(PrinterMaintenance.printer_id == printer_id)
        .options(selectinload(PrinterMaintenance.maintenance_type))
    )
    existing_items = {item.maintenance_type_id: item for item in result.scalars().all()}

    maintenance_items = []
    due_count = 0
    warning_count = 0

    now = datetime.utcnow()

    for maint_type in all_types:
        item = existing_items.get(maint_type.id)
        default_interval_type = getattr(maint_type, 'interval_type', 'hours') or 'hours'

        if item:
            interval = item.custom_interval_hours or maint_type.default_interval_hours
            # Use custom interval type if set, otherwise use type's default
            interval_type = getattr(item, 'custom_interval_type', None) or default_interval_type
            enabled = item.enabled
            last_performed_hours = item.last_performed_hours
            last_performed_at = item.last_performed_at
            item_id = item.id
        else:
            # Create default entry for this printer/type
            item = PrinterMaintenance(
                printer_id=printer_id,
                maintenance_type_id=maint_type.id,
                enabled=True,
                last_performed_hours=0.0,
            )
            db.add(item)
            await db.flush()

            interval = maint_type.default_interval_hours
            interval_type = default_interval_type
            enabled = True
            last_performed_hours = 0.0
            last_performed_at = None
            item_id = item.id

        # Calculate status based on interval type
        if interval_type == "days":
            # Time-based: calculate days since last performed
            if last_performed_at:
                days_since = (now - last_performed_at).total_seconds() / 86400.0
            else:
                # Never performed - consider it due
                days_since = interval + 1

            days_until = interval - days_since
            is_due = days_until <= 0
            is_warning = days_until <= (interval * 0.1) and not is_due

            # For compatibility, also set hours values (but they won't be primary)
            hours_since = total_hours - last_performed_hours
            hours_until = 0  # Not applicable for time-based
        else:
            # Print-hours based (default)
            hours_since = total_hours - last_performed_hours
            hours_until = interval - hours_since
            is_due = hours_until <= 0
            is_warning = hours_until <= (interval * 0.1) and not is_due

            # Calculate days for reference
            if last_performed_at:
                days_since = (now - last_performed_at).total_seconds() / 86400.0
            else:
                days_since = None
            days_until = None

        if enabled:
            if is_due:
                due_count += 1
            elif is_warning:
                warning_count += 1

        maintenance_items.append(MaintenanceStatus(
            id=item_id,
            printer_id=printer_id,
            printer_name=printer.name,
            maintenance_type_id=maint_type.id,
            maintenance_type_name=maint_type.name,
            maintenance_type_icon=maint_type.icon,
            enabled=enabled,
            interval_hours=interval,
            interval_type=interval_type,
            current_hours=total_hours,
            hours_since_maintenance=hours_since,
            hours_until_due=hours_until,
            days_since_maintenance=days_since if interval_type == "days" else None,
            days_until_due=days_until if interval_type == "days" else None,
            is_due=is_due,
            is_warning=is_warning,
            last_performed_at=last_performed_at,
        ))

    if commit:
        await db.commit()

    return PrinterMaintenanceOverview(
        printer_id=printer_id,
        printer_name=printer.name,
        total_print_hours=total_hours,
        maintenance_items=maintenance_items,
        due_count=due_count,
        warning_count=warning_count,
    )


@router.get("/printers/{printer_id}", response_model=PrinterMaintenanceOverview)
async def get_printer_maintenance(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get maintenance overview for a specific printer."""
    return await _get_printer_maintenance_internal(printer_id, db, commit=True)


@router.get("/overview", response_model=List[PrinterMaintenanceOverview])
async def get_all_maintenance_overview(db: AsyncSession = Depends(get_db)):
    """Get maintenance overview for all active printers."""
    await ensure_default_types(db)

    result = await db.execute(
        select(Printer).where(Printer.is_active == True)
    )
    printers = result.scalars().all()

    overviews = []
    for printer in printers:
        # Don't commit after each printer, commit once at the end
        overview = await _get_printer_maintenance_internal(printer.id, db, commit=False)
        overviews.append(overview)

    # Commit any new maintenance items created
    await db.commit()

    return overviews


@router.patch("/items/{item_id}", response_model=PrinterMaintenanceResponse)
async def update_printer_maintenance(
    item_id: int,
    data: PrinterMaintenanceUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a printer maintenance item (e.g., custom interval, enabled)."""
    result = await db.execute(
        select(PrinterMaintenance)
        .where(PrinterMaintenance.id == item_id)
        .options(selectinload(PrinterMaintenance.maintenance_type))
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Maintenance item not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(item, key, value)

    await db.commit()
    await db.refresh(item)
    return item


@router.post("/items/{item_id}/perform", response_model=MaintenanceStatus)
async def perform_maintenance(
    item_id: int,
    data: PerformMaintenanceRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mark maintenance as performed (reset the counter)."""
    result = await db.execute(
        select(PrinterMaintenance)
        .where(PrinterMaintenance.id == item_id)
        .options(selectinload(PrinterMaintenance.maintenance_type))
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Maintenance item not found")

    # Get printer for name
    result = await db.execute(
        select(Printer).where(Printer.id == item.printer_id)
    )
    printer = result.scalar_one()

    # Get current hours
    current_hours = await get_printer_total_hours(db, item.printer_id)

    # Create history entry
    history = MaintenanceHistory(
        printer_maintenance_id=item.id,
        hours_at_maintenance=current_hours,
        notes=data.notes,
    )
    db.add(history)

    # Update item
    item.last_performed_at = datetime.utcnow()
    item.last_performed_hours = current_hours

    await db.commit()

    # Calculate status
    interval = item.custom_interval_hours or item.maintenance_type.default_interval_hours
    interval_type = getattr(item.maintenance_type, 'interval_type', 'hours') or 'hours'
    hours_since = current_hours - item.last_performed_hours
    hours_until = interval - hours_since

    return MaintenanceStatus(
        id=item.id,
        printer_id=item.printer_id,
        printer_name=printer.name,
        maintenance_type_id=item.maintenance_type_id,
        maintenance_type_name=item.maintenance_type.name,
        maintenance_type_icon=item.maintenance_type.icon,
        enabled=item.enabled,
        interval_hours=interval,
        interval_type=interval_type,
        current_hours=current_hours,
        hours_since_maintenance=hours_since,
        hours_until_due=hours_until if interval_type == "hours" else 0,
        days_since_maintenance=0 if interval_type == "days" else None,
        days_until_due=interval if interval_type == "days" else None,
        is_due=False,
        is_warning=False,
        last_performed_at=item.last_performed_at,
    )


@router.get("/items/{item_id}/history", response_model=List[MaintenanceHistoryResponse])
async def get_maintenance_history(
    item_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get maintenance history for a specific item."""
    result = await db.execute(
        select(MaintenanceHistory)
        .where(MaintenanceHistory.printer_maintenance_id == item_id)
        .order_by(MaintenanceHistory.performed_at.desc())
    )
    return result.scalars().all()


@router.get("/summary")
async def get_maintenance_summary(db: AsyncSession = Depends(get_db)):
    """Get a summary of maintenance status across all printers."""
    await ensure_default_types(db)

    result = await db.execute(
        select(Printer).where(Printer.is_active == True)
    )
    printers = result.scalars().all()

    total_due = 0
    total_warning = 0
    printers_with_issues = []

    for printer in printers:
        overview = await get_printer_maintenance(printer.id, db)
        total_due += overview.due_count
        total_warning += overview.warning_count
        if overview.due_count > 0 or overview.warning_count > 0:
            printers_with_issues.append({
                "printer_id": printer.id,
                "printer_name": printer.name,
                "due_count": overview.due_count,
                "warning_count": overview.warning_count,
            })

    return {
        "total_due": total_due,
        "total_warning": total_warning,
        "printers_with_issues": printers_with_issues,
    }


@router.patch("/printers/{printer_id}/hours")
async def set_printer_hours(
    printer_id: int,
    total_hours: float,
    db: AsyncSession = Depends(get_db),
):
    """Set the total print hours for a printer (adjusts offset to match)."""
    # Get printer
    result = await db.execute(
        select(Printer).where(Printer.id == printer_id)
    )
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    # Get current archive hours
    result = await db.execute(
        select(func.sum(PrintArchive.print_time_seconds))
        .where(PrintArchive.printer_id == printer_id)
        .where(PrintArchive.status == "completed")
    )
    total_seconds = result.scalar() or 0
    archive_hours = total_seconds / 3600.0

    # Calculate needed offset
    printer.print_hours_offset = max(0, total_hours - archive_hours)

    await db.commit()

    # Check for maintenance items that need attention and send notification
    try:
        await ensure_default_types(db)
        overview = await _get_printer_maintenance_internal(printer_id, db, commit=True)

        items_needing_attention = [
            {
                "name": item.maintenance_type_name,
                "is_due": item.is_due,
                "is_warning": item.is_warning,
            }
            for item in overview.maintenance_items
            if item.enabled and (item.is_due or item.is_warning)
        ]

        if items_needing_attention:
            await notification_service.on_maintenance_due(
                printer_id, printer.name, items_needing_attention, db
            )
            logger.info(
                f"Sent maintenance notification for printer {printer_id}: "
                f"{len(items_needing_attention)} items need attention"
            )
    except Exception as e:
        logger.warning(f"Failed to send maintenance notification: {e}")

    return {
        "printer_id": printer_id,
        "total_hours": total_hours,
        "archive_hours": archive_hours,
        "offset_hours": printer.print_hours_offset,
    }
