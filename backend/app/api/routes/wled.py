"""WLED integration routes."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.user import User
from backend.app.services.wled import wled_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wled", tags=["WLED"])


class WledConnectionRequest(BaseModel):
    host: str
    port: int = 80
    api_key: str | None = None


class WledTestEffectRequest(BaseModel):
    host: str
    port: int = 80
    api_key: str | None = None


@router.post("/test-connection")
async def test_wled_connection(
    body: WledConnectionRequest,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Test connectivity to a WLED device and return device info."""
    return await wled_service.test_connection(body.host, body.port, body.api_key)


@router.get("/presets")
async def get_wled_presets(
    host: str,
    port: int = 80,
    api_key: str | None = None,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Fetch available presets from a WLED device."""
    return await wled_service.get_presets(host, port, api_key)


@router.post("/test-effect")
async def trigger_wled_test_effect(
    body: WledTestEffectRequest,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """Flash the WLED device white briefly as a visual confirmation test."""
    return await wled_service.trigger_test_effect(body.host, body.port, body.api_key)


@router.post("/invalidate-cache")
async def invalidate_wled_cache(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_WRITE),
):
    """Invalidate the cached state map — called automatically when settings are saved."""
    wled_service.invalidate_cache()
    return {"ok": True}
