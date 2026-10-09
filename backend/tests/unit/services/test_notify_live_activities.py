"""Lifecycle protocol tests with real persistence and a fake Notify gateway."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401
from backend.app.core.database import Base
from backend.app.models.notification import NotificationProvider
from backend.app.models.notification_live_activity import NotificationLiveActivity
from backend.app.models.printer import Printer
from backend.app.services import notify_live_activities as module
from backend.app.services.notify_client import NotifyError
from backend.app.services.notify_live_activities import NotifyLiveActivityService, PrintSnapshot


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'activities.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Printer(id=1, name="P1", serial_number="serial1", ip_address="127.0.0.1", access_code="12345678"))
        db.add(
            NotificationProvider(
                id=1,
                name="Phone",
                provider_type="notify",
                enabled=True,
                config=json.dumps({"device_id": "ABC12345", "token": "secret", "live_activities": True}),
                on_print_start=False,
                on_print_progress=False,
                on_print_complete=False,
                daily_digest_enabled=True,
            )
        )
        await db.commit()
    api = SimpleNamespace(
        start_activity=AsyncMock(return_value={"activityId": "LA123456"}),
        get_activity=AsyncMock(return_value={"state": "active"}),
        update_activity=AsyncMock(return_value={"success": True}),
        end_activity=AsyncMock(return_value={"success": True}),
    )
    clock = [datetime(2026, 1, 1, 12)]
    monkeypatch.setattr(module, "_now", lambda: clock[0])
    service = NotifyLiveActivityService(factory, api)
    monkeypatch.setattr(service, "_quiet", lambda provider: False)
    yield service, api, factory, clock
    await engine.dispose()


def state(**values):
    return (
        SimpleNamespace(
            connected=True,
            state="RUNNING",
            subtask_id="job1",
            subtask_name="Test print",
            current_print="test.3mf",
            progress=42,
            remaining_time=15,
            layer_num=42,
            total_layers=100,
            raw_data={},
            hms_errors=[],
            temperatures={"nozzle": 210, "bed": 60},
            stg_cur=-1,
            **values,
        )
        if not values
        else SimpleNamespace(
            **{
                **vars(state()),
                **values,
            }
        )
    )


async def rows(factory):
    async with factory() as db:
        return (await db.scalars(select(NotificationLiveActivity))).all()


async def start(setup):
    service, api, factory, clock = setup
    service.observe(1, state())
    await service.tick()
    return service, api, factory, clock


@pytest.mark.asyncio
async def test_start_independent_of_push_digest_and_group_thread(setup):
    service, api, factory, clock = setup
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        config = json.loads(provider.config)
        config["group_type"] = "Workshop"
        provider.config = json.dumps(config)
        await db.commit()
    await start(setup)
    assert api.start_activity.await_count == 1
    assert (await rows(factory))[0].activity_id == "LA123456"
    await service.tick()
    assert api.start_activity.await_count == 1


@pytest.mark.asyncio
async def test_startup_drops_rows_of_provider_deleted_before_cleanup_drained(setup):
    service, api, factory, clock = await start(setup)
    assert len(await rows(factory)) == 1
    # Production SQLite does not enforce the cascade.
    async with factory() as db:
        await db.execute(text("PRAGMA foreign_keys=OFF"))
        await db.execute(delete(NotificationProvider).where(NotificationProvider.id == 1))
        await db.commit()
        await db.execute(text("PRAGMA foreign_keys=ON"))
    restarted = NotifyLiveActivityService(factory, api)
    await restarted._purge_orphans()
    assert await rows(factory) == []


@pytest.mark.asyncio
async def test_restart_reuses_exact_id_without_new_start(setup, monkeypatch):
    service, api, factory, clock = await start(setup)
    clock[0] += timedelta(minutes=2)
    restarted = NotifyLiveActivityService(factory, api)
    monkeypatch.setattr(restarted, "_quiet", lambda provider: False)
    restarted.observe(1, state(progress=46))
    await restarted.tick()
    api.get_activity.assert_awaited_once_with("LA123456", "secret")
    assert api.start_activity.await_count == 1
    assert api.update_activity.call_args.args[0] == "LA123456"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,connected,expected", [("PAUSE", True, "Paused"), ("RUNNING", False, "Printer offline")])
async def test_pause_disconnect_clear_countdown_then_resume(setup, phase, connected, expected):
    service, api, factory, clock = await start(setup)
    service.observe(1, state(state=phase, connected=connected))
    await service.tick()
    content = api.update_activity.call_args.args[2]
    assert content["endsIn"] is None
    assert content["status"] == expected
    service.observe(1, state())
    await service.tick()
    assert api.update_activity.call_args.args[2]["endsIn"] == 900
    assert api.start_activity.await_count == 1


@pytest.mark.asyncio
async def test_frozen_eta_does_not_drift(setup):
    service, api, factory, clock = await start(setup)
    clock[0] += timedelta(minutes=2)
    await service.tick()
    assert api.update_activity.call_args.args[2]["endsIn"] == 780
    service.observe(1, state(remaining_time=10))
    clock[0] += timedelta(minutes=1)
    await service.tick()
    assert api.update_activity.call_args.args[2]["endsIn"] == 600


@pytest.mark.asyncio
async def test_quiet_hours_block_starts_but_not_updates_or_ends(setup, monkeypatch):
    service, api, factory, clock = setup
    monkeypatch.setattr(service, "_quiet", lambda provider: True)
    service.observe(1, state())
    await service.tick()
    api.start_activity.assert_not_awaited()
    monkeypatch.setattr(service, "_quiet", lambda provider: False)
    await service.tick()
    monkeypatch.setattr(service, "_quiet", lambda provider: True)
    clock[0] += timedelta(minutes=2)
    await service.tick()
    api.update_activity.assert_awaited_once()
    service.print_finished(1, {"subtask_id": "job1", "status": "completed"})
    await service.tick()
    assert api.end_activity.call_args.args[2]["progress"] == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["dismissed", "dead-token", "never-started", "script"])
async def test_terminal_suppression_survives_restart(setup, monkeypatch, reason):
    service, api, factory, clock = await start(setup)
    api.get_activity.return_value = {"state": "dismissed" if reason == "dismissed" else "ended", "endReason": reason}
    clock[0] += timedelta(minutes=2)
    await service.tick()
    assert (await rows(factory))[0].state == "suppressed"
    restarted = NotifyLiveActivityService(factory, api)
    monkeypatch.setattr(restarted, "_quiet", lambda provider: False)
    restarted.observe(1, state())
    await restarted.tick()
    assert api.start_activity.await_count == 1
    restarted.observe(1, state(subtask_id="job2"))
    await restarted.tick()
    assert api.start_activity.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["expired", "overdue", "abandoned"])
async def test_expiry_rolls_over_only_for_live_print(setup, reason):
    service, api, factory, clock = await start(setup)
    api.get_activity.return_value = {"state": "ended", "endReason": reason}
    clock[0] += timedelta(hours=8)
    api.start_activity.return_value = {"activityId": "LA654321"}
    await service.tick()
    assert api.start_activity.await_count == 2
    assert (await rows(factory))[0].activity_id == "LA654321"


@pytest.mark.asyncio
async def test_eight_hour_ceiling_polls_before_rollover(setup):
    service, api, factory, clock = await start(setup)
    clock[0] += timedelta(hours=8)
    await service.tick()
    api.get_activity.assert_awaited_once()
    api.end_activity.assert_awaited_once_with("LA123456", "secret")
    assert api.start_activity.await_count == 2


@pytest.mark.asyncio
async def test_ambiguous_start_with_id_is_reconciled_without_duplication(setup):
    service, api, factory, clock = setup
    api.start_activity.side_effect = NotifyError(
        "unknown", status_code=502, payload={"activityId": "LA123456", "deliveryState": "unknown"}
    )
    await start(setup)
    assert (await rows(factory))[0].activity_id == "LA123456"
    clock[0] += timedelta(minutes=2)
    await service.tick()
    api.get_activity.assert_awaited_once_with("LA123456", "secret")
    assert api.start_activity.await_count == 1


@pytest.mark.asyncio
async def test_ambiguous_start_without_id_never_repeats(setup):
    service, api, factory, clock = setup
    api.start_activity.side_effect = NotifyError("timeout", delivery_state="unknown")
    await start(setup)
    clock[0] += timedelta(hours=2)
    await service.tick()
    assert api.start_activity.await_count == 1
    assert (await rows(factory))[0].state == "suppressed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        NotifyError("full", status_code=400, payload={"message": "Maximum 5 activities"}),
        NotifyError("throttled", status_code=429, retry_after_seconds=1800),
        NotifyError("not delivered", status_code=502, retry_after_seconds=1800, delivery_state="not-delivered"),
        NotifyError("unavailable", status_code=503),
    ],
)
async def test_retryable_starts_honor_backoff(setup, error):
    service, api, factory, clock = setup
    api.start_activity.side_effect = error
    await start(setup)
    await service.tick()
    assert api.start_activity.await_count == 1
    clock[0] += timedelta(minutes=31)
    api.start_activity.side_effect = None
    await service.tick()
    assert api.start_activity.await_count == 2


@pytest.mark.asyncio
async def test_end_intent_survives_failure_restart_and_next_job(setup, monkeypatch):
    service, api, factory, clock = await start(setup)
    api.end_activity.side_effect = NotifyError("timeout", delivery_state="unknown")
    service.print_finished(1, {"subtask_id": "job1", "status": "completed"})
    await service.tick()
    assert (await rows(factory))[0].state == "ending"
    await service.tick()
    assert api.end_activity.await_count == 1
    restarted = NotifyLiveActivityService(factory, api)
    monkeypatch.setattr(restarted, "_quiet", lambda provider: False)
    restarted.observe(1, state(subtask_id="job2"))
    clock[0] += timedelta(minutes=2)
    api.end_activity.side_effect = None
    await restarted.tick()
    assert api.end_activity.call_args.args[2]["status"] == "Complete"
    assert api.start_activity.await_count == 2
    assert (await rows(factory))[0].state == "ended"


@pytest.mark.asyncio
async def test_late_complete_does_not_end_new_job(setup):
    service, api, factory, clock = await start(setup)
    service.observe(1, state(subtask_id="job2"))
    await service.tick()
    api.end_activity.reset_mock()
    service.print_finished(1, {"subtask_id": "job1", "status": "completed"})
    await service.tick()
    api.end_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_survives_delta_and_late_job_id(setup):
    service, api, factory, clock = setup
    service.print_started(1, state(subtask_id=None, raw_data={"gcode_start_time": 12345}))
    await service.tick()
    clock[0] += timedelta(minutes=2)
    service.observe(1, state(subtask_id=None, raw_data={}))
    await service.tick()
    service.observe(1, state(subtask_id="finally-known"))
    clock[0] += timedelta(minutes=2)
    await service.tick()
    assert api.start_activity.await_count == 1
    assert api.end_activity.await_count == 0


@pytest.mark.asyncio
async def test_scope_and_disabled_provider_end_tiles(setup):
    service, api, factory, clock = await start(setup)
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        provider.enabled = False
        await db.commit()
    await service.tick()
    assert api.end_activity.await_count == 1
    assert (await rows(factory))[0].state == "ended"


@pytest.mark.asyncio
async def test_delete_printer_retains_id_until_remote_end(setup):
    service, api, factory, clock = await start(setup)
    async with factory() as db:
        await db.delete(await db.get(Printer, 1))
        await db.commit()
    await service.tick()
    assert api.end_activity.await_count == 1
    assert (await rows(factory))[0].state == "ended"


@pytest.mark.asyncio
async def test_cleanup_uses_old_token_and_removes_ownership(setup):
    service, api, factory, clock = await start(setup)
    await service.cleanup_provider(1, {"device_id": "ABC12345", "token": "secret"})
    api.end_activity.assert_awaited_once_with("LA123456", "secret")
    assert await rows(factory) == []


@pytest.mark.asyncio
async def test_http_never_holds_database_session_and_concurrent_ticks_deduplicate(setup):
    import asyncio

    service, api, factory, clock = setup
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
        return {"activityId": "LA123456"}

    api.start_activity.side_effect = send
    service.observe(1, state())
    await asyncio.gather(service.tick(), service.tick(), service.tick())
    assert api.start_activity.await_count == 1


def test_advanced_content_and_utf8_budget():
    snapshot = PrintSnapshot.from_state(state(subtask_name="🔒" * 255, remaining_time=1500))
    config = {
        "live_activity_style": "segments",
        "live_activity_metrics": ["progress", "eta", "layers", "nozzle", "bed", "chamber"],
        "live_activity_button_url": "https://bambuddy.example/" + "a" * 480,
        "live_activity_privacy": True,
    }
    content = snapshot.content("🖨" * 100, config)
    assert "🔒" not in content["body"]
    assert content["steps"] == 10 and content["step"] == 4
    assert content["endsIn"] is None and content["trailing"] == "25h 0m"
    assert len(content["metrics"]) == 5
    assert len(json.dumps(content, ensure_ascii=False).encode()) < 2048
    assert content["button"] is None or content["button"]["open"] is True
    assert snapshot.content("P1", {"live_activity_style": "none"})["progress"] is None


@pytest.mark.asyncio
async def test_fallback_dismissal_follows_late_id_across_restart(setup, monkeypatch):
    service, api, factory, clock = setup
    service.print_started(1, state(subtask_id=None))
    await service.tick()
    clock[0] += timedelta(minutes=2)
    api.get_activity.return_value = {"state": "dismissed", "endReason": "dismissed"}
    await service.tick()
    service.observe(1, state(subtask_id="late-id"))
    await service.tick()
    assert (await rows(factory))[0].job_key == "job:late-id"
    restarted = NotifyLiveActivityService(factory, api)
    monkeypatch.setattr(restarted, "_quiet", lambda provider: False)
    restarted.observe(1, state(subtask_id="late-id"))
    await restarted.tick()
    assert api.start_activity.await_count == 1


@pytest.mark.asyncio
async def test_end_retry_without_any_printer_snapshot(setup):
    service, api, factory, clock = await start(setup)
    api.end_activity.side_effect = NotifyError("unavailable", status_code=503)
    service.print_finished(1, {"subtask_id": "job1", "status": "failed"})
    await service.tick()
    restarted = NotifyLiveActivityService(factory, api)
    clock[0] += timedelta(minutes=2)
    api.end_activity.side_effect = None
    await restarted.tick()
    assert api.end_activity.call_args.args[2]["status"] == "Failed"
    assert (await rows(factory))[0].state == "ended"


@pytest.mark.asyncio
async def test_printer_scope_new_provider_separate_ownership(setup):
    service, api, factory, clock = setup
    async with factory() as db:
        db.add(Printer(id=2, name="P2", serial_number="serial2", ip_address="127.0.0.2", access_code="12345678"))
        db.add(
            NotificationProvider(
                id=2,
                name="Other Phone",
                provider_type="notify",
                printer_id=2,
                config=json.dumps({"device_id": "ABC12346", "token": "other", "live_activities": True}),
            )
        )
        await db.commit()
    await start(setup)
    assert api.start_activity.await_count == 1
    async with factory() as db:
        provider = await db.get(NotificationProvider, 2)
        provider.printer_id = 1
        await db.commit()
    await service.tick()
    assert api.start_activity.await_count == 2
    assert {r.provider_id for r in await rows(factory)} == {1, 2}


@pytest.mark.asyncio
async def test_unknown_start_record_after_process_crash_is_never_restarted(setup):
    service, api, factory, clock = await start(setup)
    async with factory() as db:
        row = await db.get(NotificationLiveActivity, 1)
        row.activity_id = None
        row.state = "uncertain"
        await db.commit()
    clock[0] += timedelta(minutes=2)
    await service.tick()
    assert api.start_activity.await_count == 1
    assert (await rows(factory))[0].state == "suppressed"


@pytest.mark.asyncio
async def test_410_is_polled_for_reason_before_any_rollover(setup):
    service, api, factory, clock = await start(setup)
    api.update_activity.side_effect = NotifyError("gone", status_code=410)
    clock[0] += timedelta(minutes=2)
    await service.tick()
    api.get_activity.return_value = {"state": "dismissed", "endReason": "dismissed"}
    clock[0] += timedelta(minutes=2)
    await service.tick()
    assert api.start_activity.await_count == 1
    assert (await rows(factory))[0].state == "suppressed"


def fault(description="Nozzle temperature malfunction", severity=2, full_code="05004003", actions=None):
    return SimpleNamespace(description=description, severity=severity, full_code=full_code, actions=actions)


@pytest.mark.asyncio
async def test_running_printer_fault_replaces_counter_and_restores_after_clear(setup):
    service, api, factory, clock = await start(setup)
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        config = json.loads(provider.config)
        config.update(live_activity_metrics=["progress", "eta"], live_activity_stage=True)
        provider.config = json.dumps(config)
        await db.commit()
    service.observe(1, state(hms_errors=[fault()]))
    await service.tick()
    content = api.update_activity.call_args.args[2]
    assert content["status"] == "Printer error"
    assert content["body"] == "Nozzle temperature malfunction"
    assert content["endsIn"] is None
    assert content["metrics"] is None
    service.observe(1, state(hms_errors=[]))
    await service.tick()
    content = api.update_activity.call_args.args[2]
    assert content["status"] == "Printing"
    assert content["endsIn"] == 900
    assert len(content["metrics"]) == 2
    assert api.start_activity.await_count == 1


def test_runout_and_actionable_level_three_prompt():
    snapshot = PrintSnapshot.from_state(state(hms_errors=[fault("Filament has run out", 3)]))
    content = snapshot.content("P1", {"live_activity_privacy": True})
    assert content["status"] == "Filament runout"
    assert content["endsIn"] is None
    assert "Test print" not in content["body"]
    snapshot = PrintSnapshot.from_state(
        state(hms_errors=[fault("Heatbreak fan problem", 3, "0000000012345678", ["retry"])])
    )
    assert snapshot.content("P1")["status"] == "Printer error"


@pytest.mark.parametrize(
    "error",
    [
        fault("The cover is open", 3, "0000000012345678"),
        fault("Invalid level", 0),
        fault("", 2),
    ],
)
def test_advisory_does_not_interrupt_live_countdown(error):
    content = PrintSnapshot.from_state(state(hms_errors=[error])).content("P1")
    assert content["status"] == "Printing"
    assert content["endsIn"] == 900


@pytest.mark.asyncio
@pytest.mark.parametrize("device_id", ["GRP12345", "WB123456", "MC123456"])
async def test_non_ios_devices_never_start_activity(setup, device_id):
    service, api, factory, clock = setup
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        config = json.loads(provider.config)
        config["device_id"] = device_id
        provider.config = json.dumps(config)
        await db.commit()
    service.observe(1, state())
    await service.tick()
    api.start_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_start_resets_previous_progress_and_eta_until_fresh_telemetry(setup):
    service, api, factory, clock = setup
    service.print_started(1, state(progress=99, remaining_time=1))
    await service.tick()
    assert api.start_activity.call_args.args[2]["progress"] == 0
    assert api.start_activity.call_args.args[2]["endsIn"] is None
    service.observe(1, state(progress=99, remaining_time=1))
    clock[0] += timedelta(seconds=61)
    await service.tick()
    assert api.update_activity.call_args.args[2]["progress"] == 0
    assert api.update_activity.call_args.args[2]["endsIn"] is None
    service.observe(1, state(progress=2, remaining_time=50))
    clock[0] += timedelta(seconds=61)
    await service.tick()
    assert api.update_activity.call_args.args[2]["progress"] == 2
    assert api.update_activity.call_args.args[2]["endsIn"] == 3000


@pytest.mark.asyncio
async def test_old_tile_must_end_before_starting_next_job(setup):
    service, api, factory, clock = await start(setup)
    api.end_activity.side_effect = NotifyError("unavailable", status_code=503)
    service.observe(1, state(subtask_id="job2"))
    await service.tick()
    assert api.start_activity.await_count == 1
    assert (await rows(factory))[0].state == "ending"
    api.end_activity.side_effect = None
    clock[0] += timedelta(minutes=2)
    await service.tick()
    assert api.start_activity.await_count == 2


@pytest.mark.asyncio
async def test_complete_matches_learned_job_id_for_fallback_row(setup):
    service, api, factory, clock = setup
    service.print_started(1, state(subtask_id=None))
    await service.tick()
    service.observe(1, state(subtask_id="learned"))
    await service.tick()
    service.print_finished(1, {"raw_data": {"subtask_id": "learned"}, "status": "completed"})
    await service.tick()
    assert api.end_activity.call_args.args[2]["status"] == "Complete"
    assert (await rows(factory))[0].state == "ended"


def test_preparation_does_not_display_previous_print_progress():
    snapshot = PrintSnapshot.from_state(state(layer_num=0, total_layers=100, progress=85))
    content = snapshot.content("P1", {"live_activity_metrics": ["progress"]})
    assert content["status"] == "Preparing"
    assert content["progress"] == 0
    assert content["metrics"][0]["value"] == "0%"


@pytest.mark.asyncio
async def test_reenable_resumes_current_print_after_successful_cleanup(setup):
    service, api, factory, clock = await start(setup)
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        provider.enabled = False
        await db.commit()
    await service.tick()
    assert (await rows(factory))[0].end_reason == "disabled"
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        provider.enabled = True
        await db.commit()
    await service.tick()
    assert api.start_activity.await_count == 1  # Wait for fresh telemetry after dormant time.
    service.observe(1, state())
    await service.tick()
    assert api.start_activity.await_count == 2


@pytest.mark.asyncio
async def test_reenable_waits_for_failed_cleanup_before_restarting(setup):
    service, api, factory, clock = await start(setup)
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        provider.enabled = False
        await db.commit()
    api.end_activity.side_effect = NotifyError("unavailable", status_code=503)
    await service.tick()
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        provider.enabled = True
        await db.commit()
    await service.tick()
    assert api.start_activity.await_count == 1
    api.end_activity.side_effect = None
    clock[0] += timedelta(minutes=2)
    await service.tick()
    service.observe(1, state())
    await service.tick()
    assert api.start_activity.await_count == 2


@pytest.mark.parametrize("config", [{}, {"live_activity_metrics": []}])
def test_default_tile_uses_native_timer_without_metric_chips(config):
    content = PrintSnapshot.from_state(state()).content("P1", config)
    assert content["endsIn"] == 900
    assert content["metrics"] is None
    assert content["trailing"] is None


@pytest.mark.asyncio
async def test_disabling_optional_metrics_clears_chips_and_preserves_timer(setup):
    service, api, factory, clock = setup
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        config = json.loads(provider.config)
        config["live_activity_metrics"] = ["progress", "eta"]
        provider.config = json.dumps(config)
        await db.commit()
    await start(setup)
    content = api.start_activity.call_args.args[2]
    assert content["endsIn"] == 900
    assert content["metrics"][0] == {"label": "Progress", "value": "42%", "unit": ""}
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        config["live_activity_metrics"] = []
        provider.config = json.dumps(config)
        await db.commit()
    clock[0] += timedelta(minutes=1)
    await service.tick()
    content = api.update_activity.call_args.args[2]
    assert content["metrics"] is None
    assert content["endsIn"] == 840
    assert api.start_activity.await_count == 1


@pytest.mark.asyncio
async def test_dormant_worker_has_no_periodic_sql_or_mqtt_wakeups(setup):
    import asyncio

    from sqlalchemy import event

    service, api, factory, clock = setup
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        provider.enabled = False
        await db.commit()
    queries = []
    engine = factory.kw["bind"]

    def record(connection, cursor, statement, parameters, context, many):
        queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    service.worker_interval = 0.01
    service.start()
    try:
        for _ in range(100):
            if not service._initial_discovery:
                break
            await asyncio.sleep(0.001)
        queries.clear()
        service.observe(1, state())
        service.print_started(1, state())
        service.print_finished(1, {"subtask_id": "job1", "status": "completed"})
        await asyncio.sleep(0.05)
        assert queries == []
        assert service._snapshots == {}
        api.start_activity.assert_not_awaited()
    finally:
        await service.close()
        event.remove(engine.sync_engine, "before_cursor_execute", record)


@pytest.mark.asyncio
async def test_startup_waits_for_real_status_before_touching_existing_activity(setup):
    service, api, factory, clock = await start(setup)
    clock[0] += timedelta(minutes=2)
    restarted = NotifyLiveActivityService(factory, api)
    await restarted.tick()
    api.get_activity.assert_not_awaited()
    restarted.observe(1, state(state="unknown", progress=0))
    await restarted.tick()
    api.get_activity.assert_not_awaited()
    restarted.observe(1, state())
    await restarted.tick()
    api.get_activity.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [None, False, True])
async def test_startup_grace_expires_without_real_status_and_reconnect_resumes(setup, connected):
    service, api, factory, clock = await start(setup)
    clock[0] += timedelta(minutes=2)
    restarted = NotifyLiveActivityService(factory, api)
    # Importing the service before worker startup must not consume the grace.
    clock[0] += timedelta(minutes=5)

    # Neither complete silence nor broker-only status may extend the grace.
    for seconds in (0, 90, 29):
        clock[0] += timedelta(seconds=seconds)
        if connected is not None:
            restarted.observe(1, state(state="unknown", connected=connected, progress=0))
        await restarted.tick()
        api.get_activity.assert_not_awaited()
        api.update_activity.assert_not_awaited()

    clock[0] += timedelta(seconds=1)
    await restarted.tick()
    api.get_activity.assert_awaited_once_with("LA123456", "secret")
    content = api.update_activity.call_args.args[2]
    assert content["status"] == "Printer offline"
    assert content["endsIn"] is None
    assert content["progress"] == 42
    saved = (await rows(factory))[0]
    assert saved.eta_seconds is None
    assert saved.eta_deadline is None
    assert saved.activity_id == "LA123456"
    api.end_activity.assert_not_awaited()
    assert api.start_activity.await_count == 1

    # A real status resumes the same tile immediately, even during throttling.
    restarted.observe(1, state(progress=46, remaining_time=10))
    await restarted.tick()
    assert api.update_activity.await_count == 2
    assert api.update_activity.call_args.args[0] == "LA123456"
    content = api.update_activity.call_args.args[2]
    assert content["status"] == "Printing"
    assert content["endsIn"] == 600
    assert content["progress"] == 46
    assert api.start_activity.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["expired", "dismissed"])
async def test_startup_grace_never_restarts_an_offline_printers_activity(setup, reason):
    service, api, factory, clock = await start(setup)
    restarted = NotifyLiveActivityService(factory, api)
    await restarted.tick()
    api.get_activity.return_value = {"state": "ended", "endReason": reason}
    clock[0] += timedelta(minutes=2)
    await restarted.tick()
    api.get_activity.assert_awaited_once_with("LA123456", "secret")
    api.update_activity.assert_not_awaited()
    api.end_activity.assert_not_awaited()
    assert api.start_activity.await_count == 1
    assert (await rows(factory))[0].state == ("pending" if reason == "expired" else "suppressed")


@pytest.mark.asyncio
async def test_startup_offline_clears_saved_eta_labels(setup):
    service, api, factory, clock = setup
    async with factory() as db:
        provider = await db.get(NotificationProvider, 1)
        config = json.loads(provider.config)
        config["live_activity_metrics"] = ["progress", "eta", "nozzle"]
        provider.config = json.dumps(config)
        await db.commit()
    service.observe(1, state(remaining_time=25 * 60))
    await service.tick()
    original = api.start_activity.call_args.args[2]
    assert original["trailing"] == "25h 0m"
    assert any(metric["label"] == "Remaining" for metric in original["metrics"])

    restarted = NotifyLiveActivityService(factory, api)
    await restarted.tick()
    clock[0] += timedelta(minutes=2)
    await restarted.tick()
    content = api.update_activity.call_args.args[2]
    assert content["status"] == "Printer offline"
    assert content["endsIn"] is None
    assert content["trailing"] is None
    assert content["metrics"] is None
    assert content["progress"] == 42


@pytest.mark.asyncio
async def test_scheduled_cleanup_does_not_wait_for_create_and_keeps_late_handle(setup):
    import asyncio

    service, api, factory, clock = setup
    entered, release = asyncio.Event(), asyncio.Event()

    async def create(*args):
        entered.set()
        await release.wait()
        return {"activityId": "LA123456"}

    api.start_activity.side_effect = create
    service.observe(1, state())
    tick = asyncio.create_task(service.tick())
    await entered.wait()
    await asyncio.wait_for(service.schedule_cleanup(1, {"device_id": "ABC12345", "token": "secret"}), 0.2)
    async with factory() as db:
        await db.delete(await db.get(NotificationProvider, 1))
        await db.commit()
    release.set()
    await tick
    try:
        for _ in range(100):
            if api.end_activity.await_count:
                break
            await asyncio.sleep(0.005)
        api.end_activity.assert_awaited_once_with("LA123456", "secret")
        assert await rows(factory) == []
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_boot_cleans_disabled_activity_without_printer_queries_or_pruning(setup):
    import asyncio

    from sqlalchemy import event

    service, api, factory, clock = await start(setup)
    async with factory() as db:
        (await db.get(NotificationProvider, 1)).enabled = False
        await db.commit()
    statements = []
    engine = factory.kw["bind"]

    def record(connection, cursor, statement, parameters, context, many):
        statements.append(statement.lower())

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    restarted = NotifyLiveActivityService(factory, api)
    restarted.start()
    try:
        for _ in range(100):
            if (await rows(factory))[0].state == "ended":
                break
            await asyncio.sleep(0.005)
        assert (await rows(factory))[0].state == "ended"
        assert not any("from printers" in statement for statement in statements)
        assert not any(statement.startswith("delete from notification_live_activities") for statement in statements)
    finally:
        await restarted.close()
        event.remove(engine.sync_engine, "before_cursor_execute", record)
