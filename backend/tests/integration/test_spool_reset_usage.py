"""Reset-consumed-counter endpoint regressions (#1390 follow-up).

Endpoint paths renamed from ``/reset-usage`` to ``/reset-consumed-counter``
to match what the endpoint actually does (the previous name implied
``weight_used`` itself would drop to 0, which surprised callers reading
the JSON response — see the discussion that drove this rename).

The per-spool and bulk reset endpoints stamp `weight_used_baseline =
weight_used` instead of zeroing `weight_used` directly. This decouples
the resettable "Total Consumed" display (computed as
`weight_used - weight_used_baseline`) from remaining
(`label_weight - weight_used`), so resetting the counter does NOT
inflate remaining back to label_weight (which is what the previous
implementation did — see the report at the end of #1390).

`weight_locked` is left alone in both modes; the spool keeps receiving
AMS auto-sync updates from the next print onward.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool


@pytest.fixture
async def spool_factory(db_session: AsyncSession):
    """Create a Spool with sensible defaults."""

    async def _create(**kwargs):
        defaults = {
            "material": "PLA",
            "subtype": "Basic",
            "brand": "Bambu",
            "color_name": "Red",
            "rgba": "FF0000FF",
            "label_weight": 1000,
            "weight_used": 0,
            "weight_used_baseline": 0,
            "weight_locked": False,
        }
        defaults.update(kwargs)
        spool = Spool(**defaults)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)
        return spool

    return _create


class TestResetSpoolUsage:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reset_stamps_baseline_without_touching_weight_used(
        self, async_client: AsyncClient, spool_factory, db_session
    ):
        """Reset stamps baseline = weight_used; remaining stays the same.

        Pre-bug behaviour zeroed weight_used and made
        `label_weight - weight_used` (the displayed remaining) jump back
        to label_weight — a 456 g spool would suddenly read 1000 g.
        """
        spool = await spool_factory(label_weight=1000, weight_used=456.0)

        response = await async_client.post(f"/api/v1/inventory/spools/{spool.id}/reset-consumed-counter")

        assert response.status_code == 200
        body = response.json()
        assert body["weight_used"] == 456.0, "weight_used must NOT be zeroed (drives remaining)"
        assert body["weight_used_baseline"] == 456.0, "baseline must equal pre-reset weight_used"
        # Displayed consumed = weight_used - baseline = 0
        assert body["weight_used"] - body["weight_used_baseline"] == 0
        # Displayed remaining = label_weight - weight_used = 544 (unchanged)
        assert body["label_weight"] - body["weight_used"] == 544

        await db_session.refresh(spool)
        assert spool.weight_used == 456.0
        assert spool.weight_used_baseline == 456.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reset_does_not_lock_spool(self, async_client: AsyncClient, spool_factory, db_session):
        """Reset must leave weight_locked alone.

        PATCH /spools/{id} auto-locks when weight_used is set explicitly;
        the dedicated reset endpoint must NOT, because the user's intent
        is "track fresh from zero", not "freeze at zero forever".
        """
        spool = await spool_factory(weight_used=100.0, weight_locked=False)

        response = await async_client.post(f"/api/v1/inventory/spools/{spool.id}/reset-consumed-counter")

        assert response.status_code == 200
        await db_session.refresh(spool)
        assert spool.weight_used == 100.0
        assert spool.weight_used_baseline == 100.0
        assert spool.weight_locked is False, "Reset must not auto-lock the spool"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reset_preserves_existing_lock(self, async_client: AsyncClient, spool_factory, db_session):
        """If the user previously locked the spool, the lock is preserved."""
        spool = await spool_factory(weight_used=500.0, weight_locked=True)

        response = await async_client.post(f"/api/v1/inventory/spools/{spool.id}/reset-consumed-counter")

        assert response.status_code == 200
        await db_session.refresh(spool)
        assert spool.weight_used == 500.0
        assert spool.weight_used_baseline == 500.0
        assert spool.weight_locked is True, "Pre-existing lock must be preserved"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reset_then_print_advances_only_the_counter(
        self, async_client: AsyncClient, spool_factory, db_session
    ):
        """After reset, a subsequent print delta shows up in the consumed
        counter while remaining keeps decrementing normally.
        """
        spool = await spool_factory(label_weight=1000, weight_used=456.0)
        await async_client.post(f"/api/v1/inventory/spools/{spool.id}/reset-consumed-counter")

        # Simulate a 50g print (usage_tracker increments weight_used).
        await db_session.refresh(spool)
        spool.weight_used = (spool.weight_used or 0) + 50.0
        await db_session.commit()

        await db_session.refresh(spool)
        consumed = spool.weight_used - spool.weight_used_baseline
        remaining = spool.label_weight - spool.weight_used
        assert consumed == 50.0, "Consumed counter reflects only post-reset usage"
        assert remaining == 494, "Remaining tracks physical depletion across reset"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reset_404_for_missing_spool(self, async_client: AsyncClient):
        response = await async_client.post("/api/v1/inventory/spools/99999/reset-consumed-counter")
        assert response.status_code == 404


class TestBulkResetSpoolUsage:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_reset_stamps_baseline_only_for_listed_spools(
        self, async_client: AsyncClient, spool_factory, db_session
    ):
        """Only spools in the request are reset; others are untouched."""
        target1 = await spool_factory(weight_used=100.0)
        target2 = await spool_factory(weight_used=200.0)
        untouched = await spool_factory(weight_used=300.0)

        response = await async_client.post(
            "/api/v1/inventory/spools/reset-consumed-counter-bulk",
            json={"spool_ids": [target1.id, target2.id]},
        )

        assert response.status_code == 200
        assert response.json() == {"reset": 2}

        # The endpoint commits via its own session — expire our session so the
        # next read pulls fresh values rather than the cached pre-reset state.
        db_session.expire_all()
        spools = (await db_session.execute(select(Spool))).scalars().all()
        by_id = {s.id: s for s in spools}
        assert by_id[target1.id].weight_used == 100.0
        assert by_id[target1.id].weight_used_baseline == 100.0
        assert by_id[target2.id].weight_used == 200.0
        assert by_id[target2.id].weight_used_baseline == 200.0
        assert by_id[untouched.id].weight_used == 300.0, "Spool not in request must keep its usage"
        assert by_id[untouched.id].weight_used_baseline == 0, "Untouched baseline must stay at 0"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_reset_rejects_empty_list(self, async_client: AsyncClient):
        """Empty list must be rejected — guards against accidental wildcard wipes."""
        response = await async_client.post(
            "/api/v1/inventory/spools/reset-consumed-counter-bulk",
            json={"spool_ids": []},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_reset_rejects_missing_field(self, async_client: AsyncClient):
        """Missing spool_ids field must be rejected."""
        response = await async_client.post(
            "/api/v1/inventory/spools/reset-consumed-counter-bulk",
            json={},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_reset_does_not_lock_spools(self, async_client: AsyncClient, spool_factory, db_session):
        """Bulk reset preserves weight_locked across all targets."""
        unlocked = await spool_factory(weight_used=100.0, weight_locked=False)
        locked = await spool_factory(weight_used=200.0, weight_locked=True)

        response = await async_client.post(
            "/api/v1/inventory/spools/reset-consumed-counter-bulk",
            json={"spool_ids": [unlocked.id, locked.id]},
        )

        assert response.status_code == 200
        await db_session.refresh(unlocked)
        await db_session.refresh(locked)
        assert (
            unlocked.weight_used == 100.0 and unlocked.weight_used_baseline == 100.0 and unlocked.weight_locked is False
        )
        assert locked.weight_used == 200.0 and locked.weight_used_baseline == 200.0 and locked.weight_locked is True
