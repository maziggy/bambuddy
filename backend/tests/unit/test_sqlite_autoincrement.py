"""SQLite never hands a freed id of these tables to a new row.

Without AUTOINCREMENT, SQLite gives the highest freed id to the next row
created, and whatever still pointed at the deleted row (a token, a socket,
in-memory state, a row inserted while the delete committed) then pointed at the
new one. users, api_keys, printers, print_archives and long_lived_tokens are
created with AUTOINCREMENT, and existing databases are rebuilt once.
"""

import re

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import AUTOINCREMENT_TABLES, Base, _migrate_sqlite_autoincrement, load_all_models

# SQLite behaviour: PostgreSQL sequences never hand an id out twice.
pytestmark = [pytest.mark.asyncio, pytest.mark.sqlite_only]


@pytest.fixture
async def engine(tmp_path):
    load_all_models()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


async def _sql(conn, name: str) -> str:
    return (await conn.execute(text("SELECT sql FROM sqlite_master WHERE name = :n"), {"n": name})).scalar_one()


async def _make_old_format(conn, table: str) -> None:
    """Rebuild a table the way installs before this change have it."""
    sql = await _sql(conn, table)
    old = sql.replace("id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,", "id INTEGER NOT NULL,")
    old = re.sub(r"\)\s*$", ", \n\tPRIMARY KEY (id)\n)", old)
    old = old.replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}__old", 1)
    indexes = [
        r[0]
        for r in await conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = :t AND sql IS NOT NULL"),
            {"t": table},
        )
    ]
    await conn.execute(text(old))
    await conn.execute(text(f"DROP TABLE {table}"))
    await conn.execute(text(f"ALTER TABLE {table}__old RENAME TO {table}"))
    for index in indexes:
        await conn.execute(text(index))
    assert "AUTOINCREMENT" not in await _sql(conn, table)


async def test_new_installs_create_the_tables_with_autoincrement(engine):
    async with engine.begin() as conn:
        for table in AUTOINCREMENT_TABLES:
            assert "AUTOINCREMENT" in await _sql(conn, table), table


async def test_an_existing_table_is_rebuilt_keeping_its_rows_columns_and_indexes(engine):
    async with engine.begin() as conn:
        await _make_old_format(conn, "users")
        for i in (1, 2, 3):
            await conn.execute(
                text(
                    "INSERT INTO users (id, username, role, auth_source, is_active, created_at, updated_at) "
                    "VALUES (:i, :n, 'user', 'local', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"i": i, "n": f"u{i}"},
            )
        columns_before = [r[1] for r in await conn.execute(text("PRAGMA table_info(users)"))]

        await _migrate_sqlite_autoincrement(conn)

        assert "AUTOINCREMENT" in await _sql(conn, "users")
        assert [r[1] for r in await conn.execute(text("PRAGMA table_info(users)"))] == columns_before
        assert [r[0] for r in await conn.execute(text("SELECT username FROM users ORDER BY id"))] == ["u1", "u2", "u3"]
        indexes = {
            r[0]
            for r in await conn.execute(
                text("SELECT name FROM sqlite_master WHERE tbl_name = 'users' AND type = 'index'")
            )
        }
        assert {"ix_users_username", "ix_users_email"} <= indexes


async def test_a_deleted_highest_id_is_not_given_out_again(engine):
    async def key(conn, name):
        await conn.execute(
            text(
                "INSERT INTO api_keys (name, key_hash, key_prefix, can_queue, can_control_printer, "
                "can_read_status, enabled, created_at) VALUES (:n, 'h', :n, 1, 0, 1, 1, CURRENT_TIMESTAMP)"
            ),
            {"n": name},
        )

    async with engine.begin() as conn:
        await _make_old_format(conn, "api_keys")
        await _migrate_sqlite_autoincrement(conn)
        await key(conn, "k1")
        await key(conn, "k2")
        await conn.execute(text("DELETE FROM api_keys WHERE name = 'k2'"))
        await key(conn, "k3")

        assert (await conn.execute(text("SELECT id FROM api_keys WHERE name = 'k3'"))).scalar_one() == 3


async def test_an_id_still_referenced_is_not_given_out(engine):
    """Deleted before the rebuild, but a row still points at it (an API key
    created while its user was deleted): the counter starts above it."""
    async with engine.begin() as conn:
        await _make_old_format(conn, "users")
        await conn.execute(
            text(
                "INSERT INTO users (id, username, role, auth_source, is_active, created_at, updated_at) "
                "VALUES (1, 'u1', 'user', 'local', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO api_keys (name, key_hash, key_prefix, user_id, can_queue, can_control_printer, "
                "can_read_status, enabled, created_at) VALUES ('k', 'h', 'bb_k', 7, 1, 0, 1, 1, CURRENT_TIMESTAMP)"
            )
        )

        await _migrate_sqlite_autoincrement(conn)
        await conn.execute(
            text(
                "INSERT INTO users (username, role, auth_source, is_active, created_at, updated_at) "
                "VALUES ('u2', 'user', 'local', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

        assert (await conn.execute(text("SELECT id FROM users WHERE username = 'u2'"))).scalar_one() == 8


async def test_running_it_again_changes_nothing(engine):
    async with engine.begin() as conn:
        await _make_old_format(conn, "api_keys")
        await _migrate_sqlite_autoincrement(conn)
        before = {t: await _sql(conn, t) for t in AUTOINCREMENT_TABLES}

        await _migrate_sqlite_autoincrement(conn)

        assert {t: await _sql(conn, t) for t in AUTOINCREMENT_TABLES} == before


async def test_a_printer_id_kept_by_the_print_log_is_not_given_out(engine):
    """The log keeps a deleted printer's id on purpose, with no foreign key: the
    next printer given it would show that history to whoever may see it."""
    async with engine.begin() as conn:
        await _make_old_format(conn, "printers")
        await conn.execute(
            text(
                "INSERT INTO print_log_entries (printer_id, print_name, status, created_at) "
                "VALUES (5, 'cube', 'completed', CURRENT_TIMESTAMP)"
            )
        )

        await _migrate_sqlite_autoincrement(conn)

        assert (await conn.execute(text("SELECT seq FROM sqlite_sequence WHERE name = 'printers'"))).scalar() == 5


async def test_every_id_column_without_a_foreign_key_counts_for_the_seed():
    """A new one must be listed, or ids it keeps could be given out at upgrade."""
    from sqlalchemy import Integer

    from backend.app.core.database import NON_FK_ID_REFERENCES

    metadata = load_all_models()
    listed = {pair for pairs in NON_FK_ID_REFERENCES.values() for pair in pairs}
    found = {
        (table.name, column.name)
        for table in metadata.tables.values()
        for column in table.columns
        if re.search(r"(printer_id|user_id|api_key_id|archive_id|token_id|_by_id)$", column.name)
        and isinstance(column.type, Integer)
        and not column.foreign_keys
        and not column.primary_key
    }
    assert found <= listed, found - listed


async def _keys(conn, ids):
    for i in ids:
        await conn.execute(
            text(
                "INSERT INTO api_keys (id, name, key_hash, key_prefix, can_queue, can_control_printer, "
                "can_read_status, enabled, created_at) VALUES (:i, :n, 'h', :n, 1, 0, 1, 1, CURRENT_TIMESTAMP)"
            ),
            {"i": i, "n": f"k{i}"},
        )


async def test_a_table_already_rebuilt_still_skips_ids_kept_elsewhere(engine):
    """A PostgreSQL backup restored here has the tables with AUTOINCREMENT, its
    counter at the highest id left: a token of a deleted key 7 is still there."""
    async with engine.begin() as conn:
        await _keys(conn, range(1, 7))
        await conn.execute(
            text(
                "INSERT INTO auth_ephemeral_tokens (token, token_type, username, api_key_id, expires_at, created_at) "
                "VALUES ('t', 'camera_stream', '', 7, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

        await _migrate_sqlite_autoincrement(conn)

        assert (await conn.execute(text("SELECT seq FROM sqlite_sequence WHERE name = 'api_keys'"))).scalar() == 7


async def test_the_counter_is_never_lowered(engine):
    async with engine.begin() as conn:
        await _keys(conn, [10])
        await conn.execute(text("DELETE FROM api_keys"))
        await _keys(conn, [1])

        await _migrate_sqlite_autoincrement(conn)

        assert (await conn.execute(text("SELECT seq FROM sqlite_sequence WHERE name = 'api_keys'"))).scalar() == 10
