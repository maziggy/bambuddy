"""Tests for the preheat & heat-soak scheduler stage (#1468).

Three layered concerns the stage has to get right:

1. **Override resolution** (per-item beats global beats default).
2. **Chamber-target derivation** (item-override > filament-map max > 0).
3. **Hardware-tier branching** (chamber heater vs sensor-only vs no sensor).

The fixtures construct a queue item with `preheat_override` + the override
target both unset; tests flip those per case. `asyncio.sleep` is patched to
AsyncMock so the soak phase doesn't actually wait — assertions are on what
got scheduled, not wall-clock.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
def scheduler():
    return PrintScheduler()


@pytest.fixture
def item():
    return SimpleNamespace(
        id=42,
        preheat_override="inherit",
        preheat_chamber_target_override=None,
    )


@pytest.fixture
def archive():
    return SimpleNamespace(bed_temperature=60)


def _make_printer(model: str, printer_id: int = 7):
    return SimpleNamespace(id=printer_id, model=model)


def _make_client():
    client = MagicMock()
    client.set_bed_temperature = MagicMock(return_value=True)
    client.set_chamber_temperature = MagicMock(return_value=True)
    client.set_airduct_mode = MagicMock(return_value=True)
    return client


def _make_state(
    bed_temp: float = 0.0,
    chamber_temp: float = 0.0,
    trays: list[str] | None = None,
    airduct_mode: int = 0,
):
    """Build a PrinterState-shaped namespace with optional AMS tray types.

    `trays` is a list of tray_type strings (each becomes one loaded slot in
    AMS unit 0). Empty / None gives an empty AMS — the derivation falls
    through to 0. `airduct_mode` is 0 (cooling, default) or 1 (heating);
    matches the field on PrinterState that the preheat stage reads to
    decide whether to fire a redundant `set_airduct_mode` call."""
    raw_data: dict = {}
    if trays is not None:
        raw_data["ams"] = [{"tray": [{"tray_type": t} for t in trays]}]
    return SimpleNamespace(
        temperatures={"bed": bed_temp, "chamber": chamber_temp},
        raw_data=raw_data,
        airduct_mode=airduct_mode,
    )


def _ints(**values):
    """Mock side_effect for _get_int_setting that returns the kwarg value
    when the key matches, else the helper's `default` argument."""
    return AsyncMock(side_effect=lambda _db, key, default: values.get(key, default))


# ----------------------------------------------------------------------------
# Override resolution
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_disabled_inherit_skips(scheduler, item, archive):
    """preheat_enabled=False + item.preheat_override='inherit' → no heater dispatch."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=False)),
        patch.object(scheduler, "_get_int_setting", _ints()),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
    ):
        pm.get_client.return_value = client
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_bed_temperature.assert_not_called()
    client.set_chamber_temperature.assert_not_called()


@pytest.mark.asyncio
async def test_item_override_off_bypasses_global_on(scheduler, item, archive):
    """preheat_enabled=True + item.preheat_override='off' → preheat suppressed."""
    db = AsyncMock()
    client = _make_client()
    item.preheat_override = "off"

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints()),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
    ):
        pm.get_client.return_value = client
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_bed_temperature.assert_not_called()
    client.set_chamber_temperature.assert_not_called()


@pytest.mark.asyncio
async def test_item_override_on_runs_despite_global_off(scheduler, item, archive):
    """preheat_enabled=False + item.preheat_override='on' → preheat runs (bed
    fires, chamber depends on the resolved target)."""
    db = AsyncMock()
    client = _make_client()
    item.preheat_override = "on"
    item.preheat_chamber_target_override = 0  # explicit no-chamber so the assertion is sharp

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=False)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(60.0, 0.0)
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_bed_temperature.assert_called_once_with(60)
    client.set_chamber_temperature.assert_not_called()


# ----------------------------------------------------------------------------
# Chamber-target derivation
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chamber_target_override_beats_filament_map(scheduler, item, archive):
    """item.preheat_chamber_target_override is the highest-priority source —
    a PLA-only print with an explicit 50°C override still heats the chamber."""
    db = AsyncMock()
    client = _make_client()
    item.preheat_chamber_target_override = 50

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        # Only PLA loaded — filament map would derive 0; override forces 50.
        pm.get_status.return_value = _make_state(60.0, 52.0, trays=["PLA Basic"])
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_bed_temperature.assert_called_once_with(60)
    client.set_chamber_temperature.assert_called_once_with(50)


@pytest.mark.asyncio
async def test_filament_map_picks_max_across_loaded_slots(scheduler, item, archive):
    """Mixed PA + PLA load: PA=50 + PLA=0 → chamber target 50 (the max).
    The "lowest common denominator" model is wrong here; PA's requirement
    is the binding constraint."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        # `PA Basic` (note the space) normalises to `PA` which the bundled
        # map keys against; a hyphenated `PA-Generic` would normalise to
        # `PA-GENERIC` and fall through to `default` (0) — that's a separate
        # behaviour the user editor handles by adding a custom key.
        pm.get_status.return_value = _make_state(60.0, 52.0, trays=["PLA Basic", "PA Basic"])
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_chamber_temperature.assert_called_once_with(50)  # PA's recommendation, not PLA's


@pytest.mark.asyncio
async def test_pla_only_derives_zero_chamber_skips(scheduler, item, archive):
    """PLA-only print: filament-map lookup returns 0 → chamber phase skips
    automatically without the user touching anything."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(60.0, 0.0, trays=["PLA", "PLA"])
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_bed_temperature.assert_called_once_with(60)
    client.set_chamber_temperature.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_filament_type_falls_to_default(scheduler, item, archive):
    """A loaded tray with a type not in the map uses the `default` entry —
    keeps users with custom filament names safe (they get 0 by default,
    can be tuned via the per-filament editor)."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(60.0, 0.0, trays=["MyCustomFilament"])
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_bed_temperature.assert_called_once_with(60)
    client.set_chamber_temperature.assert_not_called()  # default = 0


@pytest.mark.asyncio
async def test_custom_filament_targets_json_parses(scheduler, item, archive):
    """User-customised filament-target JSON overrides the bundled defaults —
    raising PLA to 30°C makes a PLA-only print actually heat the chamber."""
    db = AsyncMock()
    client = _make_client()
    custom_map = '{"PLA": 30, "default": 0}'

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=custom_map)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(60.0, 31.0, trays=["PLA Basic"])
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_chamber_temperature.assert_called_once_with(30)


@pytest.mark.asyncio
async def test_malformed_filament_targets_falls_back_to_defaults(scheduler, item, archive):
    """A corrupted JSON in the setting must not break the scheduler — log
    and use bundled defaults."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value="not-json{{{")),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        # Bundled default for ABS = 45 → chamber should fire.
        pm.get_status.return_value = _make_state(60.0, 46.0, trays=["ABS"])
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_chamber_temperature.assert_called_once_with(45)


# ----------------------------------------------------------------------------
# Hardware-tier branching (unchanged from the first cut but updated for new
# fixtures that include AMS data so the derivation lands at a non-zero target).
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_bed_temperature_in_archive_skips(scheduler, item):
    """Archive without bed_temperature metadata skips entirely rather than
    guessing a default that might wreck a non-PLA print."""
    db = AsyncMock()
    client = _make_client()
    bare_archive = SimpleNamespace(bed_temperature=None)

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints()),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
    ):
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(trays=["ABS"])
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), bare_archive)

    client.set_bed_temperature.assert_not_called()
    client.set_chamber_temperature.assert_not_called()


@pytest.mark.asyncio
async def test_x1c_skips_m141_but_waits_passively(scheduler, item, archive):
    """X1C has a chamber sensor but no active heater — M141 must NOT fire even
    when the filament map derives a non-zero target."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        # ABS loaded → derived target 45; sensor reads 46 (already there).
        pm.get_status.return_value = _make_state(60.0, 46.0, trays=["ABS"])
        await scheduler._preheat_and_soak(db, item, _make_printer("X1C"), archive)

    client.set_bed_temperature.assert_called_once_with(60)
    client.set_chamber_temperature.assert_not_called()


@pytest.mark.asyncio
async def test_p1s_no_chamber_sensor_uses_soak_timer_only(scheduler, item, archive):
    """P1S has no chamber sensor — derived target is ignored for the wait
    loop, only the soak timer applies."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=600)),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()) as sleep_mock,
    ):
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(60.0, 0.0, trays=["ABS"])
        await scheduler._preheat_and_soak(db, item, _make_printer("P1S"), archive)

    client.set_bed_temperature.assert_called_once_with(60)
    client.set_chamber_temperature.assert_not_called()
    assert 600 in [call.args[0] for call in sleep_mock.call_args_list]


@pytest.mark.asyncio
async def test_lost_client_skips_silently(scheduler, item, archive):
    """If the MQTT client drops, the helper returns without raising."""
    db = AsyncMock()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints()),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
    ):
        pm.get_client.return_value = None
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)
    # No exception escaping — the disable path is silent.


@pytest.mark.asyncio
async def test_h2d_flips_airduct_to_heating_before_m141(scheduler, item, archive):
    """H-series + X2D have a cooling/heating airduct flap that DEFAULTS to
    cooling. If we energise M141 without first flipping to heating, the
    chamber fan actively extracts the heat we're trying to put in and the
    chamber never converges. Verify airduct=heating fires AND lands before
    the chamber-target call so the heater starts in the right airflow regime."""
    db = AsyncMock()
    client = _make_client()
    call_order = []
    client.set_airduct_mode.side_effect = lambda mode: call_order.append(("airduct", mode)) or True
    client.set_chamber_temperature.side_effect = lambda t: call_order.append(("chamber", t)) or True

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(60.0, 46.0, trays=["ABS"])
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_airduct_mode.assert_called_once_with("heating")
    client.set_chamber_temperature.assert_called_once_with(45)
    # Airduct heating must precede M141 — the heater enabling first while the
    # flap is still in cooling mode wastes minutes of fan-vs-heater tug-of-war.
    assert call_order == [("airduct", "heating"), ("chamber", 45)]


@pytest.mark.asyncio
async def test_x1c_skips_airduct_no_heater_no_call(scheduler, item, archive):
    """X1C has neither an active chamber heater nor an airduct flap (the
    frontend's airduct whitelist is P2S/X2D/H2D/H2C/H2S/H2D Pro — no X1
    series). The preheat stage's airduct call is gated on supports_airduct
    AND has_heater, so X1C gets neither call regardless. Important: a
    spurious set_airduct on X1C wouldn't just be wasted MQTT — there's no
    flap to set, so the firmware response would be undefined behaviour."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(60.0, 46.0, trays=["ABS"])
        await scheduler._preheat_and_soak(db, item, _make_printer("X1C"), archive)

    client.set_chamber_temperature.assert_not_called()
    client.set_airduct_mode.assert_not_called()


@pytest.mark.asyncio
async def test_normalize_filament_type_strips_at_space():
    """`PLA Basic` and `ABS Premium` should normalise to `PLA` and `ABS` so
    they match the map keys. `PA-CF` has no space and stays verbatim."""
    s = PrintScheduler
    assert s._normalize_filament_type("PLA Basic") == "PLA"
    assert s._normalize_filament_type("ABS Premium") == "ABS"
    assert s._normalize_filament_type("PA-CF") == "PA-CF"
    assert s._normalize_filament_type("") == ""
    assert s._normalize_filament_type("petg") == "PETG"  # case-folded


@pytest.mark.asyncio
async def test_get_preheat_filament_targets_defaults_when_missing(scheduler):
    """Empty / null setting → bundled defaults are used. _get_preheat_filament_targets
    upper-cases the keys, so the bundled `default` becomes `DEFAULT` on the
    returned dict — keep both spellings synced."""
    db = AsyncMock()
    with patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)):
        targets = await scheduler._get_preheat_filament_targets(db)
    # The bundled defaults dict is kept as-is on the "no setting" path, so
    # `default` (lowercase) is what callers see for that fallback.
    assert targets["PLA"] == 0
    assert targets["ABS"] == 45
    assert targets["PA-CF"] == 55
    # Either casing must resolve to the fallback 0.
    assert targets.get("default", targets.get("DEFAULT")) == 0


# ----------------------------------------------------------------------------
# Airduct mode switch (#1468 follow-up)
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h2d_chamber_heat_switches_airduct_to_heating(scheduler, item, archive):
    """H2D in cooling mode (the default; what you get after a PLA print)
    with chamber_target > 0 must switch the airduct to heating BEFORE the
    M141 dispatch — otherwise the open exhaust flap actively fights the
    chamber heater and the chamber never converges."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        # Currently in cooling (mode 0). ABS loaded → derived target 45.
        pm.get_status.return_value = _make_state(60.0, 46.0, trays=["ABS"], airduct_mode=0)
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_airduct_mode.assert_called_once_with("heating")
    client.set_chamber_temperature.assert_called_once_with(45)


@pytest.mark.asyncio
async def test_h2d_chamber_zero_switches_airduct_to_cooling(scheduler, item, archive):
    """H2D running a PLA print (chamber_target derives 0) on an airduct
    previously left in heating mode (from a prior ABS run) must switch
    back to cooling. Otherwise PLA prints inherit ABS's closed-flap recirc
    and run hot."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        # Currently in heating (mode 1). PLA loaded → derived target 0.
        pm.get_status.return_value = _make_state(60.0, 30.0, trays=["PLA"], airduct_mode=1)
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_airduct_mode.assert_called_once_with("cooling")
    client.set_chamber_temperature.assert_not_called()


@pytest.mark.asyncio
async def test_h2d_airduct_already_correct_idempotent(scheduler, item, archive):
    """If the airduct is already in the desired mode, don't re-send
    `set_airduct` — the firmware accepts it but it generates needless MQTT
    chatter and could thrash the flap motor on rapid repeats."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        # Already in heating (mode 1) and ABS → derived 45 wants heating.
        pm.get_status.return_value = _make_state(60.0, 46.0, trays=["ABS"], airduct_mode=1)
        await scheduler._preheat_and_soak(db, item, _make_printer("H2D"), archive)

    client.set_airduct_mode.assert_not_called()
    # But M141 still fires — the airduct decision is independent.
    client.set_chamber_temperature.assert_called_once_with(45)


@pytest.mark.asyncio
async def test_x1c_no_airduct_flap_never_fires_set_airduct(scheduler, item, archive):
    """X1C has a chamber sensor but no airduct flap — the firmware ignores
    `set_airduct`. We gate on `supports_airduct(model)` to avoid sending the
    no-op. Regression guard: wiring this to `supports_chamber_temp` or
    `supports_chamber_heater` alone would have leaked the command to
    X1C/X1E or P2S inappropriately."""
    db = AsyncMock()
    client = _make_client()

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(scheduler, "_get_int_setting", _ints(preheat_soak_seconds=0)),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(60.0, 46.0, trays=["ABS"], airduct_mode=0)
        await scheduler._preheat_and_soak(db, item, _make_printer("X1C"), archive)

    client.set_airduct_mode.assert_not_called()
