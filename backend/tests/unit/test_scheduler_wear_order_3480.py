"""An "Any model" job goes to the eligible printer with the fewest print hours (#3480).

The matcher used to take the first eligible printer in database order, so on a
farm the lowest id got most of the work. It now ranks the printers of the model
by the maintenance hour counter (runtime plus the baseline offset), fewest
first, and the smart-plug wake step powers on the least worn one the same way.
Everything that decides whether a printer is eligible is unchanged.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import PrintScheduler, _ModelCandidate

HOUR = 3600


async def _db(printers: dict[int, tuple[int, float]]):
    """X1Cs by id: (runtime_seconds, print_hours_offset)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as db:
        db.add_all(
            [
                Printer(
                    id=pid,
                    name=f"X1C-{pid}",
                    serial_number=f"X1C000{pid}",
                    ip_address=f"192.0.2.{pid}",
                    access_code="x",
                    model="X1C",
                    is_active=True,
                    runtime_seconds=runtime,
                    print_hours_offset=offset,
                )
                for pid, (runtime, offset) in printers.items()
            ]
        )
        await db.commit()
    return engine, SimpleNamespace(session_maker=session_maker)


@pytest.fixture
async def farm():
    """Printer 1 is the most worn, printer 3 the least; 2 sits between them."""
    engine, ctx = await _db({1: (900 * HOUR, 0.0), 2: (100 * HOUR, 0.0), 3: (20 * HOUR, 0.0)})
    try:
        yield ctx
    finally:
        await engine.dispose()


async def _find(ctx, scheduler, *, exclude=frozenset(), short=None, **kwargs):
    async def is_short(pid):
        return pid in short

    with (
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
    ):
        async with ctx.session_maker() as db:
            return await scheduler._find_idle_printer_for_model(
                db, "X1C", set(exclude), is_short=is_short if short is not None else None, **kwargs
            )


class TestMatcher:
    @pytest.mark.asyncio
    async def test_takes_the_printer_with_the_fewest_hours(self, farm):
        assert await _find(farm, PrintScheduler()) == (3, None)

    @pytest.mark.asyncio
    async def test_a_busy_least_worn_printer_passes_the_job_to_the_next(self, farm):
        assert await _find(farm, PrintScheduler(), exclude={3}) == (2, None)

    @pytest.mark.asyncio
    async def test_counts_the_baseline_offset_like_maintenance(self):
        """A second-hand machine entered with its real hours is not "new"."""
        engine, ctx = await _db({1: (10 * HOUR, 0.0), 2: (0, 500.0)})
        try:
            assert await _find(ctx, PrintScheduler()) == (1, None)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_printers_still_at_zero_go_by_id(self):
        engine, ctx = await _db({3: (0, 0.0), 1: (0, 0.0), 2: (0, 0.0)})
        try:
            assert await _find(ctx, PrintScheduler()) == (1, None)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_a_short_printer_is_passed_over_for_the_next_least_worn(self, farm):
        assert await _find(farm, PrintScheduler(), short={3}) == (2, None)

    @pytest.mark.asyncio
    async def test_every_printer_short_holds_the_job_on_the_least_worn(self, farm):
        assert await _find(farm, PrintScheduler(), short={1, 2, 3}) == (3, None)

    @pytest.mark.asyncio
    async def test_more_preferred_colours_still_beat_fewer_hours(self, farm):
        scheduler = PrintScheduler()
        matches = {1: 2, 2: 1, 3: 1}
        with patch.object(scheduler, "_count_override_color_matches", side_effect=lambda pid, _o: matches[pid]):
            result = await _find(farm, scheduler, filament_overrides=[{"type": "PLA", "color": "#000000"}])
        assert result == (1, None)

    @pytest.mark.asyncio
    async def test_equal_preferred_colours_go_to_the_fewest_hours(self, farm):
        scheduler = PrintScheduler()
        matches = {1: 1, 2: 2, 3: 2}
        with patch.object(scheduler, "_count_override_color_matches", side_effect=lambda pid, _o: matches[pid]):
            result = await _find(farm, scheduler, filament_overrides=[{"type": "PLA", "color": "#000000"}])
        assert result == (3, None)


class TestQueuePass:
    @pytest.mark.asyncio
    async def test_jobs_spread_over_the_least_worn_printers(self, farm):
        """Two "any X1C" jobs, three idle X1Cs: the two least worn take them."""
        ids = []
        async with farm.session_maker() as db:
            for pos in (1, 2):
                lib = LibraryFile(
                    filename=f"job{pos}.gcode.3mf",
                    file_path=f"/library/job{pos}.gcode.3mf",
                    file_size=10,
                    file_type="gcode.3mf",
                    file_metadata={"sliced_for_model": "X1C"},
                )
                db.add(lib)
                await db.flush()
                item = PrintQueueItem(status="pending", position=pos, target_model="X1C", library_file_id=lib.id)
                db.add(item)
                await db.flush()
                ids.append(item.id)
            await db.commit()

        scheduler = PrintScheduler()
        patches = [
            patch("backend.app.services.print_scheduler.async_session", farm.session_maker),
            patch("backend.app.core.database.async_session", farm.session_maker),
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
            patch(
                "backend.app.services.print_scheduler.ha_sensor_manager.blocked_printers",
                AsyncMock(return_value={}),
            ),
            patch(
                "backend.app.services.notification_service.notification_service.on_queue_job_waiting",
                AsyncMock(),
            ),
            patch(
                "backend.app.services.notification_service.notification_service.on_queue_job_assigned",
                AsyncMock(),
            ),
            patch("backend.app.services.print_scheduler.compute_deficit_for_queue_item", AsyncMock(return_value=[])),
            patch.object(scheduler, "_filament_short_on", AsyncMock(return_value=False)),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
            patch.object(scheduler, "_check_auto_drying", AsyncMock()),
            patch.object(scheduler, "_ensure_ams_mapping", AsyncMock(return_value=None)),
            patch.object(scheduler, "_block_on_unmatched_filament", AsyncMock(return_value=False)),
            patch.object(scheduler, "_launch_uploads", MagicMock()),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            await scheduler.check_queue()

        async with farm.session_maker() as db:
            rows = (await db.execute(select(PrintQueueItem).where(PrintQueueItem.id.in_(ids)))).scalars().all()
        assert sorted(row.printer_id for row in rows) == [2, 3]


class TestWake:
    @pytest.mark.asyncio
    async def test_powers_on_the_least_worn_offline_printer(self, farm):
        scheduler = PrintScheduler()
        plug = SimpleNamespace(id=1, auto_on=True, enabled=True, controls_printer_power=True)
        power_on = AsyncMock(return_value=True)
        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=False)),
            patch(
                "backend.app.services.print_scheduler.printer_manager.is_awaiting_plate_clear",
                MagicMock(return_value=False),
            ),
            patch.object(scheduler, "_cached_filament_shortfall", MagicMock(return_value=[])),
            patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[plug])),
            patch.object(scheduler, "_power_on_and_wait", power_on),
        ):
            async with farm.session_maker() as db:
                woken = await scheduler._wake_printer_for_model(
                    db,
                    [
                        _ModelCandidate(
                            target_model="X1C", sliced_for="X1C", required_filament_types=None, filament_overrides=None
                        )
                    ],
                    None,
                    set(),
                    {1, 2, 3},
                    require_plate_clear=True,
                )
        assert woken == (3, 3)
        assert power_on.call_args[0][1] == 3
