"""Each spool's product page at its suppliers, and buying it again (#2540).

A spool's supplier assignment keeps the product's own page at that supplier
(``product_url``), next to the supplier's article number and quoted price; the
supplier's ``website`` stays its storefront. A new spool of the product
inherits the page with the rest of the assignment, and a shopping list item
remembers where it is to be bought, which the spools it becomes when received
get as their purchase source.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.supplier import SpoolSupplier, Supplier
from backend.app.schemas.supplier import validate_http_url
from backend.app.services.supplier_links import with_purchase_source

PAGE = "https://shop.example/pla-matte-charcoal?variant=1kg"
PRODUCT = {"material": "PLA", "subtype": "Matte", "brand": "Bambu Lab", "color_name": "Charcoal"}


@pytest.fixture
async def spool(db_session: AsyncSession):
    async def _create(**kwargs):
        row = Spool(**{**PRODUCT, "label_weight": 1000, "core_weight": 250, **kwargs})
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    return _create


@pytest.fixture
async def supplier(db_session: AsyncSession):
    made = [0]

    async def _create(**kwargs):
        made[0] += 1
        row = Supplier(**{"name": f"Shop {made[0]}", **kwargs})
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    return _create


class TestAddresses:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (PAGE, PAGE),
            ("  http://shop.example/x  ", "http://shop.example/x"),
            ("HTTPS://Shop.example/x", "HTTPS://Shop.example/x"),
            ("", None),
            ("   ", None),
            (None, None),
            # No scheme: https is assumed rather than refusing the address.
            ("3djake.de", "https://3djake.de"),
            ("  shop.example/x?a=1 ", "https://shop.example/x?a=1"),
            ("localhost:8080/x", "https://localhost:8080/x"),
            ("shop.example:8080", "https://shop.example:8080"),
        ],
    )
    def test_a_web_address_is_kept(self, value, expected):
        assert validate_http_url(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "ftp://shop.example/x",
            "http://",
            "//shop.example/x",
            "mailto:a@b.c",
            # Browsers fix these up to a host, urlsplit finds none: both sides refuse them.
            "http:example.com",
            "https:/example.com",
            r"https:\example.com",
            r"https://\example.com",
            "https:///example.com",
            r"https://example.com\path",
            "https://:8080/x",
            "https://user@/x",
            "https://exa mple.com",
        ],
    )
    def test_anything_else_is_refused(self, value):
        with pytest.raises(ValueError):
            validate_http_url(value)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_supplier_website_must_be_a_web_address(self, async_client: AsyncClient, supplier):
        assert (
            await async_client.post("/api/v1/inventory/suppliers", json={"name": "X", "website": "javascript:x"})
        ).status_code == 422
        existing = await supplier()
        assert (
            await async_client.patch(f"/api/v1/inventory/suppliers/{existing.id}", json={"website": "data:x"})
        ).status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_supplier_with_a_schemeless_website_saves_and_renames(self, async_client: AsyncClient, supplier):
        created = await async_client.post("/api/v1/inventory/suppliers", json={"name": "Jake", "website": "3djake.de"})
        assert created.status_code in (200, 201), created.text
        assert created.json()["website"] == "https://3djake.de"
        # A supplier stored as typed before this rule can be renamed: the website is fixed on the way.
        old = await supplier(name="Old shop", website="3djake.de")
        renamed = await async_client.patch(
            f"/api/v1/inventory/suppliers/{old.id}", json={"name": "New shop", "website": old.website}
        )
        assert renamed.status_code == 200, renamed.text
        assert (renamed.json()["name"], renamed.json()["website"]) == ("New shop", "https://3djake.de")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_website_saved_before_the_rule_still_lists(self, async_client: AsyncClient, supplier):
        await supplier(name="Old", website="javascript:alert(1)")

        response = await async_client.get("/api/v1/inventory/suppliers")

        assert response.status_code == 200
        assert [s["website"] for s in response.json() if s["name"] == "Old"] == ["javascript:alert(1)"]


class TestAssignment:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_assignment_keeps_its_product_page(self, async_client: AsyncClient, spool, supplier):
        own, shop = await spool(), await supplier()

        put = await async_client.put(
            f"/api/v1/inventory/spools/{own.id}/suppliers",
            json=[{"supplier_id": shop.id, "is_purchase_source": True, "product_url": f" {PAGE} "}],
        )

        assert put.status_code == 200
        assert put.json()[0]["product_url"] == PAGE
        listed = (await async_client.get(f"/api/v1/inventory/spools/{own.id}")).json()["suppliers"]
        assert listed[0]["product_url"] == PAGE

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_product_page_must_be_a_web_address(self, async_client: AsyncClient, spool, supplier):
        own, shop = await spool(), await supplier()

        put = await async_client.put(
            f"/api/v1/inventory/spools/{own.id}/suppliers",
            json=[{"supplier_id": shop.id, "product_url": "javascript:alert(1)"}],
        )

        assert put.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_spoolman_spool_keeps_its_product_page(self, async_client: AsyncClient, db_session, supplier):
        from backend.app.models.settings import Settings

        db_session.add_all(
            [Settings(key="spoolman_enabled", value="true"), Settings(key="spoolman_url", value="http://sm:7912")]
        )
        await db_session.commit()
        shop = await supplier()
        client = MagicMock(base_url="http://sm:7912", health_check=AsyncMock(return_value=True))
        client.get_spool = AsyncMock(return_value={"id": 7})

        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=client)):
            put = await async_client.put(
                "/api/v1/spoolman/inventory/spools/7/suppliers",
                json=[{"supplier_id": shop.id, "product_url": PAGE}],
            )

        assert put.status_code == 200
        assert put.json()[0]["product_url"] == PAGE


class TestNewSpools:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_new_spool_of_the_product_inherits_its_page(
        self, async_client: AsyncClient, db_session, spool, supplier
    ):
        donor, shop = await spool(), await supplier()
        db_session.add(SpoolSupplier(spool_id=donor.id, supplier_id=shop.id, product_url=PAGE))
        await db_session.commit()

        created = await async_client.post("/api/v1/inventory/spools", json=PRODUCT)

        assert created.status_code == 200
        assert [(s["product_url"], s["is_purchase_source"]) for s in created.json()["suppliers"]] == [(PAGE, False)]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spools_received_are_bought_where_the_item_says(
        self, async_client: AsyncClient, db_session, spool, supplier
    ):
        donor, shop, other = await spool(), await supplier(), await supplier()
        db_session.add_all(
            [
                SpoolSupplier(spool_id=donor.id, supplier_id=shop.id, supplier_article_number="S-1", product_url=PAGE),
                SpoolSupplier(spool_id=donor.id, supplier_id=other.id, quoted_price_per_kg=21.0),
            ]
        )
        await db_session.commit()

        created = await async_client.post(
            "/api/v1/inventory/spools/bulk",
            json={"spool": PRODUCT, "quantity": 2, "purchase_source": {"supplier_id": shop.id}},
        )

        assert created.status_code == 200
        for row in created.json():
            links = {s["supplier_id"]: s for s in row["suppliers"]}
            # The item's supplier is where they were bought, with what the
            # product's link knew; the alternative stays an alternative.
            assert (links[shop.id]["is_purchase_source"], links[shop.id]["product_url"]) == (True, PAGE)
            assert links[shop.id]["supplier_article_number"] == "S-1"
            assert (links[other.id]["is_purchase_source"], links[other.id]["quoted_price_per_kg"]) == (False, 21.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_supplier_new_to_the_product_is_added(self, async_client: AsyncClient, supplier):
        shop = await supplier()

        created = await async_client.post(
            "/api/v1/inventory/spools/bulk",
            json={
                "spool": PRODUCT,
                "quantity": 1,
                "purchase_source": {"supplier_id": shop.id, "supplier_article_number": "N-9", "product_url": PAGE},
            },
        )

        assert created.status_code == 200
        (link,) = created.json()[0]["suppliers"]
        assert (link["supplier_id"], link["is_purchase_source"]) == (shop.id, True)
        assert (link["supplier_article_number"], link["product_url"]) == ("N-9", PAGE)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_unknown_purchase_source_is_refused(self, async_client: AsyncClient):
        created = await async_client.post(
            "/api/v1/inventory/spools/bulk",
            json={"spool": PRODUCT, "quantity": 1, "purchase_source": {"supplier_id": 999999}},
        )

        assert created.status_code == 404

    def test_the_items_page_wins_over_the_inherited_one_and_none_keeps_it(self):
        templates = [
            {
                "supplier_id": 1,
                "supplier_article_number": "A",
                "quoted_price_per_kg": 20.0,
                "product_url": "https://a/old",
                "is_purchase_source": False,
            }
        ]

        (merged,) = with_purchase_source(
            templates, {"supplier_id": 1, "product_url": "https://a/new", "supplier_article_number": None}
        )

        assert merged == {
            "supplier_id": 1,
            "supplier_article_number": "A",
            "quoted_price_per_kg": 20.0,
            "product_url": "https://a/new",
            "is_purchase_source": True,
        }


class TestShoppingList:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_item_remembers_where_to_buy_it(self, async_client: AsyncClient, supplier):
        shop = await supplier(name="Filament Shop")

        added = await async_client.post(
            "/api/v1/inventory/shopping-list",
            json={**PRODUCT, "supplier_id": shop.id, "supplier_article_number": "S-1", "product_url": PAGE},
        )

        assert added.status_code == 200
        body = added.json()
        assert (body["supplier_id"], body["supplier_name"]) == (shop.id, "Filament Shop")
        assert (body["supplier_article_number"], body["product_url"]) == ("S-1", PAGE)
        (listed,) = (await async_client.get("/api/v1/inventory/shopping-list")).json()
        assert listed["supplier_name"] == "Filament Shop"
        moved = await async_client.patch(
            f"/api/v1/inventory/shopping-list/{body['id']}/status", json={"status": "purchased"}
        )
        assert (moved.json()["status"], moved.json()["product_url"]) == ("purchased", PAGE)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_item_without_a_supplier_is_as_before(self, async_client: AsyncClient):
        added = await async_client.post("/api/v1/inventory/shopping-list", json=PRODUCT)

        assert added.status_code == 200
        body = added.json()
        assert (body["supplier_id"], body["supplier_name"], body["product_url"]) == (None, None, None)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_item_is_refused_an_unknown_supplier_or_a_bad_page(self, async_client: AsyncClient, supplier):
        shop = await supplier()

        assert (
            await async_client.post("/api/v1/inventory/shopping-list", json={**PRODUCT, "supplier_id": 999999})
        ).status_code == 404
        assert (
            await async_client.post(
                "/api/v1/inventory/shopping-list", json={**PRODUCT, "supplier_id": shop.id, "product_url": "data:x"}
            )
        ).status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_deleting_a_supplier_clears_it_from_the_cart(self, async_client: AsyncClient, db_session, supplier):
        """SQLite runs without foreign keys: the SET NULL is applied by core/sqlite_fk."""
        from sqlalchemy import select

        from backend.app.models.shopping_list import ShoppingListItem

        shop = await supplier(name="Gone")
        added = (
            await async_client.post("/api/v1/inventory/shopping-list", json={**PRODUCT, "supplier_id": shop.id})
        ).json()

        deleted = await async_client.delete(f"/api/v1/inventory/suppliers/{shop.id}")

        assert deleted.status_code == 200, deleted.text
        db_session.expire_all()
        item = (
            await db_session.execute(select(ShoppingListItem).where(ShoppingListItem.id == added["id"]))
        ).scalar_one()
        assert item.supplier_id is None
        (listed,) = (await async_client.get("/api/v1/inventory/shopping-list")).json()
        assert (listed["supplier_id"], listed["supplier_name"]) == (None, None)
