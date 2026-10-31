"""Every print of an archive keeps its own record, start to end (archive redesign P0).

The archive redesign moves what describes one print -- status, times, plate,
failure reason, charge -- from the archive onto the run. These tests drive
real print starts and completions against the database and pin what each run
records today, so every step of the move is checked against the same outcome.
They assert on the print log first; what the archive row shows is pinned
where the archive page reads it today, and changes there are meant to be
made here on purpose.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from backend.app import main as main_module
from backend.app.main import register_expected_print
from backend.app.models.archive import PrintArchive
from backend.app.models.finance import CostCenter, WalletTransaction
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.tests.integration.test_external_print_confirmation_1898 import (  # noqa: F401
    SUBTASK,
    _clear_print_state,
    _drive_print_start,
)
from backend.tests.integration.test_print_complete_end_to_end import _complete as _complete_concurrently

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

FILE = f"{SUBTASK}.gcode.3mf"


async def _complete(test_engine, printer_id: int, status: str, name: str, extra: dict | None = None):
    """``on_print_complete`` with its background work run one task at a time.

    The SQLite test database is one shared connection, so tasks writing at
    the same time fail each other ("cannot commit transaction - SQL
    statements in progress"), which a real database does not. The finish
    photo is left out of the turn-taking: the notification task waits for it.
    """
    from unittest.mock import patch

    from backend.app.core.tasks import spawn_background_task

    turn = asyncio.Lock()

    async def in_turn(coro):
        async with turn:
            return await coro

    def spawn(coro, name=None, **kwargs):
        if name != "background-finish-photo":
            coro = in_turn(coro)
        return spawn_background_task(coro, name=name, **kwargs)

    with patch("backend.app.main.spawn_background_task", side_effect=spawn):
        return await _complete_concurrently(test_engine, printer_id, status, name=name, extra=extra)


@pytest.fixture(autouse=True)
def _clean():
    from backend.app.services.printer_manager import printer_manager

    main_module._print_plate_ids.clear()
    yield
    main_module._print_plate_ids.clear()
    printer_manager._awaiting_plate_clear.clear()


@pytest.fixture
async def farm(db_session, printer_factory):
    """A printer, a user paying from a cost centre, and an archive of a
    multi-plate file printed before."""
    db_session.add_all(
        [Settings(key="capture_finish_photo", value="false"), Settings(key="billing_enabled", value="true")]
    )
    printer = await printer_factory(name="P")
    user = User(username="payer", password_hash="x", is_active=True)
    center = CostCenter(name="Lab", is_active=True, is_private=False)
    db_session.add_all([user, center])
    await db_session.flush()
    archive = PrintArchive(
        printer_id=printer.id,
        filename=FILE,
        file_path=f"archives/test/{FILE}",
        file_size=1,
        print_name=SUBTASK,
        status="completed",
        cost=5.0,
        created_by_id=user.id,
    )
    db_session.add(archive)
    await db_session.commit()
    return SimpleNamespace(printer=printer.id, user=user.id, center=center.id, archive=archive.id)


async def _run(test_engine, db_session, farm, *, plate: int, status: str, run_id: str) -> int:
    """One queued print of the archive's plate, from dispatch to its end.
    Returns the queue item's id."""
    item = PrintQueueItem(
        printer_id=farm.printer,
        archive_id=farm.archive,
        status="printing",
        position=1,
        plate_id=plate,
        created_by_id=farm.user,
        cost_center_id=farm.center,
        estimated_cost=5.0,
        billing_run_id=run_id,
    )
    db_session.add(item)
    # As the scheduler's dispatch writes it (_start_print).
    (await db_session.get(PrintArchive, farm.archive)).plate_id = plate
    await db_session.commit()
    item_id = item.id
    register_expected_print(farm.printer, FILE, farm.archive, plate_id=plate, cost_center_id=farm.center)

    await _drive_print_start(test_engine, SimpleNamespace(id=farm.printer), download_ok=False)
    await _complete(test_engine, farm.printer, status, name=SUBTASK)
    return item_id


async def _runs(db_session, archive_id: int) -> list[PrintLogEntry]:
    db_session.expire_all()
    return list(
        (
            await db_session.scalars(
                select(PrintLogEntry).where(PrintLogEntry.archive_id == archive_id).order_by(PrintLogEntry.id)
            )
        ).all()
    )


async def test_a_queued_print_of_an_archive_is_one_run_on_it(test_engine, db_session, farm):
    item = await _run(test_engine, db_session, farm, plate=1, status="completed", run_id="run-1")

    runs = await _runs(db_session, farm.archive)
    assert [(r.status, r.plate_id, r.queue_item_id, r.printer_id) for r in runs] == [
        ("completed", 1, item, farm.printer)
    ]
    assert runs[0].started_at is not None and runs[0].completed_at is not None
    assert runs[0].completed_at >= runs[0].started_at
    archive = await db_session.get(PrintArchive, farm.archive)
    assert (archive.status, archive.plate_id) == ("completed", 1)
    assert await db_session.scalar(select(func.count()).select_from(PrintArchive)) == 1


async def test_each_run_keeps_its_own_plate_outcome_and_times(test_engine, db_session, farm):
    """Plate 1 fails, then plate 2 prints: neither run takes the other's."""
    first = await _run(test_engine, db_session, farm, plate=1, status="failed", run_id="run-1")
    second = await _run(test_engine, db_session, farm, plate=2, status="completed", run_id="run-2")

    runs = await _runs(db_session, farm.archive)
    assert [(r.status, r.plate_id, r.queue_item_id) for r in runs] == [
        ("failed", 1, first),
        ("completed", 2, second),
    ]
    assert runs[1].started_at >= runs[0].completed_at, "the second run's start is its own"
    assert runs[1].failure_reason is None, "a completed run carries no reason of the failed one"
    # The archive page shows the latest run.
    archive = await db_session.get(PrintArchive, farm.archive)
    assert (archive.status, archive.plate_id, archive.failure_reason) == ("completed", 2, None)


async def test_each_run_is_charged_once_under_its_own_run_id(test_engine, db_session, farm):
    await _run(test_engine, db_session, farm, plate=1, status="completed", run_id="run-1")
    await _run(test_engine, db_session, farm, plate=2, status="completed", run_id="run-2")

    charges = (
        await db_session.scalars(
            select(WalletTransaction)
            .where(WalletTransaction.transaction_type == "print_charge")
            .order_by(WalletTransaction.id)
        )
    ).all()
    assert [(c.print_run_id, c.print_archive_id, c.cost_center_id) for c in charges] == [
        ("run-1", farm.archive, farm.center),
        ("run-2", farm.archive, farm.center),
    ]


async def test_a_print_started_on_the_printer_is_an_archive_with_one_run(test_engine, db_session, printer_factory):
    db_session.add(Settings(key="capture_finish_photo", value="false"))
    await db_session.commit()
    printer_id = (await printer_factory(name="P")).id

    await _drive_print_start(test_engine, SimpleNamespace(id=printer_id), download_ok=False)
    archives = (await db_session.scalars(select(PrintArchive))).all()
    assert [r.status for r in await _all_runs(db_session, archives[0].id)] == ["printing"], "opened at its start"
    await _complete(test_engine, printer_id, "completed", name=SUBTASK)

    archives = (await db_session.scalars(select(PrintArchive))).all()
    assert len(archives) == 1
    runs = await _runs(db_session, archives[0].id)
    assert [(r.status, r.queue_item_id, r.printer_id) for r in runs] == [("completed", None, printer_id)]


async def test_a_reported_end_after_an_offline_stop_replaces_the_run(test_engine, db_session, farm, async_client):
    """The stop could not reach the printer and logged the run as cancelled;
    the printer then reports how it really ended. Still one run."""
    from unittest.mock import AsyncMock, patch

    item = PrintQueueItem(
        printer_id=farm.printer,
        archive_id=farm.archive,
        status="printing",
        position=1,
        plate_id=2,
        created_by_id=farm.user,
        billing_run_id="run-1",
    )
    db_session.add(item)
    await db_session.commit()
    register_expected_print(farm.printer, FILE, farm.archive, plate_id=2)
    await _drive_print_start(test_engine, SimpleNamespace(id=farm.printer), download_ok=False)

    with (
        patch("backend.app.services.print_control.stop_print_by_user", return_value=False),
        patch("backend.app.api.routes.print_queue.ws_manager") as ws,
    ):
        ws.send_archive_updated = AsyncMock()
        assert (await async_client.post(f"/api/v1/queue/{item.id}/stop")).status_code == 200
    assert [(r.status, r.plate_id) for r in await _runs(db_session, farm.archive)] == [("cancelled", 2)]

    main_module._active_prints[(farm.printer, FILE)] = farm.archive
    await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    assert [(r.status, r.plate_id) for r in await _runs(db_session, farm.archive)] == [("completed", 2)]


async def test_each_run_gets_its_own_energy(test_engine, db_session, farm):
    """The plug's counter at each start and end: the archive card shows the
    first run's, each run its own."""
    from unittest.mock import AsyncMock, MagicMock, patch

    plug = MagicMock(id=1)
    plug.name = "Plug"
    counters = iter([10.5, 20.3])

    async def reading(candidates, read, db):
        return plug, {"total": next(counters)}

    async def started_at(kwh):
        archive = await db_session.get(PrintArchive, farm.archive)
        archive.energy_start_kwh = kwh
        await db_session.commit()

    with (
        patch("backend.app.main.energy_plug_candidates", AsyncMock(return_value=[plug])),
        patch("backend.app.main.select_energy_reading", side_effect=reading),
        patch("backend.app.services.energy_price.print_energy_cost", AsyncMock(return_value=0.1)),
        patch("backend.app.services.energy_price.current_price", AsyncMock(return_value=0.3)),
    ):
        for plate, kwh in ((1, 10.0), (2, 20.0)):
            item = PrintQueueItem(
                printer_id=farm.printer, archive_id=farm.archive, status="printing", position=1, plate_id=plate
            )
            db_session.add(item)
            await db_session.commit()
            register_expected_print(farm.printer, FILE, farm.archive, plate_id=plate)
            await _drive_print_start(test_engine, SimpleNamespace(id=farm.printer), download_ok=False)
            await started_at(kwh)
            await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    runs = await _runs(db_session, farm.archive)
    assert [(r.plate_id, r.energy_kwh) for r in runs] == [(1, 0.5), (2, pytest.approx(0.3))]
    archive = await db_session.get(PrintArchive, farm.archive)
    assert archive.energy_kwh == 0.5


async def test_each_run_keeps_its_own_verdict(test_engine, db_session, farm):
    """An answered outcome prompt belongs to its run; the next run asks again."""
    from backend.app.services.print_confirmation import apply_outcome_verdict

    archive = await db_session.get(PrintArchive, farm.archive)
    archive.confirm_requested = True
    await db_session.commit()

    await _run(test_engine, db_session, farm, plate=1, status="completed", run_id="run-1")
    archive = await db_session.get(PrintArchive, farm.archive)
    assert await apply_outcome_verdict(db_session, archive, "reject", source="dialog")
    await db_session.commit()
    await _run(test_engine, db_session, farm, plate=2, status="completed", run_id="run-2")

    runs = await _runs(db_session, farm.archive)
    assert [(r.plate_id, r.user_verdict) for r in runs] == [(1, "reject"), (2, None)]
    archive = await db_session.get(PrintArchive, farm.archive)
    assert archive.user_verdict is None, "the latest run is not judged yet"


async def _all_runs(db_session, archive_id: int) -> list[PrintLogEntry]:
    from backend.app.models.print_log import INCLUDE_OPEN_RUNS

    db_session.expire_all()
    return list(
        (
            await db_session.scalars(
                select(PrintLogEntry)
                .where(PrintLogEntry.archive_id == archive_id)
                .order_by(PrintLogEntry.id)
                .execution_options(**{INCLUDE_OPEN_RUNS: True})
            )
        ).all()
    )


async def _start_queued(test_engine, db_session, farm, *, plate: int, run_id: str) -> int:
    item = PrintQueueItem(
        printer_id=farm.printer,
        archive_id=farm.archive,
        status="printing",
        position=1,
        plate_id=plate,
        created_by_id=farm.user,
        cost_center_id=farm.center,
        billing_run_id=run_id,
    )
    db_session.add(item)
    (await db_session.get(PrintArchive, farm.archive)).plate_id = plate
    await db_session.commit()
    register_expected_print(farm.printer, FILE, farm.archive, plate_id=plate, cost_center_id=farm.center)
    await _drive_print_start(test_engine, SimpleNamespace(id=farm.printer), download_ok=False)
    return item.id


async def test_the_run_is_written_when_the_print_starts(test_engine, db_session, farm, async_client):
    """Open, it is in no statistic, log or count; its end fills in the same run."""
    item = await _start_queued(test_engine, db_session, farm, plate=2, run_id="run-1")

    [run] = await _all_runs(db_session, farm.archive)
    assert (run.status, run.plate_id, run.queue_item_id, run.billing_run_id, run.cost_center_id, run.printer_id) == (
        "printing",
        2,
        item,
        "run-1",
        farm.center,
        farm.printer,
    )
    archive = await db_session.get(PrintArchive, farm.archive)
    assert run.started_at == archive.started_at
    assert await _runs(db_session, farm.archive) == [], "an open run is not a print that happened"
    log = (await async_client.get("/api/v1/print-log/")).json()
    assert log["total"] == 0 and log["items"] == []
    stats = (await async_client.get("/api/v1/archives/stats")).json()
    assert stats["total_prints"] == 0

    await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    assert [(r.id, r.status, r.plate_id) for r in await _all_runs(db_session, farm.archive)] == [
        (run.id, "completed", 2)
    ]


async def test_a_print_start_seen_again_opens_no_second_run(test_engine, db_session, farm):
    """A restart resumes the running print: the run it opened stays the one."""
    await _start_queued(test_engine, db_session, farm, plate=1, run_id="run-1")
    archive = await db_session.get(PrintArchive, farm.archive)
    archive.subtask_id = "job-7"
    await db_session.commit()
    main_module._active_prints.clear()

    await _drive_print_start(
        test_engine,
        SimpleNamespace(id=farm.printer),
        download_ok=False,
        state_fields={"subtask_id": "job-7", "progress": 50.0},
    )

    assert await db_session.scalar(select(func.count()).select_from(PrintArchive)) == 1, "resumed, not archived anew"
    assert [r.status for r in await _all_runs(db_session, farm.archive)] == ["printing"]


async def test_the_runs_energy_start_is_its_own(test_engine, db_session, farm):
    from unittest.mock import AsyncMock, MagicMock, patch

    from backend.app.main import _record_energy_start

    await _start_queued(test_engine, db_session, farm, plate=1, run_id="run-1")
    plug = MagicMock(id=4)
    plug.name = "Plug"
    archive = await db_session.get(PrintArchive, farm.archive)
    with (
        patch("backend.app.main.energy_plug_candidates", AsyncMock(return_value=[plug])),
        patch("backend.app.main.select_energy_reading", AsyncMock(return_value=(plug, {"total": 12.5}))),
        patch("backend.app.services.energy_price.current_price", AsyncMock(return_value=0.3)),
    ):
        assert await _record_energy_start(archive, farm.printer, db_session)

    [run] = await _all_runs(db_session, farm.archive)
    assert (run.energy_start_kwh, run.energy_start_plug_id, run.energy_start_price) == (12.5, 4, 0.3)
    archive = await db_session.get(PrintArchive, farm.archive)
    assert run.energy_start_at == archive.energy_start_at


async def test_an_open_run_is_left_out_of_reads_updates_and_deletes(db_session, farm):
    from sqlalchemy import delete, update

    from backend.app.models.print_log import INCLUDE_OPEN_RUNS

    db_session.add_all(
        [
            PrintLogEntry(archive_id=farm.archive, status="completed"),
            PrintLogEntry(archive_id=farm.archive, status="printing"),
        ]
    )
    await db_session.commit()

    assert await db_session.scalar(select(func.count(PrintLogEntry.id))) == 1
    await db_session.execute(update(PrintLogEntry).values(notes="edited"))
    await db_session.execute(delete(PrintLogEntry))
    await db_session.commit()

    left = (
        await db_session.execute(
            select(PrintLogEntry.status, PrintLogEntry.notes).execution_options(**{INCLUDE_OPEN_RUNS: True})
        )
    ).all()
    assert left == [("printing", None)]


@pytest.mark.parametrize("by", ["subtask", "name"])
async def test_a_print_running_since_before_the_upgrade_gets_its_run(test_engine, db_session, farm, by):
    """Bambuddy restarts into the new version mid-print: the archive is
    printing and has no run; resuming the print opens it."""
    from datetime import datetime, timedelta

    archive = await db_session.get(PrintArchive, farm.archive)
    archive.status = "printing"
    started = datetime.utcnow().replace(microsecond=0) - timedelta(minutes=20)
    archive.started_at = started
    archive.subtask_id = "job-7"
    await db_session.commit()

    await _drive_print_start(
        test_engine,
        SimpleNamespace(id=farm.printer),
        download_ok=False,
        state_fields={"progress": 40.0},
        raw_data={"subtask_id": "job-7"} if by == "subtask" else None,
    )

    assert await db_session.scalar(select(func.count()).select_from(PrintArchive)) == 1, "resumed, not archived anew"
    runs = await _all_runs(db_session, farm.archive)
    assert [(r.status, r.subtask_id) for r in runs] == [("printing", "job-7")]
    assert runs[0].started_at == started


async def test_a_downloaded_print_opens_its_run(test_engine, db_session, printer_factory):
    """Started on the printer, its 3MF fetched: the auto-archive path."""
    printer_id = (await printer_factory(name="P")).id

    await _drive_print_start(test_engine, SimpleNamespace(id=printer_id), download_ok=True)

    archives = (await db_session.scalars(select(PrintArchive))).all()
    assert len(archives) == 1
    assert [r.status for r in await _all_runs(db_session, archives[0].id)] == ["printing"]


async def test_two_plates_on_two_printers_at_once_each_end_their_own_run(
    test_engine, db_session, farm, printer_factory
):
    """The second start moves the archive's start, printer and job id to its
    own. Each printer's end still finds its print, fills its own run with its
    own start and plate, and leaves nothing open."""
    other = (await printer_factory(name="Q")).id
    register_expected_print(farm.printer, FILE, farm.archive, plate_id=1, cost_center_id=farm.center)
    await _drive_print_start(test_engine, SimpleNamespace(id=farm.printer), download_ok=False)
    [first] = await _all_runs(db_session, farm.archive)
    first_start = first.started_at
    await asyncio.sleep(1.1)  # the second start is a later second
    register_expected_print(other, FILE, farm.archive, plate_id=2, cost_center_id=farm.center)
    await _drive_print_start(test_engine, SimpleNamespace(id=other), download_ok=False)

    await _complete(test_engine, farm.printer, "completed", name=SUBTASK)
    assert main_module._print_cost_center_ids == {(other, farm.archive): farm.center}
    # The other printer's print is still bound, for its own end.
    assert main_module._active_prints.get((other, FILE)) == farm.archive
    assert main_module._print_plate_ids == {(other, farm.archive): 2}, "the first end drains only its own"
    await _complete(test_engine, other, "completed", name=SUBTASK)
    assert main_module._print_plate_ids == {}

    runs = await _all_runs(db_session, farm.archive)
    assert [(r.printer_id, r.status, r.plate_id) for r in runs] == [
        (farm.printer, "completed", 1),
        (other, "completed", 2),
    ]
    assert runs[0].id == first.id and runs[0].started_at == first_start
    assert runs[1].started_at > first_start


async def test_a_run_is_dated_by_its_end(test_engine, db_session, farm):
    """The print log and statistics filter runs by when they were logged,
    which was always their end."""
    from datetime import datetime, timedelta

    await _start_queued(test_engine, db_session, farm, plate=1, run_id="run-1")
    [run] = await _all_runs(db_session, farm.archive)
    run.created_at = datetime(2026, 1, 1)  # opened long ago
    await db_session.commit()

    await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    [run] = await _all_runs(db_session, farm.archive)
    assert run.created_at > datetime.utcnow() - timedelta(minutes=5)


async def test_a_run_left_open_by_an_unseen_end_is_dropped_at_the_next_start(test_engine, db_session, farm):
    """A print whose end never reached its run (cancelled as stale at the next
    start): as before runs opened at the start, it leaves no run."""
    db_session.add(PrintLogEntry(archive_id=None, printer_id=farm.printer, status="printing"))
    await db_session.commit()

    await _start_queued(test_engine, db_session, farm, plate=1, run_id="run-1")

    runs = (
        await db_session.scalars(
            select(PrintLogEntry)
            .where(PrintLogEntry.printer_id == farm.printer)
            .execution_options(include_open_runs=True)
        )
    ).all()
    assert [(r.archive_id, r.status) for r in runs] == [(farm.archive, "printing")]


async def test_a_print_whose_archive_was_deleted_leaves_no_run(test_engine, db_session, farm):
    from sqlalchemy import update

    await _start_queued(test_engine, db_session, farm, plate=1, run_id="run-1")
    # Deleted while it printed (ON DELETE SET NULL takes the run's link).
    await db_session.execute(update(PrintLogEntry).values(archive_id=None).execution_options(include_open_runs=True))
    await db_session.delete(await db_session.get(PrintArchive, farm.archive))
    await db_session.commit()

    await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    left = (await db_session.scalars(select(PrintLogEntry).execution_options(include_open_runs=True))).all()
    assert left == []


async def test_deleting_an_archive_with_its_stats_takes_its_open_run(db_session, farm, async_client):
    db_session.add(PrintLogEntry(archive_id=farm.archive, printer_id=farm.printer, status="printing"))
    await db_session.commit()

    response = await async_client.delete(f"/api/v1/archives/{farm.archive}?purge_stats=true")
    assert response.status_code in (200, 204), response.text

    left = (await db_session.scalars(select(PrintLogEntry).execution_options(include_open_runs=True))).all()
    assert left == []


async def test_the_energy_goes_to_the_run_that_ended(test_engine, db_session, farm, printer_factory):
    """Its run, not the archive's newest: a run opened earlier ends after a
    later one of the archive was written."""
    from datetime import datetime
    from unittest.mock import AsyncMock, MagicMock, patch

    await _start_queued(test_engine, db_session, farm, plate=1, run_id="run-1")
    other = (await printer_factory(name="Q")).id
    later = PrintLogEntry(
        archive_id=farm.archive, printer_id=other, status="completed", started_at=datetime(2026, 10, 30, 9)
    )
    db_session.add(later)
    archive = await db_session.get(PrintArchive, farm.archive)
    archive.energy_start_kwh = 10.0
    await db_session.commit()
    plug = MagicMock(id=1)
    plug.name = "Plug"

    with (
        patch("backend.app.main.energy_plug_candidates", AsyncMock(return_value=[plug])),
        patch("backend.app.main.select_energy_reading", AsyncMock(return_value=(plug, {"total": 10.5}))),
        patch("backend.app.services.energy_price.print_energy_cost", AsyncMock(return_value=0.1)),
        patch("backend.app.services.energy_price.current_price", AsyncMock(return_value=0.3)),
    ):
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    runs = {r.printer_id: r.energy_kwh for r in await _all_runs(db_session, farm.archive)}
    assert runs == {farm.printer: 0.5, other: None}


async def test_a_reprint_after_an_unseen_end_opens_a_run_of_its_own(test_engine, db_session, farm):
    """The archive's earlier run on this printer is still open (its end never
    came); the new start is a new run, not that one carried on."""
    from datetime import datetime

    stale = PrintLogEntry(
        archive_id=farm.archive, printer_id=farm.printer, status="printing", started_at=datetime(2026, 10, 1, 8)
    )
    db_session.add(stale)
    await db_session.commit()

    await _start_queued(test_engine, db_session, farm, plate=2, run_id="run-2")

    [run] = await _all_runs(db_session, farm.archive)
    archive = await db_session.get(PrintArchive, farm.archive)
    assert run.started_at != datetime(2026, 10, 1, 8)
    assert (run.started_at, run.plate_id) == (archive.started_at, 2)


async def _two_printers_printing(test_engine, db_session, farm, printer_factory) -> int:
    """Plate 1 on the farm's printer, then plate 2 of the archive on another,
    both running. Returns the other printer's id."""
    other = (await printer_factory(name="Q")).id
    register_expected_print(farm.printer, FILE, farm.archive, plate_id=1)
    await _drive_print_start(test_engine, SimpleNamespace(id=farm.printer), download_ok=False)
    await asyncio.sleep(1.1)
    register_expected_print(other, FILE, farm.archive, plate_id=2)
    await _drive_print_start(test_engine, SimpleNamespace(id=other), download_ok=False)
    return other


async def test_after_a_restart_each_printers_end_finds_its_run(test_engine, db_session, farm, printer_factory):
    """A restart forgets which print runs where; the run open on the printer
    still says, though the archive now names the other printer."""
    from sqlalchemy import update

    other = await _two_printers_printing(test_engine, db_session, farm, printer_factory)
    await db_session.execute(
        update(PrintLogEntry)
        .where(PrintLogEntry.printer_id == farm.printer)
        .values(subtask_id="job-p")
        .execution_options(include_open_runs=True)
    )
    await db_session.commit()
    main_module._active_prints.clear()  # the restart
    archive = await db_session.get(PrintArchive, farm.archive)
    assert archive.printer_id == other

    await _complete(test_engine, farm.printer, "failed", name=SUBTASK, extra={"subtask_id": "job-p"})

    runs = {r.printer_id: r.status for r in await _all_runs(db_session, farm.archive)}
    assert runs == {farm.printer: "failed", other: "printing"}


async def test_the_running_print_on_each_printer_is_its_own(test_engine, db_session, farm, printer_factory):
    """The skip-objects, plate label and pause lookups ask which archive runs
    on a printer, by its job id; the archive row has the latest start's."""
    from unittest.mock import MagicMock

    from sqlalchemy import update

    from backend.app.main import running_print_archive

    other = await _two_printers_printing(test_engine, db_session, farm, printer_factory)
    await db_session.execute(
        update(PrintLogEntry)
        .where(PrintLogEntry.printer_id == farm.printer)
        .values(subtask_id="job-p")
        .execution_options(include_open_runs=True)
    )
    archive = await db_session.get(PrintArchive, farm.archive)
    archive.subtask_id = "job-q"
    await db_session.commit()
    main_module._active_prints.clear()

    found = await running_print_archive(
        db_session, farm.printer, MagicMock(subtask_id="job-p"), printing_only=True, by_name=False
    )

    assert found is not None and found.id == farm.archive
    assert archive.printer_id == other


async def test_the_kill_switch_knows_its_own_print_on_each_printer(test_engine, db_session, farm, printer_factory):
    """Bambuddy's print on the first printer is authorized by its own run,
    though the archive's job id and printer are the second start's."""
    from unittest.mock import MagicMock

    from sqlalchemy import update

    from backend.app.main import _is_bambuddy_authorized_print

    await _two_printers_printing(test_engine, db_session, farm, printer_factory)
    await db_session.execute(
        update(PrintLogEntry)
        .where(PrintLogEntry.printer_id == farm.printer)
        .values(subtask_id="job-p")
        .execution_options(include_open_runs=True)
    )
    archive = await db_session.get(PrintArchive, farm.archive)
    archive.subtask_id = "job-q"
    await db_session.commit()
    main_module._active_prints.clear()
    main_module._owned_print_archive_ids.clear()

    state = MagicMock(subtask_id="job-p", subtask_name=SUBTASK, gcode_file="")
    assert await _is_bambuddy_authorized_print(farm.printer, state, db_session) is True


async def test_a_restart_resumes_each_printers_print_on_its_archive(test_engine, db_session, farm, printer_factory):
    """Bambuddy comes back while both plates print: the first printer's print
    is resumed on the archive by its run, not archived anew, though the
    archive names the other printer and its job."""
    from sqlalchemy import update

    other = await _two_printers_printing(test_engine, db_session, farm, printer_factory)
    await db_session.execute(
        update(PrintLogEntry)
        .where(PrintLogEntry.printer_id == farm.printer)
        .values(subtask_id="job-p")
        .execution_options(include_open_runs=True)
    )
    archive = await db_session.get(PrintArchive, farm.archive)
    archive.subtask_id = "job-q"
    await db_session.commit()
    main_module._active_prints.clear()  # the restart

    await _drive_print_start(
        test_engine,
        SimpleNamespace(id=farm.printer),
        download_ok=False,
        state_fields={"progress": 40.0},
        raw_data={"subtask_id": "job-p"},
    )

    assert await db_session.scalar(select(func.count()).select_from(PrintArchive)) == 1, "resumed, not archived anew"
    assert main_module._active_prints.get((farm.printer, FILE)) == farm.archive
    runs = {r.printer_id: r.status for r in await _all_runs(db_session, farm.archive)}
    assert runs == {farm.printer: "printing", other: "printing"}


@pytest.mark.parametrize("jobs", [("job-z", "job-y"), ("0", "0")], ids=["other-job", "no-job-ids"])
async def test_a_late_end_of_an_earlier_job_leaves_the_running_print_alone(test_engine, db_session, farm, jobs):
    """The restart's reconcile closes a print that ended unseen, while the
    printer runs another one now: that one's open run is not the end's. A
    print started outside the cloud reports job "0", which tells nothing."""
    running_job, ending_job = jobs
    from sqlalchemy import update

    await _start_queued(test_engine, db_session, farm, plate=1, run_id="run-z")
    await db_session.execute(
        update(PrintLogEntry).values(subtask_id=running_job).execution_options(include_open_runs=True)
    )
    await db_session.commit()
    main_module._active_prints.clear()  # the restart

    await _complete(
        test_engine, farm.printer, "aborted", name="Other_part", extra={"subtask_id": ending_job, "_reconciled": True}
    )

    assert [(r.status, r.subtask_id) for r in await _all_runs(db_session, farm.archive)] == [("printing", running_job)]
    archive = await db_session.get(PrintArchive, farm.archive)
    assert archive.status == "printing"


async def test_the_archive_shows_its_latest_run(test_engine, db_session, farm, printer_factory):
    """Plate 1 ends while plate 2 still prints: the archive is left as plate
    1's end wrote it -- the reconcile, the next end and the timelapse still
    go by its printer, status and start. When plate 2 ends, the last of its
    prints, it shows plate 2's outcome."""
    other = await _two_printers_printing(test_engine, db_session, farm, printer_factory)

    await _complete(test_engine, farm.printer, "failed", name=SUBTASK)
    runs = {r.printer_id: r for r in await _all_runs(db_session, farm.archive)}
    archive = await db_session.get(PrintArchive, farm.archive)
    await db_session.refresh(archive)
    assert (archive.status, archive.printer_id, archive.started_at) == ("failed", other, runs[other].started_at)

    await _complete(test_engine, other, "completed", name=SUBTASK)
    runs = {r.printer_id: r for r in await _all_runs(db_session, farm.archive)}
    archive = await db_session.get(PrintArchive, farm.archive)
    await db_session.refresh(archive)
    assert (archive.status, archive.started_at, archive.completed_at, archive.plate_id, archive.failure_reason) == (
        "completed",
        runs[other].started_at,
        runs[other].completed_at,
        2,
        None,
    )


async def test_the_end_of_an_earlier_start_does_not_take_the_card(test_engine, db_session, farm, printer_factory):
    """Plate 2 started later and ended first; plate 1 ends after it. The
    archive shows the latest start, plate 2, not the last end."""
    other = await _two_printers_printing(test_engine, db_session, farm, printer_factory)

    await _complete(test_engine, other, "completed", name=SUBTASK)
    await _complete(test_engine, farm.printer, "failed", name=SUBTASK)

    archive = await db_session.get(PrintArchive, farm.archive)
    await db_session.refresh(archive)
    assert (archive.status, archive.plate_id) == ("completed", 2)


async def test_the_notification_of_an_end_has_its_runs_time(test_engine, db_session, farm, printer_factory):
    """Plate 1's end is notified with plate 1's duration, not the time since
    plate 2 started (the archive's start)."""
    await _two_printers_printing(test_engine, db_session, farm, printer_factory)

    notif = await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    archive_data = notif.on_print_complete.await_args.kwargs["archive_data"]
    assert archive_data["actual_time_seconds"] is not None and archive_data["actual_time_seconds"] >= 1


async def test_a_run_edited_in_the_print_log_is_shown_on_its_archive(test_engine, db_session, farm, async_client):
    await _run(test_engine, db_session, farm, plate=1, status="failed", run_id="run-1")
    [run] = await _runs(db_session, farm.archive)

    response = await async_client.patch(
        f"/api/v1/print-log/{run.id}", json={"status": "completed", "failure_reason": ""}
    )
    assert response.status_code == 200, response.text

    archive = await db_session.get(PrintArchive, farm.archive)
    await db_session.refresh(archive)
    assert (archive.status, archive.failure_reason) == ("completed", None)


async def test_an_edit_of_the_archive_lands_on_its_latest_started_run(db_session, farm, async_client):
    """The run the card shows is the latest start; with plates ended the
    other way round that is not the highest id."""
    from datetime import datetime

    later = PrintLogEntry(
        archive_id=farm.archive, printer_id=farm.printer, status="completed", started_at=datetime(2026, 10, 30, 10)
    )
    earlier = PrintLogEntry(
        archive_id=farm.archive, printer_id=farm.printer, status="completed", started_at=datetime(2026, 10, 30, 8)
    )
    db_session.add(later)
    await db_session.flush()
    db_session.add(earlier)
    await db_session.commit()

    response = await async_client.patch(
        f"/api/v1/archives/{farm.archive}", json={"status": "failed", "failure_reason": "spaghetti"}
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    rows = {r.started_at.hour: (r.status, r.failure_reason) for r in await _runs(db_session, farm.archive)}
    assert rows == {10: ("failed", "spaghetti"), 8: ("completed", None)}


async def test_the_latest_run_is_the_latest_start(db_session, farm):
    """Runs written at their end (before runs were opened at the start) are
    numbered by their end; the card's run is still the latest start."""
    from datetime import datetime

    from backend.app.services.print_log import mirror_latest_run

    db_session.add(
        PrintLogEntry(
            archive_id=farm.archive, printer_id=farm.printer, status="failed", started_at=datetime(2026, 10, 30, 10)
        )
    )
    await db_session.flush()
    db_session.add(
        PrintLogEntry(
            archive_id=farm.archive, printer_id=farm.printer, status="completed", started_at=datetime(2026, 10, 30, 8)
        )
    )
    await db_session.flush()

    await mirror_latest_run(db_session, farm.archive)

    archive = await db_session.get(PrintArchive, farm.archive)
    assert (archive.status, archive.started_at) == ("failed", datetime(2026, 10, 30, 10))


async def test_a_later_plates_end_leaves_no_printing_archive_for_its_reconnect(
    test_engine, db_session, farm, printer_factory
):
    """The later-started plate ends first. Its printer's reconnect looks for
    printing archives of that printer to close: the archive must not be one,
    or a second end of the print is made up."""
    from unittest.mock import MagicMock, patch

    from backend.app.main import reconcile_stale_active_prints

    other = await _two_printers_printing(test_engine, db_session, farm, printer_factory)
    await _complete(test_engine, other, "completed", name=SUBTASK)

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    state = MagicMock(connected=True, state="FINISH", subtask_id="", progress=100, layer_num=0)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.main.printer_manager.get_status", return_value=state),
        patch("backend.app.main.async_session", maker),
    ):
        assert await reconcile_stale_active_prints(other) == 0
    assert [r.printer_id for r in await _runs(db_session, farm.archive)] == [other]


async def _reconcile(test_engine, printer_id: int, state) -> int:
    """The reconnect's reconcile on ``printer_id`` against the test database,
    its synthesised ends run one background task at a time."""
    from unittest.mock import patch

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.app.main import reconcile_stale_active_prints

    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    payloads: list[tuple[int, dict]] = []

    async def capture(pid, payload):
        payloads.append((pid, payload))

    with (
        patch("backend.app.main.printer_manager.get_status", return_value=state),
        patch("backend.app.main.async_session", maker),
        patch("backend.app.main.on_print_complete", side_effect=capture),
    ):
        reconciled = await reconcile_stale_active_prints(printer_id)
    # The ends it synthesised, run as on_print_complete runs them.
    for pid, payload in payloads:
        await _complete(
            test_engine,
            pid,
            payload["status"],
            name=payload["subtask_name"],
            extra={k: v for k, v in payload.items() if k not in ("status", "subtask_name")},
        )
    return reconciled


async def test_the_reconnect_closes_its_printers_run_of_a_shared_archive(
    test_engine, db_session, farm, printer_factory
):
    """Plate 1's printer reconnects with its print over; the archive names
    the other printer, whose plate still prints. Plate 1's run is closed,
    plate 2's is left running."""
    from unittest.mock import MagicMock

    other = await _two_printers_printing(test_engine, db_session, farm, printer_factory)
    main_module._active_prints.clear()  # the restart
    finished = MagicMock(connected=True, state="FINISH", subtask_id="", progress=100, layer_num=0)

    assert await _reconcile(test_engine, farm.printer, finished) == 1

    runs = {r.printer_id: r.status for r in await _all_runs(db_session, farm.archive)}
    assert runs[other] == "printing"
    assert runs[farm.printer] != "printing"


async def test_the_reconnect_leaves_a_run_still_printing_alone(test_engine, db_session, farm, printer_factory):
    """The other printer reconnects mid-print, its own job on it: nothing to close."""
    from unittest.mock import MagicMock

    from sqlalchemy import update

    other = await _two_printers_printing(test_engine, db_session, farm, printer_factory)
    await db_session.execute(
        update(PrintLogEntry)
        .where(PrintLogEntry.printer_id == other)
        .values(subtask_id="job-q")
        .execution_options(include_open_runs=True)
    )
    await db_session.commit()
    running = MagicMock(
        connected=True, state="RUNNING", subtask_id="job-q", subtask_name=SUBTASK, progress=40, layer_num=3
    )

    assert await _reconcile(test_engine, other, running) == 0
    assert {r.printer_id: r.status for r in await _all_runs(db_session, farm.archive)}[other] == "printing"


async def test_a_real_end_after_an_offline_stop_is_one_run_on_a_shared_archive(
    test_engine, db_session, farm, printer_factory, async_client
):
    """Plate 1 is stopped while its printer is offline, after plate 2 started
    elsewhere (the archive has plate 2's start). Its printer then reports the
    real end: it replaces the stop, it is not a second run."""
    from unittest.mock import AsyncMock, patch

    item = PrintQueueItem(printer_id=farm.printer, archive_id=farm.archive, status="printing", position=1, plate_id=1)
    db_session.add(item)
    await db_session.commit()
    item_id = item.id
    other = await _two_printers_printing(test_engine, db_session, farm, printer_factory)
    own_start = {r.printer_id: r.started_at for r in await _all_runs(db_session, farm.archive)}[farm.printer]

    with (
        patch("backend.app.services.print_control.stop_print_by_user", return_value=False),
        patch("backend.app.api.routes.print_queue.ws_manager") as ws,
    ):
        ws.send_archive_updated = AsyncMock()
        assert (await async_client.post(f"/api/v1/queue/{item_id}/stop")).status_code == 200
    main_module._active_prints[(farm.printer, FILE)] = farm.archive

    await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    runs = [(r.printer_id, r.status) for r in await _all_runs(db_session, farm.archive)]
    assert sorted(runs) == sorted([(farm.printer, "completed"), (other, "printing")])
    ended = {r.printer_id: r for r in await _all_runs(db_session, farm.archive)}[farm.printer]
    assert ended.started_at == own_start, "timed from its own start, not the other plate's"


async def test_a_stale_archive_cancelled_at_the_next_start_keeps_other_printers_bound(farm):
    """The stale-cancel at a print start drops the archive's bindings on its
    own printer only: another printer printing a plate of it keeps its own."""
    import ast
    from pathlib import Path

    source = Path(main_module.__file__).read_text()
    tree = ast.parse(source)
    unbinds = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.ListComp) and "existing_archive.id" in ast.unparse(node)
    ]
    assert unbinds and all("printer_id" in comp for comp in unbinds), unbinds


async def test_the_reconnect_closes_a_finished_print_once(test_engine, db_session, farm, printer_factory):
    """The later plate's printer reconnects with it over. The archive names
    that printer and still says printing; its run is what is closed, once,
    not the archive a second time as if it had no run."""
    from unittest.mock import MagicMock

    other = await _two_printers_printing(test_engine, db_session, farm, printer_factory)
    main_module._active_prints.clear()
    finished = MagicMock(connected=True, state="FINISH", subtask_id="", progress=100, layer_num=0)

    assert await _reconcile(test_engine, other, finished) == 1


async def test_the_reconnect_judges_each_print_by_its_own_job(test_engine, db_session, farm, printer_factory):
    """Plate 1's printer reconnects running its job; the archive holds the
    other plate's job id. Plate 1 is not taken for a print that changed."""
    from unittest.mock import MagicMock

    from sqlalchemy import update

    await _two_printers_printing(test_engine, db_session, farm, printer_factory)
    await db_session.execute(
        update(PrintLogEntry)
        .where(PrintLogEntry.printer_id == farm.printer)
        .values(subtask_id="job-p")
        .execution_options(include_open_runs=True)
    )
    archive = await db_session.get(PrintArchive, farm.archive)
    archive.subtask_id = "job-q"
    await db_session.commit()
    running = MagicMock(
        connected=True, state="RUNNING", subtask_id="job-p", subtask_name=SUBTASK, progress=40, layer_num=3
    )

    assert await _reconcile(test_engine, farm.printer, running) == 0


async def test_an_end_does_not_take_over_an_earlier_stop_of_the_file(test_engine, db_session, farm):
    """Stopped from the queue while the printer was online (the same status and
    reason an offline stop logs), then printed again: the reprint's end
    writes its own run. Only a stop logged while the printer was offline is
    the printer's real end to replace."""
    from datetime import datetime

    db_session.add(
        PrintLogEntry(
            archive_id=farm.archive,
            printer_id=farm.printer,
            status="cancelled",
            failure_reason="userCancelled",
            started_at=datetime(2026, 10, 29, 8),
            completed_at=datetime(2026, 10, 29, 9),
        )
    )
    archive = await db_session.get(PrintArchive, farm.archive)
    archive.status = "printing"
    archive.started_at = datetime(2026, 10, 30, 8)  # printing since before runs were opened
    await db_session.commit()
    main_module._active_prints[(farm.printer, FILE)] = farm.archive

    await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    runs = sorted((r.started_at.day, r.status) for r in await _runs(db_session, farm.archive))
    assert runs == [(29, "cancelled"), (30, "completed")]


async def test_a_second_offline_stop_is_a_run_of_its_own(test_engine, db_session, farm, async_client):
    """An earlier offline stop of the file on this printer is not this stop."""
    from datetime import datetime
    from unittest.mock import AsyncMock, patch

    db_session.add(
        PrintLogEntry(
            archive_id=farm.archive,
            printer_id=farm.printer,
            status="cancelled",
            failure_reason="userCancelled",
            ended_offline=True,
            started_at=datetime(2026, 10, 29, 8),
        )
    )
    archive = await db_session.get(PrintArchive, farm.archive)
    archive.status = "printing"
    archive.started_at = datetime(2026, 10, 30, 8)
    item = PrintQueueItem(printer_id=farm.printer, archive_id=farm.archive, status="printing", position=1)
    db_session.add(item)
    await db_session.commit()
    item_id = item.id

    with (
        patch("backend.app.services.print_control.stop_print_by_user", return_value=False),
        patch("backend.app.api.routes.print_queue.ws_manager") as ws,
    ):
        ws.send_archive_updated = AsyncMock()
        assert (await async_client.post(f"/api/v1/queue/{item_id}/stop")).status_code == 200

    assert len(await _runs(db_session, farm.archive)) == 2


async def test_a_reconcile_after_the_real_end_closes_nothing(test_engine, db_session, farm, printer_factory):
    """The reconcile read the printer's runs while plate 1 still printed; the
    real end closed it before the synthesised one ran. That one ends nothing."""
    from unittest.mock import MagicMock, patch

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.app.main import reconcile_stale_active_prints

    await _two_printers_printing(test_engine, db_session, farm, printer_factory)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    payloads: list[dict] = []

    async def capture(pid, payload):
        payloads.append(payload)

    finished = MagicMock(connected=True, state="FINISH", subtask_id="", progress=100, layer_num=0)
    with (
        patch("backend.app.main.printer_manager.get_status", return_value=finished),
        patch("backend.app.main.async_session", maker),
        patch("backend.app.main.on_print_complete", side_effect=capture),
    ):
        await reconcile_stale_active_prints(farm.printer)
    await _complete(test_engine, farm.printer, "completed", name=SUBTASK)  # the real end, first
    [payload] = payloads

    await _complete(
        test_engine,
        farm.printer,
        payload["status"],
        name=payload["subtask_name"],
        extra={k: v for k, v in payload.items() if k not in ("status", "subtask_name")},
    )

    mine = [r.status for r in await _all_runs(db_session, farm.archive) if r.printer_id == farm.printer]
    assert mine == ["completed"]


async def test_a_stale_archive_cancelled_at_the_next_start_leaves_no_run_open(test_engine, db_session, farm):
    """A print whose end was never seen, its archive cancelled as stale when
    the next print starts: its run goes too, even when the new print's own
    run is not opened, or the next reconnect would close it a second time."""
    from datetime import datetime, timedelta
    from unittest.mock import AsyncMock, patch

    started = datetime.utcnow() - timedelta(hours=3)
    archive = await db_session.get(PrintArchive, farm.archive)
    archive.status = "printing"
    archive.started_at = started
    archive.created_at = started
    db_session.add(
        PrintLogEntry(archive_id=farm.archive, printer_id=farm.printer, status="printing", started_at=started)
    )
    await db_session.commit()

    with patch("backend.app.main._open_run_for", AsyncMock()):
        await _drive_print_start(
            test_engine, SimpleNamespace(id=farm.printer), download_ok=False, state_fields={"progress": 0.2}
        )

    db_session.expire_all()
    archive = await db_session.get(PrintArchive, farm.archive)
    assert archive.status == "cancelled"
    assert await _all_runs(db_session, farm.archive) == []


async def _charges(db_session) -> list[WalletTransaction]:
    db_session.expire_all()
    return list(
        (
            await db_session.scalars(
                select(WalletTransaction)
                .where(WalletTransaction.transaction_type == "print_charge")
                .order_by(WalletTransaction.id)
            )
        ).all()
    )


async def _print_on_the_printer(test_engine, printer_id: int, status: str = "completed") -> None:
    """A print of the archive started at the printer, no queue item, to its end."""
    await _drive_print_start(test_engine, SimpleNamespace(id=printer_id), download_ok=False)
    await _complete(test_engine, printer_id, status, name=SUBTASK)


async def test_each_print_of_an_archive_started_on_the_printer_is_charged(test_engine, db_session, farm):
    """A print without a queue item is charged under its run's own id, not
    under the id an earlier print of the archive was charged under."""
    await _run(test_engine, db_session, farm, plate=1, status="completed", run_id="run-1")
    # As the scheduler's dispatch left it.
    (await db_session.get(PrintArchive, farm.archive)).billing_run_id = "run-1"
    await db_session.commit()

    register_expected_print(farm.printer, FILE, farm.archive, plate_id=1, cost_center_id=farm.center)
    await _print_on_the_printer(test_engine, farm.printer)
    register_expected_print(farm.printer, FILE, farm.archive, plate_id=1, cost_center_id=farm.center)
    await _print_on_the_printer(test_engine, farm.printer)

    run_ids = [r.billing_run_id for r in await _runs(db_session, farm.archive)]
    charged = [c.print_run_id for c in await _charges(db_session)]
    assert len(set(charged)) == 3
    assert charged == run_ids and run_ids[0] == "run-1"


async def test_a_plates_end_leaves_the_budget_held_for_another_plate(test_engine, db_session, farm, printer_factory):
    """Plate 2 queued on another printer holds a reservation on the same
    archive: plate 1's end, without a queue item, does not free it."""
    from backend.app.models.finance import BudgetReservation

    other = (await printer_factory(name="Q")).id
    item = PrintQueueItem(
        printer_id=other,
        archive_id=farm.archive,
        status="printing",
        position=1,
        plate_id=2,
        created_by_id=farm.user,
        cost_center_id=farm.center,
        billing_run_id="run-2",
    )
    db_session.add(item)
    await db_session.flush()
    hold = BudgetReservation(
        cost_center_id=farm.center,
        amount=5.0,
        source_type="print_queue",
        source_id=item.id,
        print_archive_id=farm.archive,
    )
    db_session.add(hold)
    await db_session.commit()
    hold_id = hold.id

    register_expected_print(farm.printer, FILE, farm.archive, plate_id=1, cost_center_id=farm.center)
    await _print_on_the_printer(test_engine, farm.printer)

    db_session.expire_all()
    assert len(await _charges(db_session)) == 1
    assert (await db_session.get(BudgetReservation, hold_id)).status == "active"


async def test_a_run_is_charged_for_how_it_ended(db_session, farm):
    """Another plate of the archive ending at the same moment may have set
    the archive's status: the charge goes by the run's own end."""
    from backend.app.services.finance_billing import apply_print_charge_for_archive

    archive = await db_session.get(PrintArchive, farm.archive)
    archive.status = "failed"
    archive.extra_data = {"filament_grams_total": 100.0}
    run = PrintLogEntry(
        archive_id=farm.archive,
        printer_id=farm.printer,
        status="printing",
        billing_run_id="run-1",
        cost_center_id=farm.center,
    )
    db_session.add(run)
    await db_session.commit()

    assert await apply_print_charge_for_archive(
        db_session,
        farm.archive,
        charged_user_id=farm.user,
        run_entry_id=run.id,
        run_status="completed",
        filament_usage=(10.0, 100.0),
    )
    await db_session.commit()

    [charge] = await _charges(db_session)
    assert (charge.amount, charge.print_run_id, charge.cost_center_id) == (-5.0, "run-1", farm.center)


async def test_a_locked_database_does_not_leave_the_run_open(test_engine, db_session, farm):
    """The run write at a print's end is retried, or the next reconnect would
    close the print a second time."""
    from unittest.mock import patch

    from sqlalchemy.exc import OperationalError

    from backend.app.core.database import is_sqlite
    from backend.app.services import print_log

    if not is_sqlite():
        pytest.skip("SQLite's single writer only")
    real = print_log.write_log_entry
    calls = []

    async def locked_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError("UPDATE print_log_entries", {}, Exception("database is locked"))
        return await real(*args, **kwargs)

    register_expected_print(farm.printer, FILE, farm.archive, plate_id=1)
    await _drive_print_start(test_engine, SimpleNamespace(id=farm.printer), download_ok=False)
    with patch.object(print_log, "write_log_entry", side_effect=locked_once):
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    assert [r.status for r in await _all_runs(db_session, farm.archive)] == ["completed"]


async def test_a_run_without_a_billing_id_is_charged_once(db_session, farm):
    """A run opened before runs had their own billing id, of an archive with
    none: the id minted at its charge stays on the run, so its end seen again
    is not charged a second time."""
    from backend.app.services.finance_billing import apply_print_charge_for_archive

    run = PrintLogEntry(archive_id=farm.archive, printer_id=farm.printer, status="printing")
    db_session.add(run)
    await db_session.commit()

    for _ in range(2):
        await apply_print_charge_for_archive(
            db_session, farm.archive, charged_user_id=farm.user, run_entry_id=run.id, run_status="completed"
        )
        await db_session.commit()

    run_id = (await db_session.get(PrintLogEntry, run.id)).billing_run_id
    assert [c.print_run_id for c in await _charges(db_session)] == [run_id]


async def test_a_print_stopped_before_its_start_was_reported_frees_its_budget(test_engine, db_session, farm):
    """Stopped while the printer still prepared: its queue item is cancelled
    before the start report, so its run has no queue item. Its end still
    frees what the item reserved."""
    from backend.app.models.finance import BudgetReservation

    item = PrintQueueItem(
        printer_id=farm.printer,
        archive_id=farm.archive,
        status="cancelled",
        position=1,
        plate_id=1,
        created_by_id=farm.user,
        cost_center_id=farm.center,
        billing_run_id="run-1",
    )
    db_session.add(item)
    await db_session.flush()
    hold = BudgetReservation(
        cost_center_id=farm.center,
        amount=5.0,
        source_type="print_queue",
        source_id=item.id,
        print_archive_id=farm.archive,
    )
    db_session.add(hold)
    (await db_session.get(PrintArchive, farm.archive)).cost = 0.0
    await db_session.commit()
    hold_id = hold.id

    register_expected_print(farm.printer, FILE, farm.archive, plate_id=1, cost_center_id=farm.center)
    await _print_on_the_printer(test_engine, farm.printer, status="cancelled")

    db_session.expire_all()
    assert (await db_session.get(BudgetReservation, hold_id)).status == "released"


async def test_two_plates_on_two_printers_each_get_their_own_energy(test_engine, db_session, farm, printer_factory):
    """Each start's reading stays with its run: plate 2's start on another
    printer does not become plate 1's start."""
    from unittest.mock import AsyncMock, MagicMock, patch

    plugs = {}

    async def candidates(db, printer_id):
        plug = plugs.setdefault(printer_id, MagicMock(id=printer_id))
        plug.name = f"Plug {printer_id}"
        return [plug]

    counters = iter([10.0, 20.0, 10.5, 20.3])

    async def reading(found, read, db):
        return found[0], {"total": next(counters)}

    with (
        patch("backend.app.main.energy_plug_candidates", side_effect=candidates),
        patch("backend.app.main.select_energy_reading", side_effect=reading),
        patch("backend.app.services.energy_price.print_energy_cost", AsyncMock(return_value=0.1)),
        patch("backend.app.services.energy_price.current_price", AsyncMock(return_value=0.3)),
    ):
        # The start helper stubs the reading out; this test takes it.
        real_reading = patch("backend.app.main._record_energy_start", main_module._record_energy_start)
        other = (await printer_factory(name="Q")).id
        for printer_id, plate in ((farm.printer, 1), (other, 2)):
            register_expected_print(printer_id, FILE, farm.archive, plate_id=plate)
            await _drive_print_start(
                test_engine, SimpleNamespace(id=printer_id), download_ok=False, extra_patches=(real_reading,)
            )
            await asyncio.sleep(1.1)
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)
        await _complete(test_engine, other, "completed", name=SUBTASK)

    runs = {r.printer_id: r.energy_kwh for r in await _runs(db_session, farm.archive)}
    assert runs == {farm.printer: 0.5, other: pytest.approx(0.3)}


async def test_a_resume_takes_a_reading_when_the_archive_has_an_earlier_prints(db_session, farm):
    """The archive's reading is an earlier print's: the resumed run still
    needs its own. One taken for this run is copied onto it instead."""
    from datetime import datetime

    from backend.app.main import _run_needs_energy_start

    archive = await db_session.get(PrintArchive, farm.archive)
    archive.started_at = datetime(2026, 10, 30, 10)
    archive.energy_start_kwh = 5.0
    archive.energy_start_at = datetime(2026, 10, 29, 10)
    run = PrintLogEntry(
        archive_id=farm.archive, printer_id=farm.printer, status="printing", started_at=archive.started_at
    )
    db_session.add(run)
    await db_session.commit()
    run_id = run.id

    assert await _run_needs_energy_start(archive, farm.printer, db_session) is True

    archive.energy_start_at = datetime(2026, 10, 30, 10, 0, 5)
    await db_session.commit()
    assert await _run_needs_energy_start(archive, farm.printer, db_session) is False
    db_session.expire_all()
    assert (await db_session.get(PrintLogEntry, run_id)).energy_start_kwh == 5.0


async def test_an_end_whose_run_was_not_written_gives_no_other_run_its_energy(test_engine, db_session, farm):
    """The archive's newest finished run is an earlier print's."""
    from datetime import datetime
    from unittest.mock import AsyncMock, MagicMock, patch

    from backend.app.services import print_log

    earlier = PrintLogEntry(
        archive_id=farm.archive, printer_id=farm.printer, status="completed", started_at=datetime(2026, 10, 29, 9)
    )
    db_session.add(earlier)
    await db_session.commit()
    earlier_id = earlier.id
    await _start_queued(test_engine, db_session, farm, plate=1, run_id="run-1")
    archive = await db_session.get(PrintArchive, farm.archive)
    archive.energy_start_kwh = 10.0
    await db_session.commit()
    plug = MagicMock(id=1)
    plug.name = "Plug"

    with (
        patch("backend.app.main.energy_plug_candidates", AsyncMock(return_value=[plug])),
        patch("backend.app.main.select_energy_reading", AsyncMock(return_value=(plug, {"total": 10.5}))),
        patch("backend.app.services.energy_price.print_energy_cost", AsyncMock(return_value=0.1)),
        patch("backend.app.services.energy_price.current_price", AsyncMock(return_value=0.3)),
        patch.object(print_log, "write_log_entry", AsyncMock(side_effect=ValueError("broken"))),
    ):
        await _complete(test_engine, farm.printer, "completed", name=SUBTASK)

    db_session.expire_all()
    assert (await db_session.get(PrintLogEntry, earlier_id)).energy_kwh is None


async def test_a_run_without_its_own_reading_takes_no_later_plates():
    """Its reading was not copied at its start; plate 2 of the archive
    started on another printer since and took the archive's."""
    from datetime import datetime

    from backend.app.services.print_log import energy_start_of

    t1, t2 = datetime(2026, 10, 30, 9), datetime(2026, 10, 30, 10)
    run = PrintLogEntry(archive_id=1, printer_id=1, status="completed", started_at=t1)
    archive = SimpleNamespace(
        started_at=t2,
        energy_start_kwh=20.0,
        energy_start_at=datetime(2026, 10, 30, 10, 0, 5),
        energy_start_plug_id=2,
        energy_start_price=0.3,
    )
    assert energy_start_of(run, archive) is None
    # The archive's latest start is the run's own: its reading is.
    archive.started_at = t1
    archive.energy_start_at = datetime(2026, 10, 30, 9, 0, 5)
    assert energy_start_of(run, archive).kwh == 20.0


async def test_a_restart_keeps_the_first_printers_run_while_another_plate_prints(
    test_engine, db_session, farm, printer_factory
):
    """Resumed by its job after the other plate started: the same run, with
    its own start, billing id and energy reading, not one stamped with the
    other plate's start."""
    from sqlalchemy import update

    await _two_printers_printing(test_engine, db_session, farm, printer_factory)
    await db_session.execute(
        update(PrintLogEntry)
        .where(PrintLogEntry.printer_id == farm.printer)
        .values(subtask_id="job-p", energy_start_kwh=10.0)
        .execution_options(include_open_runs=True)
    )
    await db_session.commit()
    [before] = [r for r in await _all_runs(db_session, farm.archive) if r.printer_id == farm.printer]
    kept = (before.id, before.started_at, before.billing_run_id)
    main_module._active_prints.clear()  # the restart

    await _drive_print_start(
        test_engine,
        SimpleNamespace(id=farm.printer),
        download_ok=False,
        state_fields={"progress": 40.0},
        raw_data={"subtask_id": "job-p"},
    )

    [after] = [r for r in await _all_runs(db_session, farm.archive) if r.printer_id == farm.printer]
    assert (after.id, after.started_at, after.billing_run_id) == kept
    assert after.energy_start_kwh == 10.0


async def test_a_queued_prints_cost_centre_is_its_queue_items_none_included(db_session, farm):
    """The same file printing on another printer under a cost centre left it
    on the archive: a queued print without one is not charged to it."""
    from backend.app.services.finance_billing import apply_print_charge_for_archive

    archive = await db_session.get(PrintArchive, farm.archive)
    archive.cost_center_id = farm.center
    archive.extra_data = {"filament_grams_total": 100.0}
    item = PrintQueueItem(printer_id=farm.printer, archive_id=farm.archive, status="printing", position=1)
    db_session.add(item)
    await db_session.flush()
    run = PrintLogEntry(
        archive_id=farm.archive,
        printer_id=farm.printer,
        status="printing",
        billing_run_id="run-1",
        queue_item_id=item.id,
        cost_center_id=None,
    )
    db_session.add(run)
    await db_session.commit()

    assert await apply_print_charge_for_archive(
        db_session,
        farm.archive,
        charged_user_id=farm.user,
        run_entry_id=run.id,
        run_status="completed",
        filament_usage=(10.0, 100.0),
    )
    await db_session.commit()

    [charge] = await _charges(db_session)
    assert charge.cost_center_id is None


async def test_a_run_opened_for_a_queue_item_takes_its_cost_centre_none_included(db_session, farm):
    from backend.app.services.print_log import open_run

    archive = await db_session.get(PrintArchive, farm.archive)
    archive.cost_center_id = farm.center
    archive.started_at = datetime(2026, 10, 31, 9)
    db_session.add(PrintQueueItem(printer_id=farm.printer, archive_id=farm.archive, status="printing", position=1))
    await db_session.commit()

    run = await open_run(db_session, archive, printer_id=farm.printer)

    assert run is not None and run.cost_center_id is None
