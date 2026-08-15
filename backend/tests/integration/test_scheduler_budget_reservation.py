"""Budget-reservation lifecycle through the unified queue scheduler."""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
import backend.app.services.print_scheduler as scheduler_module
from backend.app.core.database import Base
from backend.app.models.archive import PrintArchive
from backend.app.models.finance import BudgetReservation, CostCenter, UserWallet
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.finance_budget import validate_print_budget
from backend.app.services.print_scheduler import PrintScheduler
from backend.tests._fixtures.background_tasks import discarding_spawn_patch

pytestmark = pytest.mark.integration


@pytest.fixture
async def billing_dispatch_case(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    base_dir = tmp_path / "billing-dispatch"
    archive_rel = Path("archives") / "job.3mf"
    archive_abs = base_dir / archive_rel
    archive_abs.parent.mkdir(parents=True)
    archive_abs.write_bytes(b"archive payload")

    async with session_maker() as db:
        db.add(Settings(key="billing_enabled", value="true"))
        user = User(username="scheduler-budget-admin", role="admin", is_active=True)
        cost_center = CostCenter(name="Scheduler Budget", is_active=True, monthly_budget=10.0)
        printer = Printer(
            name="Budget Printer",
            serial_number="BUDGET-SERIAL",
            ip_address="127.0.0.1",
            access_code="access-code",
            model="X1C",
        )
        db.add_all([user, cost_center, printer])
        await db.flush()

        archive = PrintArchive(
            printer_id=printer.id,
            filename="job.3mf",
            file_path=str(archive_rel),
            file_size=archive_abs.stat().st_size,
            status="completed",
            cost=4.0,
            created_by_id=user.id,
            cost_center_id=cost_center.id,
        )
        db.add(archive)
        await db.flush()

        item = PrintQueueItem(
            printer_id=printer.id,
            archive_id=archive.id,
            cost_center_id=cost_center.id,
            estimated_cost=4.0,
            created_by_id=user.id,
            status="pending",
        )
        db.add(item)
        await db.commit()

        ids = SimpleNamespace(
            user_id=user.id,
            cost_center_id=cost_center.id,
            printer_id=printer.id,
            archive_id=archive.id,
            item_id=item.id,
        )

    try:
        yield SimpleNamespace(session_maker=session_maker, base_dir=base_dir, ids=ids)
    finally:
        await engine.dispose()


async def _dispatch(ctx, *, uploaded: bool = True, cancel_during_upload: bool = False):
    scheduler = PrintScheduler()
    start_print = MagicMock(return_value=True)

    async def upload(*_args, **_kwargs):
        if cancel_during_upload:
            async with ctx.session_maker() as other_db:
                item = await other_db.get(PrintQueueItem, ctx.ids.item_id)
                item.status = "cancelled"
                await other_db.commit()
        return uploaded

    patches = [
        patch.object(scheduler_module, "async_session", ctx.session_maker),
        patch.object(scheduler_module.settings, "base_dir", ctx.base_dir),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager.start_print", start_print),
        patch("backend.app.services.print_scheduler.printer_manager.set_awaiting_plate_clear", MagicMock()),
        patch(
            "backend.app.services.print_scheduler.get_ftp_retry_settings",
            AsyncMock(return_value=(False, 0, 0, 1.0)),
        ),
        patch("backend.app.services.print_scheduler.delete_file_async", AsyncMock(return_value=True)),
        patch("backend.app.services.print_scheduler.upload_file_async", upload),
        patch("backend.app.services.print_scheduler.cache_3mf_download", MagicMock()),
        discarding_spawn_patch(),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_started", AsyncMock()),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_failed", AsyncMock()),
        patch("backend.app.services.mqtt_relay.mqtt_relay.on_queue_job_started", AsyncMock()),
        patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
        patch.object(scheduler, "_preheat_and_soak", AsyncMock()),
    ]
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        await scheduler._dispatch_one(ctx.ids.item_id)

    return start_print


async def _reservation(ctx):
    async with ctx.session_maker() as db:
        return await db.scalar(
            select(BudgetReservation).where(
                BudgetReservation.source_type == "print_queue",
                BudgetReservation.source_id == ctx.ids.item_id,
            )
        )


@pytest.mark.asyncio
async def test_successful_scheduler_dispatch_keeps_one_active_reservation(billing_dispatch_case):
    start_print = await _dispatch(billing_dispatch_case)

    reservation = await _reservation(billing_dispatch_case)
    assert reservation is not None
    assert reservation.status == "active"
    assert reservation.amount == 4.0
    assert reservation.print_archive_id == billing_dispatch_case.ids.archive_id
    start_print.assert_called_once()

    async with billing_dispatch_case.session_maker() as db:
        item = await db.get(PrintQueueItem, billing_dispatch_case.ids.item_id)
        archive = await db.get(PrintArchive, billing_dispatch_case.ids.archive_id)
        assert item.billing_run_id is not None
        assert archive.billing_run_id == item.billing_run_id
        # The internal UUID is deliberately independent from Bambu's 31-bit
        # task/subtask identifier.
        assert len(item.billing_run_id) == 36

    # The printing queue row and its persisted reservation represent the same
    # €4 hold. A second €6 job must fit exactly; €6.01 must not.
    async with billing_dispatch_case.session_maker() as db:
        user = await db.get(User, billing_dispatch_case.ids.user_id)
        second = PrintQueueItem(
            printer_id=billing_dispatch_case.ids.printer_id,
            archive_id=billing_dispatch_case.ids.archive_id,
            cost_center_id=billing_dispatch_case.ids.cost_center_id,
            estimated_cost=6.0,
            created_by_id=user.id,
            status="pending",
        )
        db.add(second)
        await db.commit()
        await validate_print_budget(
            db,
            cost_center_id=second.cost_center_id,
            estimated_cost=6.0,
            current_user=user,
            exclude_queue_item_id=second.id,
        )
        with pytest.raises(HTTPException, match="exceeds available"):
            await validate_print_budget(
                db,
                cost_center_id=second.cost_center_id,
                estimated_cost=6.01,
                current_user=user,
                exclude_queue_item_id=second.id,
            )


@pytest.mark.asyncio
async def test_cost_center_without_budget_is_unlimited_regardless_of_wallet_balance(billing_dispatch_case):
    """Wallet balance is accounting data; only an explicit cost-center budget gates printing."""
    async with billing_dispatch_case.session_maker() as db:
        user = await db.get(User, billing_dispatch_case.ids.user_id)
        center = await db.get(CostCenter, billing_dispatch_case.ids.cost_center_id)
        center.monthly_budget = None
        center.total_budget = None
        wallet = UserWallet(user_id=user.id, balance=-100.0, currency="EUR")
        db.add(wallet)
        await db.commit()

        await validate_print_budget(
            db,
            cost_center_id=center.id,
            estimated_cost=1_000_000.0,
            current_user=user,
        )


@pytest.mark.asyncio
async def test_upload_failure_releases_scheduler_reservation(billing_dispatch_case):
    start_print = await _dispatch(billing_dispatch_case, uploaded=False)

    reservation = await _reservation(billing_dispatch_case)
    assert reservation is not None
    assert reservation.status == "released"
    assert reservation.released_at is not None
    start_print.assert_not_called()


@pytest.mark.asyncio
async def test_retried_dispatch_reuses_active_reservation(billing_dispatch_case):
    await _dispatch(billing_dispatch_case)

    # Simulate startup recovery after the process stopped with a persisted
    # reservation and the queue row was made dispatchable again.
    async with billing_dispatch_case.session_maker() as db:
        item = await db.get(PrintQueueItem, billing_dispatch_case.ids.item_id)
        item.status = "pending"
        item.started_at = None
        item.dispatching_at = None
        await db.commit()

    await _dispatch(billing_dispatch_case)

    async with billing_dispatch_case.session_maker() as db:
        reservations = (
            (
                await db.execute(
                    select(BudgetReservation).where(
                        BudgetReservation.source_type == "print_queue",
                        BudgetReservation.source_id == billing_dispatch_case.ids.item_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(reservations) == 1
    assert reservations[0].status == "active"


@pytest.mark.asyncio
async def test_cancel_during_upload_releases_scheduler_reservation(billing_dispatch_case):
    start_print = await _dispatch(billing_dispatch_case, cancel_during_upload=True)

    reservation = await _reservation(billing_dispatch_case)
    assert reservation is not None
    assert reservation.status == "released"
    assert reservation.released_at is not None
    start_print.assert_not_called()

    async with billing_dispatch_case.session_maker() as db:
        item = await db.get(PrintQueueItem, billing_dispatch_case.ids.item_id)
        active_count = await db.scalar(
            select(func.count()).select_from(BudgetReservation).where(BudgetReservation.status == "active")
        )
    assert item.status == "cancelled"
    assert active_count == 0


@pytest.mark.asyncio
async def test_cleanup_session_does_not_rollback_failed_dispatch_status(billing_dispatch_case):
    scheduler = PrintScheduler()

    async def fail_after_reserving(db, item):
        db.add(
            BudgetReservation(
                cost_center_id=item.cost_center_id,
                amount=4.0,
                status="active",
                source_type="print_queue",
                source_id=item.id,
                print_archive_id=item.archive_id,
            )
        )
        await db.commit()
        scheduler._unconfirmed_budget_reservations.add(item.id)
        item.status = "failed"
        item.error_message = "dispatch failed after reservation"
        raise RuntimeError("simulated dispatch failure")

    with (
        patch.object(scheduler_module, "async_session", billing_dispatch_case.session_maker),
        patch.object(scheduler, "_start_print", fail_after_reserving),
        pytest.raises(RuntimeError, match="simulated dispatch failure"),
    ):
        await scheduler._dispatch_one(billing_dispatch_case.ids.item_id)

    async with billing_dispatch_case.session_maker() as db:
        item = await db.get(PrintQueueItem, billing_dispatch_case.ids.item_id)
        reservation = await db.scalar(
            select(BudgetReservation).where(
                BudgetReservation.source_type == "print_queue",
                BudgetReservation.source_id == billing_dispatch_case.ids.item_id,
            )
        )

    assert item.status == "failed"
    assert item.error_message == "dispatch failed after reservation"
    assert item.dispatching_at is None
    assert reservation.status == "released"
