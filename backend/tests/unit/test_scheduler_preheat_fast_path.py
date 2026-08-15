"""Tests for the `_preheat_and_soak` fast-path short-circuit.

When the chamber has already been at temperature for the full soak duration
AND the bed is currently at target, the preheat stage skips the convergence
wait and soak entirely. Before the fix it returned WITHOUT sending M140,
airduct, or M141 — the bed cooled while the 3MF uploaded.  The regression
guard here is: fast path fires ⇒ all applicable heater/flap commands are
sent, and the slow path (convergence wait + soak) is skipped.

Distinguishing the paths is done via `db.commit` — the slow path commits
before the convergence loop (releases the pooled connection during the
sleep-heavy wait) so `db.commit.await_count == 0` is a reliable signal
that the fast path returned early.
"""

from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import (
    _AIRDUCT_MODE_COOLING,
    _AIRDUCT_MODE_HEATING,
    PrintScheduler,
)

NOW = 10_000.0
PRINTER_ID = 7


@pytest.fixture
def scheduler():
    return PrintScheduler()


@pytest.fixture
def item():
    return SimpleNamespace(
        id=42,
        preheat_override="inherit",
        preheat_chamber_target_override=60,  # forces chamber_target=60, do_chamber=True
    )


@pytest.fixture
def archive():
    return SimpleNamespace(bed_temperature=60)


def _make_printer(model: str, printer_id: int = PRINTER_ID):
    return SimpleNamespace(id=printer_id, model=model)


def _make_client():
    client = MagicMock()
    client.set_bed_temperature = MagicMock(return_value=True)
    client.set_chamber_temperature = MagicMock(return_value=True)
    client.set_airduct_mode = MagicMock(return_value=True)
    return client


def _make_state(*, bed_temp=0.0, chamber_temp=0.0, airduct_mode=_AIRDUCT_MODE_COOLING):
    return SimpleNamespace(
        temperatures={"bed": bed_temp, "chamber": chamber_temp},
        raw_data={},
        airduct_mode=airduct_mode,
    )


def _ints(**values):
    return AsyncMock(side_effect=lambda _db, key, default: values.get(key, default))


def _preload_dense_history(scheduler, *, printer_id=PRINTER_ID, chamber_temp=62.0, duration=1800, interval=30):
    """Pre-fill scheduler._chamber_history so _chamber_soak_remaining returns 0.

    Uses dense samples (30s apart) covering the full soak window so the
    contiguity guard sees an unbroken run.
    """
    d: deque = deque()
    ts = NOW - duration
    while ts <= NOW:
        d.append((ts, float(chamber_temp)))
        ts += interval
    scheduler._chamber_history[printer_id] = d


# ---------------------------------------------------------------------------
# Fast path fires — sends all applicable targets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_path_fires_sends_bed_airduct_chamber_on_h2d(scheduler, item, archive):
    """H2D (heater + airduct + sensor) hits the fast path with all three commands."""
    _preload_dense_history(scheduler)
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=1800, preheat_max_wait_seconds=900)),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=60.0, chamber_temp=62.0)
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_bed_temperature.assert_called_once_with(60)
    client.set_chamber_temperature.assert_called_once_with(60)
    client.set_airduct_mode.assert_called_once_with("heating")
    # Slow path commits `db` before the convergence wait; fast path returns first.
    assert db.commit.await_count == 0


@pytest.mark.asyncio
async def test_fast_path_fires_sends_bed_only_on_x1c(scheduler, item, archive):
    """X1C has a chamber sensor but no heater and no airduct — only M140 fires."""
    _preload_dense_history(scheduler)
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=1800, preheat_max_wait_seconds=900)),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=60.0, chamber_temp=62.0)
        await scheduler._preheat_and_soak(db, item, _make_printer("X1C"), archive)

    client.set_bed_temperature.assert_called_once_with(60)
    client.set_chamber_temperature.assert_not_called()
    client.set_airduct_mode.assert_not_called()
    assert db.commit.await_count == 0


@pytest.mark.asyncio
async def test_fast_path_skips_airduct_when_already_in_heating(scheduler, item, archive):
    """Airduct already reported as heating → do NOT publish set_airduct_mode."""
    _preload_dense_history(scheduler)
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=1800, preheat_max_wait_seconds=900)),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=60.0, chamber_temp=62.0, airduct_mode=_AIRDUCT_MODE_HEATING)
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_bed_temperature.assert_called_once_with(60)
    client.set_chamber_temperature.assert_called_once_with(60)
    client.set_airduct_mode.assert_not_called()  # idempotence guard


# ---------------------------------------------------------------------------
# Fast path DOES NOT fire — falls through to slow path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_path_skipped_when_no_history(scheduler, item, archive):
    """Empty chamber history → _chamber_soak_remaining returns full soak → slow path."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0, preheat_max_wait_seconds=1)),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=60.0, chamber_temp=62.0)
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    # Slow path commits `db` before the convergence wait.
    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_fast_path_skipped_when_bed_too_cold(scheduler, item, archive):
    """Bed below target - 2 → cannot skip preheat, falls through to slow path."""
    _preload_dense_history(scheduler)
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0, preheat_max_wait_seconds=1)),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        # Bed at 30°C, way below 60°C target — fast path condition fails.
        pm.get_status.return_value = _make_state(bed_temp=30.0, chamber_temp=62.0)
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_fast_path_skipped_when_chamber_currently_below_target(scheduler, item, archive):
    """Chamber history shows history but current chamber reading is cold → slow path."""
    _preload_dense_history(scheduler)  # history says "hot"
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0, preheat_max_wait_seconds=1)),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        # Bed at target, but current chamber reading is 40°C (below 58 = 60-2).
        pm.get_status.return_value = _make_state(bed_temp=60.0, chamber_temp=40.0)
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_fast_path_skipped_when_no_sensor_model(scheduler, item, archive):
    """P1S has no chamber sensor → has_sensor=False → fast path condition fails."""
    _preload_dense_history(scheduler)
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0, preheat_max_wait_seconds=1)),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=60.0, chamber_temp=62.0)
        await scheduler._preheat_and_soak(db, item, _make_printer("P1S"), archive)

    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_fast_path_skipped_when_soak_seconds_zero(scheduler, item, archive):
    """soak_seconds=0 disables the fast path (nothing to skip) — slow path runs."""
    _preload_dense_history(scheduler)
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0, preheat_max_wait_seconds=1)),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=60.0, chamber_temp=62.0)
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    assert db.commit.await_count >= 1


# ---------------------------------------------------------------------------
# Preheat rollback pin: fast path populates it correctly for `_dispatch_one`
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_path_registers_all_actions_in_pin_on_h2d(scheduler, item, archive):
    """H2D fast path fires bed + airduct + chamber → pin has all three keys."""
    _preload_dense_history(scheduler)
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=1800, preheat_max_wait_seconds=900)),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=60.0, chamber_temp=62.0)
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    assert scheduler._preheat_pin.get(PRINTER_ID) == {"bed", "airduct", "chamber"}


@pytest.mark.asyncio
async def test_fast_path_registers_only_bed_in_pin_on_x1c(scheduler, item, archive):
    """X1C fast path fires bed only (no heater, no airduct) → pin has just 'bed'."""
    _preload_dense_history(scheduler)
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=1800, preheat_max_wait_seconds=900)),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=60.0, chamber_temp=62.0)
        await scheduler._preheat_and_soak(db, item, _make_printer("X1C"), archive)

    assert scheduler._preheat_pin.get(PRINTER_ID) == {"bed"}


@pytest.mark.asyncio
async def test_fast_path_skips_airduct_pin_when_already_heating(scheduler, item, archive):
    """Airduct already in heating → not published, not added to pin (nothing to unwind)."""
    _preload_dense_history(scheduler)
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=1800, preheat_max_wait_seconds=900)),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=60.0, chamber_temp=62.0, airduct_mode=_AIRDUCT_MODE_HEATING)
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    assert scheduler._preheat_pin.get(PRINTER_ID) == {"bed", "chamber"}


# ---------------------------------------------------------------------------
# _rollback_preheat_pin: unwinds every registered action, best-effort, no raise
# ---------------------------------------------------------------------------


def test_rollback_preheat_pin_unwinds_all_three_actions(scheduler):
    """Pin contains all three keys → three cleanup commands fire, pin dict shrinks."""
    scheduler._preheat_pin[PRINTER_ID] = {"bed", "chamber", "airduct"}
    client = _make_client()

    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_client.return_value = client
        scheduler._rollback_preheat_pin(item_id=42, printer_id=PRINTER_ID)

    client.set_bed_temperature.assert_called_once_with(0)
    client.set_chamber_temperature.assert_called_once_with(0)
    client.set_airduct_mode.assert_called_once_with("cooling")
    assert PRINTER_ID not in scheduler._preheat_pin


def test_rollback_preheat_pin_only_unwinds_registered_keys(scheduler):
    """Pin has only {bed} → only that command fires; chamber/airduct untouched."""
    scheduler._preheat_pin[PRINTER_ID] = {"bed"}
    client = _make_client()

    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_client.return_value = client
        scheduler._rollback_preheat_pin(item_id=42, printer_id=PRINTER_ID)

    client.set_bed_temperature.assert_called_once_with(0)
    client.set_chamber_temperature.assert_not_called()
    client.set_airduct_mode.assert_not_called()


def test_rollback_preheat_pin_noop_when_pin_absent(scheduler):
    """No pin entry for this printer → no client lookup, no commands, no crash."""
    client = _make_client()
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_client.return_value = client
        scheduler._rollback_preheat_pin(item_id=42, printer_id=PRINTER_ID)

    client.set_bed_temperature.assert_not_called()


def test_rollback_preheat_pin_noop_when_client_missing(scheduler):
    """Client is None (e.g. printer deregistered mid-dispatch) → no crash, pin still popped."""
    scheduler._preheat_pin[PRINTER_ID] = {"bed"}
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_client.return_value = None
        scheduler._rollback_preheat_pin(item_id=42, printer_id=PRINTER_ID)

    # Pin was consumed even though there was nothing to send to.
    assert PRINTER_ID not in scheduler._preheat_pin


def test_rollback_preheat_pin_swallows_setter_exceptions(scheduler):
    """A setter raising must not propagate — the interesting exception is upstream."""
    scheduler._preheat_pin[PRINTER_ID] = {"bed", "chamber", "airduct"}
    client = _make_client()
    client.set_bed_temperature.side_effect = RuntimeError("mqtt down")
    client.set_chamber_temperature.side_effect = RuntimeError("mqtt down")
    client.set_airduct_mode.side_effect = RuntimeError("mqtt down")

    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_client.return_value = client
        # Must not raise.
        scheduler._rollback_preheat_pin(item_id=42, printer_id=PRINTER_ID)

    assert PRINTER_ID not in scheduler._preheat_pin


# ---------------------------------------------------------------------------
# Missing bed_temperature metadata: heat the bed anyway when the chamber needs it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preheat_falls_back_to_configured_bed_temp_when_metadata_missing(scheduler, item):
    """No parsed bed temperature + chamber target > 0 → heat the bed to the configured temp.

    Previously preheat returned immediately ("archive has no bed_temperature
    metadata"), so the chamber phase never ran and the print started cold —
    the exact outcome preheat exists to prevent. The bed is how the chamber
    gets hot, so a missing bed temperature must not disable the stage.
    """
    db = AsyncMock()
    client = _make_client()
    archive_no_bed = SimpleNamespace(bed_temperature=None)

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(
            scheduler,
            "_get_int_setting",
            _ints(preheat_soak_seconds=0, preheat_max_wait_seconds=0, queue_keep_warm_bed_temp=90),
        ),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=20.0, chamber_temp=20.0)
        await scheduler._preheat_and_soak(db, item, _make_printer("X1C"), archive_no_bed)

    client.set_bed_temperature.assert_called_once_with(90)
    # The stage ran rather than returning early (slow path commits before waiting).
    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_preheat_fallback_honours_configured_temp(scheduler, item):
    """The fallback reads `queue_keep_warm_bed_temp`; it is not hard-coded."""
    db = AsyncMock()
    client = _make_client()
    archive_no_bed = SimpleNamespace(bed_temperature=None)

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(
            scheduler,
            "_get_int_setting",
            _ints(preheat_soak_seconds=0, preheat_max_wait_seconds=0, queue_keep_warm_bed_temp=100),
        ),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=20.0, chamber_temp=20.0)
        await scheduler._preheat_and_soak(db, item, _make_printer("X1C"), archive_no_bed)

    client.set_bed_temperature.assert_called_once_with(100)


@pytest.mark.asyncio
async def test_preheat_still_skips_when_no_bed_temp_and_no_chamber_target(scheduler):
    """No bed metadata AND no chamber requirement → nothing to preheat for; skip.

    Guards the unchanged half of the branch: a PLA print with no parsed bed
    temperature must not have one invented for it.
    """
    db = AsyncMock()
    client = _make_client()
    pla_item = SimpleNamespace(id=43, preheat_override="inherit", preheat_chamber_target_override=0)
    archive_no_bed = SimpleNamespace(bed_temperature=None)

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(
            scheduler,
            "_get_int_setting",
            _ints(preheat_soak_seconds=0, preheat_max_wait_seconds=0, queue_keep_warm_bed_temp=90),
        ),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=20.0, chamber_temp=20.0)
        await scheduler._preheat_and_soak(db, pla_item, _make_printer("X1C"), archive_no_bed)

    client.set_bed_temperature.assert_not_called()
    assert db.commit.await_count == 0


@pytest.mark.asyncio
async def test_preheat_prefers_parsed_bed_temp_over_fallback(scheduler, item, archive):
    """A parsed bed temperature is used as-is — the fallback only fills a gap."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(
            scheduler,
            "_get_int_setting",
            _ints(preheat_soak_seconds=0, preheat_max_wait_seconds=0, queue_keep_warm_bed_temp=90),
        ),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=20.0, chamber_temp=20.0)
        # archive fixture carries bed_temperature=60
        await scheduler._preheat_and_soak(db, item, _make_printer("X1C"), archive)

    client.set_bed_temperature.assert_called_once_with(60)


# ---------------------------------------------------------------------------
# Cancellation during preheat: stop heating, abandon the dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preheat_aborts_when_item_cancelled_during_soak(scheduler, item, archive):
    """Cancelling mid-soak stops the wait instead of holding the full duration.

    Cancelling only writes `status` to the database — it cannot interrupt a
    coroutine parked in `asyncio.sleep`. Before this, the stage slept out the
    remaining soak (up to 30 min) with the heaters on, and kept the printer in
    `busy_printers` the whole time, blocking every other queued item.
    """
    db = AsyncMock()
    client = _make_client()
    scheduler._inflight[item.id] = (MagicMock(), PRINTER_ID)
    scheduler.notify_dispatch_cancelled(item.id)
    slept: list[float] = []

    async def _fake_sleep(secs):
        slept.append(secs)

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(
            scheduler,
            "_get_int_setting",
            _ints(preheat_soak_seconds=1800, preheat_max_wait_seconds=0, queue_keep_warm_bed_temp=90),
        ),
        # The queue route has flagged this dispatch as cancelled.
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", _fake_sleep),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=60.0, chamber_temp=62.0)
        proceed = await scheduler._preheat_and_soak(db, item, _make_printer("X1C"), archive)

    assert proceed is False
    # Bailed after the first slice rather than sleeping the whole soak.
    assert sum(slept) <= 10.0, f"slept {sum(slept)}s — should abort on the first check"


@pytest.mark.asyncio
async def test_preheat_completes_when_item_stays_live(scheduler, item, archive):
    """The happy path still returns True so the dispatch proceeds to upload."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(
            scheduler,
            "_get_int_setting",
            _ints(preheat_soak_seconds=20, preheat_max_wait_seconds=0, queue_keep_warm_bed_temp=90),
        ),
        patch("backend.app.services.print_scheduler.time") as t,
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        t.monotonic.return_value = NOW
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(bed_temp=60.0, chamber_temp=62.0)
        proceed = await scheduler._preheat_and_soak(db, item, _make_printer("X1C"), archive)

    assert proceed is True


@pytest.mark.asyncio
async def test_preheat_skip_paths_still_return_true(scheduler, archive):
    """`preheat_override='off'` skips the stage but must NOT abandon the dispatch."""
    db = AsyncMock()
    off_item = SimpleNamespace(id=44, preheat_override="off", preheat_chamber_target_override=60)

    with patch("backend.app.services.print_scheduler.printer_manager"):
        proceed = await scheduler._preheat_and_soak(db, off_item, _make_printer("X1C"), archive)

    assert proceed is True


@pytest.mark.asyncio
async def test_preheat_sleep_slices_and_stops_on_cancel(scheduler):
    """`_preheat_sleep` chops a long wait up and bails at the first check after the flag lands.

    The slicing is the whole point: a single `asyncio.sleep(1800)` cannot
    observe a cancellation that arrives while it is parked.
    """
    slept: list[float] = []

    # The dispatch is in flight, which is the only state a cancellation is
    # recorded for.
    scheduler._inflight[1] = (MagicMock(), PRINTER_ID)

    async def _fake_sleep(secs):
        slept.append(secs)
        # Cancellation lands part-way through, as it would from the API.
        if len(slept) == 3:
            scheduler.notify_dispatch_cancelled(1)

    with patch("backend.app.services.print_scheduler.asyncio.sleep", _fake_sleep):
        ok = await scheduler._preheat_sleep(item_id=1, seconds=1800)

    assert ok is False
    assert len(slept) == 3, "should stop at the check following the cancellation"
    assert max(slept) <= 10.0, "each slice is bounded by the cancel-check interval"


def test_notify_dispatch_cancelled_is_scoped_to_the_item(scheduler):
    """The flag names one item; an unrelated dispatch must not see it."""
    scheduler._inflight[42] = (MagicMock(), PRINTER_ID)
    scheduler.notify_dispatch_cancelled(42)
    assert 42 in scheduler._cancelled_dispatches
    assert 43 not in scheduler._cancelled_dispatches


def test_notify_dispatch_cancelled_ignores_items_not_in_flight(scheduler):
    """Cancelling a merely-pending item records nothing.

    Every cancel and delete calls this, but only a dispatch that is already
    running can be interrupted by it. Recording the rest would grow the set
    once per cancelled item for the life of the process, and buys nothing:
    `_claim_for_dispatch` only claims rows that are still `pending`, and the
    caller has committed a terminal status (or deleted the row) first.
    """
    scheduler.notify_dispatch_cancelled(99)
    assert scheduler._cancelled_dispatches == set()


@pytest.mark.asyncio
async def test_preheat_sleep_runs_to_completion_when_not_cancelled(scheduler):
    """No flag set → the full duration is slept and True is returned."""
    slept: list[float] = []

    async def _fake_sleep(secs):
        slept.append(secs)

    with patch("backend.app.services.print_scheduler.asyncio.sleep", _fake_sleep):
        ok = await scheduler._preheat_sleep(item_id=7, seconds=25)

    assert ok is True
    assert sum(slept) == pytest.approx(25.0)
