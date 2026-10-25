"""A queued print's completion, end to end on the database.

The archive row, the print log entry, the queue item and the wallet charge are
what the rest of Bambuddy reads afterwards (archives page, statistics, project
totals, the cost centre's budget). Each was only pinned through mocks of the
function that writes it; here ``on_print_complete`` runs against real rows and
only the outside world (websocket, MQTT relay, notifications, plugs, the
printer's SD card) is mocked; the printer manager knows no printer.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app import main as main_module
from backend.app.models.archive import PrintArchive
from backend.app.models.finance import CostCenter, WalletTransaction
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.bambu_ftp import DeleteResult

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

RUN = "run-end-to-end"


@pytest.fixture(autouse=True)
def _clean():
    """The completion marks the printer as awaiting a plate clear on the real
    printer manager; the next test on this worker must not inherit that."""
    from backend.app.services.printer_manager import printer_manager

    main_module._active_prints.clear()
    yield
    main_module._active_prints.clear()
    printer_manager._awaiting_plate_clear.clear()


@pytest.fixture(autouse=True)
async def _no_finish_photo(db_session):
    """The finish photo would open the printer's camera."""
    db_session.add(Settings(key="capture_finish_photo", value="false"))
    await db_session.commit()


@pytest.fixture
async def queued_print(db_session, printer_factory):
    printer = await printer_factory(name="P")
    db_session.add(Settings(key="billing_enabled", value="true"))
    user = User(username="payer", password_hash="x", is_active=True)
    center = CostCenter(name="Lab", is_active=True, is_private=False)
    db_session.add_all([user, center])
    await db_session.flush()
    archive = PrintArchive(
        printer_id=printer.id,
        filename="part.3mf",
        print_name="part",
        file_path="archives/test/part.3mf",
        file_size=1,
        status="printing",
        started_at=datetime(2026, 10, 19, 8, 0),
        cost=5.0,
        created_by_id=user.id,
        billing_run_id=RUN,
    )
    db_session.add(archive)
    await db_session.flush()
    item = PrintQueueItem(
        printer_id=printer.id,
        archive_id=archive.id,
        status="printing",
        position=1,
        created_by_id=user.id,
        cost_center_id=center.id,
        estimated_cost=5.0,
        billing_run_id=RUN,
    )
    db_session.add(item)
    await db_session.commit()
    main_module._active_prints[(printer.id, "part.3mf")] = archive.id
    return {"printer": printer.id, "user": user.id, "center": center.id, "archive": archive.id, "item": item.id}


async def _complete(test_engine, printer_id: int, status: str, name: str = "part", extra: dict | None = None):
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    tasks_before = set(asyncio.all_tasks())
    with (
        patch("backend.app.main.async_session", maker),
        patch("backend.app.core.database.async_session", maker),
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.notification_service") as notif,
        patch("backend.app.main.smart_plug_manager") as plug,
        patch("backend.app.services.spoolman_tracking.async_session", maker),
        # The SD-card cleanup of the uploaded file
        patch("backend.app.services.bambu_ftp.delete_file_async", AsyncMock(return_value=DeleteResult.DELETED)),
        patch("backend.app.services.bambu_ftp.list_files_async", AsyncMock(return_value=[])),
    ):
        ws.send_print_complete = AsyncMock()
        ws.send_archive_updated = AsyncMock()
        ws.broadcast = AsyncMock()
        relay.on_print_complete = AsyncMock()
        notif.on_print_complete = AsyncMock()
        plug.on_print_complete = AsyncMock()
        await main_module.on_print_complete(
            printer_id,
            {
                "status": status,
                "filename": f"{name}.gcode",
                "subtask_name": name,
                "timelapse_was_active": False,
                **(extra or {}),
            },
        )
        spawned = asyncio.all_tasks() - tasks_before
        if spawned:
            _done, pending = await asyncio.wait(spawned, timeout=10)
            # Left running, they would carry on into the next test unpatched
            assert not pending, f"background work still running: {pending}"
    return notif


async def _rows(db_session, archive_id: int, item_id: int):
    db_session.expire_all()
    archive = await db_session.get(PrintArchive, archive_id)
    item = await db_session.get(PrintQueueItem, item_id)
    logs = (await db_session.scalars(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive_id))).all()
    charges = (
        await db_session.scalars(select(WalletTransaction).where(WalletTransaction.transaction_type == "print_charge"))
    ).all()
    return archive, item, logs, charges


async def test_a_completed_print(test_engine, db_session, queued_print):
    await _complete(test_engine, queued_print["printer"], "completed")

    archive, item, logs, charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
    assert archive.status == "completed"
    assert item.status == "completed"
    assert [(log.status, log.duration_unmeasured) for log in logs] == [("completed", None)]
    assert len(charges) == 1
    charge = charges[0]
    assert (charge.user_id, charge.cost_center_id, charge.print_queue_id, charge.print_run_id) == (
        queued_print["user"],
        queued_print["center"],
        queued_print["item"],
        RUN,
    )
    assert abs(charge.amount) == pytest.approx(5.0)


async def test_a_completion_reconciled_at_reconnect_has_no_actual_duration(test_engine, db_session, queued_print):
    """DB-C9: the printer was offline and the print ended at some unknown time;
    the reconnect closes it now, so now minus its start is not its duration."""
    notif = await _complete(test_engine, queued_print["printer"], "aborted", extra={"_reconciled": True})

    archive_data = notif.on_print_complete.await_args.kwargs["archive_data"]
    assert archive_data["actual_time_seconds"] is None


async def test_completion_stops_the_start_estimate_before_writing_the_cost(test_engine, db_session, queued_print):
    """COST-4: the background estimate must not outlive the real cost."""
    with patch("backend.app.main._settle_archive_cost_estimate", new_callable=AsyncMock) as settle:
        await _complete(test_engine, queued_print["printer"], "completed")

    settle.assert_awaited_once_with(queued_print["archive"])


async def test_a_failed_print(test_engine, db_session, queued_print):
    await _complete(test_engine, queued_print["printer"], "failed")

    archive, item, logs, _charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
    assert archive.status == "failed"
    assert item.status == "failed"
    assert [log.status for log in logs] == ["failed"]


async def test_a_second_completion_charges_nothing_more(test_engine, db_session, queued_print):
    """A duplicate completion callback (reconnect replay) is idempotent."""
    await _complete(test_engine, queued_print["printer"], "completed")
    main_module._active_prints[(queued_print["printer"], "part.3mf")] = queued_print["archive"]
    await _complete(test_engine, queued_print["printer"], "completed")

    _archive, _item, _logs, charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
    assert len(charges) == 1


async def test_a_duplicate_completion_logs_the_run_once(test_engine, db_session, queued_print):
    await _complete(test_engine, queued_print["printer"], "completed")
    main_module._active_prints[(queued_print["printer"], "part.3mf")] = queued_print["archive"]
    await _complete(test_engine, queued_print["printer"], "completed")

    _archive, _item, logs, _charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
    assert [(log.status, log.queue_item_id) for log in logs] == [("completed", queued_print["item"])]


async def test_a_completion_takes_its_own_reason(test_engine, db_session, queued_print):
    """A reprint that completes carries no reason of an earlier run."""
    archive = await db_session.get(PrintArchive, queued_print["archive"])
    archive.failure_reason = "spaghetti"
    await db_session.commit()

    await _complete(test_engine, queued_print["printer"], "completed")

    archive, _item, logs, _charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
    assert (archive.status, archive.failure_reason) == ("completed", None)
    assert [(log.status, log.failure_reason) for log in logs] == [("completed", None)]


class TestAStopWhileThePrinterIsOffline:
    """The stop could not reach the printer, so no completion may ever come (#2603)."""

    async def _stop(self, async_client, queued_print):
        with (
            patch("backend.app.services.print_control.stop_print_by_user", return_value=False),
            patch("backend.app.api.routes.print_queue.ws_manager") as ws,
        ):
            ws.send_archive_updated = AsyncMock()
            response = await async_client.post(f"/api/v1/queue/{queued_print['item']}/stop")
        assert response.status_code == 200, response.text
        return ws

    async def test_logs_the_run_as_cancelled_and_tells_the_archives_page(self, async_client, db_session, queued_print):
        ws = await self._stop(async_client, queued_print)

        archive, item, logs, _charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
        assert (archive.status, archive.failure_reason) == ("cancelled", "userCancelled")
        assert item.status == "cancelled"
        assert [(log.status, log.failure_reason, log.queue_item_id) for log in logs] == [
            ("cancelled", "userCancelled", queued_print["item"])
        ]
        assert ws.send_archive_updated.await_args.args[0] == {"id": queued_print["archive"], "status": "cancelled"}

    async def test_frees_the_budget_held_for_the_print(self, async_client, db_session, queued_print):
        """QS/I5: no completion will come to release it, which left the cost
        centre's budget reserved until Bambuddy restarted."""
        from backend.app.models.finance import BudgetReservation

        hold = BudgetReservation(
            cost_center_id=queued_print["center"], amount=5.0, source_type="print_queue", source_id=queued_print["item"]
        )
        db_session.add(hold)
        await db_session.commit()

        await self._stop(async_client, queued_print)

        await db_session.refresh(hold)
        assert hold.status == "released"

    async def test_a_stop_that_reached_the_printer_leaves_it_to_the_print_s_end(
        self, async_client, db_session, queued_print
    ):
        """The print's end charges the run and releases the hold itself."""
        from backend.app.models.finance import BudgetReservation

        hold = BudgetReservation(
            cost_center_id=queued_print["center"], amount=5.0, source_type="print_queue", source_id=queued_print["item"]
        )
        db_session.add(hold)
        await db_session.commit()

        with (
            patch("backend.app.services.print_control.stop_print_by_user", return_value=True),
            patch("backend.app.api.routes.print_queue.ws_manager"),
        ):
            await async_client.post(f"/api/v1/queue/{queued_print['item']}/stop")

        await db_session.refresh(hold)
        assert hold.status == "active"

    async def test_a_completion_that_arrives_after_all_replaces_it(
        self, async_client, test_engine, db_session, queued_print
    ):
        """The printer finished the print offline and reports it on reconnect."""
        await self._stop(async_client, queued_print)

        await _complete(test_engine, queued_print["printer"], "completed")

        archive, item, logs, _charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
        assert (archive.status, archive.failure_reason) == ("completed", None)
        assert item.status == "cancelled"
        assert [(log.status, log.failure_reason, log.queue_item_id) for log in logs] == [
            ("completed", None, queued_print["item"])
        ]


class TestACompletionNoArchiveWasBoundTo:
    """A print Bambuddy did not start is matched to its archive by name (L-W3)."""

    async def _printing(self, db_session, printer_id: int, name: str) -> int:
        archive = PrintArchive(
            printer_id=printer_id,
            filename=f"{name}.3mf",
            print_name=name,
            file_path=f"archives/test/{name}.3mf",
            file_size=1,
            status="printing",
        )
        db_session.add(archive)
        await db_session.commit()
        return archive.id

    @pytest.mark.parametrize(
        ("printed", "other"),
        [
            ("Clip", "Cable Clip"),  # a name inside another
            ("Clip_A", "ClipXA"),  # "_" is a LIKE wildcard
        ],
    )
    async def test_another_archive_whose_name_contains_it_is_left_alone(
        self, test_engine, db_session, printer_factory, printed, other
    ):
        printer = await printer_factory(name="P")
        other_id = await self._printing(db_session, printer.id, other)

        await _complete(test_engine, printer.id, "completed", name=printed)

        db_session.expire_all()
        assert (await db_session.get(PrintArchive, other_id)).status == "printing"

    async def test_its_own_archive_is_completed(self, test_engine, db_session, printer_factory):
        printer = await printer_factory(name="P")
        other_id = await self._printing(db_session, printer.id, "Cable Clip")
        own_id = await self._printing(db_session, printer.id, "Clip")

        await _complete(test_engine, printer.id, "completed", name="Clip")

        db_session.expire_all()
        assert (await db_session.get(PrintArchive, own_id)).status == "completed"
        assert (await db_session.get(PrintArchive, other_id)).status == "printing"

    async def test_the_printers_underscores_for_spaces_still_match(self, test_engine, db_session, printer_factory):
        printer = await printer_factory(name="P")
        own_id = await self._printing(db_session, printer.id, "Cable Clip")

        await _complete(test_engine, printer.id, "completed", name="Cable_Clip")

        db_session.expire_all()
        assert (await db_session.get(PrintArchive, own_id)).status == "completed"

    async def test_a_renamed_archive_is_found_by_its_file_name(self, test_engine, db_session, printer_factory):
        printer = await printer_factory(name="P")
        own_id = await self._printing(db_session, printer.id, "Clip")
        own = await db_session.get(PrintArchive, own_id)
        own.print_name = "Clip for the desk"
        await db_session.commit()

        await _complete(test_engine, printer.id, "completed", name="Clip")

        db_session.expire_all()
        assert (await db_session.get(PrintArchive, own_id)).status == "completed"

    async def test_a_print_started_on_the_printer_reports_its_file_name(self, test_engine, db_session, printer_factory):
        """The subtask then carries the extensions (#3009)."""
        printer = await printer_factory(name="P")
        own_id = await self._printing(db_session, printer.id, "Clip")
        own = await db_session.get(PrintArchive, own_id)
        own.filename = "Clip.gcode.3mf"
        await db_session.commit()

        await _complete(test_engine, printer.id, "completed", name="Clip.gcode.3mf")

        db_session.expire_all()
        assert (await db_session.get(PrintArchive, own_id)).status == "completed"


async def test_the_layer_timelapse_is_attached_for_the_run_that_ended(test_engine, db_session, queued_print, tmp_path):
    """The run is read before the stitch, which can take long enough for the
    archive to start printing again (TL-3)."""
    stitched = tmp_path / "stitched.mp4"
    stitched.write_bytes(b"layer video")
    attach = AsyncMock()

    with (
        patch("backend.app.services.layer_timelapse.on_print_complete", AsyncMock(return_value=stitched)),
        patch("backend.app.main._attach_layer_timelapse", attach),
    ):
        await _complete(test_engine, queued_print["printer"], "completed")

    attach.assert_awaited_once()
    archive_id, path, _plate, run_started_at = attach.await_args.args
    assert (archive_id, path, run_started_at) == (queued_print["archive"], stitched, datetime(2026, 10, 19, 8, 0))


async def test_the_registered_mapping_is_drained_when_the_printer_reported_one(test_engine, db_session, queued_print):
    """PAM/I3: left behind, the archive's next run (sent without a mapping)
    would charge its usage through this run's trays."""
    main_module._print_ams_mappings[queued_print["archive"]] = [4]
    try:
        await _complete(test_engine, queued_print["printer"], "completed", extra={"ams_mapping": [0]})

        assert queued_print["archive"] not in main_module._print_ams_mappings
    finally:
        main_module._print_ams_mappings.pop(queued_print["archive"], None)


async def test_a_hung_sd_cleanup_does_not_hold_up_the_print_s_end(test_engine, db_session, queued_print, monkeypatch):
    """QS/I9: the job is closed (and billed) by its end, not by the stranded-job
    closer, however long the printer takes to answer a delete."""
    import asyncio

    async def hangs(*_args, **_kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(main_module, "_SD_CLEANUP_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(main_module, "_cleanup_sd_card_after_print", hangs)

    await asyncio.wait_for(_complete(test_engine, queued_print["printer"], "completed"), timeout=15)

    _archive, item, _logs, charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
    assert item.status == "completed"
    assert len(charges) == 1


async def test_a_reconciled_abort_says_no_status_update(test_engine, db_session, queued_print):
    await _complete(test_engine, queued_print["printer"], "aborted", extra={"_reconciled": True})

    archive, item, logs, _charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
    assert (archive.status, archive.failure_reason) == ("aborted", "noStatusUpdate")
    assert [(log.status, log.duration_seconds, log.duration_unmeasured) for log in logs] == [("aborted", 0, True)]


async def test_a_print_that_finished_while_bambuddy_restarted_is_completed(test_engine, db_session, queued_print):
    """#3310: the reconnect closes it as completed, ended at its estimate."""
    ended = datetime(2026, 10, 19, 11, 0, tzinfo=timezone.utc)
    notif = await _complete(
        test_engine,
        queued_print["printer"],
        "completed",
        extra={"_reconciled": True, "_reconciled_completed_at": ended},
    )

    archive, item, logs, charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
    assert (archive.status, archive.failure_reason) == ("completed", None)
    assert archive.completed_at.replace(tzinfo=None) == datetime(2026, 10, 19, 11, 0)
    assert item.status == "completed"
    assert [(log.status, log.failure_reason, log.duration_seconds, log.duration_unmeasured) for log in logs] == [
        ("completed", None, 3 * 3600, True)
    ]
    assert len(charges) == 1
    assert notif.on_print_complete.await_args.kwargs["archive_data"]["actual_time_seconds"] == 3 * 3600


async def test_a_reconciled_completion_without_an_estimate_keeps_no_duration(test_engine, db_session, queued_print):
    await _complete(test_engine, queued_print["printer"], "completed", extra={"_reconciled": True})

    archive, _item, logs, _charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
    assert (archive.status, archive.failure_reason) == ("completed", None)
    assert [(log.status, log.duration_seconds, log.duration_unmeasured) for log in logs] == [("completed", 0, True)]


@pytest.mark.parametrize(
    ("hms_errors", "reason"),
    [
        ([{"code": "0x4057", "attr": 0x0300_0000, "module": 0x03, "severity": 1}], "layerShift"),
        ([], "noStatusUpdate"),
    ],
)
async def test_a_reconciled_failure_takes_the_printer_s_reason(
    test_engine, db_session, queued_print, hms_errors, reason
):
    await _complete(
        test_engine,
        queued_print["printer"],
        "failed",
        extra={"_reconciled": True, "hms_errors": hms_errors},
    )

    archive, _item, logs, _charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
    assert (archive.status, archive.failure_reason) == ("failed", reason)
    assert [(log.status, log.duration_seconds, log.duration_unmeasured) for log in logs] == [("failed", 0, True)]


async def test_the_reconcile_reads_the_plate_the_queue_job_printed(test_engine, db_session, queued_print):
    """#3310: a reprint of plate 3 reuses an archive that says plate 1."""
    from types import SimpleNamespace

    archive = await db_session.get(PrintArchive, queued_print["archive"])
    archive.subtask_id, archive.plate_id = "JOB1", 1
    item = await db_session.get(PrintQueueItem, queued_print["item"])
    item.plate_id = 3
    # An earlier run of the archive, of another plate.
    db_session.add(
        PrintQueueItem(
            printer_id=queued_print["printer"],
            archive_id=archive.id,
            status="completed",
            position=2,
            plate_id=5,
        )
    )
    await db_session.commit()
    state = SimpleNamespace(
        state="FINISH",
        subtask_id="JOB1",
        subtask_name="part",
        connected=True,
        raw_data={},
        progress=100,
        layer_num=0,
        hms_errors=[],
    )
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.main.async_session", maker),
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.main._reconciled_end_time", return_value=None) as end_time,
        patch("backend.app.main.on_print_complete", new=AsyncMock()) as complete,
    ):
        pm.get_status.return_value = state
        assert await main_module.reconcile_stale_active_prints(queued_print["printer"]) == 1

    assert end_time.call_args.args[2] == 3
    assert complete.call_args.args[1]["status"] == "completed"
