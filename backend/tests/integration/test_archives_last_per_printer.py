"""GET /archives/last-per-printer -- the printer cards' "last print" in one request.

The cards used to ask the full listing once per printer, with the arguments in
the wrong order, so every answer was empty. The endpoint replaces those calls
and must hand each printer its newest live archive, nothing hidden from the
caller, and nothing another printer's card could mistake for its own.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

URL = "/api/v1/archives/last-per-printer"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


async def _admin_token(async_client: AsyncClient) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "lastadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "lastadmin", "password": "AdminPass1!"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _user(
    async_client: AsyncClient,
    admin_jwt: str,
    username: str,
    permissions: list[str],
    printer_ids: list[int] | None = None,
) -> tuple[str, int]:
    group_ids = []
    perms = await async_client.post(
        "/api/v1/groups/", headers=_auth(admin_jwt), json={"name": f"perms_{username}", "permissions": permissions}
    )
    assert perms.status_code == 201, perms.text
    group_ids.append(perms.json()["id"])
    if printer_ids is not None:
        team = await async_client.post(
            "/api/v1/groups/",
            headers=_auth(admin_jwt),
            json={"name": f"team_{username}", "permissions": [], "restrict_printers": True, "printer_ids": printer_ids},
        )
        assert team.status_code == 201, team.text
        group_ids.append(team.json()["id"])
    created = await async_client.post(
        "/api/v1/users/",
        headers=_auth(admin_jwt),
        json={"username": username, "password": "UserPass1!", "group_ids": group_ids},
    )
    assert created.status_code in (200, 201), created.text
    login = await async_client.post("/api/v1/auth/login", json={"username": username, "password": "UserPass1!"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"], created.json()["id"]


async def test_newest_archive_of_each_printer(async_client, printer_factory, archive_factory):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    await archive_factory(a.id, print_name="A old", created_at=T0)
    await archive_factory(a.id, print_name="A new", created_at=T0 + timedelta(hours=2))
    await archive_factory(a.id, print_name="A middle", created_at=T0 + timedelta(hours=1))
    await archive_factory(b.id, print_name="B only", created_at=T0)

    response = await async_client.get(URL)

    assert response.status_code == 200, response.text
    assert {row["printer_id"]: row["print_name"] for row in response.json()} == {a.id: "A new", b.id: "B only"}
    assert len(response.json()) == 2


async def test_reprint_of_an_older_archive_counts_as_last(async_client, printer_factory, archive_factory):
    # A reprint reuses the archive row and only sets a fresh started_at, so the
    # row's age says nothing about which print ran last.
    printer = await printer_factory()
    await archive_factory(printer.id, print_name="first", created_at=T0, started_at=T0 + timedelta(hours=5))
    await archive_factory(
        printer.id, print_name="second", created_at=T0 + timedelta(hours=1), started_at=T0 + timedelta(hours=1)
    )

    response = await async_client.get(URL)

    assert response.status_code == 200, response.text
    assert [row["print_name"] for row in response.json()] == ["first"]


async def test_archive_never_started_ranks_by_creation(async_client, printer_factory, archive_factory):
    printer = await printer_factory()
    await archive_factory(printer.id, print_name="started", created_at=T0, started_at=T0)
    await archive_factory(printer.id, print_name="never started", created_at=T0 + timedelta(hours=1))

    response = await async_client.get(URL)

    assert response.status_code == 200, response.text
    assert [row["print_name"] for row in response.json()] == ["never started"]


async def test_skips_soft_deleted_and_printerless_archives(async_client, printer_factory, archive_factory):
    printer = await printer_factory()
    await archive_factory(printer.id, print_name="kept", created_at=T0)
    await archive_factory(printer.id, print_name="deleted", created_at=T0 + timedelta(hours=1), deleted_at=T0)
    await archive_factory(None, print_name="no printer", created_at=T0 + timedelta(hours=2))

    response = await async_client.get(URL)

    assert response.status_code == 200, response.text
    assert [(row["printer_id"], row["print_name"]) for row in response.json()] == [(printer.id, "kept")]


async def test_printer_without_archives_is_absent(async_client, printer_factory):
    await printer_factory()

    response = await async_client.get(URL)

    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_team_member_gets_only_team_printers(async_client, printer_factory, archive_factory):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    await archive_factory(a.id, print_name="team print", created_at=T0)
    await archive_factory(b.id, print_name="hidden print", created_at=T0)
    admin = await _admin_token(async_client)
    jwt, _ = await _user(async_client, admin, "member", ["archives:read_all"], printer_ids=[a.id])

    response = await async_client.get(URL, headers=_auth(jwt))

    assert response.status_code == 200, response.text
    assert [row["print_name"] for row in response.json()] == ["team print"]


async def test_read_own_gets_its_newest_own_archive(async_client, printer_factory, archive_factory):
    printer = await printer_factory()
    admin = await _admin_token(async_client)
    jwt, user_id = await _user(async_client, admin, "owner", ["archives:read_own"])
    _, other_id = await _user(async_client, admin, "other", ["archives:read_own"])
    await archive_factory(printer.id, print_name="mine", created_by_id=user_id, created_at=T0)
    await archive_factory(printer.id, print_name="theirs", created_by_id=other_id, created_at=T0 + timedelta(hours=1))

    response = await async_client.get(URL, headers=_auth(jwt))

    assert response.status_code == 200, response.text
    assert [row["print_name"] for row in response.json()] == ["mine"]


async def test_needs_archive_read_permission(async_client, printer_factory, archive_factory):
    printer = await printer_factory()
    await archive_factory(printer.id, created_at=T0)
    admin = await _admin_token(async_client)
    jwt, _ = await _user(async_client, admin, "noarchives", ["printers:read"])

    response = await async_client.get(URL, headers=_auth(jwt))

    assert response.status_code == 403, response.text
