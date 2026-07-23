"""Route tests for /scheduled-dryings (#2638)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


def _future_iso(hours: int = 2) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


@pytest.mark.asyncio
async def test_create_and_list(async_client, printer_factory):
    printer = await printer_factory()
    resp = await async_client.post(
        "/api/v1/scheduled-dryings",
        json={"printer_id": printer.id, "ams_id": 0, "temp": 65, "duration_hours": 8, "start_after": _future_iso()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["printer_id"] == printer.id

    listed = await async_client.get(f"/api/v1/scheduled-dryings?printer_id={printer.id}")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [body["id"]]


@pytest.mark.asyncio
async def test_create_rejects_past_start_after(async_client, printer_factory):
    printer = await printer_factory()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    resp = await async_client.post(
        "/api/v1/scheduled-dryings",
        json={"printer_id": printer.id, "temp": 65, "duration_hours": 8, "start_after": past},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_unknown_printer_404(async_client):
    resp = await async_client.post(
        "/api/v1/scheduled-dryings", json={"printer_id": 99999, "temp": 65, "duration_hours": 8}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_invalid_temp_422(async_client, printer_factory):
    printer = await printer_factory()
    resp = await async_client.post(
        "/api/v1/scheduled-dryings", json={"printer_id": printer.id, "temp": 90, "duration_hours": 8}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_cancel_pending(async_client, printer_factory):
    printer = await printer_factory()
    created = await async_client.post(
        "/api/v1/scheduled-dryings",
        json={"printer_id": printer.id, "temp": 65, "duration_hours": 8, "start_after": _future_iso()},
    )
    row_id = created.json()["id"]

    resp = await async_client.delete(f"/api/v1/scheduled-dryings/{row_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    # Cancelled rows disappear from the active list
    listed = await async_client.get(f"/api/v1/scheduled-dryings?printer_id={printer.id}")
    assert listed.json() == []

    # Second cancel is a 400 (not active any more)
    resp = await async_client.delete(f"/api/v1/scheduled-dryings/{row_id}")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_cancel_running_sends_stop_command(async_client, printer_factory, db_session):
    from backend.app.models.scheduled_drying import ScheduledDrying

    printer = await printer_factory()
    row = ScheduledDrying(printer_id=printer.id, ams_id=1, temp=65, duration_hours=8, status="running")
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    with patch("backend.app.api.routes.scheduled_dryings.printer_manager") as mock_pm:
        mock_pm.send_drying_command.return_value = True
        resp = await async_client.delete(f"/api/v1/scheduled-dryings/{row.id}")

    assert resp.status_code == 200
    mock_pm.send_drying_command.assert_called_once_with(printer.id, 1, 0, 0, mode=0)
