"""The startup migration gives a database from before #2915 the printers.dispatch_priority column."""

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


async def _columns(engine) -> list[str]:
    async with engine.connect() as conn:
        return [row[1] for row in (await conn.execute(text("PRAGMA table_info(printers)"))).all()]


@pytest.mark.asyncio
async def test_the_column_is_added_once_and_existing_printers_keep_no_priority():
    import importlib
    import pkgutil

    from sqlalchemy.ext.asyncio import AsyncSession

    import backend.app.models as models_pkg
    from backend.app.core.database import Base

    # Every table the migrations touch, not just those the package imports.
    for module in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"backend.app.models.{module.name}")
    from backend.app.models.printer import Printer

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as db:
        db.add(Printer(name="P", serial_number="S", ip_address="1.2.3.4", access_code="x"))
        await db.commit()
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE printers DROP COLUMN dispatch_priority"))
    assert "dispatch_priority" not in await _columns(engine)

    for _ in range(2):  # idempotent
        async with engine.begin() as conn:
            await run_migrations(conn)

    assert "dispatch_priority" in await _columns(engine)
    async with engine.connect() as conn:
        value = (await conn.execute(text("SELECT dispatch_priority FROM printers"))).scalar_one()
    await engine.dispose()
    assert value is None
