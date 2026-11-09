"""Queued H3C jobs move from the AMS-HT-range ids 128 + k to Studio's 28 + k, once.

Bambuddy without H3C support numbered the AMS 3H (unit 32) as 32 * 4 + k, so a
job queued for an H3C then carries 128-136. Other printers' jobs keep theirs.
"""

import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.core.database import _migrate_h3c_queue_tray_ids
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem, PrintQueueVariant
from backend.app.models.printer import Printer

FLAG = "_migrate_h3c_queue_tray_ids_done"


async def _seed(engine) -> dict[str, int]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        h3c = Printer(name="H3C", serial_number="SN-Q-H3C", ip_address="192.0.2.30", access_code="1", model="N10")
        h2d = Printer(name="H2D", serial_number="SN-Q-H2D", ip_address="192.0.2.31", access_code="1", model="H2D")
        db.add_all([h3c, h2d])
        await db.flush()
        items = {
            "h3c": PrintQueueItem(printer_id=h3c.id, ams_mapping=json.dumps([128, 136, -1, 254, 3])),
            "model": PrintQueueItem(target_model="H3C", ams_mapping=json.dumps([130])),
            "h2d": PrintQueueItem(printer_id=h2d.id, ams_mapping=json.dumps([128, 129])),
            "none": PrintQueueItem(printer_id=h3c.id, ams_mapping=None),
        }
        db.add_all(items.values())
        await db.flush()
        library_file = LibraryFile(filename="a.3mf", file_path="a.3mf", file_type="3mf", file_size=1)
        db.add(library_file)
        await db.flush()
        variants = {
            "v_h3c": PrintQueueVariant(
                queue_item_id=items["model"].id,
                library_file_id=library_file.id,
                target_model="H3C",
                ams_mapping=json.dumps([131, 32]),
            ),
            "v_h2d": PrintQueueVariant(
                queue_item_id=items["model"].id,
                library_file_id=library_file.id,
                target_model="H2D",
                ams_mapping=json.dumps([131]),
            ),
        }
        db.add_all(variants.values())
        await db.commit()
        return {**{k: v.id for k, v in items.items()}, **{k: v.id for k, v in variants.items()}}


async def _mappings(engine) -> dict[tuple[str, int], list | None]:
    maker = async_sessionmaker(engine)
    async with maker() as db:
        out: dict[tuple[str, int], list | None] = {}
        for model, name in ((PrintQueueItem, "q"), (PrintQueueVariant, "v")):
            for row_id, raw in (await db.execute(select(model.id, model.ams_mapping))).tuples():
                out[(name, row_id)] = json.loads(raw) if raw else None
        return out


async def _run(engine) -> None:
    async with engine.begin() as conn:
        await _migrate_h3c_queue_tray_ids(conn)


@pytest.mark.asyncio
async def test_h3c_jobs_are_renumbered_and_others_kept(test_engine):
    ids = await _seed(test_engine)

    await _run(test_engine)

    got = await _mappings(test_engine)
    assert got[("q", ids["h3c"])] == [28, 36, -1, 254, 3]
    assert got[("q", ids["model"])] == [30]
    assert got[("q", ids["h2d"])] == [128, 129]
    assert got[("q", ids["none"])] is None
    assert got[("v", ids["v_h3c"])] == [31, 32]
    assert got[("v", ids["v_h2d"])] == [131]


@pytest.mark.asyncio
async def test_it_runs_once(test_engine):
    ids = await _seed(test_engine)
    await _run(test_engine)
    async with test_engine.begin() as conn:
        flag = (await conn.execute(text('SELECT value FROM settings WHERE "key" = :k'), {"k": FLAG})).scalar_one()
        assert flag == "true"
        await conn.execute(text("UPDATE print_queue SET ams_mapping = '[129]' WHERE id = :id"), {"id": ids["h3c"]})

    await _run(test_engine)

    assert (await _mappings(test_engine))[("q", ids["h3c"])] == [129]


def test_runs_at_startup():
    """The rewrite only helps if startup runs it."""
    import inspect

    from backend.app.core.database import _run_migrations

    assert "await _migrate_h3c_queue_tray_ids(conn)" in inspect.getsource(_run_migrations)
