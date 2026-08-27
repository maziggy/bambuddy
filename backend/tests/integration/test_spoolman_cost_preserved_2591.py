"""A Spoolman-priced cost survives the two recalculations (#2591).

``spoolman_tracking`` prices an archive from the linked spools at completion.
Both cost recalculations rebuild a print's cost from ``SpoolUsageHistory``, and
Spoolman mode never writes rows there -- the built-in usage tracker is handed
``spoolman_owns_usage`` at print start. Their catalogue-or-default fallback
would therefore overwrite the Spoolman figure with a default-rate one on the
next rescan or bulk recalculate, silently undoing the fix, and the per-slot
spool resolution it came from is transient and cannot be rebuilt from the
archive row.
"""

import pytest
from httpx import AsyncClient

from backend.app.models.settings import Settings


@pytest.fixture
async def spoolman_mode(db_session):
    """Spoolman owns pricing; the built-in catalogue is empty, as the reporter's was."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="default_filament_cost", value="25"))
    await db_session.commit()
    yield
    await db_session.rollback()


class TestRecalculateCostsPreservesSpoolmanPricing:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_recalculate_keeps_the_spoolman_figure(
        self, async_client: AsyncClient, spoolman_mode, archive_factory, printer_factory, db_session
    ):
        """100 g at the 25/kg default would be 2.50. The archive says 4.00
        because the linked spool cost 40.00 a kilo, and a recalculate with no
        usage history to read must not drag it back down."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, print_name="SpoolmanPriced", status="completed", cost=4.0)
        archive.filament_used_grams = 100.0
        archive.filament_type = "PLA"
        await db_session.commit()

        response = await async_client.post("/api/v1/archives/recalculate-costs")
        assert response.status_code == 200
        assert response.json()["preserved"] >= 1

        after = await async_client.get(f"/api/v1/archives/{archive.id}")
        assert after.status_code == 200
        assert after.json()["cost"] == 4.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_archive_with_no_cost_is_still_priced(
        self, async_client: AsyncClient, spoolman_mode, archive_factory, printer_factory, db_session
    ):
        """The guard preserves a figure; it does not stop one being produced.
        An archive that never got a cost still falls to the default rate."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, print_name="NeverPriced", status="completed", cost=None)
        archive.filament_used_grams = 100.0
        archive.filament_type = "PLA"
        await db_session.commit()

        response = await async_client.post("/api/v1/archives/recalculate-costs")
        assert response.status_code == 200

        after = await async_client.get(f"/api/v1/archives/{archive.id}")
        assert after.json()["cost"] == 2.5


class TestRecalculateCostsWithoutSpoolman:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_internal_mode_recalculates_as_before(
        self, async_client: AsyncClient, archive_factory, printer_factory, db_session
    ):
        """With Spoolman off, the catalogue/default path is the only source of
        truth and must still overwrite a stale cost."""
        db_session.add(Settings(key="default_filament_cost", value="25"))
        await db_session.commit()

        printer = await printer_factory()
        archive = await archive_factory(printer.id, print_name="InternalMode", status="completed", cost=999.0)
        archive.filament_used_grams = 100.0
        archive.filament_type = "PLA"
        await db_session.commit()

        response = await async_client.post("/api/v1/archives/recalculate-costs")
        assert response.status_code == 200

        after = await async_client.get(f"/api/v1/archives/{archive.id}")
        assert after.json()["cost"] == 2.5
        await db_session.rollback()
