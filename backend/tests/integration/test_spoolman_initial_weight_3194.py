"""Spoolman mode reads a spool's size from its own initial_weight (#3194).

Spoolman keeps the net weight of a full spool on the spool (``initial_weight``)
and falls back to the filament's catalogue ``weight``, so one filament can have
spools of different sizes. Bambuddy read only the filament, so a 250 g spool of
a 1000 g filament showed as 1000 g everywhere, and editing its label weight
PATCHed the filament instead of the spool.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

# A 250 g spool of a 1000 g filament with 200 g left.
QUARTER_SPOOL = {
    "id": 42,
    "initial_weight": 250.0,
    "spool_weight": 100.0,
    "remaining_weight": 200.0,
    "used_weight": 50.0,
    "price": 6.25,
    "filament": {
        "id": 7,
        "name": "PLA Basic",
        "material": "PLA",
        "color_hex": "FF0000",
        "weight": 1000.0,
        "vendor": {"id": 3, "name": "Bambu Lab"},
    },
    "location": None,
    "comment": None,
    "archived": False,
    "registered": "2024-01-01T00:00:00+00:00",
    "extra": {"tag": '"AABBCCDD"'},
}


@pytest.fixture
async def spoolman_settings(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    await db_session.commit()


@pytest.fixture
def client():
    mock = MagicMock()
    mock.base_url = "http://localhost:7912"
    mock.health_check = AsyncMock(return_value=True)
    mock.get_all_spools = AsyncMock(return_value=[QUARTER_SPOOL])
    mock.get_spools = AsyncMock(return_value=[QUARTER_SPOOL])
    mock.get_spool = AsyncMock(return_value=QUARTER_SPOOL)
    mock.create_spool = AsyncMock(return_value=QUARTER_SPOOL)
    mock.update_spool = AsyncMock(return_value=None)
    mock.update_spool_full = AsyncMock(return_value=QUARTER_SPOOL)
    mock.merge_spool_extra = AsyncMock(return_value=QUARTER_SPOOL)
    mock.find_or_create_filament = AsyncMock(return_value=7)
    mock.find_or_create_vendor = AsyncMock(return_value=3)
    mock.patch_filament = AsyncMock(return_value={"id": 7})
    mock.is_filament_shared = AsyncMock(return_value=True)
    mock.ensure_extra_field = AsyncMock(return_value=True)
    mock.get_distinct_locations = AsyncMock(return_value=[])
    with (
        patch("backend.app.api.routes.spoolman_inventory.get_spoolman_client", AsyncMock(return_value=mock)),
        patch("backend.app.api.routes.spoolman_inventory.init_spoolman_client", AsyncMock(return_value=mock)),
        patch("backend.app.api.routes.spoolman.get_spoolman_client", AsyncMock(return_value=mock)),
        patch("backend.app.api.routes.spoolbuddy._get_spoolman_client_or_none", AsyncMock(return_value=mock)),
    ):
        yield mock


class TestReadsUseTheSpoolsWeight:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_inventory_shows_the_spools_own_weight(self, async_client: AsyncClient, spoolman_settings, client):
        response = await async_client.get("/api/v1/spoolman/inventory/spools/42")
        assert response.status_code == 200
        body = response.json()
        assert body["label_weight"] == 250
        assert body["label_weight"] - body["weight_used"] == pytest.approx(200.0)
        # 6.25 for 250 g is 25 per kilo
        assert body["cost_per_kg"] == pytest.approx(25.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_weigh_reports_weight_used_against_the_spool(
        self, async_client: AsyncClient, spoolman_settings, client
    ):
        client.update_spool_full = AsyncMock(return_value={**QUARTER_SPOOL, "remaining_weight": 150.0})
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42/weight", json={"weight_grams": 250.0})
        assert response.status_code == 200
        assert client.update_spool_full.call_args.kwargs["remaining_weight"] == pytest.approx(150.0)
        assert response.json()["weight_used"] == pytest.approx(100.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spoolbuddy_scale_reports_weight_used_against_the_spool(
        self, async_client: AsyncClient, spoolman_settings, client
    ):
        response = await async_client.post(
            "/api/v1/spoolbuddy/scale/update-spool-weight", json={"spool_id": 42, "weight_grams": 250.0}
        )
        assert response.status_code == 200
        client.update_spool.assert_called_once_with(spool_id=42, remaining_weight=pytest.approx(150.0))
        assert response.json()["weight_used"] == pytest.approx(100.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_linked_spools_fill_reference_is_the_spools_weight(
        self, async_client: AsyncClient, spoolman_settings, client
    ):
        response = await async_client.get("/api/v1/spoolman/spools/linked")
        assert response.status_code == 200
        assert response.json()["linked"]["AABBCCDD"]["filament_weight"] == pytest.approx(250.0)


class TestCreateWritesTheSpoolsWeight:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_sends_initial_weight_and_the_price_of_the_spool(
        self, async_client: AsyncClient, spoolman_settings, client
    ):
        """A 250 g spool picked from a 1000 g catalogue filament is a 250 g spool."""
        response = await async_client.post(
            "/api/v1/spoolman/inventory/spools",
            json={"spoolman_filament_id": 7, "label_weight": 250, "cost_per_kg": 25.0},
        )
        assert response.status_code == 200
        assert client.create_spool.call_args.kwargs["initial_weight"] == 250.0
        assert client.create_spool.call_args.kwargs["remaining_weight"] == 250.0
        assert client.update_spool_full.call_args.kwargs["price"] == pytest.approx(6.25)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_create_sends_initial_weight(self, async_client: AsyncClient, spoolman_settings, client):
        response = await async_client.post(
            "/api/v1/spoolman/inventory/spools/bulk",
            json={"spool": {"spoolman_filament_id": 7, "label_weight": 250}, "quantity": 2},
        )
        assert response.status_code == 200
        assert client.create_spool.call_count == 2
        for call in client.create_spool.call_args_list:
            assert call.kwargs["initial_weight"] == 250.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_without_label_weight_leaves_the_size_to_spoolman(
        self, async_client: AsyncClient, spoolman_settings, client
    ):
        """An API caller that gives no label weight gets the filament's, as
        before: Spoolman fills initial_weight from the filament itself."""
        response = await async_client.post(
            "/api/v1/spoolman/inventory/spools", json={"spoolman_filament_id": 7, "cost_per_kg": 25.0}
        )
        assert response.status_code == 200
        assert client.create_spool.call_args.kwargs["initial_weight"] is None
        # priced at the size Spoolman gave the spool (QUARTER_SPOOL: 250 g)
        assert client.update_spool_full.call_args.kwargs["price"] == pytest.approx(6.25)


class TestEditWritesTheSpoolNotTheFilament:
    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("shared", [True, False])
    async def test_label_weight_edit_goes_to_initial_weight(
        self, async_client: AsyncClient, spoolman_settings, client, shared
    ):
        """Neither a shared nor a singleton filament is touched, and no
        duplicate filament is created for a spool of another size."""
        client.is_filament_shared = AsyncMock(return_value=shared)
        response = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={"label_weight": 500, "weight_used": 100.0},
        )
        assert response.status_code == 200
        client.patch_filament.assert_not_called()
        client.find_or_create_filament.assert_not_called()
        kwargs = client.update_spool_full.call_args.kwargs
        assert kwargs["filament_id"] == 7
        assert kwargs["initial_weight"] == 500.0
        assert kwargs["remaining_weight"] == pytest.approx(400.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_untouched_edit_writes_neither_size_nor_price(
        self, async_client: AsyncClient, spoolman_settings, client
    ):
        """The form sends back the label weight and cost per kg it loaded; a
        save that changes neither must not write them."""
        response = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={"label_weight": 250, "cost_per_kg": 25.0, "note": "moved"},
        )
        assert response.status_code == 200
        client.patch_filament.assert_not_called()
        kwargs = client.update_spool_full.call_args.kwargs
        assert kwargs["initial_weight"] is None
        assert kwargs["price"] is None
        assert kwargs["remaining_weight"] == pytest.approx(200.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_fractional_size_survives_an_untouched_edit(
        self, async_client: AsyncClient, spoolman_settings, client
    ):
        """The form shows 250.7 g as 250; saving it must not cut the spool to 250 g
        or re-price it."""
        spool = {**QUARTER_SPOOL, "initial_weight": 250.7, "price": 6.99}
        client.get_spool = AsyncMock(return_value=spool)
        loaded = (await async_client.get("/api/v1/spoolman/inventory/spools/42")).json()
        assert loaded["label_weight"] == 250

        response = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={"label_weight": loaded["label_weight"], "cost_per_kg": loaded["cost_per_kg"]},
        )
        assert response.status_code == 200
        kwargs = client.update_spool_full.call_args.kwargs
        assert kwargs["initial_weight"] is None
        assert kwargs["price"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_price_is_converted_at_the_spools_weight(self, async_client: AsyncClient, spoolman_settings, client):
        response = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={"label_weight": 250, "cost_per_kg": 30.0},
        )
        assert response.status_code == 200
        assert client.update_spool_full.call_args.kwargs["price"] == pytest.approx(7.5)

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("payload", [{"label_weight": 500}, {"label_weight": 500, "cost_per_kg": 25.0}])
    async def test_resize_keeps_the_rate_per_kg(self, async_client: AsyncClient, spoolman_settings, client, payload):
        """25 per kg stays 25 per kg when the spool is resized, as in internal
        mode, whether or not the caller (e.g. Bulk Edit) sends the rate."""
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json=payload)
        assert response.status_code == 200
        kwargs = client.update_spool_full.call_args.kwargs
        assert kwargs["initial_weight"] == 500.0
        assert kwargs["price"] == pytest.approx(12.5)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_colour_change_on_shared_filament_relinks_without_resizing(
        self, async_client: AsyncClient, spoolman_settings, client
    ):
        """A relink to a filament of another weight keeps the spool's size:
        Spoolman would otherwise stamp the new filament's weight on a spool
        without an initial_weight of its own."""
        client.find_or_create_filament = AsyncMock(return_value=8)
        spool = {**QUARTER_SPOOL, "initial_weight": None, "remaining_weight": 800.0, "used_weight": 200.0}
        client.get_spool = AsyncMock(return_value=spool)
        response = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42",
            json={"rgba": "00FF00FF"},
        )
        assert response.status_code == 200
        kwargs = client.update_spool_full.call_args.kwargs
        assert kwargs["filament_id"] == 8
        assert kwargs["initial_weight"] == 1000.0
        assert kwargs["remaining_weight"] == pytest.approx(800.0)
        assert kwargs["price"] is None


class TestSpoolWithoutAnySize:
    """Spoolman refuses a remaining_weight (HTTP 400, the whole PATCH) while a
    spool has neither an initial_weight nor a filament weight."""

    BARE = {**QUARTER_SPOOL, "initial_weight": None, "remaining_weight": None, "used_weight": 0.0, "price": None}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_edit_without_label_weight_sends_no_remaining(
        self, async_client: AsyncClient, spoolman_settings, client
    ):
        client.get_spool = AsyncMock(
            return_value={**self.BARE, "filament": {**QUARTER_SPOOL["filament"], "weight": None}}
        )
        response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json={"note": "hi"})
        assert response.status_code == 200
        kwargs = client.update_spool_full.call_args.kwargs
        assert kwargs["remaining_weight"] is None
        assert kwargs["initial_weight"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_form_label_weight_becomes_the_size(self, async_client: AsyncClient, spoolman_settings, client):
        """The form shows such a spool as 1000 g and sends that back."""
        client.get_spool = AsyncMock(
            return_value={**self.BARE, "filament": {**QUARTER_SPOOL["filament"], "weight": None}}
        )
        response = await async_client.patch(
            "/api/v1/spoolman/inventory/spools/42", json={"label_weight": 1000, "weight_used": 100.0}
        )
        assert response.status_code == 200
        kwargs = client.update_spool_full.call_args.kwargs
        assert kwargs["initial_weight"] == 1000.0
        assert kwargs["remaining_weight"] == pytest.approx(900.0)


class TestAmsSyncUsesTheSpoolsWeight:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_ams_percentage_is_of_the_spool(
        self, async_client: AsyncClient, spoolman_settings, client, db_session
    ):
        from backend.app.models.printer import Printer
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

        printer = Printer(name="P", serial_number="SN3194", ip_address="192.168.1.9", access_code="12345678")
        db_session.add(printer)
        await db_session.commit()
        await db_session.refresh(printer)
        db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=0, spoolman_spool_id=42))
        await db_session.commit()

        state = MagicMock()
        state.raw_data = {"ams": [{"id": 0, "tray": [{"id": 0, "remain": 80}]}]}
        with (
            patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=client)),
            patch("backend.app.api.routes.spoolman_inventory.printer_manager") as pm,
        ):
            pm.get_status = MagicMock(return_value=state)
            response = await async_client.post("/api/v1/spoolman/inventory/sync-ams-weights")

        assert response.status_code == 200
        assert response.json()["synced"] == 1
        # 80 % of 250 g, not of the filament's 1000 g
        client.update_spool_full.assert_called_once_with(42, remaining_weight=200.0)
