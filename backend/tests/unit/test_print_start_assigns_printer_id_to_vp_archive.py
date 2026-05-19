"""Regression for #1403 follow-up: when on_print_start reuses a VP-queue archive,
it must assign archive.printer_id so the post-print "Scan for timelapse" path
in the archive UI isn't disabled forever.

Reporter @pwostran and @enjoylifenow both saw "Scan for timelapse" greyed out on
archives that came from the VP print-queue flow even though the H.264 timelapse
was sitting on the printer's SD card. The frontend gates that button on
``!archive.printer_id`` (ArchivesPage.tsx:459). VP-queue archives are created
with ``printer_id=None`` at queue-add time because we don't know which printer
will run the job yet; the print-start handler's expected-archive branch updated
status / started_at / subtask_id but never set printer_id, so the archive stayed
unassigned forever.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.main import (
    _active_prints,
    _expected_print_creators,
    _expected_print_registered_at,
    _expected_prints,
    _print_ams_mappings,
    register_expected_print,
)


@pytest.fixture(autouse=True)
def _clear_dicts():
    _expected_prints.clear()
    _expected_print_registered_at.clear()
    _expected_print_creators.clear()
    _print_ams_mappings.clear()
    _active_prints.clear()
    yield
    _expected_prints.clear()
    _expected_print_registered_at.clear()
    _expected_print_creators.clear()
    _print_ams_mappings.clear()
    _active_prints.clear()


@pytest.mark.asyncio
async def test_expected_archive_path_assigns_printer_id_when_unset():
    """VP-queue archives land here with printer_id=None and must be promoted
    to the printer that actually started the job. Without this the
    /archives/{id}/timelapse/scan endpoint refuses the request (it requires
    archive.printer_id) and the UI button stays disabled."""
    mock_printer = MagicMock()
    mock_printer.id = 1
    mock_printer.auto_archive = True
    mock_printer.external_camera_enabled = False
    mock_printer.external_camera_url = None
    mock_printer.name = "TestP1S"

    # VP-queue archive: printer_id is None — this is the bug surface.
    mock_archive = MagicMock()
    mock_archive.id = 42
    mock_archive.filename = "bambu_lab_a1_tool_plate_3.gcode.3mf"
    mock_archive.subtask_id = None
    mock_archive.print_time_seconds = None
    mock_archive.created_by_id = None
    mock_archive.printer_id = None
    mock_archive.print_name = "A1 Tool Plate 3"
    mock_archive.status = "archived"
    mock_archive.file_path = "/tmp/fake.3mf"
    mock_archive.energy_start_kwh = None

    register_expected_print(1, "bambu_lab_a1_tool_plate_3.gcode.3mf", archive_id=42, ams_mapping=None)

    def execute_router(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "from printers" in sql or "from printer " in sql:
            return MagicMock(
                scalar_one_or_none=MagicMock(return_value=mock_printer),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_printer]))),
            )
        if "from print_archives" in sql or "from print_archive" in sql:
            return MagicMock(
                scalar_one_or_none=MagicMock(return_value=mock_archive),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_archive]))),
            )
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_router)
    mock_session.commit = AsyncMock()

    with (
        patch("backend.app.main.async_session") as mock_session_maker,
        patch("backend.app.main.notification_service") as mock_notif,
        patch("backend.app.main.smart_plug_manager") as mock_plug,
        patch("backend.app.main.ws_manager") as mock_ws,
        patch("backend.app.main.printer_manager") as mock_pm,
        patch("backend.app.main.mqtt_relay") as mock_relay,
        patch("backend.app.main._record_energy_start", new_callable=AsyncMock),
        patch("backend.app.main._load_objects_from_archive"),
        patch("backend.app.main._store_spoolman_print_data", new_callable=AsyncMock),
        patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
    ):
        mock_session_maker.return_value = mock_session
        mock_notif.on_print_start = AsyncMock()
        mock_plug.on_print_start = AsyncMock()
        mock_ws.send_print_start = AsyncMock()
        mock_ws.send_archive_updated = AsyncMock()
        mock_relay.on_print_start = AsyncMock()
        mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))

        from backend.app.main import on_print_start

        await on_print_start(
            1,
            {
                "filename": "bambu_lab_a1_tool_plate_3.gcode.3mf",
                "subtask_name": "bambu_lab_a1_tool_plate_3",
            },
        )

        assert mock_archive.printer_id == 1, (
            "expected-archive branch must assign the running printer_id so the "
            "post-print timelapse-scan path (gated on archive.printer_id) works"
        )
        assert mock_archive.status == "printing"


@pytest.mark.asyncio
async def test_expected_archive_path_preserves_existing_printer_id():
    """Defensive: if the archive already carries a printer_id (e.g. a
    library-file-based queue item created with the printer pre-assigned),
    don't clobber it with a stale value. The branch is idempotent on
    correct data."""
    mock_printer = MagicMock()
    mock_printer.id = 7
    mock_printer.auto_archive = True
    mock_printer.external_camera_enabled = False
    mock_printer.external_camera_url = None
    mock_printer.name = "TestP1S"

    mock_archive = MagicMock()
    mock_archive.id = 99
    mock_archive.filename = "MyModel.3mf"
    mock_archive.subtask_id = None
    mock_archive.print_time_seconds = None
    mock_archive.created_by_id = None
    mock_archive.printer_id = 7  # already correct
    mock_archive.print_name = "MyModel"
    mock_archive.status = "archived"
    mock_archive.file_path = "/tmp/fake.3mf"
    mock_archive.energy_start_kwh = None

    register_expected_print(7, "MyModel.3mf", archive_id=99, ams_mapping=None)

    def execute_router(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        if "from printers" in sql or "from printer " in sql:
            return MagicMock(
                scalar_one_or_none=MagicMock(return_value=mock_printer),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_printer]))),
            )
        if "from print_archives" in sql or "from print_archive" in sql:
            return MagicMock(
                scalar_one_or_none=MagicMock(return_value=mock_archive),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_archive]))),
            )
        return MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_router)
    mock_session.commit = AsyncMock()

    with (
        patch("backend.app.main.async_session") as mock_session_maker,
        patch("backend.app.main.notification_service") as mock_notif,
        patch("backend.app.main.smart_plug_manager") as mock_plug,
        patch("backend.app.main.ws_manager") as mock_ws,
        patch("backend.app.main.printer_manager") as mock_pm,
        patch("backend.app.main.mqtt_relay") as mock_relay,
        patch("backend.app.main._record_energy_start", new_callable=AsyncMock),
        patch("backend.app.main._load_objects_from_archive"),
        patch("backend.app.main._store_spoolman_print_data", new_callable=AsyncMock),
        patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
    ):
        mock_session_maker.return_value = mock_session
        mock_notif.on_print_start = AsyncMock()
        mock_plug.on_print_start = AsyncMock()
        mock_ws.send_print_start = AsyncMock()
        mock_ws.send_archive_updated = AsyncMock()
        mock_relay.on_print_start = AsyncMock()
        mock_pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))

        from backend.app.main import on_print_start

        await on_print_start(7, {"filename": "MyModel.3mf", "subtask_name": "MyModel"})

        assert mock_archive.printer_id == 7
        assert mock_archive.status == "printing"
