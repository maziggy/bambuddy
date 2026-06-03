"""Regression test for the library_files.file_type backfill migration (#1600).

Pre-#1600 the upload, ZIP-extract, and in-process ingest paths all stored
`file_type='3mf'` for sliced `.gcode.3mf` outputs while the external-folder
scan stored `file_type='gcode.3mf'` — the same on-disk file family split
across two values depending on how it was ingested. `classify_file_type`
is now canonical going forward; this migration backfills the legacy `3mf`
rows so the DB ends up consistent. Idempotent and dialect-neutral.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import run_migrations


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """Force the SQLite branch regardless of test env settings."""
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    from backend.app.core import database as database_module

    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


def _register_all_models():
    from backend.app.models import (  # noqa: F401
        ams_history,
        ams_label,
        api_key,
        archive,
        color_catalog,
        external_link,
        filament,
        group,
        kprofile_note,
        library,
        maintenance,
        notification,
        notification_template,
        print_log,
        print_queue,
        printer,
        project,
        project_bom,
        settings,
        slot_preset,
        smart_plug,
        smart_plug_energy_snapshot,
        spool,
        spool_assignment,
        spool_catalog,
        spool_k_profile,
        spool_usage_history,
        spoolbuddy_device,
        user,
        user_email_pref,
        virtual_printer,
    )


@pytest.fixture
async def engine():
    from backend.app.core.database import Base

    _register_all_models()

    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _insert_file(conn, *, file_id: int, filename: str, file_type: str) -> None:
    """Insert a minimal LibraryFile row; only the columns the migration
    touches matter."""
    await conn.execute(
        text(
            "INSERT INTO library_files "
            "(id, filename, file_path, file_type, file_size, is_external, print_count) "
            "VALUES (:id, :filename, :path, :ftype, 0, 0, 0)"
        ),
        {
            "id": file_id,
            "filename": filename,
            "path": f"/lib/{file_id}",
            "ftype": file_type,
        },
    )


@pytest.mark.asyncio
async def test_backfill_flips_only_legacy_gcode_3mf_rows(engine):
    """Rows with `file_type='3mf'` whose filename ends in `.gcode.3mf` get
    upgraded to `gcode.3mf`. Everything else stays put."""
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="sliced.gcode.3mf", file_type="3mf")
        await _insert_file(conn, file_id=2, filename="UPPER.GCODE.3MF", file_type="3mf")
        await _insert_file(conn, file_id=3, filename="model.3mf", file_type="3mf")  # not sliced
        await _insert_file(conn, file_id=4, filename="model.gcode", file_type="gcode")
        await _insert_file(conn, file_id=5, filename="model.stl", file_type="stl")
        await _insert_file(conn, file_id=6, filename="already.gcode.3mf", file_type="gcode.3mf")

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        rows = dict((await conn.execute(text("SELECT id, file_type FROM library_files ORDER BY id"))).fetchall())

    assert rows[1] == "gcode.3mf", "lowercase .gcode.3mf must be backfilled"
    assert rows[2] == "gcode.3mf", "uppercase .GCODE.3MF must be backfilled (LOWER(filename) in migration)"
    assert rows[3] == "3mf", "plain .3mf stays at `3mf` — not a sliced output"
    assert rows[4] == "gcode", "raw .gcode is untouched"
    assert rows[5] == "stl", "stl is untouched"
    assert rows[6] == "gcode.3mf", "rows already at canonical pass through"


@pytest.mark.asyncio
async def test_backfill_is_idempotent(engine):
    """Every boot re-runs the migration set; a second pass on already-
    backfilled rows must be a no-op."""
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="sliced.gcode.3mf", file_type="3mf")

    async with engine.begin() as conn:
        await run_migrations(conn)
    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT file_type FROM library_files WHERE id = 1"))
        assert result.scalar() == "gcode.3mf"


@pytest.mark.asyncio
async def test_backfill_leaves_unrelated_3mf_rows_alone(engine):
    """A row whose filename happens to contain `.gcode.3mf` as a substring
    but doesn't END with it (e.g. a `.bak` of a sliced output) is not a
    sliced output — must NOT be backfilled."""
    async with engine.begin() as conn:
        await _insert_file(conn, file_id=1, filename="sliced.gcode.3mf.bak", file_type="3mf")

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT file_type FROM library_files WHERE id = 1"))
        # The LIKE predicate uses '%.gcode.3mf' so a trailing .bak doesn't match.
        # The row keeps its pre-migration `3mf` — odd, but classify_file_type
        # returns `bak` for a fresh ingest, so this row simply stays where it
        # was. The migration's job is to fix the dominant class, not chase
        # every edge case.
        assert result.scalar() == "3mf"
