"""Archives made without their 3MF get ``#`` colours on startup.

They stored the printer's AMS colours as sent (``FF8800FF``), so their
swatches stayed blank; new ones are written as ``#FF8800``. The repair brings
existing archives and the print log entries copied from them in line.
"""

import pytest
from sqlalchemy import select

from backend.app.core.database import _repair_bare_hex_filament_colors
from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

CASES = {
    "FF8800FF": "#FF8800",
    "ff8800,00AE42FF": "#FF8800,#00AE42",
    "#00AE42,FF0000FF": "#00AE42,#FF0000",
    "#00AE42,#FF0000": "#00AE42,#FF0000",
    "Red": "Red",
}


async def _repair(db_session):
    await _repair_bare_hex_filament_colors(await db_session.connection())
    await db_session.commit()
    db_session.expire_all()


async def test_bare_hex_gets_its_hash_on_archives_and_log_entries(db_session, printer_factory):
    printer = await printer_factory()
    for stored in CASES:
        archive = PrintArchive(
            printer_id=printer.id,
            filename="a.3mf",
            file_path="",
            file_size=0,
            status="completed",
            filament_color=stored,
        )
        db_session.add(archive)
        await db_session.flush()
        db_session.add(PrintLogEntry(archive_id=archive.id, status="completed", filament_color=stored))
    await db_session.commit()

    await _repair(db_session)

    archives = [a.filament_color for a in (await db_session.scalars(select(PrintArchive).order_by(PrintArchive.id)))]
    logs = [e.filament_color for e in (await db_session.scalars(select(PrintLogEntry).order_by(PrintLogEntry.id)))]
    assert archives == list(CASES.values())
    assert logs == list(CASES.values())


async def test_running_twice_changes_nothing(db_session, printer_factory):
    printer = await printer_factory()
    db_session.add(
        PrintArchive(
            printer_id=printer.id,
            filename="a.3mf",
            file_path="",
            file_size=0,
            status="completed",
            filament_color="FF8800FF",
        )
    )
    await db_session.commit()

    await _repair(db_session)
    await _repair(db_session)

    assert await db_session.scalar(select(PrintArchive.filament_color)) == "#FF8800"
