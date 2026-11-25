"""spoolman_slot_assignments admits the H3C's AMS 3H: ams_id 32 with slots 0-8, externals (255, 2-8).

Slots 4-8 exist only on the AMS 3H and among an H3C's external inputs, so the
tray CHECK is ``tray_id >= 0 AND (tray_id <= 3 OR (ams_id IN (32, 255) AND
tray_id <= 8))``. An upgraded SQLite table is rebuilt to it from upstream's
CHECKs and from the plain ``tray_id <= 8`` an earlier build of this support
wrote; a row the latter let through on another unit is dropped.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import _migrate_widen_spoolman_slot_ams_id_range
from backend.app.models.spoolman_slot_assignment import AMS_ID_CHECK, TRAY_ID_CHECK

_TABLE = """
CREATE TABLE spoolman_slot_assignments (
    id INTEGER NOT NULL,
    printer_id INTEGER NOT NULL,
    ams_id INTEGER NOT NULL,
    tray_id INTEGER NOT NULL,
    spoolman_spool_id INTEGER NOT NULL,
    assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_slot_assignment UNIQUE (printer_id, ams_id, tray_id),
    CONSTRAINT ck_ams_id_range CHECK ({ams}),
    CONSTRAINT ck_tray_id_range CHECK ({tray})
)
"""
_AMS_1274 = "(ams_id >= 0 AND ams_id <= 7) OR (ams_id >= 128 AND ams_id <= 191) OR ams_id = 255"
# The table as create_all built it for the #1274 model (upstream dev).
TABLE_1274 = _TABLE.format(ams=_AMS_1274, tray="tray_id >= 0 AND tray_id <= 3")
# As an earlier build of the H3C support left it: by its own SQLite rebuild...
TABLE_WIDENED_REBUILT = (
    "CREATE TABLE spoolman_slot_assignments (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE, "
    f"ams_id INTEGER NOT NULL CHECK ({AMS_ID_CHECK}), "
    "tray_id INTEGER NOT NULL CHECK (tray_id >= 0 AND tray_id <= 8), "
    "spoolman_spool_id INTEGER NOT NULL, assigned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CONSTRAINT uq_slot_assignment UNIQUE(printer_id, ams_id, tray_id))"
)
# ...and by the live-install script, which rewrote the create_all form in place.
TABLE_WIDENED_IN_PLACE = _TABLE.format(ams=AMS_ID_CHECK, tray="tray_id >= 0 AND tray_id <= 8")
TABLE_FINAL = _TABLE.format(ams=AMS_ID_CHECK, tray=TRAY_ID_CHECK)


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    from backend.app.core import database as database_module, db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


async def _insert(conn, ams_id: int, tray_id: int, spool: int) -> None:
    await conn.execute(
        text(
            "INSERT INTO spoolman_slot_assignments (printer_id, ams_id, tray_id, spoolman_spool_id) "
            "VALUES (1, :a, :t, :s)"
        ),
        {"a": ams_id, "t": tray_id, "s": spool},
    )


async def _rows(conn) -> list[tuple]:
    rows = await conn.execute(
        text("SELECT ams_id, tray_id, spoolman_spool_id FROM spoolman_slot_assignments ORDER BY id")
    )
    return [tuple(r) for r in rows.fetchall()]


async def _assert_final_checks(conn) -> None:
    await _insert(conn, 32, 8, 14)  # AMS 3H slot 9
    await _insert(conn, 255, 5, 15)  # an H3C external input (250)
    for ams_id, tray_id in ((0, 4), (128, 5), (8, 0), (32, 9), (40, 0), (255, 9)):
        with pytest.raises(IntegrityError):
            async with conn.begin_nested():
                await _insert(conn, ams_id, tray_id, 99)
    indexes = (
        await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='spoolman_slot_assignments'")
        )
    ).fetchall()
    assert "ix_slot_assignment_spool" in {r[0] for r in indexes}


async def _table(conn, ddl: str) -> None:
    await conn.execute(text(ddl))
    await conn.execute(text("CREATE INDEX ix_slot_assignment_spool ON spoolman_slot_assignments (spoolman_spool_id)"))


@pytest.mark.asyncio
async def test_upstreams_table_takes_the_ams3h_and_keeps_its_rows():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await _table(conn, TABLE_1274)
        await _insert(conn, 0, 3, 11)
        await _insert(conn, 128, 0, 12)
        await _insert(conn, 255, 1, 13)
        with pytest.raises(IntegrityError):
            async with conn.begin_nested():
                await _insert(conn, 32, 8, 14)

        await _migrate_widen_spoolman_slot_ams_id_range(conn)
        await _migrate_widen_spoolman_slot_ams_id_range(conn)  # a second start changes nothing

        assert await _rows(conn) == [(0, 3, 11), (128, 0, 12), (255, 1, 13)]
        await _assert_final_checks(conn)
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("ddl", [TABLE_WIDENED_REBUILT, TABLE_WIDENED_IN_PLACE])
async def test_a_table_an_earlier_build_widened_is_narrowed_to_real_slots(ddl):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await _table(conn, ddl)
        await _insert(conn, 32, 6, 11)
        await _insert(conn, 255, 4, 12)
        await _insert(conn, 0, 6, 13)  # slot 7 of a regular AMS: no such slot

        await _migrate_widen_spoolman_slot_ams_id_range(conn)
        await _migrate_widen_spoolman_slot_ams_id_range(conn)

        assert await _rows(conn) == [(32, 6, 11), (255, 4, 12)]
        await _assert_final_checks(conn)
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_final_table_is_left_alone():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await _table(conn, TABLE_FINAL)
        await _insert(conn, 32, 6, 11)
        before = (
            await conn.execute(text("SELECT sql FROM sqlite_master WHERE name='spoolman_slot_assignments'"))
        ).scalar()

        await _migrate_widen_spoolman_slot_ams_id_range(conn)

        after = (
            await conn.execute(text("SELECT sql FROM sqlite_master WHERE name='spoolman_slot_assignments'"))
        ).scalar()
        assert after == before
        assert await _rows(conn) == [(32, 6, 11)]
        await _assert_final_checks(conn)
    await engine.dispose()


def test_the_model_admits_what_the_migration_does():
    from sqlalchemy import CheckConstraint

    from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

    checks = {
        c.name: str(c.sqltext) for c in SpoolmanSlotAssignment.__table__.constraints if isinstance(c, CheckConstraint)
    }
    assert checks["ck_ams_id_range"] == AMS_ID_CHECK
    assert (
        checks["ck_tray_id_range"]
        == TRAY_ID_CHECK
        == ("tray_id >= 0 AND (tray_id <= 3 OR (ams_id IN (32, 255) AND tray_id <= 8))")
    )


def test_the_fresh_install_ddl_matches_the_model():
    import inspect

    import backend.app.core.database as database_module

    source = inspect.getsource(database_module)
    for dialect_marker in ("id INTEGER PRIMARY KEY AUTOINCREMENT,\n            printer_id", "id SERIAL PRIMARY KEY,"):
        start = source.index("CREATE TABLE IF NOT EXISTS spoolman_slot_assignments (\n            " + dialect_marker)
        ddl = source[start : source.index(')\n        """', start)]
        assert f"CHECK ({AMS_ID_CHECK})" in ddl and f"CHECK ({TRAY_ID_CHECK})" in ddl


def test_the_request_schema_admits_the_ams3h():
    from pydantic import TypeAdapter, ValidationError

    from backend.app.schemas.spoolman import SlotAmsId, SlotTrayId

    assert TypeAdapter(SlotAmsId).validate_python(32) == 32
    assert TypeAdapter(SlotTrayId).validate_python(8) == 8
    for bad_ams in (8, 31, 33):
        with pytest.raises(ValidationError):
            TypeAdapter(SlotAmsId).validate_python(bad_ams)
    with pytest.raises(ValidationError):
        TypeAdapter(SlotTrayId).validate_python(9)
