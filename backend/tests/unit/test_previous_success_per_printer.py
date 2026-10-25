"""'Only if the previous print succeeded' is about the printer a job lands on."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401
from backend.app.core.database import Base
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import MODEL_GATE_WAIT_REASON, PrintScheduler
from backend.tests.unit.test_scheduler_busy_reasons_3018 import _scheduler


@pytest.fixture
async def farm(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    base_dir = tmp_path / "farm"
    (base_dir / "archives").mkdir(parents=True)
    (base_dir / "archives" / "job.3mf").write_bytes(b"archive payload")
    async with session_maker() as db:
        printers = [
            Printer(name=name, serial_number=name, ip_address=f"10.0.0.{i}", access_code="code", model="X1C")
            for i, name in enumerate(("A", "B"), start=1)
        ]
        db.add_all(printers)
        await db.flush()
        archive = PrintArchive(
            printer_id=printers[0].id,
            filename="job.3mf",
            file_path="archives/job.3mf",
            file_size=15,
            print_time_seconds=120,
            status="completed",
            sliced_for_model="X1C",
        )
        db.add(archive)
        await db.flush()
        ids = SimpleNamespace(a=printers[0].id, b=printers[1].id, archive=archive.id)
        await db.commit()
    try:
        yield SimpleNamespace(session_maker=session_maker, base_dir=base_dir, ids=ids)
    finally:
        await engine.dispose()


async def _last_print(farm, printer_id: int, status: str) -> None:
    async with farm.session_maker() as db:
        db.add(
            PrintQueueItem(
                printer_id=printer_id,
                archive_id=farm.ids.archive,
                status=status,
                position=0,
                completed_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            )
        )
        await db.commit()


async def _model_job(farm) -> int:
    async with farm.session_maker() as db:
        item = PrintQueueItem(
            target_model="X1C",
            archive_id=farm.ids.archive,
            status="pending",
            position=1,
            require_previous_success=True,
        )
        db.add(item)
        await db.commit()
        return item.id


async def _run(farm, item_id: int, waiting=None) -> PrintQueueItem:
    async def matcher(db, model, exclude, *args, **kwargs):
        for printer_id in (farm.ids.a, farm.ids.b):
            if printer_id not in exclude:
                return printer_id, None
        return None, "All matching printers are busy"

    async with _scheduler(farm, idle=True) as scheduler:
        with (
            patch.object(scheduler, "_find_idle_printer_for_model", matcher),
            patch(
                "backend.app.services.notification_service.notification_service.on_queue_job_waiting",
                waiting or AsyncMock(),
            ),
        ):
            await scheduler.check_queue()
    async with farm.session_maker() as db:
        return await db.get(PrintQueueItem, item_id)


async def test_a_printer_whose_last_print_failed_is_passed_over(farm):
    await _last_print(farm, farm.ids.a, "failed")
    item_id = await _model_job(farm)

    item = await _run(farm, item_id)

    assert item.status != "skipped"
    assert item.printer_id == farm.ids.b


async def test_the_job_waits_when_every_printer_failed_last(farm):
    await _last_print(farm, farm.ids.a, "failed")
    await _last_print(farm, farm.ids.b, "aborted")
    item_id = await _model_job(farm)

    item = await _run(farm, item_id)

    assert item.status == "pending"
    assert item.printer_id is None
    assert item.waiting_reason == MODEL_GATE_WAIT_REASON


async def test_the_check_reads_the_printer_it_is_given(db_session, printer_factory):
    a, b = await printer_factory(), await printer_factory()
    db_session.add(PrintQueueItem(printer_id=a.id, status="failed", completed_at=datetime.now(timezone.utc)))
    job = PrintQueueItem(target_model="X1C", status="pending", require_previous_success=True)
    db_session.add(job)
    await db_session.commit()
    scheduler = PrintScheduler()

    assert await scheduler._check_previous_success(db_session, job, printer_id=a.id) is False
    assert await scheduler._check_previous_success(db_session, job, printer_id=b.id) is True


async def test_a_row_ended_without_a_time_is_not_the_latest(db_session, printer_factory):
    printer = await printer_factory()
    db_session.add(PrintQueueItem(printer_id=printer.id, status="failed", completed_at=datetime.now(timezone.utc)))
    await db_session.flush()
    db_session.add(PrintQueueItem(printer_id=printer.id, status="cancelled", completed_at=None))
    job = PrintQueueItem(printer_id=printer.id, status="pending", require_previous_success=True)
    db_session.add(job)
    await db_session.commit()

    assert await PrintScheduler()._check_previous_success(db_session, job) is False


async def test_cancelling_an_order_stamps_when_its_jobs_ended(
    async_client, db_session, printer_factory, archive_factory
):
    from backend.tests.integration.test_print_batch_orders import _create_order, _queue_item

    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    order = await _create_order(async_client, archive.id, [{"plate_id": 1, "quantity_target": 2}])
    item = await _queue_item(async_client, printer.id, archive.id, order["id"], plate_id=1)

    response = await async_client.delete(f"/api/v1/queue/batches/{order['id']}")

    assert response.status_code == 200, response.text
    db_session.expire_all()
    row = await db_session.get(PrintQueueItem, item["id"])
    assert row.status == "cancelled"
    assert row.completed_at is not None


async def test_a_job_that_waited_for_a_busy_printer_says_when_it_now_needs_you(farm):
    await _last_print(farm, farm.ids.a, "failed")
    await _last_print(farm, farm.ids.b, "failed")
    item_id = await _model_job(farm)
    async with farm.session_maker() as db:
        (await db.get(PrintQueueItem, item_id)).waiting_reason = "Busy: A, B"
        await db.commit()
    waiting = AsyncMock()

    await _run(farm, item_id, waiting)

    waiting.assert_awaited_once()
    assert waiting.await_args.kwargs["waiting_reason"] == MODEL_GATE_WAIT_REASON


async def test_resuming_a_model_clears_the_gate_on_its_printers(async_client, db_session, printer_factory):
    x1c_a = await printer_factory(model="X1C")
    x1c_b = await printer_factory(model="X1C")
    p1s = await printer_factory(model="P1S")
    ids = {"a": x1c_a.id, "b": x1c_b.id, "p": p1s.id}
    for printer_id in ids.values():
        db_session.add(PrintQueueItem(printer_id=printer_id, status="failed", completed_at=datetime.now(timezone.utc)))
    await db_session.commit()

    response = await async_client.post("/api/v1/queue/model/X1C/resume")

    assert response.status_code == 200, response.text
    assert response.json() == {"acknowledged": 2, "printers": 2}
    db_session.expire_all()
    from sqlalchemy import select

    rows = (await db_session.execute(select(PrintQueueItem))).scalars().all()
    acknowledged = {row.printer_id: row.gate_acknowledged for row in rows}
    assert acknowledged == {ids["a"]: True, ids["b"]: True, ids["p"]: False}


async def test_resuming_a_model_keeps_to_the_callers_printers(async_client, db_session, printer_factory):
    from sqlalchemy import select

    from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

    ours, theirs = await printer_factory(model="X1C"), await printer_factory(model="X1C")
    ours_id, theirs_id = ours.id, theirs.id
    for printer_id in (ours_id, theirs_id):
        db_session.add(PrintQueueItem(printer_id=printer_id, status="failed", completed_at=datetime.now(timezone.utc)))
    await db_session.commit()
    token = await _admin_token(async_client)
    perms = await _group(async_client, token, "perms", permissions=["printers:read", "queue:read", "queue:update_all"])
    team = await _group(async_client, token, "team", printer_ids=[ours_id])
    member = _auth((await _user(async_client, token, "member", [perms, team]))[0])

    response = await async_client.post("/api/v1/queue/model/X1C/resume", headers=member)

    assert response.json() == {"acknowledged": 1, "printers": 1}
    db_session.expire_all()
    rows = (await db_session.execute(select(PrintQueueItem))).scalars().all()
    assert {row.printer_id: row.gate_acknowledged for row in rows} == {ours_id: True, theirs_id: False}


def test_the_queue_page_knows_the_wait_reason():
    """The queue page offers Resume for jobs waiting with exactly this reason."""
    page = Path(__file__).resolve().parents[3] / "frontend" / "src" / "pages" / "QueuePage.tsx"
    assert f"'{MODEL_GATE_WAIT_REASON}'" in page.read_text()
