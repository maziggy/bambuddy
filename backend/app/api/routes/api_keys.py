import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled, generate_api_key
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.api_key import APIKey
from backend.app.models.user import User
from backend.app.schemas.api_key import (
    APIKeyCreate,
    APIKeyCreateResponse,
    APIKeyResponse,
    APIKeyUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.get("/", response_model=list[APIKeyResponse])
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.API_KEYS_READ),
):
    """List all API keys (without full key values)."""
    result = await db.execute(select(APIKey).order_by(APIKey.created_at.desc()))
    return list(result.scalars().all())


@router.post("/", response_model=APIKeyCreateResponse)
async def create_api_key(
    data: APIKeyCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.API_KEYS_CREATE),
):
    """Create a new API key.

    IMPORTANT: The full API key is only returned in this response.
    Store it securely - it cannot be retrieved again.
    """
    # Generate the key
    full_key, key_hash, key_prefix = generate_api_key()

    api_key = APIKey(
        name=data.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        can_queue=data.can_queue,
        can_control_printer=data.can_control_printer,
        can_read_status=data.can_read_status,
        printer_ids=data.printer_ids,
        expires_at=data.expires_at,
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)

    # Return with full key (only time it's shown)
    return APIKeyCreateResponse(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        key=full_key,  # Only returned on creation
        can_queue=api_key.can_queue,
        can_control_printer=api_key.can_control_printer,
        can_read_status=api_key.can_read_status,
        printer_ids=api_key.printer_ids,
        enabled=api_key.enabled,
        last_used=api_key.last_used,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
    )


@router.get("/{key_id}", response_model=APIKeyResponse)
async def get_api_key(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.API_KEYS_READ),
):
    """Get an API key by ID."""
    result = await db.execute(select(APIKey).where(APIKey.id == key_id))
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    return api_key


@router.patch("/{key_id}", response_model=APIKeyResponse)
async def update_api_key(
    key_id: int,
    data: APIKeyUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.API_KEYS_UPDATE),
):
    """Update an API key."""
    result = await db.execute(select(APIKey).where(APIKey.id == key_id))
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    # Update fields if provided
    if data.name is not None:
        api_key.name = data.name
    if data.can_queue is not None:
        api_key.can_queue = data.can_queue
    if data.can_control_printer is not None:
        api_key.can_control_printer = data.can_control_printer
    if data.can_read_status is not None:
        api_key.can_read_status = data.can_read_status
    if data.printer_ids is not None:
        api_key.printer_ids = data.printer_ids
    if data.enabled is not None:
        api_key.enabled = data.enabled
    if data.expires_at is not None:
        api_key.expires_at = data.expires_at

    await db.flush()
    await db.refresh(api_key)

    return api_key


@router.delete("/{key_id}")
async def delete_api_key(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.API_KEYS_DELETE),
):
    """Delete (revoke) an API key."""
    result = await db.execute(select(APIKey).where(APIKey.id == key_id))
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    await db.delete(api_key)

    return {"message": "API key deleted"}
