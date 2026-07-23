"""Tests for PrintScheduler scheduled-drying dispatch (#2638)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from backend.app.models.scheduled_drying import ScheduledDrying
from backend.app.services.print_scheduler import PrintScheduler


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mock_state(ams_id=0, dry_time=0, dry_sf_reason=None):
    state = MagicMock()
    state.raw_data = {"ams": [{"id": ams_id, "dry_time": dry_time, "dry_sf_reason": dry_sf_reason or []}]}
    return state


async def _make_row(db_session, printer_factory, **kwargs):
    printer = await printer_factory()
    defaults = {"printer_id": printer.id, "ams_id": 0, "temp": 65, "duration_hours": 8}
    defaults.update(kwargs)
    row = ScheduledDrying(**defaults)
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


@pytest.fixture
def scheduler():
    return PrintScheduler()


@pytest.mark.asyncio
async def test_future_start_after_not_dispatched(scheduler, db_session, printer_factory):
    row = await _make_row(db_session, printer_factory, start_after=_utcnow_naive() + timedelta(hours=2))
    with patch("backend.app.services.print_scheduler.printer_manager") as mock_pm:
        await scheduler._check_scheduled_dryings(db_session)
    mock_pm.send_drying_command.assert_not_called()
    await db_session.refresh(row)
    assert row.status == "pending"


@pytest.mark.asyncio
async def test_due_row_dispatches_and_goes_running(scheduler, db_session, printer_factory):
    row = await _make_row(
        db_session, printer_factory, start_after=_utcnow_naive() - timedelta(minutes=1), filament="PETG"
    )
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state()
        mock_pm.send_drying_command.return_value = True
        await scheduler._check_scheduled_dryings(db_session)

    mock_pm.send_drying_command.assert_called_once_with(
        row.printer_id, 0, 65, 8, mode=1, filament="PETG", rotate_tray=False
    )
    await db_session.refresh(row)
    assert row.status == "running"
    assert row.started_at is not None
    assert scheduler._drying_in_progress.get(row.printer_id)


@pytest.mark.asyncio
async def test_null_start_after_dispatches_immediately(scheduler, db_session, printer_factory):
    row = await _make_row(db_session, printer_factory, start_after=None)
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state()
        mock_pm.send_drying_command.return_value = True
        await scheduler._check_scheduled_dryings(db_session)
    await db_session.refresh(row)
    assert row.status == "running"


@pytest.mark.asyncio
async def test_busy_printer_stays_pending_with_reason(scheduler, db_session, printer_factory):
    row = await _make_row(db_session, printer_factory, start_after=None)
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=False),
    ):
        mock_pm.get_status.return_value = _mock_state()
        await scheduler._check_scheduled_dryings(db_session)
    mock_pm.send_drying_command.assert_not_called()
    await db_session.refresh(row)
    assert row.status == "pending"
    assert row.waiting_reason == "printer_busy"


@pytest.mark.asyncio
async def test_offline_printer_stays_pending(scheduler, db_session, printer_factory):
    row = await _make_row(db_session, printer_factory, start_after=None)
    with patch("backend.app.services.print_scheduler.printer_manager") as mock_pm:
        mock_pm.get_status.return_value = None
        await scheduler._check_scheduled_dryings(db_session)
    await db_session.refresh(row)
    assert row.status == "pending"
    assert row.waiting_reason == "printer_offline"


@pytest.mark.asyncio
async def test_ams_blocked_stays_pending(scheduler, db_session, printer_factory):
    row = await _make_row(db_session, printer_factory, start_after=None)
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state(dry_sf_reason=[1])
        await scheduler._check_scheduled_dryings(db_session)
    mock_pm.send_drying_command.assert_not_called()
    await db_session.refresh(row)
    assert row.waiting_reason == "ams_blocked"


@pytest.mark.asyncio
async def test_running_completes_after_duration(scheduler, db_session, printer_factory):
    row = await _make_row(
        db_session,
        printer_factory,
        status="running",
        duration_hours=1,
        started_at=_utcnow_naive() - timedelta(minutes=58),  # >= 90% of 1h
    )
    with patch("backend.app.services.print_scheduler.printer_manager") as mock_pm:
        mock_pm.get_status.return_value = _mock_state(dry_time=0)
        await scheduler._check_scheduled_dryings(db_session)
    await db_session.refresh(row)
    assert row.status == "completed"
    assert row.completed_at is not None


@pytest.mark.asyncio
async def test_running_interrupted_requeues(scheduler, db_session, printer_factory):
    row = await _make_row(
        db_session,
        printer_factory,
        status="running",
        duration_hours=8,
        started_at=_utcnow_naive() - timedelta(minutes=30),  # well past grace, far from done
    )
    with patch("backend.app.services.print_scheduler.printer_manager") as mock_pm:
        mock_pm.get_status.return_value = _mock_state(dry_time=0)
        await scheduler._check_scheduled_dryings(db_session)
    await db_session.refresh(row)
    assert row.status == "pending"
    assert row.started_at is None
    assert row.waiting_reason == "interrupted"


@pytest.mark.asyncio
async def test_running_within_grace_untouched(scheduler, db_session, printer_factory):
    row = await _make_row(
        db_session,
        printer_factory,
        status="running",
        duration_hours=8,
        started_at=_utcnow_naive() - timedelta(seconds=30),  # inside 120 s grace
    )
    with patch("backend.app.services.print_scheduler.printer_manager") as mock_pm:
        mock_pm.get_status.return_value = _mock_state(dry_time=0)
        await scheduler._check_scheduled_dryings(db_session)
    await db_session.refresh(row)
    assert row.status == "running"
