"""wallet_transactions.user_id becomes nullable, ON DELETE SET NULL, on existing SQLite databases.

It was NOT NULL with ON DELETE CASCADE: deleting a user deleted their ledger
entries, which freed what they had spent of a shared cost centre's budget.
The entries stay now, without a user. On SQLite the NOT NULL has to go from
tables made before, or the delete fails on it. (PostgreSQL is checked by
scripts/check_postgres_upgrade.py.)
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core import database

# As create_all made the table from the old model ...
CREATED_BY_THE_MODEL = """
CREATE TABLE wallet_transactions (
\tid INTEGER NOT NULL,
\tuser_id INTEGER NOT NULL,
\tcost_center_id INTEGER,
\ttransaction_type VARCHAR(40) NOT NULL,
\tamount NUMERIC(14, 2) NOT NULL,
\tcreated_by_user_id INTEGER,
\tPRIMARY KEY (id),
\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
\tFOREIGN KEY(cost_center_id) REFERENCES cost_centers (id) ON DELETE SET NULL,
\tFOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
)
"""

# ... and as the finance migration's own CREATE TABLE made it.
CREATED_BY_THE_MIGRATION = """
CREATE TABLE wallet_transactions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cost_center_id INTEGER REFERENCES cost_centers(id) ON DELETE SET NULL,
    transaction_type VARCHAR(40) NOT NULL,
    amount NUMERIC(14,2) NOT NULL,
    created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
)
"""


@pytest.fixture(autouse=True)
def sqlite_dialect(monkeypatch):
    monkeypatch.setattr(database, "is_sqlite", lambda: True)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [CREATED_BY_THE_MODEL, CREATED_BY_THE_MIGRATION], ids=["model", "migration"])
async def test_the_user_column_takes_null_and_says_set_null(legacy):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(legacy))
            await conn.execute(
                text(
                    "INSERT INTO wallet_transactions (id, user_id, transaction_type, amount) VALUES (1, 7, 'deposit', 5)"
                )
            )

            await database._migrate_wallet_transactions_keep_deleted_users(conn)
            await database._migrate_wallet_transactions_keep_deleted_users(conn)  # a restart changes nothing

            await conn.execute(text("UPDATE wallet_transactions SET user_id = NULL WHERE id = 1"))
            assert (await conn.execute(text("SELECT user_id, amount FROM wallet_transactions"))).one() == (None, 5)
            sql = (
                await conn.execute(text("SELECT sql FROM sqlite_master WHERE name = 'wallet_transactions'"))
            ).scalar_one()
            assert "CASCADE" not in sql
            # Only the user column changed.
            assert "transaction_type VARCHAR(40) NOT NULL" in sql
            assert sql.count("ON DELETE SET NULL") == 3
    finally:
        await engine.dispose()
