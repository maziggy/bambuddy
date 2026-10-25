"""A BOM line links only to an archive the caller can see, and shows no other.

The BOM routes stored any archive id and answered with that archive's print
name, looked up by id alone. A member of a group limited to some printers
could read the name of any archive (404 when asked for directly) by linking a
BOM line to it, and every reader of the project saw the names of archives
from printers they have no access to. An unknown id was stored as well (a
server error on PostgreSQL).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from backend.tests.integration.test_printer_scope_1727 import TEAM_PERMISSIONS, _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _member(client: AsyncClient, admin: str, printer_ids: list[int]) -> dict[str, str]:
    perms = await _group(client, admin, "perms", permissions=[*TEAM_PERMISSIONS, "projects:read", "projects:update"])
    team = await _group(client, admin, "team", printer_ids=printer_ids)
    return _auth((await _user(client, admin, "member", [perms, team]))[0])


async def _project(client: AsyncClient, headers: dict[str, str] | None = None) -> int:
    response = await client.post("/api/v1/projects/", headers=headers or {}, json={"name": "P"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def test_a_member_cannot_link_an_archive_of_another_printer(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(async_client)
    member = await _member(async_client, admin, [a.id])
    project = await _project(async_client, _auth(admin))
    hidden = await archive_factory(b.id, print_name="team-b")

    created = await async_client.post(
        f"/api/v1/projects/{project}/bom", headers=member, json={"name": "x", "archive_id": hidden.id}
    )
    line = await async_client.post(f"/api/v1/projects/{project}/bom", headers=member, json={"name": "y"})
    updated = await async_client.patch(
        f"/api/v1/projects/{project}/bom/{line.json()['id']}", headers=member, json={"archive_id": hidden.id}
    )

    assert created.status_code == 400, created.text
    assert created.json()["detail"] == "Archive not found"
    assert updated.status_code == 400, updated.text
    assert updated.json()["detail"] == "Archive not found"
    listed = (await async_client.get(f"/api/v1/projects/{project}/bom", headers=_auth(admin))).json()
    assert [(i["name"], i["archive_id"]) for i in listed] == [("y", None)]


async def test_a_hidden_archive_linked_by_an_admin_reads_as_missing(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(async_client)
    member = await _member(async_client, admin, [a.id])
    project = await _project(async_client, _auth(admin))
    hidden, visible = await archive_factory(b.id, print_name="team-b"), await archive_factory(a.id, print_name="ours")
    for name, archive in (("hidden", hidden), ("visible", visible)):
        response = await async_client.post(
            f"/api/v1/projects/{project}/bom", headers=_auth(admin), json={"name": name, "archive_id": archive.id}
        )
        assert response.json()["archive_name"] == archive.print_name

    as_member = (await async_client.get(f"/api/v1/projects/{project}/bom", headers=member)).json()
    as_admin = (await async_client.get(f"/api/v1/projects/{project}/bom", headers=_auth(admin))).json()

    assert [(i["name"], i["archive_id"], i["archive_name"]) for i in as_member] == [
        ("hidden", None, None),
        ("visible", visible.id, "ours"),
    ]
    assert [i["archive_name"] for i in as_admin] == ["team-b", "ours"]


async def test_editing_a_line_shows_no_hidden_archive(async_client: AsyncClient, printer_factory, archive_factory):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(async_client)
    member = await _member(async_client, admin, [a.id])
    project = await _project(async_client, _auth(admin))
    hidden = await archive_factory(b.id, print_name="team-b")
    line = await async_client.post(
        f"/api/v1/projects/{project}/bom", headers=_auth(admin), json={"name": "x", "archive_id": hidden.id}
    )

    response = await async_client.patch(
        f"/api/v1/projects/{project}/bom/{line.json()['id']}", headers=member, json={"quantity_acquired": 1}
    )

    assert response.status_code == 200, response.text
    assert (response.json()["archive_id"], response.json()["archive_name"]) == (None, None)
    # The link itself stays: the member only does not see it.
    as_admin = (await async_client.get(f"/api/v1/projects/{project}/bom", headers=_auth(admin))).json()
    assert as_admin[0]["archive_id"] == hidden.id


@pytest.mark.parametrize("archive_id", [9999])
async def test_an_unknown_archive_is_refused(async_client: AsyncClient, archive_id):
    project = await _project(async_client)

    response = await async_client.post(f"/api/v1/projects/{project}/bom", json={"name": "x", "archive_id": archive_id})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Archive not found"


async def test_archive_0_means_no_archive(async_client: AsyncClient):
    project = await _project(async_client)

    response = await async_client.post(f"/api/v1/projects/{project}/bom", json={"name": "x", "archive_id": 0})

    assert response.status_code == 200, response.text
    assert response.json()["archive_id"] is None


async def test_a_visible_archive_can_be_linked_and_cleared(async_client: AsyncClient, printer_factory, archive_factory):
    a = await printer_factory(name="A")
    project = await _project(async_client)
    archive = await archive_factory(a.id, print_name="ours")

    created = await async_client.post(f"/api/v1/projects/{project}/bom", json={"name": "x", "archive_id": archive.id})
    cleared = await async_client.patch(f"/api/v1/projects/{project}/bom/{created.json()['id']}", json={"archive_id": 0})

    assert (created.json()["archive_id"], created.json()["archive_name"]) == (archive.id, "ours")
    assert (cleared.json()["archive_id"], cleared.json()["archive_name"]) == (None, None)


async def test_resending_a_hidden_link_answers_as_a_missing_archive(
    async_client: AsyncClient, printer_factory, archive_factory
):
    # Otherwise 200 for the hidden current id against 400 for any other id would
    # tell the member which archive the line links to.
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(async_client)
    member = await _member(async_client, admin, [a.id])
    project = await _project(async_client, _auth(admin))
    hidden = await archive_factory(b.id)
    line = await async_client.post(
        f"/api/v1/projects/{project}/bom", headers=_auth(admin), json={"name": "x", "archive_id": hidden.id}
    )
    url = f"/api/v1/projects/{project}/bom/{line.json()['id']}"

    same = await async_client.patch(url, headers=member, json={"archive_id": hidden.id})
    other = await async_client.patch(url, headers=member, json={"archive_id": 9999})

    assert (
        (same.status_code, same.json()) == (other.status_code, other.json()) == (400, {"detail": "Archive not found"})
    )
