"""Migration tests for the supplier tables (#2988).

A database that predates the feature must gain both tables on upgrade, and
re-running the migration must be a no-op (CREATE TABLE IF NOT EXISTS via
_safe_execute).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    from backend.app.core import database as database_module

    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


def _register_all_models():
    import backend.app.models  # noqa: F401
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


@pytest.fixture
async def engine_without_supplier_tables():
    """create_all builds the current schema; dropping the tables reproduces a
    database from a Bambuddy version that predates #2988."""
    from backend.app.core.database import Base

    _register_all_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP TABLE spoolman_spool_suppliers"))
        await conn.execute(text("DROP TABLE spool_suppliers"))
        await conn.execute(text("DROP TABLE suppliers"))
    yield engine
    await engine.dispose()


@pytest.fixture
async def engine_with_pre_fix_suppliers():
    """A database written by an earlier build of this branch (#2988).

    The supplier tables are there, ``suppliers`` has no ``name_key`` column,
    and nothing stopped two rows whose names differ only in case -- which is
    exactly the state a unique index cannot be built over.
    """
    from backend.app.core.database import Base

    _register_all_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP TABLE suppliers"))
        await conn.execute(
            text(
                "CREATE TABLE suppliers ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " name VARCHAR(200) NOT NULL,"
                " website VARCHAR(500),"
                " customer_number VARCHAR(100),"
                " note VARCHAR(500),"
                " created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                " updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
            )
        )
    yield engine
    await engine.dispose()


async def test_migration_creates_supplier_tables(engine_without_supplier_tables):
    async with engine_without_supplier_tables.begin() as conn:
        await run_migrations(conn)

    async with engine_without_supplier_tables.begin() as conn:
        await conn.execute(text("INSERT INTO suppliers (name) VALUES ('Supplier A')"))
        await conn.execute(
            text(
                """
                INSERT INTO spool (material, label_weight, core_weight, weight_used, weight_used_baseline, weight_locked)
                VALUES ('PLA', 1000, 250, 0, 0, 0)
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO spool_suppliers (spool_id, supplier_id, quoted_price_per_kg, is_purchase_source)
                SELECT s.id, sup.id, 19.99, 1 FROM spool s, suppliers sup
                """
            )
        )
        # Spoolman twin (#2988 parity): local row keyed by the remote spool id.
        await conn.execute(
            text(
                """
                INSERT INTO spoolman_spool_suppliers (spoolman_spool_id, supplier_id, is_purchase_source)
                SELECT 7, sup.id, 1 FROM suppliers sup
                """
            )
        )

    async with engine_without_supplier_tables.connect() as conn:
        links = (await conn.execute(text("SELECT supplier_id, is_purchase_source FROM spool_suppliers"))).all()
        twin_links = (
            await conn.execute(text("SELECT spoolman_spool_id, supplier_id FROM spoolman_spool_suppliers"))
        ).all()
    assert len(links) == 1
    assert len(twin_links) == 1


async def test_migration_is_idempotent(engine_without_supplier_tables):
    async with engine_without_supplier_tables.begin() as conn:
        await run_migrations(conn)
    async with engine_without_supplier_tables.begin() as conn:
        await conn.execute(text("INSERT INTO suppliers (name) VALUES ('Kept')"))
    async with engine_without_supplier_tables.begin() as conn:
        await run_migrations(conn)

    async with engine_without_supplier_tables.connect() as conn:
        names = (await conn.execute(text("SELECT name FROM suppliers"))).scalars().all()
    # Existing rows survive the re-run — the CREATE is swallowed, not applied.
    assert names == ["Kept"]


async def test_migration_enforces_case_insensitive_unique_names(engine_without_supplier_tables):
    """The name is what the CSV import resolves against (#2988), so an
    upgraded database gets the same unique index create_all gives a fresh one.

    Every spelling goes in with the key the application computes, which is the
    point of storing it: the fold is the Python one, so the umlauted variant
    is refused too. A unique index on ``lower(name)`` let that one through,
    because SQLite's ``lower()`` folds ASCII only.
    """
    from sqlalchemy.exc import IntegrityError

    from backend.app.models.supplier import supplier_name_key

    async def _insert(conn, name: str) -> None:
        await conn.execute(
            text("INSERT INTO suppliers (name, name_key) VALUES (:n, :k)"),
            {"n": name, "k": supplier_name_key(name)},
        )

    async with engine_without_supplier_tables.begin() as conn:
        await run_migrations(conn)

    async with engine_without_supplier_tables.begin() as conn:
        await _insert(conn, "Extrudr")
        await _insert(conn, "Ökofilament")

    for variant in ("extrudr", "  eXtRuDr ", "ökofilament"):
        with pytest.raises(IntegrityError):
            async with engine_without_supplier_tables.begin() as conn:
                await _insert(conn, variant)


async def test_migration_collapses_duplicates_instead_of_aborting(engine_with_pre_fix_suppliers):
    """An upgrade over rows this branch itself allowed must not abort startup.

    CREATE UNIQUE INDEX refuses to build over the duplicates and _safe_execute
    re-raises that IntegrityError out of run_migrations, so without the
    collapse Bambuddy never finishes starting — and every migration queued
    after this one is skipped with it (#2988).

    Collapsed by merging, not deleting: a supplier is referenced. The oldest
    row wins — it is the one assignments and the import already resolved to —
    keeps its own spelling, takes over the assignments and fills its empty
    fields from the duplicate.
    """
    async with engine_with_pre_fix_suppliers.begin() as conn:
        await conn.execute(text("INSERT INTO suppliers (id, name) VALUES (1, 'Extrudr')"))
        await conn.execute(
            text("INSERT INTO suppliers (id, name, website) VALUES (2, 'extrudr', 'https://extrudr.example')")
        )
        await conn.execute(
            text(
                "INSERT INTO spool_suppliers (spool_id, supplier_id, supplier_article_number, is_purchase_source)"
                " VALUES (5, 2, 'EX-42', 1)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO spoolman_spool_suppliers (spoolman_spool_id, supplier_id, is_purchase_source) VALUES (7, 2, 0)"
            )
        )

    async with engine_with_pre_fix_suppliers.begin() as conn:
        await run_migrations(conn)

    async with engine_with_pre_fix_suppliers.connect() as conn:
        suppliers = (await conn.execute(text("SELECT id, name, name_key, website FROM suppliers"))).all()
        links = (
            await conn.execute(text("SELECT spool_id, supplier_id, supplier_article_number FROM spool_suppliers"))
        ).all()
        twins = (await conn.execute(text("SELECT spoolman_spool_id, supplier_id FROM spoolman_spool_suppliers"))).all()

    assert suppliers == [(1, "Extrudr", "extrudr", "https://extrudr.example")]
    assert links == [(5, 1, "EX-42")]
    assert twins == [(7, 1)]


async def test_migration_collapses_non_ascii_case_variants(engine_with_pre_fix_suppliers):
    """These two are in the database precisely because the SQL fold is ASCII
    only: an index on lower(name) never saw them as the same name (#2988)."""
    async with engine_with_pre_fix_suppliers.begin() as conn:
        await conn.execute(text("INSERT INTO suppliers (id, name) VALUES (1, :n)"), {"n": "Ökofilament"})
        await conn.execute(text("INSERT INTO suppliers (id, name) VALUES (2, :n)"), {"n": "ökofilament"})

    async with engine_with_pre_fix_suppliers.begin() as conn:
        await run_migrations(conn)

    async with engine_with_pre_fix_suppliers.connect() as conn:
        rows = (await conn.execute(text("SELECT name, name_key FROM suppliers"))).all()
    assert rows == [("Ökofilament", "ökofilament")]


async def test_merge_drops_an_assignment_the_surviving_row_already_has(engine_with_pre_fix_suppliers):
    """(spool, supplier) is unique, so a spool assigned to BOTH duplicates
    cannot have both rows re-pointed — the survivor's own row stays."""
    async with engine_with_pre_fix_suppliers.begin() as conn:
        await conn.execute(text("INSERT INTO suppliers (id, name) VALUES (1, 'Extrudr'), (2, 'EXTRUDR')"))
        await conn.execute(
            text(
                "INSERT INTO spool_suppliers (spool_id, supplier_id, supplier_article_number, is_purchase_source)"
                " VALUES (5, 1, 'KEPT', 0), (5, 2, 'DROPPED', 0), (6, 2, 'MOVED', 0)"
            )
        )

    async with engine_with_pre_fix_suppliers.begin() as conn:
        await run_migrations(conn)

    async with engine_with_pre_fix_suppliers.connect() as conn:
        links = (
            await conn.execute(
                text("SELECT spool_id, supplier_id, supplier_article_number FROM spool_suppliers ORDER BY spool_id")
            )
        ).all()
    assert links == [(5, 1, "KEPT"), (6, 1, "MOVED")]
