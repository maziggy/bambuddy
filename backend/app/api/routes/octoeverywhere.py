"""API routes for OctoEverywhere AI failure detection."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import ApiKeyActor, RequestActor, RequestPrinterScope, RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.core.printer_scope import PrinterScope
from backend.app.models.printer import Printer
from backend.app.models.user import User
from backend.app.services.notification_service import notification_service
from backend.app.services.octoeverywhere_detection import octoeverywhere_detection_service

router = APIRouter(prefix="/octoeverywhere", tags=["octoeverywhere"])


class TestConnectionRequest(BaseModel):
    # Omitted = test the saved key; empty = test with no key.
    api_key: str | None = None
    confidence: Literal["lowest", "low", "medium", "high", "highest"] | None = None


@router.get("/status")
async def get_status(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Scheduler status, print quality, and recent detection history."""
    settings = await octoeverywhere_detection_service._load_settings()
    providers = await notification_service._get_providers_for_event(db, "on_ai_failure_detection")
    printers = select(Printer.id).where(Printer.is_active.is_(True))
    if settings["enabled_printers"] is not None:
        printers = printers.where(Printer.id.in_(settings["enabled_printers"]))
    printer_ids = (await db.execute(printers)).scalars().all()
    # Use the same per-printer subscription rules as notification delivery.
    covered_printers = {provider.printer_id for provider in providers}
    uncovered_printers = (
        sorted(pid for pid in printer_ids if pid not in covered_printers) if None not in covered_printers else []
    )
    return {
        **octoeverywhere_detection_service.get_status(),
        "enabled": settings["enabled"],
        "api_key_configured": bool(settings["api_key"]),
        "confidence": settings["confidence"],
        "action": settings["action"],
        "poll_interval": settings["poll_interval"],
        "notifications": {"configured": bool(providers), "uncovered_printers": uncovered_printers},
    }


@router.get("/printer-status")
async def get_printer_status(
    user: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    printer_scope: PrinterScope = RequestPrinterScope,
    actor: User | ApiKeyActor | None = RequestActor,
):
    """Live printer-card status without exposing provider configuration."""
    settings = await octoeverywhere_detection_service._load_settings()
    enabled_printers = settings["enabled_printers"]
    can_see_error = actor is None or actor.has_permission(Permission.SETTINGS_READ.value)
    per_printer = octoeverywhere_detection_service.get_per_printer()
    if not can_see_error:
        per_printer = {pid: {**entry, "error": None, "error_code": None} for pid, entry in per_printer.items()}
    per_printer = {pid: entry for pid, entry in per_printer.items() if printer_scope.allows(int(pid))}
    if enabled_printers is not None:
        enabled_printers = [pid for pid in enabled_printers if printer_scope.allows(int(pid))]
    return {
        "enabled": settings["enabled"],
        "monitored_printers": sorted(enabled_printers) if enabled_printers is not None else None,
        "per_printer": per_printer,
        "last_error": octoeverywhere_detection_service._last_error if can_see_error else None,
        "last_error_code": octoeverywhere_detection_service._last_error_code if can_see_error else None,
    }


@router.post("/test-connection")
async def test_connection(
    req: TestConnectionRequest,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Validate the key by creating a context, without uploading a camera image."""
    settings = await octoeverywhere_detection_service._load_settings()
    api_key = settings["api_key"] if req.api_key is None else req.api_key
    return await octoeverywhere_detection_service.test_connection(api_key, req.confidence or settings["confidence"])
