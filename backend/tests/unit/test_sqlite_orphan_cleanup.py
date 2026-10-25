"""On SQLite, every start repairs rows left behind by earlier deletes.

The delete paths apply the ON DELETE rules now (core/sqlite_fk), but databases
already hold ids of archives and library files deleted before that (SQLite runs
with foreign keys off). Every start applies the rules to rows whose referenced
row is gone, which also catches a path that still leaves one behind. The rules
themselves are tested in test_sqlite_fk.py.
"""

import importlib
import pkgutil

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import init_db, run_migrations
from backend.app.core.sqlite_fk import sweep_orphans


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


@pytest.fixture
async def engine(tmp_path):
    import backend.app.models
    from backend.app.core.database import Base

    for module in pkgutil.iter_modules(backend.app.models.__path__):
        importlib.import_module(f"backend.app.models.{module.name}")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/orphans.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


async def test_orphaned_ids_are_cleared_and_valid_ones_kept(engine):
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, username, role, is_active, auth_source) VALUES (1, 'kept', 'user', 1, 'local')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO library_files (id, filename, file_path, file_type, file_size, print_count, is_external,"
                " variant_position) VALUES (1, 'kept.3mf', 'l/kept.3mf', '3mf', 1, 0, 0, 0)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO print_archives (id, filename, file_path, file_size, status, quantity, is_favorite,"
                " library_file_id, bambuddy_forced_timelapse, wallet_charge_skipped) VALUES"
                " (1, 'a.3mf', 'a/a.3mf', 1, 'completed', 1, 0, 1, 0, 0),"
                " (2, 'b.3mf', 'a/b.3mf', 1, 'completed', 1, 0, 999, 0, 0)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO print_log_entries (id, status, print_name, archive_id, created_by_id) VALUES"
                " (1, 'completed', 'a', 1, 1), (2, 'completed', 'zzz-no-archive-of-this-name', 999, NULL)"
            )
        )

    async with engine.begin() as conn:
        await run_migrations(conn)
        await sweep_orphans(conn)

    async with engine.connect() as conn:
        archives = dict((await conn.execute(text("SELECT id, library_file_id FROM print_archives"))).all())
        entries = {
            row[0]: row[1:]
            for row in (await conn.execute(text("SELECT id, archive_id, created_by_id FROM print_log_entries"))).all()
        }

    assert archives == {1: 1, 2: None}
    assert entries == {1: (1, 1), 2: (None, None)}


async def test_archives_older_than_the_search_index_are_indexed_once(engine):
    """An archive stored before archive_fts existed was never indexed, and
    updating it failed with "database disk image is malformed"."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO print_archives (id, filename, file_path, file_size, status, quantity, is_favorite,"
                " print_name, bambuddy_forced_timelapse, wallet_charge_skipped) VALUES"
                " (7, 'old.3mf', 'a/old.3mf', 1, 'completed', 1, 0, 'Gearbox', 0, 0)"
            )
        )

    async with engine.begin() as conn:
        await run_migrations(conn)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE print_archives SET notes = 'kept' WHERE id = 7"))
        found = (await conn.execute(text("SELECT rowid FROM archive_fts WHERE archive_fts MATCH 'Gearbox'"))).all()
        flags = (
            await conn.execute(text("SELECT COUNT(*) FROM settings WHERE key = '_archive_fts_rebuilt_once'"))
        ).scalar()
    async with engine.begin() as conn:
        await run_migrations(conn)

    assert found == [(7,)]
    assert flags == 1


def test_init_db_sweeps_after_the_migrations():
    """The sweep is a start-up repair, not a migration: init_db runs it."""
    import inspect

    source = inspect.getsource(init_db)
    assert source.index("_repair_sqlite_orphans(") > source.index("run_migrations(")


async def test_queue_jobs_of_a_vanished_library_file_follow_the_library_rule_not_the_cascade(engine):
    """print_queue.library_file_id is ON DELETE CASCADE in the model, but a library
    delete deliberately keeps the rows (release_queue_references): finished plates
    are what a batch counts its progress from, a printing job stays, a waiting one
    is cancelled. Rows orphaned by older versions are repaired the same way."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.core.database import _repair_sqlite_orphans
    from backend.app.models.print_queue import PrintQueueItem

    async with engine.begin() as conn:
        await run_migrations(conn)
    async with async_sessionmaker(engine)() as db:
        db.add_all(
            [
                PrintQueueItem(id=1, library_file_id=999, position=1, status="completed"),
                PrintQueueItem(id=2, library_file_id=999, position=2, status="printing"),
                PrintQueueItem(id=3, library_file_id=999, position=3, status="pending"),
            ]
        )
        await db.commit()

    async with engine.begin() as conn:
        await _repair_sqlite_orphans(conn)

    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT id, status, library_file_id FROM print_queue ORDER BY id"))).all()

    assert rows == [(1, "completed", None), (2, "printing", None), (3, "cancelled", None)]
