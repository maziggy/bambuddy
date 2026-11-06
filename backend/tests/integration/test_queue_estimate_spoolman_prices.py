"""The queue's cost estimate prices a library file from the Spoolman spools in
its trays, as it does from built-in spools. Every slot used to be priced at
the default rate in Spoolman mode."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.settings import Settings
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services import print_cost_estimate as pce


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("price,expected", [(60.0, 6.0), (None, 2.5)])
async def test_a_slot_is_priced_from_its_spoolman_spool(db_session, printer_factory, price, expected):
    """100 g from a 60/kg Spoolman spool is 6.00; an unpriced one falls back to the 25/kg default."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    printer = await printer_factory()
    db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=2, spoolman_spool_id=42))
    await db_session.commit()
    client = MagicMock()
    client.get_spool = AsyncMock(
        return_value={"id": 42, "price": price, "initial_weight": 1000.0, "filament": {"weight": 1000.0}}
    )
    library_file = MagicMock(file_metadata={"filament_used_grams": 100.0})

    with (
        patch(
            "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
            AsyncMock(return_value=client),
        ),
        patch("backend.app.services.print_cost_estimate._source_path", return_value=Path("/nonexistent.3mf")),
    ):
        cost = await pce.estimate_queue_source_cost(
            db_session, library_file=library_file, ams_mapping=[2], printer_id=printer.id
        )

    assert cost == expected


@pytest.mark.asyncio
@pytest.mark.integration
async def test_only_the_spools_the_job_uses_are_asked_for(db_session, printer_factory):
    """Runs on queue add and at dispatch: no spool outside the mapped trays,
    and an unreachable Spoolman falls back to the default rate at once."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    printer = await printer_factory()
    db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=2, spoolman_spool_id=42))
    db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=3, spoolman_spool_id=43))
    await db_session.commit()
    client = MagicMock()
    client.get_spool = AsyncMock(return_value={"id": 42, "price": 60.0, "initial_weight": 1000.0})
    library_file = MagicMock(file_metadata={"filament_used_grams": 100.0})

    with (
        patch(
            "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback",
            AsyncMock(return_value=client),
        ),
        patch("backend.app.services.print_cost_estimate._source_path", return_value=Path("/nonexistent.3mf")),
    ):
        await pce.estimate_queue_source_cost(
            db_session, library_file=library_file, ams_mapping=[2], printer_id=printer.id
        )
    client.get_spool.assert_awaited_once_with(42)

    with (
        patch(
            "backend.app.services.spoolman_tracking._get_spoolman_client_with_fallback", AsyncMock(return_value=None)
        ),
        patch("backend.app.services.print_cost_estimate._source_path", return_value=Path("/nonexistent.3mf")),
    ):
        cost = await pce.estimate_queue_source_cost(
            db_session, library_file=library_file, ams_mapping=[2], printer_id=printer.id
        )
    assert cost == 2.5
