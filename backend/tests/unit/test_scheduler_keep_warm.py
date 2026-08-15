"""Tests for the keep-bed-warm loop that fires between queued prints.

`_apply_keep_warm()` is the per-tick helper that holds the bed hot on a
printer sitting in FINISH awaiting a plate-clear — so the chamber does not
cool down between back-to-back chamber-heated prints.

The hold temperature is `queue_keep_warm_bed_temp` (default 90 °C), raised to
the next item's own parsed bed_temperature when that is higher. The bed is
the chamber's heat source here, not a print surface, so an item with no
bed_temperature metadata still gets a hold — what gates the feature is
whether the next print needs chamber heat.

Gates the whole block on three settings AND-ed together
(`queue_keep_bed_warm`, `require_plate_clear`, `preheat_enabled`) so a user
who turns off the plate-clear or preheat gate stops holding heat without
having to also toggle keep-warm.  Bounded by `queue_keep_warm_max_minutes`, and
skips the MQTT publish when the firmware already has the target.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import (
    PrintScheduler,
    _KeepWarmEntry,
)

PRINTER_ID = 7
# Archive bed temperature of the next queued item. Deliberately ABOVE HOLD_TEMP
# so the default fixtures exercise the "item's own bed temp wins" branch.
BED_TARGET = 100
# The configured `queue_keep_warm_bed_temp` floor used by `_run`.
HOLD_TEMP = 90
# The configured `queue_keep_warm_max_minutes` used by `_run`, in minutes and
# the seconds the scheduler derives from it.
MAX_HOLD_MINUTES = 120
MAX_HOLD_SECONDS = MAX_HOLD_MINUTES * 60
NOW = 10_000.0


@pytest.fixture
def scheduler():
    return PrintScheduler()


def _make_item(
    item_id: int = 1,
    printer_id: int = PRINTER_ID,
    bed_temperature: int | None = BED_TARGET,
    preheat_chamber_target_override: int | None = 60,
):
    """Build a queue-item-shaped namespace with an archive.

    ``preheat_chamber_target_override`` at a non-zero int makes the chamber-
    needed check pass without any AMS-derivation mocking. Set to ``None`` in
    tests that specifically want to exercise the derivation branch.
    """
    archive = SimpleNamespace(bed_temperature=bed_temperature)
    return SimpleNamespace(
        id=item_id,
        printer_id=printer_id,
        archive=archive,
        preheat_chamber_target_override=preheat_chamber_target_override,
    )


def _make_state(*, state="FINISH", bed_target=0.0, chamber=55.0):
    """PrinterState-shaped namespace with just what the keep-warm loop reads."""
    return SimpleNamespace(
        state=state,
        temperatures={"bed_target": bed_target, "chamber": chamber},
        raw_data={},
    )


def _make_client():
    client = MagicMock()
    client.set_bed_temperature = MagicMock(return_value=True)
    return client


def _bool_settings(**overrides):
    """AsyncMock side_effect returning per-key bool values.

    Defaults enable the full stack; pass ``queue_keep_bed_warm=False`` etc
    to switch individual gates off.
    """
    defaults = {
        "queue_keep_bed_warm": True,
        "preheat_enabled": True,
    }
    defaults.update(overrides)
    return AsyncMock(side_effect=lambda _db, key, default: defaults.get(key, default))


def _int_settings(hold_temp, max_hold_minutes):
    return {
        "queue_keep_warm_bed_temp": hold_temp,
        "queue_keep_warm_max_minutes": max_hold_minutes,
    }


async def _run(
    scheduler,
    *,
    items=None,
    dispatch_ids=None,
    busy_printers=None,
    require_plate_clear=True,
    bool_settings=None,
    hold_temp=HOLD_TEMP,
    max_hold_minutes=MAX_HOLD_MINUTES,
):
    """Invoke `_apply_keep_warm` with sensible defaults and standard patches."""
    if items is None:
        items = [_make_item()]
    if dispatch_ids is None:
        dispatch_ids = []
    if busy_printers is None:
        busy_printers = {PRINTER_ID}
    if bool_settings is None:
        bool_settings = _bool_settings()

    db = AsyncMock()
    with (
        patch.object(scheduler, "_get_bool_setting", bool_settings),
        patch.object(
            scheduler,
            "_get_int_setting",
            AsyncMock(
                side_effect=lambda _db, key, default=0: _int_settings(hold_temp, max_hold_minutes).get(key, default)
            ),
        ),
        patch("backend.app.services.print_scheduler.time") as t,
    ):
        t.monotonic.return_value = NOW
        await scheduler._apply_keep_warm(db, items, dispatch_ids, busy_printers, require_plate_clear)


# ---------------------------------------------------------------------------
# Gating: all three settings AND-ed together
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keep_warm_skips_when_feature_disabled(scheduler):
    """queue_keep_bed_warm=False → no MQTT publish, no state change."""
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = client
        await _run(scheduler, bool_settings=_bool_settings(queue_keep_bed_warm=False))

    client.set_bed_temperature.assert_not_called()
    assert PRINTER_ID not in scheduler._keep_warm


@pytest.mark.asyncio
async def test_keep_warm_skips_when_require_plate_clear_off(scheduler):
    """require_plate_clear=False → skip even if keep-warm and preheat are on."""
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = client
        await _run(scheduler, require_plate_clear=False)

    client.set_bed_temperature.assert_not_called()


@pytest.mark.asyncio
async def test_keep_warm_skips_when_preheat_disabled(scheduler):
    """preheat_enabled=False → skip regardless of the toggle."""
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = client
        await _run(scheduler, bool_settings=_bool_settings(preheat_enabled=False))

    client.set_bed_temperature.assert_not_called()


# ---------------------------------------------------------------------------
# Per-printer skip conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keep_warm_skips_when_printer_not_in_finish(scheduler):
    """Only FINISH printers keep warm — a printer that's still printing is not held."""
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state(state="RUNNING")
        pm.get_client.return_value = client
        await _run(scheduler)

    client.set_bed_temperature.assert_not_called()


@pytest.mark.asyncio
async def test_keep_warm_holds_configured_temp_when_archive_has_no_bed_temp(scheduler):
    """No parsed bed_temperature → still hold, at the configured keep-warm temp.

    The bed is the chamber's heat source during the hold, not a print surface,
    so missing slicer metadata must not disable the feature. OrcaSlicer
    `.gcode.3mf` exports parse without a bed temperature and would otherwise
    never keep warm even though their filament requires chamber heat.
    """
    client = _make_client()
    item = _make_item(bed_temperature=None)
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = client
        await _run(scheduler, items=[item])

    client.set_bed_temperature.assert_called_once_with(HOLD_TEMP)
    assert scheduler._keep_warm[PRINTER_ID].held_target == HOLD_TEMP


@pytest.mark.asyncio
async def test_keep_warm_uses_item_bed_temp_when_higher_than_configured(scheduler):
    """Item's own bed temp (100) > configured hold (90) → hold at 100.

    The hold must never run cooler than the print itself will, or the chamber
    would dip right before dispatch.
    """
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = client
        await _run(scheduler, items=[_make_item(bed_temperature=100)])

    client.set_bed_temperature.assert_called_once_with(100)


@pytest.mark.asyncio
async def test_keep_warm_uses_configured_temp_when_item_bed_temp_lower(scheduler):
    """Item's bed temp (60) < configured hold (90) → hold at 90.

    A cool-plate ASA profile still needs the chamber hot; the floor wins.
    """
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = client
        await _run(scheduler, items=[_make_item(bed_temperature=60)])

    client.set_bed_temperature.assert_called_once_with(HOLD_TEMP)


@pytest.mark.asyncio
async def test_keep_warm_honours_custom_configured_hold_temp(scheduler):
    """`queue_keep_warm_bed_temp` is read from settings, not hard-coded."""
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = client
        await _run(scheduler, items=[_make_item(bed_temperature=None)], hold_temp=105)

    client.set_bed_temperature.assert_called_once_with(105)


@pytest.mark.asyncio
async def test_keep_warm_skips_when_no_client(scheduler):
    """No live client (e.g. printer just deregistered) → skip silently."""
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = None
        await _run(scheduler)

    assert PRINTER_ID not in scheduler._keep_warm


@pytest.mark.asyncio
async def test_keep_warm_skips_when_chamber_override_zero(scheduler):
    """Per-item override of 0 → 'no chamber even if filament wants it' → skip."""
    client = _make_client()
    item = _make_item(preheat_chamber_target_override=0)
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = client
        await _run(scheduler, items=[item])

    client.set_bed_temperature.assert_not_called()


@pytest.mark.asyncio
async def test_keep_warm_skips_when_dispatched_this_cycle(scheduler):
    """Printers being dispatched this tick are excluded — _preheat_and_soak owns their bed."""
    client = _make_client()
    item = _make_item(item_id=42)
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = client
        await _run(scheduler, items=[item], dispatch_ids=[42])

    client.set_bed_temperature.assert_not_called()


@pytest.mark.asyncio
async def test_keep_warm_skips_when_chamber_derivation_yields_zero(scheduler):
    """No per-item override + _derive_chamber_target returns 0 → skip."""
    client = _make_client()
    item = _make_item(preheat_chamber_target_override=None)
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch.object(scheduler, "_get_preheat_filament_targets", AsyncMock(return_value={})),
        patch.object(scheduler, "_get_printer", AsyncMock(return_value=SimpleNamespace(id=PRINTER_ID, model="H2D"))),
        patch.object(scheduler, "_derive_chamber_target", return_value=0),
    ):
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = client
        await _run(scheduler, items=[item])

    client.set_bed_temperature.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keep_warm_publishes_bed_target(scheduler):
    """Full-stack happy path: gates on, printer in FINISH, chamber needed → M140 sent."""
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state()  # bed_target=0 → publish fires
        pm.get_client.return_value = client
        await _run(scheduler)

    client.set_bed_temperature.assert_called_once_with(BED_TARGET)
    assert PRINTER_ID in scheduler._keep_warm
    entry = scheduler._keep_warm[PRINTER_ID]
    assert entry.held_target == BED_TARGET
    assert entry.expired is False


# ---------------------------------------------------------------------------
# Idempotence guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keep_warm_skips_when_firmware_already_at_target(scheduler):
    """state.temperatures['bed_target'] already equals the desired target → no publish."""
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state(bed_target=float(BED_TARGET))
        pm.get_client.return_value = client
        await _run(scheduler)

    client.set_bed_temperature.assert_not_called()


# ---------------------------------------------------------------------------
# Max-duration timeout — publish bed → 0 once, latch expired, do NOT re-arm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keep_warm_publishes_bed_off_and_latches_on_timeout(scheduler):
    """After MAX_HOLD_SECONDS: publish bed → 0, latch expired, keep the entry.

    The old behaviour popped the entry — but that meant the next tick's
    ``setdefault`` re-seeded ``started`` and the 2 h window restarted forever.
    The entry must stay so subsequent ticks skip re-engagement.
    """
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(
        started=NOW - (MAX_HOLD_SECONDS + 1),
        held_target=BED_TARGET,
    )
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        # Firmware still holds our target → bed-off publish fires.
        pm.get_status.return_value = _make_state(bed_target=float(BED_TARGET))
        pm.get_client.return_value = client
        await _run(scheduler)

    client.set_bed_temperature.assert_called_once_with(0)
    assert PRINTER_ID in scheduler._keep_warm
    assert scheduler._keep_warm[PRINTER_ID].expired is True


@pytest.mark.asyncio
async def test_keep_warm_timeout_does_not_rearm_on_next_tick(scheduler):
    """Multi-tick regression guard: the tick AFTER a timeout must NOT re-engage.

    This is the bug the review flagged: popping on timeout let the next
    tick's ``setdefault(pid, now_mono)`` re-seed the clock, restarting the
    2 h window. Latching ``expired=True`` on the entry (kept in place)
    prevents that.
    """
    original_started = NOW - (MAX_HOLD_SECONDS + 1)
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(
        started=original_started,
        held_target=BED_TARGET,
        expired=True,  # already latched by previous tick's timeout
    )
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state(bed_target=float(BED_TARGET))
        pm.get_client.return_value = client
        await _run(scheduler)

    # No re-engagement, no bed-off (already sent in the prior tick), and the
    # entry keeps its ORIGINAL started timestamp — no clock re-seed.
    client.set_bed_temperature.assert_not_called()
    assert scheduler._keep_warm[PRINTER_ID].started == original_started
    assert scheduler._keep_warm[PRINTER_ID].expired is True


@pytest.mark.asyncio
async def test_keep_warm_timeout_skips_bed_off_when_firmware_target_changed(scheduler):
    """Firmware bed_target != held_target on timeout → don't clobber user's change."""
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(
        started=NOW - (MAX_HOLD_SECONDS + 1),
        held_target=BED_TARGET,
    )
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        # Firmware target no longer matches held_target.
        pm.get_status.return_value = _make_state(bed_target=42.0)
        pm.get_client.return_value = client
        await _run(scheduler)

    client.set_bed_temperature.assert_not_called()
    # Latch still fires so we don't re-engage next tick.
    assert scheduler._keep_warm[PRINTER_ID].expired is True


@pytest.mark.asyncio
async def test_keep_warm_starts_timer_on_first_tick(scheduler):
    """First tick for a printer creates a _KeepWarmEntry with started=NOW."""
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = client
        await _run(scheduler)

    assert PRINTER_ID in scheduler._keep_warm
    entry = scheduler._keep_warm[PRINTER_ID]
    assert entry.started == NOW
    assert entry.held_target == BED_TARGET
    assert entry.expired is False


@pytest.mark.asyncio
async def test_keep_warm_preserves_existing_timer(scheduler):
    """Subsequent ticks must NOT reset started — otherwise timeout never fires."""
    started_earlier = NOW - 3600
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(
        started=started_earlier,
        held_target=BED_TARGET,
    )
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        # Firmware already at our target → idempotence skips the publish;
        # the entry is preserved as-is.
        pm.get_status.return_value = _make_state(bed_target=float(BED_TARGET))
        pm.get_client.return_value = client
        await _run(scheduler)

    assert scheduler._keep_warm[PRINTER_ID].started == started_earlier


# ---------------------------------------------------------------------------
# Release sweep — bed → 0 when printer leaves the candidate set / gate off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keep_warm_releases_bed_when_printer_leaves_candidate_set(scheduler):
    """Owned printer no longer in candidates → publish bed → 0, drop entry."""
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(
        started=NOW - 300,
        held_target=BED_TARGET,
    )
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state(bed_target=float(BED_TARGET))
        pm.get_client.return_value = client
        await _run(scheduler, items=[], busy_printers=set())

    client.set_bed_temperature.assert_called_once_with(0)
    assert PRINTER_ID not in scheduler._keep_warm


@pytest.mark.asyncio
async def test_keep_warm_release_skipped_when_printer_was_dispatched(scheduler):
    """Dispatched printers exit candidates but _preheat_and_soak owns the bed — no bed-off."""
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(
        started=NOW - 300,
        held_target=BED_TARGET,
    )
    item = _make_item(item_id=42)
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state(bed_target=float(BED_TARGET))
        pm.get_client.return_value = client
        # Item 42 is being dispatched this tick — release must NOT publish.
        await _run(scheduler, items=[item], dispatch_ids=[42])

    client.set_bed_temperature.assert_not_called()
    assert PRINTER_ID not in scheduler._keep_warm  # tracking dropped either way


@pytest.mark.asyncio
async def test_keep_warm_hands_bed_ownership_to_preheat_pin_on_dispatch(scheduler):
    """Handing a hot bed to dispatch must register it for preheat rollback.

    Keep-warm stops tracking the printer the moment it is dispatched, and
    `_preheat_and_soak` may never claim the bed itself (it returns early when
    the item has no bed_temperature metadata). Without this transfer, an
    aborted dispatch — failed upload, cancelled item — would leave the bed hot
    with no owner and nothing to turn it off.
    """
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(started=NOW - 300, held_target=BED_TARGET)
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state(bed_target=float(BED_TARGET))
        pm.get_client.return_value = client
        await _run(scheduler, items=[_make_item(item_id=42)], dispatch_ids=[42])

    assert "bed" in scheduler._preheat_pin.get(PRINTER_ID, set())


@pytest.mark.asyncio
async def test_keep_warm_release_does_not_touch_preheat_pin(scheduler):
    """A genuine release (not a dispatch) turns the bed off — no pin entry needed."""
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(started=NOW - 300, held_target=BED_TARGET)
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state(bed_target=float(BED_TARGET))
        pm.get_client.return_value = client
        await _run(scheduler, items=[], busy_printers=set())

    client.set_bed_temperature.assert_called_once_with(0)
    assert PRINTER_ID not in scheduler._preheat_pin


@pytest.mark.asyncio
async def test_keep_warm_release_skipped_when_firmware_target_changed(scheduler):
    """Firmware bed_target != held_target on release → don't clobber user's change."""
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(
        started=NOW - 300,
        held_target=BED_TARGET,
    )
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state(bed_target=42.0)
        pm.get_client.return_value = client
        await _run(scheduler, items=[], busy_printers=set())

    client.set_bed_temperature.assert_not_called()
    assert PRINTER_ID not in scheduler._keep_warm  # tracking still dropped


@pytest.mark.asyncio
async def test_keep_warm_release_fires_when_feature_toggled_off_mid_hold(scheduler):
    """queue_keep_bed_warm turned off while a printer is owned → release still fires."""
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(
        started=NOW - 300,
        held_target=BED_TARGET,
    )
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state(bed_target=float(BED_TARGET))
        pm.get_client.return_value = client
        await _run(scheduler, bool_settings=_bool_settings(queue_keep_bed_warm=False))

    client.set_bed_temperature.assert_called_once_with(0)
    assert PRINTER_ID not in scheduler._keep_warm


@pytest.mark.asyncio
async def test_keep_warm_release_fires_when_plate_clear_toggled_off_mid_hold(scheduler):
    """require_plate_clear=False mid-hold → release still fires."""
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(
        started=NOW - 300,
        held_target=BED_TARGET,
    )
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state(bed_target=float(BED_TARGET))
        pm.get_client.return_value = client
        await _run(scheduler, require_plate_clear=False)

    client.set_bed_temperature.assert_called_once_with(0)
    assert PRINTER_ID not in scheduler._keep_warm


# ---------------------------------------------------------------------------
# Candidate-set eviction of stale state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keep_warm_keeps_entry_when_printer_is_unreachable(scheduler):
    """An unreachable printer keeps its entry so a later tick can still release it.

    Printer 99 left the candidate set, but `get_status` returns None — it is
    briefly offline, not gone. Its bed may still be hot, so dropping the entry
    here would stop the max-duration timeout applying and leave nothing
    tracking it. The entry is kept and the release retried later;
    `_sample_chamber_temps` is the only place that gives up, once the printer
    has left the manager entirely.
    """
    scheduler._keep_warm[99] = _KeepWarmEntry(started=NOW - 60, held_target=BED_TARGET)
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(started=NOW - 60, held_target=BED_TARGET)
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.side_effect = lambda pid: _make_state(bed_target=float(BED_TARGET)) if pid == PRINTER_ID else None
        pm.get_client.side_effect = lambda pid: client if pid == PRINTER_ID else None
        await _run(scheduler, busy_printers={PRINTER_ID})

    assert 99 in scheduler._keep_warm, "unreachable printer must stay tracked"
    assert PRINTER_ID in scheduler._keep_warm


@pytest.mark.asyncio
async def test_keep_warm_release_retries_after_a_failed_publish(scheduler):
    """A failed bed-off keeps the entry so the next tick tries again."""
    scheduler._keep_warm[99] = _KeepWarmEntry(started=NOW - 60, held_target=BED_TARGET)
    client = _make_client()
    client.set_bed_temperature.side_effect = RuntimeError("mqtt down")
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state(bed_target=float(BED_TARGET))
        pm.get_client.return_value = client
        await _run(scheduler, items=[], busy_printers=set())

    client.set_bed_temperature.assert_called_once_with(0)
    assert 99 in scheduler._keep_warm, "a failed release must not silently drop the entry"


def test_sample_chamber_temps_evicts_preheat_pin_for_removed_printer(scheduler):
    """Per-printer preheat state is evicted with the rest when a printer disappears."""
    scheduler._preheat_pin[99] = {"bed"}
    scheduler._preheat_pin[PRINTER_ID] = {"bed"}
    with (
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
    ):
        t.monotonic.return_value = NOW
        pm.get_all_statuses.return_value = {PRINTER_ID: SimpleNamespace(connected=True, temperatures={"chamber": 40.0})}
        scheduler._sample_chamber_temps()

    assert 99 not in scheduler._preheat_pin
    assert PRINTER_ID in scheduler._preheat_pin


# ---------------------------------------------------------------------------
# Lazy filament target fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keep_warm_does_not_fetch_filament_targets_when_all_overrides(scheduler):
    """Per-item overrides supply chamber_needed → skip the DB round-trip."""
    client = _make_client()
    fetch_targets = AsyncMock(return_value={})
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch.object(scheduler, "_get_preheat_filament_targets", fetch_targets),
    ):
        pm.get_status.return_value = _make_state()
        pm.get_client.return_value = client
        # Item has an explicit chamber override, so derivation is not needed.
        await _run(scheduler)

    fetch_targets.assert_not_called()


@pytest.mark.asyncio
async def test_keep_warm_fetches_filament_targets_once_per_tick(scheduler):
    """When derivation is needed for multiple printers, only fetch targets once."""
    items = [
        _make_item(item_id=1, printer_id=1, preheat_chamber_target_override=None),
        _make_item(item_id=2, printer_id=2, preheat_chamber_target_override=None),
    ]
    client1 = _make_client()
    client2 = _make_client()
    fetch_targets = AsyncMock(return_value={"ASA": 60})
    with (
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch.object(scheduler, "_get_preheat_filament_targets", fetch_targets),
        patch.object(scheduler, "_get_printer", AsyncMock(return_value=SimpleNamespace(id=1, model="H2D"))),
        patch.object(scheduler, "_derive_chamber_target", return_value=60),
    ):
        pm.get_status.return_value = _make_state()
        pm.get_client.side_effect = lambda pid: {1: client1, 2: client2}[pid]
        await _run(scheduler, items=items, busy_printers={1, 2})

    assert fetch_targets.call_count == 1


@pytest.mark.asyncio
async def test_keep_warm_timeout_honours_configured_minutes(scheduler):
    """A 15-minute limit stops the hold at 15 minutes, not at the default.

    The whole point of `queue_keep_warm_max_minutes`: a user who does not want
    a bed sitting hot while they are away sets a short window, and the heaters
    go off when it elapses.
    """
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(
        started=NOW - (15 * 60 + 1),
        held_target=BED_TARGET,
    )
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state(bed_target=float(BED_TARGET))
        pm.get_client.return_value = client
        await _run(scheduler, max_hold_minutes=15)

    client.set_bed_temperature.assert_called_once_with(0)
    assert scheduler._keep_warm[PRINTER_ID].expired is True


@pytest.mark.asyncio
async def test_keep_warm_holds_within_configured_window(scheduler):
    """Just inside the configured window the hold continues untouched."""
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(
        started=NOW - (15 * 60 - 60),
        held_target=BED_TARGET,
    )
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        # Firmware already holds the target, so an untouched hold means no publish.
        pm.get_status.return_value = _make_state(bed_target=float(BED_TARGET))
        pm.get_client.return_value = client
        await _run(scheduler, max_hold_minutes=15)

    client.set_bed_temperature.assert_not_called()
    assert scheduler._keep_warm[PRINTER_ID].expired is False


# ---------------------------------------------------------------------------
# Handing the hold to the preheat pin, and getting it back when dispatch bails
# ---------------------------------------------------------------------------
#
# `_sweep_keep_warm` gives up the keep-warm entry for a printer being dispatched
# this tick and pins "bed" instead, on the promise that `_dispatch_one` unwinds
# it on any non-success exit. Two of `_dispatch_one`'s exits used to break that
# promise by returning before the `finally` could run, which left the bed hot
# with the entry already gone -- so neither the max-duration cap nor
# `_release_keep_warm` applied, and on the printer's last pending item nothing
# would ever switch it off.


def test_dispatch_handover_records_the_held_target(scheduler):
    """The pin remembers what keep-warm was holding, not just that it held."""
    scheduler._keep_warm[PRINTER_ID] = _KeepWarmEntry(started=NOW, held_target=HOLD_TEMP)

    scheduler._sweep_keep_warm(active_candidates=set(), dispatched={PRINTER_ID})

    assert PRINTER_ID not in scheduler._keep_warm
    assert scheduler._preheat_pin[PRINTER_ID] == {"bed"}
    assert scheduler._preheat_pin_bed[PRINTER_ID] == HOLD_TEMP


@pytest.mark.asyncio
async def test_unclaimable_item_releases_the_handed_over_bed(scheduler):
    """A cancel landing between selection and the claim must not strand the bed.

    `_claim_for_dispatch` returning False exits before the try/finally, so the
    rollback has to fire on that path explicitly.
    """
    scheduler._preheat_pin[PRINTER_ID] = {"bed"}
    scheduler._preheat_pin_bed[PRINTER_ID] = HOLD_TEMP
    client = MagicMock()

    with (
        patch("backend.app.services.print_scheduler.async_session") as session_factory,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch.object(scheduler, "_claim_for_dispatch", AsyncMock(return_value=False)),
    ):
        session_factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        pm.get_client.return_value = client
        pm.get_status.return_value = SimpleNamespace(temperatures={"bed_target": HOLD_TEMP})

        await scheduler._dispatch_one(42, selected_printer_id=PRINTER_ID)

    client.set_bed_temperature.assert_called_once_with(0)
    assert PRINTER_ID not in scheduler._preheat_pin
    assert PRINTER_ID not in scheduler._preheat_pin_bed


@pytest.mark.asyncio
async def test_unclaimable_item_without_a_known_printer_is_a_noop(scheduler):
    """Direct callers that pass no printer keep the old behaviour."""
    scheduler._preheat_pin[PRINTER_ID] = {"bed"}
    client = MagicMock()

    with (
        patch("backend.app.services.print_scheduler.async_session") as session_factory,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch.object(scheduler, "_claim_for_dispatch", AsyncMock(return_value=False)),
    ):
        session_factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        pm.get_client.return_value = client

        await scheduler._dispatch_one(42)

    client.set_bed_temperature.assert_not_called()
    assert scheduler._preheat_pin[PRINTER_ID] == {"bed"}


@pytest.mark.asyncio
async def test_vanished_item_releases_the_handed_over_bed(scheduler):
    """The row disappearing after a successful claim takes the same exit.

    That return is inside the try, but `item_printer_id` used to still be None
    there, so the rollback was skipped by its own guard.
    """
    scheduler._preheat_pin[PRINTER_ID] = {"bed"}
    scheduler._preheat_pin_bed[PRINTER_ID] = HOLD_TEMP
    client = MagicMock()
    item_db = MagicMock()
    item_db.get = AsyncMock(return_value=None)

    with (
        patch("backend.app.services.print_scheduler.async_session") as session_factory,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch.object(scheduler, "_claim_for_dispatch", AsyncMock(return_value=True)),
        patch.object(scheduler, "_clear_dispatch_claim", AsyncMock()),
        patch.object(scheduler, "_release_unconfirmed_budget_reservation", AsyncMock()),
    ):
        session_factory.return_value.__aenter__ = AsyncMock(return_value=item_db)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        pm.get_client.return_value = client
        pm.get_status.return_value = SimpleNamespace(temperatures={"bed_target": HOLD_TEMP})

        await scheduler._dispatch_one(42, selected_printer_id=PRINTER_ID)

    client.set_bed_temperature.assert_called_once_with(0)
    assert PRINTER_ID not in scheduler._preheat_pin


# ---------------------------------------------------------------------------
# Rollback leaves a bed somebody else now owns alone
# ---------------------------------------------------------------------------


def test_rollback_leaves_a_reassigned_bed_alone(scheduler):
    """Firmware reports a target we did not set → the bed belongs to someone else."""
    scheduler._preheat_pin[PRINTER_ID] = {"bed"}
    scheduler._preheat_pin_bed[PRINTER_ID] = HOLD_TEMP
    client = MagicMock()

    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_client.return_value = client
        pm.get_status.return_value = SimpleNamespace(temperatures={"bed_target": 45})
        scheduler._rollback_preheat_pin(item_id=42, printer_id=PRINTER_ID)

    client.set_bed_temperature.assert_not_called()
    assert PRINTER_ID not in scheduler._preheat_pin
    assert PRINTER_ID not in scheduler._preheat_pin_bed


def test_rollback_switches_off_when_the_target_still_matches(scheduler):
    scheduler._preheat_pin[PRINTER_ID] = {"bed"}
    scheduler._preheat_pin_bed[PRINTER_ID] = HOLD_TEMP
    client = MagicMock()

    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_client.return_value = client
        pm.get_status.return_value = SimpleNamespace(temperatures={"bed_target": HOLD_TEMP})
        scheduler._rollback_preheat_pin(item_id=42, printer_id=PRINTER_ID)

    client.set_bed_temperature.assert_called_once_with(0)


def test_rollback_switches_off_when_the_target_cannot_be_read(scheduler):
    """No evidence is not evidence of reassignment -- err towards a cold bed."""
    scheduler._preheat_pin[PRINTER_ID] = {"bed"}
    scheduler._preheat_pin_bed[PRINTER_ID] = HOLD_TEMP
    client = MagicMock()

    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_client.return_value = client
        pm.get_status.return_value = None
        scheduler._rollback_preheat_pin(item_id=42, printer_id=PRINTER_ID)

    client.set_bed_temperature.assert_called_once_with(0)


def test_unregistered_printer_evicts_the_recorded_bed_target(scheduler):
    scheduler._preheat_pin[PRINTER_ID] = {"bed"}
    scheduler._preheat_pin_bed[PRINTER_ID] = HOLD_TEMP

    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_all_statuses.return_value = {}
        scheduler._sample_chamber_temps()

    assert PRINTER_ID not in scheduler._preheat_pin
    assert PRINTER_ID not in scheduler._preheat_pin_bed
