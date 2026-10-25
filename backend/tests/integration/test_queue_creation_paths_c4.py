"""Every path that creates queue jobs behaves like the print dialog (C4).

Only ``POST /queue/`` announced a new job ("job added" notification and the
MQTT relay event); File Manager's "Add to queue", the webhook, a virtual
printer, a pipeline run and an order's "dispatch remaining" said nothing. The
default print options (timelapse, bed levelling, ...) reached only the print
dialog and the virtual printer. The copies an order dispatches lost their cost
centre, so they were billed to none and never budget-checked. And a
``scheduled_time`` sent with a UTC offset lost the offset, so the job started
hours late.
"""

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.auth import generate_api_key, get_password_hash
from backend.app.core.config import settings as app_settings
from backend.app.models.api_key import APIKey
from backend.app.models.archive import PrintArchive
from backend.app.models.finance import CostCenter, CostCenterMember
from backend.app.models.group import Group
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.mqtt_relay import mqtt_relay
from backend.app.services.notification_service import notification_service

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _set(db_session, key: str, value: str) -> None:
    row = await db_session.scalar(select(Settings).where(Settings.key == key))
    if row is None:
        db_session.add(Settings(key=key, value=value))
    else:
        row.value = value
    await db_session.commit()


async def _items(test_engine, ids=None) -> list[PrintQueueItem]:
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as fresh:
        query = select(PrintQueueItem).order_by(PrintQueueItem.id)
        if ids is not None:
            query = query.where(PrintQueueItem.id.in_(ids))
        return list((await fresh.execute(query)).scalars().all())


@pytest.fixture
def announced():
    """The notification and relay calls, recorded."""
    notify = AsyncMock()
    relay = AsyncMock()
    with (
        patch.object(notification_service, "on_queue_job_added", notify),
        patch.object(mqtt_relay, "on_queue_job_added", relay),
    ):
        yield notify, relay


@pytest.fixture
async def library_files(db_session):
    from backend.app.models.library import LibraryFile

    made = []

    async def make(stem: str):
        name = f"{stem}_{uuid4().hex[:8]}.gcode.3mf"
        rel_path = f"archive/library/files/{name}"
        abs_path = Path(app_settings.base_dir) / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(b"probe")
        made.append(abs_path)
        lib_file = LibraryFile(filename=name, file_path=rel_path, file_size=5, file_type="3mf")
        db_session.add(lib_file)
        await db_session.commit()
        await db_session.refresh(lib_file)
        return lib_file

    yield make
    for path in made:
        path.unlink(missing_ok=True)


async def _printer(db_session, name="C4 Printer", model="X1C") -> Printer:
    printer = Printer(
        name=name,
        ip_address=f"192.168.44.{len(name)}",
        serial_number=f"C4{uuid4().hex[:12].upper()}",
        access_code="12345678",
        model=model,
    )
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)
    return printer


async def _archive(db_session, filename="widget.gcode.3mf", **extra) -> PrintArchive:
    archive = PrintArchive(
        filename=filename,
        print_name="widget",
        file_path=f"/tmp/c4_{uuid4().hex}.3mf",  # nosec B108
        file_size=1,
        content_hash=uuid4().hex,
        status="completed",
        **extra,
    )
    db_session.add(archive)
    await db_session.commit()
    await db_session.refresh(archive)
    return archive


async def _webhook_key(db_session) -> dict[str, str]:
    full_key, key_hash, key_prefix = generate_api_key()
    db_session.add(
        APIKey(name="c4", key_hash=key_hash, key_prefix=key_prefix, user_id=None, can_queue=True, can_read_status=True)
    )
    await db_session.commit()
    return {"X-API-Key": full_key}


class TestEveryPathAnnouncesItsJobs:
    async def test_a_file_manager_bulk_add_sends_one_summary(
        self, async_client: AsyncClient, db_session, library_files, announced
    ):
        notify, relay = announced
        printer = await _printer(db_session)
        files = [await library_files("bracket"), await library_files("hinge")]

        response = await async_client.post(
            "/api/v1/library/files/add-to-queue",
            json={"file_ids": [f.id for f in files], "printer_id": printer.id},
        )

        assert response.status_code == 200, response.text
        notify.assert_awaited_once()
        kwargs = notify.await_args.kwargs
        assert kwargs["job_name"].startswith("2 jobs (bracket_")
        assert "hinge_" in kwargs["job_name"]
        assert kwargs["target"] == printer.name
        assert kwargs["printer_id"] == printer.id
        # The relay is a machine event: one per job.
        assert sorted(c.kwargs["job_id"] for c in relay.await_args_list) == sorted(
            a["queue_item_id"] for a in response.json()["added"]
        )

    async def test_the_webhook_announces_its_job(self, async_client: AsyncClient, db_session, announced):
        notify, relay = announced
        printer = await _printer(db_session)
        archive = await _archive(db_session)

        response = await async_client.post(
            "/api/v1/webhook/queue/add",
            json={"printer_id": printer.id, "archive_id": archive.id},
            headers=await _webhook_key(db_session),
        )

        assert response.status_code == 200, response.text
        notify.assert_awaited_once()
        assert notify.await_args.kwargs["job_name"] == "widget"
        assert relay.await_args.kwargs["job_id"] == response.json()["id"]

    async def test_an_orders_dispatch_announces_the_copies(self, async_client: AsyncClient, db_session, announced):
        notify, _ = announced
        printer = await _printer(db_session)
        archive = await _archive(db_session)
        order = (
            await async_client.post(
                "/api/v1/queue/batches",
                json={"name": "o", "archive_id": archive.id, "plates": [{"plate_id": None, "quantity_target": 3}]},
            )
        ).json()
        first = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "batch_id": order["id"]}
        )
        assert first.status_code == 200, first.text
        notify.reset_mock()

        response = await async_client.post(f"/api/v1/queue/batches/{order['id']}/dispatch", json={})

        assert response.status_code == 200, response.text
        notify.assert_awaited_once()
        assert notify.await_args.kwargs["job_name"] == "widget ×2"

    async def test_a_failing_notification_does_not_fail_the_add(
        self, async_client: AsyncClient, db_session, library_files
    ):
        lib_file = await library_files("plain")
        with patch.object(notification_service, "on_queue_job_added", AsyncMock(side_effect=RuntimeError("smtp"))):
            response = await async_client.post("/api/v1/library/files/add-to-queue", json={"file_ids": [lib_file.id]})

        assert response.status_code == 200
        assert len(response.json()["added"]) == 1

    async def test_the_print_dialog_still_announces_once(self, async_client: AsyncClient, db_session, announced):
        notify, relay = announced
        printer = await _printer(db_session)
        archive = await _archive(db_session)

        response = await async_client.post(
            "/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id, "quantity": 3}
        )

        assert response.status_code == 200, response.text
        notify.assert_awaited_once()
        assert notify.await_args.kwargs["job_name"] == "widget ×3"
        assert relay.await_count == 3


class TestTheSummary:
    async def test_jobs_for_different_printers_are_announced_per_printer(self, db_session, test_engine, announced):
        """A provider scoped to one printer must not hear of another's jobs."""
        from backend.app.services.queue_announcements import announce_queue_jobs_added

        notify, _ = announced
        a, b = await _printer(db_session, "Alpha"), await _printer(db_session, "Bravo")
        archive = await _archive(db_session)
        items = [
            PrintQueueItem(printer_id=a.id, archive_id=archive.id, position=1),
            PrintQueueItem(printer_id=b.id, archive_id=archive.id, position=2),
            PrintQueueItem(printer_id=None, target_model="P1S", archive_id=archive.id, position=3),
        ]
        db_session.add_all(items)
        await db_session.commit()

        await announce_queue_jobs_added(db_session, [i.id for i in items])

        sent = sorted((c.kwargs["printer_id"] or 0, c.kwargs["target"]) for c in notify.await_args_list)
        assert sent == sorted([(a.id, "Alpha"), (b.id, "Bravo"), (0, "Any P1S")])

    async def test_many_different_files_are_named_briefly(self, db_session, announced):
        from backend.app.services.queue_announcements import announce_queue_jobs_added

        notify, _ = announced
        items = []
        for n in range(5):
            archive = await _archive(db_session, filename=f"part{n}.3mf")
            items.append(PrintQueueItem(printer_id=None, archive_id=archive.id, position=n))
        db_session.add_all(items)
        await db_session.commit()

        await announce_queue_jobs_added(db_session, [i.id for i in items])

        assert notify.await_args.kwargs["job_name"] == "5 jobs (part0, part1, part2, …)"
        assert notify.await_args.kwargs["target"] == "Unassigned"

    async def test_nothing_added_says_nothing(self, db_session, announced):
        from backend.app.services.queue_announcements import announce_queue_jobs_added

        notify, relay = announced
        await announce_queue_jobs_added(db_session, [])

        notify.assert_not_awaited()
        relay.assert_not_awaited()


class TestTheDefaultPrintOptions:
    async def test_unset_they_are_the_settings_defaults(self, db_session):
        from backend.app.services.print_defaults import default_print_options

        assert await default_print_options(db_session) == {
            "bed_levelling": "auto",
            "flow_cali": "auto",
            "nozzle_offset_cali": "auto",
            "vibration_cali": True,
            "layer_inspect": False,
            "timelapse": False,
        }

    async def test_set_they_are_read_including_legacy_values(self, db_session):
        from backend.app.services.print_defaults import default_print_options

        for key, value in {
            "default_bed_levelling": "false",  # legacy boolean row
            "default_flow_cali": "on",
            "default_nozzle_offset_cali": "off",
            "default_vibration_cali": "false",
            "default_layer_inspect": "true",
            "default_timelapse": "true",
        }.items():
            await _set(db_session, key, value)

        assert await default_print_options(db_session) == {
            "bed_levelling": "off",
            "flow_cali": "on",
            "nozzle_offset_cali": "off",
            "vibration_cali": False,
            "layer_inspect": True,
            "timelapse": True,
        }

    async def test_a_file_manager_add_uses_them(
        self, async_client: AsyncClient, db_session, test_engine, library_files
    ):
        await _set(db_session, "default_timelapse", "true")
        await _set(db_session, "default_bed_levelling", "off")
        lib_file = await library_files("defaults")

        response = await async_client.post("/api/v1/library/files/add-to-queue", json={"file_ids": [lib_file.id]})

        assert response.status_code == 200, response.text
        (item,) = await _items(test_engine, [response.json()["added"][0]["queue_item_id"]])
        assert (item.timelapse, item.bed_levelling) == (True, "off")

    async def test_the_webhook_uses_them(self, async_client: AsyncClient, db_session, test_engine):
        await _set(db_session, "default_timelapse", "true")
        await _set(db_session, "default_layer_inspect", "true")
        printer = await _printer(db_session)
        archive = await _archive(db_session)

        response = await async_client.post(
            "/api/v1/webhook/queue/add",
            json={"printer_id": printer.id, "archive_id": archive.id},
            headers=await _webhook_key(db_session),
        )

        assert response.status_code == 200, response.text
        (item,) = await _items(test_engine, [response.json()["id"]])
        assert (item.timelapse, item.layer_inspect) == (True, True)


class TestAPipelineRun:
    async def _run(self, db_session, test_engine, tmp_path, owner_id=None, slices=None):
        from backend.app.api.routes.pipeline_runs import _make_orchestration_callable
        from backend.app.core.printer_scope import ALL_PRINTERS
        from backend.app.models.library import LibraryFile
        from backend.app.models.pipeline_run import PipelineJob, PipelineRun
        from backend.app.models.slicer_pipeline import SlicerPipeline
        from backend.app.schemas.slicer import SliceResponse

        printer = await _printer(db_session, "Pipe")
        source = LibraryFile(filename="pipe.3mf", file_path="library/pipe.3mf", file_type="3mf", file_size=1)
        pipeline = SlicerPipeline(
            name="p",
            printer_preset_source="local",
            printer_preset_id="1",
            process_preset_source="local",
            process_preset_id="2",
            filament_presets_json='[{"source": "local", "id": "3"}]',
            target_kind="specific_printer",
            target_printer_id=printer.id,
        )
        db_session.add_all([source, pipeline])
        await db_session.commit()
        run = PipelineRun(pipeline_id=pipeline.id, source_library_file_id=source.id, copies=2, created_by=owner_id)
        db_session.add(run)
        await db_session.commit()
        db_session.add_all([PipelineJob(pipeline_run_id=run.id, copy_index=i) for i in range(2)])
        await db_session.commit()
        model = tmp_path / "pipe.3mf"
        model.write_bytes(b"model")

        async def sliced(session, **kwargs):
            if slices is not None:
                slices.append(kwargs)
            out = LibraryFile(filename="pipe.gcode.3mf", file_path="library/pipe.gcode.3mf", file_type="3mf")
            out.file_size = 1
            session.add(out)
            await session.flush()
            return SliceResponse(
                library_file_id=out.id, name="pipe", print_time_seconds=1, filament_used_g=1.0, filament_used_mm=1.0
            )

        orchestrate = _make_orchestration_callable(
            run_id=run.id,
            pipeline_id=pipeline.id,
            src_kind="library_file",
            src_id=source.id,
            src_filename="pipe.3mf",
            src_path=Path(model),
            creator_user_id=None,
            copies=2,
            printer_scope=ALL_PRINTERS,
        )
        with (
            patch(
                "backend.app.api.routes.pipeline_runs.async_session",
                async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False),
            ),
            patch("backend.app.api.routes.library.slice_and_persist", new=sliced),
            patch("backend.app.api.routes.pipeline_runs._publish_run_event", new=AsyncMock()),
        ):
            await orchestrate(1)
        return await _items(test_engine)

    async def test_announces_its_copies_and_uses_the_defaults(self, db_session, test_engine, tmp_path, announced):
        notify, _ = announced
        await _set(db_session, "default_timelapse", "true")

        items = await self._run(db_session, test_engine, tmp_path)

        assert len(items) == 2
        assert all(item.timelapse for item in items)
        notify.assert_awaited_once()
        assert notify.await_args.kwargs["job_name"] == "pipe ×2"

    async def test_the_sliced_file_is_the_runs_owners(self, db_session, test_engine, tmp_path, announced):
        """Also when no one resolves cloud presets: a key without the cloud scope started it."""
        owner = User(username="runowner", password_hash="x", role="user")
        db_session.add(owner)
        await db_session.commit()
        slices: list = []

        items = await self._run(db_session, test_engine, tmp_path, owner_id=owner.id, slices=slices)

        assert [s["current_user_id"] for s in slices] == [None]
        assert [s["owner_id"] for s in slices] == [owner.id]
        assert {item.created_by_id for item in items} == {owner.id}


class TestAVirtualPrinter:
    async def test_announces_what_it_queued(self, tmp_path):
        from backend.tests.unit.services.test_vp_queue_intake_d2 import _enqueue, _session, _vp

        added: list = []
        factory, _ = _session(added)
        inst = _vp(tmp_path, factory, target_printer_id=7)
        announce = AsyncMock()

        await _enqueue(inst, tmp_path, {"timelapse": True}, announce=announce)

        announce.assert_awaited_once()
        assert announce.await_args.args[1] == [item.id for item in added]

    async def test_where_the_slicer_says_nothing_it_uses_the_defaults(self, tmp_path):
        """Read from the same helper as every other path now."""
        from backend.tests.unit.services.test_vp_queue_intake_d2 import _enqueue, _session, _vp

        added: list = []
        factory, _ = _session(added)
        inst = _vp(tmp_path, factory, target_printer_id=7)

        await _enqueue(
            inst,
            tmp_path,
            {"flow_cali": True},
            {"default_timelapse": "true", "default_layer_inspect": "true", "default_vibration_cali": "false"},
        )

        assert (added[0].timelapse, added[0].layer_inspect, added[0].vibration_cali) == (True, True, False)


class TestAnOrdersCopiesKeepTheirCostCentre:
    @pytest.fixture
    async def billed(self, db_session):
        await _set(db_session, "auth_enabled", "true")
        await _set(db_session, "advanced_auth_enabled", "false")
        group = Group(
            name="c4-dispatchers",
            description="t",
            permissions=[
                "queue:create",
                "queue:read_all",
                "queue:update_all",
                "archives:read_all",
                "archives:reprint_all",
            ],
            is_system=False,
        )
        db_session.add(group)
        await db_session.flush()
        admin = User(username="c4admin", password_hash=get_password_hash("Pass1word!"), role="admin", is_active=True)
        clerk = User(username="c4clerk", password_hash=get_password_hash("Pass1word!"), is_active=True, groups=[group])
        center = CostCenter(name="c4-center", is_active=True, is_private=False)
        db_session.add_all([admin, clerk, center])
        await db_session.commit()
        printer = await _printer(db_session)
        archive = await _archive(db_session, cost=1.25, filament_used_grams=50.0)
        await _set(db_session, "billing_enabled", "true")
        return {"admin": admin, "clerk": clerk, "center": center, "printer": printer, "archive": archive}

    async def _login(self, client, username):
        response = await client.post("/api/v1/auth/login", json={"username": username, "password": "Pass1word!"})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    async def _order(self, client, headers, world):
        order = await client.post(
            "/api/v1/queue/batches",
            json={"name": "o", "archive_id": world["archive"].id, "plates": [{"plate_id": None, "quantity_target": 3}]},
            headers=headers,
        )
        assert order.status_code == 200, order.text
        first = await client.post(
            "/api/v1/queue/",
            json={
                "printer_id": world["printer"].id,
                "archive_id": world["archive"].id,
                "batch_id": order.json()["id"],
                "cost_center_id": world["center"].id,
            },
            headers=headers,
        )
        assert first.status_code == 200, first.text
        return order.json()["id"]

    async def test_the_copies_are_billed_to_it(self, async_client: AsyncClient, test_engine, billed):
        admin = await self._login(async_client, "c4admin")
        batch_id = await self._order(async_client, admin, billed)

        response = await async_client.post(f"/api/v1/queue/batches/{batch_id}/dispatch", json={}, headers=admin)

        assert response.status_code == 200, response.text
        items = [i for i in await _items(test_engine) if i.batch_id == batch_id]
        assert len(items) == 3
        assert {i.cost_center_id for i in items} == {billed["center"].id}

    async def test_a_dispatcher_who_may_not_use_it_is_refused(
        self, async_client: AsyncClient, db_session, test_engine, billed
    ):
        admin = await self._login(async_client, "c4admin")
        batch_id = await self._order(async_client, admin, billed)
        clerk = await self._login(async_client, "c4clerk")

        response = await async_client.post(f"/api/v1/queue/batches/{batch_id}/dispatch", json={}, headers=clerk)

        assert response.status_code == 403, response.text
        assert len([i for i in await _items(test_engine) if i.batch_id == batch_id]) == 1

        db_session.add(CostCenterMember(cost_center_id=billed["center"].id, user_id=billed["clerk"].id, can_print=True))
        await db_session.commit()
        response = await async_client.post(f"/api/v1/queue/batches/{batch_id}/dispatch", json={}, headers=clerk)
        assert response.status_code == 200, response.text

    async def test_the_budget_counts_every_copy(self, async_client: AsyncClient, db_session, test_engine, billed):
        center = await db_session.get(CostCenter, billed["center"].id)
        admin = await self._login(async_client, "c4admin")
        batch_id = await self._order(async_client, admin, billed)
        (template,) = [i for i in await _items(test_engine) if i.batch_id == batch_id]
        # Room for this one and one more, not for the two copies still owed.
        center.total_budget = 2 * template.estimated_cost + 0.01
        await db_session.commit()

        response = await async_client.post(f"/api/v1/queue/batches/{batch_id}/dispatch", json={}, headers=admin)

        assert response.status_code == 400, response.text
        assert "budget" in response.json()["detail"]

    async def test_copies_without_one_are_refused_while_billing_is_on(
        self, async_client: AsyncClient, db_session, test_engine, billed
    ):
        """Queued before billing was switched on: refused here, as POST /queue/
        would, rather than failing later at the start."""
        admin = await self._login(async_client, "c4admin")
        batch_id = await self._order(async_client, admin, billed)
        (template,) = [i for i in await _items(test_engine) if i.batch_id == batch_id]
        row = await db_session.get(PrintQueueItem, template.id)
        row.cost_center_id = None
        await db_session.commit()

        response = await async_client.post(f"/api/v1/queue/batches/{batch_id}/dispatch", json={}, headers=admin)

        assert response.status_code == 400, response.text
        assert "Cost center is required" in response.json()["detail"]

    async def test_a_budget_that_holds_every_copy_dispatches(
        self, async_client: AsyncClient, db_session, test_engine, billed
    ):
        """The copies are not counted against themselves."""
        center = await db_session.get(CostCenter, billed["center"].id)
        admin = await self._login(async_client, "c4admin")
        batch_id = await self._order(async_client, admin, billed)
        (template,) = [i for i in await _items(test_engine) if i.batch_id == batch_id]
        center.total_budget = 3 * template.estimated_cost + 0.01
        await db_session.commit()

        response = await async_client.post(f"/api/v1/queue/batches/{batch_id}/dispatch", json={}, headers=admin)

        assert response.status_code == 200, response.text
        copies = [i for i in await _items(test_engine) if i.batch_id == batch_id and i.id != template.id]
        assert [c.estimated_cost for c in copies] == [template.estimated_cost] * 2


class TestAScheduledTimeWithAnOffset:
    async def test_the_queue_stores_it_in_utc(self, async_client: AsyncClient, db_session, test_engine):
        printer = await _printer(db_session)
        archive = await _archive(db_session)

        response = await async_client.post(
            "/api/v1/queue/",
            json={"printer_id": printer.id, "archive_id": archive.id, "scheduled_time": "2026-10-15T10:00:00+02:00"},
        )

        assert response.status_code == 200, response.text
        (item,) = await _items(test_engine, [response.json()["id"]])
        assert item.scheduled_time == datetime(2026, 10, 15, 8, 0)
        assert response.json()["scheduled_time"] == "2026-10-15T08:00:00Z"

    async def test_an_edit_stores_it_in_utc(self, async_client: AsyncClient, db_session, test_engine):
        printer = await _printer(db_session)
        archive = await _archive(db_session)
        created = await async_client.post("/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id})

        response = await async_client.patch(
            f"/api/v1/queue/{created.json()['id']}", json={"scheduled_time": "2026-10-15T10:00:00-05:00"}
        )

        assert response.status_code == 200, response.text
        (item,) = await _items(test_engine, [created.json()["id"]])
        assert item.scheduled_time == datetime(2026, 10, 15, 15, 0)

    async def test_a_bulk_edit_stores_it_in_utc(self, async_client: AsyncClient, db_session, test_engine):
        printer = await _printer(db_session)
        archive = await _archive(db_session)
        created = await async_client.post("/api/v1/queue/", json={"printer_id": printer.id, "archive_id": archive.id})

        response = await async_client.patch(
            "/api/v1/queue/bulk",
            json={"item_ids": [created.json()["id"]], "scheduled_time": "2026-10-15T10:00:00+02:00"},
        )

        assert response.status_code == 200, response.text
        (item,) = await _items(test_engine, [created.json()["id"]])
        assert item.scheduled_time == datetime(2026, 10, 15, 8, 0)

    async def test_the_webhook_stores_it_in_utc(self, async_client: AsyncClient, db_session, test_engine):
        printer = await _printer(db_session)
        archive = await _archive(db_session)

        response = await async_client.post(
            "/api/v1/webhook/queue/add",
            json={"printer_id": printer.id, "archive_id": archive.id, "scheduled_time": "2026-10-15T10:00:00+02:00"},
            headers=await _webhook_key(db_session),
        )

        assert response.status_code == 200, response.text
        (item,) = await _items(test_engine, [response.json()["id"]])
        assert item.scheduled_time == datetime(2026, 10, 15, 8, 0)


async def test_the_postgres_bind_hook_converts_rather_than_drops_the_offset():
    """The backstop for any other aware value: naive columns hold UTC."""
    from datetime import timedelta, timezone

    from backend.app.core import database

    with patch.object(database, "is_sqlite", return_value=False):
        engine = database._create_engine("postgresql+asyncpg://u:p@127.0.0.1:1/never")
    try:
        (hook,) = [
            fn for fn in engine.sync_engine.dispatch.before_cursor_execute if "strip" in getattr(fn, "__name__", "")
        ]
        aware = datetime(2026, 10, 15, 10, 0, tzinfo=timezone(timedelta(hours=2)))

        _, params = hook(None, None, "", {"t": aware, "rows": [(aware,)]}, None, False)

        assert params == {"t": datetime(2026, 10, 15, 8, 0), "rows": [(datetime(2026, 10, 15, 8, 0),)]}
    finally:
        await engine.dispose()


async def test_a_time_at_the_edge_of_the_calendar_is_refused_not_a_crash(async_client: AsyncClient, db_session):
    printer = await _printer(db_session)
    archive = await _archive(db_session)

    queued = await async_client.post(
        "/api/v1/queue/",
        json={"printer_id": printer.id, "archive_id": archive.id, "scheduled_time": "0001-01-01T00:00:00+05:00"},
    )
    webhook = await async_client.post(
        "/api/v1/webhook/queue/add",
        json={"printer_id": printer.id, "archive_id": archive.id, "scheduled_time": "0001-01-01T00:00:00+05:00"},
        headers=await _webhook_key(db_session),
    )

    assert (queued.status_code, webhook.status_code) == (422, 400)


async def test_a_failed_notification_log_leaves_the_session_usable(db_session):
    from backend.app.services.queue_announcements import announce_queue_jobs_added

    archive = await _archive(db_session)
    item = PrintQueueItem(printer_id=None, archive_id=archive.id, position=1)
    db_session.add(item)
    await db_session.commit()

    async def log_then_fail(**kwargs):
        db_session.add(Settings(key="dup", value="1"))
        db_session.add(Settings(key="dup", value="2"))
        await kwargs["db"].commit()

    with patch.object(notification_service, "on_queue_job_added", side_effect=log_then_fail):
        await announce_queue_jobs_added(db_session, [item.id])

    db_session.add(Settings(key="after", value="1"))
    await db_session.commit()
