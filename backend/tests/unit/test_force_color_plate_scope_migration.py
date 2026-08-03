"""Regression test for the force-colour override re-scoping migration (#2551).

Queueing several plates of one 3MF used to store the union of every selected
plate's filament overrides on each item, so a `force_color_match` plate printing
a single colour sat at Waiting until a printer had the whole batch's palette
loaded. The write paths now scope overrides to the plate; this migration repairs
the items queued before the fix, which would otherwise stay stuck forever with a
waiting reason that explains nothing.
"""

from __future__ import annotations

import json
import zipfile

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import run_migrations

THREE_PLATES = """<?xml version="1.0" encoding="UTF-8"?>
<config>
    <plate>
        <metadata key="index" value="1"/>
        <filament id="1" used_g="50.0" type="PLA" color="#0B2C7A"/>
    </plate>
    <plate>
        <metadata key="index" value="2"/>
        <filament id="2" used_g="40.0" type="PLA" color="#9B9EA0"/>
    </plate>
    <plate>
        <metadata key="index" value="3"/>
        <filament id="3" used_g="30.0" type="PLA" color="#F4EE2A"/>
    </plate>
</config>
"""

ALL_THREE_COLORS = [
    {"slot_id": 1, "type": "PLA", "color": "#0B2C7A", "color_name": "Army Blue", "force_color_match": True},
    {"slot_id": 2, "type": "PLA", "color": "#9B9EA0", "color_name": "Ash Grey", "force_color_match": True},
    {"slot_id": 3, "type": "PLA", "color": "#F4EE2A", "color_name": "Sunshine Yellow", "force_color_match": True},
]


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """Force the SQLite branch regardless of test env settings."""
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
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


@pytest.fixture
def three_plate_3mf(tmp_path):
    path = tmp_path / "three_plates.gcode.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", THREE_PLATES)
    return path


async def _seed(engine, *, file_path, items) -> None:
    """Insert one archive plus the queue items pointing at it.

    Goes through the models rather than raw INSERTs so the columns this test
    doesn't care about get their defaults.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.app.models.archive import PrintArchive
    from backend.app.models.print_queue import PrintQueueItem

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(
            PrintArchive(
                id=1,
                filename="three_plates.gcode.3mf",
                file_path=str(file_path),
                file_size=0,
                status="completed",
            )
        )
        for item_id, plate_id, overrides, status in items:
            session.add(
                PrintQueueItem(
                    id=item_id,
                    archive_id=1,
                    target_model="X1C",
                    plate_id=plate_id,
                    filament_overrides=json.dumps(overrides),
                    status=status,
                    position=item_id,
                )
            )
        await session.commit()


async def _overrides(conn, item_id: int):
    raw = (
        await conn.execute(text("SELECT filament_overrides FROM print_queue WHERE id = :id"), {"id": item_id})
    ).scalar_one()
    return json.loads(raw) if raw else None


@pytest.mark.asyncio
async def test_each_stuck_item_is_rescoped_to_its_own_plate(engine, three_plate_3mf):
    """The reporter's queue: three plates, each item demanding all three colours."""
    await _seed(
        engine,
        file_path=three_plate_3mf,
        items=[
            (1, 1, ALL_THREE_COLORS, "pending"),
            (2, 2, ALL_THREE_COLORS, "pending"),
            (3, 3, ALL_THREE_COLORS, "pending"),
        ],
    )

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        assert [o["color_name"] for o in await _overrides(conn, 1)] == ["Army Blue"]
        assert [o["color_name"] for o in await _overrides(conn, 2)] == ["Ash Grey"]
        assert [o["color_name"] for o in await _overrides(conn, 3)] == ["Sunshine Yellow"]


@pytest.mark.asyncio
async def test_it_is_idempotent(engine, three_plate_3mf):
    """Every boot re-runs the migration set; an already-scoped item narrows to
    itself and must not be rewritten or emptied."""
    await _seed(engine, file_path=three_plate_3mf, items=[(1, 2, ALL_THREE_COLORS, "pending")])

    for _ in range(2):
        async with engine.begin() as conn:
            await run_migrations(conn)

    async with engine.connect() as conn:
        assert [o["slot_id"] for o in await _overrides(conn, 1)] == [2]


@pytest.mark.asyncio
async def test_a_dispatched_item_is_left_alone(engine, three_plate_3mf):
    """A printing item's overrides record what it dispatched with — that is
    history, not an instruction, and rewriting it would falsify the record."""
    await _seed(engine, file_path=three_plate_3mf, items=[(1, 1, ALL_THREE_COLORS, "printing")])

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        assert len(await _overrides(conn, 1)) == 3


@pytest.mark.asyncio
async def test_a_whole_file_item_keeps_every_colour(engine, three_plate_3mf):
    """No plate_id means the item prints the whole file, so it really does need
    all three colours."""
    await _seed(engine, file_path=three_plate_3mf, items=[(1, None, ALL_THREE_COLORS, "pending")])

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        assert len(await _overrides(conn, 1)) == 3


@pytest.mark.asyncio
async def test_a_missing_source_file_does_not_strip_the_overrides(engine, tmp_path):
    """The archive's 3MF is gone, so the plate's slots cannot be read. Keep the
    overrides: an item waiting on a colour it does not need is visible and
    fixable, one that silently lost a forced colour prints in the wrong filament.
    """
    await _seed(engine, file_path=tmp_path / "deleted.gcode.3mf", items=[(1, 1, ALL_THREE_COLORS, "pending")])

    async with engine.begin() as conn:
        await run_migrations(conn)

    async with engine.connect() as conn:
        assert len(await _overrides(conn, 1)) == 3
