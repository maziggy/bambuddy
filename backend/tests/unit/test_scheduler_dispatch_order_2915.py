"""The order an "Any model" job picks its printer in is a setting (#2915).

Fewest print hours stays the default (#3480). The setting adds the most hours
(use up the machines near replacement), the printer idle the longest, and the
printers' own priority.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.services.print_scheduler import PrintScheduler, _order_printers

HOUR = 3600


def _p(pid, hours=0.0, priority=None):
    return SimpleNamespace(
        id=pid, runtime_seconds=int(hours * HOUR), print_hours_offset=0.0, dispatch_priority=priority
    )


def _ids(printers):
    return [p.id for p in printers]


class TestOrdering:
    def test_fewest_hours_is_the_default_and_an_unknown_value(self):
        printers = [_p(1, 900), _p(2, 100), _p(3, 20)]

        assert _ids(_order_printers(printers, "fewest_hours")) == [3, 2, 1]
        assert _ids(_order_printers(printers, "nonsense")) == [3, 2, 1]

    def test_most_hours_puts_the_most_worn_first_and_ties_go_by_id(self):
        printers = [_p(1, 20), _p(2, 900), _p(3, 20), _p(4, 100)]

        assert _ids(_order_printers(printers, "most_hours")) == [2, 4, 1, 3]

    def test_the_baseline_offset_counts_as_hours(self):
        old = SimpleNamespace(id=1, runtime_seconds=0, print_hours_offset=500.0, dispatch_priority=None)

        assert _ids(_order_printers([_p(2, 10), old], "most_hours")) == [1, 2]

    def test_least_recently_used_takes_the_longest_idle_and_never_used_first(self):
        printers = [_p(1), _p(2), _p(3), _p(4)]
        finished = {1: datetime(2026, 5, 3), 2: datetime(2026, 5, 1), 4: datetime(2026, 5, 2)}

        # 3 never printed; then 2 (May 1), 4 (May 2), 1 (May 3).
        assert _ids(_order_printers(printers, "least_recently_used", finished)) == [3, 2, 4, 1]

    def test_least_recently_used_without_any_history_goes_by_id(self):
        assert _ids(_order_printers([_p(3), _p(1), _p(2)], "least_recently_used", {})) == [1, 2, 3]

    def test_priority_is_lowest_first_empty_last_and_ties_by_id(self):
        printers = [_p(1, priority=None), _p(2, priority=5), _p(3, priority=1), _p(4, priority=5), _p(5, priority=0)]

        # 0 is a real priority (first), not "none".
        assert _ids(_order_printers(printers, "priority")) == [5, 3, 2, 4, 1]


async def _farm(setting: str | None, **printers):
    """X1Cs by id with (runtime hours, priority); the setting row is written when given."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as db:
        for key, (hours, priority) in printers.items():
            pid = int(key.removeprefix("p"))
            db.add(
                Printer(
                    id=pid,
                    name=f"X1C-{pid}",
                    serial_number=f"X1C000{pid}",
                    ip_address=f"192.0.2.{pid}",
                    access_code="x",
                    model="X1C",
                    is_active=True,
                    runtime_seconds=int(hours * HOUR),
                    dispatch_priority=priority,
                )
            )
        if setting is not None:
            db.add(Settings(key="model_dispatch_order", value=setting))
        await db.commit()
    return engine, SimpleNamespace(session_maker=session_maker)


async def _find(ctx, exclude=frozenset()):
    scheduler = PrintScheduler()
    with (
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
    ):
        async with ctx.session_maker() as db:
            return await scheduler._find_idle_printer_for_model(db, "X1C", set(exclude))


# Printer 1 is the most worn, 3 the least; priority puts 2 first, then 1, and 3 has none.
PRINTERS = {"p1": (900, 2), "p2": (100, 1), "p3": (20, None)}


class TestTheSettingDecidesWhoGetsTheJob:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("setting", "expected"),
        [
            (None, 3),  # not set: fewest hours, as before
            ("fewest_hours", 3),
            ("most_hours", 1),
            ("priority", 2),
            ("garbage", 3),  # a bad row never stops the queue
        ],
    )
    async def test_first_pick(self, setting, expected):
        engine, ctx = await _farm(setting, **PRINTERS)
        try:
            assert await _find(ctx) == (expected, None)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_a_busy_first_choice_passes_the_job_down_the_order(self):
        engine, ctx = await _farm("priority", **PRINTERS)
        try:
            assert await _find(ctx, exclude={2}) == (1, None)
            assert await _find(ctx, exclude={2, 1}) == (3, None)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_least_recently_used_follows_the_print_log(self):
        engine, ctx = await _farm("least_recently_used", **PRINTERS)
        try:
            async with ctx.session_maker() as db:
                db.add_all(
                    [
                        PrintLogEntry(printer_id=3, status="completed", completed_at=datetime(2026, 5, 3)),
                        PrintLogEntry(printer_id=3, status="completed", completed_at=datetime(2026, 4, 1)),
                        PrintLogEntry(printer_id=1, status="completed", completed_at=datetime(2026, 5, 1)),
                        PrintLogEntry(printer_id=2, status="completed", completed_at=datetime(2026, 5, 2)),
                    ]
                )
                await db.commit()

            # Each printer counts by its latest finish: 1 (May 1) is idle longest.
            assert await _find(ctx) == (1, None)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_a_printer_that_never_printed_is_the_least_recently_used(self):
        engine, ctx = await _farm("least_recently_used", **PRINTERS)
        try:
            async with ctx.session_maker() as db:
                db.add_all(
                    [
                        PrintLogEntry(printer_id=1, status="completed", completed_at=datetime(2026, 5, 1)),
                        PrintLogEntry(printer_id=2, status="completed", completed_at=datetime(2026, 5, 2)),
                    ]
                )
                await db.commit()

            assert await _find(ctx) == (3, None)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_the_wake_step_and_the_matcher_share_the_order(self):
        """Both read _printers_for_model, so a printer is woken in the order it would be picked."""
        engine, ctx = await _farm("most_hours", **PRINTERS)
        try:
            async with ctx.session_maker() as db:
                printers = await PrintScheduler()._printers_for_model(db, "X1C")
            assert [p.id for p in printers] == [1, 2, 3]
        finally:
            await engine.dispose()


class TestApi:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_order_setting_defaults_saves_and_rejects_others(self, async_client):
        default = await async_client.get("/api/v1/settings/")
        assert default.json()["model_dispatch_order"] == "fewest_hours"

        saved = await async_client.put("/api/v1/settings/", json={"model_dispatch_order": "priority"})
        assert saved.status_code == 200, saved.text
        assert (await async_client.get("/api/v1/settings/")).json()["model_dispatch_order"] == "priority"

        refused = await async_client.put("/api/v1/settings/", json={"model_dispatch_order": "alphabetical"})
        assert refused.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_printer_priority_round_trips_and_clears(self, async_client, printer_factory):
        printer = await printer_factory()

        set_ = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"dispatch_priority": 3})
        assert set_.status_code == 200, set_.text
        assert set_.json()["dispatch_priority"] == 3

        cleared = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"dispatch_priority": None})
        assert cleared.json()["dispatch_priority"] is None

        negative = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"dispatch_priority": -1})
        assert negative.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_patch_without_it_leaves_the_priority_alone(self, async_client, printer_factory):
        printer = await printer_factory()
        await async_client.patch(f"/api/v1/printers/{printer.id}", json={"dispatch_priority": 2})

        resp = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"name": "Renamed"})

        assert resp.json()["dispatch_priority"] == 2
