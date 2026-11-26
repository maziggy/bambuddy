"""Plate changer capacity in the queue scheduler (#3575).

A printer with a plate_capacity counts plates. At zero (or with a capacity and no
count at all) the queue sends it nothing: no dispatch, no smart-plug wake, no
keep-warm. A printer without a capacity behaves exactly as before.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services import plate_clear
from backend.app.services.print_scheduler import PrintScheduler, _ModelCandidate

SCHED = "backend.app.services.print_scheduler"


@pytest.fixture(autouse=True)
def _clean_marks():
    from backend.app.services import plate_changer

    plate_changer._count_pending.clear()
    plate_changer._count_unknown.clear()
    plate_clear.load_overrides({})
    yield
    plate_changer._count_pending.clear()
    plate_changer._count_unknown.clear()
    plate_clear.load_overrides({})


@pytest.fixture
async def ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield SimpleNamespace(session_maker=session_maker)
    finally:
        await engine.dispose()


async def _add_printer(ctx, pid, *, capacity=None, remaining=None, model="X1C", name=None):
    async with ctx.session_maker() as db:
        db.add(
            Printer(
                id=pid,
                name=name or f"X1C-0{pid}",
                serial_number=f"X1C000{pid}",
                ip_address=f"10.0.0.{pid}",
                access_code="x",
                model=model,
                is_active=True,
                plate_capacity=capacity,
                plates_remaining=remaining,
            )
        )
        await db.commit()


async def _add_item(ctx, *, printer_id=None, target_model=None, position=1):
    async with ctx.session_maker() as db:
        lib = LibraryFile(
            filename="job.gcode.3mf",
            file_path="/library/job.gcode.3mf",
            file_size=10,
            file_type="gcode.3mf",
            file_metadata={"sliced_for_model": "X1C"},
        )
        db.add(lib)
        await db.flush()
        item = PrintQueueItem(
            status="pending",
            position=position,
            printer_id=printer_id,
            target_model=target_model,
            library_file_id=lib.id,
        )
        db.add(item)
        await db.commit()
        return item.id


async def _item(ctx, item_id):
    async with ctx.session_maker() as db:
        return (await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


async def _run(ctx, scheduler, *, connected=True, plugs=None, power_on=None, pending=()):
    """One check_queue pass. Returns the _launch_uploads mock."""
    launched = MagicMock()
    patches = [
        patch(f"{SCHED}.async_session", ctx.session_maker),
        patch("backend.app.core.database.async_session", ctx.session_maker),
        patch(f"{SCHED}.printer_manager.is_connected", MagicMock(return_value=connected)),
        patch(f"{SCHED}.printer_manager.get_status", MagicMock(return_value=None)),
        patch(f"{SCHED}.printer_manager.is_awaiting_plate_clear", MagicMock(return_value=False)),
        patch(f"{SCHED}.ha_sensor_manager.blocked_printers", AsyncMock(return_value={})),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_waiting", AsyncMock()),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_assigned", AsyncMock()),
        patch(f"{SCHED}.compute_deficit_for_queue_item", AsyncMock(return_value=[])),
        patch(f"{SCHED}.is_count_pending", lambda pid: pid in pending),
        patch.object(scheduler, "_filament_short_on", AsyncMock(return_value=False)),
        patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
        patch.object(scheduler, "_ensure_ams_mapping", AsyncMock(return_value=None)),
        patch.object(scheduler, "_block_on_filament_deficit", AsyncMock(return_value=False)),
        patch.object(scheduler, "_block_on_unmatched_filament", AsyncMock(return_value=False)),
        patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=plugs or [])),
        patch.object(scheduler, "_power_on_and_wait", power_on or AsyncMock(return_value=True)),
        patch.object(scheduler, "_launch_uploads", launched),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        await scheduler.check_queue()
    return launched


class TestLoadPlateChangers:
    @pytest.mark.asyncio
    async def test_null_capacity_is_ignored(self, ctx):
        await _add_printer(ctx, 1)
        await _add_printer(ctx, 2, capacity=5, remaining=3)
        scheduler = PrintScheduler()
        async with ctx.session_maker() as db:
            await scheduler._load_plate_changers(db)
        assert scheduler._plate_changer_ids == {2}
        assert scheduler._plate_changer_empty == {}

    @pytest.mark.asyncio
    async def test_zero_and_null_remaining_are_empty(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=0)
        await _add_printer(ctx, 2, capacity=5, remaining=None)
        await _add_printer(ctx, 3, capacity=5, remaining=1)
        scheduler = PrintScheduler()
        async with ctx.session_maker() as db:
            await scheduler._load_plate_changers(db)
        assert scheduler._plate_changer_ids == {1, 2, 3}
        assert scheduler._plate_changer_empty == {1: "X1C-01", 2: "X1C-02"}

    @pytest.mark.asyncio
    async def test_a_refilled_printer_is_dropped_on_the_next_load(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=5)
        scheduler = PrintScheduler()
        scheduler._plate_changer_ids = {1}
        scheduler._plate_changer_empty = {1: "X1C-01"}
        async with ctx.session_maker() as db:
            await scheduler._load_plate_changers(db)
        assert scheduler._plate_changer_empty == {}


class TestPinnedItem:
    @pytest.mark.asyncio
    async def test_empty_printer_holds_the_item_with_its_reason(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=0)
        item_id = await _add_item(ctx, printer_id=1)

        launched = await _run(ctx, PrintScheduler())

        launched.assert_not_called()
        row = await _item(ctx, item_id)
        assert row.status == "pending"
        assert row.waiting_reason == "Plate changer empty: X1C-01"

    @pytest.mark.asyncio
    async def test_no_smart_plug_power_on_for_an_empty_printer(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=0)
        await _add_item(ctx, printer_id=1)
        plug = SimpleNamespace(id=1, auto_on=True, enabled=True, controls_printer_power=True, name="plug")
        power_on = AsyncMock(return_value=True)

        launched = await _run(ctx, PrintScheduler(), connected=False, plugs=[plug], power_on=power_on)

        power_on.assert_not_called()
        launched.assert_not_called()

    @pytest.mark.asyncio
    async def test_null_remaining_with_capacity_is_held_too(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=None)
        item_id = await _add_item(ctx, printer_id=1)

        launched = await _run(ctx, PrintScheduler())

        launched.assert_not_called()
        assert (await _item(ctx, item_id)).waiting_reason == "Plate changer empty: X1C-01"

    @pytest.mark.asyncio
    async def test_printer_with_plates_left_dispatches(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=1)
        item_id = await _add_item(ctx, printer_id=1)

        launched = await _run(ctx, PrintScheduler())

        assert launched.call_args[0][0] == [item_id]
        assert (await _item(ctx, item_id)).waiting_reason is None

    @pytest.mark.asyncio
    async def test_printer_without_capacity_is_unaffected(self, ctx):
        await _add_printer(ctx, 1)
        item_id = await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()

        launched = await _run(ctx, scheduler)

        assert launched.call_args[0][0] == [item_id]
        assert scheduler._plate_changer_ids == set()
        assert scheduler._plate_changer_empty == {}

    @pytest.mark.asyncio
    async def test_refill_releases_the_item_on_the_next_pass(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=0)
        item_id = await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()
        await _run(ctx, scheduler)
        assert (await _item(ctx, item_id)).waiting_reason == "Plate changer empty: X1C-01"

        async with ctx.session_maker() as db:
            printer = await db.get(Printer, 1)
            printer.plates_remaining = 5
            await db.commit()
        launched = await _run(ctx, scheduler)

        assert launched.call_args[0][0] == [item_id]
        assert (await _item(ctx, item_id)).waiting_reason is None

    @pytest.mark.asyncio
    async def test_a_printer_still_closing_out_its_print_sits_the_pass_out(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=3)
        await _add_item(ctx, printer_id=1)

        launched = await _run(ctx, PrintScheduler(), pending={1})

        launched.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_printer_without_capacity_closing_out_is_not_marked_busy(self, ctx):
        await _add_printer(ctx, 1)
        item_id = await _add_item(ctx, printer_id=1)

        launched = await _run(ctx, PrintScheduler(), pending={1})

        assert launched.call_args[0][0] == [item_id]


class TestModelBasedItem:
    @pytest.mark.asyncio
    async def test_skips_the_empty_printer_for_a_sibling(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=0)
        await _add_printer(ctx, 2, capacity=5, remaining=2)
        item_id = await _add_item(ctx, target_model="X1C")

        launched = await _run(ctx, PrintScheduler())

        launched.assert_called_once()
        assert (await _item(ctx, item_id)).printer_id == 2

    @pytest.mark.asyncio
    async def test_only_an_empty_printer_exists(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=0)
        item_id = await _add_item(ctx, target_model="X1C")

        launched = await _run(ctx, PrintScheduler())

        launched.assert_not_called()
        row = await _item(ctx, item_id)
        assert row.status == "pending"
        assert "Plate changer empty: X1C-01" in (row.waiting_reason or "")
        assert "Busy" not in (row.waiting_reason or "")

    @pytest.mark.asyncio
    async def test_find_idle_reports_every_empty_printer(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=0)
        await _add_printer(ctx, 2, capacity=5, remaining=None)
        scheduler = PrintScheduler()
        scheduler._plate_changer_ids = {1, 2}
        scheduler._plate_changer_empty = {1: "X1C-01", 2: "X1C-02"}
        with (
            patch(f"{SCHED}.printer_manager.is_connected", MagicMock(return_value=True)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        ):
            async with ctx.session_maker() as db:
                found, reason = await scheduler._find_idle_printer_for_model(db, "X1C", set())
        assert found is None
        assert reason == "Plate changer empty: X1C-01, X1C-02"

    @pytest.mark.asyncio
    async def test_find_idle_without_capacity_is_unchanged(self, ctx):
        await _add_printer(ctx, 1)
        scheduler = PrintScheduler()
        with (
            patch(f"{SCHED}.printer_manager.is_connected", MagicMock(return_value=True)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        ):
            async with ctx.session_maker() as db:
                assert await scheduler._find_idle_printer_for_model(db, "X1C", set()) == (1, None)


class TestFilamentWaitReservation:
    """The printer kept free for a job waiting for filament (#3587) is one that can take it."""

    @staticmethod
    async def _reserved(empty: dict[int, str]) -> int | None:
        scheduler = PrintScheduler()
        scheduler._plate_changer_empty = empty
        printers = [SimpleNamespace(id=pid, name=f"X1C-0{pid}", model="X1C") for pid in (1, 2)]
        pm = MagicMock()
        pm.is_connected.return_value = True
        pm.get_status.return_value = MagicMock(state="IDLE")
        pm.is_awaiting_plate_clear.return_value = False
        with (
            patch(f"{SCHED}.printer_manager", pm),
            patch(f"{SCHED}._wrong_nozzle_label", MagicMock(return_value=None)),
            patch.object(scheduler, "_printers_for_model", AsyncMock(return_value=printers)),
        ):
            reserved = await scheduler._reservable_printer_for_model(
                None,
                [SimpleNamespace(target_model="X1C", sliced_nozzle=None)],
                set(),
                SimpleNamespace(target_location=None, require_previous_success=False),
                None,
                False,
            )
        return reserved.id if reserved else None

    @pytest.mark.asyncio
    async def test_an_empty_plate_changer_is_not_kept_free(self):
        assert await self._reserved({}) == 1
        assert await self._reserved({1: "X1C-01"}) == 2

    @pytest.mark.asyncio
    async def test_no_printer_is_kept_free_when_every_plate_changer_is_empty(self):
        assert await self._reserved({1: "X1C-01", 2: "X1C-02"}) is None


class TestWake:
    @pytest.mark.asyncio
    async def test_wake_skips_the_empty_printer(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=0)
        scheduler = PrintScheduler()
        scheduler._plate_changer_ids = {1}
        scheduler._plate_changer_empty = {1: "X1C-01"}
        plug = SimpleNamespace(id=1, auto_on=True, enabled=True, controls_printer_power=True)
        power_on = AsyncMock(return_value=True)
        with (
            patch(f"{SCHED}.printer_manager.is_connected", MagicMock(return_value=False)),
            patch(f"{SCHED}.printer_manager.is_awaiting_plate_clear", MagicMock(return_value=False)),
            patch.object(scheduler, "_cached_filament_shortfall", MagicMock(return_value=[])),
            patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[plug])),
            patch.object(scheduler, "_power_on_and_wait", power_on),
        ):
            async with ctx.session_maker() as db:
                woken = await scheduler._wake_printer_for_model(
                    db,
                    [
                        _ModelCandidate(
                            target_model="X1C", sliced_for="X1C", required_filament_types=None, filament_overrides=None
                        )
                    ],
                    None,
                    set(),
                    {1},
                    require_plate_clear=True,
                )
        assert woken == (None, None)
        power_on.assert_not_called()


class TestKeepWarm:
    async def _warm(self, scheduler, *, empty):
        scheduler._plate_changer_empty = {7: "X1C-07"} if empty else {}
        archive = SimpleNamespace(file_path=None, bed_temperature=100)
        item = SimpleNamespace(id=1, printer_id=7, archive=archive, preheat_chamber_target_override=60)
        client = MagicMock()
        state = SimpleNamespace(state="FINISH", temperatures={"bed_target": 0.0, "chamber": 55.0}, raw_data={})
        settings = {"queue_keep_bed_warm": True, "preheat_enabled": True}
        with (
            patch(f"{SCHED}.printer_manager") as pm,
            patch.object(
                scheduler, "_get_bool_setting", AsyncMock(side_effect=lambda _d, k, default: settings.get(k, default))
            ),
            patch.object(scheduler, "_get_int_setting", AsyncMock(side_effect=lambda _d, k, default=0: default)),
            patch(f"{SCHED}.time") as t,
        ):
            t.monotonic.return_value = 10_000.0
            pm.get_status.return_value = state
            pm.get_client.return_value = client
            await scheduler._apply_keep_warm(AsyncMock(), [item], [], {7}, True)
        return client

    @pytest.mark.asyncio
    async def test_an_empty_plate_changer_is_not_kept_warm(self):
        scheduler = PrintScheduler()
        client = await self._warm(scheduler, empty=True)
        client.set_bed_temperature.assert_not_called()
        assert 7 not in scheduler._keep_warm

    @pytest.mark.asyncio
    async def test_a_plate_changer_with_plates_left_is_kept_warm(self):
        scheduler = PrintScheduler()
        client = await self._warm(scheduler, empty=False)
        client.set_bed_temperature.assert_called_once()
        assert 7 in scheduler._keep_warm


class TestStartPrintGuard:
    """_start_print re-reads the count before the upload and again after it."""

    async def _start(self, ctx, scheduler, item_id, *, pending=False):
        upload = AsyncMock()
        # Anything past the guard would reach the upload.
        with (
            patch(f"{SCHED}.upload_file_async", upload),
            patch(f"{SCHED}.delete_file_async", AsyncMock()),
            patch(f"{SCHED}.printer_manager.is_connected", MagicMock(return_value=True)),
            patch(f"{SCHED}.printer_manager.get_status", MagicMock(return_value=None)),
            patch(f"{SCHED}.is_count_pending", lambda pid: pending),
            patch(f"{SCHED}.estimate_queue_source_cost", AsyncMock(return_value=0)),
            patch(f"{SCHED}.validate_print_budget", AsyncMock()),
            patch(f"{SCHED}.create_budget_reservation", AsyncMock(return_value=None)),
        ):
            async with ctx.session_maker() as db:
                item = await db.get(PrintQueueItem, item_id)
                await scheduler._start_print(db, item)
        return upload

    @pytest.mark.asyncio
    async def test_defers_when_the_printer_ran_out_since_the_pass(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=0)
        item_id = await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()
        scheduler._plate_changer_ids = {1}  # the pass saw it with plates left

        upload = await self._start(ctx, scheduler, item_id)

        upload.assert_not_called()
        assert (await _item(ctx, item_id)).status == "pending"

    @pytest.mark.asyncio
    async def test_defers_while_the_count_is_pending(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=3)
        item_id = await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()
        scheduler._plate_changer_ids = {1}

        upload = await self._start(ctx, scheduler, item_id, pending=True)

        upload.assert_not_called()
        assert (await _item(ctx, item_id)).status == "pending"

    @pytest.mark.asyncio
    async def test_a_model_matched_item_goes_back_to_the_pool(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=0)
        item_id = await _add_item(ctx, printer_id=1, target_model="X1C")
        scheduler = PrintScheduler()
        scheduler._plate_changer_ids = {1}

        upload = await self._start(ctx, scheduler, item_id)

        upload.assert_not_called()
        item = await _item(ctx, item_id)
        assert (item.status, item.printer_id) == ("pending", None)

    @pytest.mark.asyncio
    async def test_a_pinned_item_keeps_its_printer(self, ctx):
        await _add_printer(ctx, 1, capacity=5, remaining=0)
        item_id = await _add_item(ctx, printer_id=1)
        scheduler = PrintScheduler()
        scheduler._plate_changer_ids = {1}

        await self._start(ctx, scheduler, item_id)

        assert (await _item(ctx, item_id)).printer_id == 1

    @pytest.mark.asyncio
    async def test_blocks_dispatch_truth_table(self, ctx):
        await _add_printer(ctx, 1)  # no capacity
        await _add_printer(ctx, 2, capacity=5, remaining=2)
        await _add_printer(ctx, 3, capacity=5, remaining=0)
        await _add_printer(ctx, 4, capacity=5, remaining=None)
        scheduler = PrintScheduler()
        async with ctx.session_maker() as db:
            with patch(f"{SCHED}.is_count_pending", lambda pid: False):
                assert await scheduler._plate_changer_blocks_dispatch(db, 99) is False
                assert await scheduler._plate_changer_blocks_dispatch(db, 1) is False
                assert await scheduler._plate_changer_blocks_dispatch(db, 2) is False
                assert await scheduler._plate_changer_blocks_dispatch(db, 3) is True
                assert await scheduler._plate_changer_blocks_dispatch(db, 4) is True
            with patch(f"{SCHED}.is_count_pending", lambda pid: True):
                assert await scheduler._plate_changer_blocks_dispatch(db, 1) is False
                assert await scheduler._plate_changer_blocks_dispatch(db, 2) is True

    @pytest.mark.asyncio
    async def test_an_unbooked_count_blocks_dispatch(self, ctx):
        await _add_printer(ctx, 2, capacity=5, remaining=2)
        from backend.app.services import plate_changer

        plate_changer.mark_count_unknown(2)
        async with ctx.session_maker() as db:
            assert await PrintScheduler()._plate_changer_blocks_dispatch(db, 2) is True


class TestCountUnknown:
    @pytest.mark.asyncio
    async def test_an_unbooked_count_reads_as_empty(self, ctx):
        from backend.app.services import plate_changer

        await _add_printer(ctx, 1, capacity=5, remaining=3)
        await _add_printer(ctx, 2)
        plate_changer.mark_count_unknown(1)
        plate_changer.mark_count_unknown(2)  # no capacity: ignored
        scheduler = PrintScheduler()
        async with ctx.session_maker() as db:
            await scheduler._load_plate_changers(db)

        assert scheduler._plate_changer_empty == {1: "X1C-01"}

    @pytest.mark.asyncio
    async def test_it_holds_the_queue_item(self, ctx):
        from backend.app.services import plate_changer

        await _add_printer(ctx, 1, capacity=5, remaining=3)
        item_id = await _add_item(ctx, printer_id=1)
        plate_changer.mark_count_unknown(1)

        launched = await _run(ctx, PrintScheduler())

        launched.assert_not_called()
        assert (await _item(ctx, item_id)).waiting_reason == "Plate changer empty: X1C-01"


class TestPostUploadGuard:
    @pytest.mark.asyncio
    async def test_a_count_that_ran_out_during_the_upload_defers_and_deletes_the_file(self, ctx, tmp_path):
        from backend.app.models.archive import PrintArchive

        await _add_printer(ctx, 1, capacity=5, remaining=3)
        item_id = await _add_item(ctx, printer_id=1)
        (tmp_path / "a.gcode.3mf").write_bytes(b"3mf")
        async with ctx.session_maker() as db:
            db.add(PrintArchive(id=7, filename="a.gcode.3mf", file_path="a.gcode.3mf", file_size=3, status="queued"))
            lib = await db.scalar(select(LibraryFile))
            lib.file_path = str(tmp_path / "a.gcode.3mf")
            await db.commit()
        scheduler = PrintScheduler()
        scheduler._plate_changer_ids = {1}
        calls = iter([False, True])  # clear before the upload, empty after it
        delete = AsyncMock()
        start = AsyncMock()
        upload = AsyncMock(return_value=True)

        async def claim(db, *_):
            return await db.get(PrintArchive, 7)

        with (
            patch.object(scheduler, "_plate_changer_blocks_dispatch", AsyncMock(side_effect=lambda *a: next(calls))),
            patch(f"{SCHED}.printer_manager.is_connected", MagicMock(return_value=True)),
            patch(f"{SCHED}.printer_manager.get_status", MagicMock(return_value=None)),
            patch(f"{SCHED}.estimate_queue_source_cost", AsyncMock(return_value=0)),
            patch(f"{SCHED}.validate_print_budget", AsyncMock()),
            patch(f"{SCHED}.create_budget_reservation", AsyncMock(return_value=None)),
            patch(f"{SCHED}.async_session", ctx.session_maker),
            patch("backend.app.core.database.async_session", ctx.session_maker),
            patch(f"{SCHED}.settings.base_dir", tmp_path),
            patch.object(scheduler, "_claim_reusable_archive", claim),
            patch.object(scheduler, "_printing_elsewhere", AsyncMock(return_value=True)),
            patch(f"{SCHED}.upload_file_async", upload),
            patch(f"{SCHED}.delete_file_async", delete),
            patch(f"{SCHED}.printer_manager.start_print", start),
        ):
            async with ctx.session_maker() as db:
                item = await db.get(PrintQueueItem, item_id)
                await scheduler._start_print(db, item)

        assert (await _item(ctx, item_id)).status == "pending"
        start.assert_not_called()
        upload.assert_awaited_once()
        # The file the upload just put there is taken off the printer again.
        assert delete.await_args.args[2] == upload.await_args.args[3]
