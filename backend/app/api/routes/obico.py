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


@router.get("/status")
async def get_status(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Scheduler status, per-printer classification, and recent detection history."""
    settings = await obico_detection_service._load_settings()
    status = obico_detection_service.get_status()
    return {
        **status,
        "enabled": settings["enabled"],
        "ml_url": settings["ml_url"],
        "sensitivity": settings["sensitivity"],
        "action": settings["action"],
        "poll_interval": settings["poll_interval"],
        "external_url_configured": bool(settings["external_url"]),
    }


@router.post("/test-connection")
async def test_connection(
    req: TestConnectionRequest,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Ping the Obico ML API `/hc/` health endpoint. Returns ok + raw body."""
    if not req.url:
        return {"ok": False, "status_code": None, "body": None, "error": "URL is empty"}
    return await obico_detection_service.test_connection(req.url)


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
