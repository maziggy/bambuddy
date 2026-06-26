"""Sponsor-prompt trigger evaluator and dismiss handler.

Drives the in-app "support keeps Bambuddy independent" toast. Trigger families
fire at milestones the user has earned (prints, archives, filament cost,
anniversary) plus a soft version-update nudge after a major upgrade.

A 14-day cooldown applies across ALL families: if any toast fired in the last
14 days, no new toast fires. Each individual milestone is shown at most once
per user (or once per install in auth-disabled mode); version-update is the
exception — it re-arms every time the running version is newer than the one
last acknowledged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import APP_VERSION
from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.sponsor_toast_state import SponsorToastState
from backend.app.models.user import User

logger = logging.getLogger(__name__)

COOLDOWN_DAYS = 14

PRINT_MILESTONES = (100, 500, 1000, 2500, 5000)
COST_MILESTONES = (100, 500, 1000)
ARCHIVE_MILESTONES = (50, 250, 1000)
ANNIVERSARY_YEARS = 1


@dataclass
class Trigger:
    """Evaluated trigger result returned to the frontend."""

    milestone: str  # e.g. "prints-500", "anniversary-1", "version-update"
    family: str  # "prints" | "cost" | "archives" | "anniversary" | "version-update"
    threshold: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


async def _get_or_create_state(db: AsyncSession, user_id: int | None) -> SponsorToastState:
    """Fetch the state row for this user (or the install-default NULL row).

    Creates the row lazily on first access so the migration doesn't need to
    seed anything.
    """
    if user_id is None:
        stmt = select(SponsorToastState).where(SponsorToastState.user_id.is_(None))
    else:
        stmt = select(SponsorToastState).where(SponsorToastState.user_id == user_id)
    result = await db.execute(stmt)
    state = result.scalar_one_or_none()
    if state is None:
        state = SponsorToastState(user_id=user_id, milestones_seen="[]")
        db.add(state)
        await db.flush()
    return state


def _within_cooldown(state: SponsorToastState) -> bool:
    if state.last_shown_at is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=COOLDOWN_DAYS)
    last = state.last_shown_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last >= cutoff


def _seen_milestones(state: SponsorToastState) -> set[str]:
    try:
        raw = json.loads(state.milestones_seen or "[]")
        return set(raw) if isinstance(raw, list) else set()
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "sponsor_toast_state.milestones_seen for user=%s was not valid JSON; resetting",
            state.user_id,
        )
        return set()


# ---------------------------------------------------------------------------
# Per-family checks
# ---------------------------------------------------------------------------


def _user_filter(column, user_id: int | None):
    return column.is_(None) if user_id is None else column == user_id


async def _check_anniversary(
    db: AsyncSession, user_id: int | None, seen: set[str], _state: SponsorToastState
) -> Trigger | None:
    milestone = f"anniversary-{ANNIVERSARY_YEARS}"
    if milestone in seen:
        return None
    if user_id is None:
        # Install-anchor = earliest users.created_at (the first admin row).
        result = await db.execute(select(func.min(User.created_at)))
        anchor = result.scalar()
    else:
        result = await db.execute(select(User.created_at).where(User.id == user_id))
        anchor = result.scalar()
    if anchor is None:
        return None
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - anchor < timedelta(days=365 * ANNIVERSARY_YEARS):
        return None
    return Trigger(milestone=milestone, family="anniversary")


async def _check_prints(
    db: AsyncSession, user_id: int | None, seen: set[str], _state: SponsorToastState
) -> Trigger | None:
    stmt = (
        select(func.count())
        .select_from(PrintLogEntry)
        .where(
            PrintLogEntry.status == "completed",
            _user_filter(PrintLogEntry.created_by_id, user_id),
        )
    )
    completed = (await db.execute(stmt)).scalar() or 0
    # Pick the LARGEST milestone the user has crossed but not yet seen.
    for threshold in sorted(PRINT_MILESTONES, reverse=True):
        key = f"prints-{threshold}"
        if completed >= threshold and key not in seen:
            return Trigger(
                milestone=key,
                family="prints",
                threshold=threshold,
                payload={"count": completed},
            )
    return None


async def _check_archives(
    db: AsyncSession, user_id: int | None, seen: set[str], _state: SponsorToastState
) -> Trigger | None:
    stmt = select(func.count()).select_from(PrintArchive).where(_user_filter(PrintArchive.created_by_id, user_id))
    archived = (await db.execute(stmt)).scalar() or 0
    for threshold in sorted(ARCHIVE_MILESTONES, reverse=True):
        key = f"archives-{threshold}"
        if archived >= threshold and key not in seen:
            return Trigger(
                milestone=key,
                family="archives",
                threshold=threshold,
                payload={"count": archived},
            )
    return None


async def _check_cost(
    db: AsyncSession, user_id: int | None, seen: set[str], _state: SponsorToastState
) -> Trigger | None:
    stmt = (
        select(func.coalesce(func.sum(PrintLogEntry.cost), 0) + func.coalesce(func.sum(PrintLogEntry.energy_cost), 0))
        .select_from(PrintLogEntry)
        .where(_user_filter(PrintLogEntry.created_by_id, user_id))
    )
    total = float((await db.execute(stmt)).scalar() or 0)
    for threshold in sorted(COST_MILESTONES, reverse=True):
        key = f"cost-{threshold}"
        if total >= threshold and key not in seen:
            return Trigger(
                milestone=key,
                family="cost",
                threshold=threshold,
                payload={"total": round(total, 2)},
            )
    return None


async def _check_version_update(
    _db: AsyncSession, _user_id: int | None, _seen: set[str], state: SponsorToastState
) -> Trigger | None:
    # version-update is NOT in milestones_seen — it has its own state column
    # so it can re-fire on each major bump.
    if not APP_VERSION:
        return None
    last = state.last_seen_version
    if last is None:
        # First-ever read; treat as already-acknowledged so we don't toast
        # immediately on a brand-new install. Persist current version silently.
        state.last_seen_version = APP_VERSION
        return None
    if last == APP_VERSION:
        return None
    return Trigger(
        milestone="version-update",
        family="version-update",
        payload={"from": last, "to": APP_VERSION},
    )


# Priority order: most emotional / earned first; version-update is the soft fallback.
_CHECKS = (
    _check_anniversary,
    _check_prints,
    _check_archives,
    _check_cost,
    _check_version_update,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def evaluate(db: AsyncSession, user_id: int | None) -> Trigger | None:
    """Return the next eligible sponsor-toast trigger, or None."""
    state = await _get_or_create_state(db, user_id)
    if _within_cooldown(state):
        return None
    seen = _seen_milestones(state)
    for check in _CHECKS:
        trigger = await check(db, user_id, seen, state)
        if trigger is not None:
            return trigger
    # No triggers eligible — still commit any in-progress state changes
    # (e.g. version-update's first-touch persistence).
    await db.flush()
    return None


async def dismiss(db: AsyncSession, user_id: int | None, milestone: str) -> None:
    """Mark a milestone as shown (sets cooldown anchor + records seen)."""
    state = await _get_or_create_state(db, user_id)
    if milestone == "version-update":
        # Re-armable: just update last_seen_version, don't add to seen-list.
        state.last_seen_version = APP_VERSION
    else:
        seen = _seen_milestones(state)
        if milestone not in seen:
            seen.add(milestone)
            state.milestones_seen = json.dumps(sorted(seen))
    state.last_shown_at = datetime.now(timezone.utc)
    await db.flush()
