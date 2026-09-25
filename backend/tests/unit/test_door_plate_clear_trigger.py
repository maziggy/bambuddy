"""Door-sensor release of the plate-clear gate (#2805).

The gate itself is unchanged: these tests pin down what may open it. Every case
below is one of the four things six weeks of running this as an external sidecar
on a ten-printer farm got wrong first — level instead of edge, no debounce, no
state constraint, and inferring a closed door from a printer that reports none.
"""

from types import SimpleNamespace

import pytest

from backend.app import main as main_module


@pytest.fixture(autouse=True)
def clear_door_state():
    main_module._printer_door_stable.clear()
    main_module._printer_door_candidate.clear()
    main_module._door_trigger_setting_cache = None
    yield
    main_module._printer_door_stable.clear()
    main_module._printer_door_candidate.clear()
    main_module._door_trigger_setting_cache = None


@pytest.fixture
def released(monkeypatch):
    """Record every plate-clear release, with the gate up and the trigger armed."""
    calls: list[tuple[int, bool]] = []

    monkeypatch.setattr(main_module.printer_manager, "is_awaiting_plate_clear", lambda printer_id: True)
    monkeypatch.setattr(
        main_module.printer_manager,
        "set_awaiting_plate_clear",
        lambda printer_id, awaiting: calls.append((printer_id, awaiting)),
    )
    monkeypatch.setattr(main_module, "_is_door_plate_clear_trigger_armed_cached", _returning(True))
    monkeypatch.setattr(main_module, "_is_door_trigger_enabled_for_printer", _returning(True))
    return calls


def _returning(value):
    async def _coro(*args, **kwargs):
        return value

    return _coro


def _state(door_open, state="FINISH"):
    return SimpleNamespace(door_open=door_open, state=state)


async def _feed(printer_id, readings, state="FINISH"):
    """Push a run of door readings through the trigger, one per status frame."""
    for door_open in readings:
        await main_module._maybe_release_plate_clear_on_door(printer_id, _state(door_open, state))


@pytest.mark.asyncio
async def test_open_then_closed_releases_the_gate(released):
    await _feed(1, [False, False, True, True, False, False])

    assert released == [(1, False)]


@pytest.mark.asyncio
async def test_a_door_that_is_merely_closed_releases_nothing(released):
    # The resting state of every door. Releasing on the level here would clear
    # the plate the instant a print finished, and then continuously.
    await _feed(1, [False] * 6)

    assert released == []


@pytest.mark.asyncio
async def test_a_single_flicker_to_closed_is_not_an_edge(released):
    # Open, one chattering "closed" frame, open again: below the debounce, so
    # the door never actually left the open state.
    await _feed(1, [True, True, False, True, True])

    assert released == []


@pytest.mark.asyncio
async def test_a_single_flicker_to_open_cannot_arm_a_release(released):
    # A door that never confirmed "open" has no edge to close from, so the
    # steady closed readings that follow are just the resting state.
    await _feed(1, [False, False, True, False, False, False])

    assert released == []


@pytest.mark.asyncio
async def test_the_first_reading_only_establishes_the_resting_state(released):
    # A printer whose very first confirmed reading is "closed" has no
    # predecessor to have been open.
    await _feed(1, [False, False])

    assert released == []


@pytest.mark.asyncio
async def test_a_printer_reporting_no_door_is_left_alone(released):
    await _feed(1, [None, None, None, None])

    assert released == []
    assert 1 not in main_module._printer_door_stable


@pytest.mark.asyncio
async def test_a_door_permanently_reported_closed_never_fires(released):
    # A P1S reports its door as closed even though the model has none (#1866).
    # No allow-list keeps it out: with no open→closed transition there is
    # nothing for the trigger to act on.
    await _feed(1, [False] * 20)

    assert released == []


@pytest.mark.asyncio
async def test_a_door_opened_mid_print_releases_nothing(released):
    await _feed(1, [False, False, True, True, False, False], state="RUNNING")

    assert released == []


@pytest.mark.asyncio
async def test_a_door_closing_on_an_idle_printer_releases_the_gate(released):
    # Auto Power Off cycles the printer after it finishes; it boots into IDLE
    # with the gate still up (#961), and that plate still needs clearing.
    await _feed(1, [False, False, True, True, False, False], state="IDLE")

    assert released == [(1, False)]


@pytest.mark.asyncio
async def test_a_printer_not_awaiting_its_plate_releases_nothing(monkeypatch):
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(main_module.printer_manager, "is_awaiting_plate_clear", lambda printer_id: False)
    monkeypatch.setattr(
        main_module.printer_manager,
        "set_awaiting_plate_clear",
        lambda printer_id, awaiting: calls.append((printer_id, awaiting)),
    )
    monkeypatch.setattr(main_module, "_is_door_plate_clear_trigger_armed_cached", _returning(True))
    monkeypatch.setattr(main_module, "_is_door_trigger_enabled_for_printer", _returning(True))

    await _feed(1, [False, False, True, True, False, False])

    assert calls == []


@pytest.mark.asyncio
async def test_the_trigger_set_to_manual_releases_nothing(released, monkeypatch):
    monkeypatch.setattr(main_module, "_is_door_plate_clear_trigger_armed_cached", _returning(False))

    await _feed(1, [False, False, True, True, False, False])

    assert released == []


@pytest.mark.asyncio
async def test_a_printer_opted_out_releases_nothing(released, monkeypatch):
    monkeypatch.setattr(main_module, "_is_door_trigger_enabled_for_printer", _returning(False))

    await _feed(1, [False, False, True, True, False, False])

    assert released == []


@pytest.mark.asyncio
async def test_each_printer_keeps_its_own_door_history(released):
    # Interleaved frames from two printers: only the one that saw a full
    # open→closed cycle is released.
    await _feed(1, [False, False, True, True])
    await _feed(2, [False, False])
    await _feed(1, [False, False])

    assert released == [(1, False)]


@pytest.mark.asyncio
async def test_identical_status_frames_still_reach_the_trigger(monkeypatch):
    """The debounce needs the frame the broadcast dedup would have dropped.

    ``on_printer_status_change`` returns early when a frame carries the same
    status key as the last one, and a door reading only counts once a second
    frame agrees with it. On a finished printer nothing else is moving, so that
    confirming frame is exactly the one the dedup discards — which is why the
    trigger runs ahead of it.
    """

    seen: list[int] = []

    # This printer is already reconciled for this connection, so the handler
    # does not spawn a stale-print reconcile the test would have to await.
    monkeypatch.setitem(main_module._printer_reconciled_since_connect, 99, True)

    async def record(printer_id, state):
        seen.append(printer_id)

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(main_module, "_maybe_release_plate_clear_on_door", record)
    monkeypatch.setattr(main_module.printer_manager, "get_printer", lambda printer_id: None)
    monkeypatch.setattr(main_module.printer_manager, "get_model", lambda printer_id: None)
    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    monkeypatch.setattr(main_module.printer_manager, "is_awaiting_plate_clear", lambda printer_id: True)
    monkeypatch.setattr(main_module, "printer_state_to_dict", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_module.mqtt_relay, "on_printer_status", nothing)
    monkeypatch.setattr(main_module.ws_manager, "send_printer_status", nothing)

    state = SimpleNamespace(
        connected=True,
        state="FINISH",
        progress=100,
        remaining_time=0,
        layer_num=0,
        temperatures={},
        nozzles=[],
        raw_data={},
        stg_cur=0,
        fila_switch=None,
        ams_switch_inlet={},
        extruder_slots={},
        cooling_fan_speed=None,
        big_fan1_speed=None,
        big_fan2_speed=None,
        chamber_light=False,
        active_extruder=0,
        tray_now=255,
        door_open=False,
        ams_filament_backup=False,
        current_print=None,
        subtask_name="job",
        subtask_id="task-1",
        gcode_file="job.gcode",
    )

    await main_module.on_printer_status_change(99, state)
    await main_module.on_printer_status_change(99, state)

    assert seen == [99, 99]
