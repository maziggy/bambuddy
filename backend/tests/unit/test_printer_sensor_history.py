"""Tests for printer heater (nozzle / bed / chamber) sensor history."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.printer import Printer
from backend.app.models.printer_sensor_history import PrinterSensorHistory


@pytest.mark.asyncio
async def test_get_returns_per_sensor_series(async_client: AsyncClient, db_session: AsyncSession):
    """Each sensor_kind returns its own series with stats."""
    printer = Printer(name="X1C", serial_number="X1C-TEST-001", ip_address="10.0.0.10", access_code="12345678")
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)

    base = datetime.now(timezone.utc) - timedelta(hours=1)
    samples = [
        ("nozzle", 210.0, 220.0, 0),
        ("nozzle", 215.0, 220.0, 5),
        ("nozzle", 220.0, 220.0, 10),
        ("bed", 55.0, 60.0, 0),
        ("bed", 60.0, 60.0, 5),
        ("chamber", 38.0, 40.0, 0),
    ]
    for kind, value, target, minutes in samples:
        db_session.add(
            PrinterSensorHistory(
                printer_id=printer.id,
                sensor_kind=kind,
                value=value,
                target=target,
                recorded_at=base + timedelta(minutes=minutes),
            )
        )
    await db_session.commit()

    response = await async_client.get(f"/api/v1/printer-sensor-history/{printer.id}?hours=24")
    assert response.status_code == 200
    body = response.json()
    assert body["printer_id"] == printer.id
    series_by_kind = {s["sensor_kind"]: s for s in body["series"]}

    assert series_by_kind["nozzle"]["min_value"] == 210.0
    assert series_by_kind["nozzle"]["max_value"] == 220.0
    assert series_by_kind["nozzle"]["avg_value"] == pytest.approx(215.0, rel=0.01)
    assert len(series_by_kind["nozzle"]["data"]) == 3

    assert series_by_kind["bed"]["min_value"] == 55.0
    assert series_by_kind["bed"]["max_value"] == 60.0
    assert len(series_by_kind["bed"]["data"]) == 2

    assert series_by_kind["chamber"]["max_value"] == 38.0
    # nozzle_2 wasn't recorded — series present but empty
    assert series_by_kind["nozzle_2"]["data"] == []
    assert series_by_kind["nozzle_2"]["min_value"] is None


@pytest.mark.asyncio
async def test_get_filters_by_kinds_query(async_client: AsyncClient, db_session: AsyncSession):
    """`kinds=bed,chamber` only returns those series."""
    printer = Printer(name="X1C", serial_number="X1C-TEST-002", ip_address="10.0.0.11", access_code="12345678")
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)

    db_session.add(PrinterSensorHistory(printer_id=printer.id, sensor_kind="bed", value=60.0, target=60.0))
    await db_session.commit()

    response = await async_client.get(f"/api/v1/printer-sensor-history/{printer.id}?hours=24&kinds=bed,chamber")
    assert response.status_code == 200
    kinds_returned = {s["sensor_kind"] for s in response.json()["series"]}
    assert kinds_returned == {"bed", "chamber"}


@pytest.mark.asyncio
async def test_get_clamps_to_hours_window(async_client: AsyncClient, db_session: AsyncSession):
    """Rows older than the requested window are excluded."""
    printer = Printer(name="X1C", serial_number="X1C-TEST-003", ip_address="10.0.0.12", access_code="12345678")
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)

    now = datetime.now(timezone.utc)
    # One inside the window, one outside.
    db_session.add(
        PrinterSensorHistory(
            printer_id=printer.id,
            sensor_kind="bed",
            value=60.0,
            target=60.0,
            recorded_at=now - timedelta(minutes=30),
        )
    )
    db_session.add(
        PrinterSensorHistory(
            printer_id=printer.id,
            sensor_kind="bed",
            value=55.0,
            target=60.0,
            recorded_at=now - timedelta(hours=10),
        )
    )
    await db_session.commit()

    response = await async_client.get(f"/api/v1/printer-sensor-history/{printer.id}?hours=1")
    body = response.json()
    bed_series = next(s for s in body["series"] if s["sensor_kind"] == "bed")
    assert len(bed_series["data"]) == 1
    assert bed_series["data"][0]["value"] == 60.0


@pytest.mark.asyncio
async def test_delete_removes_old_rows(async_client: AsyncClient, db_session: AsyncSession):
    """DELETE removes rows older than `days` for the given printer only."""
    keep_printer = Printer(name="Keep", serial_number="KEEP-001", ip_address="10.0.0.20", access_code="12345678")
    other_printer = Printer(name="Other", serial_number="OTHER-001", ip_address="10.0.0.21", access_code="12345678")
    db_session.add_all([keep_printer, other_printer])
    await db_session.commit()
    await db_session.refresh(keep_printer)
    await db_session.refresh(other_printer)

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=40)
    db_session.add(PrinterSensorHistory(printer_id=keep_printer.id, sensor_kind="bed", value=60.0, recorded_at=old))
    db_session.add(PrinterSensorHistory(printer_id=keep_printer.id, sensor_kind="bed", value=60.0, recorded_at=now))
    db_session.add(PrinterSensorHistory(printer_id=other_printer.id, sensor_kind="bed", value=60.0, recorded_at=old))
    await db_session.commit()

    response = await async_client.delete(f"/api/v1/printer-sensor-history/{keep_printer.id}?days=30")
    assert response.status_code == 200
    assert response.json()["deleted"] == 1

    # other printer's old row untouched.
    rows = (await db_session.execute(select(PrinterSensorHistory))).scalars().all()
    kinds_left = sorted((r.printer_id, r.value) for r in rows)
    assert kinds_left == sorted([(keep_printer.id, 60.0), (other_printer.id, 60.0)])
