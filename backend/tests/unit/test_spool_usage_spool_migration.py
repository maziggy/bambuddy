"""spool_usage_history.spool_id becomes nullable, ON DELETE SET NULL, on existing SQLite databases.

Deleting a spool must keep its usage (the costs its prints are recalculated
from), so the column has to take NULL on tables made before. (PostgreSQL is
checked by scripts/check_postgres_upgrade.py.)
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core import database

# As create_all made the table from the old model ...
CREATED_BY_THE_MODEL = """
CREATE TABLE spool_usage_history (
\tid INTEGER NOT NULL,
\tspool_id INTEGER NOT NULL,
\tprinter_id INTEGER,
\tprint_name VARCHAR(500),
\tarchive_id INTEGER,
\tweight_used FLOAT NOT NULL,
\tPRIMARY KEY (id),
\tFOREIGN KEY(spool_id) REFERENCES spool (id) ON DELETE CASCADE,
\tFOREIGN KEY(printer_id) REFERENCES printers (id) ON DELETE SET NULL,
\tFOREIGN KEY(archive_id) REFERENCES print_archives (id) ON DELETE SET NULL
)
"""

# ... and as the usage-history migration's own CREATE TABLE made it.
CREATED_BY_THE_MIGRATION = """
CREATE TABLE spool_usage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spool_id INTEGER NOT NULL REFERENCES spool(id) ON DELETE CASCADE,
    printer_id INTEGER REFERENCES printers(id) ON DELETE SET NULL,
    print_name VARCHAR(500),
    weight_used REAL NOT NULL DEFAULT 0
)
"""


@pytest.fixture(autouse=True)
def sqlite_dialect(monkeypatch):
    monkeypatch.setattr(database, "is_sqlite", lambda: True)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [CREATED_BY_THE_MODEL, CREATED_BY_THE_MIGRATION], ids=["model", "migration"])
async def test_the_spool_column_takes_null_and_says_set_null(legacy):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(legacy))
            await conn.execute(text("INSERT INTO spool_usage_history (id, spool_id, weight_used) VALUES (1, 7, 5)"))

            await database._migrate_spool_usage_outlives_its_spool(conn)
            await database._migrate_spool_usage_outlives_its_spool(conn)  # a restart changes nothing

            await conn.execute(text("UPDATE spool_usage_history SET spool_id = NULL WHERE id = 1"))
            assert (await conn.execute(text("SELECT spool_id, weight_used FROM spool_usage_history"))).one() == (
                None,
                5,
            )
            sql = (
                await conn.execute(text("SELECT sql FROM sqlite_master WHERE name = 'spool_usage_history'"))
            ).scalar_one()
            assert "CASCADE" not in sql
            # Only the spool column changed.
            assert "weight_used" in sql and "NOT NULL" in sql.split("weight_used", 1)[1].split(",", 1)[0]
    finally:
        await engine.dispose()
