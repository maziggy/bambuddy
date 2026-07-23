"""Tests for the ScheduledDrying model (#2638)."""

import pytest

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
    assert row.rotate_tray is False
    assert row.filament == ""
    assert row.created_at is not None
    assert row.started_at is None


def test_printer_fk_declares_cascade_delete():
    """Verify the model declares CASCADE delete on printer FK (enforced in PostgreSQL)."""
    fk = next(iter(ScheduledDrying.__table__.c.printer_id.foreign_keys))
    assert fk.ondelete == "CASCADE"


def test_created_by_fk_declares_set_null():
    """Verify the model declares SET NULL on created_by FK (enforced in PostgreSQL)."""
    fk = next(iter(ScheduledDrying.__table__.c.created_by_id.foreign_keys))
    assert fk.ondelete == "SET NULL"
