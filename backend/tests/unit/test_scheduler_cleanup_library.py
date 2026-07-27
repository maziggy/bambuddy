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
from backend.app.models.library import LibraryFile
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

    async def make_case(*, cleanup=True, is_external=False, thumbnail_path=None):
        nonlocal case_counter
        case_counter += 1

        base_dir = tmp_path / f"case-{case_counter}"
        base_dir.mkdir()
        source_path = base_dir / "library" / f"source-{case_counter}.3mf"
        source_path.parent.mkdir()
        source_path.write_bytes(b"library source")

        thumbnail_actual_path = None
        thumbnail_db_path = None
        if thumbnail_path == "relative":
            thumbnail_db_path = f"thumbs/preview-{case_counter}.png"
            thumbnail_actual_path = base_dir / thumbnail_db_path
        elif thumbnail_path == "absolute":
            thumbnail_actual_path = tmp_path / f"absolute-preview-{case_counter}.png"
            thumbnail_db_path = str(thumbnail_actual_path)
        elif thumbnail_path is not None:
            thumbnail_actual_path = Path(thumbnail_path)
            thumbnail_db_path = str(thumbnail_path)

        if thumbnail_actual_path:
            thumbnail_actual_path.parent.mkdir(parents=True, exist_ok=True)
            thumbnail_actual_path.write_bytes(b"thumbnail")

        async with session_maker() as db:
            printer = Printer(
                name=f"Printer {case_counter}",
                serial_number=f"SERIAL-{case_counter}",
                ip_address="127.0.0.1",
                access_code="access-code",
                model="X1C",
            )
            library_file = LibraryFile(
                filename=f"source-{case_counter}.3mf",
                file_path=str(source_path),
                file_type="3mf",
                file_size=source_path.stat().st_size,
                file_hash=None,
                thumbnail_path=thumbnail_db_path,
                file_metadata=None,
                is_external=is_external,
            )
            db.add_all([printer, library_file])
            await db.flush()

            item = PrintQueueItem(
                printer_id=printer.id,
                library_file_id=library_file.id,
                status="pending",
                cleanup_library_after_dispatch=cleanup,
                bed_levelling="on",
                flow_cali="off",
                vibration_cali=True,
                layer_inspect=False,
                timelapse=False,
                use_ams=True,
                nozzle_offset_cali="on",
            )
            db.add(item)
            await db.commit()

            return SimpleNamespace(
                session_maker=session_maker,
                base_dir=base_dir,
                source_path=source_path,
                thumbnail_path=thumbnail_actual_path,
                printer_id=printer.id,
                library_file_id=library_file.id,
                queue_item_id=item.id,
                archive_path=None,
                upload=AsyncMock(return_value=True),
                start_print=MagicMock(return_value=True),
            )

    try:
        yield make_case
    finally:
        await engine.dispose()


async def _dispatch_library_item(ctx, *, archive_failure=False, unlink_side_effect=None):
    scheduler = PrintScheduler()

    async def archive_print(
        self, *, printer_id, source_file, original_filename, created_by_id=None, project_id=None, plate_id=None
    ):
        if archive_failure:
            raise RuntimeError("archive copy failed")

        archive_rel_path = Path("archives") / f"archive-{ctx.queue_item_id}.3mf"
        ctx.archive_path = ctx.base_dir / archive_rel_path
        ctx.archive_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.archive_path.write_bytes(Path(source_file).read_bytes())

        archive = PrintArchive(
            printer_id=printer_id,
            filename=original_filename,
            file_path=str(archive_rel_path),
            file_size=ctx.archive_path.stat().st_size,
            content_hash=None,
            thumbnail_path=None,
            timelapse_path=None,
            print_time_seconds=120,
            status="completed",
            project_id=project_id,
            created_by_id=created_by_id,
        )
        self.db.add(archive)
        await self.db.flush()
        return archive

    patches = [
        patch.object(scheduler_module.settings, "base_dir", ctx.base_dir),
        patch("backend.app.services.archive.ArchiveService.archive_print", new=archive_print),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager.start_print", ctx.start_print),
        patch("backend.app.services.print_scheduler.printer_manager.set_awaiting_plate_clear", MagicMock()),
        patch(
            "backend.app.services.print_scheduler.get_ftp_retry_settings", AsyncMock(return_value=(False, 0, 0, 1.0))
        ),
        patch("backend.app.services.print_scheduler.delete_file_async", AsyncMock(return_value=True)),
        patch("backend.app.services.print_scheduler.upload_file_async", ctx.upload),
        patch("backend.app.services.print_scheduler.cache_3mf_download", MagicMock()),
        patch("backend.app.services.print_scheduler.spawn_background_task", MagicMock()),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_started", AsyncMock()),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_failed", AsyncMock()),
        patch("backend.app.services.mqtt_relay.mqtt_relay.on_queue_job_started", AsyncMock()),
        patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
    ]
    if unlink_side_effect:
        patches.append(patch.object(type(ctx.source_path), "unlink", unlink_side_effect))

    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)

        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, ctx.queue_item_id)
            await scheduler._start_print(db, item)


async def _queue_snapshot(ctx):
    async with ctx.session_maker() as db:
        item = await db.get(PrintQueueItem, ctx.queue_item_id)
        library_file = await db.get(LibraryFile, ctx.library_file_id)
        archive = await db.get(PrintArchive, item.archive_id) if item.archive_id else None
        return item, library_file, archive


@pytest.mark.asyncio
async def test_cleanup_unlinks_library_file_and_removes_db_row(queue_factory):
    ctx = await queue_factory(cleanup=True)

    await _dispatch_library_item(ctx)

    item, library_file, archive = await _queue_snapshot(ctx)
    assert item.status == "printing"
    assert item.library_file_id is None
    assert item.archive_id == archive.id
    assert library_file is None
    assert not ctx.source_path.exists()


@pytest.mark.asyncio
async def test_external_library_file_skips_cleanup(queue_factory):
    ctx = await queue_factory(cleanup=True, is_external=True)

    await _dispatch_library_item(ctx)

    item, library_file, archive = await _queue_snapshot(ctx)
    assert item.status == "printing"
    assert item.library_file_id == ctx.library_file_id
    assert item.archive_id == archive.id
    assert library_file is not None
    assert ctx.source_path.exists()


@pytest.mark.asyncio
async def test_archive_creation_failure_skips_cleanup_and_dispatch(queue_factory):
    ctx = await queue_factory(cleanup=True, thumbnail_path="relative")

    await _dispatch_library_item(ctx, archive_failure=True)

    item, library_file, archive = await _queue_snapshot(ctx)
    assert item.status == "failed"
    assert item.error_message == "Failed to create archive from library file"
    assert item.archive_id is None
    assert archive is None
    assert library_file is not None
    assert ctx.source_path.exists()
    assert ctx.thumbnail_path.exists()
    ctx.upload.assert_not_awaited()
    ctx.start_print.assert_not_called()


@pytest.mark.parametrize("thumbnail_path", ["absolute", "relative"])
@pytest.mark.asyncio
async def test_cleanup_resolves_absolute_and_relative_thumbnail_paths(queue_factory, thumbnail_path):
    ctx = await queue_factory(cleanup=True, thumbnail_path=thumbnail_path)

    await _dispatch_library_item(ctx)

    item, library_file, archive = await _queue_snapshot(ctx)
    assert item.status == "printing"
    assert item.archive_id == archive.id
    assert library_file is None
    assert not ctx.source_path.exists()
    assert not ctx.thumbnail_path.exists()


@pytest.mark.asyncio
async def test_archive_copy_survives_library_cleanup(queue_factory):
    ctx = await queue_factory(cleanup=True)

    await _dispatch_library_item(ctx)

    assert not ctx.source_path.exists()
    assert ctx.archive_path.exists()
    assert ctx.archive_path.read_bytes() == b"library source"
    uploaded_path = ctx.upload.await_args.args[2]
    assert uploaded_path == ctx.archive_path


@pytest.mark.asyncio
async def test_oserror_during_unlink_logs_orphan_path_and_does_not_crash_dispatch(queue_factory, caplog):
    ctx = await queue_factory(cleanup=True, thumbnail_path="relative")
    original_unlink = type(ctx.source_path).unlink

    def unlink_with_source_failure(path, *args, **kwargs):
        if Path(path) == ctx.source_path:
            raise OSError("permission denied")
        return original_unlink(path, *args, **kwargs)

    with caplog.at_level("WARNING", logger="backend.app.services.print_scheduler"):
        await _dispatch_library_item(ctx, unlink_side_effect=unlink_with_source_failure)

    item, library_file, archive = await _queue_snapshot(ctx)
    assert item.status == "printing"
    assert item.archive_id == archive.id
    assert item.library_file_id is None
    assert library_file is None
    assert ctx.source_path.exists()
    assert not ctx.thumbnail_path.exists()
    assert ctx.archive_path.exists()
    assert "TRANSIENT_LIBRARY_FILE_ORPHAN" in caplog.text
    assert str(ctx.source_path) in caplog.text
    assert "permission denied" in caplog.text
