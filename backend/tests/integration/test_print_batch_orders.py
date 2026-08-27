"""Integration tests for batch orders — per-plate targets and staged dispatch (#342).

The behaviour these lock down that the pre-#342 batch could not express: a
failed or cancelled run does not satisfy a target, so the order keeps saying it
owes a print until one actually completes.
"""

from datetime import datetime

import pytest
from httpx import AsyncClient


@pytest.fixture
async def printer_factory(db_session):
    _counter = [0]

    async def _create_printer(**kwargs):
        from backend.app.models.printer import Printer

        _counter[0] += 1
        counter = _counter[0]
        defaults = {
            "name": f"Batch Printer {counter}",
            "ip_address": f"192.168.9.{100 + counter}",
            "serial_number": f"BATCHSERIAL{counter:04d}",
            "access_code": "12345678",
            "model": "X1C",
        }
        defaults.update(kwargs)
        printer = Printer(**defaults)
        db_session.add(printer)
        await db_session.commit()
        await db_session.refresh(printer)
        return printer

    return _create_printer


@pytest.fixture
async def archive_factory(db_session):
    _counter = [0]

    async def _create_archive(**kwargs):
        from backend.app.models.archive import PrintArchive

        _counter[0] += 1
        counter = _counter[0]
        defaults = {
            "filename": f"batch_order_{counter}.3mf",
            "print_name": f"Batch Order {counter}",
            "file_path": f"/tmp/batch_order_{counter}.3mf",  # nosec B108
            "file_size": 2048,
            "content_hash": f"batchhash{counter:08d}",
            "status": "completed",
        }
        defaults.update(kwargs)
        archive = PrintArchive(**defaults)
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)
        return archive

    return _create_archive


async def _create_order(async_client: AsyncClient, archive_id: int, plates: list[dict], **extra):
    payload = {"name": "Test Order", "archive_id": archive_id, "plates": plates}
    payload.update(extra)
    response = await async_client.post("/api/v1/queue/batches", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _queue_item(async_client: AsyncClient, printer_id: int, archive_id: int, batch_id: int, **extra):
    payload = {"printer_id": printer_id, "archive_id": archive_id, "batch_id": batch_id}
    payload.update(extra)
    response = await async_client.post("/api/v1/queue/", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _set_status(db_session, item_id: int, status: str):
    from backend.app.models.print_queue import PrintQueueItem

    item = await db_session.get(PrintQueueItem, item_id)
    item.status = status
    await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.integration
class TestBatchOrderTargets:
    async def test_order_reports_per_plate_targets(self, async_client, archive_factory):
        """The reporter's own example: plate 1 once, plate 2 twice, plate 3 three times."""
        archive = await archive_factory()
        order = await _create_order(
            async_client,
            archive.id,
            [
                {"plate_id": 1, "plate_name": "Base", "quantity_target": 1, "sort_order": 0},
                {"plate_id": 2, "quantity_target": 2, "sort_order": 1},
                {"plate_id": 3, "quantity_target": 3, "sort_order": 2},
            ],
        )

        assert order["has_targets"] is True
        assert order["target_count"] == 6
        assert order["remaining_count"] == 6
        assert [p["plate_id"] for p in order["plates"]] == [1, 2, 3]
        assert [p["quantity_target"] for p in order["plates"]] == [1, 2, 3]
        assert order["plates"][0]["plate_name"] == "Base"
        # Nothing dispatched yet, so nothing has been consumed.
        assert all(p["dispatched"] == 0 for p in order["plates"])

    async def test_zero_target_plate_is_allowed(self, async_client, archive_factory):
        """ "Plate 3 not required" keeps its row so it can be raised later."""
        archive = await archive_factory()
        order = await _create_order(
            async_client,
            archive.id,
            [{"plate_id": 1, "quantity_target": 2}, {"plate_id": 2, "quantity_target": 0}],
        )
        assert order["target_count"] == 2
        plate_two = next(p for p in order["plates"] if p["plate_id"] == 2)
        assert plate_two["quantity_target"] == 0
        assert plate_two["remaining"] == 0

    async def test_order_requesting_nothing_is_rejected(self, async_client, archive_factory):
        archive = await archive_factory()
        response = await async_client.post(
            "/api/v1/queue/batches",
            json={
                "name": "Empty",
                "archive_id": archive.id,
                "plates": [{"plate_id": 1, "quantity_target": 0}],
            },
        )
        assert response.status_code == 400
        assert "at least one print" in response.json()["detail"]

    async def test_duplicate_plate_is_rejected(self, async_client, archive_factory):
        archive = await archive_factory()
        response = await async_client.post(
            "/api/v1/queue/batches",
            json={
                "name": "Dupes",
                "archive_id": archive.id,
                "plates": [{"plate_id": 1, "quantity_target": 1}, {"plate_id": 1, "quantity_target": 2}],
            },
        )
        assert response.status_code == 400
        assert "Duplicate plate" in response.json()["detail"]

    async def test_duplicate_whole_file_plate_is_rejected(self, async_client, archive_factory):
        """NULL plate_id slips past the DB unique constraint, so the route must catch it."""
        archive = await archive_factory()
        response = await async_client.post(
            "/api/v1/queue/batches",
            json={
                "name": "Dupes",
                "archive_id": archive.id,
                "plates": [{"quantity_target": 1}, {"quantity_target": 2}],
            },
        )
        assert response.status_code == 400
        assert "whole file" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
class TestBatchOrderProgress:
    async def test_failed_run_leaves_the_work_owed(self, async_client, printer_factory, archive_factory, db_session):
        """The whole point of storing targets: a burned print is still owed."""
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 2}])

        first = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        second = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)

        await _set_status(db_session, first["id"], "completed")
        await _set_status(db_session, second["id"], "failed")

        response = await async_client.get(f"/api/v1/queue/batches/{order['id']}")
        result = response.json()
        assert result["completed_count"] == 1
        assert result["failed_count"] == 1
        # One completed, one burned — the order still owes a print.
        assert result["remaining_count"] == 1
        assert result["status"] == "active"

    async def test_cancelled_run_also_leaves_the_work_owed(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 1}])
        item = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await _set_status(db_session, item["id"], "cancelled")

        result = (await async_client.get(f"/api/v1/queue/batches/{order['id']}")).json()
        assert result["cancelled_count"] == 1
        assert result["remaining_count"] == 1

    async def test_pending_and_printing_consume_the_target(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        """In-flight work must not be re-dispatched — that would double-print."""
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 2}])
        first = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await _set_status(db_session, first["id"], "printing")

        result = (await async_client.get(f"/api/v1/queue/batches/{order['id']}")).json()
        assert result["printing_count"] == 1
        assert result["pending_count"] == 1
        assert result["remaining_count"] == 0

    async def test_legacy_batch_without_targets_owes_nothing(self, async_client, printer_factory, archive_factory):
        """Batches created before #342 keep working and report has_targets=false."""
        printer = await printer_factory()
        archive = await archive_factory()
        response = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "quantity": 3}
        )
        batch_id = response.json()["batch_id"]

        result = (await async_client.get(f"/api/v1/queue/batches/{batch_id}")).json()
        assert result["has_targets"] is False
        assert result["pending_count"] == 3
        assert result["remaining_count"] == 0
        assert result["target_count"] == 3


@pytest.mark.asyncio
@pytest.mark.integration
class TestBatchOrderCompletion:
    async def test_status_flips_to_completed_when_targets_met(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 1}])
        item = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await _set_status(db_session, item["id"], "completed")

        # Reading the order re-evaluates it; the PATCH path and the print
        # completion hook do the same.
        patched = await async_client.patch(f"/api/v1/queue/batches/{order['id']}", json={})
        assert patched.status_code == 200
        assert patched.json()["status"] == "completed"
        assert patched.json()["completed_at"] is not None

    async def test_raising_a_target_reopens_a_completed_order(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 1}])
        item = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await _set_status(db_session, item["id"], "completed")
        assert (await async_client.patch(f"/api/v1/queue/batches/{order['id']}", json={})).json()[
            "status"
        ] == "completed"

        reopened = await async_client.patch(
            f"/api/v1/queue/batches/{order['id']}",
            json={"plates": [{"plate_id": 1, "quantity_target": 3}]},
        )
        assert reopened.status_code == 200
        assert reopened.json()["status"] == "active"
        assert reopened.json()["completed_at"] is None
        assert reopened.json()["remaining_count"] == 2

    async def test_legacy_batch_with_everything_cancelled_reads_as_cancelled(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        """Cancelling every item of a grouping finishes it, but produces nothing.

        "Completed" would be a lie — its derived target is zero — and leaving it
        active would strand it on the Batches tab forever. Cancelled is what it
        is, and is what the batch-level Cancel action would have set.
        """
        printer = await printer_factory()
        archive = await archive_factory()
        response = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "quantity": 2}
        )
        batch_id = response.json()["batch_id"]

        from sqlalchemy import select

        from backend.app.models.print_queue import PrintQueueItem

        items = (
            (await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.batch_id == batch_id)))
            .scalars()
            .all()
        )
        for item in items:
            item.status = "cancelled"
        await db_session.commit()

        patched = await async_client.patch(f"/api/v1/queue/batches/{batch_id}", json={})
        assert patched.status_code == 200
        assert patched.json()["status"] == "cancelled"
        assert patched.json()["completed_at"] is None

    async def test_an_order_with_every_run_cancelled_still_owes_them(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        """The grouping rule must not leak into orders.

        An order states its intent independently of its runs, so cancelling
        them all leaves it owing the work and offering to re-queue.
        """
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 2}])
        first = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        second = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await _set_status(db_session, first["id"], "cancelled")
        await _set_status(db_session, second["id"], "cancelled")

        patched = (await async_client.patch(f"/api/v1/queue/batches/{order['id']}", json={})).json()
        assert patched["status"] == "active"
        assert patched["remaining_count"] == 2

    async def test_cancelled_order_is_never_resurrected(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 1}])
        item = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await async_client.delete(f"/api/v1/queue/batches/{order['id']}")
        await _set_status(db_session, item["id"], "completed")

        result = (await async_client.get(f"/api/v1/queue/batches/{order['id']}")).json()
        assert result["status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.integration
class TestBatchBacklog:
    """The Batches tab must not open on months of stale rows.

    `completed` only became a reachable status with #342, so every batch
    created since the feature shipped is still `active` however long ago its
    last run finished.
    """

    async def test_startup_backfill_closes_finished_batches(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        from backend.app.services.print_batch import backfill_batch_statuses

        printer = await printer_factory()
        archive = await archive_factory()
        response = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "quantity": 2}
        )
        batch_id = response.json()["batch_id"]

        from sqlalchemy import select

        from backend.app.models.print_queue import PrintQueueItem

        items = (
            (await db_session.execute(select(PrintQueueItem).where(PrintQueueItem.batch_id == batch_id)))
            .scalars()
            .all()
        )
        for item in items:
            item.status = "completed"
        await db_session.commit()

        assert (await async_client.get(f"/api/v1/queue/batches/{batch_id}")).json()["status"] == "active"

        changed = await backfill_batch_statuses(db_session)
        assert changed >= 1
        assert (await async_client.get(f"/api/v1/queue/batches/{batch_id}")).json()["status"] == "completed"

    async def test_backfill_leaves_in_flight_batches_alone(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        from backend.app.services.print_batch import backfill_batch_statuses

        printer = await printer_factory()
        archive = await archive_factory()
        response = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "quantity": 2}
        )
        batch_id = response.json()["batch_id"]

        await backfill_batch_statuses(db_session)
        assert (await async_client.get(f"/api/v1/queue/batches/{batch_id}")).json()["status"] == "active"

    async def test_backfill_is_idempotent(self, async_client, printer_factory, archive_factory, db_session):
        from backend.app.services.print_batch import backfill_batch_statuses

        printer = await printer_factory()
        archive = await archive_factory()
        response = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "quantity": 1}
        )
        item_id = response.json()["id"]
        await _set_status(db_session, item_id, "completed")
        order = await _create_order(async_client, archive.id, [{"plate_id": 9, "quantity_target": 1}])

        first = await backfill_batch_statuses(db_session)
        second = await backfill_batch_statuses(db_session)
        assert second == 0, "a second pass must have nothing left to change"
        assert first >= 0
        # The untouched order owes work and stays active across both passes.
        assert (await async_client.get(f"/api/v1/queue/batches/{order['id']}")).json()["status"] == "active"

    async def test_empty_shell_batches_are_not_listed(self, async_client, db_session):
        """A grouping whose items went with their archive has nothing to show."""
        from backend.app.models.print_batch import PrintBatch

        shell = PrintBatch(name="Orphaned grouping", quantity=1, status="active")
        db_session.add(shell)
        await db_session.commit()
        await db_session.refresh(shell)

        listed = (await async_client.get("/api/v1/queue/batches")).json()
        assert all(b["id"] != shell.id for b in listed)
        # Still addressable directly — only the list hides it.
        assert (await async_client.get(f"/api/v1/queue/batches/{shell.id}")).status_code == 200

    async def test_a_new_order_is_listed_before_its_first_dispatch(self, async_client, archive_factory):
        """Targets are enough to be worth showing — that is what it owes."""
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 3}])

        listed = (await async_client.get("/api/v1/queue/batches")).json()
        assert any(b["id"] == order["id"] for b in listed)


@pytest.mark.asyncio
@pytest.mark.integration
class TestBatchOrderDispatch:
    async def test_dispatch_clones_the_print_configuration(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 2, "quantity_target": 3}])
        source = await _queue_item(
            async_client,
            printer.id,
            archive.id,
            order["id"],
            plate_id=2,
            timelapse=True,
            use_ams=False,
            bed_levelling="off",
            ams_mapping=[3, -1],
        )

        response = await async_client.post(f"/api/v1/queue/batches/{order['id']}/dispatch", json={})
        assert response.status_code == 200
        assert response.json()["remaining_count"] == 0
        assert response.json()["pending_count"] == 3

        from sqlalchemy import select

        from backend.app.models.print_queue import PrintQueueItem

        rows = (
            (
                await db_session.execute(
                    select(PrintQueueItem).where(PrintQueueItem.batch_id == order["id"]).order_by(PrintQueueItem.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 3
        clones = [r for r in rows if r.id != source["id"]]
        for clone in clones:
            assert clone.plate_id == 2
            assert clone.printer_id == printer.id
            assert clone.timelapse is True
            assert clone.use_ams is False
            assert clone.bed_levelling == "off"
            assert clone.ams_mapping == "[3, -1]"
            assert clone.status == "pending"
            # Lifecycle state is not copied.
            assert clone.started_at is None
            assert clone.completed_at is None
            assert clone.dispatch_attempts == 0
            # Never replayed onto a clone: it would delete the source file out
            # from under the rest of the order.
            assert clone.cleanup_library_after_dispatch is False

    async def test_clones_land_in_their_own_printer_queue(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        """Positions are per-printer sequences — a global MAX would scramble them."""
        printer_a = await printer_factory()
        printer_b = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(
            async_client,
            archive.id,
            [{"plate_id": 1, "quantity_target": 3}, {"plate_id": 2, "quantity_target": 2}],
        )
        # Pad printer B's queue so a global MAX would push plate 1's clones
        # past the end of printer A's much shorter queue.
        for _ in range(5):
            await async_client.post("/api/v1/queue/", json={"printer_id": printer_b.id, "archive_id": archive.id})
        await _queue_item(async_client, printer_a.id, archive.id, order["id"], plate_id=1)
        await _queue_item(async_client, printer_b.id, archive.id, order["id"], plate_id=2)

        response = await async_client.post(f"/api/v1/queue/batches/{order['id']}/dispatch", json={})
        assert response.status_code == 200

        from sqlalchemy import select

        from backend.app.models.print_queue import PrintQueueItem

        for printer in (printer_a, printer_b):
            rows = (
                (
                    await db_session.execute(
                        select(PrintQueueItem)
                        .where(PrintQueueItem.printer_id == printer.id)
                        .where(PrintQueueItem.status == "pending")
                    )
                )
                .scalars()
                .all()
            )
            positions = sorted(r.position for r in rows)
            assert len(positions) == len(set(positions)), f"duplicate positions on printer {printer.id}"
            assert positions == list(range(1, len(rows) + 1)), f"gap in printer {printer.id} queue"

    async def test_clone_differs_from_its_source_only_in_lifecycle_state(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        """Guard for future columns.

        A clone must carry every *setting* of the item it copies and reset
        every piece of *lifecycle* state. Adding a new setting column to
        PrintQueueItem without listing it in CLONED_SETTING_COLUMNS would make
        the second run of a plate behave differently from the first — silently,
        and on real hardware. This fails when that happens.
        """
        from sqlalchemy import inspect, select

        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 2}])
        source_id = (
            await _queue_item(
                async_client,
                printer.id,
                archive.id,
                order["id"],
                plate_id=1,
                timelapse=True,
                use_ams=False,
                bed_levelling="off",
                flow_cali="on",
                vibration_cali=False,
                layer_inspect=True,
                gcode_injection=True,
                auto_off_after=True,
                require_previous_success=True,
            )
        )["id"]

        # Dirty the source with scheduler state that must not be inherited.
        source = await db_session.get(PrintQueueItem, source_id)
        source.dispatch_attempts = 3
        source.been_jumped = True
        source.gate_acknowledged = True
        source.filament_short = True
        source.waiting_reason = "no idle printer"
        source.error_message = "previous failure"
        source.scheduled_time = datetime(2026, 1, 1, 12, 0, 0)
        await db_session.commit()

        assert (await async_client.post(f"/api/v1/queue/batches/{order['id']}/dispatch", json={})).status_code == 200

        clone = (
            (
                await db_session.execute(
                    select(PrintQueueItem)
                    .where(PrintQueueItem.batch_id == order["id"])
                    .where(PrintQueueItem.id != source_id)
                )
            )
            .scalars()
            .one()
        )

        # Every column that is neither identity, ordering, nor deliberately reset
        # must match the source exactly.
        reset_on_clone = {
            "status",
            "waiting_reason",
            "been_jumped",
            "dispatch_attempts",
            "dispatching_at",
            "gate_acknowledged",
            "filament_short",
            "error_message",
            "started_at",
            "completed_at",
            "scheduled_time",
            "cleanup_library_after_dispatch",
        }
        identity = {"id", "created_at", "position"}

        await db_session.refresh(source)
        for column in (c.key for c in inspect(PrintQueueItem).mapper.column_attrs):
            if column in identity or column in reset_on_clone:
                continue
            assert getattr(clone, column) == getattr(source, column), (
                f"{column} was not carried onto the clone — a new setting column probably needs adding to "
                "CLONED_SETTING_COLUMNS"
            )

        assert clone.status == "pending"
        assert clone.dispatch_attempts == 0
        assert clone.been_jumped is False
        assert clone.gate_acknowledged is False
        assert clone.filament_short is False
        assert clone.waiting_reason is None
        assert clone.error_message is None
        assert clone.started_at is None and clone.completed_at is None
        # "Queue the rest now" must not replay a moment chosen for a different print.
        assert clone.scheduled_time is None
        # Would delete the source file out from under the rest of the order.
        assert clone.cleanup_library_after_dispatch is False

    async def test_dispatch_respects_limit(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 10}])
        await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)

        result = (await async_client.post(f"/api/v1/queue/batches/{order['id']}/dispatch", json={"limit": 4})).json()
        assert result["pending_count"] == 5  # the original plus four
        assert result["remaining_count"] == 5

    async def test_dispatch_can_target_a_single_plate(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(
            async_client,
            archive.id,
            [{"plate_id": 1, "quantity_target": 2}, {"plate_id": 2, "quantity_target": 2}],
        )
        await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=2)

        result = (
            await async_client.post(
                f"/api/v1/queue/batches/{order['id']}/dispatch",
                json={"plate_id": 2, "only_plate": True},
            )
        ).json()
        plate_one = next(p for p in result["plates"] if p["plate_id"] == 1)
        plate_two = next(p for p in result["plates"] if p["plate_id"] == 2)
        assert plate_one["remaining"] == 1
        assert plate_two["remaining"] == 0

    async def test_dispatch_without_a_reference_item_is_rejected(self, async_client, archive_factory):
        """Nothing to clone means no configuration to copy — say so, don't guess."""
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 4, "quantity_target": 2}])

        response = await async_client.post(f"/api/v1/queue/batches/{order['id']}/dispatch", json={})
        assert response.status_code == 400
        assert "no queued or finished run" in response.json()["detail"]

    async def test_one_stranded_plate_does_not_block_the_others(self, async_client, printer_factory, archive_factory):
        """A plate with nothing to clone is skipped, not an abort for the whole order (#2960)."""
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(
            async_client,
            archive.id,
            [{"plate_id": 1, "quantity_target": 2}, {"plate_id": 2, "quantity_target": 2}],
        )
        # Plate 2 never gets an item, so only plate 1 can be cloned.
        await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)

        response = await async_client.post(f"/api/v1/queue/batches/{order['id']}/dispatch", json={})
        assert response.status_code == 200, response.text
        result = response.json()
        plate_one = next(p for p in result["plates"] if p["plate_id"] == 1)
        plate_two = next(p for p in result["plates"] if p["plate_id"] == 2)
        assert plate_one["remaining"] == 0
        assert plate_two["remaining"] == 2
        # The order still owes plate 2 but reports that none of it is queueable.
        assert result["remaining_count"] == 2
        assert result["dispatchable_count"] == 0
        assert plate_one["can_dispatch"] is False  # nothing left owing
        assert plate_two["can_dispatch"] is False  # owing, but nothing to clone

    async def test_dispatching_a_stranded_plate_by_name_still_fails_loudly(
        self, async_client, printer_factory, archive_factory
    ):
        """An explicit single-plate request must not silently do nothing."""
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(
            async_client,
            archive.id,
            [
                {"plate_id": 1, "quantity_target": 1},
                {"plate_id": 2, "plate_name": "Side rail.stl", "quantity_target": 1},
            ],
        )
        await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)

        response = await async_client.post(
            f"/api/v1/queue/batches/{order['id']}/dispatch",
            json={"plate_id": 2, "only_plate": True},
        )
        assert response.status_code == 400
        # Named by the label the Batches tab shows, not by a bare index.
        assert "Side rail.stl" in response.json()["detail"]

    async def test_can_dispatch_is_true_while_a_source_survives(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        """A cancelled run is still a clone source — the plate stays queueable."""
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 2}])
        item = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await _set_status(db_session, item["id"], "cancelled")

        result = (await async_client.get(f"/api/v1/queue/batches/{order['id']}")).json()
        assert result["remaining_count"] == 2
        assert result["dispatchable_count"] == 2
        assert result["plates"][0]["can_dispatch"] is True

    async def test_dispatch_on_legacy_batch_is_a_noop(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory()
        response = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "quantity": 2}
        )
        batch_id = response.json()["batch_id"]

        result = (await async_client.post(f"/api/v1/queue/batches/{batch_id}/dispatch", json={})).json()
        assert result["pending_count"] == 2

    async def test_cannot_dispatch_a_cancelled_order(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 3}])
        await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await async_client.delete(f"/api/v1/queue/batches/{order['id']}")

        response = await async_client.post(f"/api/v1/queue/batches/{order['id']}/dispatch", json={})
        assert response.status_code == 400
        assert "cancelled" in response.json()["detail"]

    async def test_redispatch_after_failure_replaces_the_burned_run(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        """End to end: 2 wanted, 1 completes, 1 fails, dispatch queues the replacement."""
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 2}])
        first = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        second = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await _set_status(db_session, first["id"], "completed")
        await _set_status(db_session, second["id"], "failed")

        result = (await async_client.post(f"/api/v1/queue/batches/{order['id']}/dispatch", json={})).json()
        assert result["pending_count"] == 1
        assert result["remaining_count"] == 0
        assert result["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.integration
class TestBatchOrderHeader:
    async def test_header_fields_round_trip(self, async_client, archive_factory):
        archive = await archive_factory()
        order = await _create_order(
            async_client,
            archive.id,
            [{"plate_id": 1, "quantity_target": 1}],
            due_date="2026-09-01T12:00:00",
            notes="Rush job",
        )
        assert order["notes"] == "Rush job"
        assert order["due_date"].startswith("2026-09-01T12:00:00")

        patched = (
            await async_client.patch(
                f"/api/v1/queue/batches/{order['id']}", json={"name": "Renamed", "notes": "Updated"}
            )
        ).json()
        assert patched["name"] == "Renamed"
        assert patched["notes"] == "Updated"

    async def test_unknown_project_is_rejected(self, async_client, archive_factory):
        archive = await archive_factory()
        response = await async_client.post(
            "/api/v1/queue/batches",
            json={
                "name": "Order",
                "archive_id": archive.id,
                "project_id": 999999,
                "plates": [{"plate_id": 1, "quantity_target": 1}],
            },
        )
        assert response.status_code == 404

    async def test_patch_replaces_the_target_set(self, async_client, archive_factory):
        """A plate omitted from the payload has its target row removed."""
        archive = await archive_factory()
        order = await _create_order(
            async_client,
            archive.id,
            [{"plate_id": 1, "quantity_target": 1}, {"plate_id": 2, "quantity_target": 1}],
        )
        patched = (
            await async_client.patch(
                f"/api/v1/queue/batches/{order['id']}",
                json={"plates": [{"plate_id": 1, "quantity_target": 5}]},
            )
        ).json()
        assert [p["plate_id"] for p in patched["plates"]] == [1]
        assert patched["target_count"] == 5


@pytest.mark.asyncio
@pytest.mark.integration
class TestBatchOrderCost:
    async def test_cost_rolls_up_from_logged_runs(self, async_client, printer_factory, archive_factory, db_session):
        """Cost is attributed through queue_item_id, not guessed from the archive."""
        from backend.app.models.print_log import PrintLogEntry

        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 4}])
        first = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        second = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await _set_status(db_session, first["id"], "completed")
        await _set_status(db_session, second["id"], "completed")

        db_session.add(
            PrintLogEntry(
                archive_id=archive.id,
                queue_item_id=first["id"],
                status="completed",
                cost=2.0,
                energy_cost=0.5,
                filament_used_grams=40.0,
            )
        )
        db_session.add(
            PrintLogEntry(
                archive_id=archive.id,
                queue_item_id=second["id"],
                status="completed",
                cost=3.0,
                energy_cost=0.5,
                filament_used_grams=60.0,
            )
        )
        # A run of the same archive that has nothing to do with this order.
        db_session.add(PrintLogEntry(archive_id=archive.id, queue_item_id=None, status="completed", cost=99.0))
        await db_session.commit()

        result = (await async_client.get(f"/api/v1/queue/batches/{order['id']}")).json()
        assert result["actual_cost"] == pytest.approx(6.0)
        assert result["filament_used_grams"] == pytest.approx(100.0)
        # Two completed at 3.00 each, two still owed.
        assert result["estimated_remaining_cost"] == pytest.approx(6.0)

    async def test_cost_is_unknown_not_zero_before_the_first_run(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 2}])
        await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)

        result = (await async_client.get(f"/api/v1/queue/batches/{order['id']}")).json()
        assert result["actual_cost"] is None
        assert result["estimated_remaining_cost"] is None


@pytest.mark.asyncio
@pytest.mark.integration
class TestOrderSourcePreservation:
    """Deleting an order's last run for a plate must not strand it (#2960).

    Dispatch produces what an order still owes by cloning an existing queue
    item. Hard-deleting the last one left the order reporting work outstanding
    with nothing able to produce it, and no way to close it out either.
    """

    async def test_last_run_for_a_plate_is_cancelled_not_deleted(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 3}])
        item = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)

        response = await async_client.delete(f"/api/v1/queue/{item['id']}")
        assert response.status_code == 200
        assert response.json()["deleted"] is False

        db_session.expire_all()
        survivor = await db_session.get(PrintQueueItem, item["id"])
        assert survivor is not None
        assert survivor.status == "cancelled"

    async def test_the_order_can_still_be_dispatched_afterwards(self, async_client, printer_factory, archive_factory):
        """The whole point: #2960's stuck card, end to end."""
        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 2}])
        item = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await async_client.delete(f"/api/v1/queue/{item['id']}")

        # A cancelled run does not consume a target, so the order owes both.
        state = (await async_client.get(f"/api/v1/queue/batches/{order['id']}")).json()
        assert state["remaining_count"] == 2
        assert state["dispatchable_count"] == 2

        result = (await async_client.post(f"/api/v1/queue/batches/{order['id']}/dispatch", json={})).json()
        assert result["pending_count"] == 2
        assert result["remaining_count"] == 0

    async def test_a_run_with_a_sibling_is_really_deleted(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        """Only the *last* source is protected — nothing else changes."""
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 3}])
        first = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)

        response = await async_client.delete(f"/api/v1/queue/{first['id']}")
        assert response.json()["deleted"] is True
        db_session.expire_all()
        assert await db_session.get(PrintQueueItem, first["id"]) is None

    async def test_a_completed_run_is_never_rewritten_as_cancelled(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        """Deleting history is the user's call; falsifying it is not."""
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 3}])
        item = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await _set_status(db_session, item["id"], "completed")

        response = await async_client.delete(f"/api/v1/queue/{item['id']}")
        assert response.json()["deleted"] is True
        db_session.expire_all()
        assert await db_session.get(PrintQueueItem, item["id"]) is None

    async def test_a_grouping_without_targets_deletes_as_before(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        """A grouping owes nothing, so nothing about it can be stranded."""
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory()
        response = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "quantity": 2}
        )
        items = response.json()
        first = items[0] if isinstance(items, list) else items

        deleted = await async_client.delete(f"/api/v1/queue/{first['id']}")
        assert deleted.json()["deleted"] is True
        db_session.expire_all()
        assert await db_session.get(PrintQueueItem, first["id"]) is None

    async def test_a_cancelled_order_lets_its_leftover_rows_be_deleted(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        """Nothing to protect once the order is closed — and tidying up is why you delete."""
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 3}])
        item = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)
        await async_client.delete(f"/api/v1/queue/batches/{order['id']}")

        deleted = await async_client.delete(f"/api/v1/queue/{item['id']}")
        assert deleted.json()["deleted"] is True
        db_session.expire_all()
        assert await db_session.get(PrintQueueItem, item["id"]) is None

    async def test_a_plate_the_order_has_no_target_for_deletes_as_before(
        self, async_client, printer_factory, archive_factory, db_session
    ):
        """Grouped in by hand after the fact: it owes nothing, so delete means delete."""
        from backend.app.models.print_queue import PrintQueueItem

        printer = await printer_factory()
        archive = await archive_factory()
        order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 1}])
        stray = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=7)

        deleted = await async_client.delete(f"/api/v1/queue/{stray['id']}")
        assert deleted.json()["deleted"] is True
        db_session.expire_all()
        assert await db_session.get(PrintQueueItem, stray["id"]) is None
