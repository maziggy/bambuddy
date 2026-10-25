"""Deleting a printer must not hand what was limited to it to other printers.

A notification provider limited to a printer became one for all printers when
the printer was deleted (printer_id set to NULL, which means all): every other
printer's events and camera pictures went to it. An API key limited to the
printer kept its id, which SQLite gives to the next printer created.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from backend.app.core.database import _drop_deleted_printers_from_limits, _repair_sqlite_orphans, load_all_models
from backend.app.models.api_key import APIKey
from backend.app.models.notification import NotificationProvider
from backend.app.models.printer import Printer
from backend.app.services.notification_service import notification_service

pytestmark = [pytest.mark.integration]


async def _printers(db) -> tuple[int, int]:
    a = Printer(name="A", serial_number="SCOPE-A", ip_address="192.0.2.21", access_code="x")
    b = Printer(name="B", serial_number="SCOPE-B", ip_address="192.0.2.22", access_code="x")
    db.add_all([a, b])
    await db.commit()
    return a.id, b.id


async def _providers(db, a: int, b: int) -> None:
    db.add_all(
        [
            NotificationProvider(
                name=name,
                provider_type="webhook",
                config="{}",
                printer_id=printer_id,
                enabled=True,
                on_print_start=True,
            )
            for name, printer_id in (("only A", a), ("only B", b), ("all printers", None))
        ]
    )
    await db.commit()


async def _keys(db, a: int, b: int) -> None:
    db.add_all(
        [
            APIKey(name=name, key_hash="h", key_prefix=f"bb_{name[:6]}", printer_ids=printer_ids)
            for name, printer_ids in (("A and B", [a, b]), ("only B", [b]), ("all printers", None))
        ]
    )
    await db.commit()


async def _scopes(db) -> tuple[dict, dict]:
    providers = dict((await db.execute(select(NotificationProvider.name, NotificationProvider.printer_id))).all())
    statement = select(APIKey.name, APIKey.printer_ids).execution_options(populate_existing=True)
    keys = dict((await db.execute(statement)).all())
    return providers, keys


async def test_deleting_a_printer_removes_what_was_limited_to_it(async_client: AsyncClient, db_session):
    a, b = await _printers(db_session)
    await _providers(db_session, a, b)
    await _keys(db_session, a, b)

    response = await async_client.delete(f"/api/v1/printers/{b}")
    assert response.status_code == 200, response.text

    providers, keys = await _scopes(db_session)
    assert providers == {"only A": a, "all printers": None}
    # A key limited to no printer left may use none ([] means none, None all).
    assert keys == {"A and B": [a], "only B": [], "all printers": None}
    recipients = await notification_service._get_providers_for_event(db_session, "on_print_start", a)
    assert sorted(p.name for p in recipients) == ["all printers", "only A"]


async def test_the_startup_repair_takes_deleted_printers_out_of_api_keys(db_session):
    """SQLite installs that deleted a printer before this fix: keys kept its id.

    (Providers weren't left pointing at it; the delete made them all-printer
    ones, which nothing can tell from the intended ones.)
    """
    a, b = await _printers(db_session)
    await _keys(db_session, a, b)
    await db_session.execute(text("DELETE FROM printers WHERE id = :id"), {"id": b})  # the old way
    await db_session.commit()

    await _repair_sqlite_orphans(await db_session.connection())
    await _drop_deleted_printers_from_limits(await db_session.connection())
    await db_session.commit()

    assert (await _scopes(db_session))[1] == {"A and B": [a], "only B": [], "all printers": None}


def test_a_provider_goes_with_its_printer_on_postgresql_too():
    """SET NULL would make it a provider for all printers."""
    (fk,) = load_all_models().tables["notification_providers"].c.printer_id.foreign_keys
    assert fk.ondelete == "CASCADE"


async def test_a_notify_provider_of_the_printer_is_retired_on_the_service_too(async_client: AsyncClient, db_session):
    """Deleted as the provider delete route does it: its phone widgets and live
    activities are cleaned up remotely, not left behind by a cascade."""
    from unittest.mock import AsyncMock, MagicMock, patch

    _, b = await _printers(db_session)
    provider = NotificationProvider(name="phones", provider_type="notify", config="{}", printer_id=b)
    db_session.add(provider)
    await db_session.commit()
    provider_id = provider.id

    routes = "backend.app.api.routes.notifications"
    with (
        patch(f"{routes}.notify_live_activities.schedule_cleanup", AsyncMock()) as activities,
        patch(f"{routes}.notify_widgets.schedule_cleanup", AsyncMock()) as widgets,
        patch(f"{routes}._notify_providers_changed", MagicMock()) as changed,
    ):
        response = await async_client.delete(f"/api/v1/printers/{b}")

    assert response.status_code == 200, response.text
    assert activities.await_args.args[0] == provider_id
    assert widgets.await_args.args[0] == provider_id
    changed.assert_called_once()
    assert (await _scopes(db_session))[0] == {}


async def test_a_key_left_with_no_printer_stays_so_after_a_restart(db_session):
    """[] reaches no printer; a start-up step that turned it into NULL (all) undid that."""
    from backend.app.core.database import run_migrations

    db_session.add(APIKey(name="none", key_hash="h", key_prefix="bb_none", printer_ids=[]))
    await db_session.commit()

    await run_migrations(await db_session.connection())
    await db_session.commit()

    assert (await _scopes(db_session))[1] == {"none": []}


async def test_open_connections_lose_the_deleted_printer(async_client: AsyncClient, db_session):
    """Their printer access is worked out at connect; SQLite gives the id to the next printer."""
    from unittest.mock import AsyncMock, patch

    _, b = await _printers(db_session)
    with patch("backend.app.api.routes.printers.ws_manager.refresh_printer_scopes", AsyncMock()) as refresh:
        response = await async_client.delete(f"/api/v1/printers/{b}")

    assert response.status_code == 200, response.text
    refresh.assert_awaited_once()


async def test_a_virtual_printer_aimed_at_the_printer_goes_with_it(async_client: AsyncClient, db_session):
    """Without a target a queue-mode one takes jobs for any printer of its model."""
    from unittest.mock import AsyncMock, patch

    from backend.app.models.virtual_printer import VirtualPrinter

    a, b = await _printers(db_session)
    db_session.add_all(
        [
            VirtualPrinter(name="to B", mode="queue", target_printer_id=b, serial_suffix="391800011"),
            VirtualPrinter(name="to A", mode="queue", target_printer_id=a, serial_suffix="391800012"),
        ]
    )
    await db_session.commit()

    manager = "backend.app.services.virtual_printer.virtual_printer_manager"
    with (
        patch(f"{manager}.remove_instance", AsyncMock()) as removed,
        patch(f"{manager}.sync_from_db", AsyncMock()),
    ):
        response = await async_client.delete(f"/api/v1/printers/{b}")

    assert response.status_code == 200, response.text
    left = (await db_session.execute(select(VirtualPrinter.name).execution_options(populate_existing=True))).all()
    assert [name for (name,) in left] == ["to A"]
    removed.assert_awaited_once()


async def test_obico_detection_drops_the_deleted_printer(async_client: AsyncClient, db_session):
    """SQLite would otherwise enrol the next printer given its id."""
    from backend.app.models.settings import Settings

    a, b = await _printers(db_session)
    c = Printer(name="C", serial_number="SCOPE-C", ip_address="192.0.2.23", access_code="x")
    db_session.add(c)
    await db_session.commit()
    db_session.add(Settings(key="obico_enabled_printers", value=f"[{a},{b},{c.id}]"))
    await db_session.commit()

    response = await async_client.delete(f"/api/v1/printers/{b}")

    assert response.status_code == 200, response.text
    statement = select(Settings.value).where(Settings.key == "obico_enabled_printers")
    # Written as the settings page writes it, or it reads as an unsaved change and saves again.
    assert (await db_session.execute(statement.execution_options(populate_existing=True))).scalar() == f"[{a},{c.id}]"


@pytest.mark.sqlite_only  # stages stale ids with a raw delete, which foreign keys refuse or null out
async def test_the_startup_repair_drops_deleted_printers_from_obico_and_virtual_printers(db_session):
    from backend.app.models.settings import Settings
    from backend.app.models.virtual_printer import VirtualPrinter

    a, b = await _printers(db_session)
    db_session.add_all(
        [
            Settings(key="obico_enabled_printers", value=f"[{b}]"),
            VirtualPrinter(name="to B", mode="queue", target_printer_id=b, serial_suffix="391800013"),
            VirtualPrinter(name="any", mode="queue", target_printer_id=None, serial_suffix="391800014"),
        ]
    )
    await db_session.commit()
    await db_session.execute(text("DELETE FROM printers WHERE id = :id"), {"id": b})  # the old way
    await db_session.commit()

    await _repair_sqlite_orphans(await db_session.connection())
    await _drop_deleted_printers_from_limits(await db_session.connection())
    await db_session.commit()

    obico = (await db_session.execute(select(Settings.value).where(Settings.key == "obico_enabled_printers"))).scalar()
    assert obico == "[]"  # none, not all ("" would be all)
    left = (await db_session.execute(select(VirtualPrinter.name))).all()
    assert [name for (name,) in left] == ["any"]


async def test_its_smart_plugs_go_with_it(async_client: AsyncClient, db_session):
    """Without a printer a plug is everyone's: users and keys limited to other
    printers could switch the hardware it still powers."""
    from datetime import datetime
    from unittest.mock import patch

    from backend.app.models.smart_plug import SmartPlug
    from backend.app.models.smart_plug_energy_snapshot import SmartPlugEnergySnapshot

    a, b = await _printers(db_session)
    plugs = [
        SmartPlug(name="B's", plug_type="mqtt", mqtt_topic="plugs/b", printer_id=b),
        SmartPlug(name="A's", plug_type="tasmota", ip_address="192.0.2.31", printer_id=a),
        SmartPlug(name="spare", plug_type="tasmota", ip_address="192.0.2.32", printer_id=None),
    ]
    db_session.add_all(plugs)
    await db_session.commit()
    b_plug = plugs[0].id
    db_session.add(SmartPlugEnergySnapshot(plug_id=b_plug, recorded_at=datetime(2026, 10, 1), lifetime_kwh=1.0))
    await db_session.commit()

    with patch("backend.app.api.routes.smart_plugs.mqtt_relay.smart_plug_service.unsubscribe") as unsubscribe:
        response = await async_client.delete(f"/api/v1/printers/{b}")

    assert response.status_code == 200, response.text
    left = (await db_session.execute(select(SmartPlug.name).execution_options(populate_existing=True))).all()
    assert sorted(name for (name,) in left) == ["A's", "spare"]
    assert (await db_session.execute(select(SmartPlugEnergySnapshot.id))).all() == []
    unsubscribe.assert_called_once_with(b_plug)


def test_a_smart_plug_goes_with_its_printer_on_postgresql_too():
    (fk,) = load_all_models().tables["smart_plugs"].c.printer_id.foreign_keys
    assert fk.ondelete == "CASCADE"


@pytest.mark.parametrize("method", ["create", "update"])
async def test_an_api_key_cant_be_given_a_printer_that_doesnt_exist(async_client: AsyncClient, db_session, method):
    """SQLite would give that id to the next printer created, and the key with it."""
    a, _ = await _printers(db_session)
    if method == "create":
        response = await async_client.post("/api/v1/api-keys/", json={"name": "k", "printer_ids": [a, 999]})
    else:
        created = await async_client.post("/api/v1/api-keys/", json={"name": "k", "printer_ids": [a]})
        response = await async_client.patch(f"/api/v1/api-keys/{created.json()['id']}", json={"printer_ids": [999]})

    assert response.status_code == 400, response.text
    assert [ids for _, ids in (await _scopes(db_session))[1].items()] in ([], [[a]])


@pytest.mark.parametrize("method", ["create", "update"])
async def test_a_provider_cant_be_limited_to_a_printer_that_doesnt_exist(async_client: AsyncClient, db_session, method):
    body = {"name": "p", "provider_type": "webhook", "config": {"webhook_url": "http://192.0.2.99/hook"}}
    if method == "create":
        response = await async_client.post("/api/v1/notifications/", json={**body, "printer_id": 999})
    else:
        created = await async_client.post("/api/v1/notifications/", json=body)
        response = await async_client.patch(f"/api/v1/notifications/{created.json()['id']}", json={"printer_id": 999})

    assert response.status_code == 400, response.text
    assert 999 not in (await _scopes(db_session))[0].values()


async def test_obico_cant_be_given_a_printer_that_doesnt_exist(async_client: AsyncClient, db_session):
    """A settings page still open from before a delete would save the deleted id back."""
    a, _ = await _printers(db_session)

    response = await async_client.put("/api/v1/settings/", json={"obico_enabled_printers": f"[{a}, 999]"})

    assert response.status_code == 400, response.text


async def test_a_slicer_pipeline_aimed_at_the_printer_loses_it(async_client: AsyncClient, db_session):
    """SQLite would otherwise run it on the next printer given the id."""
    from backend.app.models.pipeline_run import PipelineJob, PipelineRun
    from backend.app.models.slicer_pipeline import SlicerPipeline

    _, b = await _printers(db_session)
    pipeline = SlicerPipeline(
        name="p",
        printer_preset_source="local",
        printer_preset_id="1",
        process_preset_source="local",
        process_preset_id="2",
        filament_presets_json="[]",
        target_kind="specific_printer",
        target_printer_id=b,
    )
    db_session.add(pipeline)
    await db_session.commit()
    run = PipelineRun(pipeline_id=pipeline.id, copies=1)
    db_session.add(run)
    await db_session.commit()
    db_session.add(PipelineJob(pipeline_run_id=run.id, copy_index=0, assigned_printer_id=b))
    await db_session.commit()

    response = await async_client.delete(f"/api/v1/printers/{b}")

    assert response.status_code == 200, response.text
    targets = select(SlicerPipeline.target_printer_id).execution_options(populate_existing=True)
    assert (await db_session.execute(targets)).scalar() is None
    assigned = select(PipelineJob.assigned_printer_id).execution_options(populate_existing=True)
    assert (await db_session.execute(assigned)).scalar() is None


async def test_obico_ids_already_stored_dont_block_a_save(async_client: AsyncClient, db_session):
    """PostgreSQL kept the id of a printer deleted before deletes dropped it, and
    the settings page sends the whole list back: every save would be refused."""
    from backend.app.models.settings import Settings

    a, _ = await _printers(db_session)
    db_session.add(Settings(key="obico_enabled_printers", value=f"[{a},999]"))
    await db_session.commit()

    response = await async_client.put("/api/v1/settings/", json={"obico_enabled_printers": f"[{a},999]"})

    assert response.status_code == 200, response.text
    response = await async_client.put("/api/v1/settings/", json={"obico_enabled_printers": f"[{a},998]"})
    assert response.status_code == 400, response.text


@pytest.mark.sqlite_only  # stages stale ids with a raw delete, which foreign keys refuse or null out
async def test_the_startup_trim_of_printer_lists_runs_on_postgresql_too(db_session):
    """PostgreSQL holds stale ids too: kept by its own earlier deletes, or copied
    from a SQLite backup, after which its sequence hands out the deleted id again."""
    from unittest.mock import patch

    from backend.app.models.settings import Settings
    from backend.app.models.virtual_printer import VirtualPrinter

    a, b = await _printers(db_session)
    await _keys(db_session, a, b)
    db_session.add_all(
        [
            Settings(key="obico_enabled_printers", value=f"[{a},{b}]"),
            VirtualPrinter(name="to B", mode="queue", target_printer_id=b, serial_suffix="391800016"),
            VirtualPrinter(name="to A", mode="queue", target_printer_id=a, serial_suffix="391800017"),
        ]
    )
    await db_session.commit()
    await db_session.execute(text("DELETE FROM printers WHERE id = :id"), {"id": b})  # the old way
    await db_session.commit()

    with patch("backend.app.core.database.is_sqlite", return_value=False):
        await _drop_deleted_printers_from_limits(await db_session.connection())
    await db_session.commit()

    assert (await _scopes(db_session))[1] == {"A and B": [a], "only B": [], "all printers": None}
    obico = (await db_session.execute(select(Settings.value).where(Settings.key == "obico_enabled_printers"))).scalar()
    assert obico == f"[{a}]"
    left = (await db_session.execute(select(VirtualPrinter.name))).all()
    assert [name for (name,) in left] == ["to A"]


async def test_a_deleted_virtual_printer_isnt_rebuilt_from_the_old_settings(db_session):
    """The move from the single virtual printer settings ran at every start with
    no virtual printers, rebuilding one aimed at the old target id."""
    from backend.app.core.database import run_migrations
    from backend.app.models.settings import Settings
    from backend.app.models.virtual_printer import VirtualPrinter

    _, b = await _printers(db_session)
    db_session.add_all(
        [
            Settings(key="virtual_printer_enabled", value="true"),
            Settings(key="virtual_printer_target_printer_id", value=str(b)),
            VirtualPrinter(name="to B", mode="proxy", target_printer_id=b, serial_suffix="391800015"),
        ]
    )
    await db_session.commit()
    await run_migrations(await db_session.connection())
    await db_session.commit()

    await db_session.execute(text("DELETE FROM virtual_printers"))
    await db_session.commit()
    await run_migrations(await db_session.connection())
    await db_session.commit()

    assert (await db_session.execute(select(VirtualPrinter.id))).all() == []


async def test_a_pipeline_cant_be_aimed_at_a_printer_that_doesnt_exist(async_client: AsyncClient, db_session):
    from backend.app.models.slicer_pipeline import SlicerPipeline

    a, _ = await _printers(db_session)
    pipeline = SlicerPipeline(
        name="p",
        printer_preset_source="local",
        printer_preset_id="1",
        process_preset_source="local",
        process_preset_id="2",
        filament_presets_json='[{"source": "local", "id": "3"}]',
    )
    db_session.add(pipeline)
    await db_session.commit()

    response = await async_client.put(f"/api/v1/slicer-pipelines/{pipeline.id}", json={"target_printer_id": 999})
    assert response.status_code == 400, response.text
    response = await async_client.put(f"/api/v1/slicer-pipelines/{pipeline.id}", json={"target_printer_id": a})
    assert response.status_code == 200, response.text


async def test_a_settings_restore_drops_printers_that_are_gone_from_obico(db_session):
    """A backup taken before a printer delete still lists it."""
    from backend.app.models.settings import Settings
    from backend.app.services.github_restore import _CategoryTally, github_restore_service

    a, _ = await _printers(db_session)
    await github_restore_service._restore_settings(
        db_session, {"settings": {"obico_enabled_printers": f"[{a},999]"}}, overwrite=True, tally=_CategoryTally()
    )
    await db_session.commit()

    obico = (await db_session.execute(select(Settings.value).where(Settings.key == "obico_enabled_printers"))).scalar()
    assert obico == f"[{a}]"


@pytest.mark.parametrize("via", ["printer", "plug"])
async def test_a_deleted_plugs_pending_auto_off_is_cancelled(async_client: AsyncClient, db_session, via):
    """It holds the plug's address and the printer id: it would switch the outlet
    later and, on SQLite, read and mark offline the next printer given the id."""
    import asyncio

    from backend.app.models.smart_plug import SmartPlug
    from backend.app.services.smart_plug_manager import smart_plug_manager

    _, b = await _printers(db_session)
    plug = SmartPlug(name="B's", plug_type="tasmota", ip_address="192.0.2.33", printer_id=b)
    db_session.add(plug)
    await db_session.commit()
    pending = asyncio.create_task(asyncio.sleep(3600))
    smart_plug_manager._pending_off[plug.id] = pending
    try:
        url = f"/api/v1/printers/{b}" if via == "printer" else f"/api/v1/smart-plugs/{plug.id}"
        response = await async_client.delete(url)
        assert response.status_code == 200, response.text
        await asyncio.sleep(0)
        assert pending.cancelled()
        assert plug.id not in smart_plug_manager._pending_off
    finally:
        pending.cancel()
        smart_plug_manager._pending_off.pop(plug.id, None)
