"""The tracker adds a print's usage to what is stored, not to its own copy (WU-4).

Completion loads the spool, awaits more lookups, and only then charges it. A
weight set by hand or weighed on the SpoolBuddy scale in between was lost:
the tracker wrote back its earlier copy plus the print's grams. Each test
stores 300 g used (the hand edit) while the tracker's copy still reads the
100 g it loaded before that edit.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import set_committed_value

from backend.app.models.spool import Spool
from backend.app.services.usage_tracker import (
    PrintSession,
    _active_sessions,
    _charge_spool,
    _track_from_3mf,
    on_print_complete,
)

pytestmark = pytest.mark.asyncio

HAND_SET = 300.0


def _loaded_before_the_hand_edit(spool, _context):
    set_committed_value(spool, "weight_used", 100.0)


@pytest.fixture
def stale_loads():
    event.listen(Spool, "load", _loaded_before_the_hand_edit)
    yield
    event.remove(Spool, "load", _loaded_before_the_hand_edit)


@pytest.fixture
async def maker(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _spools(maker, count: int = 1) -> list[int]:
    async with maker() as db:
        rows = [Spool(material="PLA", label_weight=1000, weight_used=HAND_SET) for _ in range(count)]
        db.add_all(rows)
        await db.commit()
        return [row.id for row in rows]


async def _printer(maker) -> int:
    from backend.app.models.printer import Printer

    async with maker() as db:
        printer = Printer(name="P", serial_number="RACE01", ip_address="10.0.0.9", access_code="12345678")
        db.add(printer)
        await db.commit()
        return printer.id


async def _stored(maker, spool_id: int) -> float:
    """Read as a column, so the stale-load hook does not touch it."""
    async with maker() as db:
        return await db.scalar(select(Spool.weight_used).where(Spool.id == spool_id))


async def test_the_charge_adds_to_the_stored_weight(maker, stale_loads):
    (spool_id,) = await _spools(maker)

    async with maker() as db:
        spool = await db.get(Spool, spool_id)
        assert spool.weight_used == 100.0, "the copy is the stale one"
        await _charge_spool(db, spool, 25.0)
        assert spool.weight_used == HAND_SET + 25.0, "the copy follows what is stored"
        await db.commit()

    assert await _stored(maker, spool_id) == HAND_SET + 25.0


async def test_a_spool_deleted_meanwhile_is_left_alone(maker):
    (spool_id,) = await _spools(maker)

    async with maker() as db:
        spool = await db.get(Spool, spool_id)
        async with maker() as other:
            await other.delete(await other.get(Spool, spool_id))
            await other.commit()
        assert await _charge_spool(db, spool, 25.0) is False
        await db.commit()


async def _track_3mf(
    maker, tmp_path, spool_ids: dict[tuple[int, int], int], slots: int = 1, mapping=(0,), **state
) -> None:
    printer_id = await _printer(maker)
    threemf = tmp_path / "part.3mf"
    threemf.write_bytes(b"3mf")
    status = SimpleNamespace(
        progress=100, layer_num=90, tray_now=0, last_loaded_tray=-1, raw_data={}, **{"total_layers": 0, **state}
    )
    printer_manager = MagicMock()
    printer_manager.get_status.return_value = status

    async def resolve(printer_id, ams_id, tray_id, **kwargs):
        return spool_ids.get((ams_id, tray_id))

    usage = [{"slot_id": n, "used_g": 9.0, "type": "PLA", "color": "#FFFFFF"} for n in range(1, slots + 1)]
    with (
        patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=usage),
        patch("backend.app.utils.threemf_tools.extract_layer_filament_usage_from_3mf", return_value=None),
        patch("backend.app.utils.threemf_tools.extract_filament_properties_from_3mf", return_value={}),
        patch("backend.app.services.usage_tracker._resolve_spool_id_for_tray", side_effect=resolve),
    ):
        async with maker() as db:
            await _track_from_3mf(
                printer_id=printer_id,
                archive_id=None,
                status="completed",
                print_name="part",
                handled_trays=set(),
                printer_manager=printer_manager,
                db=db,
                ams_mapping=list(mapping),
                print_started_at=datetime.now(timezone.utc),
                threemf_path=threemf,
            )
            await db.commit()


async def test_the_3mf_charge(maker, tmp_path, stale_loads):
    (spool_id,) = await _spools(maker)

    await _track_3mf(maker, tmp_path, {(0, 0): spool_id}, tray_change_log=[])

    assert await _stored(maker, spool_id) == HAND_SET + 9.0


async def test_the_3mf_charge_split_across_trays(maker, tmp_path, stale_loads):
    first, second = await _spools(maker, 2)

    await _track_3mf(
        maker, tmp_path, {(0, 0): first, (0, 1): second}, tray_change_log=[(0, 0), (1, 45)], total_layers=90
    )

    assert (await _stored(maker, first), await _stored(maker, second)) == (HAND_SET + 4.5, HAND_SET + 4.5)


@pytest.mark.parametrize("deleted", [False, True], ids=["charged", "deleted-meanwhile"])
async def test_the_remain_percentage_charge(maker, stale_loads, deleted):
    from backend.app.models.spool_assignment import SpoolAssignment

    (spool_id,) = await _spools(maker)
    printer_id = await _printer(maker)
    async with maker() as db:
        db.add(SpoolAssignment(spool_id=spool_id, printer_id=printer_id, ams_id=0, tray_id=0))
        await db.commit()

    # 80% -> 70% of a 1000 g spool: 100 g.
    _active_sessions[printer_id] = PrintSession(
        printer_id=printer_id, print_name="part", started_at=datetime.now(timezone.utc), tray_remain_start={(0, 0): 80}
    )
    status = SimpleNamespace(
        raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]}, progress=100, layer_num=0, tray_now=255
    )
    printer_manager = MagicMock()
    printer_manager.get_status.return_value = status
    try:
        with (
            patch("backend.app.api.routes.settings.get_setting", new_callable=AsyncMock, return_value=None),
            patch("backend.app.services.usage_tracker._track_from_3mf", AsyncMock(return_value=[])),
            patch(
                "backend.app.services.usage_tracker._charge_spool",
                _deleting(maker, spool_id if deleted else -1),
            ),
        ):
            async with maker() as db:
                await on_print_complete(printer_id, {"status": "completed"}, printer_manager, db)
    finally:
        _active_sessions.pop(printer_id, None)

    if deleted:
        assert await _charged(maker) == []
    else:
        assert await _stored(maker, spool_id) == HAND_SET + 100.0


@pytest.mark.parametrize("split", [True, False], ids=["split-across-trays", "one-tray-per-slot"])
async def test_a_spool_deleted_while_the_print_is_charged_is_skipped(maker, tmp_path, split):
    """The rest of the print is still charged: a usage row for the deleted spool
    broke the foreign key on PostgreSQL and rolled the whole completion back."""
    first, second = await _spools(maker, 2)

    with patch("backend.app.services.usage_tracker._charge_spool", _deleting(maker, first)):
        if split:
            await _track_3mf(
                maker, tmp_path, {(0, 0): first, (0, 1): second}, tray_change_log=[(0, 0), (1, 45)], total_layers=90
            )
        else:
            await _track_3mf(maker, tmp_path, {(0, 0): first, (0, 1): second}, slots=2, mapping=(0, 1))

    assert await _stored(maker, second) == HAND_SET + (4.5 if split else 9.0)
    assert await _charged(maker) == [second]


def _deleting(maker, spool_id: int):
    """_charge_spool, with *spool_id* deleted by someone else just before."""
    from backend.app.services import usage_tracker

    real_charge = usage_tracker._charge_spool

    async def charge(db, spool, grams):
        if spool.id == spool_id:
            async with maker() as other:
                await other.delete(await other.get(Spool, spool_id))
                await other.commit()
        return await real_charge(db, spool, grams)

    return charge


async def _charged(maker) -> list[int]:
    from backend.app.models.spool_usage_history import SpoolUsageHistory

    async with maker() as db:
        return list((await db.execute(select(SpoolUsageHistory.spool_id))).scalars().all())
