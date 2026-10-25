"""The print-log estimate covers the plate that printed.

The archive keeps the plate of its first print. A later run of another plate
of the same multi-plate file, dispatched directly or from the queue, has to be
estimated for that plate, not for the archive's.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app import main as main_module
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.bambu_ftp import DeleteResult


@pytest.fixture(autouse=True)
def _clean_state():
    main_module._active_prints.clear()
    main_module._print_plate_ids.clear()
    yield
    main_module._active_prints.clear()
    main_module._print_plate_ids.clear()


async def _complete(test_engine, printer_id: int) -> list:
    """Run on_print_complete against the test database; return the plates the
    estimates were asked for (billing first, then the print log)."""
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
        mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))
        mock_pm.get_current_print_user = MagicMock(return_value=None)
        mock_pm.clear_current_print_user = MagicMock()
        mock_pm.set_awaiting_plate_clear = MagicMock()

        await main_module.on_print_complete(
            printer_id,
            {"status": "completed", "filename": "multi.gcode.3mf", "subtask_name": "multi", "progress": 100},
        )

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
    main_module._print_plate_ids[archive.id] = 3

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
