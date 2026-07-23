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


@pytest.mark.asyncio
async def test_screen_only_model_rejected_at_schedule_time(async_client, printer_factory):
    """A P1S can never run a scheduled dry, so say so in the UI now."""
    printer = await printer_factory(model="P1S")
    resp = await async_client.post(
        "/api/v1/scheduled-dryings",
        json={"printer_id": printer.id, "temp": 65, "duration_hours": 8, "start_after": _future_iso()},
    )
    assert resp.status_code == 400
    assert "screen" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_offline_printer_is_still_schedulable(async_client, printer_factory):
    """Firmware is unreadable while offline and may be upgraded before the run."""
    printer = await printer_factory()
    resp = await async_client.post(
        "/api/v1/scheduled-dryings",
        json={"printer_id": printer.id, "temp": 65, "duration_hours": 8, "start_after": _future_iso()},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_stale_firmware_rejected_when_printer_is_online(async_client, printer_factory):
    printer = await printer_factory()
    state = type("S", (), {"firmware_version": "01.05.00.00", "raw_data": {}})()
    with patch("backend.app.api.routes.scheduled_dryings.printer_manager") as mock_pm:
        mock_pm.get_status.return_value = state
        resp = await async_client.post(
            "/api/v1/scheduled-dryings",
            json={"printer_id": printer.id, "temp": 65, "duration_hours": 8, "start_after": _future_iso()},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_timestamps_carry_z_suffix(async_client, printer_factory):
    """Matches every print_queue route, which the frontend parses the same way."""
    printer = await printer_factory()
    resp = await async_client.post(
        "/api/v1/scheduled-dryings",
        json={"printer_id": printer.id, "temp": 65, "duration_hours": 8, "start_after": _future_iso()},
    )
    body = resp.json()
    assert body["start_after"].endswith("Z")
    assert body["created_at"].endswith("Z")


@pytest.mark.asyncio
async def test_failed_rows_are_listed_and_dismissable(async_client, printer_factory, db_session):
    """A run that only fails at dispatch has to reach the client, not just the log."""
    from backend.app.models.scheduled_drying import ScheduledDrying

    printer = await printer_factory()
    row = ScheduledDrying(
        printer_id=printer.id,
        temp=65,
        duration_hours=8,
        status="failed",
        error_message="Drying not supported for this printer model or firmware version",
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    listed = await async_client.get(f"/api/v1/scheduled-dryings?printer_id={printer.id}")
    assert [r["id"] for r in listed.json()] == [row.id]
    assert listed.json()[0]["error_message"].startswith("Drying not supported")

    resp = await async_client.delete(f"/api/v1/scheduled-dryings/{row.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "dismissed"

    assert (await async_client.get(f"/api/v1/scheduled-dryings?printer_id={printer.id}")).json() == []
    # Dismissing twice is a 404, not a second dismiss: the row is gone.
    assert (await async_client.delete(f"/api/v1/scheduled-dryings/{row.id}")).status_code == 404


@pytest.mark.asyncio
async def test_list_orders_by_start_after_then_id(async_client, printer_factory):
    printer = await printer_factory()
    later = await async_client.post(
        "/api/v1/scheduled-dryings",
        json={"printer_id": printer.id, "temp": 65, "duration_hours": 8, "start_after": _future_iso(5)},
    )
    sooner = await async_client.post(
        "/api/v1/scheduled-dryings",
        json={"printer_id": printer.id, "temp": 65, "duration_hours": 8, "start_after": _future_iso(2)},
    )
    listed = await async_client.get(f"/api/v1/scheduled-dryings?printer_id={printer.id}")
    assert [r["id"] for r in listed.json()] == [sooner.json()["id"], later.json()["id"]]


@pytest.mark.asyncio
async def test_over_long_filament_422(async_client, printer_factory):
    printer = await printer_factory()
    resp = await async_client.post(
        "/api/v1/scheduled-dryings",
        json={"printer_id": printer.id, "temp": 65, "duration_hours": 8, "filament": "X" * 51},
    )
    assert resp.status_code == 422
