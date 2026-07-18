"""The scheduler must release its pooled DB connection before long printer I/O (#2572).

``_dispatch_selected`` opens one ``async_session`` per queue item and hands it to
``_start_print``, which reads the printer/archive rows up front and then runs the
preheat/heat-soak wait and the FTP delete+upload. Before this fix the transaction
opened by those first reads stayed "idle in transaction" for the whole soak and
the entire multi-second 3MF upload, pinning one pooled connection per in-flight
dispatch. On a large farm dispatching many jobs at once that exhausted the pool —
a reporter (@Jostxxl) correlated one surviving idle-in-transaction session to
exactly this path by timestamp.

Two release points are verified:

* ``_start_print`` commits before the FTP delete/upload block.
* ``_preheat_and_soak`` commits after its read phase, before the (up-to-15-minute)
  soak wait.

Both rely on ``expire_on_commit=False`` keeping the ORM rows readable afterwards.
"""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
import backend.app.services.print_scheduler as scheduler_module
from backend.app.core.database import Base
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
async def dispatch_case(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    base_dir = tmp_path / "case"
    base_dir.mkdir()
    archive_rel = Path("archives") / "job.3mf"
    archive_abs = base_dir / archive_rel
    archive_abs.parent.mkdir(parents=True, exist_ok=True)
    archive_abs.write_bytes(b"archive payload")

    async with session_maker() as db:
        printer = Printer(
            name="Printer",
            serial_number="SERIAL",
            ip_address="127.0.0.1",
            access_code="access-code",
            model="X1C",
        )
        db.add(printer)
        await db.flush()
        archive = PrintArchive(
            printer_id=printer.id,
            filename="job.3mf",
            file_path=str(archive_rel),
            file_size=archive_abs.stat().st_size,
            status="completed",
        )
        db.add(archive)
        await db.flush()
        item = PrintQueueItem(printer_id=printer.id, archive_id=archive.id, status="pending")
        db.add(item)
        await db.commit()
        ids = SimpleNamespace(printer_id=printer.id, archive_id=archive.id, item_id=item.id)

    try:
        yield SimpleNamespace(session_maker=session_maker, base_dir=base_dir, ids=ids)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_connection_released_before_ftp_upload(dispatch_case):
    """At the moment the FTP upload starts, the caller's transaction is closed."""
    scheduler = PrintScheduler()
    observed: dict = {}

    async with dispatch_case.session_maker() as db:
        item = await db.get(PrintQueueItem, dispatch_case.ids.item_id)

        async def record_txn_state(*args, **kwargs):
            # Captured at the instant upload_file_async runs — must be outside a txn.
            observed["in_transaction_at_upload"] = db.in_transaction()
            return True

        patches = [
            patch.object(scheduler_module.settings, "base_dir", dispatch_case.base_dir),
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
            patch("backend.app.services.print_scheduler.printer_manager.start_print", MagicMock(return_value=True)),
            patch("backend.app.services.print_scheduler.printer_manager.set_awaiting_plate_clear", MagicMock()),
            patch(
                "backend.app.services.print_scheduler.get_ftp_retry_settings",
                AsyncMock(return_value=(False, 0, 0, 1.0)),
            ),
            patch("backend.app.services.print_scheduler.delete_file_async", AsyncMock(return_value=True)),
            patch("backend.app.services.print_scheduler.upload_file_async", record_txn_state),
            patch("backend.app.services.print_scheduler.cache_3mf_download", MagicMock()),
            patch("backend.app.services.print_scheduler.spawn_background_task", MagicMock()),
            patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
            patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
            patch.object(scheduler, "_preheat_and_soak", AsyncMock()),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            await scheduler._start_print(db, item)

    assert observed.get("in_transaction_at_upload") is False, (
        "the pooled connection was still idle-in-transaction when the FTP upload started"
    )


@pytest.mark.asyncio
async def test_connection_released_before_preheat_soak(dispatch_case):
    """The preheat stage releases the caller's transaction before the soak wait."""
    scheduler = PrintScheduler()
    observed: dict = {}

    item = SimpleNamespace(id=1, preheat_override="on", preheat_chamber_target_override=None)
    printer = SimpleNamespace(id=7, model="P1S")  # no chamber sensor → straight to soak
    archive = SimpleNamespace(bed_temperature=60)

    async with dispatch_case.session_maker() as db:
        # Simulate the caller's earlier reads holding an open transaction.
        await db.execute(text("SELECT 1"))
        assert db.in_transaction()

        async def record_sleep(_seconds):
            observed["in_transaction_at_soak"] = db.in_transaction()

        client = MagicMock()
        client.set_bed_temperature = MagicMock(return_value=True)

        with (
            patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
            patch.object(
                scheduler,
                "_get_int_setting",
                AsyncMock(
                    side_effect=lambda _db, key, default: {
                        "preheat_max_wait_seconds": 0,
                        "preheat_soak_seconds": 300,
                    }.get(key, default)
                ),
            ),
            patch.object(scheduler, "_get_preheat_filament_targets", AsyncMock(return_value={})),
            patch("backend.app.services.print_scheduler.printer_manager") as pm,
            patch("backend.app.services.print_scheduler.asyncio.sleep", record_sleep),
        ):
            pm.get_client.return_value = client
            pm.get_status.return_value = SimpleNamespace(
                temperatures={"bed": 100, "chamber": 0}, airduct_mode=0, raw_data={}
            )
            await scheduler._preheat_and_soak(db, item, printer, archive)

    assert observed.get("in_transaction_at_soak") is False, (
        "the pooled connection was still idle-in-transaction during the heat-soak wait"
    )
