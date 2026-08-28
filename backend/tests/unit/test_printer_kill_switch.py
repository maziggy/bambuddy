from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app import main as main_module


@pytest.fixture(autouse=True)
def clear_kill_switch_state():
    main_module._kill_switch_setting_cache = None
    main_module._unauthorized_print_kill_sent.clear()
    main_module._kill_switch_notification_tasks.clear()
    main_module._expected_prints.clear()
    main_module._active_prints.clear()
    main_module._expected_print_registered_at.clear()
    main_module._printer_reconciled_since_connect.clear()
    yield
    for task in main_module._kill_switch_notification_tasks.values():
        if not task.done():
            task.cancel()
    main_module._unauthorized_print_kill_sent.clear()
    main_module._kill_switch_notification_tasks.clear()
    main_module._expected_prints.clear()
    main_module._active_prints.clear()
    main_module._expected_print_registered_at.clear()
    main_module._printer_reconciled_since_connect.clear()
    main_module._kill_switch_setting_cache = None


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
    broadcast = AsyncMock()
    provider_notification = AsyncMock(return_value=True)

    async def fake_status(*args, **kwargs):
        return None

    async def kill_switch_enabled(_db):
        return True

    unauthorized = AsyncMock(return_value=False)

    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    monkeypatch.setattr(
        main_module.printer_manager, "stop_print", lambda printer_id: stop_calls.append(printer_id) or True
    )
    monkeypatch.setattr(main_module.printer_manager, "get_printer", lambda printer_id: None)
    monkeypatch.setattr(main_module.printer_manager, "get_model", lambda printer_id: None)
    monkeypatch.setattr(main_module, "printer_state_to_dict", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_module.mqtt_relay, "on_printer_status", fake_status)
    monkeypatch.setattr(main_module.ws_manager, "send_printer_status", fake_status)
    monkeypatch.setattr(main_module.ws_manager, "broadcast", broadcast)
    monkeypatch.setattr(main_module, "_is_bambuddy_authorized_print", unauthorized)
    monkeypatch.setattr(main_module, "_send_kill_switch_provider_notification", provider_notification)
    monkeypatch.setattr("backend.app.services.finance_budget.is_printer_kill_switch_enabled", kill_switch_enabled)

    state = SimpleNamespace(
        connected=True,
        state="RUNNING",
        progress=0,
        remaining_time=0,
        layer_num=0,
        temperatures={},
        nozzles=[],
        raw_data={},
        stg_cur=0,
        # Real PrinterState always carries these; the status-broadcast dedup
        # key reads them so a Filament Track Switch rebind reaches the card.
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
        subtask_name="foreign_job",
        subtask_id="external-task-1",
        gcode_file="foreign_job.gcode",
    )

    await main_module.on_printer_status_change(7, state)
    await main_module.on_printer_status_change(7, state)

    assert stop_calls == [7]
    unauthorized.assert_awaited_once()
    assert 7 in main_module._unauthorized_print_kill_sent
    broadcast.assert_awaited_once_with(
        {
            "type": "kill_switch_triggered",
            "printer_id": 7,
            "printer_name": "Printer 7",
            "filename": "foreign_job",
            "reason": "unauthorized_print",
        }
    )
    notification_task = main_module._kill_switch_notification_tasks[7]
    assert await notification_task is True
    provider_notification.assert_awaited_once_with(
        7,
        "Printer 7",
        {
            "status": "stopped",
            "filename": "foreign_job.gcode",
            "subtask_name": "foreign_job",
            "progress": 0,
            "reason": "unauthorized_print",
        },
    )


@pytest.mark.asyncio
async def test_failed_immediate_notification_allows_completion_retry():
    task = main_module.spawn_background_task(_return_false(), name="test-kill-switch-notification-failure")

    assert await main_module._kill_switch_notification_already_sent(task) is False


async def _return_false():
    return False


@pytest.mark.asyncio
async def test_bambuddy_authorized_print_is_not_stopped(monkeypatch):
    monkeypatch.setitem(main_module._expected_prints, (7, "foreign_job"), 123)

    stop_calls: list[int] = []

    async def fake_status(*args, **kwargs):
        return None

    kill_switch_enabled = AsyncMock(return_value=True)

    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    monkeypatch.setattr(
        main_module.printer_manager, "stop_print", lambda printer_id: stop_calls.append(printer_id) or True
    )
    monkeypatch.setattr(main_module.printer_manager, "get_printer", lambda printer_id: None)
    monkeypatch.setattr(main_module.printer_manager, "get_model", lambda printer_id: None)
    monkeypatch.setattr(main_module, "printer_state_to_dict", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_module.mqtt_relay, "on_printer_status", fake_status)
    monkeypatch.setattr(main_module.ws_manager, "send_printer_status", fake_status)
    monkeypatch.setattr("backend.app.services.finance_budget.is_printer_kill_switch_enabled", kill_switch_enabled)

    state = SimpleNamespace(
        connected=True,
        state="RUNNING",
        progress=0,
        remaining_time=0,
        layer_num=0,
        temperatures={},
        nozzles=[],
        raw_data={},
        stg_cur=0,
        # Real PrinterState always carries these; the status-broadcast dedup
        # key reads them so a Filament Track Switch rebind reaches the card.
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
        subtask_name="foreign_job",
        gcode_file="foreign_job.gcode",
    )

    await main_module.on_printer_status_change(7, state)

    assert stop_calls == []
    assert 7 not in main_module._unauthorized_print_kill_sent
    kill_switch_enabled.assert_not_awaited()


@pytest.mark.asyncio
async def test_kill_switch_setting_is_cached(monkeypatch):
    kill_switch_enabled = AsyncMock(return_value=True)

    class FakeSessionContext:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(main_module, "async_session", FakeSessionContext)
    monkeypatch.setattr("backend.app.services.finance_budget.is_printer_kill_switch_enabled", kill_switch_enabled)

    assert await main_module._is_printer_kill_switch_enabled_cached() is True
    assert await main_module._is_printer_kill_switch_enabled_cached() is True
    kill_switch_enabled.assert_awaited_once()


@pytest.mark.asyncio
async def test_unauthorized_print_state_is_cleared_when_print_ends(monkeypatch):
    stop_calls: list[int] = []

    async def fake_status(*args, **kwargs):
        return None

    async def kill_switch_enabled(_db):
        return True

    async def unauthorized(*_args):
        return False

    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    monkeypatch.setattr(
        main_module.printer_manager, "stop_print", lambda printer_id: stop_calls.append(printer_id) or True
    )
    monkeypatch.setattr(main_module.printer_manager, "get_printer", lambda printer_id: None)
    monkeypatch.setattr(main_module.printer_manager, "get_model", lambda printer_id: None)
    monkeypatch.setattr(main_module, "printer_state_to_dict", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_module.mqtt_relay, "on_printer_status", fake_status)
    monkeypatch.setattr(main_module.ws_manager, "send_printer_status", fake_status)
    monkeypatch.setattr(main_module, "_is_bambuddy_authorized_print", unauthorized)
    monkeypatch.setattr("backend.app.services.finance_budget.is_printer_kill_switch_enabled", kill_switch_enabled)

    active_state = SimpleNamespace(
        connected=True,
        state="RUNNING",
        progress=0,
        remaining_time=0,
        layer_num=0,
        temperatures={},
        nozzles=[],
        raw_data={},
        stg_cur=0,
        # Real PrinterState always carries these; the status-broadcast dedup
        # key reads them so a Filament Track Switch rebind reaches the card.
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
        subtask_name="foreign_job",
        subtask_id="external-task-1",
        gcode_file="foreign_job.gcode",
    )

    idle_state = SimpleNamespace(
        connected=True,
        state="IDLE",
        progress=0,
        remaining_time=0,
        layer_num=0,
        temperatures={},
        nozzles=[],
        raw_data={},
        stg_cur=0,
        # Real PrinterState always carries these; the status-broadcast dedup
        # key reads them so a Filament Track Switch rebind reaches the card.
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
        subtask_name="",
        subtask_id=None,
        gcode_file=None,
    )

    await main_module.on_printer_status_change(7, active_state)
    assert stop_calls == [7]
    assert 7 in main_module._unauthorized_print_kill_sent

    await main_module.on_printer_status_change(7, idle_state)

    assert 7 not in main_module._unauthorized_print_kill_sent


@pytest.mark.asyncio
@pytest.mark.parametrize("printer_state", ["RUNNING", "PAUSE"])
async def test_persisted_print_is_authorized_after_restart(monkeypatch, printer_state):
    # billing_run_id is the marker the scheduler stamps on its own dispatches;
    # an archive without one proves only that Bambuddy watched the print.
    archive = SimpleNamespace(
        id=123,
        filename="owned_job.gcode.3mf",
        billing_run_id="d7c1f0b2-0000-4000-8000-000000000001",
        created_by_id=None,
    )
    query_result = SimpleNamespace(scalar_one_or_none=lambda: archive)
    db = SimpleNamespace(execute=AsyncMock(return_value=query_result))

    class FakeSessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return False

    stop_calls: list[int] = []

    async def fake_status(*args, **kwargs):
        return None

    async def kill_switch_enabled(_db):
        return True

    def discard_background_task(coro, **_kwargs):
        coro.close()

    monkeypatch.setattr(main_module, "async_session", FakeSessionContext)
    monkeypatch.setattr(main_module, "spawn_background_task", discard_background_task)
    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    monkeypatch.setattr(
        main_module.printer_manager, "stop_print", lambda printer_id: stop_calls.append(printer_id) or True
    )
    monkeypatch.setattr(main_module.printer_manager, "get_printer", lambda printer_id: None)
    monkeypatch.setattr(main_module.printer_manager, "get_model", lambda printer_id: None)
    monkeypatch.setattr(main_module, "printer_state_to_dict", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_module.mqtt_relay, "on_printer_status", fake_status)
    monkeypatch.setattr(main_module.ws_manager, "send_printer_status", fake_status)
    monkeypatch.setattr("backend.app.services.finance_budget.is_printer_kill_switch_enabled", kill_switch_enabled)

    state = SimpleNamespace(
        connected=True,
        state=printer_state,
        progress=42,
        remaining_time=600,
        layer_num=50,
        temperatures={},
        nozzles=[],
        raw_data={},
        stg_cur=0,
        # Real PrinterState always carries these; the status-broadcast dedup
        # key reads them so a Filament Track Switch rebind reaches the card.
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
        subtask_name="owned_job",
        subtask_id="bambuddy-task-123",
        gcode_file="owned_job.gcode.3mf",
    )

    await main_module.on_printer_status_change(7, state)

    assert stop_calls == []
    assert (7, "owned_job.gcode.3mf") in main_module._active_prints
    assert main_module._active_prints[(7, "owned_job.gcode.3mf")] == 123
    assert 7 not in main_module._unauthorized_print_kill_sent


@pytest.mark.asyncio
async def test_kill_switch_defers_when_restart_identity_is_not_available(monkeypatch):
    state = SimpleNamespace(
        current_print=None,
        subtask_name="owned_job",
        subtask_id=None,
        gcode_file="owned_job.gcode.3mf",
    )
    db = SimpleNamespace(execute=AsyncMock())

    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)

    authorization = await main_module._is_bambuddy_authorized_print(7, state, db)

    assert authorization is None
    db.execute.assert_not_awaited()


def _authorization_db(archive, dispatched_queue_item_id=None):
    """Fake session answering the two lookups `_is_bambuddy_authorized_print` makes."""

    query_result = SimpleNamespace(scalar_one_or_none=lambda: archive)
    return SimpleNamespace(
        execute=AsyncMock(return_value=query_result),
        scalar=AsyncMock(return_value=dispatched_queue_item_id),
    )


def _running_state(subtask_id="external-task-9"):
    return SimpleNamespace(
        current_print=None,
        subtask_name="some_job",
        subtask_id=subtask_id,
        gcode_file="some_job.gcode.3mf",
    )


@pytest.mark.asyncio
async def test_archive_without_a_dispatch_marker_is_not_authorization(monkeypatch):
    """on_print_start archives prints started from Studio or Handy too.

    Those rows carry the same status and subtask_id as Bambuddy's own, so treating
    the row's existence as proof would switch the feature off a few seconds into
    every foreign print — as soon as the 3MF finished downloading.
    """
    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    observed_only = SimpleNamespace(
        id=55,
        filename="some_job.gcode.3mf",
        billing_run_id=None,
        created_by_id=None,
    )
    db = _authorization_db(observed_only, dispatched_queue_item_id=None)

    assert await main_module._is_bambuddy_authorized_print(9, _running_state(), db) is False
    assert (9, "some_job.gcode.3mf") not in main_module._active_prints


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "marker",
    [
        {"billing_run_id": "9f0c2b6e-0000-4000-8000-00000000abcd", "created_by_id": None},
        {"billing_run_id": None, "created_by_id": 4},
    ],
    ids=["billing_run_id", "created_by_id"],
)
async def test_either_dispatch_marker_authorizes_after_a_restart(monkeypatch, marker):
    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    archive = SimpleNamespace(id=77, filename="some_job.gcode.3mf", **marker)
    db = _authorization_db(archive, dispatched_queue_item_id=None)

    assert await main_module._is_bambuddy_authorized_print(9, _running_state(), db) is True
    assert main_module._active_prints[(9, "some_job.gcode.3mf")] == 77
    # The fast path is rehydrated, so the queue is never consulted.
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_defers_while_bambuddy_has_a_job_running_on_that_printer(monkeypatch):
    """A library-file dispatch has no archive at send time, and the row created for
    it moments later by on_print_start carries neither marker. The queue row is the
    only durable trace, and it cannot be tied to a subtask_id — so it defers."""
    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    unmarked = SimpleNamespace(id=56, filename="some_job.gcode.3mf", billing_run_id=None, created_by_id=None)
    db = _authorization_db(unmarked, dispatched_queue_item_id=310)

    assert await main_module._is_bambuddy_authorized_print(9, _running_state(), db) is None
    # Deferring must not authorize the print for every later frame.
    assert (9, "some_job.gcode.3mf") not in main_module._active_prints


@pytest.mark.asyncio
async def test_defers_when_the_dispatch_has_not_been_archived_yet(monkeypatch):
    """Restart during the window between the MQTT send and the 3MF download."""
    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    db = _authorization_db(None, dispatched_queue_item_id=311)

    assert await main_module._is_bambuddy_authorized_print(9, _running_state(), db) is None


@pytest.mark.asyncio
async def test_foreign_print_with_no_archive_and_no_dispatch_is_unauthorized(monkeypatch):
    monkeypatch.setattr(main_module.printer_manager, "get_current_print_user", lambda printer_id: None)
    db = _authorization_db(None, dispatched_queue_item_id=None)

    assert await main_module._is_bambuddy_authorized_print(9, _running_state(), db) is False
