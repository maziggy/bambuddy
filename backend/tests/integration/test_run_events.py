"""Each print of an archive is announced as its run (archive redesign P3).

The print and archive events go out before the print's run exists, so they
cannot name it. ``run/started`` and ``run/completed`` do, once it does: the
run, its archive, printer and plate, and at the end its outcome and figures.
Notifications of a print's end carry the archive and run ids too.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app import main as main_module
from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import INCLUDE_OPEN_RUNS, PrintLogEntry
from backend.app.services.mqtt_relay import MQTTRelayService

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

T1 = datetime(2026, 10, 31, 9)


@pytest.fixture
def relay():
    relay = MagicMock(on_run_started=AsyncMock(), on_run_completed=AsyncMock())
    with patch.object(main_module, "mqtt_relay", relay):
        yield relay


@pytest.fixture
def sessions(test_engine):
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with patch.object(main_module, "async_session", maker):
        yield maker


async def _archive(db_session, printer_id: int) -> int:
    archive = PrintArchive(
        printer_id=printer_id,
        filename="part.3mf",
        print_name="part",
        file_path="archives/test/part.3mf",
        file_size=1,
        status="printing",
        started_at=T1,
        subtask_id="job-1",
    )
    db_session.add(archive)
    await db_session.commit()
    return archive.id


async def test_a_print_start_announces_its_run_once(db_session, printer_factory, relay, sessions):
    printer = await printer_factory(name="A")
    archive_id = await _archive(db_session, printer.id)

    await main_module._open_run_for(archive_id, printer.id, "job-1")
    # A restart resuming the print finds the run it opened.
    await main_module._open_run_for(archive_id, printer.id, "job-1")

    relay.on_run_started.assert_awaited_once()
    run, archive, serial, name = relay.on_run_started.await_args.args
    opened = await db_session.scalar(
        select(PrintLogEntry.id)
        .where(PrintLogEntry.archive_id == archive_id)
        .execution_options(**{INCLUDE_OPEN_RUNS: True})
    )
    assert (run.id, archive.id, serial, name) == (opened, archive_id, printer.serial_number, "A")


async def test_a_print_end_announces_its_run(db_session, printer_factory, relay, sessions):
    printer = await printer_factory(name="A")
    archive_id = await _archive(db_session, printer.id)
    run = PrintLogEntry(archive_id=archive_id, printer_id=printer.id, status="failed", started_at=T1)
    db_session.add(run)
    await db_session.commit()

    await main_module._publish_run_completed(run.id, printer.id)

    relay.on_run_completed.assert_awaited_once()
    published, archive, serial, _ = relay.on_run_completed.await_args.args
    assert (published.id, archive.id, serial) == (run.id, archive_id, printer.serial_number)


async def test_no_run_written_announces_nothing(relay, sessions):
    await main_module._publish_run_completed(None, 1)

    relay.on_run_completed.assert_not_awaited()


def _relay() -> MQTTRelayService:
    service = MQTTRelayService()
    service.enabled = True
    service.connected = True
    service.topic_prefix = "bambuddy"
    service._publish = MagicMock()
    return service


async def test_the_run_payloads():
    run = PrintLogEntry(
        id=7,
        archive_id=3,
        printer_id=2,
        plate_id=2,
        status="completed",
        started_at=T1,
        completed_at=T1.replace(hour=10),
        duration_seconds=3600,
        filament_used_grams=12.5,
        cost=0.4,
        print_name=None,
    )
    archive = PrintArchive(id=3, print_name="part", filename="part.3mf", file_path="", file_size=1)
    service = _relay()

    await service.on_run_started(run, archive, "SERIAL", "A")
    await service.on_run_completed(run, archive, "SERIAL", "A")

    (started_topic, started), (completed_topic, completed) = (c.args for c in service._publish.call_args_list)
    assert started_topic == "bambuddy/printers/SERIAL/run/started"
    assert {k: started[k] for k in ("run_id", "archive_id", "printer_id", "plate_id", "print_name")} == {
        "run_id": 7,
        "archive_id": 3,
        "printer_id": 2,
        "plate_id": 2,
        "print_name": "part",
    }
    assert completed_topic == "bambuddy/printers/SERIAL/run/completed"
    assert (completed["status"], completed["duration_seconds"], completed["filament_used_grams"]) == (
        "completed",
        3600,
        12.5,
    )


async def test_a_print_end_notification_names_its_archive_and_run(db_session):
    from backend.app.services.notification_service import NotificationService

    service = NotificationService()
    provider = MagicMock(name="provider")
    with (
        patch.object(service, "_get_providers_for_event", AsyncMock(return_value=[provider])),
        patch.object(service, "_build_message_from_template", AsyncMock(return_value=("t", "m"))),
        patch.object(service, "_send_to_providers", AsyncMock()) as send,
    ):
        await service.on_print_complete(
            1, "A", "completed", {"filename": "part.3mf"}, db_session, archive_data={"archive_id": 3, "run_id": 7}
        )

    variables = send.await_args.kwargs["variables"]
    assert (variables["archive_id"], variables["run_id"]) == ("3", "7")


async def test_a_run_replaced_by_the_next_start_is_announced_gone(db_session, printer_factory, relay, sessions):
    """Its print ended unseen; a new print opened the printer's next run."""
    relay.on_run_discarded = AsyncMock()
    printer = await printer_factory(name="A")
    archive_id = await _archive(db_session, printer.id)
    await main_module._open_run_for(archive_id, printer.id, "job-1")
    first = await db_session.scalar(
        select(PrintLogEntry.id)
        .where(PrintLogEntry.archive_id == archive_id)
        .execution_options(**{INCLUDE_OPEN_RUNS: True})
    )
    archive = await db_session.get(PrintArchive, archive_id)
    archive.started_at, archive.subtask_id = T1.replace(hour=11), "job-2"
    await db_session.commit()

    await main_module._open_run_for(archive_id, printer.id, "job-2")

    relay.on_run_discarded.assert_awaited_once()
    assert relay.on_run_discarded.await_args.args[0].id == first
    assert relay.on_run_started.await_count == 2


async def test_discarding_announces_each_run(db_session, printer_factory, relay, sessions):
    from backend.app.models.print_log import RUN_OPEN
    from backend.app.services.print_log import discard_open_runs

    relay.on_run_discarded = AsyncMock()
    printer = await printer_factory(name="A")
    archive_id = await _archive(db_session, printer.id)
    run = PrintLogEntry(archive_id=archive_id, printer_id=printer.id, status=RUN_OPEN, started_at=T1)
    db_session.add(run)
    await db_session.commit()
    run_id = run.id

    discarded = await discard_open_runs(db_session, printer.id)
    await db_session.commit()
    await main_module._announce_discarded_runs(discarded, printer.id)

    assert [r.id for r in discarded] == [run_id]
    relay.on_run_discarded.assert_awaited_once()
    assert relay.on_run_discarded.await_args.args[1] == printer.serial_number


async def test_times_are_utc_and_an_unmeasured_duration_says_so():
    run = PrintLogEntry(
        id=7,
        archive_id=3,
        printer_id=2,
        status="completed",
        started_at=T1,
        completed_at=T1.replace(hour=10),
        duration_seconds=0,
        duration_unmeasured=True,
    )
    service = _relay()

    await service.on_run_completed(run, None, "SERIAL", "A")

    payload = service._publish.call_args.args[1]
    assert (payload["started_at"], payload["completed_at"]) == (
        "2026-10-31T09:00:00+00:00",
        "2026-10-31T10:00:00+00:00",
    )
    assert payload["duration_unmeasured"] is True


async def test_the_kill_switch_notification_names_the_print(db_session, printer_factory, sessions):
    from backend.app.models.print_log import RUN_OPEN

    printer = await printer_factory(name="A")
    archive_id = await _archive(db_session, printer.id)
    run = PrintLogEntry(archive_id=archive_id, printer_id=printer.id, status=RUN_OPEN, started_at=T1)
    db_session.add(run)
    await db_session.commit()
    run_id = run.id

    with patch.object(main_module, "notification_service") as notify:
        notify.on_print_complete = AsyncMock()
        assert await main_module._send_kill_switch_provider_notification(printer.id, "A", {"filename": "part.3mf"})

    assert notify.on_print_complete.await_args.kwargs["archive_data"] == {"archive_id": archive_id, "run_id": run_id}


async def test_a_stop_while_the_printer_is_offline_announces_the_run_end(
    async_client, db_session, printer_factory, relay
):
    from backend.app.models.print_log import RUN_OPEN
    from backend.app.models.print_queue import PrintQueueItem

    printer = await printer_factory(name="A")
    archive_id = await _archive(db_session, printer.id)
    item = PrintQueueItem(printer_id=printer.id, archive_id=archive_id, status="printing", position=1)
    db_session.add(item)
    run = PrintLogEntry(archive_id=archive_id, printer_id=printer.id, status=RUN_OPEN, started_at=T1)
    db_session.add(run)
    await db_session.commit()
    run_id, item_id = run.id, item.id

    with (
        patch("backend.app.services.print_control.stop_print_by_user", return_value=False),
        patch("backend.app.api.routes.print_queue.ws_manager") as ws,
    ):
        ws.send_archive_updated = AsyncMock()
        response = await async_client.post(f"/api/v1/queue/{item_id}/stop")

    assert response.status_code == 200, response.text
    relay.on_run_completed.assert_awaited_once()
    assert relay.on_run_completed.await_args.args[0].id == run_id
