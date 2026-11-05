"""Older print log runs get the plate they printed where it is known.

Runs are now written with their plate. For runs written before, it is the
plate of the queue item that ran them, or the archive's when the run is that
archive's only one. A run of a reprinted archive with no queue item stays
NULL: the archive's plate need not be the one that run printed.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base, _backfill_print_log_plate_ids
from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer


@pytest.mark.asyncio
async def test_older_runs_get_their_plate_where_it_is_known():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            printer = Printer(name="P", serial_number="S", ip_address="127.0.0.1", access_code="c", model="X1C")
            single = PrintArchive(filename="a.3mf", file_path="a", file_size=1, status="completed", plate_id=2)
            reprinted = PrintArchive(filename="b.3mf", file_path="b", file_size=1, status="completed", plate_id=5)
            db.add_all([printer, single, reprinted])
            await db.flush()
            queued = PrintQueueItem(printer_id=printer.id, archive_id=reprinted.id, status="completed", plate_id=4)
            db.add(queued)
            await db.flush()
            runs = {
                "queued": PrintLogEntry(archive_id=reprinted.id, queue_item_id=queued.id, status="completed"),
                "reprinted": PrintLogEntry(archive_id=reprinted.id, status="completed"),
                "single": PrintLogEntry(archive_id=single.id, status="completed"),
                "known": PrintLogEntry(archive_id=single.id, status="failed", plate_id=1, queue_item_id=queued.id),
                "orphan": PrintLogEntry(archive_id=None, status="completed"),
            }
            db.add_all(runs.values())
            await db.commit()
            ids = {name: run.id for name, run in runs.items()}

        for _ in range(2):  # every start runs it; the second changes nothing
            async with engine.begin() as conn:
                await _backfill_print_log_plate_ids(conn)

        async with maker() as db:
            plates = dict((await db.execute(select(PrintLogEntry.id, PrintLogEntry.plate_id))).tuples().all())
        assert {name: plates[run_id] for name, run_id in ids.items()} == {
            "queued": 4,
            "reprinted": None,
            # Two runs on the archive now, one of them already known: the
            # archive's plate is not taken for the other.
            "single": None,
            "known": 1,
            "orphan": None,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_only_run_of_an_archive_gets_the_archives_plate():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            archive = PrintArchive(filename="a.3mf", file_path="a", file_size=1, status="completed", plate_id=2)
            db.add(archive)
            await db.flush()
            run = PrintLogEntry(archive_id=archive.id, status="completed")
            db.add(run)
            await db.commit()

        async with engine.begin() as conn:
            await _backfill_print_log_plate_ids(conn)

        async with maker() as db:
            assert await db.scalar(select(PrintLogEntry.plate_id).where(PrintLogEntry.id == run.id)) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_it_runs_once():
    """A later dispatch moves the archive's plate to its own run's before
    that run is logged; a second pass would give it to the old run."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with engine.begin() as conn:
            await _backfill_print_log_plate_ids(conn)  # the first start: nothing to do
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            archive = PrintArchive(filename="a.3mf", file_path="a", file_size=1, status="printing", plate_id=2)
            db.add(archive)
            await db.flush()
            run = PrintLogEntry(archive_id=archive.id, status="completed")
            db.add(run)
            await db.commit()

        async with engine.begin() as conn:
            await _backfill_print_log_plate_ids(conn)

        async with maker() as db:
            assert await db.scalar(select(PrintLogEntry.plate_id).where(PrintLogEntry.id == run.id)) is None
    finally:
        await engine.dispose()
