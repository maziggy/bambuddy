"""An "any model" job goes to a printer that has the filament for it (#3137).

The reporter's farm queues jobs for "any H2D Pro". The matcher took the first
idle H2D Pro with the right filament *type*, and only then did the deficit gate
look at the amount. It held the job on that printer, pinned there, while
another idle H2D Pro had plenty. The job only ran once the user moved it there
by hand.

Now the matcher passes over a printer that would be short while another
eligible one is not. When every idle printer is short the job is held on the
first, as before, so "Print Anyway" still has a printer to print on; and once
another printer of its model can run it, the hold is released and the job
goes there.
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
from backend.app.services.filament_deficit import FilamentDeficit
from backend.app.services.print_scheduler import PrintScheduler

_DEFICIT = [
    FilamentDeficit(
        slot_id=1,
        ams_id=0,
        tray_id=0,
        filament_type="PLA",
        required_grams=120.0,
        remaining_grams=40.0,
    )
]


@pytest.fixture
async def queue_db():
    """Three X1Cs, in id order, so "the first idle printer" is well defined."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        db.add_all(
            [
                Printer(
                    id=pid,
                    name=f"X1C-{pid}",
                    serial_number=f"X1C000{pid}",
                    ip_address=f"10.0.0.{pid}",
                    access_code="x",
                    model="X1C",
                    is_active=True,
                )
                for pid in (1, 2, 3)
            ]
        )
        await db.commit()

    try:
        yield SimpleNamespace(session_maker=session_maker)
    finally:
        await engine.dispose()


async def _add_item(ctx, **fields):
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
        values = {"status": "pending", "position": 1, "target_model": "X1C", "library_file_id": lib.id}
        values.update(fields)
        item = PrintQueueItem(**values)
        db.add(item)
        await db.commit()
        return item.id


async def _get_item(ctx, item_id):
    async with ctx.session_maker() as db:
        return (await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


def _short_on(short: set[int], unknown: frozenset[int] = frozenset()) -> AsyncMock:
    """``_filament_short_on`` stand-in: the printers in ``short`` are too light,
    and those in ``unknown`` have spools whose amount is not on record."""

    async def check(db, item, printer_id, *, require_known=False):
        return printer_id in short or (require_known and printer_id in unknown)

    return AsyncMock(side_effect=check)


async def _run(
    ctx, scheduler, *, short: set[int], launched, idle=lambda pid: True, short_check=None, unknown=frozenset()
):
    """One queue pass. The deficit gate is real; only the deficit it reads is faked."""

    async def deficit(db, item, *, printer_id=None, ams_mapping=None):
        return _DEFICIT if (printer_id or item.printer_id) in short else []

    patches = [
        patch("backend.app.services.print_scheduler.async_session", ctx.session_maker),
        patch("backend.app.core.database.async_session", ctx.session_maker),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
        patch(
            "backend.app.services.print_scheduler.ha_sensor_manager.blocked_printers",
            AsyncMock(return_value={}),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_waiting",
            AsyncMock(),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_assigned",
            AsyncMock(),
        ),
        patch("backend.app.services.print_scheduler.compute_deficit_for_queue_item", AsyncMock(side_effect=deficit)),
        patch.object(scheduler, "_filament_short_on", short_check or _short_on(short, unknown)),
        patch.object(scheduler, "_is_printer_idle", MagicMock(side_effect=lambda pid, *a, **k: idle(pid))),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
        patch.object(scheduler, "_ensure_ams_mapping", AsyncMock(return_value=None)),
        patch.object(scheduler, "_block_on_unmatched_filament", AsyncMock(return_value=False)),
        patch.object(scheduler, "_launch_uploads", launched),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return await scheduler.check_queue()


class TestMatcher:
    """``_find_idle_printer_for_model`` with and without the shortfall check."""

    async def _find(self, ctx, scheduler, *, short=None, **kwargs):
        async def is_short(pid):
            return pid in short

        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        ):
            async with ctx.session_maker() as db:
                return await scheduler._find_idle_printer_for_model(
                    db, "X1C", set(), is_short=is_short if short is not None else None, **kwargs
                )

    @pytest.mark.asyncio
    async def test_passes_over_a_short_printer(self, queue_db):
        assert await self._find(queue_db, PrintScheduler(), short={1}) == (2, None)

    @pytest.mark.asyncio
    async def test_every_printer_short_returns_the_first(self, queue_db):
        """The deficit gate then holds the job there, where Print Anyway can start it."""
        assert await self._find(queue_db, PrintScheduler(), short={1, 2, 3}) == (1, None)

    @pytest.mark.asyncio
    async def test_without_the_check_the_first_idle_printer_wins(self, queue_db):
        assert await self._find(queue_db, PrintScheduler()) == (1, None)

    @pytest.mark.asyncio
    async def test_force_colour_jobs_pass_over_a_short_printer(self, queue_db):
        scheduler = PrintScheduler()
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#FF0000", "force_color_match": True}]
        with patch.object(scheduler, "_get_missing_force_color_slots", MagicMock(return_value=[])):
            result = await self._find(queue_db, scheduler, short={1}, filament_overrides=overrides)
        assert result == (2, None)

    @pytest.mark.asyncio
    async def test_colour_ranking_takes_the_best_printer_that_is_not_short(self, queue_db):
        scheduler = PrintScheduler()
        overrides = [{"slot_id": 1, "type": "PLA", "color": "#FF0000"}]
        matches = {1: 1, 2: 2, 3: 1}
        with patch.object(
            scheduler, "_count_override_color_matches", MagicMock(side_effect=lambda pid, _o: matches[pid])
        ):
            ranked_first = await self._find(queue_db, scheduler, short=set(), filament_overrides=overrides)
            best_is_short = await self._find(queue_db, scheduler, short={2}, filament_overrides=overrides)
            all_short = await self._find(queue_db, scheduler, short={1, 2, 3}, filament_overrides=overrides)

        assert ranked_first == (2, None)
        assert best_is_short == (1, None)
        assert all_short == (2, None)


class TestAssignment:
    @pytest.mark.asyncio
    async def test_goes_to_the_printer_with_enough_filament(self, queue_db):
        """The reporter's case: printer 1 is idle but light, printer 2 idle and full."""
        item_id = await _add_item(queue_db)
        launched = MagicMock()

        await _run(queue_db, PrintScheduler(), short={1}, launched=launched)

        launched.assert_called_once()
        assert launched.call_args[0][0] == [item_id]
        item = await _get_item(queue_db, item_id)
        assert item.printer_id == 2
        assert item.manual_start is False
        assert item.filament_short is False

    @pytest.mark.asyncio
    async def test_held_on_the_first_printer_when_every_idle_printer_is_short(self, queue_db):
        item_id = await _add_item(queue_db)
        launched = MagicMock()

        await _run(queue_db, PrintScheduler(), short={1, 2, 3}, launched=launched)

        launched.assert_not_called()
        item = await _get_item(queue_db, item_id)
        assert item.printer_id == 1
        assert item.manual_start is True
        assert item.filament_short is True

    @pytest.mark.asyncio
    async def test_print_anyway_is_not_second_guessed(self, queue_db):
        """With Print Anyway set, no printer is passed over for being short."""
        item_id = await _add_item(queue_db, skip_filament_check=True)
        launched = MagicMock()
        scheduler = PrintScheduler()

        with patch(
            "backend.app.services.print_scheduler.PrintScheduler._compute_ams_mapping_for_printer",
            AsyncMock(return_value=[0]),
        ):
            await _run(
                queue_db,
                scheduler,
                short={1},
                launched=launched,
                short_check=AsyncMock(side_effect=scheduler._filament_short_on),
            )

        launched.assert_called_once()
        assert (await _get_item(queue_db, item_id)).printer_id == 1


class TestHeldJob:
    @pytest.mark.asyncio
    async def test_moves_to_another_printer_that_has_the_filament(self, queue_db):
        item_id = await _add_item(
            queue_db,
            printer_id=1,
            manual_start=True,
            filament_short=True,
            # Worked out for printer 1; printer 2 works out its own.
            ams_mapping="[0]",
            nozzle_mapping="[16]",
            nozzle_rack_choice='{"0": 16}',
        )
        launched = MagicMock()

        await _run(queue_db, PrintScheduler(), short={1}, launched=launched)

        launched.assert_called_once()
        assert launched.call_args[0][0] == [item_id]
        item = await _get_item(queue_db, item_id)
        assert item.printer_id == 2
        assert item.manual_start is False
        assert item.filament_short is False
        assert (item.ams_mapping, item.nozzle_mapping, item.nozzle_rack_choice) == (None, None, None)

    @pytest.mark.asyncio
    async def test_stays_held_while_every_other_printer_is_short(self, queue_db):
        item_id = await _add_item(queue_db, printer_id=1, manual_start=True, filament_short=True)
        launched = MagicMock()

        await _run(queue_db, PrintScheduler(), short={1, 2, 3}, launched=launched)

        launched.assert_not_called()
        item = await _get_item(queue_db, item_id)
        assert item.printer_id == 1
        assert item.manual_start is True
        assert item.filament_short is True

    @pytest.mark.asyncio
    async def test_stays_held_while_the_other_printers_are_busy(self, queue_db):
        """Item 2080 in the reporter's log: the printer it waits on was the only idle one."""
        item_id = await _add_item(queue_db, printer_id=1, manual_start=True, filament_short=True)
        launched = MagicMock()
        short_check = _short_on({1})

        await _run(
            queue_db, PrintScheduler(), short={1}, launched=launched, idle=lambda pid: pid == 1, short_check=short_check
        )

        launched.assert_not_called()
        assert (await _get_item(queue_db, item_id)).printer_id == 1
        # The printer it is held on is not checked again: a spool loaded there
        # is started with Start, as before.
        short_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_job_the_user_put_on_a_printer_is_not_moved(self, queue_db):
        item_id = await _add_item(queue_db, target_model=None, printer_id=1, manual_start=True, filament_short=True)
        launched = MagicMock()

        await _run(queue_db, PrintScheduler(), short={1}, launched=launched)

        launched.assert_not_called()
        item = await _get_item(queue_db, item_id)
        assert item.printer_id == 1
        assert item.manual_start is True

    @pytest.mark.asyncio
    async def test_a_job_held_for_another_reason_is_not_moved(self, queue_db):
        """Only the deficit gate sets filament_short; a plain manual start waits for the user."""
        item_id = await _add_item(queue_db, printer_id=1, manual_start=True, filament_short=False)
        launched = MagicMock()
        short_check = _short_on(set())

        await _run(queue_db, PrintScheduler(), short=set(), launched=launched, short_check=short_check)

        launched.assert_not_called()
        assert (await _get_item(queue_db, item_id)).manual_start is True
        short_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_moved_to_a_printer_whose_spools_are_not_tracked(self, queue_db):
        """Moving starts the job with nobody asked, so "nothing against it" is not enough."""
        item_id = await _add_item(queue_db, printer_id=1, manual_start=True, filament_short=True)
        launched = MagicMock()

        await _run(queue_db, PrintScheduler(), short={1}, unknown={2, 3}, launched=launched)

        launched.assert_not_called()
        item = await _get_item(queue_db, item_id)
        assert item.printer_id == 1
        assert item.manual_start is True

    @pytest.mark.asyncio
    async def test_goes_to_the_printer_that_was_checked(self, queue_db):
        """Printer 2 comes first and has nothing against it, but only printer 3
        has the amount on record: the job goes to 3, not to whichever printer a
        fresh pick would find first."""
        item_id = await _add_item(queue_db, printer_id=1, manual_start=True, filament_short=True)
        launched = MagicMock()

        await _run(queue_db, PrintScheduler(), short={1}, unknown={2}, launched=launched)

        launched.assert_called_once()
        item = await _get_item(queue_db, item_id)
        assert item.printer_id == 3
        assert item.target_model == "X1C"
        assert item.manual_start is False
        assert item.filament_short is False
        assert item.waiting_reason is None

    @pytest.mark.asyncio
    async def test_a_failing_check_keeps_the_hold_and_the_pass_going(self, queue_db):
        held_id = await _add_item(queue_db, printer_id=1, manual_start=True, filament_short=True)
        other_id = await _add_item(queue_db, position=2, target_model=None, printer_id=3)
        launched = MagicMock()
        scheduler = PrintScheduler()

        with patch.object(scheduler, "_find_idle_printer_for_model", AsyncMock(side_effect=RuntimeError("boom"))):
            await _run(queue_db, scheduler, short={1}, launched=launched)

        held = await _get_item(queue_db, held_id)
        assert held.printer_id == 1
        assert held.manual_start is True
        launched.assert_called_once()
        assert launched.call_args[0][0] == [other_id]

    @pytest.mark.asyncio
    async def test_looks_again_only_after_the_recheck_interval(self, queue_db):
        item_id = await _add_item(queue_db, printer_id=1, manual_start=True, filament_short=True)
        scheduler = PrintScheduler()
        short_check = _short_on({1, 2, 3})

        await _run(queue_db, scheduler, short={1, 2, 3}, launched=MagicMock(), short_check=short_check)
        first_pass_calls = short_check.await_count
        await _run(queue_db, scheduler, short={1, 2, 3}, launched=MagicMock(), short_check=short_check)
        assert short_check.await_count == first_pass_calls

        scheduler._short_hold_checked_at[item_id] -= 121
        launched = MagicMock()
        await _run(queue_db, scheduler, short={1}, launched=launched)

        launched.assert_called_once()
        assert (await _get_item(queue_db, item_id)).printer_id == 2


class TestFilamentShortOn:
    """``_filament_short_on``'s answer where it cannot see the amounts."""

    @staticmethod
    async def _ask(*, mapping=None, deficit=None, raises=None, skip=False, require_known=False):
        scheduler = PrintScheduler()
        item = PrintQueueItem(id=7, skip_filament_check=skip)
        compute = AsyncMock(side_effect=raises) if raises else AsyncMock(return_value=mapping)
        with (
            patch.object(scheduler, "_compute_ams_mapping_for_printer", compute),
            patch(
                "backend.app.services.print_scheduler.compute_deficit_for_queue_item",
                AsyncMock(return_value=deficit or []),
            ),
        ):
            return await scheduler._filament_short_on(MagicMock(), item, 1, require_known=require_known)

    @pytest.mark.asyncio
    async def test_a_deficit_is_short(self):
        assert await self._ask(mapping=[0], deficit=_DEFICIT) is True
        assert await self._ask(mapping=[0], deficit=_DEFICIT, require_known=True) is True

    @pytest.mark.asyncio
    async def test_enough_is_not_short(self):
        assert await self._ask(mapping=[0]) is False
        assert await self._ask(mapping=[0], require_known=True) is False

    @pytest.mark.asyncio
    async def test_no_mapping_counts_against_the_printer_only_when_required_known(self):
        assert await self._ask(mapping=None) is False
        assert await self._ask(mapping=None, require_known=True) is True

    @pytest.mark.asyncio
    async def test_a_failing_check_counts_against_the_printer_only_when_required_known(self):
        assert await self._ask(raises=RuntimeError("boom")) is False
        assert await self._ask(raises=RuntimeError("boom"), require_known=True) is True

    @pytest.mark.asyncio
    async def test_print_anyway_is_never_short(self):
        assert await self._ask(mapping=[0], deficit=_DEFICIT, skip=True) is False
        assert await self._ask(mapping=[0], deficit=_DEFICIT, skip=True, require_known=True) is False
