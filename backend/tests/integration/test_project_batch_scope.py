"""Adding archives or queue jobs to a project keeps to the caller's printers and rows.

The project batch routes (add-archives, remove-archives, add-queue) loaded
each row by id alone. A member of a group limited to printer A could link
another team's archives and jobs to a project, or unlink them, and the
"Added N" count told them which hidden ids exist. They also skipped the
ownership rule of the archive and queue edits, so an Operator could relink
other users' prints. Rows outside the caller's printers or ownership are now
skipped like missing ones. An API key changes what its owner could, on its
own printers.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.tests.integration.test_printer_scope_1727 import TEAM_PERMISSIONS, _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

EDITOR = [*TEAM_PERMISSIONS, "projects:read", "projects:update", "archives:update_all", "queue:update_all"]


async def _member(client: AsyncClient, admin: str, name: str, printer_ids: list[int] | None, permissions: list[str]):
    groups = [await _group(client, admin, f"perms_{name}", permissions=permissions)]
    if printer_ids is not None:
        groups.append(await _group(client, admin, f"team_{name}", printer_ids=printer_ids))
    jwt, user_id = await _user(client, admin, name, groups)
    return _auth(jwt), user_id


async def _project(client: AsyncClient, admin: str) -> int:
    response = await client.post("/api/v1/projects/", headers=_auth(admin), json={"name": "P"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def _archive_projects(db_session, *ids: int) -> list[int | None]:
    db_session.expire_all()
    rows = dict((await db_session.execute(select(PrintArchive.id, PrintArchive.project_id))).all())
    return [rows[i] for i in ids]


async def _queue_item(db_session, printer_id: int, archive_id: int, created_by_id: int | None = None) -> int:
    item = PrintQueueItem(printer_id=printer_id, archive_id=archive_id, created_by_id=created_by_id)
    db_session.add(item)
    await db_session.commit()
    return item.id


async def test_a_member_adds_only_archives_of_the_team_printers(
    async_client: AsyncClient, printer_factory, archive_factory, db_session
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(async_client)
    member, _ = await _member(async_client, admin, "member", [a.id], EDITOR)
    project = await _project(async_client, admin)
    on_a, on_b = await archive_factory(a.id), await archive_factory(b.id)

    response = await async_client.post(
        f"/api/v1/projects/{project}/add-archives", headers=member, json={"archive_ids": [on_a.id, on_b.id]}
    )

    assert response.status_code == 200, response.text
    assert response.json()["message"] == "Added 1 archives to project"
    assert await _archive_projects(db_session, on_a.id, on_b.id) == [project, None]


async def test_a_member_cannot_remove_archives_of_other_printers(
    async_client: AsyncClient, printer_factory, archive_factory, db_session
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(async_client)
    member, _ = await _member(async_client, admin, "member", [a.id], EDITOR)
    project = await _project(async_client, admin)
    on_a, on_b = await archive_factory(a.id), await archive_factory(b.id)
    added = await async_client.post(
        f"/api/v1/projects/{project}/add-archives", headers=_auth(admin), json={"archive_ids": [on_a.id, on_b.id]}
    )
    assert added.json()["message"] == "Added 2 archives to project"

    response = await async_client.post(
        f"/api/v1/projects/{project}/remove-archives", headers=member, json={"archive_ids": [on_a.id, on_b.id]}
    )

    assert response.json()["message"] == "Removed 1 archives from project"
    assert await _archive_projects(db_session, on_a.id, on_b.id) == [None, project]


async def test_a_member_adds_only_queue_jobs_of_the_team_printers(
    async_client: AsyncClient, printer_factory, archive_factory, db_session
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(async_client)
    member, _ = await _member(async_client, admin, "member", [a.id], EDITOR)
    project = await _project(async_client, admin)
    job_a = await _queue_item(db_session, a.id, (await archive_factory(a.id)).id)
    job_b = await _queue_item(db_session, b.id, (await archive_factory(b.id)).id)

    response = await async_client.post(
        f"/api/v1/projects/{project}/add-queue", headers=member, json={"queue_item_ids": [job_a, job_b]}
    )

    assert response.json()["message"] == "Added 1 queue items to project"
    db_session.expire_all()
    rows = dict((await db_session.execute(select(PrintQueueItem.id, PrintQueueItem.project_id))).all())
    assert [rows[job_a], rows[job_b]] == [project, None]


async def test_with_own_permissions_only_own_rows_are_linked(
    async_client: AsyncClient, printer_factory, archive_factory, db_session
):
    a = await printer_factory(name="A")
    admin = await _admin_token(async_client)
    own_perms = [*TEAM_PERMISSIONS, "projects:read", "projects:update", "archives:update_own", "queue:update_own"]
    operator, operator_id = await _member(async_client, admin, "operator", None, own_perms)
    project = await _project(async_client, admin)
    mine = await archive_factory(a.id, created_by_id=operator_id)
    theirs = await archive_factory(a.id)
    my_job = await _queue_item(db_session, a.id, mine.id, created_by_id=operator_id)
    their_job = await _queue_item(db_session, a.id, theirs.id)

    archives = await async_client.post(
        f"/api/v1/projects/{project}/add-archives", headers=operator, json={"archive_ids": [mine.id, theirs.id]}
    )
    jobs = await async_client.post(
        f"/api/v1/projects/{project}/add-queue", headers=operator, json={"queue_item_ids": [my_job, their_job]}
    )

    assert archives.json()["message"] == "Added 1 archives to project"
    assert jobs.json()["message"] == "Added 1 queue items to project"
    assert await _archive_projects(db_session, mine.id, theirs.id) == [project, None]


async def test_without_archive_permissions_nothing_is_linked(
    async_client: AsyncClient, printer_factory, archive_factory, db_session
):
    a = await printer_factory(name="A")
    admin = await _admin_token(async_client)
    editor, editor_id = await _member(
        async_client, admin, "editor", None, [*TEAM_PERMISSIONS, "projects:read", "projects:update"]
    )
    project = await _project(async_client, admin)
    own = await archive_factory(a.id, created_by_id=editor_id)

    response = await async_client.post(
        f"/api/v1/projects/{project}/add-archives", headers=editor, json={"archive_ids": [own.id]}
    )

    assert response.json()["message"] == "Added 0 archives to project"
    assert await _archive_projects(db_session, own.id) == [None]


async def test_a_project_key_links_archives_of_its_printers(
    async_client: AsyncClient, printer_factory, archive_factory, db_session
):
    # A key has no per-row owner: past its project gate it may link any archive
    # of its printers, as API keys did before (#1893).
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(async_client)
    project = await _project(async_client, admin)
    on_a, on_b = await archive_factory(a.id), await archive_factory(b.id)
    created = await async_client.post(
        "/api/v1/api-keys/",
        headers=_auth(admin),
        json={"name": "k", "can_manage_projects": True, "printer_ids": [a.id]},
    )
    assert created.status_code in (200, 201), created.text
    key = {"X-API-Key": created.json()["key"]}

    response = await async_client.post(
        f"/api/v1/projects/{project}/add-archives", headers=key, json={"archive_ids": [on_a.id, on_b.id]}
    )

    assert response.status_code == 200, response.text
    assert response.json()["message"] == "Added 1 archives to project"
    assert await _archive_projects(db_session, on_a.id, on_b.id) == [project, None]


async def test_auth_off_links_every_archive(async_client: AsyncClient, printer_factory, archive_factory, db_session):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    project = (await async_client.post("/api/v1/projects/", json={"name": "P"})).json()["id"]
    on_a, on_b = await archive_factory(a.id), await archive_factory(b.id)

    response = await async_client.post(
        f"/api/v1/projects/{project}/add-archives", json={"archive_ids": [on_a.id, on_b.id]}
    )

    assert response.json()["message"] == "Added 2 archives to project"


async def test_a_key_links_only_what_its_owner_could(
    async_client: AsyncClient, printer_factory, archive_factory, db_session
):
    # A key acts with its owner's rights: an owner who may change only their own
    # archives cannot relink everyone's through a project key.
    a = await printer_factory(name="A")
    admin = await _admin_token(async_client)
    own_perms = [*TEAM_PERMISSIONS, "projects:read", "projects:update", "archives:update_own"]
    operator, operator_id = await _member(async_client, admin, "operator", None, own_perms)
    project = await _project(async_client, admin)
    mine, theirs = await archive_factory(a.id, created_by_id=operator_id), await archive_factory(a.id)
    created = await async_client.post(
        "/api/v1/api-keys/", headers=operator, json={"name": "k", "can_manage_projects": True}
    )
    assert created.status_code in (200, 201), created.text
    key = {"X-API-Key": created.json()["key"]}

    response = await async_client.post(
        f"/api/v1/projects/{project}/add-archives", headers=key, json={"archive_ids": [mine.id, theirs.id]}
    )

    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    assert await _archive_projects(db_session, mine.id, theirs.id) == [project, None]
