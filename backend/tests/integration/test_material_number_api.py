"""API coverage for the spool material number (#2870).

The material number is the internal purchasing identifier shared by all
spools of a product. Pinned here: CRUD round-trip, inheritance on the create
paths, the per-number statistics aggregate, and the CSV round-trip.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.spool_usage_history import SpoolUsageHistory


@pytest.fixture
async def spool_factory(db_session: AsyncSession):
    async def _create(**kwargs):
        defaults = {
            "material": "PLA",
            "subtype": "Basic",
            "brand": "Bambu Lab",
            "color_name": "Jade White",
            "rgba": "FFFFFFFF",
            "label_weight": 1000,
            "core_weight": 250,
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


class TestMaterialNumberCrud:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_persists_and_lists_material_number(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/inventory/spools",
            json={"material": "PLA", "material_number": "15"},
        )
        assert resp.status_code == 200
        assert resp.json()["material_number"] == "15"

        listing = await async_client.get("/api/v1/inventory/spools")
        assert listing.status_code == 200
        assert [s["material_number"] for s in listing.json()] == ["15"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_updates_material_number(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory(material_number="15")

        resp = await async_client.patch(
            f"/api/v1/inventory/spools/{spool.id}",
            json={"material_number": "16"},
        )
        assert resp.status_code == 200
        assert resp.json()["material_number"] == "16"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_material_number_longer_than_64_chars_is_rejected(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/inventory/spools",
            json={"material": "PLA", "material_number": "x" * 65},
        )
        assert resp.status_code == 422


class TestMaterialNumberInheritance:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_new_spool_of_same_product_inherits_number(self, async_client: AsyncClient, spool_factory):
        await spool_factory(material_number="15")

        resp = await async_client.post(
            "/api/v1/inventory/spools",
            json={
                "material": "PLA",
                "subtype": "Basic",
                "brand": "Bambu Lab",
                "color_name": "Jade White",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["material_number"] == "15"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_different_product_does_not_inherit(self, async_client: AsyncClient, spool_factory):
        await spool_factory(material_number="15")

        resp = await async_client.post(
            "/api/v1/inventory/spools",
            json={
                "material": "PLA",
                "subtype": "Basic",
                "brand": "Bambu Lab",
                "color_name": "Black",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["material_number"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_explicit_number_wins_over_inheritance(self, async_client: AsyncClient, spool_factory):
        await spool_factory(material_number="15")

        resp = await async_client.post(
            "/api/v1/inventory/spools",
            json={
                "material": "PLA",
                "subtype": "Basic",
                "brand": "Bambu Lab",
                "color_name": "Jade White",
                "material_number": "99",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["material_number"] == "99"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_create_inherits_number(self, async_client: AsyncClient, spool_factory):
        await spool_factory(material_number="15")

        resp = await async_client.post(
            "/api/v1/inventory/spools/bulk",
            json={
                "spool": {
                    "material": "PLA",
                    "subtype": "Basic",
                    "brand": "Bambu Lab",
                    "color_name": "Jade White",
                },
                "quantity": 3,
            },
        )
        assert resp.status_code == 200
        assert [s["material_number"] for s in resp.json()] == ["15", "15", "15"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archived_spool_still_provides_the_number(self, async_client: AsyncClient, spool_factory):
        from datetime import datetime, timezone

        await spool_factory(material_number="15", archived_at=datetime.now(timezone.utc))

        resp = await async_client.post(
            "/api/v1/inventory/spools",
            json={
                "material": "PLA",
                "subtype": "Basic",
                "brand": "Bambu Lab",
                "color_name": "Jade White",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["material_number"] == "15"


class TestMaterialNumberStats:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_stats_group_by_number(self, async_client: AsyncClient, spool_factory, db_session: AsyncSession):
        a = await spool_factory(material_number="15", label_weight=1000, weight_used=200)
        b = await spool_factory(material_number="15", label_weight=1000, weight_used=0)
        c = await spool_factory(material_number="16", color_name="Black", label_weight=1000, weight_used=500)
        await spool_factory(material_number=None, color_name="Gray")

        db_session.add_all(
            [
                SpoolUsageHistory(spool_id=a.id, weight_used=120, percent_used=12, status="completed", cost=2.4),
                SpoolUsageHistory(spool_id=b.id, weight_used=80, percent_used=8, status="completed", cost=1.6),
                SpoolUsageHistory(spool_id=c.id, weight_used=500, percent_used=50, status="failed", cost=15.0),
            ]
        )
        await db_session.commit()

        resp = await async_client.get("/api/v1/inventory/stats/material-numbers")
        assert resp.status_code == 200
        rows = {r["material_number"]: r for r in resp.json()}

        assert set(rows) == {"15", "16"}
        assert rows["15"]["spool_count"] == 2
        assert rows["15"]["remaining_g"] == pytest.approx(1800)
        assert rows["15"]["consumed_g"] == pytest.approx(200)
        assert rows["15"]["cost"] == pytest.approx(4.0)
        assert rows["16"]["consumed_g"] == pytest.approx(500)
        assert rows["16"]["cost"] == pytest.approx(15.0)
        # Heaviest consumption first.
        assert [r["material_number"] for r in resp.json()] == ["16", "15"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archived_spools_keep_their_recorded_consumption(
        self, async_client: AsyncClient, spool_factory, db_session: AsyncSession
    ):
        from datetime import datetime, timezone

        archived = await spool_factory(material_number="15", archived_at=datetime.now(timezone.utc))
        db_session.add(
            SpoolUsageHistory(spool_id=archived.id, weight_used=300, percent_used=30, status="completed", cost=6.0)
        )
        await db_session.commit()

        resp = await async_client.get("/api/v1/inventory/stats/material-numbers")
        assert resp.status_code == 200
        rows = {r["material_number"]: r for r in resp.json()}
        # No active spools carry the number, but the consumption is still there.
        assert rows["15"]["spool_count"] == 0
        assert rows["15"]["remaining_g"] == 0
        assert rows["15"]["consumed_g"] == pytest.approx(300)


class TestMaterialNumberCsv:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_export_import_round_trip(self, async_client: AsyncClient, spool_factory, db_session: AsyncSession):
        await spool_factory(material_number="15")

        export = await async_client.get("/api/v1/inventory/spools/export")
        assert export.status_code == 200
        text = export.text
        header = text.splitlines()[0]
        assert "material_number" in header.split(",")
        assert ",15" in text.splitlines()[1] or text.splitlines()[1].endswith("15")

        # Wipe and re-import: the number must survive the round trip.
        from sqlalchemy import delete

        await db_session.execute(delete(Spool))
        await db_session.commit()

        imported = await async_client.post(
            "/api/v1/inventory/spools/import",
            files={"file": ("spools.csv", text.encode("utf-8"), "text/csv")},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["created"] == 1

        listing = await async_client.get("/api/v1/inventory/spools")
        assert [s["material_number"] for s in listing.json()] == ["15"]
