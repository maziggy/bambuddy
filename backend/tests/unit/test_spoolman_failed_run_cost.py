"""A failed or stopped print in Spoolman mode is logged with its own cost.

The run's cost came only from the built-in tracker's results, or from the
estimate for a completed print, so a failed Spoolman print had none in Print
History. Its Spoolman charges are priced as they are made, and that is the
run's cost, as with the built-in inventory.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app import main as main_module
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
        mock_pm.get_printer = MagicMock(return_value=SimpleNamespace(name="Test", serial_number="TEST123"))
        mock_pm.get_current_print_user = MagicMock(return_value=None)
        mock_pm.clear_current_print_user = MagicMock()
        mock_pm.set_awaiting_plate_clear = MagicMock()

        await main_module.on_print_complete(
            printer_id,
            {"status": "failed", "filename": "multi.gcode.3mf", "subtask_name": "multi", "progress": 40},
        )

        for task in asyncio.all_tasks() - tasks_before:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    return asked


@pytest.mark.asyncio
async def test_a_failed_spoolman_print_is_logged_with_what_its_spools_cost(
    test_engine, db_session, printer_factory, archive_factory
):
    printer = await printer_factory()
    archive = await archive_factory(printer.id, filename="multi.gcode.3mf", status="printing", with_run=False)
    archive_id = archive.id
    main_module._active_prints[(printer.id, "multi.gcode.3mf")] = archive_id

    with (
        patch("backend.app.main._cleanup_spoolman_tracking", AsyncMock(return_value=3.5)),
        patch("backend.app.services.print_log.write_log_entry", new_callable=AsyncMock) as logged,
        patch("backend.app.services.print_log.record_archive_wear", new_callable=AsyncMock),
    ):
        await _complete(test_engine, printer.id)

    logged.assert_awaited_once()
    assert (logged.await_args.kwargs["archive_id"], logged.await_args.kwargs["cost"]) == (archive_id, 3.5)
