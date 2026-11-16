"""A print's completion counts down the plate changer (#3575).

``on_print_complete`` runs against real rows (like test_print_complete_end_to_end);
only the outside world is mocked. A plate is spent by a *completed* print whose
plate nobody has to confirm: the printer's own require_plate_clear is off, or it
follows the app setting and that is off (#2860).
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app import main as main_module
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.services import plate_changer, plate_clear
from backend.app.services.plate_changer import consume_auto_cleared_plate
from backend.tests.integration.test_print_complete_end_to_end import (  # noqa: F401
    _clean,
    _complete,
    _no_finish_photo,
    queued_print,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

PC = "backend.app.services.plate_changer"


@pytest.fixture(autouse=True)
def _clean_marks():
    for marks in (plate_changer._count_pending, plate_changer._count_unknown):
        marks.clear()
    plate_clear.load_overrides({})
    yield
    for marks in (plate_changer._count_pending, plate_changer._count_unknown):
        marks.clear()
    plate_clear.load_overrides({})


@pytest.fixture
def changer_mocks():
    """The websocket broadcast and the empty-notification of the plate changer."""
    with (
        patch(f"{PC}.ws_manager") as ws,
        patch(f"{PC}.notification_service") as notif,
    ):
        ws.send_printer_plates_changed = AsyncMock()
        notif.on_plate_changer_empty = AsyncMock()
        yield ws, notif


async def _setup(db_session, printer_id, *, capacity=5, remaining=3, override=None, require_clear=None):
    """``override`` is the printer's own require_plate_clear, ``require_clear`` the app setting."""
    printer = await db_session.get(Printer, printer_id)
    printer.plate_capacity = capacity
    printer.plates_remaining = remaining
    printer.require_plate_clear = override
    # The printer route keeps the cache current; here the row is written directly.
    plate_clear.set_override(printer_id, override)
    if require_clear is not None:
        db_session.add(Settings(key="require_plate_clear", value="true" if require_clear else "false"))
    await db_session.commit()


async def _self_clearing(printer_factory, **kwargs):
    """A printer whose own require_plate_clear is off: nobody confirms its plate."""
    printer = await printer_factory(require_plate_clear=False, **kwargs)
    plate_clear.set_override(printer.id, False)
    return printer


async def _remaining(db_session, printer_id):
    db_session.expire_all()
    return (await db_session.get(Printer, printer_id)).plates_remaining


class TestWhichFinishesCount:
    async def test_completed_with_the_printers_own_setting_off_counts(
        self, test_engine, db_session, queued_print, changer_mocks
    ):
        await _setup(db_session, queued_print["printer"], override=False, require_clear=True)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert await _remaining(db_session, queued_print["printer"]) == 2
        ws, _notif = changer_mocks
        ws.send_printer_plates_changed.assert_awaited_once_with(queued_print["printer"], 2, 5)

    async def test_completed_following_the_setting_off_counts(
        self, test_engine, db_session, queued_print, changer_mocks
    ):
        await _setup(db_session, queued_print["printer"], require_clear=False)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert await _remaining(db_session, queued_print["printer"]) == 2

    async def test_completed_following_an_unset_setting_counts(
        self, test_engine, db_session, queued_print, changer_mocks
    ):
        """No require_plate_clear row reads as the setting being off."""
        await _setup(db_session, queued_print["printer"])

        await _complete(test_engine, queued_print["printer"], "completed")

        assert await _remaining(db_session, queued_print["printer"]) == 2

    async def test_completed_following_the_setting_on_does_not_count(
        self, test_engine, db_session, queued_print, changer_mocks
    ):
        await _setup(db_session, queued_print["printer"], require_clear=True)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert await _remaining(db_session, queued_print["printer"]) == 3
        changer_mocks[0].send_printer_plates_changed.assert_not_awaited()

    async def test_the_printers_own_setting_on_does_not_count(
        self, test_engine, db_session, queued_print, changer_mocks
    ):
        await _setup(db_session, queued_print["printer"], override=True, require_clear=False)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert await _remaining(db_session, queued_print["printer"]) == 3

    @pytest.mark.parametrize("status", ["failed", "aborted", "cancelled"])
    async def test_an_unsuccessful_finish_does_not_count(
        self, test_engine, db_session, queued_print, changer_mocks, status
    ):
        await _setup(db_session, queued_print["printer"], override=False)

        await _complete(test_engine, queued_print["printer"], status)

        assert await _remaining(db_session, queued_print["printer"]) == 3
        changer_mocks[0].send_printer_plates_changed.assert_not_awaited()

    async def test_no_capacity_does_nothing(self, test_engine, db_session, queued_print, changer_mocks):
        await _setup(db_session, queued_print["printer"], capacity=None, remaining=None, override=False)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert await _remaining(db_session, queued_print["printer"]) is None
        changer_mocks[0].send_printer_plates_changed.assert_not_awaited()
        changer_mocks[1].on_plate_changer_empty.assert_not_awaited()

    async def test_a_print_started_outside_bambuddy_counts_too(
        self, test_engine, db_session, queued_print, changer_mocks
    ):
        """No queue item, no archive of ours: the plate was still spent."""
        await _setup(db_session, queued_print["printer"], override=False)
        main_module._active_prints.clear()

        await _complete(test_engine, queued_print["printer"], "completed", name="from_the_screen")

        assert await _remaining(db_session, queued_print["printer"]) == 2


class TestInternalPrinterJobs:
    """The printer's own calibration runs complete too but use no plate."""

    async def test_a_pressure_advance_line_does_not_count(self, test_engine, db_session, queued_print, changer_mocks):
        await _setup(db_session, queued_print["printer"], override=False)

        await _complete(
            test_engine,
            queued_print["printer"],
            "completed",
            extra={"filename": "", "subtask_name": "auto_pa_line_calib_mode"},
        )

        assert await _remaining(db_session, queued_print["printer"]) == 3
        assert not plate_changer.is_count_pending(queued_print["printer"])

    async def test_a_usr_path_does_not_count(self, test_engine, db_session, queued_print, changer_mocks):
        await _setup(db_session, queued_print["printer"], override=False)

        await _complete(
            test_engine,
            queued_print["printer"],
            "completed",
            extra={"filename": "/usr/etc/print/auto_cali_for_user.gcode", "subtask_name": "part"},
        )

        assert await _remaining(db_session, queued_print["printer"]) == 3

    async def test_a_dispatched_job_with_an_internal_looking_name_counts(
        self, test_engine, db_session, queued_print, changer_mocks
    ):
        await _setup(db_session, queued_print["printer"], override=False)
        archive = await db_session.get(PrintArchive, queued_print["archive"])
        archive.filename = "auto_pa_line_calib_mode.gcode.3mf"
        await db_session.commit()

        await _complete(
            test_engine,
            queued_print["printer"],
            "completed",
            extra={"filename": "", "subtask_name": "auto_pa_line_calib_mode"},
        )

        assert await _remaining(db_session, queued_print["printer"]) == 2

    async def test_a_failing_check_counts_the_plate(self, test_engine, db_session, queued_print, changer_mocks):
        await _setup(db_session, queued_print["printer"], override=False)

        with patch.object(main_module, "_dispatched_by_bambuddy", AsyncMock(side_effect=RuntimeError("boom"))):
            await _complete(
                test_engine,
                queued_print["printer"],
                "completed",
                extra={"filename": "", "subtask_name": "auto_pa_line_calib_mode"},
            )

        assert await _remaining(db_session, queued_print["printer"]) == 2


class TestEmptyEdge:
    async def test_one_to_zero_notifies_once(self, test_engine, db_session, queued_print, changer_mocks):
        await _setup(db_session, queued_print["printer"], remaining=1, override=False)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert await _remaining(db_session, queued_print["printer"]) == 0
        _ws, notif = changer_mocks
        notif.on_plate_changer_empty.assert_awaited_once()
        args = notif.on_plate_changer_empty.await_args.args
        assert args[:3] == (queued_print["printer"], "P", 5)

    async def test_staying_above_zero_does_not_notify(self, test_engine, db_session, queued_print, changer_mocks):
        await _setup(db_session, queued_print["printer"], remaining=2, override=False)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert await _remaining(db_session, queued_print["printer"]) == 1
        changer_mocks[1].on_plate_changer_empty.assert_not_awaited()

    async def test_zero_stays_zero_and_is_not_announced_again(
        self, test_engine, db_session, queued_print, changer_mocks
    ):
        await _setup(db_session, queued_print["printer"], remaining=0, override=False)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert await _remaining(db_session, queued_print["printer"]) == 0
        changer_mocks[0].send_printer_plates_changed.assert_not_awaited()
        changer_mocks[1].on_plate_changer_empty.assert_not_awaited()

    async def test_null_remaining_stays_null(self, test_engine, db_session, queued_print, changer_mocks):
        await _setup(db_session, queued_print["printer"], remaining=None, override=False)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert await _remaining(db_session, queued_print["printer"]) is None
        changer_mocks[1].on_plate_changer_empty.assert_not_awaited()


class TestOrdering:
    async def test_the_count_is_committed_while_the_queue_row_is_still_printing(
        self, test_engine, db_session, queued_print, changer_mocks
    ):
        """A queue pass that sees the printer free must already see the new count."""
        await _setup(db_session, queued_print["printer"], remaining=1, override=False)
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        seen = {}

        async def watching(printer_id):
            await consume_auto_cleared_plate(printer_id)
            async with maker() as other:
                seen["remaining"] = await other.scalar(select(Printer.plates_remaining).where(Printer.id == printer_id))
                seen["item"] = await other.scalar(
                    select(PrintQueueItem.status).where(PrintQueueItem.id == queued_print["item"])
                )
                seen["archive"] = await other.scalar(
                    select(PrintArchive.status).where(PrintArchive.id == queued_print["archive"])
                )

        with patch.object(main_module, "consume_auto_cleared_plate", watching):
            await _complete(test_engine, queued_print["printer"], "completed")

        assert seen["remaining"] == 0
        assert seen["item"] == "printing"
        assert seen["archive"] == "printing"

    async def test_consume_runs_once_per_completion(self, test_engine, db_session, queued_print, changer_mocks):
        with patch.object(main_module, "consume_auto_cleared_plate", AsyncMock()) as consume:
            await _complete(test_engine, queued_print["printer"], "completed")
        consume.assert_awaited_once_with(queued_print["printer"])

    @pytest.mark.parametrize("status", ["failed", "aborted", "cancelled"])
    async def test_consume_is_not_even_called_for_other_statuses(
        self, test_engine, db_session, queued_print, changer_mocks, status
    ):
        with patch.object(main_module, "consume_auto_cleared_plate", AsyncMock()) as consume:
            await _complete(test_engine, queued_print["printer"], status)
        consume.assert_not_awaited()


class TestCountingNeverBreaksTheCompletion:
    async def test_a_database_error_in_consume(self, test_engine, db_session, queued_print, changer_mocks):
        await _setup(db_session, queued_print["printer"], override=False)

        with patch(f"{PC}.run_with_retry", AsyncMock(side_effect=RuntimeError("boom"))):
            await _complete(test_engine, queued_print["printer"], "completed")

        db_session.expire_all()
        assert (await db_session.get(PrintArchive, queued_print["archive"])).status == "completed"
        assert (await db_session.get(PrintQueueItem, queued_print["item"])).status == "completed"
        # A count that cannot be booked leaves the printer reading as empty.
        assert plate_changer.is_count_unknown(queued_print["printer"])

    async def test_a_failing_broadcast_or_notification(self, test_engine, db_session, queued_print, changer_mocks):
        ws, notif = changer_mocks
        ws.send_printer_plates_changed.side_effect = RuntimeError("ws down")
        notif.on_plate_changer_empty.side_effect = RuntimeError("provider down")
        await _setup(db_session, queued_print["printer"], remaining=1, override=False)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert await _remaining(db_session, queued_print["printer"]) == 0
        db_session.expire_all()
        assert (await db_session.get(PrintQueueItem, queued_print["item"])).status == "completed"


class TestConsumeDirectly:
    """consume_auto_cleared_plate against the test database, without the completion."""

    @pytest.fixture
    def patched(self, test_engine, changer_mocks):
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with ExitStack() as stack:
            stack.enter_context(patch(f"{PC}.async_session", maker))
            stack.enter_context(patch("backend.app.core.database.async_session", maker))
            yield changer_mocks

    async def test_decrements_and_clamps_at_zero(self, db_session, printer_factory, patched):
        printer = await _self_clearing(printer_factory, name="Swap", plate_capacity=2, plates_remaining=2)
        _ws, notif = patched

        await consume_auto_cleared_plate(printer.id)
        assert await _remaining(db_session, printer.id) == 1
        notif.on_plate_changer_empty.assert_not_awaited()

        await consume_auto_cleared_plate(printer.id)
        assert await _remaining(db_session, printer.id) == 0
        notif.on_plate_changer_empty.assert_awaited_once()

        await consume_auto_cleared_plate(printer.id)
        assert await _remaining(db_session, printer.id) == 0
        notif.on_plate_changer_empty.assert_awaited_once()

    async def test_no_override_follows_the_setting_read_now(self, db_session, printer_factory, patched):
        printer = await printer_factory(name="Swap", plate_capacity=5, plates_remaining=5)

        db_session.add(Settings(key="require_plate_clear", value="true"))
        await db_session.commit()
        await consume_auto_cleared_plate(printer.id)
        assert await _remaining(db_session, printer.id) == 5

        setting = await db_session.scalar(select(Settings).where(Settings.key == "require_plate_clear"))
        setting.value = "false"
        await db_session.commit()
        await consume_auto_cleared_plate(printer.id)
        assert await _remaining(db_session, printer.id) == 4

    async def test_only_the_named_printer_is_counted(self, db_session, printer_factory, patched):
        one = await _self_clearing(printer_factory, name="One", plate_capacity=5, plates_remaining=5)
        two = await _self_clearing(printer_factory, name="Two", plate_capacity=5, plates_remaining=5)

        one_id, two_id = one.id, two.id

        await consume_auto_cleared_plate(one_id)

        assert await _remaining(db_session, one_id) == 4
        assert await _remaining(db_session, two_id) == 5

    async def test_unknown_printer_is_a_noop(self, patched):
        await consume_auto_cleared_plate(9999)
        patched[0].send_printer_plates_changed.assert_not_awaited()

    async def test_notification_carries_the_name_and_capacity(self, db_session, printer_factory, patched):
        printer = await _self_clearing(printer_factory, name="Swap A1", plate_capacity=7, plates_remaining=1)

        await consume_auto_cleared_plate(printer.id)

        args = patched[1].on_plate_changer_empty.await_args.args
        assert args[:3] == (printer.id, "Swap A1", 7)


class TestFailClosed:
    @pytest.fixture
    def patched(self, test_engine, changer_mocks):
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with ExitStack() as stack:
            stack.enter_context(patch(f"{PC}.async_session", maker))
            stack.enter_context(patch("backend.app.core.database.async_session", maker))
            yield changer_mocks

    async def test_an_unbookable_count_blocks_the_printer_until_it_is_reset(
        self, db_session, printer_factory, patched, test_engine
    ):
        from backend.app.services.print_scheduler import PrintScheduler

        printer = await _self_clearing(printer_factory, name="Swap", plate_capacity=5, plates_remaining=3)
        real = plate_changer.run_with_retry
        calls = []

        async def flaky(fn, **kwargs):
            calls.append(kwargs["label"])
            if kwargs["label"] == "plate changer count":
                raise RuntimeError("database is locked")
            return await real(fn, **kwargs)

        with patch(f"{PC}.run_with_retry", flaky):
            await consume_auto_cleared_plate(printer.id)

        assert plate_changer.is_count_unknown(printer.id)
        assert await _remaining(db_session, printer.id) == 0  # survives a restart
        patched[1].on_plate_changer_empty.assert_awaited_once()
        assert not plate_changer.is_count_pending(printer.id)
        scheduler = PrintScheduler()
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            await scheduler._load_plate_changers(db)
        assert scheduler._plate_changer_empty == {printer.id: "Swap"}

    async def test_a_failing_zeroing_still_blocks_in_memory(self, printer_factory, patched):
        printer = await _self_clearing(printer_factory, name="Swap", plate_capacity=5, plates_remaining=3)

        with patch(f"{PC}.run_with_retry", AsyncMock(side_effect=RuntimeError("db down"))):
            await consume_auto_cleared_plate(printer.id)

        assert plate_changer.is_count_unknown(printer.id)

    async def test_a_printer_without_capacity_is_not_zeroed(self, db_session, printer_factory, patched):
        printer = await printer_factory(name="Plain")
        real = plate_changer.run_with_retry

        async def flaky(fn, **kwargs):
            if kwargs["label"] == "plate changer count":
                raise RuntimeError("boom")
            return await real(fn, **kwargs)

        with patch(f"{PC}.run_with_retry", flaky):
            await consume_auto_cleared_plate(printer.id)

        assert await _remaining(db_session, printer.id) is None
        patched[1].on_plate_changer_empty.assert_not_awaited()

    async def test_a_failing_notification_does_not_raise(self, printer_factory, patched):
        printer = await _self_clearing(printer_factory, name="Swap", plate_capacity=5, plates_remaining=3)
        patched[1].on_plate_changer_empty.side_effect = RuntimeError("provider down")
        real = plate_changer.run_with_retry

        async def flaky(fn, **kwargs):
            if kwargs["label"] == "plate changer count":
                raise RuntimeError("boom")
            return await real(fn, **kwargs)

        with patch(f"{PC}.run_with_retry", flaky):
            await consume_auto_cleared_plate(printer.id)

        assert plate_changer.is_count_unknown(printer.id)

    async def test_a_later_successful_count_does_not_clear_the_mark(self, printer_factory, patched):
        printer = await _self_clearing(printer_factory, name="Swap", plate_capacity=5, plates_remaining=3)
        plate_changer.mark_count_unknown(printer.id)

        await consume_auto_cleared_plate(printer.id)

        assert plate_changer.is_count_unknown(printer.id)


class TestPendingMarker:
    async def test_held_until_the_decrement_while_is_completing_stays_true(
        self, test_engine, db_session, queued_print, changer_mocks
    ):
        from backend.app.services.usage_tracker import is_completing

        await _setup(db_session, queued_print["printer"], remaining=2, override=False)
        pid = queued_print["printer"]
        seen = {}

        async def watching(printer_id):
            seen["before"] = plate_changer.is_count_pending(printer_id)
            await consume_auto_cleared_plate(printer_id)
            seen["after"] = plate_changer.is_count_pending(printer_id)
            seen["completing"] = is_completing(printer_id)

        with patch.object(main_module, "consume_auto_cleared_plate", watching):
            await _complete(test_engine, pid, "completed")

        assert seen == {"before": True, "after": False, "completing": True}
        assert not plate_changer.is_count_pending(pid)

    @pytest.mark.parametrize("status", ["failed", "aborted", "cancelled"])
    async def test_cleared_for_statuses_that_never_count(
        self, test_engine, db_session, queued_print, changer_mocks, status
    ):
        await _complete(test_engine, queued_print["printer"], status)
        assert not plate_changer.is_count_pending(queued_print["printer"])

    async def test_cleared_when_the_close_out_raises(self, queued_print):
        with (
            patch.object(main_module, "_close_out_print", AsyncMock(side_effect=RuntimeError("boom"))),
            pytest.raises(RuntimeError),
        ):
            await main_module.on_print_complete(queued_print["printer"], {"status": "completed"})
        assert not plate_changer.is_count_pending(queued_print["printer"])

    async def test_forget_printer_drops_the_marks(self):
        plate_changer.mark_count_pending(77)
        plate_changer.mark_count_unknown(77)
        plate_changer.forget_printer(77)
        assert not plate_changer.is_count_pending(77)
        assert not plate_changer.is_count_unknown(77)


class TestFeatureOffCost:
    async def test_no_setting_read_and_no_write_without_a_capacity(self, printer_factory, test_engine, changer_mocks):
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        printer = await _self_clearing(printer_factory, name="Plain")
        get_setting = AsyncMock()
        plate_changer.mark_count_pending(printer.id)
        with (
            patch(f"{PC}.async_session", maker),
            patch("backend.app.core.database.async_session", maker),
            patch("backend.app.api.routes.settings.get_setting", get_setting),
        ):
            await consume_auto_cleared_plate(printer.id)
            await consume_auto_cleared_plate(9999)

        get_setting.assert_not_awaited()
        assert not plate_changer.is_count_pending(printer.id)


class TestWhoClearsThePlate:
    """A plate is spent exactly where the plate-clear gate of #2860 does not apply."""

    @pytest.fixture
    def patched(self, test_engine, changer_mocks):
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with (
            patch(f"{PC}.async_session", maker),
            patch("backend.app.core.database.async_session", maker),
        ):
            yield changer_mocks

    @pytest.mark.parametrize(
        ("override", "setting", "counted"),
        [
            (True, True, False),
            (True, False, False),
            (False, True, True),
            (False, False, True),
            (None, True, False),
            (None, False, True),
        ],
    )
    async def test_truth_table(self, db_session, printer_factory, patched, override, setting, counted):
        printer = await printer_factory(name="Swap")
        await _setup(db_session, printer.id, remaining=5, override=override, require_clear=setting)

        await consume_auto_cleared_plate(printer.id)

        assert await _remaining(db_session, printer.id) == (4 if counted else 5)

    async def test_the_setting_goes_through_get_setting(self, db_session, printer_factory, patched):
        printer = await printer_factory(name="Swap", plate_capacity=5, plates_remaining=5)
        with patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value="true")) as get:
            await consume_auto_cleared_plate(printer.id)

        get.assert_awaited_once()
        assert get.await_args.args[1] == "require_plate_clear"
        assert await _remaining(db_session, printer.id) == 5

    async def test_asks_the_same_cache_as_the_queue(self, db_session, printer_factory, patched):
        """The count and the scheduler's gate must agree, so both ask plate_clear."""
        printer = await printer_factory(name="Swap", plate_capacity=5, plates_remaining=5)
        db_session.add(Settings(key="require_plate_clear", value="true"))
        await db_session.commit()

        with patch(f"{PC}.plate_clear.applies", return_value=False) as applies:
            await consume_auto_cleared_plate(printer.id)

        applies.assert_called_once_with(printer.id, True)
        assert await _remaining(db_session, printer.id) == 4

    async def test_a_setting_that_cannot_be_read_fails_closed(self, db_session, printer_factory, patched):
        printer = await _self_clearing(printer_factory, name="Swap", plate_capacity=5, plates_remaining=3)
        with patch("backend.app.api.routes.settings.get_setting", AsyncMock(side_effect=RuntimeError("boom"))):
            await consume_auto_cleared_plate(printer.id)

        assert plate_changer.is_count_unknown(printer.id)
        assert await _remaining(db_session, printer.id) == 0
