"""A SQLite backup imported into PostgreSQL keeps ids freed on SQLite unused.

The import set each sequence to the highest id left, so the next printer on
PostgreSQL got the id of a deleted one, and with it whatever still pointed at
that id (its print log history, a Notify tile, a token). The sequence now
starts above SQLite's own counter and above the ids still referred to, and
installs imported before this are repaired at start.
"""

import re
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _source(path, *, deleted_highest: bool):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE printers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, serial_number TEXT, "
        "ip_address TEXT, access_code TEXT)"
    )
    for i in range(1, 6):
        conn.execute(
            "INSERT INTO printers (id, name, serial_number, ip_address, access_code) VALUES (?, ?, ?, '192.0.2.1', 'x')",
            (i, f"p{i}", f"S{i}"),
        )
    if deleted_highest:
        conn.execute("DELETE FROM printers WHERE id = 5")
    conn.commit()
    conn.close()


async def _import(path, pg_values: dict[str, int]) -> list[str]:
    """Run the import on a fake PostgreSQL that answers max() queries from pg_values."""
    from backend.app.api.routes import settings as settings_module

    executed: list[str] = []

    async def execute(stmt, *_a, **_k):
        sql = getattr(stmt, "text", str(stmt))
        executed.append(sql)
        value = None
        for pattern, v in pg_values.items():
            if re.search(pattern, sql, re.IGNORECASE):
                value = v
        result = MagicMock()
        result.scalar = MagicMock(return_value=value)
        return result

    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=execute)
    conn.run_sync = AsyncMock()
    begin = MagicMock()
    begin.__aenter__ = AsyncMock(return_value=conn)
    begin.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin = MagicMock(return_value=begin)
    engine.dispose = AsyncMock()
    with patch("backend.app.core.database._create_engine", new=MagicMock(return_value=engine)):
        await settings_module._import_sqlite_to_postgres(path)
    return executed


def _setval(executed: list[str], table: str) -> int | None:
    for sql in executed:
        m = re.search(rf"setval\('{table}_id_seq', (\d+)\)", sql)
        if m:
            return int(m.group(1))
    return None


async def test_an_id_freed_on_sqlite_is_not_given_out_on_postgres(tmp_path):
    path = tmp_path / "b.db"
    _source(path, deleted_highest=True)

    executed = await _import(path, {r"MAX\(id\) FROM printers": 4})

    assert _setval(executed, "printers") == 5


async def test_an_id_still_referred_to_is_not_given_out_on_postgres(tmp_path):
    """A backup from before the counter was kept: the print log still says 7."""
    path = tmp_path / "b.db"
    _source(path, deleted_highest=False)

    executed = await _import(path, {r"MAX\(id\) FROM printers": 5, r"max\(printer_id\) FROM print_log_entries": 7})

    assert _setval(executed, "printers") == 7


async def _start(pg: dict[str, tuple[int, bool]], referenced: dict[str, int]) -> list[tuple[str, int]]:
    """Run the start-up step on a fake PostgreSQL: pg maps a table to its
    sequence's (last_value, is_called), referenced to its highest id in use."""
    from backend.app.core import database

    raised: list[tuple[str, int]] = []

    async def execute(stmt, params=None, *_a, **_k):
        sql = getattr(stmt, "text", str(stmt))
        result = MagicMock()
        if "pg_get_serial_sequence" in sql:
            result.scalar = MagicMock(return_value=f"public.{params['t']}_id_seq")
        elif "last_value" in sql:
            table = re.search(r"public\.(\w+)_id_seq", sql).group(1)
            result.first = MagicMock(return_value=pg.get(table, (1, False)))
        elif "setval" in sql:
            raised.append((params["s"], params["v"]))
        return result

    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=execute)
    with (
        patch.object(database, "is_sqlite", return_value=False),
        patch.object(database, "highest_referenced_id", AsyncMock(side_effect=lambda _c, t: referenced.get(t, 0))),
    ):
        await database._raise_postgres_id_sequences(conn)
    return raised


async def test_an_install_imported_earlier_is_repaired_at_start():
    """Imported before the import kept them: the sequence stands at 4, the
    print log still says 5."""
    raised = await _start({"printers": (4, True)}, {"printers": 5})

    assert raised == [("public.printers_id_seq", 5)]


async def test_a_sequence_is_never_lowered():
    assert await _start({"printers": (9, True), "users": (3, False)}, {"printers": 5, "users": 2}) == []


async def test_a_sequence_not_yet_used_counts_from_its_start():
    """last_value 1, never called: id 1 is still to come, so 1 in use must raise it."""
    assert await _start({"users": (1, False)}, {"users": 1}) == [("public.users_id_seq", 1)]
