"""Jobs that wait for review (#1620).

A user without ``queue:start_unreviewed`` may still queue, but every job they
queue waits until someone with ``queue:update_all`` starts it. These tests pin
every way a job can be created or set going:

* POST /queue/, the library's add-to-queue, a batch dispatch, an API key and the
  webhook all leave such a user's job waiting, whatever the request asked for;
* the start button, clearing "wait for manual start" in the editor or the bulk
  editor, and the webhook start all refuse them, also for ownerless
  virtual-printer jobs a user with the permission may claim by starting;
* staff can start them, and users with the permission keep today's behaviour;
* the upgrade grants the permission once, so removing it from a group sticks.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core import database as _database_module
from backend.app.core.config import settings as app_settings
from backend.app.core.database import seed_default_groups
from backend.app.models.group import Group
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

# What a student needs to queue an archive and look after their own jobs
STUDENT = [
    "printers:read",
    "archives:read_all",
    "archives:reprint_all",
    "queue:read_own",
    "queue:create",
    "queue:update_own",
    "queue:delete_own",
    "api_keys:create",
]
STAFF = [*STUDENT, "queue:read_all", "queue:update_all", "queue:delete_all"]


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


async def _admin_token(async_client: AsyncClient) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "reviewadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "reviewadmin", "password": "AdminPass1!"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _user(async_client: AsyncClient, admin_jwt: str, username: str, permissions: list[str]) -> tuple[str, int]:
    group = await async_client.post(
        "/api/v1/groups/", headers=_auth(admin_jwt), json={"name": f"g_{username}", "permissions": permissions}
    )
    assert group.status_code == 201, group.text
    created = await async_client.post(
        "/api/v1/users/",
        headers=_auth(admin_jwt),
        json={"username": username, "password": "UserPass1!", "group_ids": [group.json()["id"]]},
    )
    assert created.status_code in (200, 201), created.text
    login = await async_client.post("/api/v1/auth/login", json={"username": username, "password": "UserPass1!"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"], created.json()["id"]


async def _queue(async_client: AsyncClient, headers: dict, printer_id: int, archive_id: int, **extra) -> dict:
    response = await async_client.post(
        "/api/v1/queue/", headers=headers, json={"printer_id": printer_id, "archive_id": archive_id, **extra}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _manual_start(item_id: int) -> bool:
    async with _database_module.async_session() as session:
        item = (await session.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()
        return item.manual_start


@pytest.fixture
async def setup(async_client, printer_factory, archive_factory):
    printer = await printer_factory(name="Lab")
    archive = await archive_factory(printer.id)
    admin = await _admin_token(async_client)
    student, student_id = await _user(async_client, admin, "student", STUDENT)
    staff, _ = await _user(async_client, admin, "staff", STAFF)
    trusted, _ = await _user(async_client, admin, "trusted", [*STUDENT, "queue:start_unreviewed"])
    return {
        "printer": printer,
        "archive": archive,
        "admin": admin,
        "student": student,
        "student_id": student_id,
        "staff": staff,
        "trusted": trusted,
    }


class TestQueueing:
    async def test_a_students_job_waits_whatever_they_asked_for(self, async_client, setup):
        item = await _queue(
            async_client, _auth(setup["student"]), setup["printer"].id, setup["archive"].id, manual_start=False
        )
        assert item["manual_start"] is True

    async def test_a_trusted_users_job_starts_on_its_own(self, async_client, setup):
        item = await _queue(async_client, _auth(setup["trusted"]), setup["printer"].id, setup["archive"].id)
        assert item["manual_start"] is False

    async def test_staff_jobs_do_not_wait_without_the_permission(self, async_client, setup):
        """Whoever may start every job is the reviewer; their own jobs don't wait."""
        item = await _queue(async_client, _auth(setup["staff"]), setup["printer"].id, setup["archive"].id)
        assert item["manual_start"] is False

    async def test_auth_off_changes_nothing(self, async_client, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        item = await _queue(async_client, {}, printer.id, archive.id)
        assert item["manual_start"] is False

    async def test_library_add_to_queue_waits(self, async_client, setup, db_session):
        from backend.app.models.library import LibraryFile

        rel_path = "archive/library/files/review_probe.gcode.3mf"
        abs_path = Path(app_settings.base_dir) / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(b"probe")
        try:
            lib_file = LibraryFile(
                filename="review_probe.gcode.3mf",
                file_path=rel_path,
                file_size=5,
                file_type="3mf",
                created_by_id=setup["student_id"],
            )
            db_session.add(lib_file)
            await db_session.commit()

            response = await async_client.post(
                "/api/v1/library/files/add-to-queue",
                headers=_auth(setup["student"]),
                json={"file_ids": [lib_file.id], "printer_id": setup["printer"].id},
            )
            assert response.status_code == 200, response.text
            added = response.json()["added"]
            assert len(added) == 1
            assert await _manual_start(added[0]["queue_item_id"]) is True
        finally:
            abs_path.unlink(missing_ok=True)

    async def test_dispatching_more_of_an_order_waits_again(self, async_client, setup):
        """The clones copy the template's flag, which is off once staff started it."""
        student = _auth(setup["student"])
        order = await async_client.post(
            "/api/v1/queue/batches",
            headers=student,
            json={
                "name": "Order",
                "archive_id": setup["archive"].id,
                "plates": [{"plate_id": 1, "quantity_target": 3}],
            },
        )
        assert order.status_code == 200, order.text
        first = await _queue(
            async_client, student, setup["printer"].id, setup["archive"].id, batch_id=order.json()["id"], plate_id=1
        )
        started = await async_client.post(f"/api/v1/queue/{first['id']}/start", headers=_auth(setup["staff"]))
        assert started.status_code == 200, started.text

        dispatched = await async_client.post(
            f"/api/v1/queue/batches/{order.json()['id']}/dispatch", headers=student, json={}
        )
        assert dispatched.status_code == 200, dispatched.text

        async with _database_module.async_session() as session:
            clones = (
                (
                    await session.execute(
                        select(PrintQueueItem).where(
                            PrintQueueItem.batch_id == order.json()["id"], PrintQueueItem.id != first["id"]
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(clones) == 2
        assert all(clone.manual_start for clone in clones)


class TestStarting:
    async def test_a_student_cannot_start_their_own_waiting_job(self, async_client, setup):
        item = await _queue(async_client, _auth(setup["student"]), setup["printer"].id, setup["archive"].id)

        response = await async_client.post(f"/api/v1/queue/{item['id']}/start", headers=_auth(setup["student"]))
        assert response.status_code == 403
        assert "review" in response.json()["detail"]
        assert await _manual_start(item["id"]) is True

    async def test_staff_start_it(self, async_client, setup):
        item = await _queue(async_client, _auth(setup["student"]), setup["printer"].id, setup["archive"].id)

        response = await async_client.post(f"/api/v1/queue/{item['id']}/start", headers=_auth(setup["staff"]))
        assert response.status_code == 200, response.text
        assert await _manual_start(item["id"]) is False

    async def test_a_trusted_user_still_starts_their_own_staged_job(self, async_client, setup):
        trusted = _auth(setup["trusted"])
        item = await _queue(async_client, trusted, setup["printer"].id, setup["archive"].id, manual_start=True)

        response = await async_client.post(f"/api/v1/queue/{item['id']}/start", headers=trusted)
        assert response.status_code == 200, response.text

    async def test_an_ownerless_job_is_not_claimable_by_a_student(self, async_client, setup):
        """Virtual-printer uploads arrive without an owner and are claimed by starting them (#1670)."""
        item = await _queue(async_client, _auth(setup["admin"]), setup["printer"].id, setup["archive"].id)
        async with _database_module.async_session() as session:
            row = (await session.execute(select(PrintQueueItem).where(PrintQueueItem.id == item["id"]))).scalar_one()
            row.created_by_id = None
            row.manual_start = True
            await session.commit()

        refused = await async_client.post(f"/api/v1/queue/{item['id']}/start", headers=_auth(setup["student"]))
        assert refused.status_code == 403
        claimed = await async_client.post(f"/api/v1/queue/{item['id']}/start", headers=_auth(setup["trusted"]))
        assert claimed.status_code == 200, claimed.text


class TestEditing:
    async def test_clearing_wait_in_the_editor_is_refused(self, async_client, setup):
        student = _auth(setup["student"])
        item = await _queue(async_client, student, setup["printer"].id, setup["archive"].id)

        response = await async_client.patch(
            f"/api/v1/queue/{item['id']}", headers=student, json={"manual_start": False}
        )
        assert response.status_code == 403
        assert await _manual_start(item["id"]) is True

    async def test_other_edits_still_work(self, async_client, setup):
        student = _auth(setup["student"])
        item = await _queue(async_client, student, setup["printer"].id, setup["archive"].id)

        response = await async_client.patch(
            f"/api/v1/queue/{item['id']}",
            headers=student,
            json={"manual_start": True, "require_previous_success": True},
        )
        assert response.status_code == 200, response.text

    async def test_staff_may_clear_it_in_the_editor(self, async_client, setup):
        item = await _queue(async_client, _auth(setup["student"]), setup["printer"].id, setup["archive"].id)

        response = await async_client.patch(
            f"/api/v1/queue/{item['id']}", headers=_auth(setup["staff"]), json={"manual_start": False}
        )
        assert response.status_code == 200, response.text
        assert await _manual_start(item["id"]) is False

    async def test_the_bulk_editor_skips_it(self, async_client, setup):
        student = _auth(setup["student"])
        item = await _queue(async_client, student, setup["printer"].id, setup["archive"].id)

        response = await async_client.patch(
            "/api/v1/queue/bulk", headers=student, json={"item_ids": [item["id"]], "manual_start": False}
        )
        assert response.status_code == 200, response.text
        assert response.json()["updated_count"] == 0
        assert await _manual_start(item["id"]) is True


class TestApiKeys:
    async def _key(self, async_client, jwt: str, **flags) -> dict[str, str]:
        created = await async_client.post(
            "/api/v1/api-keys/", headers=_auth(jwt), json={"name": "k", "can_queue": True, **flags}
        )
        assert created.status_code in (200, 201), created.text
        return {"X-API-Key": created.json()["key"]}

    async def test_a_students_key_queues_jobs_that_wait(self, async_client, setup):
        key = await self._key(async_client, setup["student"])
        item = await _queue(async_client, key, setup["printer"].id, setup["archive"].id)
        assert item["manual_start"] is True

    async def test_a_trusted_users_key_does_not(self, async_client, setup):
        key = await self._key(async_client, setup["trusted"])
        item = await _queue(async_client, key, setup["printer"].id, setup["archive"].id)
        assert item["manual_start"] is False

    async def test_webhook_queue_add_waits(self, async_client, setup):
        key = await self._key(async_client, setup["student"])
        response = await async_client.post(
            "/api/v1/webhook/queue/add",
            headers=key,
            json={"printer_id": setup["printer"].id, "archive_id": setup["archive"].id},
        )
        assert response.status_code == 200, response.text
        assert await _manual_start(response.json()["id"]) is True

    async def test_webhook_start_refuses_a_key_whose_owner_needs_review(self, async_client, setup):
        admin = setup["admin"]
        # Printer control, but their own jobs wait: they can't release anyone's
        controller, _ = await _user(async_client, admin, "controller", [*STUDENT, "printers:control"])
        item = await _queue(async_client, _auth(setup["student"]), setup["printer"].id, setup["archive"].id)
        key = await self._key(async_client, controller, can_control_printer=True)

        refused = await async_client.post(f"/api/v1/webhook/printer/{setup['printer'].id}/start", headers=key)
        assert refused.status_code == 403
        assert await _manual_start(item["id"]) is True

        staff_key = await self._key(async_client, admin, can_control_printer=True)
        started = await async_client.post(f"/api/v1/webhook/printer/{setup['printer'].id}/start", headers=staff_key)
        assert started.status_code == 200, started.text
        assert await _manual_start(item["id"]) is False


class TestUpgrade:
    async def test_granted_once_to_groups_that_could_print(self, async_client):
        async with _database_module.async_session() as session:
            session.add_all(
                [
                    Group(name="queuers", permissions=["queue:create"], is_system=False),
                    Group(name="controllers", permissions=["printers:control"], is_system=False),
                    # Could start their own staged jobs, or run pipelines, without queue:create
                    Group(name="starters", permissions=["queue:read_all", "queue:update_own"], is_system=False),
                    Group(name="pipeliners", permissions=["pipelines:run"], is_system=False),
                    Group(name="readers", permissions=["queue:read_own"], is_system=False),
                ]
            )
            flag = (
                await session.execute(
                    select(Settings).where(Settings.key == "_backfill_1620_queue_start_unreviewed_done")
                )
            ).scalar_one_or_none()
            # Seeding already ran once for this database; make it an upgrade again
            if flag is not None:
                await session.delete(flag)
            await session.commit()

        await seed_default_groups()

        async with _database_module.async_session() as session:
            groups = {g.name: g for g in (await session.execute(select(Group))).scalars().all()}
            assert "queue:start_unreviewed" in groups["queuers"].permissions
            assert "queue:start_unreviewed" in groups["controllers"].permissions
            assert "queue:start_unreviewed" in groups["starters"].permissions
            assert "queue:start_unreviewed" in groups["pipeliners"].permissions
            assert "queue:start_unreviewed" not in groups["readers"].permissions
            # An admin takes it away from the students...
            groups["queuers"].permissions = ["queue:create"]
            await session.commit()

        # ...and a restart doesn't hand it back
        await seed_default_groups()
        async with _database_module.async_session() as session:
            queuers = (await session.execute(select(Group).where(Group.name == "queuers"))).scalar_one()
            assert "queue:start_unreviewed" not in queuers.permissions
