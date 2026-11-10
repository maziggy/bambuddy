"""API coverage for a spool's batch ID, owner and purchase date (#2828, #1301, #3408).

Pinned here: create / update / list round-trip, the server-side normalisation
every write path shares, the purchase date being a plain day, bulk edit, and
the CSV round-trip.
"""

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool


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


class TestCrud:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_persists_and_lists_the_fields(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/inventory/spools",
            json={"material": "PLA", "batch_id": "LOT-2411-07", "owner": "Alice", "purchase_date": "2026-03-14"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["batch_id"], body["owner"], body["purchase_date"]) == ("LOT-2411-07", "Alice", "2026-03-14")

        listing = await async_client.get("/api/v1/inventory/spools")
        assert [(s["batch_id"], s["owner"], s["purchase_date"]) for s in listing.json()] == [
            ("LOT-2411-07", "Alice", "2026-03-14")
        ]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_spool_without_them_has_none(self, async_client: AsyncClient):
        resp = await async_client.post("/api/v1/inventory/spools", json={"material": "PLA"})

        body = resp.json()
        assert (body["batch_id"], body["owner"], body["purchase_date"]) == (None, None, None)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_updates_and_clears_them(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory(batch_id="A1", owner="Bob", purchase_date=date(2026, 1, 2))

        changed = await async_client.patch(
            f"/api/v1/inventory/spools/{spool.id}",
            json={"batch_id": "B2", "owner": "Carol", "purchase_date": "2026-02-03"},
        )
        assert changed.status_code == 200, changed.text
        assert (changed.json()["batch_id"], changed.json()["owner"], changed.json()["purchase_date"]) == (
            "B2",
            "Carol",
            "2026-02-03",
        )

        cleared = await async_client.patch(
            f"/api/v1/inventory/spools/{spool.id}",
            json={"batch_id": None, "owner": None, "purchase_date": None},
        )
        assert cleared.status_code == 200
        assert (cleared.json()["batch_id"], cleared.json()["owner"], cleared.json()["purchase_date"]) == (
            None,
            None,
            None,
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patching_something_else_leaves_them_alone(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory(batch_id="A1", owner="Bob", purchase_date=date(2026, 1, 2))

        resp = await async_client.patch(f"/api/v1/inventory/spools/{spool.id}", json={"note": "hello"})

        body = resp.json()
        assert (body["batch_id"], body["owner"], body["purchase_date"]) == ("A1", "Bob", "2026-01-02")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_too_long_values_are_rejected(self, async_client: AsyncClient):
        long_batch = await async_client.post("/api/v1/inventory/spools", json={"material": "PLA", "batch_id": "x" * 65})
        long_owner = await async_client.post("/api/v1/inventory/spools", json={"material": "PLA", "owner": "x" * 101})

        assert long_batch.status_code == 422
        assert long_owner.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_purchase_date_must_be_a_date(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/inventory/spools", json={"material": "PLA", "purchase_date": "last tuesday"}
        )

        assert resp.status_code == 422


class TestNormalisation:
    """One validator on the schema, so "Alice" and "Alice " cannot become two owners."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_trims_and_maps_blank_to_none(self, async_client: AsyncClient):
        padded = await async_client.post(
            "/api/v1/inventory/spools", json={"material": "PLA", "batch_id": "  L1 ", "owner": " Alice  "}
        )
        blank = await async_client.post(
            "/api/v1/inventory/spools", json={"material": "PLA", "batch_id": "   ", "owner": ""}
        )

        assert (padded.json()["batch_id"], padded.json()["owner"]) == ("L1", "Alice")
        # NULL, not "": "has none" stays a single state.
        assert (blank.json()["batch_id"], blank.json()["owner"]) == (None, None)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_patch_trims(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory()

        resp = await async_client.patch(f"/api/v1/inventory/spools/{spool.id}", json={"owner": " Bob "})

        assert resp.json()["owner"] == "Bob"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_padded_value_is_held_to_the_stored_length(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/inventory/spools", json={"material": "PLA", "owner": " " + "x" * 100 + " "}
        )

        assert resp.status_code == 200
        assert resp.json()["owner"] == "x" * 100


class TestBulkEdit:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_one_edit_sets_them_on_every_selected_spool(self, async_client: AsyncClient, spool_factory):
        first, second, other = await spool_factory(), await spool_factory(), await spool_factory()

        resp = await async_client.post(
            "/api/v1/inventory/spools/bulk-update",
            json={
                "ids": [first.id, second.id],
                "update": {"owner": " Alice ", "batch_id": "LOT-9", "purchase_date": "2026-04-05"},
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["updated"] == 2

        by_id = {s["id"]: s for s in (await async_client.get("/api/v1/inventory/spools")).json()}
        for spool_id in (first.id, second.id):
            assert (by_id[spool_id]["owner"], by_id[spool_id]["batch_id"], by_id[spool_id]["purchase_date"]) == (
                "Alice",
                "LOT-9",
                "2026-04-05",
            )
        assert by_id[other.id]["owner"] is None


class TestCsv:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_export_has_the_columns(self, async_client: AsyncClient, spool_factory):
        await spool_factory(batch_id="LOT-1", owner="Alice", purchase_date=date(2026, 3, 14))

        text = (await async_client.get("/api/v1/inventory/spools/export")).text

        header = text.splitlines()[0].split(",")
        assert {"batch_id", "owner", "purchase_date"} <= set(header)
        assert "LOT-1" in text and "Alice" in text and "2026-03-14" in text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_round_trip_keeps_them(self, async_client: AsyncClient, db_session: AsyncSession, spool_factory):
        await spool_factory(batch_id="LOT-1", owner="Alice", purchase_date=date(2026, 3, 14))
        text = (await async_client.get("/api/v1/inventory/spools/export")).text
        await db_session.execute(delete(Spool))
        await db_session.commit()

        imported = await async_client.post(
            "/api/v1/inventory/spools/import",
            files={"file": ("spools.csv", text.encode("utf-8"), "text/csv")},
        )

        assert imported.status_code == 200, imported.text
        assert imported.json()["created"] == 1
        spool = (await db_session.execute(select(Spool))).scalars().one()
        assert (spool.batch_id, spool.owner, spool.purchase_date) == ("LOT-1", "Alice", date(2026, 3, 14))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_import_trims_and_reads_a_timestamp_as_its_day(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        csv = "material,batch_id,owner,purchase_date\nPLA, L1 , Alice ,2026-03-14T08:30:00\n"

        resp = await async_client.post(
            "/api/v1/inventory/spools/import", files={"file": ("spools.csv", csv.encode("utf-8"), "text/csv")}
        )

        assert resp.status_code == 200, resp.text
        spool = (await db_session.execute(select(Spool))).scalars().one()
        assert (spool.batch_id, spool.owner, spool.purchase_date) == ("L1", "Alice", date(2026, 3, 14))

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_import_refuses_a_bad_date_by_row_and_keeps_the_others(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        csv = "material,purchase_date\nPLA,14/03/2026\nPETG,2026-03-14\n"

        resp = await async_client.post(
            "/api/v1/inventory/spools/import", files={"file": ("spools.csv", csv.encode("utf-8"), "text/csv")}
        )

        body = resp.json()
        assert (body["created"], body["errors"]) == (1, 1)
        assert "purchase_date" in body["error_rows"][0]["reason"]
        assert [s.material for s in (await db_session.execute(select(Spool))).scalars().all()] == ["PETG"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_file_without_the_columns_still_imports(self, async_client: AsyncClient, db_session: AsyncSession):
        """CSVs exported before these columns existed must keep working."""
        csv = "material,brand\nPLA,Polymaker\n"

        resp = await async_client.post(
            "/api/v1/inventory/spools/import", files={"file": ("spools.csv", csv.encode("utf-8"), "text/csv")}
        )

        assert resp.json()["created"] == 1
        spool = (await db_session.execute(select(Spool))).scalars().one()
        assert (spool.batch_id, spool.owner, spool.purchase_date) == (None, None, None)
