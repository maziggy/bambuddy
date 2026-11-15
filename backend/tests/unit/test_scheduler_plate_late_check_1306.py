"""Build plate re-check at dispatch time (#1306).

check_queue matches a job to a printer by its build plate when it *selects*
the job. Preheat and the upload can then take minutes, and the user can swap
the plate on the printer card in that window. ``_start_print`` checks again
before the upload and right before the print command, and hands the job back
to the queue — pending, not failed — if the plate no longer fits.
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
from backend.app.models.build_plate import BuildPlate
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.services.print_scheduler import PrintScheduler
from backend.tests._fixtures.background_tasks import discarding_spawn_patch

TEXTURED, SMOOTH = 1, 2


@pytest.fixture
async def plate_case(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def make_case(*, tracking=True, installed=SMOOTH, target_model=None):
        base_dir = tmp_path / "case"
        base_dir.mkdir(exist_ok=True)
        archive_rel = Path("archives") / "job.3mf"
        archive_abs = base_dir / archive_rel
        archive_abs.parent.mkdir(parents=True, exist_ok=True)
        archive_abs.write_bytes(b"sliced 3mf")

        async with session_maker() as db:
            db.add(BuildPlate(id=TEXTURED, name="Textured PEI Plate", base_type="textured_pei"))
            db.add(BuildPlate(id=SMOOTH, name="Smooth PEI Plate", base_type="smooth_pei"))
            db.add(Settings(key="build_plate_tracking_enabled", value="true" if tracking else "false"))
            printer = Printer(
                name="X1C-01",
                serial_number="SN-X1C",
                ip_address="127.0.0.1",
                access_code="ac",
                model="X1C",
                installed_plate_id=installed,
            )
            db.add(printer)
            await db.flush()
            archive = PrintArchive(
                printer_id=printer.id,
                filename="job.3mf",
                file_path=str(archive_rel),
                file_size=archive_abs.stat().st_size,
                status="completed",
            )
            db.add(archive)
            await db.flush()
            item = PrintQueueItem(
                printer_id=printer.id,
                target_model=target_model,
                archive_id=archive.id,
                status="pending",
                required_plate_type="smooth_pei",
            )
            db.add(item)
            await db.commit()
            return SimpleNamespace(
                session_maker=session_maker,
                base_dir=base_dir,
                printer_id=printer.id,
                queue_item_id=item.id,
                start_print=MagicMock(return_value=True),
                upload=AsyncMock(return_value=True),
                delete=AsyncMock(return_value=True),
            )

    try:
        yield make_case
    finally:
        await engine.dispose()


async def _swap_plate(ctx, plate_id):
    async with ctx.session_maker() as db:
        printer = await db.get(Printer, ctx.printer_id)
        printer.installed_plate_id = plate_id
        await db.commit()


async def _run_start_print(ctx):
    scheduler = PrintScheduler()
    status = SimpleNamespace(nozzles=[], nozzle_rack=[], state="IDLE")
    patches = [
        patch.object(scheduler_module.settings, "base_dir", ctx.base_dir),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=status)),
        patch("backend.app.services.print_scheduler.printer_manager.start_print", ctx.start_print),
        patch("backend.app.services.print_scheduler.printer_manager.set_awaiting_plate_clear", MagicMock()),
        patch("backend.app.services.print_scheduler.upload_file_async", ctx.upload),
        patch("backend.app.services.print_scheduler.delete_file_async", ctx.delete),
        patch("backend.app.services.print_scheduler.cache_3mf_download", MagicMock()),
        discarding_spawn_patch(),
        patch(
            "backend.app.services.print_scheduler.get_ftp_retry_settings", AsyncMock(return_value=(False, 0, 0, 1.0))
        ),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_started", AsyncMock()),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_failed", AsyncMock()),
        patch("backend.app.services.mqtt_relay.mqtt_relay.on_queue_job_started", AsyncMock()),
        patch("backend.app.services.print_scheduler.ws_manager.send_queue_item_failed", AsyncMock()),
        patch.object(scheduler, "_preheat_and_soak", AsyncMock()),
        patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, ctx.queue_item_id)
            await scheduler._start_print(db, item)


async def _item(ctx):
    async with ctx.session_maker() as db:
        return await db.get(PrintQueueItem, ctx.queue_item_id)


@pytest.mark.asyncio
async def test_plate_swapped_before_upload_defers_without_uploading(plate_case):
    """Selected on a smooth plate, swapped to textured before dispatch got going."""
    ctx = await plate_case(installed=TEXTURED)

    await _run_start_print(ctx)

    item = await _item(ctx)
    assert item.status == "pending"
    assert item.waiting_reason == ("Wrong plate: X1C-01 (needs Smooth PEI / High Temp Plate, has Textured PEI Plate)")
    ctx.upload.assert_not_called()
    ctx.start_print.assert_not_called()


@pytest.mark.asyncio
async def test_plate_swapped_during_upload_stops_before_the_print_command(plate_case):
    """The upload window is the gap the selection-time check cannot cover."""
    ctx = await plate_case(installed=SMOOTH)

    async def upload_then_swap(*args, **kwargs):
        await _swap_plate(ctx, TEXTURED)
        return True

    ctx.upload.side_effect = upload_then_swap

    await _run_start_print(ctx)

    ctx.upload.assert_called_once()
    ctx.start_print.assert_not_called()
    ctx.delete.assert_awaited()  # the uploaded file is removed from the printer
    item = await _item(ctx)
    assert item.status == "pending"
    assert item.waiting_reason.startswith("Wrong plate: X1C-01")


@pytest.mark.asyncio
async def test_model_based_job_gives_its_printer_back(plate_case):
    """Otherwise the next pass would treat it as pinned to the wrong printer."""
    ctx = await plate_case(installed=TEXTURED, target_model="X1C")

    await _run_start_print(ctx)

    item = await _item(ctx)
    assert item.status == "pending"
    assert item.printer_id is None


@pytest.mark.asyncio
async def test_matching_plate_prints(plate_case):
    ctx = await plate_case(installed=SMOOTH)

    await _run_start_print(ctx)

    ctx.start_print.assert_called_once()
    assert (await _item(ctx)).status != "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tracking", "installed"),
    [(False, TEXTURED), (True, None)],
    ids=["tracking-off", "printer-untracked"],
)
async def test_no_check_when_tracking_off_or_printer_untracked(plate_case, tracking, installed):
    ctx = await plate_case(tracking=tracking, installed=installed)

    await _run_start_print(ctx)

    ctx.start_print.assert_called_once()
