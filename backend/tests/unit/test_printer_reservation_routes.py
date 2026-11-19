"""Route tests for /printer-reservations (#2785)."""

from datetime import datetime, timedelta, timezone

import pytest

BASE = "/api/v1/printer-reservations"


def _body(printer_ids, **kw):
    body = {
        "label": "Project X",
        "printer_ids": printer_ids,
        "days_mask": 0b11111,
        "start_minute": 900,
        "end_minute": 300,
    }
    body.update(kw)
    return body


@pytest.mark.asyncio
async def test_create_list_update_delete(async_client, printer_factory):
    a = await printer_factory()
    b = await printer_factory()

    created = await async_client.post(BASE, json=_body([b.id, a.id, a.id]))
    assert created.status_code == 200, created.text
    row = created.json()
    assert row["printer_ids"] == sorted([a.id, b.id])
    assert row["label"] == "Project X"

    listed = await async_client.get(BASE)
    assert [r["id"] for r in listed.json()] == [row["id"]]

    updated = await async_client.put(f"{BASE}/{row['id']}", json=_body([a.id], label="Night run", all_day=True))
    assert updated.status_code == 200, updated.text
    assert updated.json()["printer_ids"] == [a.id]
    assert updated.json()["all_day"] is True

    deleted = await async_client.delete(f"{BASE}/{row['id']}")
    assert deleted.status_code == 200
    assert (await async_client.get(BASE)).json() == []


@pytest.mark.asyncio
async def test_validation(async_client, printer_factory):
    printer = await printer_factory()
    assert (await async_client.post(BASE, json=_body([]))).status_code == 422
    assert (await async_client.post(BASE, json=_body([printer.id], days_mask=0))).status_code == 422
    assert (await async_client.post(BASE, json=_body([printer.id], start_minute=1440))).status_code == 422
    assert (
        await async_client.post(BASE, json=_body([printer.id], valid_from="2026-12-02", valid_until="2026-12-01"))
    ).status_code == 422
    assert (await async_client.post(BASE, json=_body([99999]))).status_code == 400


@pytest.mark.asyncio
async def test_occurrences_cover_the_requested_range(async_client, printer_factory):
    printer = await printer_factory()
    await async_client.post(BASE, json=_body([printer.id], days_mask=0b1111111, all_day=True))
    start = datetime.now(timezone.utc).replace(microsecond=0)
    end = start + timedelta(days=3)
    resp = await async_client.get(f"{BASE}/occurrences", params={"start": start.isoformat(), "end": end.isoformat()})
    assert resp.status_code == 200, resp.text
    occ = resp.json()
    # Today's window (already running) plus three more days, give or take the
    # boundary landing exactly on midnight.
    assert 3 <= len(occ) <= 4
    assert all(o["printer_ids"] == [printer.id] for o in occ)
    assert all(o["start"].endswith("Z") for o in occ)


@pytest.mark.asyncio
async def test_occurrences_range_is_bounded(async_client):
    start = datetime.now(timezone.utc)
    too_long = await async_client.get(
        f"{BASE}/occurrences",
        params={"start": start.isoformat(), "end": (start + timedelta(days=90)).isoformat()},
    )
    assert too_long.status_code == 400
    backwards = await async_client.get(
        f"{BASE}/occurrences",
        params={"start": start.isoformat(), "end": (start - timedelta(days=1)).isoformat()},
    )
    assert backwards.status_code == 400


@pytest.mark.asyncio
async def test_deleting_a_printer_drops_it_from_reservations(async_client, printer_factory):
    a = await printer_factory()
    b = await printer_factory()
    shared = (await async_client.post(BASE, json=_body([a.id, b.id]))).json()
    only_a = (await async_client.post(BASE, json=_body([a.id]))).json()

    resp = await async_client.delete(f"/api/v1/printers/{a.id}")
    assert resp.status_code == 200, resp.text

    rows = {r["id"]: r for r in (await async_client.get(BASE)).json()}
    assert rows[shared["id"]]["printer_ids"] == [b.id]
    assert only_a["id"] not in rows
