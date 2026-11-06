"""A re-sliced, never-printed archive reads as not printed and shows a cost.

It was stored with the default status "completed": listed under "Printed" and
counted where completed archives are counted, although nothing was printed.
Unprinted files are "archived" elsewhere (shown as "Not printed"). It also got
grams but no cost, unlike every other archive with grams; the cost is now
priced the same way print start prices it.
"""

import pytest
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.tests.integration.test_slice_archive_output_path_2832 import _slice, archive_root  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _resliced(db_session, response):
    return await db_session.scalar(select(PrintArchive).where(PrintArchive.id == response.archive_id))


async def test_it_is_not_printed(db_session, archive_factory, printer_factory, archive_root):  # noqa: F811
    printer = await printer_factory()
    source = await archive_factory(printer.id, print_name="Bracket", filename="Bracket.3mf")

    archive = await _resliced(db_session, await _slice(db_session, source, "Bracket.3mf"))

    assert archive.status == "archived"
    assert (archive.started_at, archive.completed_at) == (None, None)


async def test_it_is_priced_like_a_printed_archive(db_session, archive_factory, printer_factory, archive_root):  # noqa: F811
    """12.5 g at the default 25 per kg; no filament of that type is on record."""
    printer = await printer_factory()
    source = await archive_factory(printer.id, print_name="Bracket", filename="Bracket.3mf", filament_type="PLA")

    archive = await _resliced(db_session, await _slice(db_session, source, "Bracket.3mf"))

    assert archive.filament_used_grams == 12.5
    assert archive.cost == pytest.approx(0.31)


async def test_the_filament_on_record_sets_the_price(db_session, archive_factory, printer_factory, archive_root):  # noqa: F811
    from backend.app.models.filament import Filament

    db_session.add(Filament(name="House PLA", type="PLA", cost_per_kg=40.0))
    await db_session.commit()
    printer = await printer_factory()
    source = await archive_factory(printer.id, print_name="Bracket", filename="Bracket.3mf", filament_type="PLA")

    archive = await _resliced(db_session, await _slice(db_session, source, "Bracket.3mf"))

    assert archive.cost == pytest.approx(0.5)
