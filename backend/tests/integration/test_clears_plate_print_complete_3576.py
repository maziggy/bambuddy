"""A finished print whose job clears its own plate raises no plate gate (#3576).

``on_print_complete`` runs against real rows (like test_print_complete_end_to_end);
only the outside world is mocked, plus ``printer_manager.set_awaiting_plate_clear``,
which is spied on (and still records the flag so the scheduler sees it).

Only a *completed* job with the flag, on a printer that follows the app setting
(no per-printer ``require_plate_clear``, #2860), with the app-wide
``require_plate_clear`` on, skips the gate. Everything else raises it as before.
"""

import itertools
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app import main as main_module
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.services import plate_clear
from backend.app.services.print_scheduler import PrintScheduler
from backend.app.services.printer_manager import printer_manager
from backend.tests.integration.test_print_complete_end_to_end import (  # noqa: F401
    _clean,
    _complete,
    _no_finish_photo,
    queued_print,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

STATUSES = ["completed", "failed", "aborted", "cancelled"]
QUEUE_STATUS = {"completed": "completed", "failed": "failed", "aborted": "cancelled", "cancelled": "cancelled"}


@pytest.fixture
def gate():
    """Spy on the plate-clear gate; it still records the flag like the real one."""

    def _set(printer_id, awaiting):
        if awaiting:
            printer_manager._awaiting_plate_clear.add(printer_id)
        else:
            printer_manager._awaiting_plate_clear.discard(printer_id)

    with patch.object(printer_manager, "set_awaiting_plate_clear", MagicMock(side_effect=_set)) as spy:
        yield spy
    printer_manager._awaiting_plate_clear.clear()
    plate_clear.load_overrides({})


async def _setup(db_session, qp, *, flag=True, override=None, require_clear=True):
    """``override`` is the printer's own require_plate_clear: None follows the app setting."""
    printer = await db_session.get(Printer, qp["printer"])
    printer.require_plate_clear = override
    # The row and the cache move together, as the printer routes keep them (#2860).
    plate_clear.set_override(qp["printer"], override)
    item = await db_session.get(PrintQueueItem, qp["item"])
    item.clears_plate = flag
    if require_clear is not None:
        db_session.add(Settings(key="require_plate_clear", value="true" if require_clear else "false"))
    await db_session.commit()


def _raised(gate, printer_id) -> bool:
    return any(call.args == (printer_id, True) for call in gate.call_args_list)


class TestMatrix:
    @pytest.mark.parametrize(
        ("status", "flag", "require_clear", "override"),
        list(itertools.product(STATUSES, [True, False], [True, False], [None, True, False])),
    )
    async def test_gate_and_row(
        self, test_engine, db_session, queued_print, gate, status, flag, require_clear, override
    ):
        await _setup(db_session, queued_print, flag=flag, override=override, require_clear=require_clear)

        await _complete(test_engine, queued_print["printer"], status)

        skipped = status == "completed" and flag and override is None and require_clear
        assert _raised(gate, queued_print["printer"]) is (not skipped)
        assert printer_manager.is_awaiting_plate_clear(queued_print["printer"]) is (not skipped)
        # The row is closed with the finish status either way.
        db_session.expire_all()
        item = await db_session.get(PrintQueueItem, queued_print["item"])
        archive = await db_session.get(PrintArchive, queued_print["archive"])
        assert item.status == QUEUE_STATUS[status]
        assert item.completed_at is not None
        assert archive.status == QUEUE_STATUS[status] or (status == "aborted" and archive.status == "aborted")

    async def test_the_flag_is_unchanged_by_the_finish(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print)

        await _complete(test_engine, queued_print["printer"], "completed")

        db_session.expire_all()
        assert (await db_session.get(PrintQueueItem, queued_print["item"])).clears_plate is True

    async def test_no_require_plate_clear_row_reads_as_off(self, test_engine, db_session, queued_print, gate):
        """Setting unset: nothing to lift, the gate is raised as today."""
        await _setup(db_session, queued_print, require_clear=None)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert _raised(gate, queued_print["printer"])

    async def test_a_print_started_outside_bambuddy_still_raises(self, test_engine, db_session, queued_print, gate):
        """No queue row to read the flag from."""
        await _setup(db_session, queued_print)
        item = await db_session.get(PrintQueueItem, queued_print["item"])
        item.status = "cancelled"
        await db_session.commit()
        main_module._active_prints.clear()

        await _complete(test_engine, queued_print["printer"], "completed", name="from_the_screen")

        assert _raised(gate, queued_print["printer"])


class TestFailSafe:
    """Anything doubtful keeps the confirmation."""

    async def test_subtask_mismatch(self, test_engine, db_session, queued_print, gate):
        """The completion is about some other print: it closes nothing and grants nothing."""
        await _setup(db_session, queued_print)

        await _complete(test_engine, queued_print["printer"], "completed", name="somebody_elses_print")

        assert _raised(gate, queued_print["printer"])
        db_session.expire_all()
        assert (await db_session.get(PrintQueueItem, queued_print["item"])).status == "printing"

    async def test_two_printing_rows(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print)
        db_session.add(
            PrintQueueItem(
                printer_id=queued_print["printer"],
                archive_id=queued_print["archive"],
                status="printing",
                position=2,
                clears_plate=True,
            )
        )
        await db_session.commit()

        await _complete(test_engine, queued_print["printer"], "completed")

        assert _raised(gate, queued_print["printer"])

    async def test_reconciled_archive_id_mismatch(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print)

        await _complete(
            test_engine,
            queued_print["printer"],
            "completed",
            extra={"_reconciled": True, "_reconciled_archive_id": queued_print["archive"] + 1000},
        )

        assert _raised(gate, queued_print["printer"])

    async def test_reconciled_archive_id_match_skips(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print)

        await _complete(
            test_engine,
            queued_print["printer"],
            "completed",
            extra={"_reconciled": True, "_reconciled_archive_id": queued_print["archive"]},
        )

        assert not _raised(gate, queued_print["printer"])

    async def test_item_without_archive_is_unverifiable_and_keeps_the_gate(
        self, test_engine, db_session, queued_print, gate
    ):
        """Lifting the gate needs a positive name match; a row with no archive cannot give one."""
        await _setup(db_session, queued_print)
        item = await db_session.get(PrintQueueItem, queued_print["item"])
        item.archive_id = None
        await db_session.commit()

        await _complete(test_engine, queued_print["printer"], "completed")

        assert _raised(gate, queued_print["printer"])

    async def test_archive_without_filename_keeps_the_gate(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print)
        archive = await db_session.get(PrintArchive, queued_print["archive"])
        archive.filename = ""
        await db_session.commit()

        await _complete(test_engine, queued_print["printer"], "completed")

        assert _raised(gate, queued_print["printer"])

    async def test_empty_subtask_name_keeps_the_gate(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print)

        await _complete(test_engine, queued_print["printer"], "completed", name="")

        assert _raised(gate, queued_print["printer"])

    async def test_positive_match_skips_without_logging_the_mismatch_warning(
        self, test_engine, db_session, queued_print, gate, caplog
    ):
        await _setup(db_session, queued_print)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert not _raised(gate, queued_print["printer"])
        assert "Ignoring print completion" not in caplog.text

    async def test_exception_while_reading_the_flag(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print)

        with patch.object(main_module, "_queue_item_dispatched_name", AsyncMock(side_effect=RuntimeError("boom"))):
            maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
            with patch("backend.app.main.async_session", maker):
                assert (
                    await main_module._finishing_job_clears_plate(queued_print["printer"], {"subtask_name": "part"})
                    is False
                )

    async def test_exception_in_the_helper_during_a_completion(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print)

        with patch.object(main_module, "async_session", MagicMock(side_effect=RuntimeError("db gone"))):
            assert await main_module._finishing_job_clears_plate(queued_print["printer"], {}) is False
            assert await main_module._job_skips_plate_confirmation(queued_print["printer"]) is False

    async def test_policy_read_failure_keeps_the_gate(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print)

        with patch(
            "backend.app.services.print_scheduler.load_plate_clear_policy", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            await _complete(test_engine, queued_print["printer"], "completed")

        assert _raised(gate, queued_print["printer"])
        db_session.expire_all()
        assert (await db_session.get(PrintQueueItem, queued_print["item"])).status == "completed"

    async def test_flag_helper_failure_inside_the_completion_keeps_the_gate(
        self, test_engine, db_session, queued_print, gate
    ):
        """Whatever fails in the lookup, the completion itself still closes the row."""
        await _setup(db_session, queued_print)

        with patch.object(main_module, "_queue_item_dispatched_name", AsyncMock(side_effect=RuntimeError("boom"))):
            await _complete(test_engine, queued_print["printer"], "completed")

        assert _raised(gate, queued_print["printer"])
        db_session.expire_all()
        assert (await db_session.get(PrintQueueItem, queued_print["item"])).status == "completed"


class TestCalibrationCompletionAndMidRunGate:
    """Upstream behaviour is kept for every non-flagged completion; a flagged success ends with the gate down (#3576)."""

    @pytest.mark.parametrize("status", ["failed", "cancelled", "aborted"])
    async def test_internal_subtask_failure_while_a_row_prints_raises(
        self, test_engine, db_session, queued_print, gate, status
    ):
        await _setup(db_session, queued_print)

        await _complete(test_engine, queued_print["printer"], status, name="auto_pa_line_calib_mode")

        assert _raised(gate, queued_print["printer"])
        assert printer_manager.is_awaiting_plate_clear(queued_print["printer"])

    async def test_flagged_success_after_a_mid_run_raise_ends_with_the_gate_down(
        self, test_engine, db_session, queued_print, gate
    ):
        await _setup(db_session, queued_print)
        printer_manager._awaiting_plate_clear.add(queued_print["printer"])

        await _complete(test_engine, queued_print["printer"], "completed")

        assert any(call.args == (queued_print["printer"], False) for call in gate.call_args_list)
        assert not printer_manager.is_awaiting_plate_clear(queued_print["printer"])

    async def test_unflagged_success_keeps_a_mid_run_raise(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print, flag=False)
        printer_manager._awaiting_plate_clear.add(queued_print["printer"])

        await _complete(test_engine, queued_print["printer"], "completed")

        assert printer_manager.is_awaiting_plate_clear(queued_print["printer"])

    async def test_reconciled_archive_id_match_beats_a_differing_name(
        self, test_engine, db_session, queued_print, gate
    ):
        await _setup(db_session, queued_print)

        await _complete(
            test_engine,
            queued_print["printer"],
            "completed",
            name="a_different_print_name",
            extra={"_reconciled": True, "_reconciled_archive_id": queued_print["archive"]},
        )

        assert not _raised(gate, queued_print["printer"])
        assert not printer_manager.is_awaiting_plate_clear(queued_print["printer"])

    async def test_reconciled_archive_id_mismatch_raises_even_with_a_matching_name(
        self, test_engine, db_session, queued_print, gate
    ):
        await _setup(db_session, queued_print)

        await _complete(
            test_engine,
            queued_print["printer"],
            "completed",
            extra={"_reconciled": True, "_reconciled_archive_id": queued_print["archive"] + 1000},
        )

        assert _raised(gate, queued_print["printer"])


class TestPolicyHelper:
    @pytest.mark.parametrize(
        ("override", "require_clear", "expected"),
        [
            (None, True, True),
            (None, False, False),
            (True, True, False),
            (True, False, False),
            (False, True, False),
            (False, False, False),
        ],
    )
    async def test_only_a_printer_following_the_setting_that_is_on_is_lifted(
        self, test_engine, db_session, queued_print, override, require_clear, expected
    ):
        await _setup(db_session, queued_print, override=override, require_clear=require_clear)
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        with patch("backend.app.main.async_session", maker):
            assert await main_module._job_skips_plate_confirmation(queued_print["printer"]) is expected

    async def test_the_printers_own_setting_is_read_where_the_gate_reads_it(
        self, test_engine, db_session, queued_print
    ):
        """One source for the printer's own setting: the cache the scheduler's gate asks (#2860)."""
        await _setup(db_session, queued_print)
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        with patch("backend.app.main.async_session", maker):
            assert await main_module._job_skips_plate_confirmation(queued_print["printer"]) is True
            plate_clear.set_override(queued_print["printer"], True)
            assert await main_module._job_skips_plate_confirmation(queued_print["printer"]) is False
            plate_clear.set_override(queued_print["printer"], None)
            assert await main_module._job_skips_plate_confirmation(queued_print["printer"]) is True
        plate_clear.load_overrides({})


class TestPlateClearNotification:
    """ "Plate clear required" goes out only where somebody has to clear the plate (#2860, #3576)."""

    async def _notified(self, test_engine, printer_id, status) -> int:
        """Finish a print through the real gate setter; count the notifications it raises."""
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        scheduled = []
        required = AsyncMock()
        with (
            patch.object(printer_manager, "_loop", MagicMock(is_running=MagicMock(return_value=True))),
            patch.object(printer_manager, "_persist_awaiting_plate_clear", MagicMock(return_value=None)),
            patch.object(printer_manager, "_broadcast_status_change", MagicMock(return_value=None)),
            patch.object(printer_manager, "_schedule_async", MagicMock(side_effect=scheduled.append)),
            patch("backend.app.services.mqtt_relay.mqtt_relay.on_plate_clear_state", AsyncMock()),
            patch("backend.app.services.notification_service.notification_service.on_plate_clear_required", required),
        ):
            try:
                await _complete(test_engine, printer_id, status)
                with patch("backend.app.core.database.async_session", maker):
                    for coro in scheduled:
                        if coro is not None:
                            await coro
            finally:
                printer_manager._awaiting_plate_clear.clear()
                plate_clear.load_overrides({})
        return required.await_count

    @pytest.mark.parametrize(
        ("status", "flag", "require_clear", "override", "expected"),
        [
            # A flagged success on a printer that follows the setting: nothing to clear.
            ("completed", True, True, None, 0),
            # Without the flag, or when the flagged job did not succeed: as upstream.
            ("completed", False, True, None, 1),
            ("failed", True, True, None, 1),
            ("aborted", True, True, None, 1),
            ("cancelled", True, True, None, 1),
            # The printer's own setting decides on its own, flag or not.
            ("completed", True, True, True, 1),
            ("completed", True, False, True, 1),
            ("completed", True, True, False, 0),
            ("failed", True, True, False, 0),
            # The app setting off and no setting of its own: the gate holds nobody.
            ("completed", True, False, None, 0),
            ("completed", False, False, None, 0),
        ],
    )
    async def test_notification(
        self, test_engine, db_session, queued_print, status, flag, require_clear, override, expected
    ):
        await _setup(db_session, queued_print, flag=flag, override=override, require_clear=require_clear)

        assert await self._notified(test_engine, queued_print["printer"], status) == expected


class TestTheNextJobDispatches:
    """After a skip the printer is free: the following pending item launches in the next queue pass."""

    async def _add_pending(self, db_session, printer_id):
        lib = LibraryFile(
            filename="next.gcode.3mf",
            file_path="/library/next.gcode.3mf",
            file_size=10,
            file_type="gcode.3mf",
            file_metadata={"sliced_for_model": "X1C"},
        )
        db_session.add(lib)
        await db_session.flush()
        item = PrintQueueItem(status="pending", position=5, printer_id=printer_id, library_file_id=lib.id)
        db_session.add(item)
        await db_session.commit()
        return item.id

    async def _pass(self, test_engine, scheduler):
        """One check_queue pass; the printer reports FINISH, the gate is the real one."""
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        launched = MagicMock()
        patches = [
            patch("backend.app.services.print_scheduler.async_session", maker),
            patch("backend.app.core.database.async_session", maker),
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch(
                "backend.app.services.print_scheduler.printer_manager.get_status",
                MagicMock(return_value=MagicMock(state="FINISH")),
            ),
            patch(
                "backend.app.services.print_scheduler.ha_sensor_manager.blocked_printers", AsyncMock(return_value={})
            ),
            patch("backend.app.services.notification_service.notification_service.on_queue_job_waiting", AsyncMock()),
            patch("backend.app.services.notification_service.notification_service.on_queue_job_assigned", AsyncMock()),
            patch.object(scheduler, "_check_auto_drying", AsyncMock()),
            patch.object(scheduler, "_ensure_ams_mapping", AsyncMock(return_value=None)),
            patch.object(scheduler, "_block_on_filament_deficit", AsyncMock(return_value=False)),
            patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[])),
            patch.object(scheduler, "_launch_uploads", launched),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            await scheduler.check_queue()
        return launched

    async def test_flagged_success_lets_the_next_item_start(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print)
        next_id = await self._add_pending(db_session, queued_print["printer"])

        await _complete(test_engine, queued_print["printer"], "completed")
        launched = await self._pass(test_engine, PrintScheduler())

        launched.assert_called_once()
        assert launched.call_args[0][0] == [next_id]

    async def test_unflagged_success_still_waits(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print, flag=False)
        await self._add_pending(db_session, queued_print["printer"])

        await _complete(test_engine, queued_print["printer"], "completed")
        launched = await self._pass(test_engine, PrintScheduler())

        launched.assert_not_called()

    async def test_flagged_failure_still_waits(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print)
        await self._add_pending(db_session, queued_print["printer"])

        await _complete(test_engine, queued_print["printer"], "failed")
        launched = await self._pass(test_engine, PrintScheduler())

        launched.assert_not_called()

    async def test_flagged_success_on_an_always_require_printer_still_waits(
        self, test_engine, db_session, queued_print, gate
    ):
        await _setup(db_session, queued_print, override=True)
        await self._add_pending(db_session, queued_print["printer"])

        await _complete(test_engine, queued_print["printer"], "completed")
        launched = await self._pass(test_engine, PrintScheduler())

        launched.assert_not_called()


class TestAlternativesJob:
    """One job, [A1 file flagged, P1S file unflagged]: the printer that wins decides."""

    async def _resolve_to(self, db_session, qp, winner_flag: bool):
        """Resolve the printing row to an alternative with the given flag, as the scheduler does."""
        from types import SimpleNamespace

        from backend.app.models.print_queue import PrintQueueVariant

        a1 = LibraryFile(filename="a1.3mf", file_path="/l/a1.3mf", file_size=1, file_type="3mf", clears_plate=True)
        p1s = LibraryFile(filename="p1s.3mf", file_path="/l/p1s.3mf", file_size=1, file_type="3mf", clears_plate=False)
        db_session.add_all([a1, p1s])
        await db_session.flush()
        item = await db_session.get(PrintQueueItem, qp["item"])
        variants = [
            PrintQueueVariant(
                queue_item_id=item.id, position=0, library_file_id=a1.id, target_model="A1", clears_plate=True
            ),
            PrintQueueVariant(
                queue_item_id=item.id, position=1, library_file_id=p1s.id, target_model="P1S", clears_plate=False
            ),
        ]
        db_session.add_all(variants)
        await db_session.flush()
        winner = variants[0] if winner_flag else variants[1]
        PrintScheduler()._resolve_variant(item, SimpleNamespace(variant=winner))
        # Dispatch keeps the archive; resolution is the only part under test.
        item.archive = await db_session.get(PrintArchive, qp["archive"])
        await db_session.commit()

    async def test_the_flagged_alternative_skips_the_gate(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print, flag=False)
        await self._resolve_to(db_session, queued_print, winner_flag=True)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert not _raised(gate, queued_print["printer"])
        assert printer_manager.is_awaiting_plate_clear(queued_print["printer"]) is False

    async def test_the_unflagged_alternative_raises_the_gate(self, test_engine, db_session, queued_print, gate):
        await _setup(db_session, queued_print, flag=True)
        await self._resolve_to(db_session, queued_print, winner_flag=False)

        await _complete(test_engine, queued_print["printer"], "completed")

        assert _raised(gate, queued_print["printer"])
        assert printer_manager.is_awaiting_plate_clear(queued_print["printer"]) is True
