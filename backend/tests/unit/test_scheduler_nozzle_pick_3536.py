"""Nozzle-aware printer pick for "Any <model>" queue items (#3536).

Before #3536 the model-based pick looked at model, filament and state but not
at the nozzle: a file sliced for 0.6 mm could be assigned to a printer with a
0.4 mm nozzle and then fail in ``_start_print``. These tests cover the pieces
of the fix in order: the sliced diameter carried on each candidate, then the
printer filter built on it.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base
from backend.app.core.printer_scope import ALL_PRINTERS
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import (
    PrintScheduler,
    _asks_nothing_new,
    _candidates_for,
    _ModelCandidate,
    _sliced_nozzle,
    _wrong_nozzle_label,
)

# --- Task 1: sliced diameter per candidate ---------------------------------


def _item(*, variants=None, archive=None, library_file=None):
    return SimpleNamespace(
        variants=variants or [],
        archive=archive,
        archive_id=1 if archive is not None else None,
        library_file=library_file,
        library_file_id=2 if library_file is not None else None,
        target_model="A1",
        required_filament_types=None,
        filament_overrides=None,
    )


def _variant(vid, nozzle):
    return SimpleNamespace(
        id=vid,
        position=vid,
        attempt_count=0,
        target_model="A1",
        required_filament_types=None,
        filament_overrides=None,
        library_file=SimpleNamespace(deleted_at=None, file_metadata={"nozzle_diameter": nozzle}),
    )


def test_sliced_nozzle_from_archive():
    assert _sliced_nozzle(SimpleNamespace(nozzle_diameter=0.6), None) == 0.6


def test_sliced_nozzle_from_library_metadata():
    assert _sliced_nozzle(None, SimpleNamespace(file_metadata={"nozzle_diameter": 0.6})) == 0.6


def test_sliced_nozzle_unknown():
    assert _sliced_nozzle(None, SimpleNamespace(file_metadata=None)) is None
    assert _sliced_nozzle(None, SimpleNamespace(file_metadata={})) is None
    assert _sliced_nozzle(None, None) is None


def test_sliced_nozzle_rejects_garbage_and_non_positive():
    assert _sliced_nozzle(SimpleNamespace(nozzle_diameter=0), None) is None
    assert _sliced_nozzle(SimpleNamespace(nozzle_diameter=-0.4), None) is None
    assert _sliced_nozzle(None, SimpleNamespace(file_metadata={"nozzle_diameter": "abc"})) is None
    assert _sliced_nozzle(None, SimpleNamespace(file_metadata={"nozzle_diameter": [0.4]})) is None
    assert _sliced_nozzle(None, SimpleNamespace(file_metadata={"nozzle_diameter": "0.6"})) == 0.6


def test_sliced_nozzle_is_read_without_rounding():
    # The tolerance lives in _nozzle_mismatch_message, not here.
    meta = SimpleNamespace(file_metadata={"nozzle_diameter": "0.6000001"})
    assert _sliced_nozzle(None, meta) == 0.6000001


def test_candidates_carry_their_own_nozzle():
    single = _item(library_file=SimpleNamespace(file_metadata={"nozzle_diameter": 0.6}))
    assert [c.sliced_nozzle for c in _candidates_for(single)] == [0.6]

    multi = _item(variants=[_variant(1, 0.4), _variant(2, 0.6)])
    assert sorted(c.sliced_nozzle for c in _candidates_for(multi)) == [0.4, 0.6]


# --- Task 2: does this nozzle fit? ------------------------------------------


def _state(*diameters: str):
    """PrinterState-shaped namespace with the given nozzle diameter strings."""
    return SimpleNamespace(nozzles=[SimpleNamespace(nozzle_diameter=d) for d in diameters])


def _h2c(*rack: tuple[int, str], mounted: str = "0.4"):
    """H2C-shaped state: one mounted hotend plus docked nozzles (#2885 shape)."""
    info = [{"id": 0, "diameter": mounted, "wear": 0, "max_temp": 350, "serial_number": "SN0"}]
    info += [
        {"id": nozzle_id, "diameter": d, "wear": 0, "max_temp": 350, "serial_number": f"SN{nozzle_id}"}
        for nozzle_id, d in rack
    ]
    return SimpleNamespace(nozzles=[SimpleNamespace(nozzle_diameter=mounted)], nozzle_rack=info)


def test_wrong_nozzle_label_single_and_dual():
    assert _wrong_nozzle_label("A1-1", _state("0.4"), 0.6) == "A1-1 (0.4mm)"
    assert _wrong_nozzle_label("A1-1", _state("0.6"), 0.6) is None
    assert _wrong_nozzle_label("H2D-1", _state("0.4", "0.4"), 0.6) == "H2D-1 (0.4mm)"
    assert _wrong_nozzle_label("H2D-1", _state("0.4", "0.6"), 0.6) is None


def test_wrong_nozzle_label_unknown_fits():
    assert _wrong_nozzle_label("A1-1", _state("0.4"), None) is None
    assert _wrong_nozzle_label("A1-1", _state("0.4"), 0) is None
    assert _wrong_nozzle_label("A1-1", None, 0.6) is None
    assert _wrong_nozzle_label("A1-1", _state(""), 0.6) is None  # nothing reported


def test_wrong_nozzle_label_rack():
    assert _wrong_nozzle_label("H2C-1", _h2c((16, "0.6")), 0.6) is None
    assert _wrong_nozzle_label("H2C-1", _h2c((16, "0.2"), (18, "0.8")), 0.6) == "H2C-1 (0.4mm, rack 0.2mm / 0.8mm)"


def test_wrong_nozzle_label_rack_without_mounted_nozzle():
    status = SimpleNamespace(
        nozzles=[SimpleNamespace(nozzle_diameter="")],
        nozzle_rack=[{"id": 16, "diameter": "0.2", "max_temp": 350, "serial_number": "SN16"}],
    )
    assert _wrong_nozzle_label("H2C-1", status, 0.6) == "H2C-1 (no nozzle mounted, rack 0.2mm)"


SIZES = ["0.2", "0.4", "0.6", "0.8"]


@pytest.mark.parametrize("sliced", SIZES)
@pytest.mark.parametrize("installed", SIZES)
def test_only_the_same_size_fits(sliced, installed):
    # Same size fits, every other (also the neighbouring one) never does.
    label = _wrong_nozzle_label("A1-1", _state(installed), float(sliced))
    if sliced == installed:
        assert label is None
    else:
        assert label == f"A1-1 ({installed}mm)"


def test_float_noise_from_metadata_still_fits():
    assert _wrong_nozzle_label("A1-1", _state("0.6"), 0.6000001) is None
    assert _wrong_nozzle_label("A1-1", _state("0.4"), 0.6000001) == "A1-1 (0.4mm)"


# --- Task 3: the matcher passes over a printer with the wrong nozzle --------


@pytest.fixture
async def a1_farm():
    """Two A1s in "Workshop"; A1-1 is the one with fewer print hours."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as db:
        db.add_all(
            [
                Printer(
                    id=pid,
                    name=f"A1-{pid}",
                    serial_number=f"A1000{pid}",
                    ip_address=f"192.0.2.{pid}",
                    access_code="x",
                    model="A1",
                    is_active=True,
                    location="Workshop",
                    runtime_seconds=pid * 3600,
                )
                for pid in (1, 2)
            ]
        )
        await db.commit()
    try:
        yield SimpleNamespace(session_maker=session_maker)
    finally:
        await engine.dispose()


async def _find(ctx, scheduler, states, *, exclude=frozenset(), connected=True, idle=None, **kwargs):
    """Run the matcher; ``states`` maps printer id to its reported state (or None)."""
    idle = idle or {}
    with (
        patch("backend.app.services.print_scheduler.printer_manager.get_status", lambda pid: states.get(pid)),
        patch(
            "backend.app.services.print_scheduler.printer_manager.is_connected",
            lambda pid: connected if not callable(connected) else connected(pid),
        ),
        patch.object(scheduler, "_is_printer_idle", lambda pid, *_a, **_k: idle.get(pid, True)),
    ):
        async with ctx.session_maker() as db:
            return await scheduler._find_idle_printer_for_model(db, "A1", set(exclude), **kwargs)


class TestMatcherNozzle:
    @pytest.mark.asyncio
    async def test_skips_the_printer_with_the_wrong_nozzle(self, a1_farm):
        states = {1: _state("0.4"), 2: _state("0.6")}
        assert await _find(a1_farm, PrintScheduler(), states, sliced_nozzle=0.6) == (2, None)

    @pytest.mark.asyncio
    async def test_no_printer_fits_names_every_nozzle(self, a1_farm):
        states = {1: _state("0.4"), 2: _state("0.4")}
        assert await _find(a1_farm, PrintScheduler(), states, sliced_nozzle=0.6) == (
            None,
            "No A1 has a 0.6mm nozzle: A1-1 (0.4mm), A1-2 (0.4mm)",
        )

    @pytest.mark.asyncio
    async def test_reason_names_the_location(self, a1_farm):
        states = {1: _state("0.4"), 2: _state("0.4")}
        _, reason = await _find(a1_farm, PrintScheduler(), states, sliced_nozzle=0.6, target_location="Workshop")
        assert reason.startswith("No A1 in Workshop has a 0.6mm nozzle: ")

    @pytest.mark.asyncio
    async def test_float_noise_in_the_sliced_size_is_not_shown(self, a1_farm):
        states = {1: _state("0.4"), 2: _state("0.4")}
        _, reason = await _find(a1_farm, PrintScheduler(), states, sliced_nozzle=0.6000001)
        assert reason.startswith("No A1 has a 0.6mm nozzle: ")

    @pytest.mark.asyncio
    async def test_unknown_sliced_nozzle_behaves_as_before(self, a1_farm):
        states = {1: _state("0.4"), 2: _state("0.6")}
        assert await _find(a1_farm, PrintScheduler(), states, sliced_nozzle=None) == (1, None)

    @pytest.mark.asyncio
    async def test_printer_without_status_fits(self, a1_farm):
        states = {1: None, 2: _state("0.4")}
        assert await _find(a1_farm, PrintScheduler(), states, sliced_nozzle=0.6) == (1, None)

    @pytest.mark.asyncio
    async def test_wrong_nozzle_printer_does_not_hide_a_busy_matching_one(self, a1_farm):
        """The only printer that fits is busy: the job just waits, silently."""
        states = {1: _state("0.4"), 2: _state("0.6")}
        pick, reason = await _find(a1_farm, PrintScheduler(), states, exclude={2}, sliced_nozzle=0.6)
        assert pick is None
        assert reason == "Busy: A1-2"
        assert PrintScheduler._is_busy_only(reason) is True

    @pytest.mark.asyncio
    async def test_busy_or_offline_wrong_nozzle_printers_read_as_nozzle_not_busy(self, a1_farm):
        """A1-1 is printing, A1-2 offline, both 0.4: waiting would never help."""
        states = {1: _state("0.4"), 2: _state("0.4")}
        pick, reason = await _find(
            a1_farm,
            PrintScheduler(),
            states,
            connected=lambda pid: pid == 1,
            idle={1: False},
            sliced_nozzle=0.6,
        )
        assert pick is None
        assert reason == "No A1 has a 0.6mm nozzle: A1-1 (0.4mm), A1-2 (0.4mm)"

    @pytest.mark.asyncio
    async def test_excluded_wrong_nozzle_printer_is_not_listed_as_busy(self, a1_farm):
        states = {1: _state("0.4"), 2: _state("0.4")}
        _, reason = await _find(a1_farm, PrintScheduler(), states, exclude={1, 2}, sliced_nozzle=0.6)
        assert reason.startswith("No A1 has a 0.6mm nozzle: ")

    @pytest.mark.asyncio
    async def test_nozzle_reason_is_not_busy_only_and_asks_something(self, a1_farm):
        states = {1: _state("0.4"), 2: _state("0.4")}
        _, reason = await _find(a1_farm, PrintScheduler(), states, sliced_nozzle=0.6)
        assert PrintScheduler._is_busy_only(reason) is False
        assert _asks_nothing_new(reason) is False


# --- Task 4: the wake path does not power on a printer with the wrong nozzle --


class TestWakeNozzle:
    async def _wake(self, ctx, sliced_nozzle):
        scheduler = PrintScheduler()
        plug = SimpleNamespace(id=1, auto_on=True, enabled=True, controls_printer_power=True)
        power_on = AsyncMock(return_value=True)
        # Both printers are off but keep their last status (A1-1 0.4, A1-2 0.6).
        states = {1: _state("0.4"), 2: _state("0.6")}
        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=False)),
            patch(
                "backend.app.services.print_scheduler.printer_manager.is_awaiting_plate_clear",
                MagicMock(return_value=False),
            ),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", lambda pid: states.get(pid)),
            patch.object(scheduler, "_cached_filament_shortfall", MagicMock(return_value=[])),
            patch.object(scheduler, "_get_smart_plugs", AsyncMock(return_value=[plug])),
            patch.object(scheduler, "_power_on_and_wait", power_on),
        ):
            async with ctx.session_maker() as db:
                return await scheduler._wake_printer_for_model(
                    db,
                    [
                        _ModelCandidate(
                            target_model="A1",
                            sliced_for=None,
                            required_filament_types=None,
                            filament_overrides=None,
                            sliced_nozzle=sliced_nozzle,
                        )
                    ],
                    None,
                    set(),
                    {1, 2},
                    require_plate_clear=False,
                )

    @pytest.mark.asyncio
    async def test_wakes_the_printer_whose_nozzle_fits(self, a1_farm):
        assert await self._wake(a1_farm, 0.6) == (2, 2)

    @pytest.mark.asyncio
    async def test_unknown_sliced_nozzle_wakes_the_least_worn_as_before(self, a1_farm):
        assert await self._wake(a1_farm, None) == (1, 1)


# --- Task 5: end to end through the queue pass -------------------------------


async def _add_any_a1(ctx, nozzle, **fields):
    async with ctx.session_maker() as db:
        lib = LibraryFile(
            filename="part-a.gcode.3mf",
            file_path="/library/part-a.gcode.3mf",
            file_size=10,
            file_type="gcode.3mf",
            file_metadata={"nozzle_diameter": nozzle},
        )
        db.add(lib)
        await db.flush()
        values = {"status": "pending", "position": 1, "target_model": "A1", "library_file_id": lib.id}
        values.update(fields)
        item = PrintQueueItem(**values)
        db.add(item)
        await db.commit()
        return item.id


async def _load(db, item_id):
    return (
        await db.execute(
            select(PrintQueueItem)
            .where(PrintQueueItem.id == item_id)
            .options(
                selectinload(PrintQueueItem.library_file),
                selectinload(PrintQueueItem.archive),
                selectinload(PrintQueueItem.variants),
            )
        )
    ).scalar_one()


async def _get_item(ctx, item_id):
    async with ctx.session_maker() as db:
        return await _load(db, item_id)


class TestQueuePassNozzle:
    async def _pass(self, ctx, states):
        scheduler = PrintScheduler()
        launched = MagicMock()
        dispatched = AsyncMock()
        patches = [
            patch("backend.app.services.print_scheduler.async_session", ctx.session_maker),
            patch("backend.app.core.database.async_session", ctx.session_maker),
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", lambda pid: states.get(pid)),
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
            patch.object(scheduler, "_unmatched_on", AsyncMock(return_value=[])),
            patch.object(scheduler, "_launch_uploads", launched),
            patch.object(scheduler, "_dispatch_one", dispatched),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            await scheduler.check_queue()
        return launched, dispatched

    @pytest.mark.asyncio
    async def test_waits_with_the_nozzle_reason_when_no_printer_fits(self, a1_farm):
        item_id = await _add_any_a1(a1_farm, 0.6)
        launched, dispatched = await self._pass(a1_farm, {1: _state("0.4"), 2: _state("0.4")})

        item = await _get_item(a1_farm, item_id)
        assert item.status == "pending"
        assert item.printer_id is None
        assert item.waiting_reason == "No A1 has a 0.6mm nozzle: A1-1 (0.4mm), A1-2 (0.4mm)"
        launched.assert_not_called()
        dispatched.assert_not_called()

    @pytest.mark.asyncio
    async def test_goes_to_the_printer_whose_nozzle_fits(self, a1_farm):
        item_id = await _add_any_a1(a1_farm, 0.6)
        launched, _ = await self._pass(a1_farm, {1: _state("0.4"), 2: _state("0.6")})

        launched.assert_called_once()
        assert launched.call_args[0][0] == [item_id]
        assert launched.call_args[0][1] == {item_id: 2}
        assert (await _get_item(a1_farm, item_id)).printer_id == 2


class TestShortHoldNozzle:
    """A job held for low filament on A1-1 only moves to a printer whose nozzle fits.

    Without the nozzle the move would pin it on a 0.4 mm A1, where the
    dispatch guard (#1899) then fails it.
    """

    async def _release(self, ctx, states):
        item_id = await _add_any_a1(ctx, 0.6, printer_id=1, manual_start=True, filament_short=True)
        scheduler = PrintScheduler()

        async def short_on(db, item, printer_id, *, require_known=False):
            return printer_id == 1  # A1-2 has enough filament

        with (
            patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
            patch("backend.app.services.print_scheduler.printer_manager.get_status", lambda pid: states.get(pid)),
            patch.object(scheduler, "_filament_short_on", AsyncMock(side_effect=short_on)),
            patch.object(scheduler, "_unmatched_on", AsyncMock(return_value=[])),
            patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        ):
            async with ctx.session_maker() as db:
                item = await _load(db, item_id)
                moved = await scheduler._release_short_model_hold(db, item, set(), False, ALL_PRINTERS)
                return moved, item.printer_id

    @pytest.mark.asyncio
    async def test_not_moved_to_a_printer_with_the_wrong_nozzle(self, a1_farm):
        assert await self._release(a1_farm, {1: _state("0.6"), 2: _state("0.4")}) == (False, 1)

    @pytest.mark.asyncio
    async def test_moved_to_a_printer_whose_nozzle_fits(self, a1_farm):
        assert await self._release(a1_farm, {1: _state("0.6"), 2: _state("0.6")}) == (True, 2)


# --- Auto-drying: which printers an "Any model" job counts for ---------------


class TestDryingPrinterIds:
    """An "Any A1" job that no 0.4 mm A1 can print must not block or trigger drying there."""

    async def _ids(self, ctx, nozzle, states):
        item_id = await _add_any_a1(ctx, nozzle)
        with patch("backend.app.services.print_scheduler.printer_manager.get_status", lambda pid: states.get(pid)):
            async with ctx.session_maker() as db:
                item = await _load(db, item_id)
                return await PrintScheduler()._model_job_printer_ids(db, item, ALL_PRINTERS, {})

    @pytest.mark.asyncio
    async def test_only_printers_whose_nozzle_fits_count(self, a1_farm):
        assert await self._ids(a1_farm, 0.6, {1: _state("0.4"), 2: _state("0.6")}) == {2}

    @pytest.mark.asyncio
    async def test_unknown_sliced_nozzle_counts_every_printer(self, a1_farm):
        assert await self._ids(a1_farm, None, {1: _state("0.4"), 2: _state("0.6")}) == {1, 2}
