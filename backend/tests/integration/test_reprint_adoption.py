"""A print Bambuddy expected starts on a clean archive, or on a new one if the archive is gone."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from backend.app.main import register_expected_print
from backend.app.models.archive import PrintArchive
from backend.tests.integration.test_external_print_confirmation_1898 import (  # noqa: F401
    SUBTASK,
    _clear_print_state,
    _drive_print_start,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_a_reprint_does_not_keep_the_last_runs_failure(test_engine, db_session, printer_factory):
    printer = await printer_factory()
    printer_id = printer.id
    archive = PrintArchive(
        printer_id=printer_id,
        filename=f"{SUBTASK}.gcode.3mf",
        file_path=f"archives/test/{SUBTASK}.gcode.3mf",
        file_size=1,
        print_name=SUBTASK,
        status="failed",
        failure_reason="cloggedNozzle",
    )
    db_session.add(archive)
    await db_session.flush()
    archive_id = archive.id
    await db_session.commit()
    register_expected_print(printer_id, f"{SUBTASK}.gcode.3mf", archive_id)

    await _drive_print_start(test_engine, SimpleNamespace(id=printer_id), download_ok=False)

    db_session.expire_all()
    row = await db_session.get(PrintArchive, archive_id)
    assert (row.status, row.failure_reason, row.completed_at) == ("printing", None, None)


async def test_a_print_whose_archive_was_deleted_is_archived_anew(test_engine, db_session, printer_factory):
    printer_id = (await printer_factory()).id
    register_expected_print(printer_id, f"{SUBTASK}.gcode.3mf", 999_999)

    await _drive_print_start(test_engine, SimpleNamespace(id=printer_id), download_ok=False)

    db_session.expire_all()
    count = await db_session.scalar(
        select(func.count()).select_from(PrintArchive).where(PrintArchive.printer_id == printer_id)
    )
    assert count == 1


async def test_a_printer_that_does_not_archive_gets_no_archive_for_it(test_engine, db_session, printer_factory):
    printer_id = (await printer_factory(auto_archive=False)).id
    register_expected_print(printer_id, f"{SUBTASK}.gcode.3mf", 999_999)

    await _drive_print_start(test_engine, SimpleNamespace(id=printer_id), download_ok=False)

    db_session.expire_all()
    count = await db_session.scalar(
        select(func.count()).select_from(PrintArchive).where(PrintArchive.printer_id == printer_id)
    )
    assert count == 0
