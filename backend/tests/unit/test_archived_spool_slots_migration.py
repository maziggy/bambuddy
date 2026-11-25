"""Slots still held by a spool archived before archiving released them are
released by the startup migration. Idempotent."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


@pytest.mark.asyncio
async def test_slots_of_archived_spools_are_released_and_others_kept():
    from datetime import datetime, timezone

    from sqlalchemy.ext.asyncio import AsyncSession

    import backend.app.models  # noqa: F401
    from backend.app.core.database import Base
    from backend.app.models import (  # noqa: F401
        external_link,
        location,
        print_log,
        print_queue,
        project_bom,
        slot_preset,
        spoolman_k_profile,
        spoolman_slot_assignment,
        virtual_printer,
    )
    from backend.app.models.printer import Printer
    from backend.app.models.spool import Spool
    from backend.app.models.spool_assignment import SpoolAssignment

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as db:
        printer = Printer(name="P", serial_number="S", ip_address="1.2.3.4", access_code="x")
        archived = Spool(material="PLA", label_weight=1000, archived_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        active = Spool(material="PLA", label_weight=1000)
        db.add_all([printer, archived, active])
        await db.flush()
        db.add(SpoolAssignment(spool_id=archived.id, printer_id=printer.id, ams_id=0, tray_id=0))
        db.add(SpoolAssignment(spool_id=active.id, printer_id=printer.id, ams_id=0, tray_id=1))
        active_id = active.id
        await db.commit()

    for _ in range(2):
        async with engine.begin() as conn:
            await run_migrations(conn)

    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT spool_id FROM spool_assignment"))).scalars().all()
    await engine.dispose()
    assert rows == [active_id]
