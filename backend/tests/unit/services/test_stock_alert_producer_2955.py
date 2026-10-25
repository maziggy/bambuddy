"""The two Inventory stock alerts have a producer now (issue #2955).

``on_stock_reorder_alert`` and ``on_stock_break_alert`` had a column, a schema
field, a route, a template and a UI toggle -- and, until #2945, could not even be
saved -- but nothing that decides a SKU has reached its reorder point: the Forecast
panel does that in the browser, so with no page open nothing could ever alert.
``PrintScheduler._check_stock_forecast`` runs the panel's arithmetic
(``stock_forecast``) from the scheduler loop.

The arithmetic itself is pinned against the panel in ``test_stock_forecast_2955``;
these tests are about *when* it alerts and what it sends.

The scenarios use the delta rate (grams consumed over the age of the spool)
because it needs no usage history: a 1000 g spool created 100 days ago with
``used`` grams consumed runs at ``used / 100`` g/day. With a 6 day lead time and
the default 14 day margin:

    used  920 -> 80 g left, 9.2 g/day, 8 days of stock, reorder point ~90 g : reorder
    used  960 -> 40 g left, 9.6 g/day, 4 days of stock                       : break
    used  500 -> 500 g left, 5 g/day, 100 days of stock                      : nothing
"""

import json
import logging
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.filament_sku_settings import FilamentSkuSettings
from backend.app.models.notification import NotificationProvider
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.services.print_scheduler import _STOCK_ALERTS_SETTING_KEY, PrintScheduler
from backend.app.services.spoolman import SpoolmanUnavailableError
from backend.app.utils.local_time import utcnow_naive

REORDER_USED = 920.0
BREAK_USED = 960.0
QUIET_USED = 500.0


async def _spool(
    db,
    *,
    used: float,
    color: str | None = "Black",
    subtype: str | None = "Basic",
    brand: str | None = "Bambu Lab",
    age_days: int = 100,
    archived: bool = False,
    baseline: float = 0.0,
) -> Spool:
    spool = Spool(
        material="PLA",
        subtype=subtype,
        brand=brand,
        color_name=color,
        label_weight=1000,
        core_weight=250,
        weight_used=used,
        weight_used_baseline=baseline,
        created_at=utcnow_naive() - timedelta(days=age_days),
        archived_at=utcnow_naive() if archived else None,
    )
    spool.k_profiles = []
    spool.assignments = []
    db.add(spool)
    await db.flush()
    return spool


async def _sku(db, *, lead: int = 6, color: str | None = "Black", snoozed: bool = False, margin: int = 14):
    row = FilamentSkuSettings(
        material="PLA",
        subtype="Basic",
        brand="Bambu Lab",
        color_name=color,
        lead_time_days=lead,
        safety_margin_value=margin,
        safety_margin_unit="days",
        alerts_snoozed=snoozed,
    )
    db.add(row)
    await db.flush()
    return row


@pytest.fixture
def scheduler():
    return PrintScheduler()


async def _pass(scheduler, db):
    """Run one check, stepping over the hourly gate (which has its own test)."""
    scheduler._stock_forecast_next_check = 0.0
    await scheduler._check_stock_forecast(db)


@pytest.fixture
def notify():
    """The service, replaced, with a provider that wants both events.

    Every test about the producer assumes someone wants the alerts; the guard in
    front of it has its own tests further down, against the real provider query.
    """
    with patch("backend.app.services.print_scheduler.notification_service") as ns:
        ns.on_stock_reorder_alert = AsyncMock()
        ns.on_stock_break_alert = AsyncMock()
        ns._get_providers_for_event = AsyncMock(return_value=[MagicMock()])
        yield ns


# -- a restart does not start it over ----------------------------------------


async def _stored(db) -> list | None:
    """What the settings row holds, parsed. None when there is no row."""
    from sqlalchemy import select

    row = (await db.execute(select(Settings).where(Settings.key == _STOCK_ALERTS_SETTING_KEY))).scalar_one_or_none()
    return json.loads(row.value) if row else None


@pytest.mark.asyncio
async def test_a_fresh_scheduler_does_not_re_send_what_is_already_stored(db_session, scheduler, notify):
    """The first check runs as soon as Bambuddy starts. Without the stored set, every update
    re-sent one message per SKU that was still low."""
    await _sku(db_session)
    await _spool(db_session, used=REORDER_USED)
    await db_session.commit()
    await _pass(scheduler, db_session)
    assert notify.on_stock_reorder_alert.await_count == 1

    restarted = PrintScheduler()
    await _pass(restarted, db_session)

    assert notify.on_stock_reorder_alert.await_count == 1
    notify.on_stock_break_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stored_reorder_still_lets_the_worsening_to_a_break_through_after_a_restart(
    db_session, scheduler, notify
):
    await _sku(db_session)
    spool = await _spool(db_session, used=REORDER_USED)
    await db_session.commit()
    await _pass(scheduler, db_session)

    spool.weight_used = BREAK_USED
    await db_session.commit()
    restarted = PrintScheduler()
    await _pass(restarted, db_session)

    notify.on_stock_break_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_sku_that_cleared_while_it_was_down_alerts_again_after_a_restart(db_session, scheduler, notify):
    await _sku(db_session)
    spool = await _spool(db_session, used=REORDER_USED)
    await db_session.commit()
    await _pass(scheduler, db_session)

    spool.weight_used = QUIET_USED
    await db_session.commit()
    await _pass(PrintScheduler(), db_session)
    spool.weight_used = REORDER_USED
    await db_session.commit()
    await _pass(PrintScheduler(), db_session)

    assert notify.on_stock_reorder_alert.await_count == 2


@pytest.mark.asyncio
async def test_the_stored_set_drops_a_sku_that_no_longer_exists(db_session, scheduler, notify):
    await _sku(db_session)
    spool = await _spool(db_session, used=REORDER_USED)
    await db_session.commit()
    await _pass(scheduler, db_session)
    assert len(await _stored(db_session)) == 1

    spool.archived_at = utcnow_naive()
    await db_session.commit()
    await _pass(PrintScheduler(), db_session)

    assert await _stored(db_session) == []


@pytest.mark.asyncio
async def test_the_stored_set_is_emptied_when_nobody_wants_either_event(db_session, scheduler, real_providers):
    await _sku(db_session)
    await _spool(db_session, used=REORDER_USED)
    provider_row = NotificationProvider(
        name="p", provider_type="ntfy", config="{}", enabled=True, on_stock_reorder_alert=True
    )
    db_session.add(provider_row)
    await db_session.commit()
    await _pass(scheduler, db_session)
    assert len(await _stored(db_session)) == 1

    provider_row.on_stock_reorder_alert = False
    await db_session.commit()
    await _pass(PrintScheduler(), db_session)

    assert await _stored(db_session) == []


@pytest.mark.asyncio
async def test_an_unchanged_set_is_not_written_again(db_session, scheduler, notify):
    await _sku(db_session)
    await _spool(db_session, used=REORDER_USED)
    await db_session.commit()
    await _pass(scheduler, db_session)

    with patch("backend.app.core.db_dialect.upsert_setting", AsyncMock()) as upsert:
        await _pass(scheduler, db_session)
        await _pass(PrintScheduler(), db_session)

    upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unreadable_stored_set_is_treated_as_empty(db_session, scheduler, notify):
    db_session.add(Settings(key=_STOCK_ALERTS_SETTING_KEY, value="not json {"))
    await _sku(db_session)
    await _spool(db_session, used=REORDER_USED)
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_stock_reorder_alert.assert_awaited_once()
    assert len(await _stored(db_session)) == 1


# -- when it alerts ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sku_at_its_reorder_point_alerts_once_with_its_numbers(db_session, scheduler, notify):
    await _sku(db_session)
    await _spool(db_session, used=REORDER_USED)
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_stock_break_alert.assert_not_awaited()
    notify.on_stock_reorder_alert.assert_awaited_once()
    material, brand, stock_g, rate, days_left, _db = notify.on_stock_reorder_alert.await_args.args
    assert (material, brand) == ("PLA", "Bambu Lab")
    assert stock_g == pytest.approx(80.0)
    assert rate == pytest.approx(9.2, rel=1e-3)
    assert days_left == 8
    kwargs = notify.on_stock_reorder_alert.await_args.kwargs
    assert kwargs == {"subtype": "Basic", "color": "Black", "skip_break_subscribers": False}


@pytest.mark.asyncio
async def test_a_sku_that_runs_out_before_the_lead_time_alerts_as_a_break(db_session, scheduler, notify):
    await _sku(db_session)
    await _spool(db_session, used=BREAK_USED)
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_stock_break_alert.assert_awaited_once()
    args = notify.on_stock_break_alert.await_args.args
    assert args[2] == pytest.approx(40.0)
    assert args[4] == 4  # days left
    assert args[5] == 6  # lead time
    assert notify.on_stock_break_alert.await_args.kwargs == {"subtype": "Basic", "color": "Black"}
    # A breaking SKU has also reached its reorder point: the reorder event goes too, minus the
    # providers that have the break alert on (the service applies that filter; see below).
    notify.on_stock_reorder_alert.assert_awaited_once()
    assert notify.on_stock_reorder_alert.await_args.kwargs["skip_break_subscribers"] is True


@pytest.mark.asyncio
async def test_a_sku_with_plenty_of_stock_stays_quiet(db_session, scheduler, notify):
    await _sku(db_session)
    await _spool(db_session, used=QUIET_USED)
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_stock_reorder_alert.assert_not_awaited()
    notify.on_stock_break_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_sku_that_has_not_been_used_has_no_rate_and_stays_quiet(db_session, scheduler, notify):
    await _sku(db_session)
    await _spool(db_session, used=0.0)
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_stock_reorder_alert.assert_not_awaited()
    notify.on_stock_break_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_global_lead_time_counts(db_session, scheduler, notify):
    """No SKU row at all: the lead time is the global setting, and 40 g at 9.6 g/day is 4 days."""
    db_session.add(Settings(key="forecast_global_lead_time_days", value="30"))
    await _spool(db_session, used=BREAK_USED)
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_stock_break_alert.assert_awaited_once()
    assert notify.on_stock_break_alert.await_args.args[5] == 30


@pytest.mark.asyncio
async def test_usage_history_beats_the_delta_rate_when_there_is_enough_of_it(db_session, scheduler, notify):
    """300 g used over 100 days is 3 g/day and 700 g left is months of stock. But the
    last three days took 300 g each: the history rate says two days, so it is a break.

    Also the only test that reads ``spool_usage_history`` through the scheduler.
    """
    from backend.app.models.spool_usage_history import SpoolUsageHistory

    await _sku(db_session)
    spool = await _spool(db_session, used=300.0)
    for days_ago in (3, 2, 1):
        db_session.add(
            SpoolUsageHistory(
                spool_id=spool.id, weight_used=300.0, created_at=utcnow_naive() - timedelta(days=days_ago)
            )
        )
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_stock_break_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_history_of_a_reset_spool_is_left_out(db_session, scheduler, notify):
    """Same history, but the spool was reset (baseline set): its earlier events have no
    anchor, so the rate falls back to the delta rate -- 3 g/day, nothing to alert."""
    from backend.app.models.spool_usage_history import SpoolUsageHistory

    await _sku(db_session)
    spool = await _spool(db_session, used=300.0, baseline=100.0)
    for days_ago in (3, 2, 1):
        db_session.add(
            SpoolUsageHistory(
                spool_id=spool.id, weight_used=300.0, created_at=utcnow_naive() - timedelta(days=days_ago)
            )
        )
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_stock_break_alert.assert_not_awaited()
    notify.on_stock_reorder_alert.assert_not_awaited()


# -- once per transition -----------------------------------------------------


@pytest.mark.asyncio
async def test_does_not_repeat_on_the_next_pass(db_session, scheduler, notify):
    await _sku(db_session)
    await _spool(db_session, used=REORDER_USED)
    await db_session.commit()

    await _pass(scheduler, db_session)
    await _pass(scheduler, db_session)
    await _pass(scheduler, db_session)

    notify.on_stock_reorder_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_re_arms_once_the_condition_clears(db_session, scheduler, notify):
    """Restocked (a fresh roll brings the SKU back above the line), then low again."""
    await _sku(db_session)
    spool = await _spool(db_session, used=REORDER_USED)
    await db_session.commit()
    await _pass(scheduler, db_session)
    assert notify.on_stock_reorder_alert.await_count == 1

    spool.weight_used = QUIET_USED
    await db_session.commit()
    await _pass(scheduler, db_session)
    assert notify.on_stock_reorder_alert.await_count == 1

    spool.weight_used = REORDER_USED
    await db_session.commit()
    await _pass(scheduler, db_session)
    assert notify.on_stock_reorder_alert.await_count == 2


@pytest.mark.asyncio
async def test_worsening_from_reorder_to_break_alerts_again_as_a_break(db_session, scheduler, notify):
    await _sku(db_session)
    spool = await _spool(db_session, used=REORDER_USED)
    await db_session.commit()
    await _pass(scheduler, db_session)

    spool.weight_used = BREAK_USED
    await db_session.commit()
    await _pass(scheduler, db_session)
    await _pass(scheduler, db_session)

    notify.on_stock_reorder_alert.assert_awaited_once()
    notify.on_stock_break_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_easing_from_break_to_reorder_is_not_announced_again(db_session, scheduler, notify):
    """Ordered a few rolls' worth, so it is no longer a break but still under the reorder point.
    "Reached the reorder point" straight after "order immediately" is the opposite news."""
    await _sku(db_session)
    spool = await _spool(db_session, used=BREAK_USED)
    await db_session.commit()
    await _pass(scheduler, db_session)

    spool.weight_used = REORDER_USED
    await db_session.commit()
    await _pass(scheduler, db_session)

    assert notify.on_stock_break_alert.await_count == 1
    assert notify.on_stock_reorder_alert.await_count == 1  # the break's own, from before it eased

    # Worse again is news again.
    spool.weight_used = BREAK_USED
    await db_session.commit()
    await _pass(scheduler, db_session)
    assert notify.on_stock_break_alert.await_count == 2
    assert notify.on_stock_reorder_alert.await_count == 1


@pytest.mark.asyncio
async def test_two_colours_of_one_product_alert_separately_and_say_which(db_session, scheduler, notify):
    """The SKU is material/subtype/brand/colour, so the messages must differ by colour."""
    await _sku(db_session, color="Black")
    await _sku(db_session, color="White")
    await _spool(db_session, used=REORDER_USED, color="Black")
    await _spool(db_session, used=REORDER_USED, color="White")
    await db_session.commit()

    await _pass(scheduler, db_session)

    assert notify.on_stock_reorder_alert.await_count == 2
    colours = {call.kwargs["color"] for call in notify.on_stock_reorder_alert.await_args_list}
    assert colours == {"Black", "White"}


@pytest.mark.asyncio
async def test_a_sku_that_disappears_and_comes_back_low_alerts_again(db_session, scheduler, notify):
    """The roll runs out and is archived, so the SKU has no spools; a new roll is later low.

    The old alert must not silence the new roll: nothing has cleared the SKU's
    entry, because it was not in the forecast to be cleared.
    """
    await _sku(db_session)
    spool = await _spool(db_session, used=REORDER_USED)
    await db_session.commit()
    await _pass(scheduler, db_session)
    assert notify.on_stock_reorder_alert.await_count == 1

    spool.archived_at = utcnow_naive()
    await db_session.commit()
    await _pass(scheduler, db_session)
    await _spool(db_session, used=REORDER_USED)
    await db_session.commit()
    await _pass(scheduler, db_session)

    assert notify.on_stock_reorder_alert.await_count == 2


@pytest.mark.asyncio
async def test_spools_of_one_sku_are_added_together(db_session, scheduler, notify):
    """Two half-full spools are one SKU with 1000 g, not two SKUs with 500 g each."""
    await _sku(db_session)
    await _spool(db_session, used=REORDER_USED)  # 80 g left, on its own a reorder
    await _spool(db_session, used=QUIET_USED)  # 500 g left; together 580 g at 14.2 g/day, 40 days
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_stock_reorder_alert.assert_not_awaited()
    notify.on_stock_break_alert.assert_not_awaited()


# -- snooze and archive ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_snoozed_sku_does_not_alert_and_alerts_when_unsnoozed(db_session, scheduler, notify):
    row = await _sku(db_session, snoozed=True)
    await _spool(db_session, used=REORDER_USED)
    await db_session.commit()

    await _pass(scheduler, db_session)
    notify.on_stock_reorder_alert.assert_not_awaited()

    row.alerts_snoozed = False
    await db_session.commit()
    await _pass(scheduler, db_session)
    notify.on_stock_reorder_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_colour_with_no_row_of_its_own_uses_the_colourless_row(db_session, scheduler, notify):
    """Settings saved before colour became part of the key live on the colourless row."""
    await _sku(db_session, color=None, snoozed=True)
    await _spool(db_session, used=REORDER_USED)
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_stock_reorder_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_archived_spool_is_not_stock(db_session, scheduler, notify):
    """It would supply 900 g of stock the SKU does not have."""
    await _sku(db_session)
    await _spool(db_session, used=REORDER_USED)
    await _spool(db_session, used=0.0, archived=True)
    await db_session.commit()

    await _pass(scheduler, db_session)

    notify.on_stock_reorder_alert.assert_awaited_once()
    assert notify.on_stock_reorder_alert.await_args.args[2] == pytest.approx(80.0)


# -- failure and cost --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_provider_does_not_break_the_pass(db_session, scheduler, notify):
    await _sku(db_session)
    await _sku(db_session, color="White")
    await _spool(db_session, used=REORDER_USED, color="Black")
    await _spool(db_session, used=REORDER_USED, color="White")
    await db_session.commit()
    notify.on_stock_reorder_alert.side_effect = [RuntimeError("provider down"), None]

    await _pass(scheduler, db_session)
    assert notify.on_stock_reorder_alert.await_count == 2

    # The failed alert is lost, not retried every hour: that repetition is what the
    # debounce is for, and a provider that is down is the case where it would never stop.
    notify.on_stock_reorder_alert.side_effect = None
    await _pass(scheduler, db_session)
    assert notify.on_stock_reorder_alert.await_count == 2


@pytest.mark.asyncio
async def test_a_second_pass_inside_the_interval_does_no_work(db_session, scheduler, notify):
    """The gate. run() re-enters the checks every 3 s while an upload is in flight."""
    await _spool(db_session, used=REORDER_USED)
    await db_session.commit()

    with patch.object(scheduler, "_stock_forecasts", new_callable=AsyncMock) as work:
        work.return_value = {}
        await scheduler._check_stock_forecast(db_session)
        wait = scheduler._stock_forecast_next_check - time.monotonic()
        await scheduler._check_stock_forecast(db_session)
        await scheduler._check_stock_forecast(db_session)

    assert work.await_count == 1
    # Hourly: the forecast is in whole days, so a finer check repeats the same answer.
    assert 3500 < wait <= 3600


# -- the provider guard ------------------------------------------------------


async def _provider(db, *, enabled: bool = True, reorder: bool = False, brk: bool = False) -> None:
    db.add(
        NotificationProvider(
            name=f"p-{enabled}-{reorder}-{brk}",
            provider_type="ntfy",
            config="{}",
            enabled=enabled,
            on_stock_reorder_alert=reorder,
            on_stock_break_alert=brk,
        )
    )
    await db.flush()


@pytest.fixture
def real_providers():
    """The real service, with only the two sends replaced, so the guard runs its real query."""
    from backend.app.services.print_scheduler import notification_service

    with (
        patch.object(notification_service, "on_stock_reorder_alert", new_callable=AsyncMock) as reorder,
        patch.object(notification_service, "on_stock_break_alert", new_callable=AsyncMock) as brk,
    ):
        yield reorder, brk


@pytest.mark.asyncio
async def test_no_work_when_no_provider_wants_either_event(db_session, scheduler, real_providers):
    """Both toggles default to off, so on most installs nobody wants this.

    Unguarded, every install read its whole spool collection every hour (in
    Spoolman mode, over HTTP) for alerts that were switched off. The two
    providers are the two ways of not wanting them: enabled with both events off,
    and the events on but the provider disabled.
    """
    await _provider(db_session, enabled=True)
    await _provider(db_session, enabled=False, reorder=True, brk=True)
    await _spool(db_session, used=REORDER_USED)
    await db_session.commit()

    with patch.object(scheduler, "_stock_forecasts", new_callable=AsyncMock) as work:
        await _pass(scheduler, db_session)

    work.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_event_nobody_wants_is_not_recorded_as_sent(db_session, scheduler, real_providers):
    """Only the break event is on, and the SKU is at its reorder point. Switching
    the reorder event on later must report it -- it was never sent."""
    reorder, brk = real_providers
    await _sku(db_session)
    await _spool(db_session, used=REORDER_USED)
    provider_row = NotificationProvider(
        name="p", provider_type="ntfy", config="{}", enabled=True, on_stock_break_alert=True
    )
    db_session.add(provider_row)
    await db_session.commit()

    await _pass(scheduler, db_session)
    reorder.assert_not_awaited()
    brk.assert_not_awaited()

    provider_row.on_stock_reorder_alert = True
    await db_session.commit()
    await _pass(scheduler, db_session)

    reorder.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_reorder_only_provider_still_hears_about_a_break(db_session, scheduler, real_providers):
    """A SKU that is breaking has certainly reached its reorder point, which is what the
    Reorder Alert toggle promises. Without this, the worst state is the one a provider
    with only that toggle on is never told about."""
    reorder, brk = real_providers
    await _sku(db_session)
    await _spool(db_session, used=BREAK_USED)
    await _provider(db_session, reorder=True)
    await db_session.commit()

    await _pass(scheduler, db_session)

    reorder.assert_awaited_once()
    assert reorder.await_args.kwargs["skip_break_subscribers"] is True
    brk.assert_not_awaited()  # nobody has it on


@pytest.mark.asyncio
async def test_switching_the_break_alert_on_later_reports_the_break(db_session, scheduler, real_providers):
    """The reorder-only provider was told when the SKU broke. A provider that turns the break
    alert on afterwards is told too -- and the reorder-only one is not told a second time."""
    reorder, brk = real_providers
    await _sku(db_session)
    await _spool(db_session, used=BREAK_USED)
    await _provider(db_session, reorder=True)
    await db_session.commit()
    await _pass(scheduler, db_session)
    assert reorder.await_count == 1

    await _provider(db_session, brk=True)
    await db_session.commit()
    await _pass(scheduler, db_session)

    brk.assert_awaited_once()
    assert reorder.await_count == 1


@pytest.mark.asyncio
async def test_switching_one_event_off_and_on_again_reports_it_while_the_other_stays_on(
    db_session, scheduler, real_providers
):
    """A wants reorders, B wants breaks, and the SKU is breaking: both were told. B switches
    the break alert off (A keeps the check running) and on again: B is told again."""
    reorder, brk = real_providers
    await _sku(db_session)
    await _spool(db_session, used=BREAK_USED)
    await _provider(db_session, reorder=True)
    b_provider = NotificationProvider(
        name="b", provider_type="ntfy", config="{}", enabled=True, on_stock_break_alert=True
    )
    db_session.add(b_provider)
    await db_session.commit()
    await _pass(scheduler, db_session)
    assert (reorder.await_count, brk.await_count) == (1, 1)

    b_provider.on_stock_break_alert = False
    await db_session.commit()
    await _pass(scheduler, db_session)
    b_provider.on_stock_break_alert = True
    await db_session.commit()
    await _pass(scheduler, db_session)

    assert brk.await_count == 2
    assert reorder.await_count == 1


@pytest.mark.asyncio
async def test_a_break_alert_only_provider_is_not_told_about_a_reorder(db_session, scheduler, real_providers):
    reorder, brk = real_providers
    await _sku(db_session)
    await _spool(db_session, used=REORDER_USED)
    await _provider(db_session, brk=True)
    await db_session.commit()

    await _pass(scheduler, db_session)

    reorder.assert_not_awaited()
    brk.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_provider_with_both_on_gets_the_break_and_no_second_message(db_session, scheduler):
    """Through the real service: A has both toggles on, B only reorder. A break SKU goes to A as
    a break and to B as a reorder -- and A does not also get the reorder."""
    from backend.app.services.notification_service import NotificationService
    from backend.app.services.print_scheduler import notification_service

    await _sku(db_session)
    await _spool(db_session, used=BREAK_USED)
    both = NotificationProvider(
        name="both",
        provider_type="ntfy",
        config="{}",
        enabled=True,
        on_stock_reorder_alert=True,
        on_stock_break_alert=True,
    )
    only_reorder = NotificationProvider(
        name="only-reorder", provider_type="ntfy", config="{}", enabled=True, on_stock_reorder_alert=True
    )
    db_session.add_all([both, only_reorder])
    await db_session.commit()

    sent: list[tuple[str, list[str]]] = []

    async def record(providers, title, message, db, event_type, **kwargs):
        sent.append((event_type, sorted(p.name for p in providers)))

    with (
        patch.object(notification_service, "_send_to_providers", side_effect=record),
        patch.object(notification_service, "_build_message_from_template", AsyncMock(return_value=("t", "b"))),
    ):
        await _pass(scheduler, db_session)

    assert isinstance(notification_service, NotificationService)
    assert sorted(sent) == [("stock_break_alert", ["both"]), ("stock_reorder_alert", ["only-reorder"])]


@pytest.mark.asyncio
async def test_switching_every_event_off_and_on_again_reports_what_is_low_now(db_session, scheduler, real_providers):
    reorder, _brk = real_providers
    await _sku(db_session)
    await _spool(db_session, used=REORDER_USED)
    provider_row = NotificationProvider(
        name="p", provider_type="ntfy", config="{}", enabled=True, on_stock_reorder_alert=True
    )
    db_session.add(provider_row)
    await db_session.commit()

    await _pass(scheduler, db_session)
    assert reorder.await_count == 1

    provider_row.on_stock_reorder_alert = False
    await db_session.commit()
    await _pass(scheduler, db_session)
    provider_row.on_stock_reorder_alert = True
    await db_session.commit()
    await _pass(scheduler, db_session)

    assert reorder.await_count == 2


# -- Spoolman mode -----------------------------------------------------------


def _spoolman_spool(spool_id: int, used: float, *, color: str = "Black", age_days: int = 100) -> dict:
    """A raw Spoolman spool, in the shape _map_spoolman_spool reads."""
    registered = (utcnow_naive() - timedelta(days=age_days)).isoformat() + "Z"
    return {
        "id": spool_id,
        "remaining_weight": 1000 - used,
        "used_weight": used,
        "registered": registered,
        "filament": {
            "id": 1,
            "name": "PLA Basic",
            "material": "PLA",
            "weight": 1000,
            "color_name": color,
            "vendor": {"name": "Bambu Lab"},
        },
    }


def _spoolman_client(spools, *, unavailable: bool = False):
    client = MagicMock()
    if unavailable:
        client.get_all_spools = AsyncMock(side_effect=SpoolmanUnavailableError("Cannot reach Spoolman"))
    else:
        client.get_all_spools = AsyncMock(return_value=spools)
    return client


@pytest.mark.asyncio
async def test_spoolman_mode_alerts_from_one_collection_read(db_session, scheduler, notify):
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    await db_session.commit()
    client = _spoolman_client([_spoolman_spool(7, used=REORDER_USED), _spoolman_spool(8, used=0.0, color="White")])

    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)):
        await _pass(scheduler, db_session)

    # Black has 80 g at 9.2 g/day and no SKU row: lead time 0, so a reorder needs the
    # 14 day margin -- 80 g is under the ~128 g that gives.
    notify.on_stock_reorder_alert.assert_awaited_once()
    assert notify.on_stock_reorder_alert.await_args.kwargs["color"] == "Black"
    client.get_all_spools.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_unreachable_spoolman_stays_quiet_and_does_not_forget(db_session, scheduler, notify, caplog):
    """An outage is an ordinary state for a third-party service. It must not raise, and it
    must not clear what was sent: the SKU would alert again the moment Spoolman came back."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    await db_session.commit()
    spools = [_spoolman_spool(7, used=REORDER_USED)]

    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=_spoolman_client(spools))):
        await _pass(scheduler, db_session)
    assert notify.on_stock_reorder_alert.await_count == 1

    down = _spoolman_client([], unavailable=True)
    with (
        caplog.at_level(logging.WARNING, logger="backend.app.services.print_scheduler"),
        patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=down)),
    ):
        await _pass(scheduler, db_session)
    # An ordinary state for a third-party service, not a traceback every hour.
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=_spoolman_client(spools))):
        await _pass(scheduler, db_session)

    assert notify.on_stock_reorder_alert.await_count == 1


@pytest.mark.asyncio
async def test_a_colour_spoolman_made_up_from_the_subtype_is_not_sent_as_a_colour(db_session, scheduler, notify):
    """With no colour of its own, Spoolman mode shows the subtype in its place so the list has
    no blank colours. In a message that reads "PLA Basic Basic", so the colour is left out."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    await db_session.commit()
    raw = _spoolman_spool(7, used=REORDER_USED)
    del raw["filament"]["color_name"]
    raw["filament"]["name"] = "Basic"

    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=_spoolman_client([raw]))):
        await _pass(scheduler, db_session)

    notify.on_stock_reorder_alert.assert_awaited_once()
    assert notify.on_stock_reorder_alert.await_args.kwargs["color"] is None


@pytest.mark.asyncio
async def test_spoolman_mode_does_not_borrow_a_local_spools_history(db_session, scheduler, notify):
    """A Spoolman id that matches a local spool id (left from before Spoolman was switched on)
    must not pick up that spool's usage history. Here the local spool 1 has a history that would
    make this a break; Spoolman's spool 1 is quiet by its own delta rate."""
    from backend.app.models.spool_usage_history import SpoolUsageHistory

    local = await _spool(db_session, used=300.0)
    for days_ago in (3, 2, 1):
        db_session.add(
            SpoolUsageHistory(
                spool_id=local.id, weight_used=300.0, created_at=utcnow_naive() - timedelta(days=days_ago)
            )
        )
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    await _sku(db_session)
    await db_session.commit()
    client = _spoolman_client([_spoolman_spool(local.id, used=300.0)])

    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)):
        await _pass(scheduler, db_session)

    notify.on_stock_break_alert.assert_not_awaited()
    notify.on_stock_reorder_alert.assert_not_awaited()


# -- the service -------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_events_reach_a_provider_with_subtype_and_colour():
    """Both events pass subtype and colour through to the template variables a provider is sent."""
    from backend.app.services.notification_service import NotificationService

    service = NotificationService()
    provider = MagicMock()
    provider.id = 1

    with (
        patch.object(service, "_get_providers_for_event", new_callable=AsyncMock) as mock_get,
        patch.object(service, "_send_to_providers", new_callable=AsyncMock) as mock_send,
        patch.object(service, "_build_message_from_template", new_callable=AsyncMock) as mock_build,
    ):
        mock_get.return_value = [provider]
        mock_build.return_value = ("t", "b")

        await service.on_stock_reorder_alert(
            "PLA", "Bambu Lab", 80.0, 9.2, 8, AsyncMock(), subtype="Matte", color="Charcoal"
        )
        await service.on_stock_break_alert(
            "PLA", "Bambu Lab", 40.0, 9.6, 4, 6, AsyncMock(), subtype="Matte", color="Charcoal"
        )

    assert [c.args[1] for c in mock_get.await_args_list] == ["on_stock_reorder_alert", "on_stock_break_alert"]
    assert mock_send.await_count == 2
    for call in mock_send.await_args_list:
        variables = call.kwargs["variables"]
        assert variables["subtype"] == "Matte"
        assert variables["color"] == "Charcoal"
        assert variables["material"] == "PLA"


@pytest.mark.asyncio
async def test_subtype_and_colour_are_optional_so_the_old_call_shape_still_works():
    """Neither method had a caller, but the new arguments are keyword-only with defaults regardless."""
    from backend.app.services.notification_service import NotificationService

    service = NotificationService()
    with (
        patch.object(service, "_get_providers_for_event", new_callable=AsyncMock, return_value=[MagicMock()]),
        patch.object(service, "_send_to_providers", new_callable=AsyncMock) as mock_send,
        patch.object(service, "_build_message_from_template", new_callable=AsyncMock, return_value=("t", "b")),
    ):
        await service.on_stock_reorder_alert("PLA", None, 80.0, 9.2, 8, AsyncMock())

    variables = mock_send.await_args.kwargs["variables"]
    assert variables["subtype"] == ""
    assert variables["color"] == ""


@pytest.mark.asyncio
async def test_a_spoolman_registered_date_with_a_trailing_z_still_gives_the_spool_an_age(db_session, scheduler, notify):
    """Python 3.10's fromisoformat rejects a trailing "Z" (Bambuddy ran on 3.10 when this was written), which
    silently dropped the spool's age and with it the delta rate. Simulated here so the test
    means the same on every interpreter."""

    class _Py310Datetime(datetime):
        @classmethod
        def fromisoformat(cls, value):
            if value.endswith("Z"):
                raise ValueError(f"Invalid isoformat string: {value!r}")
            return super().fromisoformat(value)

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    await db_session.commit()
    client = _spoolman_client([_spoolman_spool(7, used=REORDER_USED)])

    with (
        patch("backend.app.services.print_scheduler.datetime", _Py310Datetime),
        patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)),
    ):
        await _pass(scheduler, db_session)

    # 920 g in 100 days is 9.2 g/day: a reorder. With the age dropped the rate falls back to a
    # different number and this SKU is not at its reorder point.
    notify.on_stock_reorder_alert.assert_awaited_once()
    assert notify.on_stock_reorder_alert.await_args.args[3] == pytest.approx(9.2, abs=0.05)
