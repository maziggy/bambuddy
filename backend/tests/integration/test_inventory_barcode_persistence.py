"""Integration coverage for SpoolCode persistence on the local-DB inventory
create/update endpoints — the write side of the multi-code barcode
architecture (see _persist_barcode_codes_for_spool in routes/inventory.py).

Every external-database call is patched so these tests never hit the
network; only the persistence/response-shaping behavior is under test here
(the resolution logic itself is covered by test_barcode_lookup_endpoints.py).
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool_code import SpoolCode

pytestmark = pytest.mark.integration


def _patch_external(ofd_result=None, smdb_result=None):
    return (
        patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=ofd_result)),
        patch("backend.app.services.ofd_client.lookup_article", new=AsyncMock(return_value=None)),
        patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=smdb_result)),
        patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock(return_value=None)),
    )


async def _codes_for(db_session: AsyncSession, spool_id: int) -> list[SpoolCode]:
    result = await db_session.execute(select(SpoolCode).where(SpoolCode.spool_id == spool_id))
    return list(result.scalars().all())


class TestCreateSpoolPersistsCodes:
    async def test_create_with_barcode_persists_cross_referenced_siblings(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        ofd_hit = (
            {"material": "PLA", "brand": "Sunlu"},
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
            ],
        )
        p1, p2, p3, p4 = _patch_external(ofd_result=ofd_hit)
        with p1, p2, p3, p4:
            resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "barcode": "06938936716785", "label_weight": 1000},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["barcode"] == "6938936716785"
        assert {c["code"] for c in body["linked_codes"]} == {"ALZMNTABS01"}

        codes = await _codes_for(db_session, body["id"])
        assert {c.code for c in codes} == {"6938936716785", "ALZMNTABS01"}
        primary = next(c for c in codes if c.code == "6938936716785")
        assert primary.is_primary is True

    async def test_create_without_barcode_persists_no_codes(self, async_client: AsyncClient, db_session: AsyncSession):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "label_weight": 1000},
            )

        assert resp.status_code == 200
        assert resp.json()["linked_codes"] == []
        assert await _codes_for(db_session, resp.json()["id"]) == []

    async def test_bulk_create_persists_codes_for_every_spool(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            resp = await async_client.post(
                "/api/v1/inventory/spools/bulk",
                json={"spool": {"material": "PLA", "barcode": "ALZMNTABS01", "label_weight": 1000}, "quantity": 2},
            )

        assert resp.status_code == 200
        spools = resp.json()
        assert len(spools) == 2
        for spool in spools:
            codes = await _codes_for(db_session, spool["id"])
            assert len(codes) == 1
            assert codes[0].code == "ALZMNTABS01"
            assert codes[0].kind == "sku"

    async def test_bulk_create_resolves_shared_barcode_only_once(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """All spools in a batch share one barcode — the external
        cross-reference must be resolved once per batch, not once per spool."""
        with patch(
            "backend.app.api.routes.inventory._external_all_codes", new=AsyncMock(return_value=None)
        ) as mock_external:
            resp = await async_client.post(
                "/api/v1/inventory/spools/bulk",
                json={"spool": {"material": "PLA", "barcode": "ALZMNTABS01", "label_weight": 1000}, "quantity": 3},
            )

        assert resp.status_code == 200
        spools = resp.json()
        assert len(spools) == 3
        assert mock_external.await_count == 1
        for spool in spools:
            codes = await _codes_for(db_session, spool["id"])
            assert len(codes) == 1
            assert codes[0].code == "ALZMNTABS01"


class TestUpdateSpoolPersistsCodes:
    async def test_setting_barcode_on_update_persists_codes(self, async_client: AsyncClient, db_session: AsyncSession):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "label_weight": 1000},
            )
        spool_id = create_resp.json()["id"]
        assert await _codes_for(db_session, spool_id) == []

        ofd_hit = ({"material": "PLA"}, [{"code": "6938936716785", "kind": "gtin", "is_refill": False}])
        p1, p2, p3, p4 = _patch_external(ofd_result=ofd_hit)
        with p1, p2, p3, p4:
            update_resp = await async_client.patch(
                f"/api/v1/inventory/spools/{spool_id}",
                json={"barcode": "06938936716785"},
            )

        assert update_resp.status_code == 200
        codes = await _codes_for(db_session, spool_id)
        assert {c.code for c in codes} == {"6938936716785"}

    async def test_updating_unrelated_field_does_not_reresolve_barcode(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """No barcode change → no external cross-reference call at all."""
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "barcode": "06938936716785", "label_weight": 1000},
            )
        spool_id = create_resp.json()["id"]

        with patch("backend.app.api.routes.inventory._external_all_codes", new=AsyncMock()) as mock_external:
            update_resp = await async_client.patch(
                f"/api/v1/inventory/spools/{spool_id}",
                json={"note": "just a note update"},
            )

        assert update_resp.status_code == 200
        mock_external.assert_not_called()


class TestReadPathResolvesOwnInventoryThroughRealSql:
    """Exercises the actual _resolve_barcode SQL against a real DB — the gap
    Martin flagged: test_barcode_lookup_endpoints.py only ever drives the
    resolver against a MagicMock DB, so the SpoolCode query itself (and the
    scan/persist classification agreement it depends on) was never actually
    exercised. This is also the exact regression case from the review: a
    UPC-A with a leading zero must resolve on repeat scan regardless of
    whether the raw or already-normalized form is scanned."""

    async def test_repeat_scan_of_leading_zero_upc_a_resolves_from_own_inventory(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "barcode": "036000291452", "label_weight": 1000},
            )
        assert create_resp.status_code == 200
        assert create_resp.json()["barcode"] == "36000291452"  # stored, zero-stripped

        # Re-scan both the raw (as-scanned) and the already-stripped (as-stored)
        # forms — both must resolve from inventory with zero external calls.
        for barcode in ("036000291452", "36000291452"):
            with (
                patch("backend.app.services.ofd_client.lookup", new=AsyncMock()) as mock_ofd,
                patch("backend.app.services.ofd_client.lookup_article", new=AsyncMock()) as mock_ofd_article,
                patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock()) as mock_smdb,
                patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock()) as mock_smdb_sku,
            ):
                resp = await async_client.get(f"/api/v1/inventory/barcode/{barcode}")

            assert resp.status_code == 200
            body = resp.json()
            assert body["matched"] is True, f"barcode {barcode} failed to resolve: {body}"
            assert body["source"] == "inventory"
            mock_ofd.assert_not_called()
            mock_ofd_article.assert_not_called()
            mock_smdb.assert_not_called()
            mock_smdb_sku.assert_not_called()

    async def test_repeat_scan_of_alphanumeric_sku_resolves_from_own_inventory(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "barcode": "ALZMNTABS01", "label_weight": 1000},
            )
        assert create_resp.status_code == 200

        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock()) as mock_ofd,
            patch("backend.app.services.ofd_client.lookup_article", new=AsyncMock()) as mock_ofd_article,
            patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock()) as mock_smdb,
            patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock()) as mock_smdb_sku,
        ):
            resp = await async_client.get("/api/v1/inventory/barcode/ALZMNTABS01")

        assert resp.status_code == 200
        body = resp.json()
        assert body["matched"] is True
        assert body["source"] == "inventory"
        mock_ofd.assert_not_called()
        mock_ofd_article.assert_not_called()
        mock_smdb.assert_not_called()
        mock_smdb_sku.assert_not_called()
