"""A print Bambuddy dispatched is adopted whatever it is called; the printer's
own calibration runs are still left out of the archive."""

import pytest

from backend.app import main as main_module
from backend.app.main import register_expected_print
from backend.app.models.archive import PrintArchive
from backend.tests.integration import test_external_print_confirmation_1898 as harness
from backend.tests.integration.test_external_print_confirmation_1898 import (  # noqa: F401
    _clear_print_state,
    _drive_print_start,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

NAME = "pa_line_calib_mode"


@pytest.fixture(autouse=True)
def _calibration_name(monkeypatch):
    monkeypatch.setattr(harness, "SUBTASK", NAME)
    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    main_module._owned_print_archive_ids.clear()
    yield
    main_module._owned_print_archive_ids.clear()


async def test_a_dispatched_print_named_like_a_calibration_is_adopted(test_engine, db_session, printer_factory):
    printer_id = (await printer_factory()).id
    archive = PrintArchive(
        printer_id=printer_id,
        filename=f"{NAME}.gcode.3mf",
        file_path=f"archives/test/{NAME}.gcode.3mf",
        file_size=1,
        print_name=NAME,
        status="pending",
    )
    db_session.add(archive)
    await db_session.commit()
    archive_id = archive.id
    register_expected_print(printer_id, f"{NAME}.gcode.3mf", archive_id)

    await _drive_print_start(test_engine, type("P", (), {"id": printer_id})(), download_ok=False)

    db_session.expire_all()
    adopted = await db_session.get(PrintArchive, archive_id)
    assert adopted.status == "printing"
    assert archive_id in main_module._owned_print_archive_ids


async def test_the_printers_own_calibration_is_still_skipped(test_engine, db_session, printer_factory):
    """Nothing dispatched: a calibration run started on the printer or from the
    slicer is not archived, as before."""
    printer_id = (await printer_factory()).id

    await _drive_print_start(test_engine, type("P", (), {"id": printer_id})(), download_ok=False)

    db_session.expire_all()
    from sqlalchemy import select

    rows = (await db_session.execute(select(PrintArchive).where(PrintArchive.printer_id == printer_id))).scalars().all()
    assert rows == []


async def test_after_a_restart_the_printing_queue_job_says_it_is_bambuddys(test_engine, db_session, printer_factory):
    """The expected entry is in memory only; the queue row is what survives."""
    from sqlalchemy import select

    from backend.app.models.print_queue import PrintQueueItem

    printer_id = (await printer_factory()).id
    archive = PrintArchive(
        printer_id=printer_id,
        filename=f"{NAME}.gcode.3mf",
        file_path="archives/x.3mf",
        file_size=1,
        status="printing",
        billing_run_id="run-9",
    )
    db_session.add(archive)
    await db_session.flush()
    db_session.add(PrintQueueItem(printer_id=printer_id, status="printing", position=1, archive_id=archive.id))
    await db_session.commit()

    await _drive_print_start(
        test_engine, type("P", (), {"id": printer_id})(), download_ok=False, state_fields={"progress": 40}
    )

    db_session.expire_all()
    rows = (await db_session.execute(select(PrintArchive).where(PrintArchive.printer_id == printer_id))).scalars().all()
    assert [row.status for row in rows] == ["printing"]
    assert rows[0].id in main_module._owned_print_archive_ids


async def test_a_queue_job_of_another_file_does_not_count(test_engine, db_session, printer_factory):
    """A row left printing by a run whose end was missed names another file."""
    from sqlalchemy import select

    from backend.app.models.print_queue import PrintQueueItem

    printer_id = (await printer_factory()).id
    other = PrintArchive(
        printer_id=printer_id, filename="Bracket.gcode.3mf", file_path="archives/b.3mf", file_size=1, status="printing"
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add(PrintQueueItem(printer_id=printer_id, status="printing", position=1, archive_id=other.id))
    await db_session.commit()
    other_id = other.id

    await _drive_print_start(test_engine, type("P", (), {"id": printer_id})(), download_ok=False)

    db_session.expire_all()
    rows = (await db_session.execute(select(PrintArchive).where(PrintArchive.printer_id == printer_id))).scalars().all()
    assert [row.id for row in rows] == [other_id]


async def test_a_system_partition_file_is_the_printers_whatever_the_queue_says(
    test_engine, db_session, printer_factory, monkeypatch
):
    """Nothing a user prints lives under /usr/: Bambuddy's own Calibrate runs it."""
    from sqlalchemy import select

    from backend.app.models.print_queue import PrintQueueItem

    monkeypatch.setattr(harness, "SUBTASK", "auto_cali_for_user_param")
    monkeypatch.setattr(harness, "DISPATCH", "/usr/etc/print/O1C2/auto_cali_for_user_param.gcode")
    printer_id = (await printer_factory()).id
    db_session.add(PrintQueueItem(printer_id=printer_id, status="printing", position=1))
    await db_session.commit()

    await _drive_print_start(test_engine, type("P", (), {"id": printer_id})(), download_ok=False)

    db_session.expire_all()
    rows = (await db_session.execute(select(PrintArchive).where(PrintArchive.printer_id == printer_id))).scalars().all()
    assert rows == []
