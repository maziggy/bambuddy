import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/virtual-printers", tags=["virtual-printers"])


class VirtualPrinterCreate(BaseModel):
    name: str = "Bambuddy"
    enabled: bool = False
    mode: str = "immediate"
    model: str | None = None
    access_code: str | None = None
    target_printer_id: int | None = None
    bind_ip: str | None = None
    remote_interface_ip: str | None = None


class VirtualPrinterUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    mode: str | None = None
    model: str | None = None
    access_code: str | None = None
    target_printer_id: int | None = None
    bind_ip: str | None = None
    remote_interface_ip: str | None = None


def _vp_to_dict(vp, status: dict | None = None) -> dict:
    """Convert VirtualPrinter model to response dict."""
    from backend.app.services.virtual_printer import VIRTUAL_PRINTER_MODELS
    from backend.app.services.virtual_printer.manager import DEFAULT_VIRTUAL_PRINTER_MODEL, _get_serial_for_model

    model_code = vp.model or DEFAULT_VIRTUAL_PRINTER_MODEL
    serial = _get_serial_for_model(model_code, vp.serial_suffix)

    return {
        "id": vp.id,
        "name": vp.name,
        "enabled": vp.enabled,
        "mode": vp.mode,
        "model": model_code,
        "model_name": VIRTUAL_PRINTER_MODELS.get(model_code, model_code),
        "access_code_set": bool(vp.access_code),
        "serial": serial,
        "target_printer_id": vp.target_printer_id,
        "bind_ip": vp.bind_ip,
        "remote_interface_ip": vp.remote_interface_ip,
        "position": vp.position,
        "status": status or {"running": False, "pending_files": 0},
    }


@router.get("")
async def list_virtual_printers(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """List all virtual printers with status."""
    from backend.app.models.virtual_printer import VirtualPrinter
    from backend.app.services.virtual_printer import VIRTUAL_PRINTER_MODELS, virtual_printer_manager

    result = await db.execute(select(VirtualPrinter).order_by(VirtualPrinter.position, VirtualPrinter.id))
    vps = result.scalars().all()

    printers = []
    for vp in vps:
        instance = virtual_printer_manager.get_instance(vp.id)
        status = instance.get_status() if instance else {"running": False, "pending_files": 0}
        printers.append(_vp_to_dict(vp, status))

    return {
        "printers": printers,
        "models": VIRTUAL_PRINTER_MODELS,
    }


@router.post("")
async def create_virtual_printer(
    body: VirtualPrinterCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Create a new virtual printer."""
    from backend.app.models.virtual_printer import VirtualPrinter
    from backend.app.services.virtual_printer import VIRTUAL_PRINTER_MODELS, virtual_printer_manager
    from backend.app.services.virtual_printer.manager import DEFAULT_VIRTUAL_PRINTER_MODEL

    # Validate mode
    if body.mode not in ("immediate", "review", "print_queue", "proxy"):
        return JSONResponse(status_code=400, content={"detail": "Invalid mode"})

    # Validate model
    if body.model and body.model not in VIRTUAL_PRINTER_MODELS:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid model. Must be one of: {', '.join(VIRTUAL_PRINTER_MODELS.keys())}"},
        )

    # Validate access code length
    if body.access_code and len(body.access_code) != 8:
        return JSONResponse(status_code=400, content={"detail": "Access code must be exactly 8 characters"})

    # Validation when enabling
    if body.enabled:
        if not body.bind_ip:
            return JSONResponse(status_code=400, content={"detail": "Bind IP is required when enabling"})
        if body.mode == "proxy":
            if not body.target_printer_id:
                return JSONResponse(status_code=400, content={"detail": "Target printer is required for proxy mode"})
        else:
            if not body.access_code:
                return JSONResponse(status_code=400, content={"detail": "Access code is required when enabling"})

    # Validate proxy target printer exists
    if body.target_printer_id:
        from backend.app.models.printer import Printer

        result = await db.execute(select(Printer).where(Printer.id == body.target_printer_id))
        if not result.scalar_one_or_none():
            return JSONResponse(
                status_code=400, content={"detail": f"Printer with ID {body.target_printer_id} not found"}
            )

    # Validate bind_ip uniqueness (against all enabled VPs)
    if body.bind_ip:
        result = await db.execute(
            select(VirtualPrinter).where(
                VirtualPrinter.bind_ip == body.bind_ip,
                VirtualPrinter.enabled == True,  # noqa: E712
            )
        )
        if result.scalar_one_or_none():
            return JSONResponse(status_code=400, content={"detail": f"Bind IP {body.bind_ip} is already in use"})

    # Generate next serial suffix
    result = await db.execute(select(VirtualPrinter.serial_suffix).order_by(VirtualPrinter.id.desc()))
    last_suffix = result.scalar()
    if last_suffix:
        try:
            next_num = int(last_suffix) + 1
            new_suffix = str(next_num).zfill(9)
        except ValueError:
            new_suffix = "391800002"
    else:
        new_suffix = "391800001"

    # Get next position
    result = await db.execute(select(VirtualPrinter.position).order_by(VirtualPrinter.position.desc()))
    last_pos = result.scalar()
    next_pos = (last_pos or 0) + 1

    vp = VirtualPrinter(
        name=body.name,
        enabled=body.enabled,
        mode=body.mode,
        model=body.model or DEFAULT_VIRTUAL_PRINTER_MODEL,
        access_code=body.access_code,
        target_printer_id=body.target_printer_id,
        bind_ip=body.bind_ip,
        remote_interface_ip=body.remote_interface_ip,
        serial_suffix=new_suffix,
        position=next_pos,
    )
    db.add(vp)
    await db.commit()
    await db.refresh(vp)

    logger.info("Created virtual printer: %s (id=%d)", vp.name, vp.id)

    # Sync services if enabled
    if body.enabled:
        try:
            await virtual_printer_manager.sync_from_db()
        except Exception as e:
            logger.error("Failed to start virtual printer after create: %s", e)

    return _vp_to_dict(vp)


@router.get("/{vp_id}")
async def get_virtual_printer(
    vp_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Get a single virtual printer with status."""
    from backend.app.models.virtual_printer import VirtualPrinter
    from backend.app.services.virtual_printer import virtual_printer_manager

    result = await db.execute(select(VirtualPrinter).where(VirtualPrinter.id == vp_id))
    vp = result.scalar_one_or_none()
    if not vp:
        return JSONResponse(status_code=404, content={"detail": "Virtual printer not found"})

    instance = virtual_printer_manager.get_instance(vp.id)
    status = instance.get_status() if instance else {"running": False, "pending_files": 0}

    return _vp_to_dict(vp, status)


@router.put("/{vp_id}")
async def update_virtual_printer(
    vp_id: int,
    body: VirtualPrinterUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Update a virtual printer."""
    from backend.app.models.virtual_printer import VirtualPrinter
    from backend.app.services.virtual_printer import VIRTUAL_PRINTER_MODELS, virtual_printer_manager

    result = await db.execute(select(VirtualPrinter).where(VirtualPrinter.id == vp_id))
    vp = result.scalar_one_or_none()
    if not vp:
        return JSONResponse(status_code=404, content={"detail": "Virtual printer not found"})

    logger.debug(
        "Update VP %d: body=%s, current state: mode=%s, enabled=%s, access_code_set=%s, bind_ip=%s, target=%s",
        vp_id,
        body.model_dump(exclude_unset=True),
        vp.mode,
        vp.enabled,
        bool(vp.access_code),
        vp.bind_ip,
        vp.target_printer_id,
    )

    # Apply updates
    if body.name is not None:
        vp.name = body.name
    if body.mode is not None:
        if body.mode not in ("immediate", "review", "print_queue", "proxy"):
            return JSONResponse(status_code=400, content={"detail": "Invalid mode"})
        vp.mode = body.mode
    if body.model is not None:
        if body.model not in VIRTUAL_PRINTER_MODELS:
            return JSONResponse(
                status_code=400,
                content={"detail": f"Invalid model. Must be one of: {', '.join(VIRTUAL_PRINTER_MODELS.keys())}"},
            )
        vp.model = body.model
    if body.access_code is not None:
        if body.access_code and len(body.access_code) != 8:
            return JSONResponse(status_code=400, content={"detail": "Access code must be exactly 8 characters"})
        vp.access_code = body.access_code
    if body.target_printer_id is not None:
        from backend.app.models.printer import Printer

        result = await db.execute(select(Printer).where(Printer.id == body.target_printer_id))
        if not result.scalar_one_or_none():
            return JSONResponse(
                status_code=400, content={"detail": f"Printer with ID {body.target_printer_id} not found"}
            )
        vp.target_printer_id = body.target_printer_id
    if body.bind_ip is not None:
        vp.bind_ip = body.bind_ip
    if body.remote_interface_ip is not None:
        vp.remote_interface_ip = body.remote_interface_ip

    # Determine final enabled state
    explicitly_enabling = body.enabled is True
    new_enabled = body.enabled if body.enabled is not None else vp.enabled
    effective_mode = vp.mode

    if explicitly_enabling:
        # User is explicitly toggling on — enforce all requirements
        if not vp.bind_ip:
            logger.warning("Update VP %d rejected: no bind_ip", vp_id)
            return JSONResponse(status_code=400, content={"detail": "Bind IP is required when enabling"})
        # Validate bind_ip uniqueness (against all enabled VPs)
        existing = await db.execute(
            select(VirtualPrinter).where(
                VirtualPrinter.bind_ip == vp.bind_ip,
                VirtualPrinter.id != vp_id,
                VirtualPrinter.enabled == True,  # noqa: E712
            )
        )
        conflict = existing.scalar_one_or_none()
        if conflict:
            logger.warning(
                "Update VP %d rejected: bind_ip %s already in use by VP %d (enabled=%s, mode=%s)",
                vp_id,
                vp.bind_ip,
                conflict.id,
                conflict.enabled,
                conflict.mode,
            )
            return JSONResponse(
                status_code=400,
                content={"detail": f"Bind IP {vp.bind_ip} is already in use by '{conflict.name}'"},
            )
        if effective_mode == "proxy":
            if not vp.target_printer_id:
                logger.warning("Update VP %d rejected: no target_printer_id for proxy mode", vp_id)
                return JSONResponse(status_code=400, content={"detail": "Target printer is required for proxy mode"})
        else:
            if not vp.access_code:
                logger.warning(
                    "Update VP %d rejected: no access_code for non-proxy enable (mode=%s)", vp_id, effective_mode
                )
                return JSONResponse(status_code=400, content={"detail": "Access code is required when enabling"})
    elif new_enabled and body.enabled is None:
        # VP is already enabled and user is changing other fields —
        # auto-disable if new state doesn't meet requirements
        if not vp.bind_ip:
            new_enabled = False
        elif effective_mode == "proxy":
            if not vp.target_printer_id:
                new_enabled = False
        else:
            if not vp.access_code:
                new_enabled = False

    vp.enabled = new_enabled

    await db.commit()
    await db.refresh(vp)

    logger.info("Updated virtual printer: %s (id=%d)", vp.name, vp.id)

    # Sync services
    try:
        await virtual_printer_manager.sync_from_db()
    except Exception as e:
        logger.error("Failed to sync virtual printers after update: %s", e)

    instance = virtual_printer_manager.get_instance(vp.id)
    status = instance.get_status() if instance else {"running": False, "pending_files": 0}

    return _vp_to_dict(vp, status)


@router.delete("/{vp_id}")
async def delete_virtual_printer(
    vp_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Delete a virtual printer."""
    from sqlalchemy import delete as sql_delete

    from backend.app.models.virtual_printer import VirtualPrinter
    from backend.app.services.virtual_printer import virtual_printer_manager

    result = await db.execute(select(VirtualPrinter).where(VirtualPrinter.id == vp_id))
    vp = result.scalar_one_or_none()
    if not vp:
        return JSONResponse(status_code=404, content={"detail": "Virtual printer not found"})

    vp_name = vp.name

    # Stop instance if running
    await virtual_printer_manager.remove_instance(vp_id)

    # Delete from DB
    await db.execute(sql_delete(VirtualPrinter).where(VirtualPrinter.id == vp_id))
    await db.commit()

    logger.info("Deleted virtual printer: %s (id=%d)", vp_name, vp_id)

    # Resync remaining services
    try:
        await virtual_printer_manager.sync_from_db()
    except Exception as e:
        logger.error("Failed to sync virtual printers after delete: %s", e)

    return {"detail": "Deleted", "id": vp_id}
