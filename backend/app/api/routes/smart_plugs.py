"""API routes for smart plug management."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.database import get_db
from backend.app.models.smart_plug import SmartPlug
from backend.app.models.printer import Printer
from backend.app.schemas.smart_plug import (
    SmartPlugCreate,
    SmartPlugUpdate,
    SmartPlugResponse,
    SmartPlugControl,
    SmartPlugStatus,
    SmartPlugTestConnection,
)
from backend.app.services.tasmota import tasmota_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/smart-plugs", tags=["smart-plugs"])


@router.get("/", response_model=list[SmartPlugResponse])
async def list_smart_plugs(db: AsyncSession = Depends(get_db)):
    """List all smart plugs."""
    result = await db.execute(select(SmartPlug).order_by(SmartPlug.name))
    return list(result.scalars().all())


@router.post("/", response_model=SmartPlugResponse)
async def create_smart_plug(
    data: SmartPlugCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new smart plug."""
    # Validate printer_id if provided
    if data.printer_id:
        result = await db.execute(
            select(Printer).where(Printer.id == data.printer_id)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(400, "Printer not found")

        # Check if printer already has a plug assigned
        result = await db.execute(
            select(SmartPlug).where(SmartPlug.printer_id == data.printer_id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(400, "This printer already has a smart plug assigned")

    plug = SmartPlug(**data.model_dump())
    db.add(plug)
    await db.commit()
    await db.refresh(plug)

    logger.info(f"Created smart plug '{plug.name}' at {plug.ip_address}")
    return plug


@router.get("/{plug_id}", response_model=SmartPlugResponse)
async def get_smart_plug(plug_id: int, db: AsyncSession = Depends(get_db)):
    """Get a specific smart plug."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug:
        raise HTTPException(404, "Smart plug not found")
    return plug


@router.patch("/{plug_id}", response_model=SmartPlugResponse)
async def update_smart_plug(
    plug_id: int,
    data: SmartPlugUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a smart plug."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug:
        raise HTTPException(404, "Smart plug not found")

    update_data = data.model_dump(exclude_unset=True)

    # Validate new printer_id if being changed
    if "printer_id" in update_data and update_data["printer_id"]:
        new_printer_id = update_data["printer_id"]

        # Check printer exists
        result = await db.execute(
            select(Printer).where(Printer.id == new_printer_id)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(400, "Printer not found")

        # Check if that printer already has a different plug assigned
        result = await db.execute(
            select(SmartPlug).where(
                SmartPlug.printer_id == new_printer_id,
                SmartPlug.id != plug_id,
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(400, "This printer already has a smart plug assigned")

    for field, value in update_data.items():
        setattr(plug, field, value)

    await db.commit()
    await db.refresh(plug)

    logger.info(f"Updated smart plug '{plug.name}'")
    return plug


@router.delete("/{plug_id}")
async def delete_smart_plug(plug_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a smart plug."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug:
        raise HTTPException(404, "Smart plug not found")

    plug_name = plug.name
    await db.delete(plug)
    await db.commit()

    logger.info(f"Deleted smart plug '{plug_name}'")
    return {"message": "Smart plug deleted"}


@router.post("/{plug_id}/control")
async def control_smart_plug(
    plug_id: int,
    control: SmartPlugControl,
    db: AsyncSession = Depends(get_db),
):
    """Manual control: on/off/toggle."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug:
        raise HTTPException(404, "Smart plug not found")

    if control.action == "on":
        success = await tasmota_service.turn_on(plug)
        expected_state = "ON"
    elif control.action == "off":
        success = await tasmota_service.turn_off(plug)
        expected_state = "OFF"
    elif control.action == "toggle":
        success = await tasmota_service.toggle(plug)
        expected_state = None  # Unknown after toggle
    else:
        raise HTTPException(400, f"Invalid action: {control.action}")

    if not success:
        raise HTTPException(503, "Failed to communicate with device")

    # Update last state
    if expected_state:
        plug.last_state = expected_state
    plug.last_checked = datetime.utcnow()
    await db.commit()

    return {"success": True, "action": control.action}


@router.get("/{plug_id}/status", response_model=SmartPlugStatus)
async def get_plug_status(plug_id: int, db: AsyncSession = Depends(get_db)):
    """Get current plug status from device."""
    result = await db.execute(select(SmartPlug).where(SmartPlug.id == plug_id))
    plug = result.scalar_one_or_none()
    if not plug:
        raise HTTPException(404, "Smart plug not found")

    status = await tasmota_service.get_status(plug)

    # Update last state in database
    if status["reachable"]:
        plug.last_state = status["state"]
        plug.last_checked = datetime.utcnow()
        await db.commit()

    return SmartPlugStatus(
        state=status["state"],
        reachable=status["reachable"],
        device_name=status.get("device_name"),
    )


@router.post("/test-connection")
async def test_connection(data: SmartPlugTestConnection):
    """Test connection to a Tasmota device."""
    result = await tasmota_service.test_connection(
        data.ip_address,
        data.username,
        data.password,
    )

    if not result["success"]:
        raise HTTPException(503, result.get("error", "Failed to connect to device"))

    return {
        "success": True,
        "state": result["state"],
        "device_name": result.get("device_name"),
    }
