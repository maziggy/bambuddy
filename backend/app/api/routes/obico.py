"""API routes for Obico AI failure detection."""

import logging

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.permissions import Permission
from backend.app.models.user import User
from backend.app.services.obico_detection import obico_detection_service, pop_frame

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/obico", tags=["obico"])


class TestConnectionRequest(BaseModel):
    url: str
    # Omitted entirely = test with the saved token; "" = test with no token.
    token: str | None = None


@router.get("/status")
async def get_status(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Scheduler status, per-printer classification, and recent detection history."""
    settings = await obico_detection_service._load_settings()
    status = obico_detection_service.get_status(settings["sensitivity"])
    return {
        **status,
        "enabled": settings["enabled"],
        "ml_url": settings["ml_url"],
        "sensitivity": settings["sensitivity"],
        "action": settings["action"],
        "poll_interval": settings["poll_interval"],
        "external_url_configured": bool(settings["external_url"]),
    }


@router.get("/printer-status")
async def get_printer_status(
    user: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
):
    """Per-printer live classification for the printer cards (#1546).

    Deliberately excludes configuration (ML URL, action, history) so users
    with printers:read but no settings:read can still render the badge.
    """
    settings = await obico_detection_service._load_settings()
    enabled_printers = settings["enabled_printers"]
    # Error strings can embed configured URLs (ML API base, external URL), so
    # they stay behind settings:read like the rest of the configuration.
    can_see_error = user is None or user.has_permission(Permission.SETTINGS_READ.value)
    per_printer = obico_detection_service.get_per_printer()
    if not can_see_error:
        # The "error" *class* is not configuration — a printers:read user still
        # needs to know their print is not being watched. Only the reason, which
        # can name a URL, is withheld.
        per_printer = {pid: {**entry, "error": None} for pid, entry in per_printer.items()}
    return {
        "enabled": settings["enabled"],
        # None = all printers are monitored
        "monitored_printers": sorted(enabled_printers) if enabled_printers is not None else None,
        "per_printer": per_printer,
        "last_error": obico_detection_service._last_error if can_see_error else None,
    }


@router.post("/test-connection")
async def test_connection(
    req: TestConnectionRequest,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Ping the Obico ML API health endpoint and check the token. Returns ok + raw body."""
    if not req.url:
        return {"ok": False, "status_code": None, "body": None, "error": "URL is empty", "auth_ok": None}
    token = req.token
    if token is None:
        # Field omitted entirely — test what the service actually uses.
        settings = await obico_detection_service._load_settings()
        token = settings.get("ml_token") or ""
    return await obico_detection_service.test_connection(req.url, token)


@router.get("/cached-frame/{nonce}")
async def cached_frame(nonce: str):
    """Serve a pre-captured JPEG to the Obico ML API.

    The detection loop captures a snapshot locally (where we control the timeout),
    stashes the bytes under a one-shot random nonce, then hands this URL to Obico's
    ML API. Obico's hardcoded 5s read timeout never races our snapshot pipeline.

    Unauthenticated: the unguessable 32-byte nonce is single-use and expires in
    seconds, so exposing this path doesn't widen the camera access surface.
    """
    data = await pop_frame(nonce)
    if data is None:
        raise HTTPException(status_code=404, detail="Frame not found or expired")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )
