"""Concurrent queue dispatch across printers (#2555).

Reported as "prints are sent to the printer one by one, very slowly" on a
19-printer farm — up to an hour before the last printer started. Not a config
problem: ``check_queue`` awaited ``_start_print`` inline for each pending item,
and ``_start_print`` performs the FTP upload, so every printer queued behind
every other printer's transfer. A Bambu printer's FTP server sustains ~150 KB/s
(its own SD write is the bottleneck, not the network), so the reporter's 41 MB
3MF took ~254 s *per printer* — 19 of those in series is ~80 minutes.

Printers are independent machines, so the uploads have no reason to be
serialized. They now run concurrently, capped by ``queue_max_concurrent_uploads``.

What must stay true:

* Uploads to different printers overlap in time (the actual fix).
* No more than ``queue_max_concurrent_uploads`` run at once (the host is not
  infinite: each in-flight upload holds an FTP thread, a TLS session, a handle).
* Setting it to 1 restores exactly the old serial behaviour.
* One printer failing must not cancel its siblings' in-flight uploads.
* A pass still never overlaps with the next one — ``_start_print`` flips the row
  pending -> printing only *after* the upload completes, so returning early
  while uploads were in flight would let the next pass re-dispatch the same rows.
"""

import asyncio
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
import backend.app.services.archive as archive_module
import backend.app.services.print_scheduler as scheduler_module
from backend.app.core.database import Base
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.services.print_scheduler import PrintScheduler

UPLOAD_SECONDS = 0.15


@pytest.fixture
async def farm(tmp_path):
    """Build a farm of N printers, each with one pending queue item."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def make_farm(printer_count: int, *, max_concurrent: int | None = None):
        base_dir = tmp_path / "farm"
        (base_dir / "archives").mkdir(parents=True, exist_ok=True)

        async with session_maker() as db:
            if max_concurrent is not None:
                db.add(Settings(key="queue_max_concurrent_uploads", value=str(max_concurrent)))

            printer_ids = []
            for n in range(printer_count):
                archive_rel = Path("archives") / f"job-{n}.3mf"
                (base_dir / archive_rel).write_bytes(b"archive payload")

                printer = Printer(
                    name=f"Printer {n}",
                    serial_number=f"SERIAL-{n}",
                    ip_address=f"10.0.0.{n + 1}",
                    access_code="access-code",
                    model="A1",
                )
                db.add(printer)
                await db.flush()

                archive = PrintArchive(
                    printer_id=printer.id,
                    filename=f"job-{n}.3mf",
                    file_path=str(archive_rel),
                    file_size=15,
                    print_time_seconds=120,
                    status="completed",
                )
                db.add(archive)
                await db.flush()

                db.add(
                    PrintQueueItem(
                        printer_id=printer.id,
                        archive_id=archive.id,
                        status="pending",
                        position=n,
                    )
                )
                printer_ids.append(printer.id)
            await db.commit()

        return SimpleNamespace(
            session_maker=session_maker,
            base_dir=base_dir,
            printer_ids=printer_ids,
        )

    try:
        yield make_farm
    finally:
        await engine.dispose()


class _UploadRecorder:
    """Stands in for ``upload_file_async``; records overlap.

    Each call sleeps, so genuinely concurrent uploads have overlapping
    lifetimes. ``peak`` is the high-water mark of simultaneous in-flight
    uploads — the number the whole fix turns on.
    """

    def __init__(self, *, fail_for_ip: str | None = None):
        self.in_flight = 0
        self.peak = 0
        self.order: list[str] = []
        self.fail_for_ip = fail_for_ip

    async def __call__(self, ip_address, access_code, local_path, remote_path, **kwargs):
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        self.order.append(ip_address)
        try:
            await asyncio.sleep(UPLOAD_SECONDS)
            if self.fail_for_ip is not None and ip_address == self.fail_for_ip:
                raise OSError(f"simulated FTP failure for {ip_address}")
            return True
        finally:
            self.in_flight -= 1


async def _run_check_queue(ctx, upload, job_started=None):
    scheduler = PrintScheduler()
    job_started = job_started or AsyncMock()

    patches = [
        patch.object(scheduler_module.settings, "base_dir", ctx.base_dir),
        # The library-file path archives the 3MF before uploading it, and the
        # archive service resolves its own settings — redirect both or it writes
        # into the real repo and then fails relative_to(base_dir).
        patch.object(archive_module.settings, "base_dir", ctx.base_dir),
        patch.object(archive_module.settings, "archive_dir", ctx.base_dir / "archive"),
        patch("backend.app.services.print_scheduler.async_session", ctx.session_maker),
        patch("backend.app.core.database.async_session", ctx.session_maker),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager.start_print", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.set_awaiting_plate_clear", MagicMock()),
        patch("backend.app.services.print_scheduler.upload_file_async", upload),
        patch("backend.app.services.print_scheduler.delete_file_async", AsyncMock(return_value=True)),
        patch(
            "backend.app.services.print_scheduler.get_ftp_retry_settings",
            AsyncMock(return_value=(False, 0, 0, 1.0)),
        ),
        patch("backend.app.services.print_scheduler.cache_3mf_download", MagicMock()),
        patch("backend.app.services.print_scheduler.spawn_background_task", MagicMock()),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_started", job_started),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_failed", AsyncMock()),
        patch("backend.app.services.mqtt_relay.mqtt_relay.on_queue_job_started", AsyncMock()),
        patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
        patch.object(scheduler, "_preheat_and_soak", AsyncMock()),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
    ]

    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        await scheduler.check_queue()


async def _statuses(ctx):
    async with ctx.session_maker() as db:
        rows = (await db.execute(select(PrintQueueItem).order_by(PrintQueueItem.position))).scalars().all()
        return [r.status for r in rows]


@pytest.mark.asyncio
async def test_uploads_to_different_printers_overlap(farm):
    """The headline fix: six printers must not queue behind each other.

    Pre-fix this recorded peak == 1 no matter how many printers were pending.
    """
    ctx = await farm(6, max_concurrent=6)
    upload = _UploadRecorder()

    await _run_check_queue(ctx, upload)

    assert upload.peak == 6, (
        f"expected all 6 printers to be uploaded to concurrently, but the "
        f"high-water mark was {upload.peak} — uploads are still serialized"
    )
    assert await _statuses(ctx) == ["printing"] * 6


@pytest.mark.asyncio
async def test_concurrency_is_capped_by_the_setting(farm):
    """Eight pending printers, cap of 3 — never more than 3 uploads at once.

    The cap is the reason this is a setting and not just ``asyncio.gather``:
    the printers are independent but the Bambuddy host is not.
    """
    ctx = await farm(8, max_concurrent=3)
    upload = _UploadRecorder()

    await _run_check_queue(ctx, upload)

    assert upload.peak == 3, f"cap of 3 not honoured — peak was {upload.peak}"
    assert len(upload.order) == 8, "every pending item must still be dispatched, just not all at once"
    assert await _statuses(ctx) == ["printing"] * 8


@pytest.mark.asyncio
async def test_limit_of_one_restores_serial_behaviour(farm):
    """An escape hatch for weak networks: 1 == the pre-#2555 behaviour."""
    ctx = await farm(4, max_concurrent=1)
    upload = _UploadRecorder()

    await _run_check_queue(ctx, upload)

    assert upload.peak == 1
    assert await _statuses(ctx) == ["printing"] * 4


@pytest.mark.asyncio
async def test_default_concurrency_applies_when_setting_absent(farm):
    """No Settings row (every existing install) must still dispatch in parallel.

    The whole point is that the reporter's farm gets faster *without* him having
    to find a new setting first. Default is 4.
    """
    ctx = await farm(5, max_concurrent=None)
    upload = _UploadRecorder()

    await _run_check_queue(ctx, upload)

    assert upload.peak == 4, f"expected the default cap of 4, got {upload.peak}"
    assert await _statuses(ctx) == ["printing"] * 5


@pytest.mark.asyncio
async def test_one_failing_upload_does_not_cancel_the_others(farm):
    """A dead printer must not take its siblings' in-flight uploads down with it.

    ``asyncio.gather`` without ``return_exceptions=True`` cancels every sibling
    task the moment one raises — which would mean a single unreachable printer
    silently aborts the whole batch mid-transfer.
    """
    ctx = await farm(4, max_concurrent=4)
    upload = _UploadRecorder(fail_for_ip="10.0.0.2")  # printer index 1

    await _run_check_queue(ctx, upload)

    statuses = await _statuses(ctx)
    assert statuses[1] == "failed", "the unreachable printer's item should be marked failed"
    assert [s for i, s in enumerate(statuses) if i != 1] == ["printing"] * 3, (
        "the other three printers must have started despite the failure"
    )


@pytest.mark.asyncio
async def test_check_queue_awaits_its_dispatches_before_returning(farm):
    """The pass must not return while uploads are still in flight.

    ``_start_print`` flips the row pending -> printing only *after* the upload
    finishes. If ``check_queue`` returned early, the next 30-second tick would
    still see those rows as ``pending`` on an idle-looking printer and dispatch
    them a second time.
    """
    ctx = await farm(3, max_concurrent=3)
    upload = _UploadRecorder()

    await _run_check_queue(ctx, upload)

    assert upload.in_flight == 0, "check_queue returned with uploads still running"
    assert await _statuses(ctx) == ["printing"] * 3


class TestSharedLibraryRow:
    """Dispatching in parallel means two items can now reach the same library row
    at the same time — impossible when dispatch was serial.

    Only the ``cleanup_library_after_dispatch`` flow (printer-card "upload and
    print") *mutates* that row: it deletes it and unlinks the 3MF from disk once
    the print is away. Two of those against one row would race — the loser's
    DELETE matches no row, and the winner's unlink can pull the file out from
    under the loser's in-flight upload.

    An ordinary library print only reads the row. That distinction is load-bearing:
    the reporter's own batch was one File Manager file fanned out across his farm
    (both of the queue items in his log point at library file 116), so a blanket
    "never share a library row" guard would re-serialize the exact workload this
    change exists to fix.
    """

    @staticmethod
    async def _library_farm(session_maker, tmp_path, printer_count, *, cleanup: bool):
        """One shared library file, one queue item per printer, all pointing at it."""
        base_dir = tmp_path / "libfarm"
        (base_dir / "library").mkdir(parents=True, exist_ok=True)
        shared = base_dir / "library" / "shared.3mf"
        shared.write_bytes(b"shared payload")

        async with session_maker() as db:
            db.add(Settings(key="queue_max_concurrent_uploads", value=str(printer_count)))
            library_file = LibraryFile(
                filename="shared.3mf",
                file_path=str(shared),
                file_type="3mf",
                file_size=shared.stat().st_size,
            )
            db.add(library_file)
            await db.flush()

            for n in range(printer_count):
                printer = Printer(
                    name=f"Printer {n}",
                    serial_number=f"LIB-SERIAL-{n}",
                    ip_address=f"10.1.0.{n + 1}",
                    access_code="access-code",
                    model="A1",
                )
                db.add(printer)
                await db.flush()
                db.add(
                    PrintQueueItem(
                        printer_id=printer.id,
                        library_file_id=library_file.id,
                        cleanup_library_after_dispatch=cleanup,
                        status="pending",
                        position=n,
                    )
                )
            await db.commit()

        return SimpleNamespace(session_maker=session_maker, base_dir=base_dir, printer_ids=None)

    @pytest.mark.asyncio
    async def test_plain_library_file_still_fans_out_in_parallel(self, tmp_path):
        """The reporter's actual workload: one File Manager file, four printers.

        Nothing here mutates the library row, so all four must upload at once. If
        this ever drops to 1 the headline fix is gone.
        """
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            ctx = await self._library_farm(session_maker, tmp_path, 4, cleanup=False)
            upload = _UploadRecorder()

            await _run_check_queue(ctx, upload)

            assert upload.peak == 4, f"a shared library file must not re-serialize the fan-out — peak was {upload.peak}"
            assert await _statuses(ctx) == ["printing"] * 4
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_cleanup_items_never_share_a_row_in_one_pass(self, tmp_path):
        """The mutating flow must be held to one dispatch per pass.

        Each of these deletes the library row and unlinks the 3MF when it is done.
        Exactly one may go per pass; the rest stay pending for a later one.
        """
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            ctx = await self._library_farm(session_maker, tmp_path, 3, cleanup=True)
            upload = _UploadRecorder()

            await _run_check_queue(ctx, upload)

            assert upload.peak <= 1, (
                f"{upload.peak} dispatches raced over one consumable library row — "
                f"the loser's DELETE finds nothing and its 3MF can be unlinked mid-upload"
            )
            statuses = await _statuses(ctx)
            assert statuses.count("printing") == 1, "exactly one item should have gone out"
            assert statuses.count("pending") == 2, "the rest must stay queued, not fail"
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_library_print_without_a_parseable_print_time_does_not_crash(tmp_path):
    """Regression: `_start_print` read `library_file.print_time_seconds`, a column
    LibraryFile does not have.

    It only fired when the archive carried no print time — a plain .gcode, or a 3MF
    the parser could not read — and it fired *after* the printer had been sent the
    job. The started-notification was lost, and the AttributeError unwound the whole
    queue pass, so every other printer still waiting to be dispatched on that tick
    silently missed its turn. Exactly the "why did only some of them start" shape.

    Two printers here: if the first one's dispatch blows up, the second must still
    go out.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        base_dir = tmp_path / "nolibtime"
        (base_dir / "library").mkdir(parents=True, exist_ok=True)

        async with session_maker() as db:
            db.add(Settings(key="queue_max_concurrent_uploads", value="2"))
            for n in range(2):
                src = base_dir / "library" / f"job-{n}.gcode"
                src.write_bytes(b"G28\n")
                lib = LibraryFile(
                    filename=f"job-{n}.gcode",
                    file_path=str(src),
                    file_type="gcode",
                    file_size=src.stat().st_size,
                )
                db.add(lib)
                printer = Printer(
                    name=f"Printer {n}",
                    serial_number=f"NT-{n}",
                    ip_address=f"10.2.0.{n + 1}",
                    access_code="access-code",
                    model="A1",
                )
                db.add(printer)
                await db.flush()
                db.add(
                    PrintQueueItem(
                        printer_id=printer.id,
                        library_file_id=lib.id,
                        status="pending",
                        position=n,
                        print_time_seconds=None,  # nothing cached either — the crashing shape
                    )
                )
            await db.commit()

        ctx = SimpleNamespace(session_maker=session_maker, base_dir=base_dir, printer_ids=None)
        job_started = AsyncMock()

        await _run_check_queue(ctx, _UploadRecorder(), job_started=job_started)

        assert await _statuses(ctx) == ["printing", "printing"]

        # The status flip happens BEFORE the crash point, so it is not the signal —
        # both rows read "printing" even with the bug present. The started-notification
        # is emitted just after it, and is what the AttributeError actually destroyed.
        assert job_started.await_count == 2, (
            "the job-started notification was lost — _start_print raised after the "
            "printer had already been sent the job"
        )
    finally:
        await engine.dispose()
