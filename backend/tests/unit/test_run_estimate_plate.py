"""The print-log estimate covers the plate that printed, and the run keeps it.

The archive holds one plate. A later run of another plate of the same
multi-plate file, dispatched directly or from the queue, has to be estimated
for that plate, not for the archive's, and its print log entry names it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app import main as main_module
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.bambu_ftp import DeleteResult


@pytest.fixture(autouse=True)
def _clean_state():
    main_module._active_prints.clear()
    main_module._print_plate_ids.clear()
    yield
    main_module._active_prints.clear()
    main_module._print_plate_ids.clear()


async def _complete(test_engine, printer_id: int, logged: list | None = None) -> list:
    """Run on_print_complete against the test database; return the plates the
    estimates were asked for (billing first, then the print log). ``logged``
    gets the plates of the print log entries written, read before the
    background work is torn down."""
    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    asked: list = []

    def _estimate(archive, full_path, plate_id=None):
        asked.append(plate_id)
        return None, None

    tasks_before = set(asyncio.all_tasks())
    with (
        patch("backend.app.main.async_session", real_db),
        patch("backend.app.core.database.async_session", real_db),
        patch("backend.app.main._plate_scoped_run_estimate", side_effect=_estimate),
        patch("backend.app.main.ws_manager") as mock_ws,
        patch("backend.app.main.printer_manager") as mock_pm,
        patch("backend.app.main.mqtt_relay") as mock_relay,
        patch("backend.app.main.notification_service") as mock_notif,
        patch("backend.app.main.smart_plug_manager") as mock_plug,
        patch("backend.app.main.clear_3mf_cache"),
        patch("backend.app.services.bambu_ftp.delete_file_async", new=AsyncMock(return_value=DeleteResult.DELETED)),
    ):
        mock_ws.send_print_complete = AsyncMock()
        mock_ws.broadcast = AsyncMock()
        mock_relay.on_print_complete = AsyncMock()
        mock_relay.on_queue_job_completed = AsyncMock()
        mock_notif.on_print_complete = AsyncMock()
        mock_plug.on_print_complete = AsyncMock()
        printer_info = MagicMock(serial_number="TEST123")
        printer_info.name = "Test"  # name= would only name the mock
        mock_pm.get_printer = MagicMock(return_value=printer_info)
        mock_pm.get_current_print_user = MagicMock(return_value=None)
        mock_pm.clear_current_print_user = MagicMock()
        mock_pm.set_awaiting_plate_clear = MagicMock()

        await main_module.on_print_complete(
            printer_id,
            {"status": "completed", "filename": "multi.gcode.3mf", "subtask_name": "multi", "progress": 100},
        )
        if logged is not None:
            async with real_db() as db:
                logged.extend(await db.scalars(select(PrintLogEntry.plate_id)))

        for task in asyncio.all_tasks() - tasks_before:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    return asked


@pytest.mark.asyncio
async def test_a_direct_print_is_estimated_for_its_plate(test_engine, db_session, printer_factory, archive_factory):
    printer = await printer_factory()
    archive = await archive_factory(
        printer.id, filename="multi.gcode.3mf", plate_id=1, status="printing", with_run=False
    )
    main_module._active_prints[(printer.id, "multi.gcode.3mf")] = archive.id
    main_module._print_plate_ids[(printer.id, archive.id)] = 3

    assert await _complete(test_engine, printer.id) == [3, 3]


@pytest.mark.asyncio
async def test_a_queue_run_is_estimated_for_the_queue_items_plate(
    test_engine, db_session, printer_factory, archive_factory
):
    """After a restart the in-memory plate is gone; the queue item still has it."""
    printer = await printer_factory()
    archive = await archive_factory(
        printer.id, filename="multi.gcode.3mf", plate_id=1, status="printing", with_run=False
    )
    db_session.add(PrintQueueItem(printer_id=printer.id, archive_id=archive.id, plate_id=2, status="printing"))
    await db_session.commit()
    main_module._active_prints[(printer.id, "multi.gcode.3mf")] = archive.id

    assert await _complete(test_engine, printer.id) == [2, 2]


@pytest.mark.asyncio
async def test_a_direct_print_logs_its_plate(test_engine, db_session, printer_factory, archive_factory):
    printer = await printer_factory()
    archive = await archive_factory(
        printer.id, filename="multi.gcode.3mf", plate_id=1, status="printing", with_run=False
    )
    main_module._active_prints[(printer.id, "multi.gcode.3mf")] = archive.id
    main_module._print_plate_ids[(printer.id, archive.id)] = 3

    logged: list = []
    await _complete(test_engine, printer.id, logged)

    assert logged == [3]


@pytest.mark.asyncio
async def test_a_queue_run_logs_the_queue_items_plate(test_engine, db_session, printer_factory, archive_factory):
    printer = await printer_factory()
    archive = await archive_factory(
        printer.id, filename="multi.gcode.3mf", plate_id=1, status="printing", with_run=False
    )
    db_session.add(PrintQueueItem(printer_id=printer.id, archive_id=archive.id, plate_id=2, status="printing"))
    await db_session.commit()
    main_module._active_prints[(printer.id, "multi.gcode.3mf")] = archive.id

    logged: list = []
    await _complete(test_engine, printer.id, logged)

    assert logged == [2]


@pytest.mark.asyncio
async def test_a_print_with_no_known_plate_logs_the_archives(test_engine, db_session, printer_factory, archive_factory):
    """Started on the printer: the archive was made for this print, its plate
    read off the printed file's name."""
    printer = await printer_factory()
    archive = await archive_factory(
        printer.id, filename="multi.gcode.3mf", plate_id=4, status="printing", with_run=False
    )
    main_module._active_prints[(printer.id, "multi.gcode.3mf")] = archive.id

    logged: list = []
    await _complete(test_engine, printer.id, logged)

    assert logged == [4]
