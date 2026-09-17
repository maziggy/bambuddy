"""API coverage for the supplier master list and spool assignments (#2988).

The master list lives under /api/v1/inventory/suppliers (Locations pattern),
gated by the plain inventory permissions. Assignments exist for both
inventories: `spool_suppliers` for built-in spools and the
`spoolman_spool_suppliers` twin keyed by the remote spool id.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.spool_usage_history import SpoolUsageHistory
from backend.app.models.supplier import SpoolmanSpoolSupplier, SpoolSupplier, Supplier

SAMPLE_SPOOLMAN_SPOOL = {
    "id": 7,
    "filament": {
        "id": 1,
        "name": "PETG CF",
        "material": "PETG",
        "weight": 1000,
        "color_hex": "000000",
        "vendor": {"id": 1, "name": "BrandX"},
    },
    "remaining_weight": 600.0,
    "used_weight": 400.0,
    "location": None,
    "comment": None,
    "first_used": None,
    "last_used": None,
    "registered": "2024-01-01T00:00:00+00:00",
    "archived": False,
    "price": None,
    "extra": {},
}


@pytest.fixture
async def spool_factory(db_session: AsyncSession):
    async def _create(**kwargs):
        defaults = {
            "material": "PLA",
            "subtype": "Matte",
            "brand": "Bambu Lab",
            "color_name": "Charcoal",
            "rgba": "333333FF",
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


@pytest.fixture
async def supplier_factory(db_session: AsyncSession):
    _counter = [0]

    async def _create(**kwargs):
        _counter[0] += 1
        defaults = {"name": f"Supplier {_counter[0]}"}
        defaults.update(kwargs)
        supplier = Supplier(**defaults)
        db_session.add(supplier)
        await db_session.commit()
        await db_session.refresh(supplier)
        return supplier

    return _create


@pytest.fixture
async def spoolman_settings(db_session: AsyncSession):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


@pytest.fixture
def mock_spoolman_client():
    client = MagicMock()
    client.base_url = "http://localhost:7912"
    client.health_check = AsyncMock(return_value=True)
    client.get_spool = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    client.get_all_spools = AsyncMock(return_value=[SAMPLE_SPOOLMAN_SPOOL])
    client.get_distinct_locations = AsyncMock(return_value=[])

    with patch(
        "backend.app.api.routes.spoolman_inventory._get_client",
        AsyncMock(return_value=client),
    ):
        yield client


class TestSupplierCrud:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_and_list(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/inventory/suppliers",
            json={"name": "Filament24", "website": "https://filament24.example", "customer_number": "C-1042"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Filament24"
        assert body["spool_count"] == 0

        listing = await async_client.get("/api/v1/inventory/suppliers")
        assert listing.status_code == 200
        assert [s["name"] for s in listing.json()] == ["Filament24"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update(self, async_client: AsyncClient, supplier_factory):
        supplier = await supplier_factory(name="Old Name")
        resp = await async_client.patch(f"/api/v1/inventory/suppliers/{supplier.id}", json={"name": "New Name"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_unreferenced(self, async_client: AsyncClient, supplier_factory):
        supplier = await supplier_factory()
        resp = await async_client.delete(f"/api/v1/inventory/suppliers/{supplier.id}")
        assert resp.status_code == 200
        assert (await async_client.get("/api/v1/inventory/suppliers")).json() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_referenced_is_refused(
        self, async_client: AsyncClient, supplier_factory, spool_factory, db_session: AsyncSession
    ):
        supplier = await supplier_factory()
        spool = await spool_factory()
        db_session.add(SpoolSupplier(spool_id=spool.id, supplier_id=supplier.id))
        await db_session.commit()

        resp = await async_client.delete(f"/api/v1/inventory/suppliers/{supplier.id}")
        assert resp.status_code == 409
        assert "cannot be deleted" in resp.json()["detail"]

        # The listing surfaces the usage count behind the refusal.
        listing = await async_client.get("/api/v1/inventory/suppliers")
        assert listing.json()[0]["spool_count"] == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_refused_for_spoolman_only_reference(
        self, async_client: AsyncClient, supplier_factory, db_session: AsyncSession
    ):
        """A supplier used only by Spoolman-mode assignments is still protected."""
        supplier = await supplier_factory()
        db_session.add(SpoolmanSpoolSupplier(spoolman_spool_id=7, supplier_id=supplier.id))
        await db_session.commit()

        resp = await async_client.delete(f"/api/v1/inventory/suppliers/{supplier.id}")
        assert resp.status_code == 409
        assert (await async_client.get("/api/v1/inventory/suppliers")).json()[0]["spool_count"] == 1


class TestSpoolSupplierAssignments:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_replace_all_and_embed_in_spool_response(
        self, async_client: AsyncClient, supplier_factory, spool_factory
    ):
        a = await supplier_factory(name="Supplier A")
        b = await supplier_factory(name="Supplier B")
        spool = await spool_factory()

        resp = await async_client.put(
            f"/api/v1/inventory/spools/{spool.id}/suppliers",
            json=[
                {
                    "supplier_id": a.id,
                    "supplier_article_number": "A-100",
                    "quoted_price_per_kg": 19.99,
                    "is_purchase_source": True,
                },
                {"supplier_id": b.id, "quoted_price_per_kg": 22.5},
            ],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert {row["supplier_name"] for row in body} == {"Supplier A", "Supplier B"}
        assert [row["is_purchase_source"] for row in sorted(body, key=lambda r: r["supplier_id"])] == [True, False]

        # Embedded in the inventory listing.
        listing = await async_client.get("/api/v1/inventory/spools")
        spool_row = next(s for s in listing.json() if s["id"] == spool.id)
        assert {row["supplier_name"] for row in spool_row["suppliers"]} == {"Supplier A", "Supplier B"}

        # Replace-all: shrinking the list removes the other assignment.
        resp = await async_client.put(
            f"/api/v1/inventory/spools/{spool.id}/suppliers",
            json=[{"supplier_id": b.id, "is_purchase_source": True}],
        )
        assert resp.status_code == 200
        assert [row["supplier_name"] for row in resp.json()] == ["Supplier B"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_two_purchase_sources_are_refused(self, async_client: AsyncClient, supplier_factory, spool_factory):
        a = await supplier_factory()
        b = await supplier_factory()
        spool = await spool_factory()

        resp = await async_client.put(
            f"/api/v1/inventory/spools/{spool.id}/suppliers",
            json=[
                {"supplier_id": a.id, "is_purchase_source": True},
                {"supplier_id": b.id, "is_purchase_source": True},
            ],
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_supplier_is_refused(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory()
        resp = await async_client.put(
            f"/api/v1/inventory/spools/{spool.id}/suppliers",
            json=[{"supplier_id": 999999}],
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_duplicate_supplier_is_refused(self, async_client: AsyncClient, supplier_factory, spool_factory):
        a = await supplier_factory()
        spool = await spool_factory()
        resp = await async_client.put(
            f"/api/v1/inventory/spools/{spool.id}/suppliers",
            json=[{"supplier_id": a.id}, {"supplier_id": a.id}],
        )
        assert resp.status_code == 400


class TestSpoolmanSupplierAssignments:
    """Spoolman parity (#2988): same endpoints, same shape, twin table."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_replace_all_and_embed(
        self, async_client: AsyncClient, supplier_factory, spoolman_settings, mock_spoolman_client
    ):
        a = await supplier_factory(name="Supplier A")

        resp = await async_client.put(
            "/api/v1/spoolman/inventory/spools/7/suppliers",
            json=[
                {
                    "supplier_id": a.id,
                    "supplier_article_number": "A-100",
                    "quoted_price_per_kg": 19.99,
                    "is_purchase_source": True,
                }
            ],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["supplier_name"] == "Supplier A"
        assert body[0]["quoted_price_per_kg"] == 19.99

        # Same rows via GET, embedded in the single-spool and list responses.
        assert (await async_client.get("/api/v1/spoolman/inventory/spools/7/suppliers")).json() == body
        single = await async_client.get("/api/v1/spoolman/inventory/spools/7")
        assert [row["supplier_name"] for row in single.json()["suppliers"]] == ["Supplier A"]
        listing = await async_client.get("/api/v1/spoolman/inventory/spools")
        spool_row = next(s for s in listing.json() if s["id"] == 7)
        assert [row["supplier_name"] for row in spool_row["suppliers"]] == ["Supplier A"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_validation_matches_builtin_inventory(
        self, async_client: AsyncClient, supplier_factory, spoolman_settings, mock_spoolman_client
    ):
        a = await supplier_factory()
        b = await supplier_factory()

        resp = await async_client.put(
            "/api/v1/spoolman/inventory/spools/7/suppliers",
            json=[{"supplier_id": a.id, "is_purchase_source": True}, {"supplier_id": b.id, "is_purchase_source": True}],
        )
        assert resp.status_code == 400

        resp = await async_client.put(
            "/api/v1/spoolman/inventory/spools/7/suppliers",
            json=[{"supplier_id": 999999}],
        )
        assert resp.status_code == 404


class TestSupplierInheritance:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_new_spool_of_same_product_inherits_sources(
        self, async_client: AsyncClient, supplier_factory, spool_factory, db_session: AsyncSession
    ):
        supplier = await supplier_factory(name="Supplier A")
        donor = await spool_factory()
        db_session.add(
            SpoolSupplier(
                spool_id=donor.id,
                supplier_id=supplier.id,
                supplier_article_number="A-100",
                quoted_price_per_kg=19.99,
                is_purchase_source=True,
            )
        )
        await db_session.commit()

        resp = await async_client.post(
            "/api/v1/inventory/spools",
            json={"material": "PLA", "subtype": "Matte", "brand": "Bambu Lab", "color_name": "Charcoal"},
        )
        assert resp.status_code == 200
        suppliers = resp.json()["suppliers"]
        assert [row["supplier_name"] for row in suppliers] == ["Supplier A"]
        assert suppliers[0]["supplier_article_number"] == "A-100"
        assert suppliers[0]["quoted_price_per_kg"] == 19.99
        # Where THIS spool was bought is unknown — never inherited.
        assert suppliers[0]["is_purchase_source"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_different_product_inherits_nothing(
        self, async_client: AsyncClient, supplier_factory, spool_factory, db_session: AsyncSession
    ):
        supplier = await supplier_factory()
        donor = await spool_factory()
        db_session.add(SpoolSupplier(spool_id=donor.id, supplier_id=supplier.id))
        await db_session.commit()

        resp = await async_client.post(
            "/api/v1/inventory/spools",
            json={"material": "PETG", "subtype": "Matte", "brand": "Bambu Lab", "color_name": "Charcoal"},
        )
        assert resp.status_code == 200
        assert resp.json()["suppliers"] == []


class TestSupplierStats:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_groups_by_purchase_source(
        self, async_client: AsyncClient, supplier_factory, spool_factory, db_session: AsyncSession
    ):
        a = await supplier_factory(name="Supplier A")
        b = await supplier_factory(name="Supplier B")
        bought_at_a = await spool_factory(label_weight=1000, weight_used=200)
        alt_only = await spool_factory(color_name="Red")
        db_session.add_all(
            [
                SpoolSupplier(spool_id=bought_at_a.id, supplier_id=a.id, is_purchase_source=True),
                # Alternative source only — must NOT count toward supplier B.
                SpoolSupplier(spool_id=alt_only.id, supplier_id=b.id, is_purchase_source=False),
                SpoolUsageHistory(
                    spool_id=bought_at_a.id, weight_used=150, percent_used=15, status="completed", cost=3.0
                ),
            ]
        )
        await db_session.commit()

        resp = await async_client.get("/api/v1/inventory/stats/suppliers")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["supplier_name"] == "Supplier A"
        assert rows[0]["spool_count"] == 1
        assert rows[0]["remaining_g"] == pytest.approx(800)
        assert rows[0]["consumed_g"] == pytest.approx(150)
        # Cost comes from the recorded usage history (spool.cost_per_kg based),
        # never from quoted_price_per_kg.
        assert rows[0]["cost"] == pytest.approx(3.0)


class TestSupplierCsv:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_export_carries_both_columns(
        self, async_client: AsyncClient, supplier_factory, spool_factory, db_session: AsyncSession
    ):
        a = await supplier_factory(name="Supplier A")
        b = await supplier_factory(name="Supplier B")
        spool = await spool_factory()
        db_session.add_all(
            [
                SpoolSupplier(spool_id=spool.id, supplier_id=a.id, is_purchase_source=True),
                SpoolSupplier(spool_id=spool.id, supplier_id=b.id),
            ]
        )
        await db_session.commit()

        export = await async_client.get("/api/v1/inventory/spools/export")
        assert export.status_code == 200
        header, row = export.text.splitlines()[:2]
        columns = header.split(",")
        assert "suppliers" in columns
        assert "purchase_supplier" in columns
        assert "Supplier A; Supplier B" in row
        assert row.split(",")[columns.index("purchase_supplier")].strip('"') == "Supplier A"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_import_matches_by_name_and_creates_nothing(
        self, async_client: AsyncClient, supplier_factory, db_session: AsyncSession
    ):
        await supplier_factory(name="Supplier A")
        await supplier_factory(name="Supplier B")
        csv_text = (
            "material,brand,color_name,suppliers,purchase_supplier\n"
            'PLA,Bambu Lab,Charcoal,"  supplier a ; Supplier B",Supplier B\n'
            "PETG,Bambu Lab,Red,Unknown Corp,\n"
        )

        # Dry run: the unknown name is a warning, not a row error.
        preview = await async_client.post(
            "/api/v1/inventory/spools/import?dry_run=true",
            files={"file": ("spools.csv", csv_text.encode(), "text/csv")},
        )
        assert preview.status_code == 200
        body = preview.json()
        assert body["valid_count"] == 2
        assert body["error_count"] == 0
        assert any("Unknown Corp" in w for w in body["warnings"])

        # Real import: both rows land; assignments match by name, trimmed and
        # case-insensitive; the unknown name is dropped and NOT created.
        result = await async_client.post(
            "/api/v1/inventory/spools/import",
            files={"file": ("spools.csv", csv_text.encode(), "text/csv")},
        )
        assert result.status_code == 200
        assert result.json()["created"] == 2

        listing = await async_client.get("/api/v1/inventory/spools")
        by_color = {s["color_name"]: s for s in listing.json()}
        charcoal = by_color["Charcoal"]["suppliers"]
        assert {row["supplier_name"] for row in charcoal} == {"Supplier A", "Supplier B"}
        assert [row["supplier_name"] for row in charcoal if row["is_purchase_source"]] == ["Supplier B"]
        assert by_color["Red"]["suppliers"] == []
        suppliers = (await async_client.get("/api/v1/inventory/suppliers")).json()
        assert {s["name"] for s in suppliers} == {"Supplier A", "Supplier B"}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_round_trip_preserves_assignments(
        self, async_client: AsyncClient, supplier_factory, spool_factory, db_session: AsyncSession
    ):
        a = await supplier_factory(name="Supplier A")
        spool = await spool_factory()
        db_session.add(SpoolSupplier(spool_id=spool.id, supplier_id=a.id, is_purchase_source=True))
        await db_session.commit()

        export = await async_client.get("/api/v1/inventory/spools/export")
        result = await async_client.post(
            "/api/v1/inventory/spools/import",
            files={"file": ("spools.csv", export.content, "text/csv")},
        )
        assert result.status_code == 200
        assert result.json()["created"] == 1

        listing = (await async_client.get("/api/v1/inventory/spools")).json()
        assert len(listing) == 2
        for row in listing:
            assert [link["supplier_name"] for link in row["suppliers"]] == ["Supplier A"]
            assert row["suppliers"][0]["is_purchase_source"] is True
