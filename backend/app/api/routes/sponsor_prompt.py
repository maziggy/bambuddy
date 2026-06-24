"""API routes for the in-app sponsor toast."""

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.user import User
from backend.app.schemas.sponsor_prompt import (
    SponsorPromptCheckResponse,
    SponsorPromptDismissRequest,
)
from backend.app.services import sponsor_prompt as service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sponsor-prompt", tags=["sponsor-prompt"])


def _user_id(current_user: User | None) -> int | None:
    return current_user.id if current_user is not None else None


@router.get("/check", response_model=SponsorPromptCheckResponse)
async def check_sponsor_prompt(
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Return the next eligible sponsor-toast trigger, or `{show: false}`."""
    trigger = await service.evaluate(db, _user_id(current_user))
    await db.commit()
    if trigger is None:
        return SponsorPromptCheckResponse(show=False)
    return SponsorPromptCheckResponse(
        show=True,
        milestone=trigger.milestone,
        family=trigger.family,
        threshold=trigger.threshold,
        payload=trigger.payload,
    )


@router.post("/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_sponsor_prompt(
    data: SponsorPromptDismissRequest,
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Anchor the 14-day cooldown and record the milestone as shown."""
    await service.dismiss(db, _user_id(current_user), data.milestone)
    await db.commit()
    return None
