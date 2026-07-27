"""Concurrent queue dispatch as a refillable upload pool (#2555, #2602).

Reported first (#2555) as "prints are sent to the printer one by one, very
slowly" on a 19-printer farm: ``check_queue`` awaited ``_start_print`` inline per
item, and ``_start_print`` performs the FTP upload, so every printer queued
behind every other printer's transfer. #2555 moved the uploads to a parallel
``asyncio.gather()`` — but that gather was *awaited before check_queue returned*,
so the run loop stayed blocked until the slowest upload in the batch finished. On
a 93-printer farm (#2602) a 513 s upload left 15 of 16 configured slots idle for
8.5 minutes while other printers came free.

The uploads now run as independent background tasks tracked in
``scheduler._inflight``; each tick launches at most ``limit - len(_inflight)`` new
ones and returns immediately, so a freed slot refills on the next fast tick.

What must stay true:

* Uploads to different printers overlap in time (the #2555 fix).
* No more than ``queue_max_concurrent_uploads`` run at once — as a *pool*, across
  ticks, not just within one batch (#2602).
* A freed slot is refilled by a later tick (#2602).
* An item whose upload is in flight — and its printer — are excluded from the
  next pass, so a still-`pending` row is never dispatched twice (#2602).
* check_queue returns *without* waiting for the uploads (#2602), reporting a
  productive/in-flight pass so ``run()`` re-checks on the fast interval.
* Setting the cap to 1 restores serial behaviour; one printer failing must not
  cancel its siblings' in-flight uploads.

Test model: the scheduler now launches uploads via ``spawn_background_task``, so
the harness swaps in a real task-spawning shim and drains ``_inflight`` explicitly
(inside the patched context, so the upload/session patches are still active while
the pool workers run). ``_run_to_completion`` loops check_queue + drain to model
the run loop draining a queue that exceeds the cap.
"""

import asyncio
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select
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
    uploads — the number the pool cap turns on.
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


@asynccontextmanager
async def _scheduler_ctx(ctx, upload, job_started=None):
    """Yield a scheduler with all I/O patched, and a real task-spawning shim.

    The scheduler launches uploads through ``spawn_background_task`` (#2602), so
    the harness gives it a real ``create_task`` shim rather than the no-op mock
    used before — otherwise the pool workers never run and rows stay ``pending``.
    The watchdog (also spawned per dispatch) is stubbed so it doesn't poll for
    the whole test. Drain ``_inflight`` *inside* this context so the workers run
    while the upload/session patches are still active.
    """
    scheduler = PrintScheduler()
    job_started = job_started or AsyncMock()

    def _real_spawn(coro, *, name=None):
        return asyncio.create_task(coro, name=name)

    patches = [
        patch.object(scheduler_module.settings, "base_dir", ctx.base_dir),
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
        patch("backend.app.services.print_scheduler.spawn_background_task", _real_spawn),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_started", job_started),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_failed", AsyncMock()),
        patch("backend.app.services.mqtt_relay.mqtt_relay.on_queue_job_started", AsyncMock()),
        patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
        patch.object(scheduler, "_preheat_and_soak", AsyncMock()),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
        patch.object(scheduler, "_watchdog_print_start", AsyncMock()),
    ]

    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        yield scheduler


async def _drain(scheduler):
    """Run the currently in-flight pool workers to completion."""
    tasks = [task for (task, _pid) in scheduler._inflight.values()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _run_check_queue(ctx, upload, job_started=None, *, drain=True):
    """Run one check_queue pass; by default also drain the launched uploads.

    Returns the check_queue result (True if the pass was productive / has uploads
    still in flight).
    """
    async with _scheduler_ctx(ctx, upload, job_started) as scheduler:
        result = await scheduler.check_queue()
        if drain:
            await _drain(scheduler)
        return result


async def _run_to_completion(ctx, upload, job_started=None, *, max_ticks: int = 50) -> int:
    """Model the run loop: check_queue + drain until the queue is empty.

    Draining fully between ticks makes each tick a fresh batch of at most the cap,
    which is enough to prove the cap holds across the whole drain and every item
    eventually goes out. Returns the number of ticks it took.
    """
    ticks = 0
    async with _scheduler_ctx(ctx, upload, job_started) as scheduler:
        while ticks < max_ticks:
            await scheduler.check_queue()
            await _drain(scheduler)
            ticks += 1
            if await _pending_count(ctx) == 0 and not scheduler._inflight:
                break
    return ticks


async def _statuses(ctx):
    async with ctx.session_maker() as db:
        rows = (await db.execute(select(PrintQueueItem).order_by(PrintQueueItem.position))).scalars().all()
        return [r.status for r in rows]


async def _pending_count(ctx) -> int:
    async with ctx.session_maker() as db:
        return await db.scalar(
            select(func.count()).select_from(PrintQueueItem).where(PrintQueueItem.status == "pending")
        )


@pytest.mark.asyncio
async def test_uploads_to_different_printers_overlap(farm):
    """The #2555 headline: six printers must not queue behind each other.

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
async def test_pool_cap_holds_across_refills(farm):
    """Eight pending printers, cap of 3 — never more than 3 uploads at once.

    Under the pool model (#2602) one tick launches at most 3; the queue drains
    over several ticks. The cap must hold across the *whole* drain, and every
    item must still go out.
    """
    ctx = await farm(8, max_concurrent=3)
    upload = _UploadRecorder()

    ticks = await _run_to_completion(ctx, upload)

    assert upload.peak == 3, f"cap of 3 not honoured across the drain — peak was {upload.peak}"
    assert len(upload.order) == 8, "every pending item must still be dispatched, just not all at once"
    assert await _statuses(ctx) == ["printing"] * 8
    assert ticks >= 3, "8 items at a cap of 3 must take at least 3 ticks to drain"


@pytest.mark.asyncio
async def test_freed_slot_is_refilled_on_the_next_tick(farm):
    """The #2602 fix: a busy pool doesn't block, and a freed slot refills.

    Cap of 1, two printers. Tick 1 launches printer A. A second tick while A is
    still in flight must launch nothing (pool full) rather than block. Once A
    finishes, the next tick fills the freed slot with printer B.
    """
    ctx = await farm(2, max_concurrent=1)
    upload = _UploadRecorder()

    async with _scheduler_ctx(ctx, upload) as scheduler:
        # Tick 1: one slot, one launch. Don't drain — A is now "in flight".
        assert await scheduler.check_queue() is True
        assert len(scheduler._inflight) == 1

        # Tick 2 while A is in flight: pool full → no new launch, no blocking.
        assert await scheduler.check_queue() is True
        assert len(scheduler._inflight) == 1, "a full pool must not launch a second upload"

        # A completes, freeing the slot.
        await _drain(scheduler)
        assert not scheduler._inflight

        # Tick 3: the freed slot is refilled with the second printer.
        assert await scheduler.check_queue() is True
        assert len(scheduler._inflight) == 1
        await _drain(scheduler)

    assert await _statuses(ctx) == ["printing", "printing"]
    assert upload.peak == 1, "cap of 1 must never overlap two uploads"


@pytest.mark.asyncio
async def test_inflight_item_and_printer_are_excluded_from_reselection(farm):
    """A still-`pending` in-flight row must not be dispatched a second time (#2602).

    The row flips pending -> printing only after its upload completes, so the
    reservation that stops a fast tick re-dispatching it is the in-flight
    exclusion, not the DB status.
    """
    ctx = await farm(1, max_concurrent=4)
    upload = _UploadRecorder()

    async with _scheduler_ctx(ctx, upload) as scheduler:
        await scheduler.check_queue()
        inflight_before = set(scheduler._inflight)
        assert len(inflight_before) == 1

        # Second tick while the upload is in flight (row still pending): the item
        # and its printer must be excluded — no new task, pool unchanged.
        await scheduler.check_queue()
        assert set(scheduler._inflight) == inflight_before, "an in-flight item was re-selected"

        await _drain(scheduler)

    assert await _statuses(ctx) == ["printing"]
    assert len(upload.order) == 1, "the item must be uploaded exactly once, not twice"


@pytest.mark.asyncio
async def test_inflight_printer_is_kept_out_of_auto_drying(farm):
    """A printer with an upload in flight must not be auto-dried in the gap (#2602).

    Once check_queue returns while the upload runs, the only pending row is the
    in-flight one — so the pass takes the "no dispatchable items" path. That path
    must still exclude the in-flight printer from auto-drying, because its print
    is imminent (the row flips to printing the moment the upload finishes).
    """
    ctx = await farm(1, max_concurrent=4)
    printer_id = ctx.printer_ids[0]
    upload = _UploadRecorder()

    async with _scheduler_ctx(ctx, upload) as scheduler:
        await scheduler.check_queue()  # launch the only item; now in flight
        scheduler._check_auto_drying.reset_mock()

        # Second tick: the sole pending row is in flight, so this hits the
        # empty-items path. It must report the in-flight printer as busy.
        result = await scheduler.check_queue()
        assert result is True, "in-flight uploads keep the loop on the fast interval"
        assert scheduler._check_auto_drying.await_count == 1
        busy_arg = scheduler._check_auto_drying.await_args.args[2]
        assert printer_id in busy_arg, "the in-flight printer must be excluded from auto-drying"

        await _drain(scheduler)


@pytest.mark.asyncio
async def test_limit_of_one_restores_serial_behaviour(farm):
    """An escape hatch for weak networks: 1 == one upload at a time."""
    ctx = await farm(4, max_concurrent=1)
    upload = _UploadRecorder()

    await _run_to_completion(ctx, upload)

    assert upload.peak == 1
    assert await _statuses(ctx) == ["printing"] * 4


@pytest.mark.asyncio
async def test_default_concurrency_applies_when_setting_absent(farm):
    """No Settings row (every existing install) must still dispatch in parallel.

    Default cap is 4.
    """
    ctx = await farm(5, max_concurrent=None)
    upload = _UploadRecorder()

    await _run_to_completion(ctx, upload)

    assert upload.peak == 4, f"expected the default cap of 4, got {upload.peak}"
    assert await _statuses(ctx) == ["printing"] * 5


@pytest.mark.asyncio
async def test_one_failing_upload_does_not_cancel_the_others(farm):
    """A dead printer must not take its siblings' in-flight uploads down with it.

    Each upload is an independent task, so one raising cannot cancel the others;
    _start_print marks that one item failed and the rest proceed.
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
async def test_check_queue_reports_it_dispatched(farm):
    """A productive pass returns True so ``run()`` re-checks quickly (#2555)."""
    ctx = await farm(3, max_concurrent=3)

    dispatched = await _run_check_queue(ctx, _UploadRecorder())

    assert dispatched is True, "check_queue dispatched 3 items but did not report it"


@pytest.mark.asyncio
async def test_check_queue_reports_nothing_dispatched_when_empty(farm):
    """An empty queue returns False so ``run()`` falls back to the idle interval."""
    ctx = await farm(0, max_concurrent=3)

    dispatched = await _run_check_queue(ctx, _UploadRecorder())

    assert dispatched is False, "an empty pass must not trigger a fast re-tick"


@pytest.mark.asyncio
async def test_check_queue_returns_without_awaiting_the_uploads(farm):
    """The pass must return *before* the uploads finish (#2602).

    This is the inversion of the old contract: check_queue no longer blocks on
    the batch. It launches the uploads as tracked background tasks, leaves the
    rows ``pending`` (they flip to ``printing`` only when each upload completes),
    and returns True so the run loop keeps ticking fast while they drain.
    """
    ctx = await farm(3, max_concurrent=3)
    upload = _UploadRecorder()

    async with _scheduler_ctx(ctx, upload) as scheduler:
        result = await scheduler.check_queue()

        # Uploads are tracked but have not been awaited: rows are still pending.
        assert result is True
        assert len(scheduler._inflight) == 3
        assert await _statuses(ctx) == ["pending"] * 3

        await _drain(scheduler)

    assert await _statuses(ctx) == ["printing"] * 3
    assert upload.peak == 3


class TestSharedLibraryRow:
    """Dispatching in parallel means two items can reach the same library row at
    the same time — impossible when dispatch was serial.

    Only the ``cleanup_library_after_dispatch`` flow (printer-card "upload and
    print") *mutates* that row: it deletes it and unlinks the 3MF once the print
    is away. Two of those against one row would race. An ordinary library print
    only reads the row, and the reporter's own batch was one File Manager file
    fanned out across his farm, so a blanket "never share a library row" guard
    would re-serialize the exact workload this exists to fix.
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

        Nothing here mutates the library row, so all four must upload at once.
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

        Each of these deletes the library row and unlinks the 3MF when done.
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
    job. The started-notification was lost and the AttributeError unwound the
    dispatch. Two printers here: if the first one's dispatch blows up, the second
    must still go out.
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
        assert job_started.await_count == 2, (
            "the job-started notification was lost — _start_print raised after the "
            "printer had already been sent the job"
        )
    finally:
        await engine.dispose()
