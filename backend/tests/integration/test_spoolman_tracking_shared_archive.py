"""Spoolman tracking for an archive printing on several printers at once.

A batch dispatches one archive as several queue items. The print-start
capture looked the queue item up by archive alone and insisted on one row, so
with two copies printing it raised and the print was never charged.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.active_print_spoolman import ActivePrintSpoolman  # registers its table
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.spoolman_tracking import store_print_data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_each_printer_reads_its_own_queue_item(db_session, printer_factory, archive_factory):
    from sqlalchemy import select

    printer_a = await printer_factory()
    printer_b = await printer_factory()
    archive = await archive_factory(printer_a.id, status="printing", with_run=False)
    db_session.add(PrintQueueItem(printer_id=printer_a.id, archive_id=archive.id, plate_id=2, status="printing"))
    db_session.add(PrintQueueItem(printer_id=printer_b.id, archive_id=archive.id, plate_id=3, status="printing"))
    await db_session.commit()
    plates: list = []

    printer_manager = MagicMock()
    printer_manager.get_status.return_value = SimpleNamespace(
        raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA"}]}]}
    )
    mock_settings = MagicMock()
    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_settings.base_dir.__truediv__.return_value = mock_path

    def _usage(path, plate_id=None):
        plates.append(plate_id)
        return [{"slot_id": 1, "used_g": 5.0, "type": "PLA", "color": "#FF0000"}]

    with (
        patch("backend.app.services.spoolman_tracking.app_settings", mock_settings),
        patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")),
        patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", side_effect=_usage),
        patch("backend.app.utils.threemf_tools.extract_layer_filament_usage_from_3mf", return_value=None),
        patch("backend.app.utils.threemf_tools.extract_filament_properties_from_3mf", return_value={}),
    ):
        await store_print_data(
            printer_id=printer_a.id,
            archive_id=archive.id,
            file_path="archives/test.3mf",
            db=db_session,
            printer_manager=printer_manager,
            ams_mapping=[0],
        )

    assert plates == [2]
    rows = (await db_session.execute(select(ActivePrintSpoolman))).scalars().all()
    assert [r.printer_id for r in rows] == [printer_a.id]
