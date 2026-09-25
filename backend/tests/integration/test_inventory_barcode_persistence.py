"""Integration coverage for the typed spool code columns on the local-DB
inventory endpoints — scanned-code routing + size-consistent cross-fill on
the write side, and the GET /inventory/barcode/{code} read path.

Every external-database call is patched so these tests never hit the
network; routing/persistence run against the real FastAPI app and a real
SQLite DB (PR #1895's review specifically flagged MagicMock-DB tests as not
exercising the real SQL at all).
"""

from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.settings import Settings
from backend.app.models.spool import Spool

pytestmark = pytest.mark.integration

_EXTERNAL_TARGETS = (
    "backend.app.services.ofd_client.lookup",
    "backend.app.services.ofd_client.lookup_article",
    "backend.app.services.ofd_client.same_package_code",
    "backend.app.services.spoolmandb_community_client.lookup",
    "backend.app.services.spoolmandb_community_client.lookup_sku",
)


def _patch_external(smdb_result=None, smdb_sku_result=None, ofd_paired=(False, None)):
    return (
        patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=None)),
        patch("backend.app.services.ofd_client.lookup_article", new=AsyncMock(return_value=None)),
        patch("backend.app.services.ofd_client.same_package_code", new=AsyncMock(return_value=ofd_paired)),
        patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=smdb_result)),
        patch(
            "backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock(return_value=smdb_sku_result)
        ),
    )


@contextmanager
def patched_external(**kwargs):
    with ExitStack() as stack:
        for patcher in _patch_external(**kwargs):
            stack.enter_context(patcher)
        yield


@contextmanager
def forbidden_external():
    """Patches that fail the test outright if any external client is called —
    the strong form of 'no external activity' for the toggle-gating tests."""
    boom = AssertionError("external barcode lookup must not be called")
    with ExitStack() as stack:
        for target in _EXTERNAL_TARGETS:
            stack.enter_context(patch(target, new=AsyncMock(side_effect=boom)))
        yield


@pytest.fixture
async def lookup_disabled(db_session: AsyncSession):
    db_session.add(Settings(key="barcode_lookup_enabled", value="false"))
    await db_session.commit()


SMDB_PACKAGE = (
    {"material": "PLA", "brand": "Bambu Lab"},
    [
        {"code": "6938936716785", "kind": "gtin", "is_refill": False},
        {"code": "6938936716792", "kind": "gtin", "is_refill": True},
        {"code": "17600", "kind": "sku", "is_refill": False},
    ],
)


class TestCreateSpoolRoutesScannedCode:
    async def test_scanned_gtin_fills_gtin_and_cross_fills_sku(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        with patched_external(smdb_result=SMDB_PACKAGE):
            resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "scanned_code": "06938936716785", "label_weight": 1000},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["gtin_code"] == "6938936716785"  # canonicalized
        assert body["sku_code"] == "17600"  # same-package cross-fill
        assert body["other_code"] is None
        assert body["bought_as_refill"] is False

        spool = (await db_session.execute(select(Spool))).scalars().one()
        assert spool.gtin_code == "6938936716785"
        assert spool.sku_code == "17600"

    async def test_bought_as_refill_picks_the_refill_gtin(self, async_client: AsyncClient, db_session: AsyncSession):
        """Scanning the SKU of a refill purchase cross-fills the refill EAN,
        not the with-spool one."""
        with patched_external(smdb_sku_result=SMDB_PACKAGE):
            resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "scanned_code": "17600", "bought_as_refill": True, "label_weight": 1000},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["sku_code"] == "17600"
        assert body["gtin_code"] == "6938936716792"  # the refill EAN
        assert body["bought_as_refill"] is True

    async def test_unknown_scanned_code_lands_in_other_code(self, async_client: AsyncClient, db_session: AsyncSession):
        with patched_external():
            resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "scanned_code": "MyShelf-a42", "label_weight": 1000},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["other_code"] == "MyShelf-a42"
        assert body["gtin_code"] is None
        assert body["sku_code"] is None

    async def test_explicit_fields_win_over_routing(self, async_client: AsyncClient, db_session: AsyncSession):
        with patched_external(smdb_result=SMDB_PACKAGE):
            resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={
                    "material": "PLA",
                    "scanned_code": "6938936716785",
                    "sku_code": "MYOWNSKU",
                    "label_weight": 1000,
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["sku_code"] == "MYOWNSKU"  # explicit beats cross-fill
        assert body["gtin_code"] == "6938936716785"

    async def test_create_without_codes_persists_none(self, async_client: AsyncClient, db_session: AsyncSession):
        with forbidden_external():
            resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "label_weight": 1000},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["gtin_code"] is None
        assert body["asin_code"] is None
        assert body["sku_code"] is None
        assert body["other_code"] is None

    async def test_bulk_create_routes_once_for_every_spool(self, async_client: AsyncClient, db_session: AsyncSession):
        with patch(
            "backend.app.api.routes.inventory.route_scanned_code",
            new=AsyncMock(
                return_value={"gtin_code": "6938936716785", "asin_code": None, "sku_code": "17600", "other_code": None}
            ),
        ) as mock_route:
            resp = await async_client.post(
                "/api/v1/inventory/spools/bulk",
                json={
                    "spool": {"material": "PLA", "scanned_code": "6938936716785", "label_weight": 1000},
                    "quantity": 3,
                },
            )

        assert resp.status_code == 200
        assert mock_route.await_count == 1  # once per batch, not per spool
        spools = (await db_session.execute(select(Spool))).scalars().all()
        assert len(spools) == 3
        assert all(s.gtin_code == "6938936716785" and s.sku_code == "17600" for s in spools)


class TestUpdateSpoolCodeFields:
    async def test_update_sets_and_clears_typed_fields(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(Spool(material="PLA", gtin_code="6938936716785"))
        await db_session.commit()
        spool_id = (await db_session.execute(select(Spool.id))).scalar_one()

        with forbidden_external():
            resp = await async_client.patch(
                f"/api/v1/inventory/spools/{spool_id}",
                json={"sku_code": "17600", "gtin_code": "", "bought_as_refill": True},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["sku_code"] == "17600"
        assert body["gtin_code"] is None  # explicit clear
        assert body["bought_as_refill"] is True

    async def test_update_rejects_invalid_gtin(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(Spool(material="PLA"))
        await db_session.commit()
        spool_id = (await db_session.execute(select(Spool.id))).scalar_one()

        resp = await async_client.patch(
            f"/api/v1/inventory/spools/{spool_id}",
            json={"gtin_code": "NOTAGTIN01"},
        )
        assert resp.status_code == 422

    async def test_updating_unrelated_field_touches_no_codes(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(Spool(material="PLA", gtin_code="6938936716785", sku_code="17600"))
        await db_session.commit()
        spool_id = (await db_session.execute(select(Spool.id))).scalar_one()

        with forbidden_external():
            resp = await async_client.patch(f"/api/v1/inventory/spools/{spool_id}", json={"note": "hello"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["gtin_code"] == "6938936716785"
        assert body["sku_code"] == "17600"


class TestWritePathToggleGating:
    """With barcode_lookup_enabled off, no write path may trigger an external
    fetch — a first-ever offline instance would otherwise block a simple
    spool save behind the OFD/tarball refresh timeouts (#1895 review 5)."""

    async def test_create_with_scanned_gtin_routes_structurally_without_external_calls(
        self, async_client: AsyncClient, db_session: AsyncSession, lookup_disabled
    ):
        with forbidden_external():
            resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "scanned_code": "6938936716785", "label_weight": 1000},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["gtin_code"] == "6938936716785"  # structural routing still works
        assert body["sku_code"] is None  # but no cross-fill

    async def test_scanned_candidate_lands_in_other_code_without_external_calls(
        self, async_client: AsyncClient, db_session: AsyncSession, lookup_disabled
    ):
        with forbidden_external():
            resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "scanned_code": "17600", "label_weight": 1000},
            )

        assert resp.status_code == 200
        # No community evidence available → conservative routing to other_code.
        assert resp.json()["other_code"] == "17600"


class TestReadPathResolvesOwnInventoryThroughRealSql:
    """End-to-end repeat-scan through the API against real rows — the exact
    scenario PR #1895's review 2 found broken (leading-zero UPC-A never
    matching its own inventory row) and asked to be covered with real SQL."""

    async def test_repeat_scan_of_leading_zero_upc_a_resolves_from_own_inventory(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        with patched_external():
            create = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "brand": "Sunlu", "scanned_code": "036000291452", "label_weight": 1000},
            )
        assert create.status_code == 200

        with forbidden_external():
            lookup = await async_client.get("/api/v1/inventory/barcode/036000291452")

        assert lookup.status_code == 200
        body = lookup.json()
        assert body["matched"] is True
        assert body["source"] == "inventory"
        assert body["brand"] == "Sunlu"

    async def test_repeat_scan_of_other_code_resolves_from_own_inventory(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """A user-owned code (self-printed barcode) stored in other_code must
        match verbatim on re-scan."""
        with patched_external():
            create = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "brand": "Custom", "scanned_code": "MyShelf-a42", "label_weight": 1000},
            )
        assert create.status_code == 200

        with forbidden_external():
            lookup = await async_client.get("/api/v1/inventory/barcode/MyShelf-a42")

        assert lookup.status_code == 200
        body = lookup.json()
        assert body["matched"] is True
        assert body["source"] == "inventory"
        assert body["brand"] == "Custom"

    async def test_scan_of_cross_filled_sku_matches_the_same_roll(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Scan the box GTIN at create (SKU cross-fills); a later scan of the
        SKU printed on the same box matches the roll through sku_code."""
        with patched_external(smdb_result=SMDB_PACKAGE):
            create = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "brand": "Bambu Lab", "scanned_code": "6938936716785", "label_weight": 1000},
            )
        assert create.status_code == 200

        with forbidden_external():
            lookup = await async_client.get("/api/v1/inventory/barcode/17600")

        assert lookup.status_code == 200
        body = lookup.json()
        assert body["matched"] is True
        assert body["source"] == "inventory"

    async def test_inventory_hit_reports_scanned_code_refill_flag(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Re-scanning a roll bought as a refill auto-arms the kiosk toggle:
        the lookup's is_refill mirrors the stored bought_as_refill."""
        with patched_external():
            create = await async_client.post(
                "/api/v1/inventory/spools",
                json={
                    "material": "PLA",
                    "scanned_code": "6938936716785",
                    "bought_as_refill": True,
                    "label_weight": 1000,
                },
            )
        assert create.status_code == 200

        with forbidden_external():
            lookup = await async_client.get("/api/v1/inventory/barcode/6938936716785")

        assert lookup.status_code == 200
        assert lookup.json()["is_refill"] is True


class TestLookupBarcodeEndpoint:
    async def test_unknown_code_returns_matched_false_with_enabled_true(self, async_client: AsyncClient):
        with patched_external():
            resp = await async_client.get("/api/v1/inventory/barcode/111111111117")
        assert resp.status_code == 200
        body = resp.json()
        assert body["matched"] is False
        assert body["enabled"] is True
        assert body["source"] is None

    async def test_disabled_setting_reports_enabled_false_and_skips_external(
        self, async_client: AsyncClient, lookup_disabled
    ):
        with forbidden_external():
            resp = await async_client.get("/api/v1/inventory/barcode/111111111117")
        assert resp.status_code == 200
        body = resp.json()
        assert body["matched"] is False
        assert body["enabled"] is False

    async def test_barcode_is_canonicalized_in_response(self, async_client: AsyncClient):
        with patched_external():
            resp = await async_client.get("/api/v1/inventory/barcode/0012345678905")
        assert resp.status_code == 200
        assert resp.json()["barcode"] == "12345678905"


class TestBarcodePathParamLengthLimit:
    """The path param caps at 64 chars to match the code columns — without it
    an arbitrarily long segment reaches classify_code and the external chain
    unbounded."""

    async def test_barcode_over_max_length_is_rejected(self, async_client: AsyncClient):
        resp = await async_client.get(f"/api/v1/inventory/barcode/{'A' * 65}")
        assert resp.status_code == 422

    async def test_barcode_at_max_length_is_accepted(self, async_client: AsyncClient):
        with patched_external():
            resp = await async_client.get(f"/api/v1/inventory/barcode/{'A' * 64}")
        assert resp.status_code == 200


class TestAssignmentsEndpointShape:
    """The assignments listing returns SpoolResponse-shaped spools — the typed
    code fields must serialize there too (the old spool_code model 500'd here
    once via a missing selectinload; the columns can't, but lock the shape)."""

    async def test_assignments_return_typed_code_fields(self, async_client: AsyncClient, db_session: AsyncSession):
        db_session.add(Spool(material="PLA", gtin_code="6938936716785", bought_as_refill=True))
        await db_session.commit()

        resp = await async_client.get("/api/v1/inventory/spools")
        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["gtin_code"] == "6938936716785"
        assert body[0]["bought_as_refill"] is True
