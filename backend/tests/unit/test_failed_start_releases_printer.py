"""A queued print that never starts leaves no claim on the printer's next print.

Dispatching a queue item tells the rest of Bambuddy that the printer's next
print is the item's: the item's owner is recorded as the print's user, and an
archive print is registered as expected. When that print never starts, both
must go, or a print started on the printer afterwards is taken for it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.print_scheduler import DISPATCH_MAX_ATTEMPTS, PrintScheduler, scheduler
from backend.app.services.printer_manager import printer_manager

PRINTER = 42
ITEM = 1


@pytest.fixture
async def db_session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import backend.app.models  # noqa: F401
    from backend.app.core.database import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as db:
        db.add(PrintQueueItem(id=ITEM, printer_id=PRINTER, archive_id=99, status="printing"))
        await db.commit()
    try:
        yield session_maker
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean_state():
    from backend.app import main

    tables = ("_expected_prints", "_expected_print_creators", "_expected_print_registered_at")
    saved = {name: dict(getattr(main, name)) for name in tables}
    for name in tables:
        getattr(main, name).clear()
    printer_manager.clear_current_print_user(PRINTER)
    scheduler._sent_unstarted_print.clear()
    yield main
    printer_manager.clear_current_print_user(PRINTER)
    scheduler._sent_unstarted_print.clear()
    for name in tables:
        getattr(main, name).clear()
        getattr(main, name).update(saved[name])


def _sent(main, item_id: int = ITEM, *, sched=scheduler):
    """What a dispatch whose print command went out leaves behind."""
    main.register_expected_print(PRINTER, "part.3mf", 99, created_by_id=7)
    printer_manager.set_current_print_user(PRINTER, 7, "anna", queue_item_id=item_id)
    sched._sent_unstarted_print[item_id] = (PRINTER, "part.3mf", 99)


def _claimed(main) -> bool:
    return printer_manager.get_current_print_user(PRINTER) is not None or bool(main._expected_prints)


async def _watch(db_session, status):
    with (
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=status)),
        patch("backend.app.services.print_scheduler.printer_manager.get_client", MagicMock(return_value=MagicMock())),
        patch("backend.app.services.print_scheduler.async_session", db_session),
        patch("backend.app.core.database.async_session", db_session),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_failed", AsyncMock()),
    ):
        await PrintScheduler._watchdog_print_start(
            queue_item_id=ITEM,
            printer_id=PRINTER,
            pre_state="IDLE",
            pre_subtask_id="OLD",
            pre_gcode_file="/old.3mf",
            timeout=0.2,
            phase_b_timeout=0.2,
            poll_interval=0.05,
        )


async def _set_printing(db_session):
    async with db_session() as db:
        (await db.get(PrintQueueItem, ITEM)).status = "printing"
        await db.commit()


@pytest.mark.asyncio
async def test_giving_up_on_a_print_releases_the_printer(db_session, clean_state):
    for _ in range(DISPATCH_MAX_ATTEMPTS):
        await _set_printing(db_session)
        _sent(clean_state)
        await _watch(db_session, SimpleNamespace(state="IDLE", subtask_id="NEW", gcode_file="/new.3mf"))

    async with db_session() as db:
        assert (await db.get(PrintQueueItem, ITEM)).status == "failed"
    assert not _claimed(clean_state)


@pytest.mark.asyncio
async def test_a_rejected_print_command_releases_the_printer(db_session, clean_state):
    from backend.app.services.bambu_mqtt import HMS_MQTT_VERIFY_FAILED

    _sent(clean_state)
    rejected = SimpleNamespace(
        state="IDLE",
        subtask_id="OLD",
        gcode_file="/new.3mf",
        hms_errors=[SimpleNamespace(full_code=HMS_MQTT_VERIFY_FAILED)],
    )
    await _watch(db_session, rejected)

    assert not _claimed(clean_state)


@pytest.mark.asyncio
async def test_a_print_that_starts_keeps_its_claim(db_session, clean_state):
    _sent(clean_state)
    await _watch(db_session, SimpleNamespace(state="RUNNING", subtask_id="NEW", gcode_file="/new.3mf"))

    assert printer_manager.get_current_print_user(PRINTER)["user_id"] == 7
    assert clean_state._expected_prints
    assert ITEM not in scheduler._sent_unstarted_print


def test_a_print_that_started_late_keeps_its_claim(clean_state):
    _sent(clean_state)
    with patch(
        "backend.app.services.print_scheduler.printer_manager.get_status",
        MagicMock(return_value=SimpleNamespace(state="RUNNING")),
    ):
        scheduler._forget_unstarted_print(ITEM, unless_printing=True)

    assert _claimed(clean_state)


def test_another_item_keeps_the_printer(clean_state):
    _sent(clean_state)
    printer_manager.set_current_print_user(PRINTER, 8, "ben", queue_item_id=2)

    scheduler._forget_unstarted_print(ITEM, unless_printing=False)

    assert printer_manager.get_current_print_user(PRINTER)["user_id"] == 8


def test_cancelling_an_item_after_a_failed_start_releases_the_printer(clean_state):
    _sent(clean_state)

    scheduler.notify_dispatch_cancelled(ITEM)

    assert not _claimed(clean_state)


@pytest.mark.asyncio
async def test_a_dispatch_that_never_sends_releases_the_print_user(clean_state):
    sched = PrintScheduler()

    async def fake_start_print(db, item):
        # What `_start_print` has done by the time the cancel wins the race.
        printer_manager.set_current_print_user(PRINTER, 7, "anna", queue_item_id=597)
        sched._unconfirmed_print_user[597] = PRINTER

    db = MagicMock()
    db.get = AsyncMock(return_value=MagicMock(id=597, printer_id=PRINTER))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("backend.app.services.print_scheduler.async_session", return_value=ctx),
        patch.object(sched, "_claim_for_dispatch", AsyncMock(return_value=True)),
        patch.object(sched, "_start_print", side_effect=fake_start_print),
        patch.object(sched, "_clear_dispatch_claim", AsyncMock()),
    ):
        await sched._dispatch_one(597)

    assert printer_manager.get_current_print_user(PRINTER) is None
    assert sched._unconfirmed_print_user == {}


@pytest.mark.asyncio
async def test_a_dispatch_records_whose_print_it_claims(db_session, clean_state):
    from backend.app.models.printer import Printer
    from backend.app.models.user import User

    async with db_session() as db:
        db.add(User(id=7, username="anna", password_hash="x"))
        db.add(Printer(id=PRINTER, name="P", serial_number="00M09A000000042", ip_address="192.0.2.1", access_code="x"))
        item = await db.get(PrintQueueItem, ITEM)
        item.created_by_id = 7
        await db.commit()
        sched = PrintScheduler()
        await sched._propagate_owner_to_printer_manager(db, item)

    assert printer_manager.get_current_print_user(PRINTER)["queue_item_id"] == ITEM
    assert sched._unconfirmed_print_user == {ITEM: PRINTER}


@pytest.mark.asyncio
async def test_a_print_sent_back_for_another_try_vouches_only_for_its_own_file(db_session, clean_state):
    _sent(clean_state)
    await _watch(db_session, SimpleNamespace(state="IDLE", subtask_id="NEW", gcode_file="/new.3mf"))

    async with db_session() as db:
        assert (await db.get(PrintQueueItem, ITEM)).status == "pending"
    assert printer_manager.get_current_print_user(PRINTER) is None
    assert clean_state._expected_prints, "a late start of its own file must still match its archive"

    # Cancelling it then withdraws the rest.
    scheduler.notify_dispatch_cancelled(ITEM)
    assert not _claimed(clean_state)


@pytest.mark.asyncio
async def test_closing_a_stuck_item_releases_the_printer(db_session, clean_state):
    _sent(clean_state)
    sched = PrintScheduler()
    sched._sent_unstarted_print[ITEM] = scheduler._sent_unstarted_print[ITEM]
    sched._terminal_since[PRINTER] = -10_000.0
    with (
        patch("backend.app.services.print_scheduler.async_session", db_session),
        patch(
            "backend.app.services.print_scheduler.printer_manager.get_status",
            MagicMock(return_value=SimpleNamespace(state="IDLE", connected=True)),
        ),
    ):
        await sched._close_stranded_printing_items()

    async with db_session() as db:
        assert (await db.get(PrintQueueItem, ITEM)).status != "printing"
    assert not _claimed(clean_state)


def test_deleting_a_printer_drops_its_sent_prints(clean_state):
    sched = PrintScheduler()
    sched._sent_unstarted_print[ITEM] = (PRINTER, "part.3mf", 99)
    sched._sent_unstarted_print[2] = (7, None, None)

    sched.forget_printer(PRINTER)

    assert sched._sent_unstarted_print == {2: (7, None, None)}
