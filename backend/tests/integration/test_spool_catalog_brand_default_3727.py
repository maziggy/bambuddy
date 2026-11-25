"""Spool catalog API: the manufacturer of a row and its brand default (#3727)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.catalog_defaults import DEFAULT_BRAND_SPOOLS, DEFAULT_SPOOL_CATALOG
from backend.app.models.spool_catalog import SpoolCatalogEntry

CATALOG = "/api/v1/inventory/catalog"

# An OpenSpool tag from Sunlu, with no empty-spool weight on it:
# {"protocol":"openspool","version":"1.0","type":"PLA","color_hex":"FFAABB","brand":"Sunlu"}
SUNLU_JSON = b'{"protocol":"openspool","version":"1.0","type":"PLA","color_hex":"FFAABB","brand":"Sunlu"}'


def _openspool_ndef_hex(payload: bytes) -> str:
    mime = b"application/json"
    record = bytes([0xD2, len(mime), len(payload)]) + mime + payload
    return (bytes([0x03, len(record)]) + record + b"\xfe").hex()


async def _create(async_client: AsyncClient, **body) -> dict:
    resp = await async_client.post(CATALOG, json={"name": "Row", "weight": 200, **body})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _catalog(async_client: AsyncClient) -> dict[str, dict]:
    resp = await async_client.get(CATALOG)
    assert resp.status_code == 200
    return {e["name"]: e for e in resp.json()}


@pytest.mark.asyncio
@pytest.mark.integration
class TestCatalogApi:
    async def test_create_and_list_carry_the_new_fields(self, async_client: AsyncClient):
        row = await _create(async_client, name="Sunlu - V3 + Core", manufacturer=" Sunlu ", default_for_brand=True)
        assert row["manufacturer"] == "Sunlu"
        assert row["default_for_brand"] is True
        assert (await _catalog(async_client))["Sunlu - V3 + Core"]["default_for_brand"] is True

    async def test_a_client_that_sends_only_name_and_weight_still_works(self, async_client: AsyncClient):
        """Older clients, and exports from before #3727."""
        row = await _create(async_client, name="Old style")
        assert row["manufacturer"] is None
        assert row["default_for_brand"] is False

    async def test_one_default_per_brand(self, async_client: AsyncClient):
        first = await _create(async_client, name="A", manufacturer="Sunlu", default_for_brand=True)
        await _create(async_client, name="B", manufacturer="SUNLU", default_for_brand=True)
        rows = await _catalog(async_client)
        assert rows["A"]["default_for_brand"] is False
        assert rows["B"]["default_for_brand"] is True

        resp = await async_client.put(
            f"{CATALOG}/{first['id']}", json={"name": "A", "weight": 200, "default_for_brand": True}
        )
        assert resp.status_code == 200
        rows = await _catalog(async_client)
        assert rows["A"]["default_for_brand"] is True
        assert rows["B"]["default_for_brand"] is False

    async def test_other_brands_keep_their_default(self, async_client: AsyncClient):
        await _create(async_client, name="A", manufacturer="Sunlu", default_for_brand=True)
        await _create(async_client, name="B", manufacturer="eSUN", default_for_brand=True)
        rows = await _catalog(async_client)
        assert rows["A"]["default_for_brand"] and rows["B"]["default_for_brand"]

    async def test_one_default_per_brand_with_a_non_ascii_name(self, async_client: AsyncClient):
        await _create(async_client, name="A", manufacturer="Filamentwerk Süd", default_for_brand=True)
        await _create(async_client, name="B", manufacturer="filamentwerk süd", default_for_brand=True)
        rows = await _catalog(async_client)
        assert (rows["A"]["default_for_brand"], rows["B"]["default_for_brand"]) == (False, True)

    async def test_an_explicit_null_default_is_false(self, async_client: AsyncClient):
        row = await _create(async_client, name="N", default_for_brand=None)
        assert row["default_for_brand"] is False

    async def test_default_spool_answers_for_a_brand(self, async_client: AsyncClient):
        """What the Add Spool form asks when a brand is picked."""
        row = await _create(async_client, name="A", weight=217, manufacturer="Sunlu", default_for_brand=True)
        resp = await async_client.get(f"{CATALOG}/default-spool", params={"brand": " sunlu "})
        assert resp.status_code == 200
        assert resp.json() == {"weight": 217, "catalog_id": row["id"]}

        resp = await async_client.get(f"{CATALOG}/default-spool", params={"brand": "eSUN"})
        assert resp.status_code == 200
        assert resp.json() is None

    async def test_a_default_needs_a_manufacturer(self, async_client: AsyncClient):
        resp = await async_client.post(CATALOG, json={"name": "X", "weight": 1, "default_for_brand": True})
        assert resp.status_code == 422
        row = await _create(async_client, name="Y", manufacturer="Sunlu", default_for_brand=True)
        resp = await async_client.put(f"{CATALOG}/{row['id']}", json={"name": "Y", "weight": 1, "manufacturer": None})
        assert resp.status_code == 422

    async def test_an_update_without_the_new_fields_keeps_them(self, async_client: AsyncClient):
        row = await _create(async_client, name="A", manufacturer="Sunlu", default_for_brand=True)
        resp = await async_client.put(f"{CATALOG}/{row['id']}", json={"name": "A", "weight": 218})
        assert resp.status_code == 200
        body = resp.json()
        assert (body["weight"], body["manufacturer"], body["default_for_brand"]) == (218, "Sunlu", True)

    async def test_reset_seeds_manufacturers_and_the_brand_defaults(self, async_client: AsyncClient):
        await _create(async_client, name="Mine", manufacturer="Sunlu", default_for_brand=True)
        assert (await async_client.post(f"{CATALOG}/reset")).status_code == 200
        rows = await _catalog(async_client)
        assert len(rows) == len(DEFAULT_SPOOL_CATALOG)
        assert {e["manufacturer"]: n for n, e in rows.items() if e["default_for_brand"]} == DEFAULT_BRAND_SPOOLS
        assert rows["eSUN - Plastic"]["manufacturer"] == "eSUN"


@pytest.mark.asyncio
@pytest.mark.integration
class TestNdefPrefillTakesTheBrandDefault:
    async def _scan(self, async_client: AsyncClient, payload: bytes = SUNLU_JSON) -> dict:
        with patch("backend.app.api.routes.spoolbuddy.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            resp = await async_client.post(
                "/api/v1/spoolbuddy/nfc/tag-scanned",
                json={
                    "device_id": "sb-1",
                    "tag_uid": "04E115D15F6181",
                    "sak": 0,
                    "tag_type": "ntag",
                    "ndef_hex": _openspool_ndef_hex(payload),
                },
            )
        assert resp.status_code == 200, resp.text
        return resp.json()["tag_data"]

    async def test_a_tag_without_an_empty_spool_weight_gets_its_brands(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        row = SpoolCatalogEntry(name="Sunlu - Core", weight=217, manufacturer="Sunlu", default_for_brand=True)
        db_session.add(row)
        await db_session.commit()

        tag_data = await self._scan(async_client)
        assert tag_data["brand"] == "Sunlu"
        assert (tag_data["core_weight"], tag_data["core_weight_catalog_id"]) == (217, row.id)

    async def test_a_brand_without_a_default_leaves_it_to_quick_add(self, async_client: AsyncClient):
        tag_data = await self._scan(async_client)
        assert tag_data["core_weight"] is None
        assert tag_data["core_weight_catalog_id"] is None
