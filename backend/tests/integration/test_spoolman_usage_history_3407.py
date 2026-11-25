"""Spoolman spools get the per-print usage history built-in spools have (#3407).

Bambuddy charges Spoolman for every print but kept no record of it, so Spoolman
mode had no usage history: no trend for the stock forecast and no usage list
on the spool. Each charge now writes a ``spool_usage_history`` row naming the
Spoolman spool in ``spoolman_spool_id``, with ``spool_id`` NULL so a Spoolman
id never reads as a built-in spool's (#3406).
"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_usage_history import SpoolUsageHistory
from backend.app.utils.local_time import utcnow_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _row(spoolman_spool_id=None, spool_id=None, *, grams=10.0, days_ago=1.0, archive_id=None, cost=None):
    return SpoolUsageHistory(
        spool_id=spool_id,
        spoolman_spool_id=spoolman_spool_id,
        weight_used=grams,
        percent_used=1,
        status="completed",
        archive_id=archive_id,
        cost=cost,
        created_at=utcnow_naive() - timedelta(days=days_ago),
    )


async def _built_in_spool(db) -> Spool:
    spool = Spool(material="PLA", label_weight=1000, core_weight=250, weight_used=0)
    db.add(spool)
    await db.commit()
    await db.refresh(spool)
    return spool


def _spoolman_with(*spools):
    """Spoolman as Recalculate Costs reads its prices (#3414)."""
    client = MagicMock()
    client.get_all_spools = AsyncMock(return_value=list(spools))
    return patch("backend.app.services.usage_repricing._spoolman_client", AsyncMock(return_value=client))


async def _rows(db) -> list[SpoolUsageHistory]:
    return list((await db.execute(select(SpoolUsageHistory).execution_options(populate_existing=True))).scalars().all())


async def test_a_failed_print_records_its_partial_usage_with_its_status(db_session, printer_factory, archive_factory):
    from backend.app.models.active_print_spoolman import ActivePrintSpoolman
    from backend.app.services import spoolman_tracking

    printer = await printer_factory()
    archive = await archive_factory(printer.id, print_name="Bracket", status="aborted")
    db_session.add(ActivePrintSpoolman(printer_id=printer.id, archive_id=archive.id, filament_usage=[], ams_trays={}))
    await db_session.commit()

    async def _partial(printer_id, tracking, *, last_layer_num=None, last_progress=None, cost_out=None):
        cost_out.add(40.0, {"id": 5, "price": 20.0, "filament": {"weight": 1000}}, "slot 1", spool_id=5)

    with patch.object(spoolman_tracking, "_report_partial_usage", _partial):
        await spoolman_tracking.cleanup_tracking(printer.id, archive.id, db_session, status="aborted")

    ((row,),) = [await _rows(db_session)]
    assert (row.spool_id, row.spoolman_spool_id, row.weight_used, row.status, row.print_name) == (
        None,
        5,
        40.0,
        "aborted",
        "Bracket",
    )
    assert (row.percent_used, row.cost) == (4, 0.8)


async def test_an_unpriced_spool_is_recorded_without_a_price(db_session, printer_factory, archive_factory):
    """As completion leaves the archive's cost alone for it, so a recalculation
    keeps that figure too (see the recalculation test below)."""
    from backend.app.services.spoolman_tracking import _PrintCost, _record_usage_history

    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    cost = _PrintCost()
    cost.add(100.0, {"id": 6, "filament": {"weight": 1000}}, "slot 2", spool_id=6)

    await _record_usage_history(db_session, printer.id, archive.id, "completed", cost)

    ((row,),) = [await _rows(db_session)]
    assert (row.weight_used, row.cost) == (100.0, None)


async def test_recalculating_keeps_the_cost_of_a_print_from_unpriced_spools(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """The archive keeps what it was priced at (here the catalogue's 30/kg),
    not the 25/kg default the unpriced row would otherwise stand for."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="default_filament_cost", value="25"))
    printer = await printer_factory()
    archive = await archive_factory(printer.id, status="completed", cost=3.0)
    archive.filament_used_grams = 100.0
    db_session.add(_row(spoolman_spool_id=41, grams=100.0, archive_id=archive.id, cost=None))
    await db_session.commit()

    with _spoolman_with({"id": 41, "initial_weight": 1000, "filament": {"weight": 1000}}):
        assert (await async_client.post("/api/v1/archives/recalculate-costs")).status_code == 200

    assert (await async_client.get(f"/api/v1/archives/{archive.id}")).json()["cost"] == 3.0


async def test_the_global_usage_list_is_one_inventorys(async_client: AsyncClient, db_session):
    spool = await _built_in_spool(db_session)
    db_session.add_all([_row(spool_id=spool.id), _row(spoolman_spool_id=spool.id)])
    await db_session.commit()

    built_in = (await async_client.get("/api/v1/inventory/usage")).json()
    spoolman = (await async_client.get("/api/v1/inventory/usage", params={"spoolman": "true"})).json()

    assert [(r["spool_id"], r["spoolman_spool_id"]) for r in built_in] == [(spool.id, None)]
    assert [(r["spool_id"], r["spoolman_spool_id"]) for r in spoolman] == [(None, spool.id)]


async def test_a_spoolman_spool_lists_and_clears_its_own_usage(async_client: AsyncClient, db_session):
    """Never a built-in spool's that shares its id."""
    spool = await _built_in_spool(db_session)
    db_session.add_all([_row(spoolman_spool_id=spool.id, grams=12.0), _row(spool_id=spool.id, grams=99.0)])
    await db_session.commit()
    url = f"/api/v1/spoolman/inventory/spools/{spool.id}/usage"

    listed = (await async_client.get(url)).json()
    assert [r["weight_used"] for r in listed] == [12.0]

    assert (await async_client.delete(url)).status_code == 200
    assert [(r.spool_id, r.weight_used) for r in await _rows(db_session)] == [(spool.id, 99.0)]


async def test_deleting_the_spoolman_spool_keeps_its_usage_without_its_id(async_client: AsyncClient, db_session):
    """Spoolman can give the id to a new spool, which must not inherit it."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value="http://localhost:7912"))
    db_session.add_all([_row(spoolman_spool_id=5), _row(spoolman_spool_id=6)])
    await db_session.commit()
    client = MagicMock()
    client.delete_spool = AsyncMock()

    with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=client)):
        response = await async_client.delete("/api/v1/spoolman/inventory/spools/5")

    assert response.status_code == 200, response.text
    assert sorted((r.spoolman_spool_id or 0) for r in await _rows(db_session)) == [0, 6]


async def test_the_stock_alerts_use_the_spoolman_rows_in_spoolman_mode(db_session):
    """And with them the trend, as built-in spools have it."""
    from backend.app.services.print_scheduler import PrintScheduler
    from backend.app.services.stock_forecast import UsageRecord, history_rate

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    days = (30, 20, 10)
    db_session.add_all([_row(spoolman_spool_id=7, grams=30.0, days_ago=d) for d in days])
    # A built-in spool 7, from before the switch: not the Spoolman spool's.
    db_session.add_all([_row(spool_id=None, grams=500.0, days_ago=d) for d in (5, 4)])
    await db_session.commit()
    spoolman = MagicMock()
    spoolman.get_all_spools = AsyncMock(
        return_value=[
            {
                "id": 7,
                "remaining_weight": 900.0,
                "used_weight": 100.0,
                "registered": (utcnow_naive() - timedelta(days=100)).isoformat() + "Z",
                "filament": {"id": 1, "material": "PLA", "weight": 1000, "vendor": {"name": "Bambu Lab"}},
            }
        ]
    )

    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=spoolman)):
        ((forecast, _),) = (await PrintScheduler()._stock_forecasts(db_session)).values()

    trend = history_rate([UsageRecord(utcnow_naive() - timedelta(days=d), 30.0) for d in days], utcnow_naive())
    assert forecast.daily_rate_g == pytest.approx(trend[0], rel=1e-3)


async def test_recalculating_costs_rebuilds_a_spoolman_archive_from_its_rows(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    """The rows carry what the charges were priced at; at the spool's price
    unchanged, the figure stays."""
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="default_filament_cost", value="25"))
    printer = await printer_factory()
    archive = await archive_factory(printer.id, status="completed", cost=4.0)
    archive.filament_used_grams = 100.0
    db_session.add(_row(spoolman_spool_id=41, grams=100.0, archive_id=archive.id, cost=4.0))
    await db_session.commit()

    with _spoolman_with({"id": 41, "price": 40.0, "initial_weight": 1000, "filament": {"weight": 1000}}):
        assert (await async_client.post("/api/v1/archives/recalculate-costs")).status_code == 200

    assert (await async_client.get(f"/api/v1/archives/{archive.id}")).json()["cost"] == 4.0


async def test_a_backup_restores_the_spoolman_spool(db_session):
    from backend.app.services.github_restore import GitHubRestoreService, _CategoryTally

    usage = {
        "usage_history": [
            {
                "id": 1,
                "spool_id": None,
                "spoolman_spool_id": 41,
                "weight_used": 12.5,
                "created_at": "2026-01-06 09:00:00",
            },
            {
                "id": 2,
                "spool_id": None,
                "spoolman_spool_id": True,
                "weight_used": 1.0,
                "created_at": "2026-01-06 10:00:00",
            },
        ]
    }
    tally = _CategoryTally()
    await GitHubRestoreService()._restore_spool_usage(db_session, usage, tally, {}, {})
    await db_session.commit()

    assert sorted((r.spoolman_spool_id or 0, r.weight_used) for r in await _rows(db_session)) == [(0, 1.0), (41, 12.5)]

    # Two Spoolman spools charged the same at the same moment are two rows.
    twins = {
        "usage_history": [
            {"id": 3, "spoolman_spool_id": 50, "weight_used": 7.0, "created_at": "2026-01-07 09:00:00"},
            {"id": 4, "spoolman_spool_id": 51, "weight_used": 7.0, "created_at": "2026-01-07 09:00:00"},
        ]
    }
    await GitHubRestoreService()._restore_spool_usage(db_session, twins, _CategoryTally(), {}, {})
    await db_session.commit()
    assert sorted(r.spoolman_spool_id or 0 for r in await _rows(db_session) if r.weight_used == 7.0) == [50, 51]
    usage["usage_history"] += twins["usage_history"]

    # Restored again: recognised, not doubled.
    await GitHubRestoreService()._restore_spool_usage(db_session, usage, _CategoryTally(), {}, {})
    await db_session.commit()
    assert len(await _rows(db_session)) == 4


async def test_a_backup_without_built_in_spools_restores_the_usage(db_session):
    """A Spoolman-mode backup has no spools/inventory.json to go with it."""
    from backend.app.services.github_restore import GitHubRestoreService, _CategoryTally

    usage = {"usage_history": [{"id": 1, "spoolman_spool_id": 41, "weight_used": 5.0, "created_at": "2026-01-06"}]}
    await GitHubRestoreService()._restore_spools(db_session, None, usage, False, _CategoryTally(), {})
    await db_session.commit()

    assert [r.spoolman_spool_id for r in await _rows(db_session)] == [41]


async def test_a_backup_carries_the_spoolman_spool(db_session):
    from backend.app.services.github_backup import GitHubBackupService

    db_session.add(_row(spoolman_spool_id=41))
    await db_session.commit()

    files: dict = {}
    await GitHubBackupService()._collect_spools(db_session, files)
    assert [u["spoolman_spool_id"] for u in files["spools/usage_history.json"]["usage_history"]] == [41]


async def test_each_inventory_reads_its_own_rows_up_to_the_limit(db_session, monkeypatch):
    """The read is capped; newer rows of the other inventory must not use it up."""
    from backend.app.services import print_scheduler

    built_in = await _built_in_spool(db_session)
    db_session.add(_row(spool_id=built_in.id, days_ago=5))
    db_session.add_all([_row(spoolman_spool_id=9, days_ago=d) for d in (1, 2)])
    await db_session.commit()
    monkeypatch.setattr(print_scheduler, "_STOCK_FORECAST_HISTORY_LIMIT", 1)

    scheduler = print_scheduler.PrintScheduler()
    assert list(await scheduler._stock_usage_history(db_session)) == [built_in.id]
    assert list(await scheduler._stock_usage_history(db_session, spoolman=True)) == [9]


async def test_the_spoolman_stock_alerts_spread_a_spool_from_its_first_use(db_session):
    """Registered 120 days ago, first printed with 21 days ago, last 2 days ago."""
    from backend.app.services.print_scheduler import PrintScheduler

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    await db_session.commit()

    def _iso(days):
        return (utcnow_naive() - timedelta(days=days)).isoformat() + "Z"

    spoolman = MagicMock()
    spoolman.get_all_spools = AsyncMock(
        return_value=[
            {
                "id": 3,
                "remaining_weight": 900.0,
                "used_weight": 100.0,
                "registered": _iso(120),
                "first_used": _iso(21),
                "last_used": _iso(2),
                "filament": {"id": 1, "material": "PLA", "weight": 1000, "vendor": {"name": "Bambu Lab"}},
            }
        ]
    )
    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=spoolman)):
        ((forecast, _),) = (await PrintScheduler()._stock_forecasts(db_session)).values()

    assert forecast.daily_rate_g == pytest.approx(100 / 21, rel=1e-3)


async def test_the_built_in_stock_alerts_end_a_spool_at_its_last_use(db_session):
    """Created 120 days ago, last used 100 days ago: nothing in the last 90."""
    from backend.app.services.print_scheduler import PrintScheduler

    spool = await _built_in_spool(db_session)
    spool.weight_used = 300.0
    spool.created_at = utcnow_naive() - timedelta(days=120)
    spool.last_used = utcnow_naive() - timedelta(days=100)
    await db_session.commit()

    ((forecast, _),) = (await PrintScheduler()._stock_forecasts(db_session)).values()

    assert forecast.daily_rate_g == 0


async def test_the_spoolman_stock_alerts_end_a_spool_at_its_last_use(db_session):
    """Used from 150 to 60 days ago: 30 of its 90 days of use are in the window,
    so 30 of its 90 g. Same as the panel's test."""
    from backend.app.services.print_scheduler import PrintScheduler

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    await db_session.commit()

    def _iso(days):
        return (utcnow_naive() - timedelta(days=days)).isoformat() + "Z"

    spoolman = MagicMock()
    spoolman.get_all_spools = AsyncMock(
        return_value=[
            {
                "id": 4,
                "remaining_weight": 910.0,
                "used_weight": 90.0,
                "registered": _iso(200),
                "first_used": _iso(150),
                "last_used": _iso(60),
                "filament": {"id": 1, "material": "PLA", "weight": 1000, "vendor": {"name": "Bambu Lab"}},
            }
        ]
    )
    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=spoolman)):
        ((forecast, _),) = (await PrintScheduler()._stock_forecasts(db_session)).values()

    assert forecast.daily_rate_g == pytest.approx(30 / 90, rel=1e-3)


async def test_rows_of_an_earlier_spool_with_the_same_id_are_not_its_own(db_session):
    """Spoolman handed id 6 to a new spool, registered 15 days ago; rows from
    before that are the deleted spool's."""
    from backend.app.services.print_scheduler import PrintScheduler
    from backend.app.services.stock_forecast import UsageRecord, history_rate

    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add_all([_row(spoolman_spool_id=6, grams=30.0, days_ago=d) for d in (40, 30, 10, 5)])
    await db_session.commit()
    spoolman = MagicMock()
    spoolman.get_all_spools = AsyncMock(
        return_value=[
            {
                "id": 6,
                "remaining_weight": 940.0,
                "used_weight": 60.0,
                "registered": (utcnow_naive() - timedelta(days=15)).isoformat() + "Z",
                "filament": {"id": 1, "material": "PLA", "weight": 1000, "vendor": {"name": "Bambu Lab"}},
            }
        ]
    )
    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=spoolman)):
        ((forecast, _),) = (await PrintScheduler()._stock_forecasts(db_session)).values()

    own = history_rate([UsageRecord(utcnow_naive() - timedelta(days=d), 30.0) for d in (10, 5)], utcnow_naive())
    assert forecast.daily_rate_g == pytest.approx(own[0], rel=1e-3)
