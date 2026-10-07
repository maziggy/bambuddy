"""Persisted Notify widget ownership and write cadence against a fake gateway."""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401
from backend.app.core.database import Base
from backend.app.models.notification import NotificationProvider
from backend.app.models.notification_lock_screen_widget import NotificationLockScreenWidget
from backend.app.models.printer import Printer
from backend.app.services import notify_widgets as module
from backend.app.services.notify_client import NotifyError
from backend.app.services.notify_widgets import NotifyWidgetService, widget_content


def state(**values):
    defaults = {
        "connected": True,
        "state": "RUNNING",
        "subtask_id": "job1",
        "subtask_name": "job.3mf",
        "current_print": "job.3mf",
        "progress": 42,
        "remaining_time": 15,
        "layer_num": 42,
        "total_layers": 100,
        "hms_errors": [],
        "raw_data": {},
        "temperatures": {},
        "stg_cur": -1,
    }
    return SimpleNamespace(**{**defaults, **values})


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'widgets.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_fk(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Printer(id=1, name="Printer 1", serial_number="serial1", ip_address="127.0.0.1", access_code="12345678"))
        db.add(
            NotificationProvider(
                id=1,
                name="Phone",
                provider_type="notify",
                enabled=True,
                config=json.dumps({"device_id": "ABC12345", "token": "secret", "lock_screen_widgets": True}),
                on_print_start=False,
                on_print_complete=False,
                daily_digest_enabled=True,
                quiet_hours_enabled=True,
                quiet_hours_start="00:00",
                quiet_hours_end="23:59",
            )
        )
        await db.commit()
    api = SimpleNamespace(
        create_widget=AsyncMock(return_value={"widgetId": "WG123456"}),
        update_widget=AsyncMock(return_value={"widgetId": "WG123456"}),
        delete_widget=AsyncMock(return_value={"success": True}),
        list_widgets=AsyncMock(return_value={"widgets": []}),
    )
    clock = [datetime(2026, 1, 1, 12)]
    monkeypatch.setattr(module, "_now", lambda: clock[0])
    states = {1: state()}
    service = NotifyWidgetService(factory, api, states.get)
    yield service, api, factory, clock, states
    await engine.dispose()


async def rows(factory):
    async with factory() as db:
        return (await db.scalars(select(NotificationLockScreenWidget))).all()


async def configure(factory, **values):
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        config = json.loads(provider.config)
        config.update(values)
        provider.config = json.dumps(config)
        await db.commit()


@pytest.mark.asyncio
async def test_default_opt_out_and_independent_of_push_digest_quiet_hours(setup):
    service, api, factory, clock, states = setup
    await configure(factory, lock_screen_widgets=False)
    await service.tick()
    api.create_widget.assert_not_awaited()
    await configure(factory, lock_screen_widgets=True)
    await service.tick()
    api.create_widget.assert_awaited_once()
    assert (await rows(factory))[0].widget_id == "WG123456"


@pytest.mark.asyncio
async def test_restart_reuses_exact_widget_across_jobs_and_completion(setup):
    service, api, factory, clock, states = setup
    await service.tick()
    restarted = NotifyWidgetService(factory, api, states.get)
    states[1] = state(state="FINISH", progress=100)
    clock[0] += timedelta(minutes=1)
    await restarted.tick()
    assert api.update_widget.call_args.args[:2] == ("WG123456", "secret")
    assert api.update_widget.call_args.args[2]["value"] == "Complete"
    states[1] = state(subtask_id="job2", progress=2)
    clock[0] += timedelta(minutes=1)
    await restarted.tick()
    assert api.create_widget.await_count == 1
    assert api.update_widget.call_args.args[2]["value"] == "2"
    api.delete_widget.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_changed_values_send_and_at_most_once_per_minute(setup):
    service, api, factory, clock, states = setup
    await service.tick()
    states[1] = state(progress=43)
    clock[0] += timedelta(seconds=59)
    await service.tick()
    api.update_widget.assert_not_awaited()
    clock[0] += timedelta(seconds=1)
    await service.tick()
    assert api.update_widget.await_count == 1
    clock[0] += timedelta(minutes=60)
    await service.tick()
    assert api.update_widget.await_count == 1
    api.list_widgets.assert_awaited_once_with("ABC12345", "secret")


@pytest.mark.asyncio
async def test_offline_and_idle_widgets_are_persistent(setup):
    service, api, factory, clock, states = setup
    states.clear()
    await service.tick()
    assert api.create_widget.call_args.args[2]["value"] == "Offline"
    states[1] = state(state="IDLE")
    clock[0] += timedelta(minutes=1)
    await service.tick()
    assert api.update_widget.call_args.args[2]["value"] == "Idle"
    assert api.create_widget.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("device_id", ["GRP12345", "WB123456", "MC123456"])
async def test_unsupported_devices_are_push_only(setup, device_id):
    service, api, factory, clock, states = setup
    await configure(factory, device_id=device_id)
    await service.tick()
    api.create_widget.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_create_is_not_repeated_and_has_manual_recovery_message(setup):
    service, api, factory, clock, states = setup
    api.create_widget.side_effect = NotifyError("Timeout", delivery_state="unknown")
    await service.tick()
    clock[0] += timedelta(hours=2)
    restarted = NotifyWidgetService(factory, api, states.get)
    await restarted.tick()
    assert api.create_widget.await_count == 1
    assert (await rows(factory))[0].state == "uncertain"
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        assert "turn Lock Screen widgets off and on" in provider.last_error
    # Explicit opt-out clears unknown ownership after the user can inspect Notify.
    await configure(factory, lock_screen_widgets=False)
    await restarted.tick()
    assert await rows(factory) == []
    await configure(factory, lock_screen_widgets=True)
    api.create_widget.side_effect = None
    await restarted.tick()
    assert api.create_widget.await_count == 2


@pytest.mark.asyncio
async def test_process_crash_after_create_intent_does_not_duplicate(setup):
    service, api, factory, clock, states = setup
    await service.tick()
    async with factory() as db:
        row = await db.get(NotificationLockScreenWidget, 1)
        row.state, row.widget_id = "uncertain", None
        await db.commit()
    restarted = NotifyWidgetService(factory, api, states.get)
    await restarted.tick()
    assert api.create_widget.await_count == 1
    async with factory() as db:
        assert "Previous creation was interrupted" in (await db.get(NotificationProvider, 1)).last_error


@pytest.mark.asyncio
async def test_ambiguous_create_with_id_recovers_exact_id(setup):
    service, api, factory, clock, states = setup
    api.create_widget.side_effect = NotifyError(
        "unknown", status_code=502, payload={"widgetId": "WG123456"}, delivery_state="unknown"
    )
    await service.tick()
    clock[0] += timedelta(minutes=2)
    await service.tick()
    assert api.create_widget.await_count == 1
    assert api.update_widget.call_args.args[0] == "WG123456"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        NotifyError("capacity", status_code=400, payload={"message": "Maximum of 10 widgets"}),
        NotifyError("disabled", status_code=503),
        NotifyError("throttled", status_code=429, retry_after_seconds=1800),
    ],
)
async def test_retryable_creates_back_off(setup, error):
    service, api, factory, clock, states = setup
    api.create_widget.side_effect = error
    await service.tick()
    await service.tick()
    assert api.create_widget.await_count == 1
    clock[0] += timedelta(minutes=31)
    api.create_widget.side_effect = None
    await service.tick()
    assert api.create_widget.await_count == 2


@pytest.mark.asyncio
async def test_delete_retry_survives_restart_and_reenable(setup):
    service, api, factory, clock, states = setup
    await service.tick()
    await configure(factory, lock_screen_widgets=False)
    api.delete_widget.side_effect = NotifyError("timeout", delivery_state="unknown")
    await service.tick()
    assert (await rows(factory))[0].state == "deleting"
    restarted = NotifyWidgetService(factory, api, states.get)
    await configure(factory, lock_screen_widgets=True)
    await restarted.tick()
    assert api.create_widget.await_count == 1
    assert api.delete_widget.await_count == 1
    clock[0] += timedelta(minutes=2)
    api.delete_widget.side_effect = None
    await restarted.tick()
    await restarted.tick()
    assert api.create_widget.await_count == 2


@pytest.mark.asyncio
async def test_deleted_printer_widget_is_deleted_with_fk_enforced(setup):
    service, api, factory, clock, states = setup
    await service.tick()
    async with factory() as db:
        await db.delete(await db.get(Printer, 1))
        await db.commit()
    await service.tick()
    api.delete_widget.assert_awaited_once_with("WG123456", "secret")
    assert await rows(factory) == []


@pytest.mark.asyncio
async def test_cleanup_addresses_exact_saved_widget_with_old_token(setup):
    service, api, factory, clock, states = setup
    await service.tick()
    await service.cleanup_provider(1, {"device_id": "ABC12345", "token": "secret"})
    api.delete_widget.assert_awaited_once_with("WG123456", "secret")
    assert await rows(factory) == []


@pytest.mark.asyncio
async def test_deleted_remote_widget_403_is_confirmed_by_device_list(setup):
    service, api, factory, clock, states = setup
    await service.tick()
    await configure(factory, lock_screen_widgets=False)
    api.delete_widget.side_effect = NotifyError("Forbidden", status_code=403)
    await service.tick()
    assert api.list_widgets.await_count == 2
    assert api.list_widgets.call_args.args == ("ABC12345", "secret")
    assert await rows(factory) == []


@pytest.mark.asyncio
async def test_bad_credentials_do_not_discard_widget_needing_cleanup(setup):
    service, api, factory, clock, states = setup
    await service.tick()
    await configure(factory, lock_screen_widgets=False)
    api.delete_widget.side_effect = NotifyError("Forbidden", status_code=403)
    api.list_widgets.side_effect = NotifyError("Forbidden", status_code=403)
    await service.tick()
    assert (await rows(factory))[0].state == "deleting"


@pytest.mark.asyncio
async def test_success_does_not_clear_push_error(setup):
    service, api, factory, clock, states = setup
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        provider.last_error = "Push failed"
        await db.commit()
    await service.tick()
    async with factory() as db:
        assert (await db.get(NotificationProvider, 1)).last_error == "Push failed"


@pytest.mark.asyncio
async def test_no_database_session_during_http_and_concurrent_ticks_deduplicate(setup):
    service, api, factory, clock, states = setup
    entered = 0

    class TrackedSession:
        async def __aenter__(self):
            nonlocal entered
            entered += 1
            self.db = factory()
            return await self.db.__aenter__()

        async def __aexit__(self, *args):
            nonlocal entered
            await self.db.__aexit__(*args)
            entered -= 1

    service._session = TrackedSession

    async def send(*args):
        assert entered == 0
        await asyncio.sleep(0)
        return {"widgetId": "WG123456"}

    api.create_widget.side_effect = send
    await asyncio.gather(service.tick(), service.tick())
    assert api.create_widget.await_count == 1


def test_faults_replace_gauge_and_advisories_do_not():
    fault = SimpleNamespace(description="Filament has run out", severity=2, full_code="07004000", actions=None)
    content = widget_content("P1", state(hms_errors=[fault]))
    assert content["value"] == "Error" and content["detail"] == "Filament has run out"
    assert content["unit"] is None and content["progress"] is None
    advisory = SimpleNamespace(description="Cover is open", severity=3, full_code="0000000012345678", actions=None)
    content = widget_content("P1", state(hms_errors=[advisory]))
    assert content["value"] == "42" and content["progress"] == 42
    assert "endsIn" not in content and "metrics" not in content


def test_payload_caps_and_static_eta():
    content = widget_content("🖨" * 120, state(remaining_time=1500))
    assert "25h 0m" in content["detail"]
    assert len(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode()) <= 1024
    content = widget_content("P1", state(state="PAUSE"))
    assert content["value"] == "Paused" and content["progress"] is None


@pytest.mark.asyncio
async def test_opt_out_during_create_cannot_be_overwritten_by_response(setup):
    service, api, factory, clock, states = setup

    async def create(*args):
        async with factory() as db:
            await db.execute(
                update(NotificationLockScreenWidget).values(state="deleting", failures=0, next_attempt_at=None)
            )
            await db.commit()
        return {"widgetId": "WG123456"}

    api.create_widget.side_effect = create
    await service.tick()
    row = (await rows(factory))[0]
    assert row.state == "deleting" and row.widget_id == "WG123456"
    await service.tick()
    api.delete_widget.assert_awaited_once_with("WG123456", "secret")
    assert await rows(factory) == []


@pytest.mark.asyncio
async def test_uncertain_intent_reset_does_not_wait_for_backoff(setup):
    service, api, factory, clock, states = setup
    api.create_widget.side_effect = NotifyError("Timeout", delivery_state="unknown")
    await service.tick()
    await configure(factory, lock_screen_widgets=False)
    await service.tick()
    assert await rows(factory) == []


@pytest.mark.asyncio
async def test_token_rotation_preserves_widget_and_resumes_credential_failure(setup):
    service, api, factory, clock, states = setup
    await service.tick()
    async with factory() as db:
        row = await db.get(NotificationLockScreenWidget, 1)
        row.state = "suppressed"
        await db.commit()
    await configure(factory, token="rotated")
    await service.cleanup_provider(1, {"device_id": "ABC12345", "token": "secret"})
    row = (await rows(factory))[0]
    assert row.widget_id == "WG123456" and row.state == "active"
    await service.tick()
    api.update_widget.assert_awaited_once()
    assert api.update_widget.call_args.args[:2] == ("WG123456", "rotated")
    api.delete_widget.assert_not_awaited()
    assert api.create_widget.await_count == 1


@pytest.mark.asyncio
async def test_token_rotation_does_not_retry_unknown_creation(setup):
    service, api, factory, clock, states = setup
    api.create_widget.side_effect = NotifyError("unknown", delivery_state="unknown")
    await service.tick()
    await configure(factory, token="rotated")
    await service.cleanup_provider(1, {"device_id": "ABC12345", "token": "secret"})
    await service.tick()
    assert api.create_widget.await_count == 1
    assert (await rows(factory))[0].state == "uncertain"


@pytest.mark.asyncio
async def test_device_change_cleanup_failure_remains_visible_after_new_device_success(setup):
    service, api, factory, clock, states = setup
    await service.tick()
    await configure(factory, device_id="ABC12346", token="other")
    api.delete_widget.side_effect = NotifyError("timeout", delivery_state="unknown")
    await service.cleanup_provider(1, {"device_id": "ABC12345", "token": "secret"})
    assert await rows(factory) == []
    await service.tick()
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        assert "Remove the previous device" in provider.last_error
    assert api.create_widget.await_count == 2


@pytest.mark.asyncio
async def test_printer_scope_exclusion_deletes_only_owned_widget(setup):
    service, api, factory, clock, states = setup
    async with factory() as db:
        db.add(Printer(id=2, name="Printer 2", serial_number="serial2", ip_address="127.0.0.2", access_code="12345678"))
        await db.commit()
    api.create_widget.side_effect = [{"widgetId": "WG123456"}, {"widgetId": "WG654321"}]
    await service.tick()
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        provider.printer_id = 2
        await db.commit()
    await service.tick()
    api.delete_widget.assert_awaited_once_with("WG123456", "secret")
    assert [(row.printer_id, row.widget_id) for row in await rows(factory)] == [(2, "WG654321")]


@pytest.mark.asyncio
async def test_read_preflight_never_creates_or_adopts_unrelated_widget(setup):
    service, api, factory, clock, states = setup
    api.list_widgets.side_effect = NotifyError(
        "Expected a device widget list", delivery_state="unknown", payload={"widgetId": "WG654321"}
    )
    await service.tick()
    api.create_widget.assert_not_awaited()
    row = (await rows(factory))[0]
    assert row.widget_id is None and row.state == "pending"
    clock[0] += timedelta(minutes=2)
    api.list_widgets.side_effect = None
    await service.tick()
    api.create_widget.assert_awaited_once()


@pytest.mark.asyncio
async def test_credential_whitespace_does_not_create_duplicate_ownership(setup):
    service, api, factory, clock, states = setup
    await configure(factory, device_id=" ABC12345 ", token=" secret ")
    await service.tick()
    await configure(factory, device_id="ABC12345", token="rotated")
    await service.cleanup_provider(1, {"device_id": " ABC12345 ", "token": " secret "})
    await service.tick()
    assert api.create_widget.await_count == 1
    api.delete_widget.assert_not_awaited()
