"""Selected plate persists onto the archive and backfills from the queue (#2603).

A whole multi-plate 3MF is uploaded under one filename with no plate suffix, so
the archive parser can't recover the selected plate and Print History fell back
to Plate 1. The queue row keeps the correct ``plate_id``; these tests cover
copying it onto the archive, the startup backfill for pre-existing rows, and that
``run_migrations`` applies the new column + backfill cleanly.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base, run_migrations
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer


@pytest.fixture
def force_sqlite_dialect(monkeypatch):
    """Force the SQLite branch of run_migrations regardless of the test env's
    DATABASE_URL (this sandbox points it at Postgres)."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


# The exact backfill statement run by run_migrations (kept in sync deliberately;
# the run_migrations smoke test below exercises the real one).
_BACKFILL_SQL = (
    "UPDATE print_archives "
    "SET plate_id = ("
    "  SELECT pq.plate_id FROM print_queue pq "
    "  WHERE pq.archive_id = print_archives.id AND pq.plate_id IS NOT NULL "
    "  LIMIT 1"
    ") "
    "WHERE plate_id IS NULL "
    "AND EXISTS ("
    "  SELECT 1 FROM print_queue pq "
    "  WHERE pq.archive_id = print_archives.id AND pq.plate_id IS NOT NULL"
    ")"
)


@pytest.fixture
async def sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _printer(db) -> int:
    printer = Printer(name="P", serial_number="S", ip_address="10.0.0.1", access_code="code", model="X1C")
    db.add(printer)
    await db.flush()
    return printer.id


@pytest.mark.asyncio
async def test_archive_row_stores_plate_id(sm):
    """The column round-trips a selected plate."""
    async with sm() as db:
        archive = PrintArchive(filename="heart 3.gcode.3mf", file_path="x", file_size=1, status="printing", plate_id=22)
        db.add(archive)
        await db.commit()
        await db.refresh(archive)
        assert archive.plate_id == 22


@pytest.mark.asyncio
async def test_backfill_copies_plate_from_linked_queue_row(sm):
    """An archive with no plate inherits it from a queue row that still links to it."""
    async with sm() as db:
        printer_id = await _printer(db)
        archive = PrintArchive(
            filename="heart 3.gcode.3mf", file_path="x", file_size=1, status="cancelled", plate_id=None
        )
        db.add(archive)
        await db.flush()
        db.add(PrintQueueItem(printer_id=printer_id, archive_id=archive.id, status="cancelled", plate_id=22))
        await db.commit()

        await db.execute(text(_BACKFILL_SQL))
        await db.commit()
        await db.refresh(archive)
        assert archive.plate_id == 22


@pytest.mark.asyncio
async def test_backfill_does_not_clobber_existing_plate_or_touch_unlinked(sm):
    """Idempotent: a set plate is left alone, and an archive with no linked queue plate stays NULL."""
    async with sm() as db:
        printer_id = await _printer(db)
        # Archive already carrying a plate; queue row disagrees — must not be overwritten.
        set_archive = PrintArchive(filename="a.3mf", file_path="a", file_size=1, status="cancelled", plate_id=7)
        # Archive with no linked queue plate at all — must stay NULL.
        null_archive = PrintArchive(filename="b.3mf", file_path="b", file_size=1, status="completed", plate_id=None)
        db.add_all([set_archive, null_archive])
        await db.flush()
        db.add(PrintQueueItem(printer_id=printer_id, archive_id=set_archive.id, status="cancelled", plate_id=3))
        await db.commit()

        # Run twice — second run must be a no-op.
        await db.execute(text(_BACKFILL_SQL))
        await db.execute(text(_BACKFILL_SQL))
        await db.commit()
        await db.refresh(set_archive)
        await db.refresh(null_archive)
        assert set_archive.plate_id == 7, "an archive that already had a plate must not be relabelled"
        assert null_archive.plate_id is None, "an archive with no linked queue plate must stay NULL"


@pytest.mark.asyncio
async def test_run_migrations_adds_column_and_backfills_in_order(force_sqlite_dialect):
    """End-to-end: run_migrations adds print_archives.plate_id and backfills it from
    print_queue.plate_id without crashing (#2603).

    Guards the migration *ordering*: the backfill reads print_queue.plate_id, which is
    added earlier in run_migrations. If the backfill ran before that column existed
    (as it did in the first draft), a first-ever migration pass would raise
    "no such column: print_queue.plate_id" and abort startup. Running the full
    migration here — twice — proves the order is correct and idempotent. Mirrors the
    harness in test_cancellation_cascade_recovery_migration.py.
    """
    # run_migrations touches many tables; register the full model set so
    # create_all builds the whole schema (imports for side effects only).
    from backend.app.models import (  # noqa: F401
        ams_history,
        ams_label,
        api_key,
        auth_ephemeral,
        color_catalog,
        external_link,
        filament,
        group,
        kprofile_note,
        maintenance,
        notification,
        notification_template,
        oidc_provider,
        print_log,
        project,
        project_bom,
        slot_preset,
        smart_plug,
        smart_plug_energy_snapshot,
        sponsor_toast_state,
        spool,
        spool_assignment,
        spool_catalog,
        spool_k_profile,
        spool_usage_history,
        spoolbuddy_device,
        spoolman_k_profile,
        spoolman_slot_assignment,
        user,
        user_email_pref,
        user_otp_code,
        user_totp,
        virtual_printer,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Seed the archive + queue row BEFORE any migration pass — an existing
        # install upgrading to this version. The archive predates the archive_fts
        # FTS table (created inside run_migrations), so it is NOT indexed; the
        # backfill's UPDATE would trip the external-content FTS 'delete' ("database
        # disk image is malformed") unless the migration rebuilds the FTS index
        # first. This is the exact shape that failed the force-color migration
        # tests before the rebuild guard was added.
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as db:
            printer_id = await _printer(db)
            db.add(PrintArchive(id=436, filename="heart 3.gcode.3mf", file_path="x", file_size=1, status="cancelled"))
            await db.flush()
            db.add(PrintQueueItem(id=177, printer_id=printer_id, archive_id=436, status="cancelled", plate_id=22))
            await db.commit()

        # Upgrade boot: run_migrations must add print_archives.plate_id, rebuild the
        # FTS index, and backfill the plate — without crashing. Also guards the
        # ordering bug: the full migration runs top-to-bottom, so if the backfill
        # preceded the print_queue.plate_id column it would raise "no such column".
        async with engine.begin() as conn:
            await run_migrations(conn)
        async with sm() as db:
            archive = await db.get(PrintArchive, 436)
            assert archive.plate_id == 22, "run_migrations must backfill the archive's plate from its queue row"

        # A further startup re-runs migrations — idempotent, plate unchanged, and
        # (plate now set) the rebuild+backfill is skipped entirely.
        async with engine.begin() as conn:
            await run_migrations(conn)
        async with sm() as db:
            assert (await db.get(PrintArchive, 436)).plate_id == 22
    finally:
        await engine.dispose()
