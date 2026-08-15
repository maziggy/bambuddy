"""Tests for PrintScheduler scheduled-drying dispatch (#2638)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from backend.app.models.scheduled_drying import ScheduledDrying
from backend.app.services.print_scheduler import (
    SCHEDULED_DRYING_PRUNE_INTERVAL_SECONDS,
    SCHEDULED_DRYING_RETENTION_DAYS,
    PrintScheduler,
)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Above the X1C drying minimum, so the shared preflight lets dispatch through.
DRYING_CAPABLE_FIRMWARE = "01.09.00.00"


def _mock_state(ams_id=0, dry_time=0, dry_sf_reason=None, firmware=DRYING_CAPABLE_FIRMWARE):
    state = MagicMock()
    state.firmware_version = firmware
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
        mock_pm.get_status.return_value = _mock_state(dry_sf_reason=[2])
        await scheduler._check_scheduled_dryings(db_session)
    mock_pm.send_drying_command.assert_not_called()
    await db_session.refresh(row)
    assert row.status == "pending"
    assert row.waiting_reason == "ams_blocked"


@pytest.mark.asyncio
async def test_retract_block_gets_its_own_waiting_reason(scheduler, db_session, printer_factory):
    """Code 3 is user-actionable (retract the filament), so it says so rather
    than bucketing into the generic blocked message.
    """
    row = await _make_row(db_session, printer_factory, start_after=None)
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state(dry_sf_reason=[3])
        await scheduler._check_scheduled_dryings(db_session)
    mock_pm.send_drying_command.assert_not_called()
    await db_session.refresh(row)
    assert row.status == "pending"
    assert row.waiting_reason == "ams_retract_filament"


@pytest.mark.asyncio
async def test_power_block_outranks_retract(scheduler, db_session, printer_factory):
    """Both blocking at once: power is the one that has to be fixed first."""
    row = await _make_row(db_session, printer_factory, start_after=None)
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state(dry_sf_reason=[3, 8])
        await scheduler._check_scheduled_dryings(db_session)
    await db_session.refresh(row)
    assert row.waiting_reason == "ams_power_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [1, 8])
async def test_power_block_gets_its_own_waiting_reason(scheduler, db_session, printer_factory, code):
    """A run the user has to unblock says so, rather than waiting silently."""
    row = await _make_row(db_session, printer_factory, start_after=None)
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state(dry_sf_reason=[code])
        await scheduler._check_scheduled_dryings(db_session)
    mock_pm.send_drying_command.assert_not_called()
    await db_session.refresh(row)
    assert row.status == "pending"
    assert row.waiting_reason == "ams_power_required"


@pytest.mark.asyncio
async def test_screen_only_model_fails_instead_of_dispatching(scheduler, db_session, printer_factory):
    """A P1S acks the publish and ignores it; dispatching would silently self-cancel."""
    printer = await printer_factory(model="P1S")
    row = ScheduledDrying(printer_id=printer.id, ams_id=0, temp=65, duration_hours=8)
    db_session.add(row)
    await db_session.commit()

    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state()
        await scheduler._check_scheduled_dryings(db_session)

    mock_pm.send_drying_command.assert_not_called()
    await db_session.refresh(row)
    assert row.status == "failed"
    assert row.error_message
    assert row.completed_at is not None


@pytest.mark.asyncio
async def test_firmware_below_minimum_fails(scheduler, db_session, printer_factory):
    row = await _make_row(db_session, printer_factory, start_after=None)
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state(firmware="01.05.00.00")
        await scheduler._check_scheduled_dryings(db_session)

    mock_pm.send_drying_command.assert_not_called()
    await db_session.refresh(row)
    assert row.status == "failed"
    assert row.error_message
    assert row.completed_at is not None


@pytest.mark.asyncio
async def test_empty_filament_backfills_from_loaded_tray(scheduler, db_session, printer_factory):
    """Matches the immediate endpoint, which sends the loaded type rather than PLA."""
    row = await _make_row(db_session, printer_factory, start_after=None, filament="")
    state = _mock_state()
    state.raw_data["ams"][0]["tray"] = [{"tray_type": ""}, {"tray_type": "PETG"}]

    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = state
        mock_pm.send_drying_command.return_value = True
        await scheduler._check_scheduled_dryings(db_session)

    assert mock_pm.send_drying_command.call_args.kwargs["filament"] == "PETG"
    await db_session.refresh(row)
    assert row.filament == "PETG"


@pytest.mark.asyncio
async def test_empty_filament_falls_back_to_pla(scheduler, db_session, printer_factory):
    await _make_row(db_session, printer_factory, start_after=None, filament="")
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state()
        mock_pm.send_drying_command.return_value = True
        await scheduler._check_scheduled_dryings(db_session)

    assert mock_pm.send_drying_command.call_args.kwargs["filament"] == "PLA"


@pytest.mark.asyncio
async def test_finished_rows_pruned_after_retention(scheduler, db_session, printer_factory):
    printer = await printer_factory()
    stale = ScheduledDrying(
        printer_id=printer.id,
        ams_id=0,
        temp=65,
        duration_hours=8,
        status="completed",
        completed_at=_utcnow_naive() - timedelta(days=SCHEDULED_DRYING_RETENTION_DAYS + 1),
    )
    recent = ScheduledDrying(
        printer_id=printer.id,
        ams_id=0,
        temp=65,
        duration_hours=8,
        status="cancelled",
        completed_at=_utcnow_naive() - timedelta(hours=1),
    )
    db_session.add_all([stale, recent])
    await db_session.commit()

    with patch("backend.app.services.print_scheduler.printer_manager"):
        await scheduler._check_scheduled_dryings(db_session)

    remaining = (await db_session.execute(select(ScheduledDrying.id))).scalars().all()
    assert stale.id not in remaining
    assert recent.id in remaining


@pytest.mark.asyncio
async def test_prune_does_not_run_on_every_pass(scheduler, db_session, printer_factory):
    """The prune is throttled, because issuing the DELETE is what starts a
    write transaction and this method runs every 3s while the queue dispatches.
    Rows only become prunable a week after they finish, so nothing is lost by
    waiting an hour to reap them."""
    printer = await printer_factory()

    async def _stale_row() -> ScheduledDrying:
        row = ScheduledDrying(
            printer_id=printer.id,
            ams_id=0,
            temp=65,
            duration_hours=8,
            status="completed",
            completed_at=_utcnow_naive() - timedelta(days=SCHEDULED_DRYING_RETENTION_DAYS + 1),
        )
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    with patch("backend.app.services.print_scheduler.printer_manager"):
        # First pass after a restart always prunes, so rows left behind by the
        # process that died are still reaped.
        first = await _stale_row()
        await scheduler._check_scheduled_dryings(db_session)
        assert first.id not in (await db_session.execute(select(ScheduledDrying.id))).scalars().all()

        # A second pass moments later leaves an equally stale row alone.
        second = await _stale_row()
        await scheduler._check_scheduled_dryings(db_session)
        assert second.id in (await db_session.execute(select(ScheduledDrying.id))).scalars().all()

        # ...and reaps it once the interval has elapsed.
        scheduler._last_scheduled_drying_prune -= SCHEDULED_DRYING_PRUNE_INTERVAL_SECONDS
        await scheduler._check_scheduled_dryings(db_session)
        assert second.id not in (await db_session.execute(select(ScheduledDrying.id))).scalars().all()


@pytest.mark.asyncio
async def test_a_finished_run_releases_the_printer_without_auto_drying(scheduler, db_session, printer_factory):
    """_drying_in_progress must not outlive the run that set it.

    Auto-drying's _sync_drying_state() prunes that map, but it sits behind the
    enabled check, so on a default install (both auto-drying modes off) it never
    runs. Nothing else drops the entry unless a print is dispatched to the same
    printer, and a nightly off-peak dry with no printing in between is exactly
    the case this feature is for: the second night's run would sit on
    "already_drying" forever, and with queue_drying_block on the printer would
    stop taking prints as well.
    """
    row = await _make_row(
        db_session, printer_factory, start_after=_utcnow_naive() - timedelta(minutes=1), duration_hours=1
    )
    printer_id = row.printer_id

    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state()
        mock_pm.send_drying_command.return_value = True
        await scheduler._check_scheduled_dryings(db_session)
    await db_session.refresh(row)
    assert row.status == "running"
    assert printer_id in scheduler._drying_in_progress

    # The firmware has stopped reporting a dry_time, well past the duration.
    row.started_at = _utcnow_naive() - timedelta(hours=2)
    await db_session.commit()
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state(dry_time=0)
        await scheduler._check_scheduled_dryings(db_session)
    await db_session.refresh(row)
    assert row.status == "completed"
    assert printer_id not in scheduler._drying_in_progress

    # And the next night's run still dispatches.
    tomorrow = ScheduledDrying(
        printer_id=printer_id,
        ams_id=0,
        temp=65,
        duration_hours=8,
        start_after=_utcnow_naive() - timedelta(minutes=1),
    )
    db_session.add(tomorrow)
    await db_session.commit()
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state()
        mock_pm.send_drying_command.return_value = True
        await scheduler._check_scheduled_dryings(db_session)
    await db_session.refresh(tomorrow)
    assert tomorrow.status == "running"
    assert tomorrow.waiting_reason is None


@pytest.mark.asyncio
async def test_a_route_cancel_releases_the_printer(scheduler, db_session, printer_factory):
    """The DELETE route flips the row to cancelled in the database and sends the
    stop, but knows nothing about the scheduler's in-memory map. The next pass
    has to notice the run is gone and release the printer, or it stays marked as
    drying until a restart."""
    row = await _make_row(db_session, printer_factory, start_after=_utcnow_naive() - timedelta(minutes=1))
    printer_id = row.printer_id

    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state()
        mock_pm.send_drying_command.return_value = True
        await scheduler._check_scheduled_dryings(db_session)
    assert printer_id in scheduler._drying_in_progress

    # What DELETE /scheduled-dryings/{id} leaves behind.
    row.status = "cancelled"
    row.completed_at = _utcnow_naive()
    await db_session.commit()

    with patch("backend.app.services.print_scheduler.printer_manager") as mock_pm:
        mock_pm.get_status.return_value = _mock_state()
        await scheduler._check_scheduled_dryings(db_session)

    assert printer_id not in scheduler._drying_in_progress
    assert printer_id not in scheduler._scheduled_drying_printer_ids


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
async def test_running_interrupted_by_print_requeues(scheduler, db_session, printer_factory):
    row = await _make_row(
        db_session,
        printer_factory,
        status="running",
        duration_hours=8,
        started_at=_utcnow_naive() - timedelta(minutes=30),  # well past grace, far from done
    )
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=False),
    ):
        mock_pm.get_status.return_value = _mock_state(dry_time=0)
        await scheduler._check_scheduled_dryings(db_session)
    await db_session.refresh(row)
    assert row.status == "pending"
    assert row.started_at is None
    assert row.waiting_reason == "interrupted"


@pytest.mark.asyncio
async def test_running_stopped_while_idle_cancels(scheduler, db_session, printer_factory):
    """A stop on an idle printer is deliberate; the row must not resurrect."""
    row = await _make_row(
        db_session,
        printer_factory,
        status="running",
        duration_hours=8,
        started_at=_utcnow_naive() - timedelta(minutes=30),
    )
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state(dry_time=0)
        await scheduler._check_scheduled_dryings(db_session)
    await db_session.refresh(row)
    assert row.status == "cancelled"
    assert row.completed_at is not None


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


@pytest.mark.asyncio
async def test_scheduled_drying_survives_auto_drying_stop_all(scheduler, db_session, printer_factory):
    """Regression (#2638): a running scheduled drying must not be stopped or
    untracked by _check_auto_drying's stop-all branch, even in the default
    config where both auto-drying toggles are off. Before the fix, the two
    features co-owned _drying_in_progress and auto-drying would stop/pop any
    printer it didn't start drying on itself.
    """
    row = await _make_row(db_session, printer_factory, start_after=_utcnow_naive() - timedelta(minutes=1))
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state()
        mock_pm.send_drying_command.return_value = True
        await scheduler._check_scheduled_dryings(db_session)

        await db_session.refresh(row)
        assert row.status == "running"
        assert scheduler._drying_in_progress.get(row.printer_id)
        assert row.printer_id in scheduler._scheduled_drying_printer_ids

        mock_pm.reset_mock()
        with patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=False)):
            # Default config: queue_drying_enabled and ambient_drying_enabled both off.
            await scheduler._check_auto_drying(db_session, [], set())

    mock_pm.send_drying_command.assert_not_called()
    await db_session.refresh(row)
    assert row.status == "running"
    assert scheduler._drying_in_progress.get(row.printer_id)
    assert row.printer_id in scheduler._scheduled_drying_printer_ids


@pytest.mark.asyncio
async def test_second_pending_row_for_same_printer_does_not_dispatch(scheduler, db_session, printer_factory):
    """Regression (#2638): two pending rows for the same printer must not both
    dispatch in the same tick; the second should see the first's dispatch and
    stay pending.
    """
    printer = await printer_factory()
    past = _utcnow_naive() - timedelta(minutes=1)
    row1 = ScheduledDrying(printer_id=printer.id, ams_id=0, temp=65, duration_hours=8, start_after=past)
    row2 = ScheduledDrying(printer_id=printer.id, ams_id=0, temp=60, duration_hours=6, start_after=past)
    db_session.add_all([row1, row2])
    await db_session.commit()
    await db_session.refresh(row1)
    await db_session.refresh(row2)

    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state()
        mock_pm.send_drying_command.return_value = True
        await scheduler._check_scheduled_dryings(db_session)

    mock_pm.send_drying_command.assert_called_once()
    await db_session.refresh(row1)
    await db_session.refresh(row2)
    # Ordered dispatch: same start_after, so the row created first wins.
    assert row1.status == "running"
    assert row2.status == "pending"
    assert row2.waiting_reason == "already_drying"


@pytest.mark.asyncio
async def test_earliest_start_after_dispatches_first(scheduler, db_session, printer_factory):
    """Two rows due on one printer: the earlier schedule starts, not an arbitrary one."""
    printer = await printer_factory()
    now = _utcnow_naive()
    later = ScheduledDrying(
        printer_id=printer.id, ams_id=0, temp=65, duration_hours=8, start_after=now - timedelta(minutes=1)
    )
    sooner = ScheduledDrying(
        printer_id=printer.id, ams_id=0, temp=60, duration_hours=6, start_after=now - timedelta(hours=3)
    )
    # Inserted later-first so row order alone cannot produce the right answer.
    db_session.add_all([later, sooner])
    await db_session.commit()
    await db_session.refresh(later)
    await db_session.refresh(sooner)

    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=True),
    ):
        mock_pm.get_status.return_value = _mock_state()
        mock_pm.send_drying_command.return_value = True
        await scheduler._check_scheduled_dryings(db_session)

    mock_pm.send_drying_command.assert_called_once_with(printer.id, 0, 60, 6, mode=1, filament="PLA", rotate_tray=False)
    await db_session.refresh(later)
    await db_session.refresh(sooner)
    assert sooner.status == "running"
    assert later.status == "pending"


@pytest.mark.asyncio
async def test_malformed_ams_id_does_not_throw_while_running(scheduler, db_session, printer_factory):
    """_update_running_scheduled_drying runs inside check_queue: a throw here
    would cost the whole pass, print dispatch included, on every tick.
    """
    row = await _make_row(
        db_session,
        printer_factory,
        status="running",
        started_at=_utcnow_naive() - timedelta(minutes=30),
    )
    state = MagicMock()
    state.firmware_version = DRYING_CAPABLE_FIRMWARE
    state.raw_data = {"ams": [{"id": "not-a-number", "dry_time": 120}]}

    with (
        patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
        patch.object(scheduler, "_is_printer_idle", return_value=False),
    ):
        mock_pm.get_status.return_value = state
        await scheduler._check_scheduled_dryings(db_session)

    # No matching unit means no dry_time; the printer is busy, so it re-queues.
    await db_session.refresh(row)
    assert row.status == "pending"
    assert row.waiting_reason == "interrupted"
