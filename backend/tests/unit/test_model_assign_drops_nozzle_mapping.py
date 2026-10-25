"""An "Any <model>" job does not carry a nozzle mapping onto the printer it gets.

Every move from one printer to another resets the printer-specific settings
(drop_printer_resolved), but the model-based assignment dropped only the AMS
mapping. A nozzle mapping on a model job (set through the API, a variant or an
order's plate spec) was then sent as-is and skipped the rack resolve for the
printer actually picked.
"""

import pytest

from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.print_scheduler import PrintScheduler
from backend.tests.unit.test_scheduler_pinned_waiting_reason_3074 import (
    _add_item,
    _item,
    _run,
    ctx,  # noqa: F401 - fixture
)


@pytest.mark.asyncio
async def test_the_picked_printer_works_its_own_mapping_out(ctx):
    """On a nozzle-rack printer (H2C) the mapping names rack positions."""
    from backend.app.models.printer import Printer

    async with ctx.session_maker() as db:
        printer = await db.get(Printer, 1)
        printer.model = "H2C"
        await db.commit()
    item_id = await _add_item(ctx, printer_id=None, target_model="H2C")
    async with ctx.session_maker() as db:
        item = await db.get(PrintQueueItem, item_id)
        item.nozzle_mapping = "[16, 17]"
        library_file = await db.get(LibraryFile, item.library_file_id)
        library_file.file_metadata = {"sliced_for_model": "H2C"}  # else the cross-model gate holds it
        await db.commit()

    launched = await _run(ctx, PrintScheduler(), idle=True)

    launched.assert_called_once()
    item = await _item(ctx, item_id)
    assert item.printer_id == 1
    assert item.nozzle_mapping is None


@pytest.mark.asyncio
async def test_on_a_dual_nozzle_printer_without_a_rack_the_mapping_stays(ctx):
    """An H2D's nozzle mapping holds extruder indices, the same on every H2D:
    the slicer's pick from an "any printer" VP must survive assignment."""
    from backend.app.models.printer import Printer

    async with ctx.session_maker() as db:
        printer = await db.get(Printer, 1)
        printer.model = "H2D"
        await db.commit()
    item_id = await _add_item(ctx, printer_id=None, target_model="H2D")
    async with ctx.session_maker() as db:
        item = await db.get(PrintQueueItem, item_id)
        item.nozzle_mapping = "[0, 1]"
        library_file = await db.get(LibraryFile, item.library_file_id)
        library_file.file_metadata = {"sliced_for_model": "H2D"}
        await db.commit()

    launched = await _run(ctx, PrintScheduler(), idle=True)

    launched.assert_called_once()

    assert (await _item(ctx, item_id)).nozzle_mapping == "[0, 1]"
