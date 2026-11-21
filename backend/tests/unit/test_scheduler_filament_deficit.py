"""Scheduler pre-dispatch filament-deficit guard tests (#1496).

``PrintScheduler._block_on_filament_deficit`` is the gate that keeps an
auto_dispatch=True VP intake (or any other scheduler-driven dispatch) from
sending a print onto a spool that can't satisfy it. On a deficit it
promotes the item to manual_start; when a previously-flagged item's spool
is now adequate it clears the flag so the next tick dispatches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.filament_deficit import FilamentDeficit
from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
def scheduler():
    """A fresh scheduler instance — internal state is not exercised."""
    return PrintScheduler()


@pytest.fixture
def queue_item(db_session, printer_factory):
    """Helper to drop a queue item the helper can mutate."""

    async def _make(**overrides):
        printer = await printer_factory()
        defaults = {
            "printer_id": printer.id,
            "status": "pending",
            "manual_start": False,
            "filament_short": False,
        }
        defaults.update(overrides)
        item = PrintQueueItem(**defaults)
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(item)
        return item

    return _make


@pytest.mark.asyncio
async def test_blocks_on_deficit_promotes_to_manual_start(scheduler, db_session, queue_item):
    item = await queue_item()
    with patch(
        "backend.app.services.print_scheduler.compute_deficit_for_queue_item",
        AsyncMock(
            return_value=[
                FilamentDeficit(
                    slot_id=1,
                    ams_id=0,
                    tray_id=0,
                    filament_type="PLA",
                    required_grams=270.0,
                    remaining_grams=200.0,
                ),
            ]
        ),
    ):
        blocked = await scheduler._block_on_filament_deficit(db_session, item)

    assert blocked is True
    await db_session.refresh(item)
    assert item.manual_start is True
    assert item.filament_short is True


@pytest.mark.asyncio
async def test_clears_stale_flag_when_deficit_resolves(scheduler, db_session, queue_item):
    """Previously-flagged item whose spool was swapped is unblocked."""
    item = await queue_item(filament_short=True, manual_start=False)
    with patch(
        "backend.app.services.print_scheduler.compute_deficit_for_queue_item",
        AsyncMock(return_value=[]),
    ):
        blocked = await scheduler._block_on_filament_deficit(db_session, item)

    assert blocked is False
    await db_session.refresh(item)
    assert item.filament_short is False
    assert item.manual_start is False


@pytest.mark.asyncio
async def test_no_deficit_no_op(scheduler, db_session, queue_item):
    """Happy path — no deficit, no flag changes, dispatch proceeds."""
    item = await queue_item()
    with patch(
        "backend.app.services.print_scheduler.compute_deficit_for_queue_item",
        AsyncMock(return_value=[]),
    ):
        blocked = await scheduler._block_on_filament_deficit(db_session, item)

    assert blocked is False
    await db_session.refresh(item)
    assert item.filament_short is False
    assert item.manual_start is False


@pytest.mark.asyncio
async def test_helper_exception_does_not_wedge_dispatch(scheduler, db_session, queue_item):
    """A flaky deficit check (e.g. Spoolman timeout) must not block dispatch."""
    item = await queue_item()
    with patch(
        "backend.app.services.print_scheduler.compute_deficit_for_queue_item",
        AsyncMock(side_effect=RuntimeError("network down")),
    ):
        blocked = await scheduler._block_on_filament_deficit(db_session, item)

    assert blocked is False
    await db_session.refresh(item)
    assert item.filament_short is False


@pytest.mark.asyncio
async def test_skip_filament_check_short_circuits_without_compute(scheduler, db_session, queue_item):
    """User clicked Print Anyway (skip_filament_check=True): no compute, no flag (#1698-followup).

    Pre-fix the scheduler re-ran the deficit check on every tick, re-set
    manual_start/filament_short to True, and the item bounced between
    "user said anyway" (route clears flags) and "scheduler re-blocked"
    forever. With the persistent acknowledgement flag the scheduler bails
    early without even touching the deficit helper.
    """
    item = await queue_item(skip_filament_check=True)
    compute_mock = AsyncMock(
        return_value=[
            FilamentDeficit(
                slot_id=1,
                ams_id=0,
                tray_id=0,
                filament_type="PLA",
                required_grams=270.0,
                remaining_grams=200.0,
            ),
        ]
    )
    with patch(
        "backend.app.services.print_scheduler.compute_deficit_for_queue_item",
        compute_mock,
    ):
        blocked = await scheduler._block_on_filament_deficit(db_session, item)

    assert blocked is False
    compute_mock.assert_not_awaited()
    await db_session.refresh(item)
    # Flags must not get re-set by the scheduler now that the user has
    # acknowledged the deficit.
    assert item.filament_short is False
    assert item.manual_start is False


@pytest.mark.asyncio
async def test_the_waiting_notification_names_the_printer(scheduler, db_session, queue_item):
    """A channel limited to the job's printer is told the job is short."""
    item = await queue_item()
    waiting = AsyncMock()
    deficit = FilamentDeficit(
        slot_id=1, ams_id=0, tray_id=0, filament_type="PLA", required_grams=270.0, remaining_grams=200.0
    )
    with (
        patch("backend.app.services.print_scheduler.compute_deficit_for_queue_item", AsyncMock(return_value=[deficit])),
        patch("backend.app.services.notification_service.notification_service.on_queue_job_waiting", waiting),
    ):
        await scheduler._block_on_filament_deficit(db_session, item)

    assert waiting.await_args.kwargs["printer_id"] == item.printer_id


def _deficit(ams_id, tray_id, remaining, required=40.0, material="PLA"):
    return FilamentDeficit(
        slot_id=1,
        ams_id=ams_id,
        tray_id=tray_id,
        filament_type=material,
        required_grams=required,
        remaining_grams=remaining,
    )


async def _waiting_reason_sent(scheduler, db_session, item, deficits):
    """The waiting_reason the notification gets for ``deficits`` (#3642)."""
    notify = AsyncMock()
    with (
        patch("backend.app.services.print_scheduler.compute_deficit_for_queue_item", AsyncMock(return_value=deficits)),
        patch("backend.app.services.print_scheduler.notification_service.on_queue_job_waiting", notify),
    ):
        assert await scheduler._block_on_filament_deficit(db_session, item) is True
    return notify.await_args.kwargs["waiting_reason"]


@pytest.mark.asyncio
async def test_notification_reason_is_a_sentence_not_the_code(scheduler, db_session, queue_item):
    item = await queue_item()
    reason = await _waiting_reason_sent(scheduler, db_session, item, [_deficit(0, 1, 12.0)])

    assert reason == "Not enough filament on Test Printer: PLA in A2 (12 g left, needs 40 g)"
    assert "filament_short" not in reason


@pytest.mark.asyncio
async def test_notification_reason_with_unknown_grams(scheduler, db_session, queue_item):
    item = await queue_item()
    reason = await _waiting_reason_sent(scheduler, db_session, item, [_deficit(0, 0, None)])

    assert reason == "Not enough filament on Test Printer: PLA in A1 (needs 40 g)"


@pytest.mark.asyncio
async def test_notification_reason_joins_several_deficits(scheduler, db_session, queue_item):
    item = await queue_item()
    reason = await _waiting_reason_sent(
        scheduler, db_session, item, [_deficit(0, 1, 12.0), _deficit(1, 3, None, 90.0, "PETG")]
    )

    assert reason == ("Not enough filament on Test Printer: PLA in A2 (12 g left, needs 40 g), PETG in B4 (needs 90 g)")


@pytest.mark.asyncio
async def test_notification_reason_names_ht_and_external_slots(scheduler, db_session, queue_item):
    item = await queue_item()
    reason = await _waiting_reason_sent(
        scheduler, db_session, item, [_deficit(128, 0, 5.0), _deficit(255, 1, 8.0, material="PETG")]
    )

    assert "PLA in HT-A (5 g left, needs 40 g)" in reason
    assert "PETG in Ext-R (8 g left, needs 40 g)" in reason


def test_describe_deficit_for_notification_without_slot_or_printer():
    from backend.app.services.print_scheduler import _describe_deficit_for_notification

    assert (
        _describe_deficit_for_notification([_deficit(None, None, 12.0)])
        == "Not enough filament: PLA (12 g left, needs 40 g)"
    )
