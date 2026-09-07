"""Real-database regression coverage for queue heat-soak reservations and cleanup."""

import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.app.core.database import Base, _ensure_active_queue_printer_reservation
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.schemas.print_queue import PrintQueueItemCreate, PrintQueueItemUpdate
from backend.app.services import chamber_heat_soak as heat
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.heat_soak_telemetry import record_heat_soak_reports


@pytest.fixture
async def soak(tmp_path, monkeypatch):
    import backend.app.models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'soak.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_active_queue_printer_reservation(conn)
    state = PrinterState(connected=True, state="IDLE")
    client = MagicMock()
    client.set_bed_temperature.return_value = True
    client.set_chamber_temperature.return_value = True
    client.set_airduct_mode.return_value = True
    manager = MagicMock()
    manager._broadcast_status_change = AsyncMock()
    manager.is_connected.return_value = True
    manager.get_client.return_value = client
    manager.get_status.return_value = state
    monkeypatch.setattr(heat, "printer_manager", manager)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        printer = Printer(
            id=1, name="Test", serial_number="TEST", ip_address="127.0.0.1", access_code="12345678", model="H2D"
        )
        item = PrintQueueItem(id=1, printer_id=1, chamber_heat_soak=True, heat_soak_minutes=1, status="pending")
        db.add_all([printer, item])
        await db.commit()
        yield SimpleNamespace(
            engine=engine,
            db=db,
            printer=printer,
            item=item,
            state=state,
            client=client,
            manager=manager,
            service=heat.ChamberHeatSoak(),
        )
    await engine.dispose()


def confirm(soak, target=60):
    record_heat_soak_reports(
        soak.state,
        {
            "bed_target_temper": target,
            "device": {"ctc": {"info": {"target": target}}, "airduct": {"modeCur": int(target > 0)}},
        },
    )


@pytest.mark.parametrize(
    "model,chamber,airduct", [("H2D", True, True), ("X1C", False, False), ("P2S", False, True), ("A1", False, False)]
)
async def test_supported_controls_and_durable_reservation(soak, model, chamber, airduct):
    soak.printer.model = model
    await soak.db.commit()
    assert await soak.service.stage(soak.db, soak.item)
    await soak.db.refresh(soak.item)
    assert soak.item.status == "preheating"
    assert soak.item.preheat_started_at is not None
    soak.client.set_bed_temperature.assert_called_once_with(60)
    assert soak.client.set_chamber_temperature.called == chamber
    assert soak.client.set_airduct_mode.called == airduct
    soak.db.add(PrintQueueItem(printer_id=1, status="dispatching"))
    with pytest.raises(IntegrityError):
        await soak.db.commit()
    await soak.db.rollback()


async def test_full_timer_starts_when_heating_commands_are_sent(soak):
    assert await soak.service.stage(soak.db, soak.item)
    await soak.db.refresh(soak.item)
    assert soak.item.preheat_started_at is not None
    assert soak.item.preheat_started_at <= heat.utcnow()
    soak.item.preheat_started_at = heat.utcnow() - timedelta(seconds=59)
    await soak.db.commit()
    assert await soak.service.check(soak.db) == []
    soak.item.preheat_started_at = heat.utcnow() - timedelta(seconds=61)
    await soak.db.commit()
    assert await soak.service.check(soak.db) == [1]
    await soak.db.refresh(soak.item)
    assert soak.item.status == "dispatching"
    assert soak.item.dispatch_subtask_id is None  # normal dispatch owns this next
    assert await soak.service.check(soak.db) == []


async def test_bed_only_heating_starts_timer(soak):
    soak.printer.model = "P1S"
    await soak.db.commit()
    assert await soak.service.stage(soak.db, soak.item)
    await soak.db.refresh(soak.item)
    assert soak.item.preheat_started_at is not None


@pytest.mark.parametrize(
    "interruption",
    [
        "disconnect",
        "reconnect",
        "external_print",
        "timeout",
        "restart",
        "disabled",
    ],
)
async def test_interruptions_release_item_for_manual_retry_and_shutdown(soak, interruption):
    await soak.service.stage(soak.db, soak.item)
    await soak.db.refresh(soak.item)
    if interruption == "disconnect":
        soak.manager.is_connected.return_value = False
    elif interruption == "reconnect":
        soak.state.heat_soak_disconnected_at = time.time()
    elif interruption == "external_print":
        soak.state.state = "RUNNING"
    elif interruption == "disabled":
        soak.printer.is_active = False
    elif interruption == "timeout":
        soak.item.preheat_checked_at = heat.utcnow() - timedelta(seconds=91)
    elif interruption == "restart":
        soak.service = heat.ChamberHeatSoak()
        soak.item.preheat_checked_at = heat.utcnow() - timedelta(seconds=91)
    await soak.db.commit()
    assert await soak.service.check(soak.db) == []
    await soak.db.refresh(soak.item)
    await soak.db.refresh(soak.printer)
    assert soak.item.status == "pending"
    assert soak.item.manual_start is True
    assert soak.item.preheat_owner is None
    assert soak.item.preheat_started_at is None
    assert soak.item.error_message
    assert soak.printer.heat_soak_shutdown_pending
    assert not soak.client.start_print.called


async def test_live_foreign_worker_never_claims_or_advances_reserved_item(soak):
    async with AsyncSession(soak.engine, expire_on_commit=False) as other_db:
        stale_item = await other_db.get(PrintQueueItem, 1)
        await soak.service.stage(soak.db, soak.item)
        other = heat.ChamberHeatSoak()
        stale_item.printer_id = 2
        assert not await other.stage(other_db, stale_item)
        assert await other.check(other_db) == []
        await soak.db.refresh(soak.item)
        assert soak.item.status == "preheating"
        assert soak.item.printer_id == 1
        assert soak.item.preheat_owner == soak.service.owner
        soak.client.set_bed_temperature.assert_called_once_with(60)


async def test_restart_aborts_an_owned_preheat_and_requires_manual_retry(soak):
    assert await soak.service.stage(soak.db, soak.item)
    soak.item.preheat_checked_at = heat.utcnow() - timedelta(seconds=91)
    await soak.db.commit()
    restarted = heat.ChamberHeatSoak()
    assert await restarted.check(soak.db) == []
    await soak.db.refresh(soak.item)
    assert soak.item.status == "pending"
    assert soak.item.manual_start is True
    assert "restart" in (soak.item.error_message or "")
    soak.client.set_bed_temperature.assert_called_with(0)


async def test_failed_command_stops_every_supported_heater(soak):
    soak.client.set_chamber_temperature.return_value = False
    assert not await soak.service.stage(soak.db, soak.item)
    await soak.db.refresh(soak.item)
    assert soak.item.status == "pending"
    soak.client.set_bed_temperature.assert_called_with(0)
    soak.client.set_chamber_temperature.assert_called_with(0)
    soak.client.set_airduct_mode.assert_called_with("cooling")


async def test_delete_offline_preserves_cleanup_and_prevents_new_soak_until_off_confirmed(soak):
    await soak.service.stage(soak.db, soak.item)
    soak.manager.is_connected.return_value = False
    item = await heat.lock_queue_item(soak.db, 1)
    await heat.abort_heat_soak(soak.db, item, "Cancelled", status="cancelled")
    await soak.db.delete(item)
    await soak.db.commit()
    await soak.service.cleanup(soak.db)
    await soak.db.refresh(soak.printer)
    assert soak.printer.heat_soak_shutdown_pending
    soak.manager.is_connected.return_value = True
    new_item = PrintQueueItem(id=2, printer_id=1, chamber_heat_soak=True)
    soak.db.add(new_item)
    await soak.db.commit()
    assert not await soak.service.stage(soak.db, new_item)
    await soak.service.cleanup(soak.db)
    assert soak.printer.heat_soak_shutdown_pending
    confirm(soak, target=0)
    await soak.service.cleanup(soak.db)
    await soak.db.refresh(soak.printer)
    assert not soak.printer.heat_soak_shutdown_pending
    assert await soak.service.stage(soak.db, new_item)


async def test_cancel_at_timer_boundary_cannot_dispatch(soak):
    await soak.service.stage(soak.db, soak.item)
    await soak.service.check(soak.db)
    soak.item.preheat_started_at = heat.utcnow() - timedelta(minutes=2)
    await soak.db.commit()
    item = await heat.lock_queue_item(soak.db, 1)
    await heat.abort_heat_soak(soak.db, item, "Cancelled", status="cancelled")
    assert await soak.service.check(soak.db) == []
    await soak.db.refresh(soak.item)
    assert soak.item.status == "cancelled"
    soak.client.set_bed_temperature.assert_called_with(0)


@pytest.mark.parametrize("schema", [PrintQueueItemCreate, PrintQueueItemUpdate])
@pytest.mark.parametrize(
    "field,value",
    [
        ("heat_soak_temperature", 29),
        ("heat_soak_temperature", 61),
        ("heat_soak_minutes", 0),
        ("heat_soak_minutes", 121),
        ("heat_soak_minutes", 1.5),
        ("heat_soak_temperature", None),
        ("chamber_heat_soak", None),
    ],
)
def test_schema_rejects_invalid_heat_soak_options(schema, field, value):
    with pytest.raises(ValidationError):
        schema(**{field: value})


def test_schema_defaults_are_off_and_patch_omission_preserves_existing_values():
    create = PrintQueueItemCreate()
    assert not create.chamber_heat_soak
    assert (create.heat_soak_temperature, create.heat_soak_minutes) == (60, 30)
    assert PrintQueueItemUpdate().model_dump(exclude_unset=True) == {}


def test_telemetry_decodes_nested_firmware_targets_and_ignores_local_ui_values():
    state = PrinterState(temperatures={"chamber_target": 60})
    record_heat_soak_reports(
        state,
        {
            "device": {
                "bed": {"info": {"temp": 60 * 65536 + 35}},
                "ctc": {"info": {"temp": 50 * 65536 + 30, "target": 55}},
                "airduct": {"modeCur": 1},
            }
        },
    )
    assert state.heat_soak_reports["bed_target"][0] == 60
    assert state.heat_soak_reports["chamber_target"][0] == 55
    assert state.heat_soak_reports["airduct"][0] == 1


async def test_index_upgrade_includes_preheating_when_old_index_exists(soak):
    async with soak.engine.begin() as conn:
        await conn.execute(text("DROP INDEX uq_print_queue_active_printer_heat_soak"))
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_print_queue_active_printer ON print_queue(printer_id) "
                "WHERE status IN ('dispatching', 'printing')"
            )
        )
        await _ensure_active_queue_printer_reservation(conn)
        await _ensure_active_queue_printer_reservation(conn)
    await soak.service.stage(soak.db, soak.item)
    soak.db.add(PrintQueueItem(printer_id=1, status="preheating"))
    with pytest.raises(IntegrityError):
        await soak.db.commit()
    await soak.db.rollback()


async def test_no_upload_or_print_until_soak_then_normal_correlated_dispatch(soak, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from backend.app.models.archive import PrintArchive
    from backend.app.services import print_scheduler as scheduling
    from backend.app.services.archive import ArchiveService

    source = tmp_path / "test.3mf"
    source.write_bytes(b"test print")
    archive = PrintArchive(
        filename="test.3mf", file_path=str(source), file_size=10, content_hash="soak-test", status="completed"
    )
    soak.db.add(archive)
    await soak.db.flush()
    soak.item.archive_id = archive.id
    await soak.db.commit()
    scheduler = scheduling.PrintScheduler()
    scheduler._heat_soak = soak.service
    scheduler._prepare_drying_for_dispatch = AsyncMock(return_value=True)
    scheduler._active_drying_ams_ids = MagicMock(return_value=[])
    scheduler._propagate_owner_to_printer_manager = AsyncMock()
    scheduler._schedule_dispatch_confirmation = MagicMock()
    upload = AsyncMock(return_value=True)
    archiving = AsyncMock()
    monkeypatch.setattr(scheduling, "printer_manager", soak.manager)
    monkeypatch.setattr(scheduling, "upload_file_async", upload)
    monkeypatch.setattr(scheduling, "delete_file_async", AsyncMock())
    monkeypatch.setattr(scheduling, "get_ftp_retry_settings", AsyncMock(return_value=(False, 0, 0, 1)))
    monkeypatch.setattr(scheduling, "cache_3mf_download", MagicMock())
    monkeypatch.setattr(ArchiveService, "archive_print", archiving)
    monkeypatch.setattr(scheduling, "async_session", lambda: AsyncSession(soak.engine, expire_on_commit=False))
    soak.manager.start_print.return_value = True

    await scheduler._start_print(soak.db, soak.item)
    upload.assert_not_awaited()
    archiving.assert_not_awaited()
    soak.manager.start_print.assert_not_called()
    await soak.service.check(soak.db)
    soak.item.preheat_started_at = heat.utcnow() - timedelta(seconds=61)
    await soak.db.commit()
    assert await soak.service.check(soak.db) == [1]
    await scheduler._dispatch_after_heat_soak(1)
    upload.assert_awaited_once()
    soak.manager.start_print.assert_called_once()
    await soak.db.refresh(soak.item)
    assert soak.item.status == "dispatching"
    assert soak.item.dispatch_subtask_id
    assert soak.manager.start_print.call_args.kwargs["submission_id"] == soak.item.dispatch_subtask_id
    scheduler._schedule_dispatch_confirmation.assert_called_once()


async def test_cancel_after_soak_before_dispatch_task_does_not_upload(soak, monkeypatch):
    from unittest.mock import AsyncMock

    from backend.app.services import print_scheduler as scheduling

    await soak.service.stage(soak.db, soak.item)
    await soak.service.check(soak.db)
    soak.item.preheat_started_at = heat.utcnow() - timedelta(seconds=61)
    await soak.db.commit()
    assert await soak.service.check(soak.db) == [1]
    item = await heat.lock_queue_item(soak.db, 1)
    await heat.abort_heat_soak(soak.db, item, "Stopped", status="cancelled")
    scheduler = scheduling.PrintScheduler()
    scheduler._heat_soak = soak.service
    scheduler._start_print = AsyncMock()
    monkeypatch.setattr(scheduling, "async_session", lambda: AsyncSession(soak.engine, expire_on_commit=False))
    await scheduler._dispatch_after_heat_soak(1)
    scheduler._start_print.assert_not_awaited()


async def test_upgrade_defaults_existing_rows_to_off(tmp_path):
    from backend.app.core.database import ensure_queue_insert_schema

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy-soak.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE print_queue (id INTEGER PRIMARY KEY, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
            )
            await conn.execute(text("INSERT INTO print_queue (id) VALUES (1)"))
            await ensure_queue_insert_schema(conn)
            await ensure_queue_insert_schema(conn)
            row = (
                await conn.execute(
                    text(
                        "SELECT chamber_heat_soak, heat_soak_temperature, heat_soak_minutes, "
                        "preheat_started_at FROM print_queue"
                    )
                )
            ).one()
            assert tuple(row) == (0, 60, 30, None)
    finally:
        await engine.dispose()
