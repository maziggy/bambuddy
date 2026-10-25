"""A print matched to a 'printing' archive by name only is a new print when
the subtask ids show it.

An archive can be left in 'printing' when its completion was missed. Started
again within two hours, the same file used to be folded into that archive:
no new history entry, no timelapse, no cost estimate. Two different subtask
ids are two prints, unless a queue job of this printer is running that
archive: a Bambuddy reprint mints a fresh id for the same row (#1807).
"""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.main import (
    _active_prints,
    _expected_print_creators,
    _expected_print_registered_at,
    _expected_prints,
    _print_ams_mappings,
)
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem

DISPATCH = "/data/Metadata/plate_1.gcode"
SUBTASK = "Bracket_plate_1"


@pytest.fixture(autouse=True)
def _clear_print_state():
    dicts = (
        _expected_prints,
        _expected_print_registered_at,
        _expected_print_creators,
        _print_ams_mappings,
        _active_prints,
    )
    for d in dicts:
        d.clear()
    yield
    for d in dicts:
        d.clear()


async def _start(test_engine, printer_id: int, subtask_id: str, last_dispatch: str | None = None) -> None:
    session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    state = MagicMock(
        current_project_url=f"ftp://{SUBTASK}.gcode.3mf",
        sdcard=True,
        sdcard_reported=True,
        subtask_id=subtask_id,
        progress=0.0,
    )
    patches = [
        patch("backend.app.main.async_session", session_maker),
        patch("backend.app.core.database.async_session", session_maker),
        patch("backend.app.main.download_file_async", new=AsyncMock(return_value=False)),
        patch("backend.app.main.download_file_try_paths_async", new=AsyncMock(return_value=None)),
        patch("backend.app.main.get_cached_3mf", return_value=None),
        patch("backend.app.main.cache_3mf_download"),
        patch("backend.app.main.peek_plate_index_in_3mf", return_value=None),
        patch("backend.app.services.bambu_ftp.list_files_async", new=AsyncMock(return_value=[])),
        patch("backend.app.main.ftps_handshake_blocked", return_value=False),
        patch("backend.app.main.get_ftp_retry_settings", new=AsyncMock(return_value=(False, 3, 2.0, 30))),
        patch("backend.app.main._record_energy_start", new_callable=AsyncMock),
        patch("backend.app.main._send_print_start_notification", new_callable=AsyncMock),
        patch("backend.app.main._maybe_start_layer_timelapse"),
        patch("backend.app.main._capture_timelapse_baseline_at_start", new_callable=AsyncMock),
        patch("backend.app.main._schedule_fallback_3mf_retry"),
        patch("backend.app.main._store_spoolman_print_data", new_callable=AsyncMock),
        patch("backend.app.main._load_objects_from_archive"),
        patch("backend.app.services.usage_tracker.on_print_start", new_callable=AsyncMock),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        notif = stack.enter_context(patch("backend.app.main.notification_service"))
        plug = stack.enter_context(patch("backend.app.main.smart_plug_manager"))
        ws = stack.enter_context(patch("backend.app.main.ws_manager"))
        relay = stack.enter_context(patch("backend.app.main.mqtt_relay"))
        pm = stack.enter_context(patch("backend.app.main.printer_manager"))
        notif.on_print_start = AsyncMock()
        plug.on_print_start = AsyncMock()
        ws.send_print_start = AsyncMock()
        ws.send_archive_created = AsyncMock()
        ws.send_archive_updated = AsyncMock()
        relay.on_print_start = AsyncMock()
        relay.on_archive_created = AsyncMock()
        pm.get_status = MagicMock(return_value=state)
        pm.get_printer = MagicMock(return_value=MagicMock(serial_number="TESTAP2"))
        pm.get_client = MagicMock(
            return_value=MagicMock(last_dispatch_subtask_id=last_dispatch) if last_dispatch else None
        )

        from backend.app.main import on_print_start

        await on_print_start(
            printer_id, {"filename": DISPATCH, "subtask_name": SUBTASK, "raw_data": {"subtask_id": subtask_id}}
        )


async def _stuck_archive(db_session, printer_id: int, subtask_id: str | None) -> int:
    archive = PrintArchive(
        printer_id=printer_id,
        filename=f"{SUBTASK}.gcode.3mf",
        file_path=f"archives/test/{SUBTASK}.gcode.3mf",
        file_size=2048,
        print_name=SUBTASK,
        status="printing",
        subtask_id=subtask_id,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    db_session.add(archive)
    await db_session.commit()
    return archive.id


async def _archives(db_session, printer_id: int) -> list[PrintArchive]:
    db_session.expire_all()
    result = await db_session.execute(
        select(PrintArchive).where(PrintArchive.printer_id == printer_id).order_by(PrintArchive.id)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
@pytest.mark.integration
async def test_another_subtask_id_is_a_new_print(test_engine, db_session, printer_factory):
    printer = await printer_factory()
    stuck_id = await _stuck_archive(db_session, printer.id, "111")
    _active_prints[(printer.id, f"{SUBTASK}.gcode.3mf")] = stuck_id
    _active_prints[(printer.id, "old/plate_1.gcode")] = stuck_id

    await _start(test_engine, printer.id, "222")

    rows = await _archives(db_session, printer.id)
    stuck = next(a for a in rows if a.id == stuck_id)
    assert (stuck.status, stuck.failure_reason) == ("cancelled", "noStatusUpdate")
    new = [a for a in rows if a.id != stuck_id]
    assert len(new) == 1 and new[0].subtask_id == "222"
    assert stuck_id not in _active_prints.values()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_queue_reprint_of_the_archive_resumes_it(test_engine, db_session, printer_factory):
    printer = await printer_factory()
    stuck_id = await _stuck_archive(db_session, printer.id, "111")
    db_session.add(PrintQueueItem(printer_id=printer.id, archive_id=stuck_id, status="printing"))
    await db_session.commit()

    await _start(test_engine, printer.id, "222")

    rows = await _archives(db_session, printer.id)
    assert [(a.id, a.status, a.subtask_id) for a in rows] == [(stuck_id, "printing", "222")]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_archive_without_a_subtask_id_is_resumed(test_engine, db_session, printer_factory):
    printer = await printer_factory()
    stuck_id = await _stuck_archive(db_session, printer.id, None)

    await _start(test_engine, printer.id, "222")

    rows = await _archives(db_session, printer.id)
    assert [(a.id, a.status, a.subtask_id) for a in rows] == [(stuck_id, "printing", "222")]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_cancelled_archive_with_the_same_subtask_id_is_not_reopened(test_engine, db_session, printer_factory):
    """Completion already charged and logged it; reopening would do it twice."""
    printer = await printer_factory()
    old_id = await _stuck_archive(db_session, printer.id, "333")
    old = await db_session.get(PrintArchive, old_id)
    old.status, old.failure_reason = "cancelled", "noStatusUpdate"
    await db_session.commit()

    await _start(test_engine, printer.id, "333")

    rows = await _archives(db_session, printer.id)
    assert next(a for a in rows if a.id == old_id).status == "cancelled"
    assert [a.subtask_id for a in rows if a.id != old_id] == ["333"]
    assert old_id not in _active_prints.values()


async def _queue_item(db_session, printer_id: int, archive_id: int, status: str = "printing") -> None:
    db_session.add(PrintQueueItem(printer_id=printer_id, archive_id=archive_id, status=status))
    await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_queue_job_left_printing_does_not_take_another_print(test_engine, db_session, printer_factory):
    """The archive's queue job stayed 'printing' with it, but Bambuddy's last
    dispatch to this printer was another job id: this print is not that run."""
    printer = await printer_factory()
    stuck_id = await _stuck_archive(db_session, printer.id, "111")
    await _queue_item(db_session, printer.id, stuck_id)

    await _start(test_engine, printer.id, "222", last_dispatch="111")

    rows = await _archives(db_session, printer.id)
    assert next(a for a in rows if a.id == stuck_id).status == "cancelled"
    assert [a.subtask_id for a in rows if a.id != stuck_id] == ["222"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_dispatched_job_id_resumes_without_a_queue_lookup(test_engine, db_session, printer_factory):
    printer = await printer_factory()
    stuck_id = await _stuck_archive(db_session, printer.id, "111")
    await _queue_item(db_session, printer.id, stuck_id)

    await _start(test_engine, printer.id, "222", last_dispatch="222")

    rows = await _archives(db_session, printer.id)
    assert [(a.id, a.status, a.subtask_id) for a in rows] == [(stuck_id, "printing", "222")]


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("elsewhere", ["other_printer", "finished"])
async def test_only_this_printers_running_queue_job_exempts(test_engine, db_session, printer_factory, elsewhere):
    printer = await printer_factory()
    stuck_id = await _stuck_archive(db_session, printer.id, "111")
    if elsewhere == "other_printer":
        other = await printer_factory()
        await _queue_item(db_session, other.id, stuck_id)
    else:
        await _queue_item(db_session, printer.id, stuck_id, status="completed")

    await _start(test_engine, printer.id, "222")

    rows = await _archives(db_session, printer.id)
    assert next(a for a in rows if a.id == stuck_id).status == "cancelled"
