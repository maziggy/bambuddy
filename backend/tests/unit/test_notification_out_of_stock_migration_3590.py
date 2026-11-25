"""The on_filament_out_of_stock column reaches upgraded installs, default off (#3590)."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
import backend.app.models.external_link  # noqa: F401 - required by a legacy ALTER in run_migrations
import backend.app.models.print_log  # noqa: F401 - required by a legacy ALTER in run_migrations
import backend.app.models.virtual_printer  # noqa: F401 - required by a legacy ALTER in run_migrations
from backend.app.core.database import _PG_COLUMNS_SET_FROM_MODEL, Base, run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """run_migrations branches on the global dialect; same fixture as the other migration tests."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


async def _columns(conn) -> dict[str, tuple]:
    rows = (await conn.execute(text("PRAGMA table_info(notification_providers)"))).all()
    return {r[1]: r for r in rows}


@pytest.mark.asyncio
async def test_column_is_added_with_default_false_and_is_idempotent(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'oos.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE notification_providers DROP COLUMN on_filament_out_of_stock"))
            assert "on_filament_out_of_stock" not in await _columns(conn)
            await conn.execute(
                text(
                    "INSERT INTO notification_providers (name, provider_type, enabled, config) "
                    "VALUES ('old', 'ntfy', 1, '{}')"
                )
            )

            await run_migrations(conn)
            assert "on_filament_out_of_stock" in await _columns(conn)
            value = await conn.scalar(text("SELECT on_filament_out_of_stock FROM notification_providers"))
            assert value in (0, False)

            await run_migrations(conn)
            assert "on_filament_out_of_stock" in await _columns(conn)
    finally:
        await engine.dispose()


def test_postgres_boolean_is_reset_from_the_model():
    assert ("notification_providers", "on_filament_out_of_stock") in _PG_COLUMNS_SET_FROM_MODEL
