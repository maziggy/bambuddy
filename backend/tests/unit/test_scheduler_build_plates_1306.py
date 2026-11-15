"""Plate-aware queue dispatch (#1306).

A print farm swaps build plates between jobs. A file sliced for Smooth PEI
must not be handed to a printer with a textured plate on it, and a job the user
wants on a specific patterned plate (a Bambu 3D Effect Carbon Fiber sheet) must
wait for that plate. Rules exercised here:

- **Opt-in.** With ``build_plate_tracking_enabled`` off, plates are ignored.
- **Untracked printers take anything.** A printer with no plate recorded is
  never held for a plate.
- **A pattern is a subset of its type.** A Carbon Fiber plate is a Smooth PEI
  plate, so it takes Smooth PEI jobs; asking for Carbon Fiber specifically
  needs exactly that plate.
- **A wrong plate waits, it does not fail**, and says which swap would help.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base
from backend.app.models.build_plate import BuildPlate
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.settings import Settings
from backend.app.services.print_scheduler import PrintScheduler, _ModelCandidate

TEXTURED, SMOOTH, CARBON = 1, 2, 3


@pytest.fixture
async def ctx():
    """Two idle X1Cs and three plates: textured, smooth, and a smooth-PEI pattern."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        for pid in (1, 2):
            db.add(
                Printer(
                    id=pid,
                    name=f"X1C-0{pid}",
                    serial_number=f"X1C000{pid}",
                    ip_address=f"10.0.0.{pid}",
                    access_code="x",
                    model="X1C",
                    is_active=True,
                )
            )
        db.add(BuildPlate(id=TEXTURED, name="Textured PEI Plate", base_type="textured_pei"))
        db.add(BuildPlate(id=SMOOTH, name="Smooth PEI Plate", base_type="smooth_pei"))
        db.add(BuildPlate(id=CARBON, name="3D Effect – Carbon Fiber", base_type="smooth_pei", pattern="Carbon Fiber"))
        await db.commit()

    try:
        yield SimpleNamespace(session_maker=session_maker)
    finally:
        await engine.dispose()


async def _enable_tracking(ctx, on=True):
    async with ctx.session_maker() as db:
        db.add(Settings(key="build_plate_tracking_enabled", value="true" if on else "false"))
        await db.commit()


async def _install(ctx, printer_id, plate_id):
    async with ctx.session_maker() as db:
        printer = (await db.execute(select(Printer).where(Printer.id == printer_id))).scalar_one()
        printer.installed_plate_id = plate_id
        await db.commit()


async def _add_item(
    ctx,
    *,
    printer_id=None,
    target_model="X1C",
    plate_type="smooth_pei",
    plate_id=None,
    position=1,
    bed_type_meta=None,
):
    async with ctx.session_maker() as db:
        meta = {"sliced_for_model": "X1C"}
        if bed_type_meta:
            meta["bed_type"] = bed_type_meta
        lib = LibraryFile(
            filename=f"job{position}.gcode.3mf",
            file_path=f"/library/job{position}.gcode.3mf",
            file_size=10,
            file_type="gcode.3mf",
            file_metadata=meta,
        )
        db.add(lib)
        await db.flush()
        item = PrintQueueItem(
            status="pending",
            position=position,
            printer_id=printer_id,
            target_model=None if printer_id else target_model,
            library_file_id=lib.id,
            required_plate_type=plate_type,
            required_plate_id=plate_id,
        )
        db.add(item)
        await db.commit()
        return item.id


async def _item(ctx, item_id):
    async with ctx.session_maker() as db:
        return (await db.execute(select(PrintQueueItem).where(PrintQueueItem.id == item_id))).scalar_one()


async def _run(ctx, scheduler, *, connected=True, waiting=None):
    """One check_queue pass with both printers idle and nothing else in the way."""
    launched = MagicMock()
    patches = [
        patch("backend.app.services.print_scheduler.async_session", ctx.session_maker),
        patch("backend.app.core.database.async_session", ctx.session_maker),
        patch(
            "backend.app.services.print_scheduler.printer_manager.is_connected",
            MagicMock(return_value=connected),
        ),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
        patch(
            "backend.app.services.print_scheduler.printer_manager.is_awaiting_plate_clear",
            MagicMock(return_value=False),
        ),
        patch(
            "backend.app.services.print_scheduler.ha_sensor_manager.blocked_printers",
            AsyncMock(return_value={}),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_waiting",
            waiting or AsyncMock(),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_assigned",
            AsyncMock(),
        ),
        patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
        patch.object(scheduler, "_ensure_ams_mapping", AsyncMock(return_value=None)),
        patch.object(scheduler, "_block_on_filament_deficit", AsyncMock(return_value=False)),
        patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[])),
        patch.object(scheduler, "_launch_uploads", launched),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        await scheduler.check_queue()
    return launched


class TestModelBasedJobs:
    @pytest.mark.asyncio
    async def test_tracking_off_ignores_plates(self, ctx):
        """The default: nothing about dispatch changes until the user opts in."""
        await _install(ctx, 1, TEXTURED)
        await _install(ctx, 2, TEXTURED)
        item_id = await _add_item(ctx, plate_type="smooth_pei")

        await _run(ctx, PrintScheduler())

        item = await _item(ctx, item_id)
        assert item.printer_id == 1
        assert item.waiting_reason is None

    @pytest.mark.asyncio
    async def test_goes_to_the_printer_with_the_right_plate(self, ctx):
        await _enable_tracking(ctx)
        await _install(ctx, 1, TEXTURED)
        await _install(ctx, 2, SMOOTH)
        item_id = await _add_item(ctx, plate_type="smooth_pei")

        await _run(ctx, PrintScheduler())

        assert (await _item(ctx, item_id)).printer_id == 2

    @pytest.mark.asyncio
    async def test_a_patterned_plate_counts_as_its_base_type(self, ctx):
        """Carbon Fiber is a Smooth PEI plate, so it takes any Smooth PEI job."""
        await _enable_tracking(ctx)
        await _install(ctx, 1, TEXTURED)
        await _install(ctx, 2, CARBON)
        item_id = await _add_item(ctx, plate_type="smooth_pei")

        await _run(ctx, PrintScheduler())

        assert (await _item(ctx, item_id)).printer_id == 2

    @pytest.mark.asyncio
    async def test_a_specific_plate_waits_for_that_plate(self, ctx):
        """Plain Smooth PEI is the right type but not the Carbon Fiber look asked for."""
        await _enable_tracking(ctx)
        await _install(ctx, 1, TEXTURED)
        await _install(ctx, 2, SMOOTH)
        item_id = await _add_item(ctx, plate_type="smooth_pei", plate_id=CARBON)
        waiting = AsyncMock()

        await _run(ctx, PrintScheduler(), waiting=waiting)

        item = await _item(ctx, item_id)
        assert item.printer_id is None
        assert item.waiting_reason.startswith("Wrong plate:")
        assert "needs 3D Effect – Carbon Fiber" in item.waiting_reason
        assert "X1C-02 (needs 3D Effect – Carbon Fiber, has Smooth PEI Plate)" in item.waiting_reason
        # A plate swap needs a person, so it is not a silent busy-only wait.
        waiting.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_wrong_plate_everywhere_waits_with_the_swap_needed(self, ctx):
        await _enable_tracking(ctx)
        await _install(ctx, 1, TEXTURED)
        await _install(ctx, 2, TEXTURED)
        item_id = await _add_item(ctx, plate_type="smooth_pei")

        await _run(ctx, PrintScheduler())

        item = await _item(ctx, item_id)
        assert item.status == "pending"
        assert item.printer_id is None
        assert "needs Smooth PEI / High Temp Plate, has Textured PEI Plate" in item.waiting_reason

    @pytest.mark.asyncio
    async def test_untracked_printers_take_any_job(self, ctx):
        await _enable_tracking(ctx)
        item_id = await _add_item(ctx, plate_type="smooth_pei")

        await _run(ctx, PrintScheduler())

        assert (await _item(ctx, item_id)).printer_id == 1

    @pytest.mark.asyncio
    async def test_any_plate_job_goes_anywhere(self, ctx):
        await _enable_tracking(ctx)
        await _install(ctx, 1, TEXTURED)
        await _install(ctx, 2, TEXTURED)
        item_id = await _add_item(ctx, plate_type="any")

        await _run(ctx, PrintScheduler())

        assert (await _item(ctx, item_id)).printer_id == 1

    @pytest.mark.asyncio
    async def test_plate_type_is_derived_for_rows_that_never_read_the_file(self, ctx):
        """Library bulk-add, the virtual printer and webhooks build rows without a
        plate type. The scheduler fills it in from the file's metadata once."""
        await _enable_tracking(ctx)
        await _install(ctx, 1, TEXTURED)
        await _install(ctx, 2, SMOOTH)
        item_id = await _add_item(ctx, plate_type=None, bed_type_meta="High Temp Plate")

        await _run(ctx, PrintScheduler())

        item = await _item(ctx, item_id)
        assert item.required_plate_type == "smooth_pei"
        assert item.printer_id == 2

    @pytest.mark.asyncio
    async def test_a_deleted_specific_plate_falls_back_to_the_type(self, ctx):
        """Waiting for a plate that no longer exists would be waiting forever."""
        await _enable_tracking(ctx)
        await _install(ctx, 1, SMOOTH)
        item_id = await _add_item(ctx, plate_type="smooth_pei", plate_id=999)

        await _run(ctx, PrintScheduler())

        assert (await _item(ctx, item_id)).printer_id == 1


class TestPinnedJobs:
    @pytest.mark.asyncio
    async def test_wrong_plate_holds_and_asks_for_a_swap(self, ctx):
        await _enable_tracking(ctx)
        await _install(ctx, 1, TEXTURED)
        item_id = await _add_item(ctx, printer_id=1, plate_type="smooth_pei")

        launched = await _run(ctx, PrintScheduler())

        launched.assert_not_called()
        item = await _item(ctx, item_id)
        assert item.status == "pending"
        assert item.waiting_reason == (
            "Wrong plate: X1C-01 (needs Smooth PEI / High Temp Plate, has Textured PEI Plate)"
        )

    @pytest.mark.asyncio
    async def test_swapping_the_plate_releases_it(self, ctx):
        await _enable_tracking(ctx)
        await _install(ctx, 1, TEXTURED)
        item_id = await _add_item(ctx, printer_id=1, plate_type="smooth_pei")
        scheduler = PrintScheduler()
        await _run(ctx, scheduler)

        await _install(ctx, 1, SMOOTH)
        launched = await _run(ctx, scheduler)

        launched.assert_called_once()
        assert (await _item(ctx, item_id)).waiting_reason is None

    @pytest.mark.asyncio
    async def test_a_later_job_for_the_plate_on_the_printer_goes_ahead(self, ctx):
        """The held job does not hold the printer: a job that fits the plate
        already on it runs, which is the point of tracking plates at all."""
        await _enable_tracking(ctx)
        await _install(ctx, 1, TEXTURED)
        held_id = await _add_item(ctx, printer_id=1, plate_type="smooth_pei", position=1)
        fits_id = await _add_item(ctx, printer_id=1, plate_type="textured_pei", position=2)

        launched = await _run(ctx, PrintScheduler())

        launched.assert_called_once()
        dispatched = launched.call_args.args[0]
        assert dispatched == [fits_id]
        assert (await _item(ctx, held_id)).waiting_reason.startswith("Wrong plate:")

    @pytest.mark.asyncio
    async def test_tracking_off_pinned_job_ignores_plate(self, ctx):
        await _install(ctx, 1, TEXTURED)
        await _add_item(ctx, printer_id=1, plate_type="smooth_pei")

        launched = await _run(ctx, PrintScheduler())

        launched.assert_called_once()


class TestTurningTrackingOff:
    """Jobs held for a plate go out on the next pass once tracking is off."""

    async def _set_tracking(self, ctx, on):
        async with ctx.session_maker() as db:
            row = (
                await db.execute(select(Settings).where(Settings.key == "build_plate_tracking_enabled"))
            ).scalar_one()
            row.value = "true" if on else "false"
            await db.commit()

    @pytest.mark.asyncio
    async def test_pinned_and_model_jobs_dispatch_and_lose_the_plate_reason(self, ctx):
        await _enable_tracking(ctx)
        await _install(ctx, 1, TEXTURED)
        await _install(ctx, 2, TEXTURED)
        pinned_id = await _add_item(ctx, printer_id=2, plate_type="smooth_pei", plate_id=CARBON, position=1)
        model_id = await _add_item(ctx, plate_type="smooth_pei", plate_id=CARBON, position=2)
        scheduler = PrintScheduler()

        await _run(ctx, scheduler)
        assert (await _item(ctx, pinned_id)).waiting_reason.startswith("Wrong plate:")
        assert "Wrong plate:" in (await _item(ctx, model_id)).waiting_reason

        await self._set_tracking(ctx, False)
        launched = await _run(ctx, scheduler)

        launched.assert_called_once()
        assert sorted(launched.call_args.args[0]) == sorted([pinned_id, model_id])
        pinned, model = await _item(ctx, pinned_id), await _item(ctx, model_id)
        assert pinned.waiting_reason is None
        assert model.waiting_reason is None
        assert model.printer_id == 1
        # The requirement itself is kept for if tracking comes back on.
        assert pinned.required_plate_id == CARBON

    @pytest.mark.asyncio
    async def test_a_pass_that_wakes_a_printer_still_drops_the_plate_reason(self, ctx):
        """The wake step skips the usual reason update for the pass it powers a
        printer on; a "Wrong plate" reason that no longer holds must not survive it."""
        item_id = await _add_item(ctx, plate_type="smooth_pei")
        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, item_id)
            item.waiting_reason = "Wrong plate: X1C-01 (needs Smooth PEI / High Temp Plate, has Textured PEI Plate)"
            await db.commit()
        scheduler = PrintScheduler()

        with (
            patch.object(scheduler, "_find_idle_printer_for_model", AsyncMock(return_value=(None, "Offline: X1C-01"))),
            patch.object(scheduler, "_wake_printer_for_model", AsyncMock(return_value=(1, 1))),
        ):
            launched = await _run(ctx, scheduler)

        launched.assert_not_called()
        assert (await _item(ctx, item_id)).waiting_reason == "Offline: X1C-01"


class TestWake:
    @pytest.mark.asyncio
    async def test_does_not_power_on_a_printer_with_the_wrong_plate(self, ctx):
        await _install(ctx, 1, TEXTURED)
        scheduler = PrintScheduler()
        candidate = _ModelCandidate(
            target_model="X1C",
            sliced_for="X1C",
            required_filament_types=None,
            filament_overrides=None,
            required_plate_type="smooth_pei",
        )
        power_on = AsyncMock(return_value=True)

        async with ctx.session_maker() as db:
            with (
                patch(
                    "backend.app.services.print_scheduler.printer_manager.is_connected",
                    MagicMock(return_value=False),
                ),
                patch.object(scheduler, "_power_on_and_wait", power_on),
                patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[MagicMock(auto_on=True)])),
            ):
                woken, attempted = await scheduler._wake_printer_for_model(
                    db,
                    [candidate],
                    None,
                    set(),
                    {1},
                    False,
                    plate_check_for=lambda c: lambda pid: "needs Smooth PEI" if pid == 1 else None,
                )

        assert (woken, attempted) == (None, None)
        power_on.assert_not_awaited()


class TestVariantResolution:
    def test_an_underived_variant_does_not_blank_the_items_plate_type(self):
        """With tracking off, variants are never derived. Resolving one must not
        wipe what the item already knows."""
        from types import SimpleNamespace

        variant = SimpleNamespace(
            library_file_id=5,
            library_file=None,
            target_model="X1C",
            plate_id=1,
            ams_mapping=None,
            nozzle_mapping=None,
            nozzle_rack_choice=None,
            filament_overrides=None,
            required_filament_types=None,
            required_plate_type=None,
            print_time_seconds=None,
        )
        item = SimpleNamespace(required_plate_type="textured_pei", print_time_seconds=None)
        candidate = _ModelCandidate(
            target_model="X1C",
            sliced_for="X1C",
            required_filament_types=None,
            filament_overrides=None,
            variant=variant,
        )

        PrintScheduler()._resolve_variant(item, candidate)

        assert item.required_plate_type == "textured_pei"

        variant.required_plate_type = "smooth_pei"
        PrintScheduler()._resolve_variant(item, candidate)
        assert item.required_plate_type == "smooth_pei"
