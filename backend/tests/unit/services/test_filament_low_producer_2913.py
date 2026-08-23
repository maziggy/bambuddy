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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.print_scheduler import PrintScheduler, _ams_slot_label, _remaining_percent


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


def test_slot_labels_cover_ams_ht_and_external():
    assert _ams_slot_label(0, 0) == "AMS-A T1"
    assert _ams_slot_label(1, 3) == "AMS-B T4"
    assert _ams_slot_label(128, 0) == "HT-A"
    assert _ams_slot_label(255, 0) == "External"


# -- the producer -----------------------------------------------------------


@pytest.fixture
def scheduler():
    return PrintScheduler()


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

    await scheduler._check_filament_low(db_session)

    notify.on_filament_low.assert_awaited_once()
    args = notify.on_filament_low.await_args.args
    assert args[0] == printer.id
    assert args[1] == "X2D"
    assert args[2] == "AMS-A T1"
    assert args[3] == 15


@pytest.mark.asyncio
async def test_stays_quiet_above_the_threshold(db_session, scheduler, notify):
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=500.0)  # 50% left
    await db_session.commit()

    await scheduler._check_filament_low(db_session)

    notify.on_filament_low.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_slot_with_no_assigned_spool_produces_nothing(db_session, scheduler, notify):
    """Guessing a remaining weight for an unassigned slot is how the AMS remain
    percentage would have got back in."""
    await _printer(db_session)
    await db_session.commit()

    await scheduler._check_filament_low(db_session)

    notify.on_filament_low.assert_not_awaited()


@pytest.mark.asyncio
async def test_does_not_repeat_on_the_next_pass(db_session, scheduler, notify):
    """The debounce. A spool sitting under the threshold must not alert every
    30 seconds until it is changed."""
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=850.0)
    await db_session.commit()

    await scheduler._check_filament_low(db_session)
    await scheduler._check_filament_low(db_session)
    await scheduler._check_filament_low(db_session)

    assert notify.on_filament_low.await_count == 1


@pytest.mark.asyncio
async def test_re_arms_once_the_slot_goes_back_above_the_threshold(db_session, scheduler, notify):
    """Cleared by the value going back up, not by a timer -- so a refilled slot
    can alert again, and a spool hovering at the boundary cannot spam."""
    printer = await _printer(db_session)
    spool = await _assigned_spool(db_session, printer, weight_used=850.0)
    await db_session.commit()

    await scheduler._check_filament_low(db_session)
    assert notify.on_filament_low.await_count == 1

    # Refilled — a fresh spool put on the same slot.
    spool.weight_used = 0.0
    await db_session.commit()
    await scheduler._check_filament_low(db_session)
    assert notify.on_filament_low.await_count == 1

    # And down again.
    spool.weight_used = 900.0
    await db_session.commit()
    await scheduler._check_filament_low(db_session)
    assert notify.on_filament_low.await_count == 2


@pytest.mark.asyncio
async def test_debounce_is_per_slot_not_per_printer(db_session, scheduler, notify):
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=850.0, ams=0, tray=0)
    await _assigned_spool(db_session, printer, weight_used=900.0, ams=0, tray=1)
    await db_session.commit()

    await scheduler._check_filament_low(db_session)

    assert notify.on_filament_low.await_count == 2
    slots = {call.args[2] for call in notify.on_filament_low.await_args_list}
    assert slots == {"AMS-A T1", "AMS-A T2"}


@pytest.mark.asyncio
async def test_per_spool_override_beats_the_global_threshold(db_session, scheduler, notify):
    """A spool marked as needing 60% left alerts at 50%, where the global 20%
    would have stayed quiet. Same precedence the Inventory page uses."""
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=500.0, threshold_pct=60)
    await db_session.commit()

    await scheduler._check_filament_low(db_session)

    notify.on_filament_low.assert_awaited_once()
    assert notify.on_filament_low.await_args.args[3] == 50


@pytest.mark.asyncio
async def test_global_setting_is_honoured(db_session, scheduler, notify):
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=600.0)  # 40% left
    db_session.add(Settings(key="low_stock_threshold", value="50"))
    await db_session.commit()

    await scheduler._check_filament_low(db_session)

    notify.on_filament_low.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failing_provider_does_not_break_the_pass(db_session, scheduler, notify):
    """A notification provider being down must not take the scheduler with it."""
    printer = await _printer(db_session)
    await _assigned_spool(db_session, printer, weight_used=850.0)
    await db_session.commit()
    notify.on_filament_low.side_effect = RuntimeError("provider down")

    await scheduler._check_filament_low(db_session)  # must not raise

    notify.on_filament_low.assert_awaited_once()


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
