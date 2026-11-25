"""A project shows each caller the archives and queue jobs they may see.

Projects are shared, but the archives and queue jobs in them belong to the
users who made them. The project's archive list, queue list and timeline show
the caller the rows the archives and the queue themselves would show them:
their own, unless they may see everyone's, on their printers. An API key sees
what its owner could. With auth off every row shows.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from backend.app.models.print_queue import PrintQueueItem
from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth
from backend.tests.integration.test_project_batch_scope import _archive_projects, _member, _project

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

BASE = ["printers:read", "api_keys:create", "projects:read", "projects:update"]
OWN = [*BASE, "archives:read_own", "archives:update_own", "queue:read_own", "library:read_own", "library:upload"]


async def _job(db_session, printer_id: int, archive_id: int, project_id: int, created_by_id: int | None) -> int:
    item = PrintQueueItem(
        printer_id=printer_id,
        archive_id=archive_id,
        project_id=project_id,
        created_by_id=created_by_id,
        status="pending",
    )
    db_session.add(item)
    await db_session.commit()
    return item.id


@pytest.fixture
async def shared(async_client: AsyncClient, printer_factory, archive_factory, db_session):
    """A project holding one archive and one job of each of two users, and an ownerless pair."""
    a = await printer_factory(name="A")
    admin = await _admin_token(async_client)
    first, first_id = await _member(async_client, admin, "first", None, OWN)
    second, second_id = await _member(async_client, admin, "second", None, OWN)
    project = await _project(async_client, admin)
    rows = {}
    for who, user_id in (("first", first_id), ("second", second_id), ("nobody", None)):
        archive = await archive_factory(
            a.id, created_by_id=user_id, project_id=project, print_name=f"{who} part", status="completed"
        )
        rows[who] = (archive.id, await _job(db_session, a.id, archive.id, project, user_id))
    return {"admin": _auth(admin), "first": first, "second": second, "project": project, "rows": rows}


async def _lists(client: AsyncClient, headers: dict, project: int):
    archives = await client.get(f"/api/v1/projects/{project}/archives", headers=headers)
    queue = await client.get(f"/api/v1/projects/{project}/queue", headers=headers)
    timeline = await client.get(f"/api/v1/projects/{project}/timeline", headers=headers)
    for response in (archives, queue, timeline):
        assert response.status_code == 200, response.text
    events = timeline.json()
    return (
        sorted(a["id"] for a in archives.json()),
        sorted(q["id"] for q in queue.json()),
        sorted(e["metadata"]["archive_id"] for e in events if "archive_id" in (e.get("metadata") or {})),
        sorted(e["metadata"]["queue_item_id"] for e in events if "queue_item_id" in (e.get("metadata") or {})),
    )


async def test_a_user_sees_only_their_own_rows(async_client: AsyncClient, shared):
    archive, job = shared["rows"]["first"]

    assert await _lists(async_client, shared["first"], shared["project"]) == ([archive], [job], [archive], [job])


async def test_so_does_the_other_one(async_client: AsyncClient, shared):
    archive, job = shared["rows"]["second"]

    assert await _lists(async_client, shared["second"], shared["project"]) == ([archive], [job], [archive], [job])


async def test_an_admin_sees_every_row(async_client: AsyncClient, shared):
    archives = sorted(a for a, _ in shared["rows"].values())
    jobs = sorted(j for _, j in shared["rows"].values())

    assert await _lists(async_client, shared["admin"], shared["project"]) == (archives, jobs, archives, jobs)


async def test_without_archive_or_queue_rights_nothing_shows(async_client: AsyncClient, admin_and_editor):
    headers, project = admin_and_editor

    assert await _lists(async_client, headers, project) == ([], [], [], [])


@pytest.fixture
async def admin_and_editor(async_client: AsyncClient, shared):
    admin = shared["admin"]["Authorization"].removeprefix("Bearer ")
    editor, _ = await _member(async_client, admin, "editor", None, BASE)
    return editor, shared["project"]


async def test_a_key_sees_what_its_owner_could(async_client: AsyncClient, shared):
    created = await async_client.post(
        "/api/v1/api-keys/", headers=shared["first"], json={"name": "k", "can_manage_projects": True}
    )
    assert created.status_code in (200, 201), created.text
    archive, job = shared["rows"]["first"]

    key = {"X-API-Key": created.json()["key"]}
    assert await _lists(async_client, key, shared["project"]) == ([archive], [job], [archive], [job])


async def test_a_user_cannot_unlink_anothers_archive(async_client: AsyncClient, shared, db_session):
    mine, _ = shared["rows"]["first"]
    theirs, _ = shared["rows"]["second"]

    response = await async_client.post(
        f"/api/v1/projects/{shared['project']}/remove-archives",
        headers=shared["first"],
        json={"archive_ids": [mine, theirs]},
    )

    assert response.json()["count"] == 1
    assert await _archive_projects(db_session, mine, theirs) == [None, shared["project"]]


async def test_auth_off_shows_every_row(async_client: AsyncClient, printer_factory, archive_factory, db_session):
    a = await printer_factory(name="A")
    project = (await async_client.post("/api/v1/projects/", json={"name": "P"})).json()["id"]
    first = await archive_factory(a.id, project_id=project, status="completed")
    second = await archive_factory(a.id, project_id=project, status="completed")
    jobs = sorted(
        [await _job(db_session, a.id, first.id, project, None), await _job(db_session, a.id, second.id, project, None)]
    )

    assert await _lists(async_client, {}, project) == (
        sorted([first.id, second.id]),
        jobs,
        sorted([first.id, second.id]),
        jobs,
    )


async def test_the_project_list_previews_only_visible_archives(async_client: AsyncClient, shared):
    response = await async_client.get("/api/v1/projects/", headers=shared["first"])

    assert response.status_code == 200, response.text
    (project,) = [p for p in response.json() if p["id"] == shared["project"]]
    assert [a["id"] for a in project["archives"]] == [shared["rows"]["first"][0]]


async def test_a_bom_names_only_visible_archives(async_client: AsyncClient, shared):
    project = shared["project"]
    mine, theirs = shared["rows"]["first"][0], shared["rows"]["second"][0]
    linked = await async_client.post(
        f"/api/v1/projects/{project}/bom", headers=shared["admin"], json={"name": "b", "archive_id": theirs}
    )
    assert linked.status_code == 200, linked.text
    item = linked.json()["id"]

    listed = await async_client.get(f"/api/v1/projects/{project}/bom", headers=shared["first"])
    relinked = await async_client.patch(
        f"/api/v1/projects/{project}/bom/{item}", headers=shared["first"], json={"archive_id": theirs}
    )
    created = await async_client.post(
        f"/api/v1/projects/{project}/bom", headers=shared["first"], json={"name": "c", "archive_id": theirs}
    )
    own = await async_client.post(
        f"/api/v1/projects/{project}/bom", headers=shared["first"], json={"name": "d", "archive_id": mine}
    )

    assert [(b["archive_id"], b["archive_name"]) for b in listed.json()] == [(None, None)]
    assert relinked.status_code == created.status_code == 400
    assert (own.json()["archive_id"], own.json()["archive_name"]) == (mine, "first part")


async def test_a_library_folder_links_only_visible_archives(
    async_client: AsyncClient, shared, archive_factory, db_session
):
    from datetime import datetime

    from backend.app.models.archive import PrintArchive

    mine, theirs = shared["rows"]["first"][0], shared["rows"]["second"][0]
    trashed = await db_session.get(PrintArchive, mine)
    folder = lambda archive_id: async_client.post(  # noqa: E731
        "/api/v1/library/folders/", headers=shared["first"], json={"name": f"f{archive_id}", "archive_id": archive_id}
    )

    other = await folder(theirs)
    own = await folder(mine)
    trashed.deleted_at = datetime.now()
    await db_session.commit()
    gone = await folder(mine)

    assert other.status_code == 404
    assert (own.json()["archive_id"], own.json()["archive_name"]) == (mine, "first part")
    assert gone.status_code == 404


async def test_a_key_of_a_whole_library_reader_still_sees_only_its_owners_archives(async_client: AsyncClient, shared):
    admin = shared["admin"]["Authorization"].removeprefix("Bearer ")
    reader, _ = await _member(
        async_client, admin, "reader", None, [*BASE, "archives:read_own", "library:read_all", "library:update_all"]
    )
    theirs = shared["rows"]["second"][0]
    folder = await async_client.post(
        "/api/v1/library/folders/", headers=shared["admin"], json={"name": "shared", "archive_id": theirs}
    )
    assert folder.status_code == 200, folder.text
    created = await async_client.post("/api/v1/api-keys/", headers=reader, json={"name": "k"})
    assert created.status_code in (200, 201), created.text
    key = {"X-API-Key": created.json()["key"]}

    listed = await async_client.get("/api/v1/library/folders/", headers=key)
    one = await async_client.get(f"/api/v1/library/folders/{folder.json()['id']}", headers=key)
    by_archive = await async_client.get(f"/api/v1/library/folders/by-archive/{theirs}", headers=key)
    bulk = await async_client.get("/api/v1/bulk/archive-folders", params={"ids": str(theirs)}, headers=key)
    relinked = await async_client.put(
        f"/api/v1/library/folders/{folder.json()['id']}", headers=key, json={"archive_id": theirs}
    )

    assert [(f["archive_id"], f["archive_name"]) for f in listed.json() if f["name"] == "shared"] == [(None, None)]
    assert (one.json()["archive_id"], one.json()["archive_name"]) == (None, None)
    assert by_archive.json() == []
    assert bulk.json() == {str(theirs): []}
    assert relinked.status_code == 404
