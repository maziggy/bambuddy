"""The "Low Filament" notification has a producer now (issue #2913).

Every layer of this feature existed -- a column on notification_providers, a
schema field, a route, a message template, thirteen locales and a UI toggle --
except the thing that fires it. `_get_providers_for_event(db, "on_filament_low")`
appeared only inside the method that would be called, so the toggle could be
switched on and could never produce a notification.

The reason it shipped is visible in the old test file: the only occurrence of
`on_filament_low` in test_notification_service.py is a fixture setting the flag
to False. Nothing ever asserted the event reaches a provider. The last test here
does exactly that.
"""

import logging
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.print_scheduler import PrintScheduler, _ams_slot_label, _remaining_percent
from backend.app.services.spoolman import SpoolmanUnavailableError


async def _printer(db, name: str = "X2D") -> Printer:
    p = Printer(name=name, serial_number=f"S-{name}", ip_address="1.1.1.1", access_code="c", model="X2D")
    db.add(p)
    await db.flush()
    return p


async def _assigned_spool(db, printer, *, weight_used: float, threshold_pct: int | None = None, ams=0, tray=0):
    spool = Spool(
        material="PLA",
        label_weight=1000,
        core_weight=250,
        weight_used=weight_used,
        low_stock_threshold_pct=threshold_pct,
    )
    spool.k_profiles = []
    spool.assignments = []
    db.add(spool)
    await db.flush()
    db.add(SpoolAssignment(printer_id=printer.id, spool_id=spool.id, ams_id=ams, tray_id=tray))
    await db.flush()
    return spool


# -- the arithmetic ---------------------------------------------------------


def test_remaining_percent_matches_the_inventory_page_rule():
    """`remaining = label - used`, as a percentage of label. The same expression
    the Low Stock count uses, so the alert and the badge cannot disagree."""
    assert _remaining_percent(1000, 850.0) == pytest.approx(15.0)
    assert _remaining_percent(1000, 0.0) == pytest.approx(100.0)


def test_remaining_percent_is_none_without_a_label_weight():
    """A spool that cannot say how full it started cannot say how empty it is."""
    assert _remaining_percent(0, 100.0) is None
    assert _remaining_percent(None, 100.0) is None


def test_remaining_percent_floors_at_zero_when_overdrawn():
    assert _remaining_percent(1000, 1200.0) == pytest.approx(0.0)


def test_slot_labels_are_the_ones_the_notification_side_already_uses():
    """Every kind of slot, against the spelling the rest of the app produces.

    The four cases after the first two are the ones the hand-rolled version got
    wrong: 254 is a real ams_id (InventoryPage.tsx:286, usage_tracker.py:55) and
    fell into its ``>= 128`` branch as ``HT-`` plus ``chr(191)``; an external
    assignment stores 255 with tray_id choosing the side, so both externals came
    out as one label and a two-external printer could not tell them apart; and
    an A2L AMS-Lite normalises to unit 6, which read as ``AMS-G``.
    """
    assert _ams_slot_label(0, 0) == "A1"
    assert _ams_slot_label(1, 3) == "B4"
    assert _ams_slot_label(128, 0) == "HT-A"
    assert _ams_slot_label(254, 0) == "Ext-L"
    assert _ams_slot_label(255, 0) == "Ext-L"
    assert _ams_slot_label(255, 1) == "Ext-R"
    assert _ams_slot_label(6, 0) == "Lite-1"


# -- the producer -----------------------------------------------------------


@pytest.fixture
def scheduler():
    return PrintScheduler()


async def _pass(scheduler, db):
    """Run one low-filament check, stepping over the inter-pass time gate.

    ``_check_filament_low`` refuses to run again within
    ``_FILAMENT_LOW_MIN_INTERVAL``, so consecutive calls in a test would be
    swallowed by the gate and every multi-pass assertion below would pass
    without exercising what it names. The gate has its own test.
    """
    scheduler._filament_low_next_check = 0.0
    await scheduler._check_filament_low(db)


@pytest.fixture
def notify():
    with patch("backend.app.services.print_scheduler.notification_service") as ns:
        ns.on_filament_low = AsyncMock()
        yield ns


@pytest.mark.asyncio
async def test_fires_when_an_assigned_spool_is_below_the_threshold(db_session, scheduler, notify):
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=850.0)  # 15% left, default threshold 20
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_filament_low.assert_awaited_once()
    args = notify.on_filament_low.await_args.args
    assert args[0] == printer.id
    assert args[1] == "X2D"
    assert args[2] == "A1"
    assert args[3] == 15


@pytest.mark.asyncio
async def test_stays_quiet_above_the_threshold(db_session, scheduler, notify):
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=500.0)  # 50% left
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_filament_low.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_slot_with_no_assigned_spool_produces_nothing(db_session, scheduler, notify):
    """Guessing a remaining weight for an unassigned slot is how the AMS remain
    percentage would have got back in."""
    await _printer(db_session)
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_filament_low.assert_not_awaited()


@pytest.mark.asyncio
async def test_does_not_repeat_on_the_next_pass(db_session, scheduler, notify):
    """The debounce. A spool sitting under the threshold must not alert every
    30 seconds until it is changed."""
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=850.0)
    await db_session.commit()

    await _pass(scheduler, db_session)
    await _pass(scheduler, db_session)
    await _pass(scheduler, db_session)

    assert notify.on_filament_low.await_count == 1


@pytest.mark.asyncio
async def test_re_arms_once_the_slot_goes_back_above_the_threshold(db_session, scheduler, notify):
    """Cleared by the value going back up, not by a timer -- so a refilled slot
    can alert again, and a spool hovering at the boundary cannot spam."""
    printer = await _printer(db_session)
    spool = await _assigned_spool(db_session, printer, weight_used=850.0)
    await db_session.commit()

    await _pass(scheduler, db_session)
    assert notify.on_filament_low.await_count == 1

    # Refilled — a fresh spool put on the same slot.
    spool.weight_used = 0.0
    await db_session.commit()
    await _pass(scheduler, db_session)
    assert notify.on_filament_low.await_count == 1

    # And down again.
    spool.weight_used = 900.0
    await db_session.commit()
    await _pass(scheduler, db_session)
    assert notify.on_filament_low.await_count == 2


@pytest.mark.asyncio
async def test_debounce_is_per_slot_not_per_printer(db_session, scheduler, notify):
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=850.0, ams=0, tray=0)
    await _assigned_spool(db_session, printer, weight_used=900.0, ams=0, tray=1)
    await db_session.commit()

    await _pass(scheduler, db_session)

    assert notify.on_filament_low.await_count == 2
    slots = {call.args[2] for call in notify.on_filament_low.await_args_list}
    assert slots == {"A1", "A2"}


@pytest.mark.asyncio
async def test_per_spool_override_beats_the_global_threshold(db_session, scheduler, notify):
    """A spool marked as needing 60% left alerts at 50%, where the global 20%
    would have stayed quiet. Same precedence the Inventory page uses."""
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=500.0, threshold_pct=60)
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_filament_low.assert_awaited_once()
    assert notify.on_filament_low.await_args.args[3] == 50


@pytest.mark.asyncio
async def test_global_setting_is_honoured(db_session, scheduler, notify):
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=600.0)  # 40% left
    db_session.add(Settings(key="low_stock_threshold", value="50"))
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_filament_low.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failing_provider_does_not_break_the_pass(db_session, scheduler, notify):
    """A notification provider being down must not take the scheduler with it."""
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=850.0)
    await db_session.commit()
    notify.on_filament_low.side_effect = RuntimeError("provider down")

    await _pass(scheduler, db_session)  # must not raise

    notify.on_filament_low.assert_awaited_once()


# -- the pass is time-gated -------------------------------------------------


@pytest.mark.asyncio
async def test_a_second_pass_inside_the_interval_does_no_work(db_session, scheduler, notify):
    """The gate, which every multi-pass test above steps over deliberately.

    run() sleeps _fast_check_interval -- 3 seconds -- on any productive pass,
    and the early-return path counts as productive while an upload is in flight
    (#2602). Ungated, this check would re-read every assigned spool on that
    cadence for the length of a batch drain, which in Spoolman mode means the
    whole collection over HTTP against a third-party service.
    """
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=850.0)
    await db_session.commit()

    with patch.object(scheduler, "_get_low_stock_threshold", new_callable=AsyncMock) as work:
        work.return_value = 20.0
        await scheduler._check_filament_low(db_session)
        await scheduler._check_filament_low(db_session)
        await scheduler._check_filament_low(db_session)

    # Not "no notification" -- the debounce would give that too. No work at all.
    assert work.await_count == 1


@pytest.mark.asyncio
async def test_an_archived_spool_is_not_stock(db_session, scheduler, notify):
    """Archiving a spool takes it out of the Low Stock count (InventoryPage.tsx:1089).

    Spoolman mode gets this for free -- get_all_spools without allow_archived
    does not return them -- so without the explicit filter the internal path
    would be the only mode that alerts on a spool the user has retired.
    """
    printer = await _printer(db_session)
    spool = await _assigned_spool(db_session, printer, weight_used=850.0)
    spool.archived_at = datetime(2026, 8, 1, 12, 0, 0)
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_filament_low.assert_not_awaited()


# -- Spoolman mode ----------------------------------------------------------


def _spoolman_spool(spool_id: int, remaining_weight: float, label_weight: int = 1000) -> dict:
    """A raw Spoolman spool, in the shape _map_spoolman_spool reads."""
    return {
        "id": spool_id,
        "remaining_weight": remaining_weight,
        "used_weight": label_weight - remaining_weight,
        "filament": {"id": 1, "name": "PLA Basic", "material": "PLA", "weight": label_weight, "vendor": {"name": "X"}},
    }


async def _spoolman_slot(db, printer, spool_id: int, *, ams=0, tray=0):
    db.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=ams, tray_id=tray, spoolman_spool_id=spool_id))
    db.add(Settings(key="spoolman_enabled", value="true"))
    await db.flush()


def _spoolman_client(spools, *, unavailable: bool = False):
    client = MagicMock()
    if unavailable:
        client.get_all_spools = AsyncMock(side_effect=SpoolmanUnavailableError("Cannot reach Spoolman"))
    else:
        client.get_all_spools = AsyncMock(return_value=spools)
    return client


@pytest.mark.asyncio
async def test_spoolman_mode_alerts_from_the_collection_read(db_session, scheduler, notify):
    """The half with the client in it, which had no coverage at all.

    The threshold is meant to mean the same thing in both inventory modes, so
    the mode that reaches a third-party service for its numbers needs its own
    tests rather than inheriting the internal path's.
    """
    printer = await _printer(db_session)
    await _spoolman_slot(db_session, printer, 7, ams=0, tray=1)
    await db_session.commit()
    client = _spoolman_client([_spoolman_spool(7, remaining_weight=150.0)])  # 15% left

    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)):
        await _pass(scheduler, db_session)

    notify.on_filament_low.assert_awaited_once()
    args = notify.on_filament_low.await_args.args
    assert args[2] == "A2"
    assert args[3] == 15
    # One request for the whole collection, not one per assigned slot.
    client.get_all_spools.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_spoolman_slot_whose_spool_is_gone_is_skipped(db_session, scheduler, notify):
    """A spool deleted in Spoolman leaves the assignment row behind. It has no
    remaining weight, so it produces nothing rather than a guess."""
    printer = await _printer(db_session)
    await _spoolman_slot(db_session, printer, 7)
    await db_session.commit()
    client = _spoolman_client([_spoolman_spool(99, remaining_weight=150.0)])

    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)):
        await _pass(scheduler, db_session)

    notify.on_filament_low.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unreachable_spoolman_resolves_nothing_and_stays_quiet(db_session, scheduler, notify, caplog):
    """An outage is an ordinary state for a third-party service, not an error here.

    The assertion is the log, not the silence. Letting SpoolmanUnavailableError
    reach _check_filament_low's broad except produces the same absence of
    notifications -- so "nothing was sent" cannot tell the two apart -- while
    writing a full traceback on every pass for the length of the outage. What
    the catch buys is that it is handled where it is expected.
    """
    printer = await _printer(db_session)
    await _spoolman_slot(db_session, printer, 7)
    await db_session.commit()
    client = _spoolman_client(None, unavailable=True)

    with (
        caplog.at_level(logging.WARNING, logger="backend.app.services.print_scheduler"),
        patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)),
    ):
        await _pass(scheduler, db_session)  # must not raise

    notify.on_filament_low.assert_not_awaited()
    assert "Low-filament check failed" not in caplog.text


@pytest.mark.asyncio
async def test_an_outage_does_not_re_alert_everything_when_it_clears(db_session, scheduler, notify):
    """The reason a pass that resolves nothing leaves the notified set alone.

    Clearing the set on an empty pass would make a brief Spoolman outage
    indistinguishable from every spool being refilled, and the alerts would all
    fire again the moment it came back.
    """
    printer = await _printer(db_session)
    await _spoolman_slot(db_session, printer, 7)
    await db_session.commit()
    low = [_spoolman_spool(7, remaining_weight=150.0)]

    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=_spoolman_client(low))):
        await _pass(scheduler, db_session)
    assert notify.on_filament_low.await_count == 1

    outage = _spoolman_client(None, unavailable=True)
    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=outage)):
        await _pass(scheduler, db_session)

    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=_spoolman_client(low))):
        await _pass(scheduler, db_session)
    assert notify.on_filament_low.await_count == 1


@pytest.mark.asyncio
async def test_a_replacement_spool_in_the_same_slot_can_alert(db_session, scheduler, notify):
    """Why the notified key carries the spool id.

    Pull a low spool and put a different part-used one in the same slot. The
    key survives the pass where the slot resolves to nothing -- deliberately --
    and the replacement never goes above the threshold, so keyed on the slot
    alone it could never re-arm and the second spool would run out silently.
    """
    printer = await _printer(db_session)
    await _spoolman_slot(db_session, printer, 7)
    await db_session.commit()

    first = _spoolman_client([_spoolman_spool(7, remaining_weight=150.0)])
    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=first)):
        await _pass(scheduler, db_session)
    assert notify.on_filament_low.await_count == 1

    # The slot resolves to nothing for a pass while the roll is swapped.
    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=_spoolman_client([]))):
        await _pass(scheduler, db_session)

    assignment = (await db_session.execute(select(SpoolmanSlotAssignment))).scalars().one()
    assignment.spoolman_spool_id = 8
    await db_session.commit()

    second = _spoolman_client([_spoolman_spool(8, remaining_weight=100.0)])  # 10% left
    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=second)):
        await _pass(scheduler, db_session)

    assert notify.on_filament_low.await_count == 2
    assert notify.on_filament_low.await_args.args[3] == 10


# -- the event actually reaches a provider ----------------------------------


@pytest.mark.asyncio
async def test_the_event_reaches_a_provider_with_the_toggle_on():
    """The assertion that was missing, and the reason this shipped unfired.

    Everything above proves the producer calls the service. This proves the
    service does not drop it: a provider with on_filament_low enabled is handed
    the rendered message.
    """
    from backend.app.services.notification_service import NotificationService

    service = NotificationService()
    provider = MagicMock()
    provider.id = 1
    provider.on_filament_low = True

    with (
        patch.object(service, "_get_providers_for_event", new_callable=AsyncMock) as mock_get,
        patch.object(service, "_send_to_providers", new_callable=AsyncMock) as mock_send,
        patch.object(service, "_build_message_from_template", new_callable=AsyncMock) as mock_build,
    ):
        mock_get.return_value = [provider]
        mock_build.return_value = ("Filament Low", "X2D AMS-A T1 is at 15%")

        await service.on_filament_low(1, "X2D", "AMS-A T1", 15, AsyncMock())

    mock_get.assert_awaited_once()
    assert mock_get.await_args.args[1] == "on_filament_low"
    mock_send.assert_awaited_once()
    variables = mock_send.await_args.kwargs["variables"]
    assert variables["printer"] == "X2D"
    assert variables["slot"] == "AMS-A T1"
    assert variables["remaining_percent"] == "15"
