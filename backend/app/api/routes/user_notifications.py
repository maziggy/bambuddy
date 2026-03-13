"""API routes for user email notification preferences."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import get_current_active_user
from backend.app.core.database import get_db
from backend.app.models.user import User
from backend.app.models.user_email_pref import UserEmailPreference
from backend.app.schemas.user_notifications import UserEmailPreferenceResponse, UserEmailPreferenceUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user-notifications", tags=["user-notifications"])


@router.get("/preferences", response_model=UserEmailPreferenceResponse)
async def get_user_email_preferences(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current user's email notification preferences.

    Returns defaults (all enabled) if no preferences are saved yet.
    """
    result = await db.execute(
        select(UserEmailPreference).where(UserEmailPreference.user_id == current_user.id)
    )
    pref = result.scalar_one_or_none()

    if pref is None:
        # Return defaults
        return UserEmailPreferenceResponse(
            notify_print_start=True,
            notify_print_complete=True,
            notify_print_failed=True,
            notify_print_stopped=True,
        )

    return pref


@router.put("/preferences", response_model=UserEmailPreferenceResponse)
async def update_user_email_preferences(
    data: UserEmailPreferenceUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the current user's email notification preferences."""
    if not current_user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User must have an email address to receive notifications",
        )

    result = await db.execute(
        select(UserEmailPreference).where(UserEmailPreference.user_id == current_user.id)
    )
    pref = result.scalar_one_or_none()

    if pref is None:
        pref = UserEmailPreference(
            user_id=current_user.id,
            notify_print_start=data.notify_print_start,
            notify_print_complete=data.notify_print_complete,
            notify_print_failed=data.notify_print_failed,
            notify_print_stopped=data.notify_print_stopped,
        )
        db.add(pref)
    else:
        pref.notify_print_start = data.notify_print_start
        pref.notify_print_complete = data.notify_print_complete
        pref.notify_print_failed = data.notify_print_failed
        pref.notify_print_stopped = data.notify_print_stopped

    await db.commit()
    await db.refresh(pref)

    logger.info(
        "Updated email notification preferences for user %s: start=%s, complete=%s, failed=%s, stopped=%s",
        current_user.username,
        pref.notify_print_start,
        pref.notify_print_complete,
        pref.notify_print_failed,
        pref.notify_print_stopped,
    )

    return pref
