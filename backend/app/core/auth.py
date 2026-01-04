import hashlib
import secrets
from datetime import datetime

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import get_db
from backend.app.models.api_key import APIKey


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns:
        Tuple of (full_key, key_hash, key_prefix)
    """
    # Generate a random 32-byte key and encode as hex (64 chars)
    full_key = f"bb_{secrets.token_hex(32)}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    key_prefix = full_key[:11]  # "bb_" + first 8 chars of token
    return full_key, key_hash, key_prefix


def hash_api_key(key: str) -> str:
    """Hash an API key for comparison."""
    return hashlib.sha256(key.encode()).hexdigest()


async def get_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> APIKey:
    """Verify API key and return the key record.

    Raises HTTPException if key is invalid, disabled, or expired.
    """
    key_hash = hash_api_key(x_api_key)

    result = await db.execute(select(APIKey).where(APIKey.key_hash == key_hash))
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not api_key.enabled:
        raise HTTPException(status_code=403, detail="API key is disabled")

    if api_key.expires_at and api_key.expires_at < datetime.utcnow():
        raise HTTPException(status_code=403, detail="API key has expired")

    # Update last_used timestamp
    api_key.last_used = datetime.utcnow()

    return api_key


async def get_optional_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> APIKey | None:
    """Get API key if provided, return None otherwise."""
    if not x_api_key:
        return None

    try:
        return await get_api_key(x_api_key, db)
    except HTTPException:
        return None


def check_permission(api_key: APIKey, permission: str) -> None:
    """Check if API key has a specific permission.

    Args:
        api_key: The API key record
        permission: One of 'queue', 'control_printer', 'read_status'

    Raises HTTPException if permission is denied.
    """
    permission_map = {
        "queue": api_key.can_queue,
        "control_printer": api_key.can_control_printer,
        "read_status": api_key.can_read_status,
    }

    if permission not in permission_map:
        raise HTTPException(status_code=500, detail=f"Unknown permission: {permission}")

    if not permission_map[permission]:
        raise HTTPException(status_code=403, detail=f"API key does not have '{permission}' permission")


def check_printer_access(api_key: APIKey, printer_id: int) -> None:
    """Check if API key has access to a specific printer.

    Args:
        api_key: The API key record
        printer_id: The printer ID to check

    Raises HTTPException if access is denied.
    """
    if api_key.printer_ids is not None and printer_id not in api_key.printer_ids:
        raise HTTPException(status_code=403, detail=f"API key does not have access to printer {printer_id}")
