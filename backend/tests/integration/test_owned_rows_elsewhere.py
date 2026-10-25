"""Pipeline runs, the webhook and live archive events keep to what each user may see.

A pipeline run's queue jobs and source belong to the user who started it. The
run shows another user neither its source nor its queue jobs, and only someone
who may change those jobs cancels or clears it. A webhook key acts for its
owner: it starts only a job its owner could start, and lists only the jobs its
owner sees. A live archive event says only that an archive on a printer
changed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core import websocket
from backend.app.core.printer_scope import ALL_PRINTERS
from backend.app.models.pipeline_run import PipelineJob, PipelineRun
from backend.app.models.print_queue import PrintQueueItem
from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth
from backend.tests.integration.test_project_batch_scope import _member

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

OPERATOR = [
    "printers:read",
    "printers:control",
    "api_keys:create",
    "archives:read_own",
    "archives:update_own",
    "queue:read_own",
    "queue:update_own",
    "library:read_own",
    "makerworld:view",
    "pipelines:read",
    "pipelines:write",
    "pipelines:run",
]


async def _job(db_session, printer_id: int, archive_id: int, owner: int | None, position: int = 1) -> int:
    item = PrintQueueItem(
        printer_id=printer_id, archive_id=archive_id, created_by_id=owner, status="pending", position=position
    )
    db_session.add(item)
    await db_session.commit()
    return item.id


@pytest.fixture
async def two(async_client: AsyncClient, printer_factory, archive_factory, db_session):
    printer = await printer_factory(name="A")
    admin = await _admin_token(async_client)
    first, first_id = await _member(async_client, admin, "first", None, OPERATOR)
    second, second_id = await _member(async_client, admin, "second", None, OPERATOR)
    return SimpleNamespace(
        printer=printer, admin=_auth(admin), first=first, first_id=first_id, second=second, second_id=second_id
    )


async def _run(db_session, archive_factory, two, owner_id: int, status: str = "in_progress"):
    archive = await archive_factory(two.printer.id, created_by_id=owner_id, print_name="Their part")
    entry = await _job(db_session, two.printer.id, archive.id, owner_id)
    run = PipelineRun(source_archive_id=archive.id, status=status, copies=1, created_by=owner_id)
    db_session.add(run)
    await db_session.commit()
    db_session.add(
        PipelineJob(pipeline_run_id=run.id, assigned_printer_id=two.printer.id, queue_entry_id=entry, status="queued")
    )
    await db_session.commit()
    return run.id, archive.id, entry


async def _entry_status(db_session, entry: int) -> str:
    db_session.expire_all()
    return (await db_session.execute(select(PrintQueueItem.status).where(PrintQueueItem.id == entry))).scalar_one()


class TestPipelineRuns:
    async def test_another_users_run_names_nothing_of_theirs(
        self, async_client: AsyncClient, two, archive_factory, db_session
    ):
        run, archive, entry = await _run(db_session, archive_factory, two, two.second_id)

        theirs = (await async_client.get(f"/api/v1/pipeline-runs/{run}", headers=two.first)).json()
        listed = (await async_client.get("/api/v1/pipeline-runs", headers=two.first)).json()["runs"]
        own = (await async_client.get(f"/api/v1/pipeline-runs/{run}", headers=two.second)).json()

        for seen in (theirs, *listed):
            assert (seen["source_archive_id"], seen["source_filename"]) == (None, None)
            assert [j["queue_entry_id"] for j in seen["jobs"]] == [None]
        assert (own["source_archive_id"], own["source_filename"]) == (archive, "Their part")
        assert [j["queue_entry_id"] for j in own["jobs"]] == [entry]

    async def test_nor_a_library_file_of_theirs(self, async_client: AsyncClient, two, db_session):
        from backend.app.models.library import LibraryFile

        source = LibraryFile(
            filename="their.3mf",
            file_path="their.3mf",
            file_type="3mf",
            file_size=0,
            file_hash="h",
            source_type="uploaded",
            created_by_id=two.second_id,
        )
        db_session.add(source)
        await db_session.commit()
        run = PipelineRun(source_library_file_id=source.id, status="completed", copies=1, created_by=two.second_id)
        db_session.add(run)
        await db_session.commit()

        theirs = (await async_client.get(f"/api/v1/pipeline-runs/{run.id}", headers=two.first)).json()
        own = (await async_client.get(f"/api/v1/pipeline-runs/{run.id}", headers=two.second)).json()

        assert (theirs["source_library_file_id"], theirs["source_filename"]) == (None, None)
        assert (own["source_library_file_id"], own["source_filename"]) == (source.id, "their.3mf")

    async def test_only_its_starter_cancels_it(self, async_client: AsyncClient, two, archive_factory, db_session):
        run, _, entry = await _run(db_session, archive_factory, two, two.second_id)

        refused = await async_client.post(f"/api/v1/pipeline-runs/{run}/cancel", headers=two.first)
        assert refused.status_code == 403
        assert await _entry_status(db_session, entry) == "pending"

        cancelled = await async_client.post(f"/api/v1/pipeline-runs/{run}/cancel", headers=two.second)
        assert cancelled.status_code == 200, cancelled.text
        assert await _entry_status(db_session, entry) == "cancelled"

    async def test_or_someone_who_may_change_every_job(
        self, async_client: AsyncClient, two, archive_factory, db_session
    ):
        run, _, entry = await _run(db_session, archive_factory, two, two.second_id)

        cancelled = await async_client.post(f"/api/v1/pipeline-runs/{run}/cancel", headers=two.admin)

        assert cancelled.status_code == 200, cancelled.text
        assert await _entry_status(db_session, entry) == "cancelled"

    async def test_clearing_finished_runs_clears_only_ones_own(
        self, async_client: AsyncClient, two, archive_factory, db_session
    ):
        mine, *_ = await _run(db_session, archive_factory, two, two.first_id, status="completed")
        theirs, *_ = await _run(db_session, archive_factory, two, two.second_id, status="completed")

        cleared = await async_client.post("/api/v1/pipeline-runs/clear", headers=two.first)

        assert cleared.json() == {"deleted": 1}
        db_session.expire_all()
        assert list((await db_session.execute(select(PipelineRun.id))).scalars()) == [theirs]
        assert mine != theirs


async def _key(client: AsyncClient, headers: dict, printer_id: int) -> dict:
    created = await client.post(
        "/api/v1/api-keys/",
        headers=headers,
        json={"name": "k", "can_control_printer": True, "can_read_status": True, "printer_ids": [printer_id]},
    )
    assert created.status_code in (200, 201), created.text
    return {"X-API-Key": created.json()["key"]}


class TestWebhook:
    async def test_start_takes_the_next_job_its_owner_could_start(
        self, async_client: AsyncClient, two, archive_factory, db_session
    ):
        archive = await archive_factory(two.printer.id)
        await _job(db_session, two.printer.id, archive.id, two.second_id, position=1)
        mine = await _job(db_session, two.printer.id, archive.id, two.first_id, position=2)
        key = await _key(async_client, two.first, two.printer.id)

        started = await async_client.post(f"/api/v1/webhook/printer/{two.printer.id}/start", headers=key)

        assert started.status_code == 200, started.text
        assert started.json()["queue_item_id"] == mine

    async def test_a_job_nobody_owns_is_started_and_becomes_the_owners(
        self, async_client: AsyncClient, two, archive_factory, db_session
    ):
        archive = await archive_factory(two.printer.id)
        unowned = await _job(db_session, two.printer.id, archive.id, None, position=1)
        key = await _key(async_client, two.first, two.printer.id)

        started = await async_client.post(f"/api/v1/webhook/printer/{two.printer.id}/start", headers=key)

        assert started.json()["queue_item_id"] == unowned
        db_session.expire_all()
        owner = await db_session.scalar(select(PrintQueueItem.created_by_id).where(PrintQueueItem.id == unowned))
        assert owner == two.first_id

    async def test_and_none_when_only_others_wait(self, async_client: AsyncClient, two, archive_factory, db_session):
        archive = await archive_factory(two.printer.id)
        await _job(db_session, two.printer.id, archive.id, two.second_id)
        key = await _key(async_client, two.first, two.printer.id)

        started = await async_client.post(f"/api/v1/webhook/printer/{two.printer.id}/start", headers=key)

        assert started.status_code == 404

    async def test_queue_status_lists_only_the_owners_jobs(
        self, async_client: AsyncClient, two, archive_factory, db_session
    ):
        archive = await archive_factory(two.printer.id)
        await _job(db_session, two.printer.id, archive.id, two.second_id, position=1)
        mine = await _job(db_session, two.printer.id, archive.id, two.first_id, position=2)
        key = await _key(async_client, two.first, two.printer.id)

        (status,) = (await async_client.get("/api/v1/webhook/queue", headers=key)).json()

        assert status["pending"] == 2
        assert [item["id"] for item in status["items"]] == [mine]


class TestLiveArchiveEvents:
    @pytest.mark.parametrize("send", ["send_archive_created", "send_archive_updated"])
    async def test_an_event_names_only_the_printer(self, send):
        manager = websocket.ConnectionManager()
        socket = SimpleNamespace(state=SimpleNamespace(bambuddy_printer_scope=ALL_PRINTERS), send_text=AsyncMock())
        manager.active_connections = [socket]

        await getattr(manager, send)(
            {"id": 9, "printer_id": 1, "filename": "part.3mf", "print_name": "Part", "status": "printing", "cost": 2}
        )

        message = json.loads(socket.send_text.await_args.args[0])
        assert message["data"] == {"printer_id": 1}


def _user_socket(user_id: int | None):
    return SimpleNamespace(
        state=SimpleNamespace(bambuddy_printer_scope=ALL_PRINTERS, bambuddy_principal_user_id=user_id),
        send_text=AsyncMock(),
    )


class TestOutcomePrompt:
    async def test_who_may_answer_is_asked(self):
        manager = websocket.ConnectionManager()
        owner, other = _user_socket(7), _user_socket(8)
        manager.active_connections = [owner, other]

        await manager.send_print_confirm_request(1, {"archive_id": 9, "print_name": "Part"}, user_ids={7})

        owner.send_text.assert_awaited_once()
        other.send_text.assert_not_awaited()

    async def test_an_archive_nobody_owns_asks_everyone(self):
        manager = websocket.ConnectionManager()
        sockets = [_user_socket(7), _user_socket(8)]
        manager.active_connections = list(sockets)

        await manager.send_print_confirm_request(1, {"archive_id": 9, "print_name": "Part"})

        for socket in sockets:
            socket.send_text.assert_awaited_once()


class TestMakerWorldRecentImports:
    async def test_only_ones_own_imports_are_listed(self, async_client: AsyncClient, two, db_session):
        from backend.app.models.library import LibraryFile

        rows = {}
        for who, owner in (("first", two.first_id), ("second", two.second_id)):
            row = LibraryFile(
                filename=f"{who}.3mf",
                file_path=f"{who}.3mf",
                file_type="3mf",
                file_size=0,
                file_hash=who,
                source_type="makerworld",
                source_url=f"https://makerworld.com/models/{who}",
                created_by_id=owner,
            )
            db_session.add(row)
            await db_session.commit()
            rows[who] = row.id

        listed = await async_client.get("/api/v1/makerworld/recent-imports", headers=two.first)

        assert listed.status_code == 200, listed.text
        assert [r["library_file_id"] for r in listed.json()] == [rows["first"]]


class TestASecondCopyOnAMount:
    async def test_is_stored_beside_a_colleagues_under_a_free_name(self, db_session, tmp_path):
        from backend.app.api.routes.library import save_3mf_bytes_to_library
        from backend.app.models.library import LibraryFile, LibraryFolder

        mount = tmp_path / "nas"
        mount.mkdir()
        (mount / "benchy.stl").write_bytes(b"theirs")
        folder = LibraryFolder(name="NAS", is_external=True, external_path=str(mount), external_readonly=False)
        db_session.add(folder)
        await db_session.commit()
        db_session.add(
            LibraryFile(
                folder_id=folder.id,
                is_external=True,
                filename="benchy.stl",
                file_path=str(mount / "benchy.stl"),
                file_type="stl",
                file_size=6,
                file_hash="t",
                source_type="manyfold",
                source_url="manyfold:m1/f1",
            )
        )
        await db_session.commit()

        mine, was_existing = await save_3mf_bytes_to_library(
            db_session,
            file_bytes=b"solid x\nendsolid x\n",
            filename="benchy.stl",
            folder_id=folder.id,
            source_type="manyfold",
            source_url="manyfold:m1/f1",
            dedupe_within=None,
        )

        assert was_existing is False
        assert mine.filename == "benchy (2).stl"
        assert (mount / "benchy.stl").read_bytes() == b"theirs"
