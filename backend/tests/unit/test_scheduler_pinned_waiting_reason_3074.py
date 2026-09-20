"""A queue item pinned to one printer explains itself (#3074).

Bambuddy has two ways to queue a job. "Any X1C" goes through the model-based
branch, which builds a sentence for every way the job could not start and puts
it on the row: `Busy: X1C-01`, `Waiting for filament: X1C-02 (needs PETG)`. The
same job pinned to a specific printer went through a branch that wrote nothing.

The reporter watched a pinned item sit at `pending` with `waiting_reason: null`
for fourteen minutes while its printer ran a print started from the printer's
own screen. Nothing in the UI or the API said why, and a queue that will not say
why it is waiting is indistinguishable from a queue that has stopped working.

Two rules hold everything here together:

- **Every exit writes.** Not one path out of the fixed-printer branch may leave
  the field as it found it, or a reason from an earlier pass outlives the
  condition that produced it.
- **Only what the user must act on makes a noise.** A printer that is merely
  printing resolves itself; the wording stays inside what `_is_busy_only` reads
  as silent. A plate nobody has confirmed does not resolve itself, so it is
  worded as itself and allowed through.
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
from backend.app.models.settings import Settings
from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
async def ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        db.add(
            Printer(
                id=1,
                name="X1C-01",
                serial_number="X1C0001",
                ip_address="10.0.0.1",
                access_code="x",
                model="X1C",
                is_active=True,
            )
        )
        await db.commit()

    try:
        yield SimpleNamespace(session_maker=session_maker)
    finally:
        await engine.dispose()


async def _add_item(ctx, *, printer_id=1, target_model=None, position=1, manual_start=False):
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
            manual_start=manual_start,
        )
        db.add(item)
        await db.commit()
        return item.id


async def _set(ctx, key, value):
    async with ctx.session_maker() as db:
        db.add(Settings(key=key, value=value))
        await db.commit()


async def _item(ctx, item_id):
    async with ctx.session_maker() as db:
        return (await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


async def _run(
    ctx,
    scheduler,
    *,
    idle=True,
    connected=True,
    awaiting_plate_clear=False,
    blocked=None,
    plugs=None,
    launched=None,
    waiting=None,
):
    """One check_queue pass with the printer in the state the test cares about."""
    launched = launched or MagicMock()
    patches = [
        patch("backend.app.services.print_scheduler.async_session", ctx.session_maker),
        patch("backend.app.core.database.async_session", ctx.session_maker),
        patch(
            "backend.app.services.print_scheduler.printer_manager.is_connected",
            MagicMock(return_value=connected),
        ),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
        patch(
            "backend.app.services.print_scheduler.printer_manager.is_awaiting_plate_clear",
            MagicMock(return_value=awaiting_plate_clear),
        ),
        patch(
            "backend.app.services.print_scheduler.ha_sensor_manager.blocked_printers",
            AsyncMock(return_value=blocked or {}),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_waiting",
            waiting or AsyncMock(),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_assigned",
            AsyncMock(),
        ),
        patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=idle)),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
        patch.object(scheduler, "_ensure_ams_mapping", AsyncMock(return_value=None)),
        patch.object(scheduler, "_block_on_filament_deficit", AsyncMock(return_value=False)),
        patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=plugs or [])),
        patch.object(scheduler, "_launch_uploads", launched),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        await scheduler.check_queue()
    return launched


class TestThePinnedItemSaysWhyItIsWaiting:
    """The reporter's four cases, each of which used to produce ``None``."""

    @pytest.mark.asyncio
    async def test_a_printer_midway_through_a_print(self, ctx):
        """The exact fourteen minutes from the report: a print started at the
        printer's own screen, and a pinned item with nothing to show for it."""
        item_id = await _add_item(ctx)

        launched = await _run(ctx, PrintScheduler(), idle=False)

        launched.assert_not_called()
        assert (await _item(ctx, item_id)).waiting_reason == "Busy: X1C-01"

    @pytest.mark.asyncio
    async def test_a_plate_nobody_has_confirmed(self, ctx):
        """The other half of the report. `_is_printer_idle` returns a plain
        False for both this and a running print, but they are not the same
        thing to the person looking at the queue: this one waits on them."""
        await _set(ctx, "require_plate_clear", "true")
        item_id = await _add_item(ctx)

        await _run(ctx, PrintScheduler(), idle=False, awaiting_plate_clear=True)

        assert (await _item(ctx, item_id)).waiting_reason == "Waiting for plate confirmation: X1C-01"

    @pytest.mark.asyncio
    async def test_an_item_queued_behind_another_on_the_same_printer(self, ctx):
        """The commonest case of all, and one the reporter never even reached:
        two items pinned to one printer. The second leaves the pass at the
        `busy_printers` test, several checks before anything that could have
        described the printer."""
        first = await _add_item(ctx, position=1)
        second = await _add_item(ctx, position=2)

        launched = await _run(ctx, PrintScheduler(), idle=True)

        assert launched.call_args[0][0] == [first]
        assert (await _item(ctx, second)).waiting_reason == "Busy: X1C-01"

    @pytest.mark.asyncio
    async def test_a_printer_that_is_off_with_nothing_to_switch_it_on(self, ctx):
        """Worded exactly as the model-based branch words it (#2786). This is
        the one entry here the user has to act on themselves: with no enabled
        Auto On plug, Bambuddy will never power this printer up for the queue."""
        item_id = await _add_item(ctx)

        await _run(ctx, PrintScheduler(), connected=False)

        assert (await _item(ctx, item_id)).waiting_reason == "Offline, no Auto On smart plug: X1C-01"

    @pytest.mark.asyncio
    async def test_a_drying_cycle_the_user_asked_to_block_the_queue(self, ctx):
        await _set(ctx, "queue_drying_block", "true")
        item_id = await _add_item(ctx)
        scheduler = PrintScheduler()
        scheduler._drying_in_progress[1] = True

        launched = await _run(ctx, scheduler, idle=True)

        launched.assert_not_called()
        assert (await _item(ctx, item_id)).waiting_reason == "Busy: X1C-01 (drying)"


class TestTheReasonNeverOutlivesTheThingItDescribes:
    """Every exit from the branch writes, so no pass can leave a stale reason."""

    @pytest.mark.asyncio
    async def test_it_clears_the_moment_the_item_goes_out(self, ctx):
        item_id = await _add_item(ctx)
        scheduler = PrintScheduler()
        await _run(ctx, scheduler, idle=False)
        assert (await _item(ctx, item_id)).waiting_reason == "Busy: X1C-01"

        launched = await _run(ctx, scheduler, idle=True)

        assert launched.call_args[0][0] == [item_id]
        assert (await _item(ctx, item_id)).waiting_reason is None

    @pytest.mark.asyncio
    async def test_a_busy_printer_replaces_a_lifted_interlock(self, ctx):
        """The guarantee the interlock used to buy by clearing the field up
        front, now bought by the exits all writing instead."""
        item_id = await _add_item(ctx)
        scheduler = PrintScheduler()
        await _run(ctx, scheduler, idle=False, blocked={1: "Enclosure Door"})
        assert (await _item(ctx, item_id)).waiting_reason == "Waiting on Enclosure Door"

        await _run(ctx, scheduler, idle=False)

        assert (await _item(ctx, item_id)).waiting_reason == "Busy: X1C-01"

    @pytest.mark.asyncio
    async def test_staging_an_item_drops_the_reason_it_was_carrying(self, ctx):
        """A staged item never reaches the fixed-printer branch again, so
        whatever it was carrying when the user staged it would otherwise stand
        for as long as the row lived."""
        item_id = await _add_item(ctx)
        scheduler = PrintScheduler()
        await _run(ctx, scheduler, idle=False)
        assert (await _item(ctx, item_id)).waiting_reason == "Busy: X1C-01"

        async with ctx.session_maker() as db:
            item = (await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()
            item.manual_start = True
            await db.commit()

        await _run(ctx, scheduler, idle=False)

        assert (await _item(ctx, item_id)).waiting_reason is None

    @pytest.mark.asyncio
    async def test_the_reason_is_written_once_not_once_per_tick(self, ctx):
        """Four passes over a printer that is printing throughout must not be
        four writes — the scheduler runs on a timer and this row is read by the
        UI on a poll."""
        item_id = await _add_item(ctx)
        scheduler = PrintScheduler()
        for _ in range(4):
            await _run(ctx, scheduler, idle=False)

        item = await _item(ctx, item_id)
        assert item.waiting_reason == "Busy: X1C-01"
        assert item.status == "pending"


class TestOnlyWhatTheUserMustActOnMakesANoise:
    """`_is_busy_only` decided this for the model-based branch; the reporter
    asked for the same restraint here, and the wording is what enforces it."""

    @pytest.mark.asyncio
    async def test_a_printer_that_is_simply_printing_stays_silent(self, ctx):
        await _add_item(ctx)
        waiting = AsyncMock()

        await _run(ctx, PrintScheduler(), idle=False, waiting=waiting)

        waiting.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_drying_cycle_stays_silent(self, ctx):
        await _set(ctx, "queue_drying_block", "true")
        await _add_item(ctx)
        scheduler = PrintScheduler()
        scheduler._drying_in_progress[1] = True
        waiting = AsyncMock()

        await _run(ctx, scheduler, idle=True, waiting=waiting)

        waiting.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_item_waiting_its_turn_stays_silent(self, ctx):
        await _add_item(ctx, position=1)
        await _add_item(ctx, position=2)
        waiting = AsyncMock()

        await _run(ctx, PrintScheduler(), idle=True, waiting=waiting)

        waiting.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unconfirmed_plate_is_worth_saying_once(self, ctx):
        await _set(ctx, "require_plate_clear", "true")
        await _add_item(ctx)
        waiting = AsyncMock()
        scheduler = PrintScheduler()

        for _ in range(3):
            await _run(ctx, scheduler, idle=False, awaiting_plate_clear=True, waiting=waiting)

        waiting.assert_called_once()
        assert waiting.call_args.kwargs["waiting_reason"] == "Waiting for plate confirmation: X1C-01"
        assert waiting.call_args.kwargs["target_model"] == "X1C"

    @pytest.mark.asyncio
    async def test_the_plate_notification_survives_the_print_that_came_before_it(self, ctx):
        """The sequence this branch actually produces, and the one a naive
        "was the field empty" transition test gets wrong.

        Nobody's queue goes straight from idle to an unconfirmed plate. It waits
        behind the print first, carrying "Busy: X1C-01" for however long that
        takes, and only then does the plate appear. Asking whether the item was
        waiting at all would call that no transition and stay silent through the
        single case on this list that needs a human.
        """
        await _set(ctx, "require_plate_clear", "true")
        item_id = await _add_item(ctx)
        scheduler = PrintScheduler()
        waiting = AsyncMock()

        await _run(ctx, scheduler, idle=False, waiting=waiting)
        assert (await _item(ctx, item_id)).waiting_reason == "Busy: X1C-01"
        waiting.assert_not_called()

        await _run(ctx, scheduler, idle=False, awaiting_plate_clear=True, waiting=waiting)

        assert (await _item(ctx, item_id)).waiting_reason == "Waiting for plate confirmation: X1C-01"
        waiting.assert_called_once()

    @pytest.mark.asyncio
    async def test_it_does_not_ask_twice_for_the_same_thing(self, ctx):
        """Busy, plate, plate, plate — one notification, not three."""
        await _set(ctx, "require_plate_clear", "true")
        await _add_item(ctx)
        scheduler = PrintScheduler()
        waiting = AsyncMock()

        await _run(ctx, scheduler, idle=False, waiting=waiting)
        for _ in range(3):
            await _run(ctx, scheduler, idle=False, awaiting_plate_clear=True, waiting=waiting)

        waiting.assert_called_once()

    @pytest.mark.asyncio
    async def test_an_interlock_has_never_notified_and_still_does_not(self, ctx):
        """#1148 built the sensor interlock as a hold that shows on the row, not
        as an alert. Routing it through the shared writer must not quietly turn
        every open enclosure door into a notification."""
        item_id = await _add_item(ctx)
        waiting = AsyncMock()

        await _run(ctx, PrintScheduler(), blocked={1: "Enclosure Door"}, waiting=waiting)

        assert (await _item(ctx, item_id)).waiting_reason == "Waiting on Enclosure Door"
        waiting.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_printer_nobody_can_switch_on_is_worth_saying(self, ctx):
        await _add_item(ctx)
        waiting = AsyncMock()

        await _run(ctx, PrintScheduler(), connected=False, waiting=waiting)

        waiting.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_provider_that_is_down_does_not_stop_the_queue(self, ctx):
        """The bug being fixed is a queue that cannot say why it is waiting. A
        queue that stops dispatching because a webhook timed out would be the
        worse one."""
        await _set(ctx, "require_plate_clear", "true")
        item_id = await _add_item(ctx)
        scheduler = PrintScheduler()

        await _run(
            ctx,
            scheduler,
            idle=False,
            awaiting_plate_clear=True,
            waiting=AsyncMock(side_effect=RuntimeError("provider down")),
        )
        assert (await _item(ctx, item_id)).waiting_reason == "Waiting for plate confirmation: X1C-01"

        launched = await _run(ctx, scheduler, idle=True)

        assert launched.call_args[0][0] == [item_id]


class TestWhatMustNotChange:
    @pytest.mark.asyncio
    async def test_an_interlock_still_reads_as_itself(self, ctx):
        """#1148's wording is what the user acts on — it names the sensor."""
        item_id = await _add_item(ctx)

        launched = await _run(ctx, PrintScheduler(), blocked={1: "Enclosure Door"})

        launched.assert_not_called()
        assert (await _item(ctx, item_id)).waiting_reason == "Waiting on Enclosure Door"

    @pytest.mark.asyncio
    async def test_an_idle_printer_still_dispatches(self, ctx):
        item_id = await _add_item(ctx)

        launched = await _run(ctx, PrintScheduler(), idle=True)

        assert launched.call_args[0][0] == [item_id]
        assert (await _item(ctx, item_id)).waiting_reason is None

    @pytest.mark.asyncio
    async def test_the_plate_gate_is_not_consulted_when_it_is_switched_off(self, ctx):
        """`require_plate_clear` defaults to off. With the gate off, an
        unconfirmed plate is not what is holding the queue, and saying so would
        send the user to a prompt that is not there."""
        item_id = await _add_item(ctx)

        await _run(ctx, PrintScheduler(), idle=False, awaiting_plate_clear=True)

        assert (await _item(ctx, item_id)).waiting_reason == "Busy: X1C-01"


class TestTheWordingStaysInsideWhatIsBusyOnlyUnderstands:
    """The silence is enforced by string prefixes, so pin them directly rather
    than only through the scheduler. A future rewording that drops the ``Busy:``
    prefix would turn every printing fleet into a notification source."""

    @pytest.mark.parametrize(
        "reason",
        [
            "Busy: X1C-01",
            "Busy: X1C-01 (drying)",
        ],
    )
    def test_these_are_silent(self, reason):
        assert PrintScheduler._is_busy_only(reason) is True

    @pytest.mark.parametrize(
        "reason",
        [
            "Waiting for plate confirmation: X1C-01",
            "Offline, no Auto On smart plug: X1C-01",
            "Offline: X1C-01 — the smart plug could not power it on",
            "Waiting on Enclosure Door",
        ],
    )
    def test_these_are_not(self, reason):
        assert PrintScheduler._is_busy_only(reason) is False


class TestPinnedHoldReason:
    """The one new branch, exercised without a scheduler pass around it."""

    def test_a_printing_printer_is_busy(self):
        with patch(
            "backend.app.services.print_scheduler.printer_manager.is_awaiting_plate_clear",
            MagicMock(return_value=False),
        ):
            assert PrintScheduler._pinned_hold_reason(1, "X1C-01", True) == "Busy: X1C-01"

    def test_an_unconfirmed_plate_is_named(self):
        with patch(
            "backend.app.services.print_scheduler.printer_manager.is_awaiting_plate_clear",
            MagicMock(return_value=True),
        ):
            assert PrintScheduler._pinned_hold_reason(1, "X1C-01", True) == "Waiting for plate confirmation: X1C-01"

    def test_the_gate_being_off_beats_the_flag(self):
        """The flag is persisted, so it survives the setting being turned off."""
        with patch(
            "backend.app.services.print_scheduler.printer_manager.is_awaiting_plate_clear",
            MagicMock(return_value=True),
        ):
            assert PrintScheduler._pinned_hold_reason(1, "X1C-01", False) == "Busy: X1C-01"

    def test_no_telemetry_yet_reads_as_busy(self):
        """A printer that reconnected a second ago has no status, which
        `_is_printer_idle` refuses. It resolves itself within a tick or two, and
        the model-based branch has always reported it as plain busy."""
        with patch(
            "backend.app.services.print_scheduler.printer_manager.is_awaiting_plate_clear",
            MagicMock(return_value=False),
        ):
            assert PrintScheduler._pinned_hold_reason(9, "X1C-01", True) == "Busy: X1C-01"
