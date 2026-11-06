"""Bulk archive actions keep to the caller's printers.

Recalculating costs went over every archive, so a member of a group limited
to some printers rewrote other printers' archives.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.tests.integration.test_printer_scope_1727 import TEAM_PERMISSIONS, _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _member(client: AsyncClient, printer_ids: list[int]) -> dict[str, str]:
    admin = await _admin_token(client)
    perms = await _group(client, admin, "perms", permissions=[*TEAM_PERMISSIONS, "archives:update_all"])
    team = await _group(client, admin, "team", printer_ids=printer_ids)
    return _auth((await _user(client, admin, "member", [perms, team]))[0])


async def test_recalculating_costs_leaves_other_printers_alone(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    on_a = await archive_factory(a.id, cost=99.0, filament_used_grams=100, filament_type="PLA")
    on_b = await archive_factory(b.id, cost=99.0, filament_used_grams=100, filament_type="PLA")
    ids = (on_a.id, on_b.id)
    member = await _member(async_client, [a.id])

    response = await async_client.post("/api/v1/archives/recalculate-costs", headers=member)

    assert response.status_code == 200, response.text
    db_session.expire_all()
    costs = dict(
        (await db_session.execute(select(PrintArchive.id, PrintArchive.cost).where(PrintArchive.id.in_(ids)))).all()
    )
    assert costs[ids[0]] == 2.5  # 100 g at the default 25.00 per kg
    assert costs[ids[1]] == 99.0


async def test_tags_keep_to_the_team_printers(async_client: AsyncClient, db_session, printer_factory, archive_factory):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    on_a = await archive_factory(a.id, tags="shared, alpha")
    on_b = await archive_factory(b.id, tags="shared, beta")
    ids = (on_a.id, on_b.id)
    member = await _member(async_client, [a.id])

    listed = await async_client.get("/api/v1/archives/tags", headers=member)
    renamed = await async_client.put("/api/v1/archives/tags/shared", headers=member, json={"new_name": "common"})
    deleted = await async_client.delete("/api/v1/archives/tags/common", headers=member)

    assert sorted(t["name"] for t in listed.json()) == ["alpha", "shared"]
    assert (renamed.json()["affected"], deleted.json()["affected"]) == (1, 1)
    db_session.expire_all()
    tags = dict(
        (await db_session.execute(select(PrintArchive.id, PrintArchive.tags).where(PrintArchive.id.in_(ids)))).all()
    )
    assert tags[ids[1]] == "shared, beta"


async def test_duplicates_and_similar_show_only_visible_archives(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    mine = await archive_factory(a.id, print_name="Benchy", content_hash="h1")
    same_team = await archive_factory(a.id, print_name="Benchy", content_hash="h1")
    await archive_factory(b.id, print_name="Benchy", content_hash="h1")
    member = await _member(async_client, [a.id])

    duplicates = await async_client.get(f"/api/v1/archives/{mine.id}/duplicates", headers=member)
    detail = await async_client.get(f"/api/v1/archives/{mine.id}", headers=member)
    similar = await async_client.get(f"/api/v1/archives/{mine.id}/similar", headers=member)

    assert [d["id"] for d in duplicates.json()["duplicates"]] == [same_team.id]
    assert [d["id"] for d in detail.json()["duplicates"]] == [same_team.id]
    assert {s["archive"]["id"] for s in similar.json()} == {same_team.id}


async def test_backfill_hashes_reports_only_the_team_printers_archives(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    on_a = await archive_factory(a.id, file_path="archives/none-a.3mf", content_hash=None)
    await archive_factory(b.id, file_path="archives/none-b.3mf", content_hash=None)
    member = await _member(async_client, [a.id])

    response = await async_client.post("/api/v1/archives/backfill-hashes", headers=member)

    assert [e["id"] for e in response.json()["errors"]] == [on_a.id]


async def test_uploading_a_source_by_name_matches_only_the_team_printers(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    await archive_factory(b.id, print_name="Secret Part")
    member = await _member(async_client, [a.id])

    response = await async_client.post(
        "/api/v1/archives/upload-source",
        headers=member,
        params={"print_name": "Secret"},
        files={"file": ("Secret Part.3mf", b"PK\x03\x04", "application/octet-stream")},
    )

    assert response.status_code == 404, response.text
    assert "Secret Part" not in response.text


async def test_the_list_counts_duplicates_among_visible_archives_only(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    await archive_factory(b.id, print_name="Benchy", content_hash="h2")  # older, on B
    mine = await archive_factory(a.id, print_name="Benchy", content_hash="h2")
    member = await _member(async_client, [a.id])

    listed = {row["id"]: row for row in (await async_client.get("/api/v1/archives/", headers=member)).json()}

    assert listed[mine.id]["duplicate_count"] in (0, None)
    assert listed[mine.id]["original_archive_id"] is None


async def test_the_no_3mf_warning_ignores_other_printers(async_client: AsyncClient, printer_factory, archive_factory):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    await archive_factory(b.id, extra_data={"no_3mf_available": True, "no_3mf_reason": "internal_storage"})
    member = await _member(async_client, [a.id])

    response = await async_client.get("/api/v1/archives/no-3mf-warning", headers=member)

    assert response.json() == {"has_fallback": False, "reason": None}
