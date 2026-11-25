"""The global auto-archive switch is the master over the printers' own.

Off, no printer archives a print started outside Bambuddy; on, each
printer's own switch decides. The global switch used to be saved and never
read, so turning it off changed nothing.
"""

import logging

import pytest
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.models.settings import Settings
from backend.tests.integration.test_external_print_confirmation_1898 import (  # noqa: F401 (autouse fixture)
    SUBTASK,
    _clear_print_state,
    _drive_print_start,
)


async def _archives(db_session) -> list:
    db_session.expire_all()
    return list((await db_session.execute(select(PrintArchive))).scalars().all())


@pytest.mark.asyncio
@pytest.mark.integration
async def test_global_off_archives_nothing(test_engine, db_session, printer_factory, caplog):
    printer = await printer_factory()
    db_session.add(Settings(key="auto_archive", value="false"))
    await db_session.commit()

    with caplog.at_level(logging.INFO, logger="backend.app.main"):
        await _drive_print_start(test_engine, printer, download_ok=False)

    assert await _archives(db_session) == []
    # The log names the switch: upgraded installs had it off without knowing,
    # as it did nothing before 1.3.0 (#3477).
    assert "global 'Auto-archive prints' switch" in caplog.text
    assert "auto-archive is off for printer" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_global_on_lets_the_printer_decide(test_engine, db_session, printer_factory):
    printer = await printer_factory()
    db_session.add(Settings(key="auto_archive", value="true"))
    await db_session.commit()

    await _drive_print_start(test_engine, printer, download_ok=False)

    assert len(await _archives(db_session)) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_printer_switched_off_stays_off_under_global_on(test_engine, db_session, printer_factory, caplog):
    printer = await printer_factory(auto_archive=False)
    printer_id = printer.id

    with caplog.at_level(logging.INFO, logger="backend.app.main"):
        await _drive_print_start(test_engine, printer, download_ok=False)

    assert await _archives(db_session) == []
    assert f"auto-archive is off for printer {printer_id}" in caplog.text
    assert "global 'Auto-archive prints' switch" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_unset_global_lets_the_printer_decide(test_engine, db_session, printer_factory):
    """Installs that never saved the setting have no row; it reads as on."""
    printer = await printer_factory()

    await _drive_print_start(test_engine, printer, download_ok=False)

    assert len(await _archives(db_session)) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_global_off_still_tracks_a_print_bambuddy_sent(test_engine, db_session, printer_factory, archive_factory):
    from backend.app.main import _active_prints, register_expected_print

    printer = await printer_factory()
    archive = await archive_factory(printer.id, filename=f"{SUBTASK}.gcode.3mf", status="pending", with_run=False)
    db_session.add(Settings(key="auto_archive", value="false"))
    await db_session.commit()
    archive_id = archive.id
    register_expected_print(printer.id, f"{SUBTASK}.gcode.3mf", archive_id)

    await _drive_print_start(test_engine, printer, download_ok=False)

    assert [a.id for a in await _archives(db_session)] == [archive_id]
    assert (await db_session.get(PrintArchive, archive_id)).status == "printing"
    assert archive_id in _active_prints.values()
