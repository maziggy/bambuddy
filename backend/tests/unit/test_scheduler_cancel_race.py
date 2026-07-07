"""Cancel-during-dispatch race regression (#1853).

The user reported: queued a batch of 10 prints, pressed Cancel on a pending
item, the print started anyway. Root cause is a check-then-act race in
``_start_print``: the snapshot of pending items is taken at the top of
``check_queue``, then ``_start_print`` does FTP delete + FTP upload (5-30 s)
before flipping the row to ``"printing"`` and sending MQTT. If the user wins
the race and ``/cancel`` lands during that window, the scheduler's stale
in-memory write of ``status="printing"`` silently overwrites the cancellation.

Three guards exercised here:

* Early refresh after the connectivity check — bails before FTP I/O if the
  row is already cancelled.
* Atomic CAS at the pending→printing transition — UPDATE WHERE
  status='pending'; rowcount==0 means user won, do NOT send MQTT.
* Best-effort delete of the file we just FTP'd up when the CAS aborts.
"""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
import backend.app.services.print_scheduler as scheduler_module
from backend.app.core.database import Base
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
async def queue_factory(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    case_counter = 0

    async def make_case(*, status="pending"):
        nonlocal case_counter
        case_counter += 1

        base_dir = tmp_path / f"case-{case_counter}"
        base_dir.mkdir()
        archive_rel = Path("archives") / f"job-{case_counter}.3mf"
        archive_abs = base_dir / archive_rel
        archive_abs.parent.mkdir(parents=True, exist_ok=True)
        archive_abs.write_bytes(b"archive payload")

        async with session_maker() as db:
            printer = Printer(
                name=f"Printer {case_counter}",
                serial_number=f"SERIAL-{case_counter}",
                ip_address="127.0.0.1",
                access_code="access-code",
                model="X1C",
            )
            db.add(printer)
            await db.flush()

            archive = PrintArchive(
                printer_id=printer.id,
                filename=f"job-{case_counter}.3mf",
                file_path=str(archive_rel),
                file_size=archive_abs.stat().st_size,
                content_hash=None,
                thumbnail_path=None,
                timelapse_path=None,
                print_time_seconds=120,
                status="completed",
            )
            db.add(archive)
            await db.flush()

            item = PrintQueueItem(
                printer_id=printer.id,
                archive_id=archive.id,
                status=status,
                bed_levelling=True,
                flow_cali=False,
                vibration_cali=True,
                layer_inspect=False,
                timelapse=False,
                use_ams=True,
                nozzle_offset_cali=True,
            )
            db.add(item)
            await db.commit()

            return SimpleNamespace(
                session_maker=session_maker,
                base_dir=base_dir,
                archive_path=archive_abs,
                printer_id=printer.id,
                archive_id=archive.id,
                queue_item_id=item.id,
                upload=AsyncMock(return_value=True),
                start_print=MagicMock(return_value=True),
                delete_file=AsyncMock(return_value=True),
            )

    try:
        yield make_case
    finally:
        await engine.dispose()


async def _dispatch(ctx, *, upload_side_effect=None):
    scheduler = PrintScheduler()

    if upload_side_effect is not None:
        ctx.upload.side_effect = upload_side_effect

    patches = [
        patch.object(scheduler_module.settings, "base_dir", ctx.base_dir),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager.start_print", ctx.start_print),
        patch("backend.app.services.print_scheduler.printer_manager.set_awaiting_plate_clear", MagicMock()),
        patch(
            "backend.app.services.print_scheduler.get_ftp_retry_settings",
            AsyncMock(return_value=(False, 0, 0, 1.0)),
        ),
        patch("backend.app.services.print_scheduler.delete_file_async", ctx.delete_file),
        patch("backend.app.services.print_scheduler.upload_file_async", ctx.upload),
        patch("backend.app.services.print_scheduler.cache_3mf_download", MagicMock()),
        patch("backend.app.services.print_scheduler.spawn_background_task", MagicMock()),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_started",
            AsyncMock(),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_failed",
            AsyncMock(),
        ),
        patch("backend.app.services.mqtt_relay.mqtt_relay.on_queue_job_started", AsyncMock()),
        patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
        patch.object(scheduler, "_preheat_and_soak", AsyncMock()),
    ]

    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)

        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, ctx.queue_item_id)
            await scheduler._start_print(db, item)


async def _final_status(ctx):
    async with ctx.session_maker() as db:
        item = await db.get(PrintQueueItem, ctx.queue_item_id)
        return item.status, item.started_at


@pytest.mark.asyncio
async def test_cancel_during_ftp_upload_aborts_before_mqtt(queue_factory):
    """User wins the race during the FTP upload — CAS must detect & bail.

    This is the headline #1853 scenario: snapshot saw pending, FTP upload
    starts, user clicks Cancel, /cancel commits ``cancelled`` to the row,
    FTP finishes successfully, scheduler reaches the CAS. CAS rowcount must
    be 0; ``printer_manager.start_print`` must NOT be called; row must stay
    ``cancelled``; uploaded file must be deleted from the printer's SD.
    """
    ctx = await queue_factory()

    async def cancel_mid_upload(*args, **kwargs):
        # Simulate /cancel landing in a separate session while FTP is in
        # flight. The endpoint commits status='cancelled' then returns 200.
        async with ctx.session_maker() as other_db:
            other_item = await other_db.get(PrintQueueItem, ctx.queue_item_id)
            other_item.status = "cancelled"
            await other_db.commit()
        return True

    await _dispatch(ctx, upload_side_effect=cancel_mid_upload)

    status, started_at = await _final_status(ctx)
    assert status == "cancelled", "CAS overwrote the user's cancellation"
    assert started_at is None, "started_at must not be stamped on a cancelled row"
    ctx.start_print.assert_not_called()
    # Two delete calls — the pre-upload sweep and the post-CAS cleanup.
    assert ctx.delete_file.await_count == 2


@pytest.mark.asyncio
async def test_cancel_before_ftp_upload_skips_dispatch(queue_factory):
    """Early-refresh path: row was cancelled before _start_print resumed.

    Mirrors the case where ``/cancel`` lands between the ``check_queue``
    snapshot and the time ``_start_print`` runs. The early ``db.refresh``
    after the connectivity check sees ``cancelled`` and returns immediately
    — no FTP upload, no MQTT send, row unchanged.
    """
    ctx = await queue_factory()

    # Flip to cancelled before _start_print runs; the in-memory snapshot
    # the scheduler holds still reads 'pending', exactly the bug shape.
    async with ctx.session_maker() as other_db:
        item = await other_db.get(PrintQueueItem, ctx.queue_item_id)
        item.status = "cancelled"
        await other_db.commit()

    await _dispatch(ctx)

    status, started_at = await _final_status(ctx)
    assert status == "cancelled"
    assert started_at is None
    ctx.upload.assert_not_awaited()
    ctx.start_print.assert_not_called()


@pytest.mark.asyncio
async def test_happy_path_still_dispatches(queue_factory):
    """Sanity: no cancel, no race — pending row flips to printing, MQTT fires.

    Regression guard so the CAS doesn't accidentally block normal dispatch
    on a row that was always pending.
    """
    ctx = await queue_factory()

    await _dispatch(ctx)

    status, started_at = await _final_status(ctx)
    assert status == "printing"
    assert started_at is not None
    ctx.upload.assert_awaited_once()
    ctx.start_print.assert_called_once()
