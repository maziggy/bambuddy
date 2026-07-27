"""Backfill for whole-file filament mis-copied onto per-plate print-log rows (#2614).

A plate dispatched from a multi-plate 3MF, when the AMS tracker measured nothing,
logged the archive's whole-file filament (the sum over every plate) into
PrintLogEntry.filament_used_grams — inflating stats by the plate count. The
forward fix scopes new rows; _migrate_scope_run_filament_to_plate repairs the
rows already written, touching only the exact whole-file mis-copies.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
import backend.app.utils.threemf_tools as threemf_tools
from backend.app.core import database as database_module
from backend.app.core.database import Base, _migrate_scope_run_filament_to_plate
from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.printer import Printer

WHOLE = 12006.49  # 22-plate file total
PLATE = 350.0  # the printed plate's own estimate
COST = 240.13  # whole-file cost


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def stub_3mf(tmp_path, monkeypatch):
    """A stub file on disk + a patched extractor returning the plate estimate."""
    monkeypatch.setattr(database_module.settings, "base_dir", tmp_path)
    fp = tmp_path / "archive" / "1" / "heart.gcode.3mf"
    fp.parent.mkdir(parents=True)
    fp.write_bytes(b"stub")
    monkeypatch.setattr(
        threemf_tools,
        "extract_plate_metadata_from_3mf",
        lambda path, plate_id: SimpleNamespace(filament_used_grams=PLATE),
    )
    return "archive/1/heart.gcode.3mf"


async def _archive(db, file_path, *, plate_id=3, whole=WHOLE, cost=COST):
    p = Printer(name="P", serial_number="S", ip_address="1.1.1.1", access_code="c", model="X1C")
    db.add(p)
    await db.flush()
    a = PrintArchive(
        filename="heart.gcode.3mf",
        file_path=file_path,
        file_size=1,
        status="completed",
        plate_id=plate_id,
        filament_used_grams=whole,
        cost=cost,
    )
    db.add(a)
    await db.flush()
    return a


@pytest.mark.asyncio
async def test_rescopes_miscopied_row_and_scales_cost(engine, stub_3mf):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        a = await _archive(db, stub_3mf)
        mis = PrintLogEntry(archive_id=a.id, status="completed", filament_used_grams=WHOLE, cost=COST)
        db.add(mis)
        await db.commit()
        mis_id = mis.id

    async with engine.begin() as conn:
        await _migrate_scope_run_filament_to_plate(conn)

    async with sm() as db:
        fixed = await db.get(PrintLogEntry, mis_id)
        assert fixed.filament_used_grams == PLATE
        assert fixed.cost == round(COST * (PLATE / WHOLE), 2)


@pytest.mark.asyncio
async def test_leaves_tracker_measured_and_partial_rows_alone(engine, stub_3mf):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        a = await _archive(db, stub_3mf)
        # Measured spool delta (rounded), != whole-file → must be untouched.
        tracked = PrintLogEntry(archive_id=a.id, status="completed", filament_used_grams=96.5, cost=2.0)
        # A partial (failed) run scaled to progress, != whole-file → untouched.
        partial = PrintLogEntry(archive_id=a.id, status="failed", filament_used_grams=1200.6, cost=24.0)
        db.add_all([tracked, partial])
        await db.commit()
        tracked_id, partial_id = tracked.id, partial.id

    async with engine.begin() as conn:
        await _migrate_scope_run_filament_to_plate(conn)

    async with sm() as db:
        assert (await db.get(PrintLogEntry, tracked_id)).filament_used_grams == 96.5
        assert (await db.get(PrintLogEntry, partial_id)).filament_used_grams == 1200.6


@pytest.mark.asyncio
async def test_idempotent_second_run_is_a_noop(engine, stub_3mf):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        a = await _archive(db, stub_3mf)
        mis = PrintLogEntry(archive_id=a.id, status="completed", filament_used_grams=WHOLE, cost=COST)
        db.add(mis)
        await db.commit()
        mis_id = mis.id

    async with engine.begin() as conn:
        await _migrate_scope_run_filament_to_plate(conn)
    async with engine.begin() as conn:
        await _migrate_scope_run_filament_to_plate(conn)

    async with sm() as db:
        assert (await db.get(PrintLogEntry, mis_id)).filament_used_grams == PLATE


@pytest.mark.asyncio
async def test_one_shot_gate_prevents_rescan_on_later_boots(engine, stub_3mf):
    """After the first pass writes its settings flag, a later boot does no work —
    the migration must never re-scan the print log every startup (single-plate rows
    legitimately match the whole-file==plate signature forever, so an ungated
    version would re-parse every single-plate 3MF on each boot)."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        a = await _archive(db, stub_3mf)
        first = PrintLogEntry(archive_id=a.id, status="completed", filament_used_grams=WHOLE, cost=COST)
        db.add(first)
        await db.commit()
        first_id, archive_id = first.id, a.id

    async with engine.begin() as conn:
        await _migrate_scope_run_filament_to_plate(conn)  # fixes `first`, writes the flag

    # A fresh mis-copy appears after the one-shot already ran.
    async with sm() as db:
        later = PrintLogEntry(archive_id=archive_id, status="completed", filament_used_grams=WHOLE, cost=COST)
        db.add(later)
        await db.commit()
        later_id = later.id

    async with engine.begin() as conn:
        await _migrate_scope_run_filament_to_plate(conn)  # gate short-circuits; no scan

    async with sm() as db:
        assert (await db.get(PrintLogEntry, first_id)).filament_used_grams == PLATE
        # Deliberately untouched: the gate skipped the whole pass. New mis-copies
        # can't occur anyway — the forward fix scopes every row at write time.
        assert (await db.get(PrintLogEntry, later_id)).filament_used_grams == WHOLE


@pytest.mark.asyncio
async def test_skips_row_when_3mf_missing(engine, tmp_path, monkeypatch):
    # base_dir set, but the archive's file was never on disk → row is left alone
    # (can't compute a plate value; don't guess).
    monkeypatch.setattr(database_module.settings, "base_dir", tmp_path)
    monkeypatch.setattr(
        threemf_tools,
        "extract_plate_metadata_from_3mf",
        lambda path, plate_id: SimpleNamespace(filament_used_grams=PLATE),
    )
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        a = await _archive(db, "archive/1/gone.gcode.3mf")
        mis = PrintLogEntry(archive_id=a.id, status="completed", filament_used_grams=WHOLE, cost=COST)
        db.add(mis)
        await db.commit()
        mis_id = mis.id

    async with engine.begin() as conn:
        await _migrate_scope_run_filament_to_plate(conn)

    async with sm() as db:
        assert (await db.get(PrintLogEntry, mis_id)).filament_used_grams == WHOLE


@pytest.mark.asyncio
async def test_single_plate_archive_not_relabelled(engine, tmp_path, monkeypatch):
    # A genuine single-plate archive whose plate estimate equals the whole-file
    # value must not be rewritten (no-op guard on unchanged grams).
    monkeypatch.setattr(database_module.settings, "base_dir", tmp_path)
    fp = tmp_path / "archive" / "1" / "heart.gcode.3mf"
    fp.parent.mkdir(parents=True)
    fp.write_bytes(b"stub")
    monkeypatch.setattr(
        threemf_tools,
        "extract_plate_metadata_from_3mf",
        lambda path, plate_id: SimpleNamespace(filament_used_grams=WHOLE),
    )
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        a = await _archive(db, "archive/1/heart.gcode.3mf", plate_id=1)
        row = PrintLogEntry(archive_id=a.id, status="completed", filament_used_grams=WHOLE, cost=COST)
        db.add(row)
        await db.commit()
        row_id = row.id

    async with engine.begin() as conn:
        await _migrate_scope_run_filament_to_plate(conn)

    async with sm() as db:
        fixed = await db.get(PrintLogEntry, row_id)
        assert fixed.filament_used_grams == WHOLE
        assert fixed.cost == COST
