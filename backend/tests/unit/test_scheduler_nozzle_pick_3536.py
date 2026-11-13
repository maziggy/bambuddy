"""Nozzle-aware printer pick for "Any <model>" queue items (#3536).

Before #3536 the model-based pick looked at model, filament and state but not
at the nozzle: a file sliced for 0.6 mm could be assigned to a printer with a
0.4 mm nozzle and then fail in ``_start_print``. These tests cover the pieces
of the fix in order: the sliced diameter carried on each candidate, then the
printer filter built on it.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
from backend.app.core.database import Base
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import (
    PrintScheduler,
    _asks_nothing_new,
    _candidates_for,
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
