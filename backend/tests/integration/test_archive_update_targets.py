"""Editing an archive cannot move it to a printer or project the caller may not use.

The archive edit checked only the printer the archive was on. A member of a
group limited to printer A could move an archive of A to printer B, into the
history of a team they have no access to, and any caller could link an
archive to a printer or project that does not exist (stored dangling on
SQLite, a server error on PostgreSQL). Upload already checked the printer.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from backend.tests.integration.test_printer_scope_1727 import TEAM_PERMISSIONS, _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _member(client: AsyncClient, admin: str, name: str, printer_ids: list[int]) -> dict[str, str]:
    perms = await _group(client, admin, f"perms_{name}", permissions=[*TEAM_PERMISSIONS, "archives:update_all"])
    team = await _group(client, admin, f"team_{name}", printer_ids=printer_ids)
    return _auth((await _user(client, admin, name, [perms, team]))[0])


async def _printer_of(client: AsyncClient, headers: dict[str, str], archive_id: int) -> int | None:
    response = await client.get(f"/api/v1/archives/{archive_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["printer_id"]


async def test_a_member_cannot_move_an_archive_to_a_printer_outside_the_team(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(async_client)
    member = await _member(async_client, admin, "member", [a.id])
    other_team = await _member(async_client, admin, "other", [b.id])
    archive = await archive_factory(a.id, print_name="on-a")

    response = await async_client.patch(f"/api/v1/archives/{archive.id}", headers=member, json={"printer_id": b.id})

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Printer not found"
    assert await _printer_of(async_client, _auth(admin), archive.id) == a.id
    listed = await async_client.get("/api/v1/archives/", headers=other_team)
    assert [x["print_name"] for x in listed.json()] == []


async def test_a_member_can_move_an_archive_between_team_printers(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, a2 = await printer_factory(name="A"), await printer_factory(name="A2")
    admin = await _admin_token(async_client)
    member = await _member(async_client, admin, "member", [a.id, a2.id])
    archive = await archive_factory(a.id)

    response = await async_client.patch(f"/api/v1/archives/{archive.id}", headers=member, json={"printer_id": a2.id})

    assert response.status_code == 200, response.text
    assert await _printer_of(async_client, member, archive.id) == a2.id


async def test_a_member_can_still_clear_the_printer(async_client: AsyncClient, printer_factory, archive_factory):
    # As an upload without a printer: a printer-less archive is in every scope.
    a = await printer_factory(name="A")
    admin = await _admin_token(async_client)
    member = await _member(async_client, admin, "member", [a.id])
    archive = await archive_factory(a.id)

    response = await async_client.patch(f"/api/v1/archives/{archive.id}", headers=member, json={"printer_id": None})

    assert response.status_code == 200, response.text
    assert await _printer_of(async_client, member, archive.id) is None


async def test_an_admin_can_move_an_archive_to_any_printer(async_client: AsyncClient, printer_factory, archive_factory):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    archive = await archive_factory(a.id)

    response = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"printer_id": b.id})

    assert response.status_code == 200, response.text
    assert response.json()["printer_id"] == b.id


@pytest.mark.parametrize("printer_id", [0, 9999])
async def test_an_unknown_printer_is_refused(async_client: AsyncClient, printer_factory, archive_factory, printer_id):
    a = await printer_factory(name="A")
    archive = await archive_factory(a.id)

    response = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"printer_id": printer_id})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Printer not found"
    assert (await async_client.get(f"/api/v1/archives/{archive.id}")).json()["printer_id"] == a.id


@pytest.mark.parametrize("project_id", [0, 9999])
async def test_an_unknown_project_is_refused(async_client: AsyncClient, printer_factory, archive_factory, project_id):
    a = await printer_factory(name="A")
    archive = await archive_factory(a.id)

    response = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"project_id": project_id})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Project not found"
    assert (await async_client.get(f"/api/v1/archives/{archive.id}")).json()["project_id"] is None


async def test_a_known_project_can_be_set_and_cleared(async_client: AsyncClient, printer_factory, archive_factory):
    a = await printer_factory(name="A")
    archive = await archive_factory(a.id)
    project = (await async_client.post("/api/v1/projects/", json={"name": "P"})).json()

    linked = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"project_id": project["id"]})
    cleared = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"project_id": None})

    assert linked.status_code == 200, linked.text
    assert linked.json()["project_id"] == project["id"]
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["project_id"] is None
