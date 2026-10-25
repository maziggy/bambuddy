"""Library folder links to archives keep to the caller's printers."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

PERMS = ["printers:read", "archives:read_all", "library:read_all", "library:upload", "library:update_all"]


async def _setup(client: AsyncClient, printer_factory, archive_factory):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    ours = await archive_factory(a.id, print_name="Our print")
    theirs = await archive_factory(b.id, print_name="Their print")
    token = await _admin_token(client)
    admin = _auth(token)
    perms = await _group(client, token, "perms", permissions=PERMS)
    team = await _group(client, token, "team", printer_ids=[a.id])
    member = _auth((await _user(client, token, "member", [perms, team]))[0])
    return ours.id, theirs.id, admin, member


def _link(folder: dict) -> tuple:
    return folder["archive_id"], folder["archive_name"]


async def test_a_member_links_folders_to_archives_of_their_printers_only(
    async_client: AsyncClient, printer_factory, archive_factory
):
    ours, theirs, admin, member = await _setup(async_client, printer_factory, archive_factory)
    folder = (await async_client.post("/api/v1/library/folders/", headers=member, json={"name": "f"})).json()

    created = await async_client.post(
        "/api/v1/library/folders/", headers=member, json={"name": "g", "archive_id": theirs}
    )
    missing = await async_client.post(
        "/api/v1/library/folders/", headers=member, json={"name": "g", "archive_id": 999_999}
    )
    updated = await async_client.put(
        f"/api/v1/library/folders/{folder['id']}", headers=member, json={"archive_id": theirs}
    )
    linked = await async_client.put(
        f"/api/v1/library/folders/{folder['id']}", headers=member, json={"archive_id": ours}
    )

    assert (
        (created.status_code, created.json())
        == (missing.status_code, missing.json())
        == (
            404,
            {"detail": "Archive not found"},
        )
    )
    assert updated.status_code == 404
    assert linked.status_code == 200
    assert _link(linked.json()) == (ours, "Our print")


async def test_a_member_does_not_see_links_to_archives_of_other_printers(
    async_client: AsyncClient, printer_factory, archive_factory
):
    ours, theirs, admin, member = await _setup(async_client, printer_factory, archive_factory)
    ids = {}
    for name, archive in (("ours", ours), ("theirs", theirs)):
        created = await async_client.post(
            "/api/v1/library/folders/", headers=admin, json={"name": name, "archive_id": archive, "shared": True}
        )
        assert created.status_code == 200, created.text
        ids[name] = created.json()["id"]
        await async_client.put(f"/api/v1/library/folders/{ids[name]}", headers=admin, json={"shared": True})

    listed = {f["name"]: _link(f) for f in (await async_client.get("/api/v1/library/folders/", headers=member)).json()}
    got = await async_client.get(f"/api/v1/library/folders/{ids['theirs']}", headers=member)
    renamed = await async_client.put(
        f"/api/v1/library/folders/{ids['theirs']}", headers=member, json={"name": "theirs2"}
    )
    by_theirs = await async_client.get(f"/api/v1/library/folders/by-archive/{theirs}", headers=member)
    by_ours = await async_client.get(f"/api/v1/library/folders/by-archive/{ours}", headers=member)
    as_admin = await async_client.get(f"/api/v1/library/folders/{ids['theirs']}", headers=admin)

    assert listed == {"ours": (ours, "Our print"), "theirs": (None, None)}
    assert _link(got.json()) == (None, None)
    assert _link(renamed.json()) == (None, None)
    assert by_theirs.json() == []
    assert [_link(f) for f in by_ours.json()] == [(ours, "Our print")]
    assert _link(as_admin.json()) == (theirs, "Their print")


async def test_linking_a_project_keeps_an_archive_link_the_member_cannot_see(
    async_client: AsyncClient, printer_factory, archive_factory
):
    ours, theirs, admin, member = await _setup(async_client, printer_factory, archive_factory)
    project = (await async_client.post("/api/v1/projects/", headers=admin, json={"name": "p"})).json()
    folders = {}
    for name, archive in (("ours", ours), ("theirs", theirs)):
        created = await async_client.post(
            "/api/v1/library/folders/", headers=admin, json={"name": name, "archive_id": archive}
        )
        folders[name] = created.json()["id"]
        await async_client.put(f"/api/v1/library/folders/{folders[name]}", headers=admin, json={"shared": True})

    for folder_id in folders.values():
        relinked = await async_client.put(
            f"/api/v1/library/folders/{folder_id}",
            headers=member,
            json={"project_id": project["id"], "archive_id": 0},
        )
        assert relinked.status_code == 200, relinked.text

    links = {
        name: (await async_client.get(f"/api/v1/library/folders/{folder_id}", headers=admin)).json()["archive_id"]
        for name, folder_id in folders.items()
    }
    assert links == {"ours": None, "theirs": theirs}
