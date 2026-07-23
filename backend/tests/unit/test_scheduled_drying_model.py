"""Tests for the ScheduledDrying model (#2638)."""

import pytest
from sqlalchemy import select

from backend.app.models.scheduled_drying import ScheduledDrying


@pytest.mark.asyncio
async def test_scheduled_drying_defaults(db_session, printer_factory):
    printer = await printer_factory()
    row = ScheduledDrying(printer_id=printer.id, ams_id=0, temp=65, duration_hours=8)
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    assert row.id is not None
    assert row.status == "pending"
    assert row.start_after is None
    assert row.wait_for_off_peak is False
    assert row.rotate_tray is False
    assert row.filament == ""
    assert row.created_at is not None
    assert row.started_at is None


@pytest.mark.asyncio
async def test_scheduled_drying_cascade_delete_with_printer(db_session, printer_factory):
    printer = await printer_factory()
    db_session.add(ScheduledDrying(printer_id=printer.id, ams_id=0, temp=45, duration_hours=4))
    await db_session.commit()

    await db_session.delete(printer)
    await db_session.commit()

    result = await db_session.execute(select(ScheduledDrying))
    assert result.scalars().all() == []
