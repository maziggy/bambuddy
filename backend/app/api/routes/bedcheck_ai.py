"""API routes for the AI bed-check backend (build-plate occupancy via vision model)."""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.permissions import Permission
from backend.app.models.user import User
from backend.app.services import bedcheck_ai

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bedcheck-ai", tags=["bedcheck-ai"])


class BedcheckAiTestConnectionRequest(BaseModel):
    base_url: str
    model: str
    # Omitted entirely = test with the saved key; "" = test with no key.
    api_key: str | None = None


@router.post("/test-connection")
async def test_connection(
    req: BedcheckAiTestConnectionRequest,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Send one synthetic frame through the configured AI backend and report whether
    it returns a schema-valid bed-occupancy verdict, plus latency. Never touches a
    real printer or its calibration references."""
    if not req.base_url or not req.model:
        return {"ok": False, "error": "Base URL and model are required", "verdict": None, "latency_ms": None}
    api_key = req.api_key
    if api_key is None:
        cfg = await bedcheck_ai._load_ai_settings()
        api_key = cfg["api_key"]
    return await bedcheck_ai.test_connection(req.base_url, req.model, api_key)
