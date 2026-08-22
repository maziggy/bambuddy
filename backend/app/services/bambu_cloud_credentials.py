"""Bambu Cloud credential storage.

Single seam for reading and bookkeeping the stored Bambu Cloud bearer token:
per-user columns when auth is enabled, global ``Settings`` rows otherwise
(auth-disabled single-user installs). Lives in the services layer so feature
packages (e.g. ``model_providers``) can consume credentials without importing
the route layer — routes are just one consumer among several here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import async_session
from backend.app.models.settings import Settings
from backend.app.models.user import User

logger = logging.getLogger(__name__)

# Keys for storing cloud credentials in settings
CLOUD_TOKEN_KEY = "bambu_cloud_token"
CLOUD_EMAIL_KEY = "bambu_cloud_email"
CLOUD_REGION_KEY = "bambu_cloud_region"
# Global (auth-disabled) counterpart of ``User.cloud_token_invalid_at``. Stores
# an ISO timestamp; absent/empty means "not known to be dead".
CLOUD_TOKEN_INVALID_KEY = "bambu_cloud_token_invalid_at"


def _normalise_region(region: str | None) -> str:
    """Treat NULL/empty as 'global' for legacy rows that predate the region column."""
    return region if region in ("global", "china") else "global"


async def is_cloud_token_invalid(db: AsyncSession, user: User | None = None) -> bool:
    """Whether the stored Bambu token is known to have been rejected.

    Set by :func:`mark_cloud_token_invalid` the first time Bambu answers 401,
    cleared on a fresh login/logout. This is the only durable record we have:
    Bambu's access token is opaque (no readable expiry) and Bambuddy does not
    persist the refresh token, so without this flag a dead credential looks
    exactly like a live one.
    """
    if user is not None:
        return user.cloud_token_invalid_at is not None
    result = await db.execute(select(Settings).where(Settings.key == CLOUD_TOKEN_INVALID_KEY))
    row = result.scalar_one_or_none()
    return bool(row and row.value)


async def mark_cloud_token_invalid(user_id: int | None) -> None:
    """Record that Bambu rejected the stored token.

    Opens its own session on purpose. This runs from
    ``BambuCloudService._on_auth_failure``, i.e. in the middle of a route that
    is about to fail — writing through that route's session would tie the flag
    to a transaction the route may still roll back, and the fact that the
    credential is dead is true regardless of how the request ends.

    Best-effort: a bookkeeping failure must never replace the 401 the caller
    actually needs to see. ``user_id=None`` (auth-disabled single-user setup)
    records the global flag — those installs *do* hold a token
    (:func:`get_stored_token` reads it from ``Settings``), so the rejection
    must land somewhere the status endpoints can see it.
    """
    now = datetime.now(timezone.utc)
    try:
        async with async_session() as db:
            if user_id is not None:
                await db.execute(update(User).where(User.id == user_id).values(cloud_token_invalid_at=now))
            else:
                result = await db.execute(select(Settings).where(Settings.key == CLOUD_TOKEN_INVALID_KEY))
                row = result.scalar_one_or_none()
                if row:
                    row.value = now.isoformat()
                else:
                    db.add(Settings(key=CLOUD_TOKEN_INVALID_KEY, value=now.isoformat()))
            await db.commit()
        logger.warning("Bambu Cloud rejected the stored token (user_id=%s) — marking the sign-in as expired", user_id)
    except Exception:
        logger.exception("Could not record the Bambu Cloud token as invalid")


async def _clear_cloud_token_invalid(db: AsyncSession, user: User | None) -> None:
    """Clear the rejected-token flag — called on every fresh login and logout."""
    if user is not None:
        await db.execute(update(User).where(User.id == user.id).values(cloud_token_invalid_at=None))
        return
    result = await db.execute(select(Settings).where(Settings.key == CLOUD_TOKEN_INVALID_KEY))
    row = result.scalar_one_or_none()
    if row:
        await db.delete(row)


async def get_stored_token(db: AsyncSession, user: User | None = None) -> tuple[str | None, str | None, str]:
    """Get stored cloud token, email, and region.

    When a user is provided (auth enabled), returns that user's per-user credentials.
    When user is None (auth disabled), falls back to global Settings table.
    Region defaults to ``"global"`` when unset (including for rows that predate the
    ``cloud_region`` column).
    """
    if user is not None:
        return user.cloud_token, user.cloud_email, _normalise_region(user.cloud_region)

    # Fallback: global storage (auth disabled)
    result = await db.execute(
        select(Settings).where(Settings.key.in_([CLOUD_TOKEN_KEY, CLOUD_EMAIL_KEY, CLOUD_REGION_KEY]))
    )
    settings = {s.key: s.value for s in result.scalars().all()}
    return (
        settings.get(CLOUD_TOKEN_KEY),
        settings.get(CLOUD_EMAIL_KEY),
        _normalise_region(settings.get(CLOUD_REGION_KEY)),
    )
