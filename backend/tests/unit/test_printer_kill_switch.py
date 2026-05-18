from types import SimpleNamespace

import pytest

from backend.app import main as main_module


@pytest.fixture(autouse=True)
def clear_kill_switch_state():
    main_module._unauthorized_print_kill_sent.clear()
    main_module._expected_prints.clear()
    main_module._active_prints.clear()
    main_module._expected_print_registered_at.clear()
    yield
    main_module._unauthorized_print_kill_sent.clear()
    main_module._expected_prints.clear()
    main_module._active_prints.clear()
    main_module._expected_print_registered_at.clear()


def test_gcode_3mf_status_filename_matches_registered_expected_print():
    state = SimpleNamespace(
        current_print=None,
        subtask_name="",
        gcode_file="foreign_job.gcode.3mf",
    )

    keys = main_module._build_status_print_keys(7, state)

    assert (7, "foreign_job.gcode.3mf") in keys
    assert (7, "foreign_job.gcode") in keys


@pytest.mark.asyncio
async def test_unauthorized_active_print_triggers_stop(monkeypatch):
    stop_calls: list[int] = []

    async def fake_status(*args, **kwargs):
        return None

    async def kill_switch_enabled(_db):
        return True

    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    monkeypatch.setattr(
        main_module.printer_manager, "stop_print", lambda printer_id: stop_calls.append(printer_id) or True
    )
    monkeypatch.setattr(main_module.printer_manager, "get_printer", lambda printer_id: None)
    monkeypatch.setattr(main_module.printer_manager, "get_model", lambda printer_id: None)
    monkeypatch.setattr(main_module, "printer_state_to_dict", lambda state, printer_id, model: {})
    monkeypatch.setattr(main_module.mqtt_relay, "on_printer_status", fake_status)
    monkeypatch.setattr(main_module.ws_manager, "send_printer_status", fake_status)
    monkeypatch.setattr("backend.app.services.finance_budget.is_printer_kill_switch_enabled", kill_switch_enabled)

    state = SimpleNamespace(
        connected=True,
        state="RUNNING",
        progress=0,
        layer_num=0,
        temperatures={},
        raw_data={},
        stg_cur=0,
        cooling_fan_speed=None,
        big_fan1_speed=None,
        big_fan2_speed=None,
        chamber_light=False,
        active_extruder=0,
        tray_now=255,
        door_open=False,
        current_print=None,
        subtask_name="foreign_job",
        gcode_file="foreign_job.gcode",
    )

    await main_module.on_printer_status_change(7, state)

    assert stop_calls == [7]
    assert 7 in main_module._unauthorized_print_kill_sent


@pytest.mark.asyncio
async def test_bambuddy_authorized_print_is_not_stopped(monkeypatch):
    monkeypatch.setitem(main_module._expected_prints, (7, "foreign_job"), 123)

    stop_calls: list[int] = []

    async def fake_status(*args, **kwargs):
        return None

    async def kill_switch_enabled(_db):
        return True

    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    monkeypatch.setattr(
        main_module.printer_manager, "stop_print", lambda printer_id: stop_calls.append(printer_id) or True
    )
    monkeypatch.setattr(main_module.printer_manager, "get_printer", lambda printer_id: None)
    monkeypatch.setattr(main_module.printer_manager, "get_model", lambda printer_id: None)
    monkeypatch.setattr(main_module, "printer_state_to_dict", lambda state, printer_id, model: {})
    monkeypatch.setattr(main_module.mqtt_relay, "on_printer_status", fake_status)
    monkeypatch.setattr(main_module.ws_manager, "send_printer_status", fake_status)
    monkeypatch.setattr("backend.app.services.finance_budget.is_printer_kill_switch_enabled", kill_switch_enabled)

    state = SimpleNamespace(
        connected=True,
        state="RUNNING",
        progress=0,
        layer_num=0,
        temperatures={},
        raw_data={},
        stg_cur=0,
        cooling_fan_speed=None,
        big_fan1_speed=None,
        big_fan2_speed=None,
        chamber_light=False,
        active_extruder=0,
        tray_now=255,
        door_open=False,
        current_print=None,
        subtask_name="foreign_job",
        gcode_file="foreign_job.gcode",
    )

    await main_module.on_printer_status_change(7, state)

    assert stop_calls == []
    assert 7 not in main_module._unauthorized_print_kill_sent


@pytest.mark.asyncio
async def test_unauthorized_print_state_is_cleared_when_print_ends(monkeypatch):
    stop_calls: list[int] = []

    async def fake_status(*args, **kwargs):
        return None

    async def kill_switch_enabled(_db):
        return True

    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    monkeypatch.setattr(
        main_module.printer_manager, "stop_print", lambda printer_id: stop_calls.append(printer_id) or True
    )
    monkeypatch.setattr(main_module.printer_manager, "get_printer", lambda printer_id: None)
    monkeypatch.setattr(main_module.printer_manager, "get_model", lambda printer_id: None)
    monkeypatch.setattr(main_module, "printer_state_to_dict", lambda state, printer_id, model: {})
    monkeypatch.setattr(main_module.mqtt_relay, "on_printer_status", fake_status)
    monkeypatch.setattr(main_module.ws_manager, "send_printer_status", fake_status)
    monkeypatch.setattr("backend.app.services.finance_budget.is_printer_kill_switch_enabled", kill_switch_enabled)

    active_state = SimpleNamespace(
        connected=True,
        state="RUNNING",
        progress=0,
        layer_num=0,
        temperatures={},
        raw_data={},
        stg_cur=0,
        cooling_fan_speed=None,
        big_fan1_speed=None,
        big_fan2_speed=None,
        chamber_light=False,
        active_extruder=0,
        tray_now=255,
        door_open=False,
        current_print=None,
        subtask_name="foreign_job",
        gcode_file="foreign_job.gcode",
    )

    idle_state = SimpleNamespace(
        connected=True,
        state="IDLE",
        progress=0,
        layer_num=0,
        temperatures={},
        raw_data={},
        stg_cur=0,
        cooling_fan_speed=None,
        big_fan1_speed=None,
        big_fan2_speed=None,
        chamber_light=False,
        active_extruder=0,
        tray_now=255,
        door_open=False,
        current_print=None,
        subtask_name="",
        gcode_file=None,
    )

    await main_module.on_printer_status_change(7, active_state)
    assert stop_calls == [7]
    assert 7 in main_module._unauthorized_print_kill_sent

    await main_module.on_printer_status_change(7, idle_state)

    assert 7 not in main_module._unauthorized_print_kill_sent
