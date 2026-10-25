"""Announcements from the Bambuddy maintainers (services/announcements.py).

Shown to administrators, and to every signed-in user when the
``announcements_all_users`` setting is on. With authentication off whoever opens
Bambuddy runs it, so they see them. Anyone else gets ``visible: false`` and an
empty list rather than a 403. ``visible`` is separate from the list because the
sidebar entry is there for whoever may see announcements, also while nothing is
published, and hidden for everyone else.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import is_auth_enabled, require_auth_if_enabled
from backend.app.core.database import get_db
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services import announcements as service

router = APIRouter(prefix="/announcements", tags=["announcements"])


async def _may_see(db: AsyncSession, user: User | None) -> bool:
    if not await service.is_enabled(db):
        return False
    if not await is_auth_enabled(db):
        return True
    # Authenticated by API key: a script, not a person with an inbox.
    if user is None:
        return False
    if user.is_admin:
        return True
    all_users = (
        await db.execute(select(Settings.value).where(Settings.key == service.ALL_USERS_KEY))
    ).scalar_one_or_none()
    return (all_users or "").lower() == "true"


@router.get("")
async def list_announcements(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User | None = Depends(require_auth_if_enabled),
) -> dict:
    """Whether this user may see announcements, and the live ones newest first,
    with their read state."""
    if not await _may_see(db, current_user):
        return {"visible": False, "announcements": []}
    return {
        "visible": True,
        "announcements": await service.list_for(db, current_user.id if current_user else None),
    }


@router.post("/{public_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_announcement_read(
    public_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User | None = Depends(require_auth_if_enabled),
) -> None:
    if not await _may_see(db, current_user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Announcement not found")
    if not await service.mark_read(db, public_id, current_user.id if current_user else None):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Announcement not found")
    await db.commit()
