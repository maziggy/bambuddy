"""Unit tests for the sponsor-prompt trigger evaluator."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.sponsor_toast_state import SponsorToastState
from backend.app.models.user import User
from backend.app.services import sponsor_prompt as service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db: AsyncSession, *, username: str = "alice", created_days_ago: int = 0) -> User:
    user = User(username=username, role="admin")
    db.add(user)
    await db.flush()
    if created_days_ago:
        user.created_at = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
        await db.flush()
    return user


async def _add_completed_prints(db: AsyncSession, *, user_id: int | None, count: int, cost_each: float = 0.0) -> None:
    for _ in range(count):
        db.add(
            PrintLogEntry(
                status="completed",
                created_by_id=user_id,
                cost=cost_each if cost_each else None,
            )
        )
    await db.flush()


async def _add_archives(db: AsyncSession, *, user_id: int | None, count: int) -> None:
    for i in range(count):
        db.add(
            PrintArchive(
                filename=f"archive-{i}.zip",
                file_path=f"/tmp/archive-{i}.zip",  # nosec B108
                file_size=1024,
                created_by_id=user_id,
            )
        )
    await db.flush()


# ---------------------------------------------------------------------------
# Empty / no-eligibility cases
# ---------------------------------------------------------------------------


class TestEmptyState:
    @pytest.mark.asyncio
    async def test_evaluate_returns_none_for_fresh_user(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        trigger = await service.evaluate(db_session, user.id)
        assert trigger is None

    @pytest.mark.asyncio
    async def test_state_row_is_created_lazily(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        await service.evaluate(db_session, user.id)
        from sqlalchemy import select

        row = (
            await db_session.execute(select(SponsorToastState).where(SponsorToastState.user_id == user.id))
        ).scalar_one_or_none()
        assert row is not None
        assert row.milestones_seen == "[]"


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


class TestCooldown:
    @pytest.mark.asyncio
    async def test_no_toast_within_14d_window(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        await _add_completed_prints(db_session, user_id=user.id, count=200)
        # Pre-populate state with a recent last_shown_at
        state = SponsorToastState(
            user_id=user.id,
            last_shown_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
        db_session.add(state)
        await db_session.flush()
        trigger = await service.evaluate(db_session, user.id)
        assert trigger is None

    @pytest.mark.asyncio
    async def test_toast_eligible_after_14d_window(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        await _add_completed_prints(db_session, user_id=user.id, count=200)
        state = SponsorToastState(
            user_id=user.id,
            last_shown_at=datetime.now(timezone.utc) - timedelta(days=15),
        )
        db_session.add(state)
        await db_session.flush()
        trigger = await service.evaluate(db_session, user.id)
        assert trigger is not None
        assert trigger.family == "prints"


# ---------------------------------------------------------------------------
# Per-family triggers
# ---------------------------------------------------------------------------


class TestPrintMilestones:
    @pytest.mark.asyncio
    async def test_fires_at_100(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        await _add_completed_prints(db_session, user_id=user.id, count=100)
        trigger = await service.evaluate(db_session, user.id)
        assert trigger is not None
        assert trigger.milestone == "prints-100"
        assert trigger.threshold == 100

    @pytest.mark.asyncio
    async def test_picks_highest_unseen_milestone(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        await _add_completed_prints(db_session, user_id=user.id, count=600)
        trigger = await service.evaluate(db_session, user.id)
        # 500 is the highest crossed milestone (1000 not reached).
        assert trigger is not None
        assert trigger.milestone == "prints-500"

    @pytest.mark.asyncio
    async def test_skips_already_seen(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        await _add_completed_prints(db_session, user_id=user.id, count=600)
        # Mark prints-500 as already seen — but NOT prints-100.
        # Service should fall through to the next-largest unseen, which is prints-100.
        state = SponsorToastState(
            user_id=user.id,
            milestones_seen=json.dumps(["prints-500"]),
        )
        db_session.add(state)
        await db_session.flush()
        trigger = await service.evaluate(db_session, user.id)
        assert trigger is not None
        assert trigger.milestone == "prints-100"

    @pytest.mark.asyncio
    async def test_fires_at_lowest_threshold(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        await _add_completed_prints(db_session, user_id=user.id, count=10)
        trigger = await service.evaluate(db_session, user.id)
        assert trigger is not None
        assert trigger.milestone == "prints-10"
        assert trigger.threshold == 10

    @pytest.mark.asyncio
    async def test_failed_prints_dont_count(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        await _add_completed_prints(db_session, user_id=user.id, count=5)
        for _ in range(60):
            db_session.add(PrintLogEntry(status="failed", created_by_id=user.id))
        await db_session.flush()
        trigger = await service.evaluate(db_session, user.id)
        # Only 5 completed → below 10 threshold → no print trigger.
        # Anniversary not reached either; no other counter populated.
        assert trigger is None


class TestArchiveMilestones:
    @pytest.mark.asyncio
    async def test_fires_at_50(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        await _add_archives(db_session, user_id=user.id, count=50)
        trigger = await service.evaluate(db_session, user.id)
        assert trigger is not None
        assert trigger.milestone == "archives-50"


class TestCostMilestones:
    @pytest.mark.asyncio
    async def test_fires_when_cost_sum_crosses_100(self, db_session: AsyncSession):
        # 5 prints, cost ~21 each → 105 total. Below the 10-print threshold so
        # the prints family stays silent and cost gets a chance.
        user = await _make_user(db_session)
        await _add_completed_prints(db_session, user_id=user.id, count=5, cost_each=21.0)
        trigger = await service.evaluate(db_session, user.id)
        assert trigger is not None
        assert trigger.family == "cost"
        assert trigger.milestone == "cost-100"


class TestAnniversary:
    @pytest.mark.asyncio
    async def test_fires_after_1_year(self, db_session: AsyncSession):
        user = await _make_user(db_session, created_days_ago=370)
        trigger = await service.evaluate(db_session, user.id)
        assert trigger is not None
        assert trigger.milestone == "anniversary-1"
        assert trigger.family == "anniversary"

    @pytest.mark.asyncio
    async def test_does_not_fire_before_1_year(self, db_session: AsyncSession):
        user = await _make_user(db_session, created_days_ago=300)
        trigger = await service.evaluate(db_session, user.id)
        assert trigger is None


class TestVersionUpdate:
    @pytest.mark.asyncio
    async def test_first_read_silently_anchors(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        with patch.object(service, "APP_VERSION", "0.3.0"):
            trigger = await service.evaluate(db_session, user.id)
        assert trigger is None
        from sqlalchemy import select

        state = (
            await db_session.execute(select(SponsorToastState).where(SponsorToastState.user_id == user.id))
        ).scalar_one()
        assert state.last_seen_version == "0.3.0"

    @pytest.mark.asyncio
    async def test_fires_on_version_bump(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        state = SponsorToastState(user_id=user.id, last_seen_version="0.2.0")
        db_session.add(state)
        await db_session.flush()
        with patch.object(service, "APP_VERSION", "0.3.0"):
            trigger = await service.evaluate(db_session, user.id)
        assert trigger is not None
        assert trigger.milestone == "version-update"
        assert trigger.payload == {"from": "0.2.0", "to": "0.3.0"}


# ---------------------------------------------------------------------------
# Priority order
# ---------------------------------------------------------------------------


class TestPriorityOrder:
    @pytest.mark.asyncio
    async def test_anniversary_beats_prints(self, db_session: AsyncSession):
        # User old enough for anniversary AND with 100+ prints.
        user = await _make_user(db_session, created_days_ago=400)
        await _add_completed_prints(db_session, user_id=user.id, count=200)
        trigger = await service.evaluate(db_session, user.id)
        assert trigger is not None
        assert trigger.family == "anniversary"

    @pytest.mark.asyncio
    async def test_prints_beats_archives(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        await _add_completed_prints(db_session, user_id=user.id, count=200)
        await _add_archives(db_session, user_id=user.id, count=100)
        trigger = await service.evaluate(db_session, user.id)
        assert trigger is not None
        assert trigger.family == "prints"


# ---------------------------------------------------------------------------
# Dismiss
# ---------------------------------------------------------------------------


class TestDismiss:
    @pytest.mark.asyncio
    async def test_dismiss_adds_to_seen_and_anchors_cooldown(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        await _add_completed_prints(db_session, user_id=user.id, count=100)
        await service.evaluate(db_session, user.id)
        await service.dismiss(db_session, user.id, "prints-100")
        from sqlalchemy import select

        state = (
            await db_session.execute(select(SponsorToastState).where(SponsorToastState.user_id == user.id))
        ).scalar_one()
        assert "prints-100" in json.loads(state.milestones_seen)
        assert state.last_shown_at is not None
        # Re-evaluation must now return None (cooldown).
        next_trigger = await service.evaluate(db_session, user.id)
        assert next_trigger is None

    @pytest.mark.asyncio
    async def test_version_update_dismiss_updates_version_not_seen_list(self, db_session: AsyncSession):
        user = await _make_user(db_session)
        state = SponsorToastState(user_id=user.id, last_seen_version="0.2.0")
        db_session.add(state)
        await db_session.flush()
        with patch.object(service, "APP_VERSION", "0.3.0"):
            await service.dismiss(db_session, user.id, "version-update")
        from sqlalchemy import select

        state = (
            await db_session.execute(select(SponsorToastState).where(SponsorToastState.user_id == user.id))
        ).scalar_one()
        assert state.last_seen_version == "0.3.0"
        assert json.loads(state.milestones_seen) == []


# ---------------------------------------------------------------------------
# Auth-disabled (user_id = None) — NULL-keyed install-default row
# ---------------------------------------------------------------------------


class TestAuthDisabledMode:
    @pytest.mark.asyncio
    async def test_uses_install_anchor_for_anniversary(self, db_session: AsyncSession):
        # In auth-disabled mode, anniversary anchor = MIN(users.created_at).
        # Seed a user from >1 year ago.
        await _make_user(db_session, username="root", created_days_ago=400)
        # Prints written without created_by_id.
        await _add_completed_prints(db_session, user_id=None, count=10)
        trigger = await service.evaluate(db_session, None)
        assert trigger is not None
        assert trigger.family == "anniversary"

    @pytest.mark.asyncio
    async def test_null_keyed_counters_isolated_from_per_user(self, db_session: AsyncSession):
        # A user-attributed prints set should NOT show up in the install-default count.
        user = await _make_user(db_session, username="alice")
        await _add_completed_prints(db_session, user_id=user.id, count=200)
        # NULL-keyed install has zero prints.
        trigger = await service.evaluate(db_session, None)
        # No anniversary either (user only just created).
        assert trigger is None
