"""Project cards and orders keep to the caller's printers.

Project cards previewed archives of any printer, and an order (batch) whose
jobs run on a printer outside the caller's groups was listed, shown and
editable, although cancelling or ungrouping it already treated it as missing.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from backend.app.models.print_batch import PrintBatch
from backend.app.models.print_queue import PrintQueueItem
from backend.tests.integration.test_printer_scope_1727 import TEAM_PERMISSIONS, _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _member(client: AsyncClient, printer_ids: list[int]) -> tuple[dict[str, str], str]:
    admin = await _admin_token(client)
    perms = await _group(
        client,
        admin,
        "perms",
        permissions=[*TEAM_PERMISSIONS, "projects:read", "queue:read_all", "queue:update_own", "queue:update_all"],
    )
    team = await _group(client, admin, "team", printer_ids=printer_ids)
    return _auth((await _user(client, admin, "member", [perms, team]))[0]), admin


async def _batch_on(db_session, archive_id: int, printer_id: int) -> int:
    batch = PrintBatch(name=f"order {printer_id}", archive_id=archive_id, quantity=1, status="active")
    db_session.add(batch)
    await db_session.flush()
    db_session.add(PrintQueueItem(printer_id=printer_id, archive_id=archive_id, batch_id=batch.id, status="pending"))
    await db_session.commit()
    return batch.id


async def test_project_cards_preview_only_the_team_printers_archives(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    member, admin = await _member(async_client, [a.id])
    project = (await async_client.post("/api/v1/projects/", headers=_auth(admin), json={"name": "P"})).json()["id"]
    on_a = await archive_factory(a.id, project_id=project, print_name="ours")
    await archive_factory(b.id, project_id=project, print_name="theirs")

    cards = (await async_client.get("/api/v1/projects/", headers=member)).json()

    previews = next(card for card in cards if card["id"] == project)["archives"]
    assert [p["id"] for p in previews] == [on_a.id]


async def test_an_order_with_jobs_on_another_printer_is_missing(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    member, _ = await _member(async_client, [a.id])
    ours = await _batch_on(db_session, (await archive_factory(a.id)).id, a.id)
    theirs = await _batch_on(db_session, (await archive_factory(b.id)).id, b.id)

    listed = await async_client.get("/api/v1/queue/batches", headers=member)
    detail = await async_client.get(f"/api/v1/queue/batches/{theirs}", headers=member)
    edited = await async_client.patch(f"/api/v1/queue/batches/{theirs}", headers=member, json={"name": "renamed"})
    own_edit = await async_client.patch(f"/api/v1/queue/batches/{ours}", headers=member, json={"name": "renamed"})

    assert [batch["id"] for batch in listed.json()] == [ours]
    assert (detail.status_code, edited.status_code) == (404, 404)
    assert own_edit.status_code == 200, own_edit.text


async def test_a_hidden_order_takes_no_new_jobs_or_dispatches(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    member, _ = await _member(async_client, [a.id])
    archive_on_a = await archive_factory(a.id)
    theirs = await _batch_on(db_session, (await archive_factory(b.id)).id, b.id)

    added = await async_client.post(
        "/api/v1/queue/", headers=member, json={"archive_id": archive_on_a.id, "printer_id": a.id, "batch_id": theirs}
    )
    dispatched = await async_client.post(f"/api/v1/queue/batches/{theirs}/dispatch", headers=member, json={})

    assert (added.status_code, dispatched.status_code) == (404, 404), (added.text, dispatched.text)


async def test_dispatching_a_plate_of_a_hidden_order_is_refused(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """Plate 1 runs on the caller's printer, plate 2 elsewhere: the order is hidden as a whole."""
    from backend.app.models.print_batch import PrintBatchPlate

    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    member, _ = await _member(async_client, [a.id])
    archive = await archive_factory(a.id)
    batch = PrintBatch(name="mixed order", archive_id=archive.id, quantity=2, status="active")
    db_session.add(batch)
    await db_session.flush()
    db_session.add_all(
        [
            PrintBatchPlate(batch_id=batch.id, plate_id=1, quantity_target=2),
            PrintBatchPlate(batch_id=batch.id, plate_id=2, quantity_target=1),
            PrintQueueItem(printer_id=a.id, archive_id=archive.id, batch_id=batch.id, plate_id=1, status="pending"),
            PrintQueueItem(printer_id=b.id, archive_id=archive.id, batch_id=batch.id, plate_id=2, status="pending"),
        ]
    )
    await db_session.commit()

    response = await async_client.post(
        f"/api/v1/queue/batches/{batch.id}/dispatch", headers=member, json={"plate_id": 1, "only_plate": True}
    )

    assert response.status_code == 404, response.text
