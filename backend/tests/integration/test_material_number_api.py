"""API coverage for the spool material number (#2870).

The material number is the internal purchasing identifier shared by all
spools of a product. Pinned here: CRUD round-trip, server-side normalisation,
inheritance on the create paths, the per-number statistics aggregate and its
dashboard timeframe, and the CSV round-trip.
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


class TestMaterialNumberNormalisation:
    """One validator on the schema, so every write path normalises (#2870).

    Without it "15" and "15 " are two groups in the statistics aggregate and
    two entries in the inventory filter chip, and the chip's exact match
    never finds the padded one.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_strips_surrounding_whitespace(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/inventory/spools",
            json={"material": "PLA", "material_number": "  15 "},
        )
        assert resp.status_code == 200
        assert resp.json()["material_number"] == "15"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_maps_blank_to_none(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/inventory/spools",
            json={"material": "PLA", "material_number": "   "},
        )
        assert resp.status_code == 200
        # NULL, not "" — "has no number" stays a single state to query for.
        assert resp.json()["material_number"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_strips_surrounding_whitespace(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory(material_number="15")

        resp = await async_client.patch(
            f"/api/v1/inventory/spools/{spool.id}",
            json={"material_number": " 16 "},
        )
        assert resp.status_code == 200
        assert resp.json()["material_number"] == "16"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_edit_strips_surrounding_whitespace(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory()

        resp = await async_client.post(
            "/api/v1/inventory/spools/bulk-update",
            json={"ids": [spool.id], "update": {"material_number": " 15 "}},
        )
        assert resp.status_code == 200

        listing = await async_client.get("/api/v1/inventory/spools")
        assert [s["material_number"] for s in listing.json()] == ["15"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_csv_import_strips_surrounding_whitespace(self, async_client: AsyncClient):
        csv = "material,brand,material_number\nPLA,Bambu Lab, 15 \n"
        resp = await async_client.post(
            "/api/v1/inventory/spools/import",
            files={"file": ("spools.csv", csv.encode("utf-8"), "text/csv")},
        )
        assert resp.status_code == 200, resp.text

        listing = await async_client.get("/api/v1/inventory/spools")
        assert [s["material_number"] for s in listing.json()] == ["15"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_padded_duplicate_does_not_become_a_second_group(self, async_client: AsyncClient, spool_factory):
        await async_client.post("/api/v1/inventory/spools", json={"material": "PLA", "material_number": "15"})
        await async_client.post("/api/v1/inventory/spools", json={"material": "PLA", "material_number": "15 "})

        resp = await async_client.get("/api/v1/inventory/stats/material-numbers")
        assert [r["material_number"] for r in resp.json()] == ["15"]
        assert resp.json()[0]["spool_count"] == 2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_blank_number_is_not_offered_as_a_group(self, async_client: AsyncClient):
        await async_client.post("/api/v1/inventory/spools", json={"material": "PLA", "material_number": "  "})

        resp = await async_client.get("/api/v1/inventory/stats/material-numbers")
        assert resp.json() == []


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
    async def test_an_over_consumed_spool_does_not_eat_its_siblings_stock(
        self, async_client: AsyncClient, spool_factory
    ):
        """Remaining stock is clamped per spool, not once over the group.

        weight_used above label_weight is reachable (a scale reading, an AMS
        sync, or a plain PATCH), and every other remaining-weight computation
        in the codebase clamps each spool at 0. Summing the raw difference
        first would subtract the overshoot from the other spools of the same
        number and report less stock than the inventory list does.
        """
        await spool_factory(material_number="15", label_weight=1000, weight_used=0)
        await spool_factory(material_number="15", color_name="Black", label_weight=1000, weight_used=1200)

        resp = await async_client.get("/api/v1/inventory/stats/material-numbers")
        assert resp.status_code == 200
        row = resp.json()[0]
        assert row["spool_count"] == 2
        assert row["remaining_g"] == pytest.approx(1000)

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


class TestMaterialNumberStatsTimeframe:
    """The widget sits in the stats dashboard, so it follows its timeframe.

    Usage history is the per-period half; stock is point-in-time and stays
    whole — "how much do I hold" has no date range.
    """

    @staticmethod
    async def _usage(db_session, spool_id, *, days_ago, grams, cost):
        from datetime import datetime, timedelta, timezone

        row = SpoolUsageHistory(
            spool_id=spool_id, weight_used=grams, percent_used=grams / 10, status="completed", cost=cost
        )
        # created_at is a server default, so set it explicitly to age the row.
        row.created_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(tzinfo=None)
        db_session.add(row)
        await db_session.commit()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_date_from_excludes_older_consumption(
        self, async_client: AsyncClient, spool_factory, db_session: AsyncSession
    ):
        from datetime import datetime, timedelta, timezone

        spool = await spool_factory(material_number="15", label_weight=1000, weight_used=400)
        await self._usage(db_session, spool.id, days_ago=200, grams=1000, cost=20.0)
        await self._usage(db_session, spool.id, days_ago=2, grams=10, cost=0.2)

        since = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
        resp = await async_client.get(f"/api/v1/inventory/stats/material-numbers?date_from={since}")
        assert resp.status_code == 200
        row = resp.json()[0]
        assert row["consumed_g"] == pytest.approx(10)
        assert row["cost"] == pytest.approx(0.2)
        # Stock is point-in-time: unaffected by the range.
        assert row["spool_count"] == 1
        assert row["remaining_g"] == pytest.approx(600)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_date_to_excludes_newer_consumption(
        self, async_client: AsyncClient, spool_factory, db_session: AsyncSession
    ):
        from datetime import datetime, timedelta, timezone

        spool = await spool_factory(material_number="15")
        await self._usage(db_session, spool.id, days_ago=200, grams=1000, cost=20.0)
        await self._usage(db_session, spool.id, days_ago=2, grams=10, cost=0.2)

        until = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
        resp = await async_client.get(f"/api/v1/inventory/stats/material-numbers?date_to={until}")
        assert resp.json()[0]["consumed_g"] == pytest.approx(1000)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_range_still_reports_lifetime_totals(
        self, async_client: AsyncClient, spool_factory, db_session: AsyncSession
    ):
        spool = await spool_factory(material_number="15")
        await self._usage(db_session, spool.id, days_ago=200, grams=1000, cost=20.0)
        await self._usage(db_session, spool.id, days_ago=2, grams=10, cost=0.2)

        resp = await async_client.get("/api/v1/inventory/stats/material-numbers")
        assert resp.json()[0]["consumed_g"] == pytest.approx(1010)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_number_with_no_usage_in_range_still_lists_its_stock(
        self, async_client: AsyncClient, spool_factory, db_session: AsyncSession
    ):
        from datetime import datetime, timedelta, timezone

        spool = await spool_factory(material_number="15", label_weight=1000, weight_used=250)
        await self._usage(db_session, spool.id, days_ago=200, grams=250, cost=5.0)

        since = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
        resp = await async_client.get(f"/api/v1/inventory/stats/material-numbers?date_from={since}")
        row = resp.json()[0]
        assert row["consumed_g"] == 0
        assert row["remaining_g"] == pytest.approx(750)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ties_sort_by_number_so_the_order_is_stable(
        self, async_client: AsyncClient, spool_factory, db_session: AsyncSession
    ):
        """Equal consumption has to fall back to the number, not to row order.

        The response is assembled in two passes — active spools first, then
        the numbers that only appear in usage history — so "16" (which has a
        live spool) is seeded before "15" (archived, usage only). Without the
        number tie-break the endpoint hands that seeding order straight back.
        """
        from datetime import datetime, timezone

        live = await spool_factory(material_number="16", color_name="Black")
        archived = await spool_factory(material_number="15", archived_at=datetime.now(timezone.utc))
        db_session.add_all(
            [
                SpoolUsageHistory(spool_id=live.id, weight_used=100, percent_used=10, status="completed", cost=2.0),
                SpoolUsageHistory(spool_id=archived.id, weight_used=100, percent_used=10, status="completed", cost=2.0),
            ]
        )
        await db_session.commit()

        resp = await async_client.get("/api/v1/inventory/stats/material-numbers")
        rows = resp.json()
        assert [r["consumed_g"] for r in rows] == [pytest.approx(100), pytest.approx(100)]
        assert [r["material_number"] for r in rows] == ["15", "16"]


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
