"""The late slicer update leaves a queue job the scheduler has claimed alone.

The slicer's options can arrive up to 30 s after a virtual-printer job was
queued. By then the scheduler may have claimed the job (``dispatching_at``
set, status still "pending") and started its upload with the old mapping;
rewriting the row then made it, and the spool charge read from it, disagree
with what the printer printed.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_only_the_unclaimed_job_is_restamped(test_engine, db_session, printer_factory, tmp_path):
    printer = await printer_factory()
    claimed = PrintQueueItem(printer_id=printer.id, status="pending", position=1, dispatching_at=datetime.utcnow())
    waiting = PrintQueueItem(printer_id=printer.id, status="pending", position=2)
    db_session.add_all([claimed, waiting])
    await db_session.commit()

    inst = VirtualPrinterInstance(
        target_printer_id=printer.id,
        vp_id=97,
        name="LateMQTTClaimed",
        mode="queue",
        model="O1C2",
        access_code="12345678",
        serial_suffix="391800097",
        base_dir=tmp_path,
        session_factory=async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False),
    )
    inst._mqtt = MagicMock()
    inst._recent_queue_items["test.3mf"] = ([claimed.id, waiting.id], 1_000_000.0)

    with patch("backend.app.services.virtual_printer.manager.time.monotonic", return_value=1_000_001.0):
        await inst.on_print_command(
            "test.3mf", {"command": "project_file", "file": "test.3mf", "nozzle_mapping": [16, -1]}
        )

    await db_session.refresh(claimed)
    await db_session.refresh(waiting)
    assert claimed.nozzle_mapping is None
    assert json.loads(waiting.nozzle_mapping) == [16, -1]


async def test_the_slicer_s_pick_is_still_kept_on_a_claimed_job_s_archive(
    test_engine, db_session, printer_factory, archive_factory, tmp_path
):
    """The archive copy only serves later reprints, so it does not race the
    upload: a claimed job's archive still gets the slicer's AMS pick."""
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    claimed = PrintQueueItem(
        printer_id=printer.id, archive_id=archive.id, status="pending", position=1, dispatching_at=datetime.utcnow()
    )
    db_session.add(claimed)
    await db_session.commit()

    inst = VirtualPrinterInstance(
        target_printer_id=printer.id,
        vp_id=96,
        name="LateMQTTArchive",
        mode="queue",
        model="O1C2",
        access_code="12345678",
        serial_suffix="391800096",
        base_dir=tmp_path,
        session_factory=async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False),
    )
    inst._mqtt = MagicMock()
    inst.save_ams_mapping = True
    inst._recent_queue_items["test.3mf"] = ([claimed.id], 1_000_000.0)

    with patch("backend.app.services.virtual_printer.manager.time.monotonic", return_value=1_000_001.0):
        await inst.on_print_command("test.3mf", {"command": "project_file", "file": "test.3mf", "ams_mapping": [2]})

    await db_session.refresh(archive)
    await db_session.refresh(claimed)
    assert (archive.extra_data or {}).get("slicer_ams_mapping", {}).get("mapping") == [2]
    assert claimed.ams_mapping is None
