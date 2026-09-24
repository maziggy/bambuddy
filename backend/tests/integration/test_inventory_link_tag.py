"""Conflict handling on PATCH /api/v1/inventory/spools/{id}/link-tag (#3110).

The route loaded the conflicting spool row and then threw it away, refusing
with a bare "already linked to another active spool" -- so a client could not
tell which spool to look at, and could not offer to move the tag. It also read
that row with ``scalar_one_or_none()``, which raises ``MultipleResultsFound``
when two active spools carry one tag. Nothing prevents that duplicate: no
unique index, no conflict check on PATCH /spools/{id}, and POST /spools/bulk
copies one tag into every row it creates.

That exception escapes the route into the auth middleware's fail-closed
``except Exception`` (main.py:9685), so the caller does not even get a 500 --
they get 503 "Authentication service temporarily unavailable" for a request
that has nothing to do with auth. The middleware is right to fail closed
(GHSA-6mf4-q26m-47pv); the route is what must not raise.
"""

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool

TAG = "AABBCCDD"
TRAY_UUID = "AABBCCDDEEFF0011AABBCCDDEEFF0011"


@pytest.fixture
async def spool_factory(db_session: AsyncSession):
    async def _create_spool(**kwargs):
        defaults = {
            "material": "PLA",
            "subtype": "Basic",
            "brand": "Devil Design",
            "rgba": "FF0000FF",
            "label_weight": 1000,
            "weight_used": 0,
        }
        defaults.update(kwargs)
        spool = Spool(**defaults)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)
        return spool

    return _create_spool


class TestLinkTagNamesTheHolder:
    """The 409 carries the id the route already had in hand."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tag_uid_conflict_names_the_spool_holding_it(self, async_client: AsyncClient, spool_factory):
        holder = await spool_factory(tag_uid=TAG)
        target = await spool_factory()

        resp = await async_client.patch(f"/api/v1/inventory/spools/{target.id}/link-tag", json={"tag_uid": TAG})

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "tag_already_linked"
        assert detail["spool_id"] == holder.id
        assert detail["field"] == "tag_uid"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tray_uuid_conflict_names_the_spool_holding_it(self, async_client: AsyncClient, spool_factory):
        holder = await spool_factory(tray_uuid=TRAY_UUID)
        target = await spool_factory()

        resp = await async_client.patch(f"/api/v1/inventory/spools/{target.id}/link-tag", json={"tray_uuid": TRAY_UUID})

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["spool_id"] == holder.id
        # Which identifier collided, so a client knows what it would be moving.
        assert detail["field"] == "tray_uuid"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_free_tag_still_links(self, async_client: AsyncClient, spool_factory):
        """Regression guard: the conflict rewrite must not refuse a clean link."""
        target = await spool_factory()

        resp = await async_client.patch(f"/api/v1/inventory/spools/{target.id}/link-tag", json={"tag_uid": TAG})

        assert resp.status_code == 200
        assert resp.json()["tag_uid"] == TAG

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_archived_holder_still_yields_the_tag(self, async_client: AsyncClient, spool_factory):
        """Regression guard: tag recycling off archived spools is untouched."""
        archived = await spool_factory(tag_uid=TAG, archived_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        target = await spool_factory()

        resp = await async_client.patch(f"/api/v1/inventory/spools/{target.id}/link-tag", json={"tag_uid": TAG})

        assert resp.status_code == 200
        reread = await async_client.get(f"/api/v1/inventory/spools/{archived.id}")
        assert reread.json()["tag_uid"] is None


class TestLinkTagDuplicateHolders:
    """Two active spools on one tag is a 409 naming the lowest id, not a crash."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_duplicate_tag_uid_holders_yield_a_409_not_a_crash(self, async_client: AsyncClient, spool_factory):
        first = await spool_factory(tag_uid=TAG)
        await spool_factory(tag_uid=TAG)
        target = await spool_factory()

        resp = await async_client.patch(f"/api/v1/inventory/spools/{target.id}/link-tag", json={"tag_uid": TAG})

        assert resp.status_code == 409
        assert resp.json()["detail"]["spool_id"] == first.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_duplicate_tray_uuid_holders_yield_a_409_not_a_crash(self, async_client: AsyncClient, spool_factory):
        first = await spool_factory(tray_uuid=TRAY_UUID)
        await spool_factory(tray_uuid=TRAY_UUID)
        target = await spool_factory()

        resp = await async_client.patch(f"/api/v1/inventory/spools/{target.id}/link-tag", json={"tray_uuid": TRAY_UUID})

        assert resp.status_code == 409
        assert resp.json()["detail"]["spool_id"] == first.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_create_is_one_route_to_that_duplicate(self, async_client: AsyncClient, spool_factory):
        """POST /spools/bulk copies a single payload -- tag included -- N times.

        Reached through the API rather than the fixture, so the duplicate is
        shown to be a state the app itself produces, not one only a test can
        stage.
        """
        created = await async_client.post(
            "/api/v1/inventory/spools/bulk",
            json={"quantity": 2, "spool": {"material": "PLA", "label_weight": 1000, "tag_uid": TAG}},
        )
        assert created.status_code in (200, 201)
        ids = sorted(s["id"] for s in created.json())
        assert len(ids) == 2

        target = await spool_factory()
        resp = await async_client.patch(f"/api/v1/inventory/spools/{target.id}/link-tag", json={"tag_uid": TAG})

        assert resp.status_code == 409
        assert resp.json()["detail"]["spool_id"] == ids[0]
