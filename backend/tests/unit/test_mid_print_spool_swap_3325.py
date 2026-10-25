"""A spool swapped by hand on one tray mid-print shares the print (#3325).

The reporter's P2S has no AMS: the external spool ran out, they loaded a new
roll and assigned it in Bambuddy while the print was paused, and the whole
print was charged to the new spool. The tray never changed (254 before and
after), so the tray-change split had nothing to work with; the assignment was
the only trace of the swap.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.active_print_session import ActivePrintSession
from backend.app.models.archive import PrintArchive
from backend.app.models.spool import Spool
from backend.app.services import spool_swaps
from backend.app.services.usage_tracker import _track_from_3mf
from backend.app.utils.threemf_tools import mm_to_grams

pytestmark = pytest.mark.unit

INV = spool_swaps.INVENTORY
EXT = (255, 0)  # the external spool, as assignments key it


def _log(*swaps, start=4, backend=INV, key=EXT):
    """A print that started with spool ``start`` on ``key`` and the given (spool, layer) swaps."""
    log = [spool_swaps.entry(backend, *key, start, 0, start=True)]
    log += [spool_swaps.entry(backend, *key, spool, layer) for spool, layer in swaps]
    return log


# 100 layers of filament 0, evenly: the whole print is 50 g of PLA, as the
# slicer says, so the G-code and the slicer agree.
LAYER_MM = 50.0 / mm_to_grams(1.0, 1.75, 1.24) / 100
LAYER_USAGE = {layer: {0: layer * LAYER_MM} for layer in range(1, 101)}


class TestSegments:
    def test_a_swap_after_the_start(self):
        assert spool_swaps.segments(_log((9, 60)), INV, *EXT) == [(4, 0), (9, 60)]

    def test_no_swap_is_no_split(self):
        assert spool_swaps.segments(_log(), INV, *EXT) == []

    def test_reassigning_the_same_spool_is_no_swap(self):
        assert spool_swaps.segments(_log((4, 30)), INV, *EXT) == []

    def test_a_tray_empty_at_the_start_is_not_split(self):
        # Assigning a spool mid-print to a tray that had none is catching up on
        # a forgotten assignment: the whole print goes to it, as before.
        log = [spool_swaps.entry(INV, *EXT, 9, 40)]
        assert spool_swaps.segments(log, INV, *EXT) == []

    def test_a_caught_up_assignment_covers_the_start_of_a_later_swap(self):
        # Spool 9 assigned late at layer 40, then swapped for 12 at 70: 9 fed
        # the print from its start, not from layer 40.
        log = [spool_swaps.entry(INV, *EXT, 9, 40), spool_swaps.entry(INV, *EXT, 12, 70)]
        assert spool_swaps.segments(log, INV, *EXT) == [(9, 0), (12, 70)]

    def test_other_trays_and_backends_are_ignored(self):
        log = _log((9, 60), key=(0, 1)) + _log((7, 20), backend=spool_swaps.SPOOLMAN)
        assert spool_swaps.segments(log, INV, *EXT) == []

    def test_swapping_back_is_a_segment_of_its_own(self):
        assert spool_swaps.segments(_log((9, 30), (4, 70)), INV, *EXT) == [(4, 0), (9, 30), (4, 70)]


class TestSplit:
    def test_by_gcode_extrusion(self):
        shares = spool_swaps.split([(4, 0), (9, 60)], 50.0, 0, LAYER_USAGE, 100)
        assert [(s, round(g, 6)) for s, g in shares] == [(4, 30.0), (9, 20.0)]

    def test_by_layer_count_without_gcode(self):
        shares = spool_swaps.split([(4, 0), (9, 25)], 40.0, 0, None, 100)
        assert [(s, round(g, 6)) for s, g in shares] == [(4, 10.0), (9, 30.0)]

    def test_shares_always_add_up_and_never_starve_the_last_spool(self):
        # Front-loaded G-code: nearly all filament before the swap. The new
        # spool still gets its proportional share, and the total holds.
        usage = {1: {0: 9000.0}, 50: {0: 9900.0}, 100: {0: 10000.0}}
        shares = dict(spool_swaps.split([(4, 0), (9, 50)], 12.0, 0, usage, 100))
        assert shares[9] > 0
        assert sum(shares.values()) == pytest.approx(12.0)

    def test_a_swap_back_adds_up_per_spool(self):
        shares = dict(spool_swaps.split([(4, 0), (9, 25), (4, 75)], 100.0, 0, None, 100))
        assert shares == pytest.approx({4: 50.0, 9: 50.0})

    def test_within_a_range(self):
        # An AMS-backup segment from layer 40 to 80, the spool swapped at 60.
        shares = spool_swaps.split([(4, 0), (9, 60)], 8.0, 0, LAYER_USAGE, 80, from_layer=40)
        assert [(s, round(g, 6)) for s, g in shares] == [(4, 4.0), (9, 4.0)]

    def test_a_swap_after_the_print_stopped_goes_nowhere(self):
        # A failed print stopped at layer 30; the spool was swapped at 60.
        assert spool_swaps.split([(4, 0), (9, 60)], 5.0, 0, LAYER_USAGE, 30) == [(4, 5.0)]

    def test_nothing_to_weigh_by_goes_to_the_last_spool(self):
        assert spool_swaps.split([(4, 0), (9, 0)], 5.0, 0, None, 0) == [(9, 5.0)]


class TestRecordAssignment:
    async def _record(self, db_session, printer_id, state, spool_id=9):
        with patch("backend.app.services.printer_manager.printer_manager.get_status", return_value=state):
            await spool_swaps.record_assignment(db_session, printer_id, *EXT, spool_id, INV)
        await db_session.commit()

    @pytest.mark.asyncio
    async def test_a_swap_while_printing_is_logged_at_the_current_layer(self, db_session, printer_factory):
        printer = await printer_factory()
        db_session.add(ActivePrintSession(printer_id=printer.id, started_at=datetime.now(), spool_change_log=_log()))
        await db_session.commit()
        state = SimpleNamespace(layer_num=57, spool_change_log=_log())

        await self._record(db_session, printer.id, state)

        assert spool_swaps.segments(state.spool_change_log, INV, *EXT) == [(4, 0), (9, 57)]
        row = await db_session.get(ActivePrintSession, printer.id)
        await db_session.refresh(row)
        assert row.spool_change_log == state.spool_change_log

    @pytest.mark.asyncio
    async def test_nothing_is_logged_without_a_print(self, db_session, printer_factory):
        printer = await printer_factory()
        state = SimpleNamespace(layer_num=0, spool_change_log=[])

        await self._record(db_session, printer.id, state)

        assert state.spool_change_log == []

    @pytest.mark.asyncio
    async def test_an_assignment_that_rolls_back_is_not_logged(self, db_session, printer_factory):
        printer = await printer_factory()
        db_session.add(ActivePrintSession(printer_id=printer.id, started_at=datetime.now(), spool_change_log=_log()))
        await db_session.commit()
        state = SimpleNamespace(layer_num=57, spool_change_log=_log())

        with patch("backend.app.services.printer_manager.printer_manager.get_status", return_value=state):
            await spool_swaps.record_assignment(db_session, printer.id, *EXT, 9, INV)
        await db_session.rollback()

        assert state.spool_change_log == _log()

    @pytest.mark.asyncio
    async def test_after_a_restart_the_start_spool_is_kept(self, db_session, printer_factory):
        # Bambuddy restarted during the runout pause: the state's log is empty
        # until the printer reports RUNNING, the row still has the start spool.
        printer = await printer_factory()
        db_session.add(ActivePrintSession(printer_id=printer.id, started_at=datetime.now(), spool_change_log=_log()))
        await db_session.commit()
        state = SimpleNamespace(layer_num=57, spool_change_log=[])

        await self._record(db_session, printer.id, state)

        assert spool_swaps.segments(state.spool_change_log, INV, *EXT) == [(4, 0), (9, 57)]
        row = await db_session.get(ActivePrintSession, printer.id)
        await db_session.refresh(row)
        assert row.spool_change_log == state.spool_change_log

    @pytest.mark.asyncio
    async def test_the_same_spool_again_is_not_logged(self, db_session, printer_factory):
        printer = await printer_factory()
        db_session.add(ActivePrintSession(printer_id=printer.id, started_at=datetime.now()))
        await db_session.commit()
        state = SimpleNamespace(layer_num=20, spool_change_log=_log())

        await self._record(db_session, printer.id, state, spool_id=4)

        assert state.spool_change_log == _log()


# --- The built-in inventory -------------------------------------------------


def _spool(spool_id: int):
    spool = MagicMock()
    spool.id = spool_id
    spool.label_weight = 1000
    spool.weight_used = 0
    spool.cost_per_kg = None
    spool.material = "PLA"
    spool.rgba = None
    return spool


def _db(spools: dict[int, MagicMock]):
    archive = MagicMock()
    archive.id = 7
    archive.file_path = "archives/7/test.3mf"
    archive.extra_data = None
    archive.plate_id = None

    async def execute(stmt, *args, **kwargs):
        entity = stmt.column_descriptions[0].get("entity")
        result = MagicMock()
        value = None
        if entity is PrintArchive:
            value = archive
        elif entity is Spool:
            value = spools.get(stmt.whereclause.right.value)
        result.scalar_one_or_none.return_value = value
        result.scalars.return_value.first.return_value = None
        result.scalar.return_value = None
        return result

    db = AsyncMock()
    db.execute = execute
    db.add = MagicMock()
    return db


async def _track(
    spool_change_log, status="completed", layer_num=100, tray_change_log=None, last_layer_num=0, total_layers=100
):
    printer_manager = MagicMock()
    printer_manager.get_status.return_value = SimpleNamespace(
        progress=100 if status == "completed" else 40,
        layer_num=layer_num,
        total_layers=total_layers,
        tray_now=254,
        last_loaded_tray=254,
        tray_change_log=tray_change_log or [],
        spool_change_log=spool_change_log,
        raw_data={"ams": [], "vt_tray": [{"id": "254", "tray_type": "PLA"}]},
    )

    async def resolve(printer_id, ams_id, tray_id, **kwargs):
        # AMS slot 0 holds spool 20; the external spool's live assignment is
        # spool 9, the one loaded at the end.
        return 20 if (ams_id, tray_id) == (0, 0) else 9

    charged: list[tuple[int, float]] = []

    async def charge(db, spool, grams):
        charged.append((spool.id, round(grams, 2)))
        return True

    with (
        patch("backend.app.core.config.settings") as mock_settings,
        patch(
            "backend.app.utils.threemf_tools.extract_filament_usage_from_3mf",
            return_value=[{"slot_id": 1, "used_g": 50.0, "type": "PLA", "color": "#000000"}],
        ),
        patch("backend.app.utils.threemf_tools.extract_layer_filament_usage_from_3mf", return_value=LAYER_USAGE),
        patch("backend.app.utils.threemf_tools.extract_filament_properties_from_3mf", return_value={}),
        patch("backend.app.services.usage_tracker._resolve_spool_id_for_tray", side_effect=resolve),
        patch("backend.app.services.usage_tracker._charge_spool", side_effect=charge),
    ):
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_settings.base_dir = MagicMock()
        mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)
        results = await _track_from_3mf(
            printer_id=1,
            archive_id=7,
            status=status,
            print_name="swap",
            handled_trays=set(),
            printer_manager=printer_manager,
            db=_db({4: _spool(4), 9: _spool(9), 20: _spool(20)}),
            ams_mapping=[254],
            last_layer_num=last_layer_num,
            print_started_at=datetime.now(timezone.utc),
        )
    return results, charged


class TestBuiltInInventory:
    @pytest.mark.asyncio
    async def test_the_print_is_split_between_the_two_spools(self):
        results, charged = await _track(_log((9, 60)))
        assert charged == [(4, 30.0), (9, 20.0)]
        assert [r["spool_id"] for r in results] == [4, 9]

    @pytest.mark.asyncio
    async def test_a_printer_that_forgot_its_layer_count_still_splits(self):
        # P1S firmware zeroes total_layer_num at print end; the G-code's own
        # last layer bounds the split instead.
        _, charged = await _track(_log((9, 60)), layer_num=0, total_layers=0)
        assert charged == [(4, 30.0), (9, 20.0)]

    @pytest.mark.asyncio
    async def test_without_a_swap_the_live_spool_takes_it_all(self):
        _, charged = await _track(_log())
        assert charged == [(9, 50.0)]

    @pytest.mark.asyncio
    async def test_a_failed_print_splits_up_to_where_it_stopped(self):
        # Stopped at layer 80 of 100: 40 g of the slot's 50 g by the G-code,
        # the swap at 60 splits it 30 / 10.
        _, charged = await _track(_log((9, 60)), status="failed", layer_num=0, last_layer_num=80)
        assert charged == [(4, 30.0), (9, 10.0)]

    @pytest.mark.asyncio
    async def test_an_ams_backup_segment_is_split_too(self):
        # AMS backup moved the print from AMS slot 0 to the external spool at
        # layer 40, and that spool was swapped by hand at 60.
        _, charged = await _track(_log((9, 60)), tray_change_log=[(0, 0), (254, 40)])
        assert charged == [(20, 20.0), (4, 10.0), (9, 20.0)]


# --- Spoolman ---------------------------------------------------------------


def _client():
    client = MagicMock()
    client.find_spool_by_tag = AsyncMock(return_value=None)
    client.use_spool = AsyncMock()
    client.get_spool = AsyncMock(side_effect=lambda spool_id: {"id": spool_id, "price": 20.0, "initial_weight": 1000})
    return client


def _splitter(log, end_layer=100, completed=True):
    return spool_swaps.Splitter(log, spool_swaps.SPOOLMAN, LAYER_USAGE, end_layer, completed=completed)


class TestSpoolman:
    @pytest.mark.asyncio
    async def test_the_print_is_split_between_the_two_spools(self):
        from backend.app.services.spoolman_tracking import _PrintCost, _report_spool_usage_for_slots

        client = _client()
        cost = _PrintCost()
        with patch(
            "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment", AsyncMock(return_value=9)
        ):
            updated = await _report_spool_usage_for_slots(
                client,
                [(1, 50.0)],
                {254: {"tray_type": "PLA"}},
                [254],
                "Archive 7",
                "SERIAL",
                printer_id=1,
                cost_out=cost,
                swaps=_splitter(_log((9, 60), backend=spool_swaps.SPOOLMAN)),
            )

        assert updated == 2
        assert [c.args for c in client.use_spool.await_args_list] == [(4, 30.0), (9, 20.0)]
        assert cost.priced_grams == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_without_a_swap_nothing_changes(self):
        from backend.app.services.spoolman_tracking import _report_spool_usage_for_slots

        client = _client()
        with patch(
            "backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment", AsyncMock(return_value=9)
        ):
            await _report_spool_usage_for_slots(
                client,
                [(1, 50.0)],
                {254: {"tray_type": "PLA"}},
                [254],
                "Archive 7",
                "SERIAL",
                printer_id=1,
                swaps=_splitter(_log(backend=spool_swaps.SPOOLMAN)),
            )

        assert [c.args for c in client.use_spool.await_args_list] == [(9, 50.0)]

    def test_a_multi_colour_print_without_gcode_layers_splits_by_layer_count(self):
        # report_usage reads the printer's layers for single-slot prints only.
        from backend.app.services.spoolman_tracking import _swap_splitter

        log = _log((9, 25), backend=spool_swaps.SPOOLMAN)
        state = SimpleNamespace(total_layers=100, layer_num=100, spool_change_log=log)
        with patch("backend.app.services.printer_manager.printer_manager.get_status", return_value=state):
            swaps = _swap_splitter(1, None, None, 0, completed=True)

        assert swaps.shares(*EXT, 1, 40.0) == pytest.approx([(4, 10.0), (9, 30.0)])

    @pytest.mark.asyncio
    async def test_a_tray_change_segment_is_split_too(self):
        from backend.app.services.spoolman_tracking import _report_spool_usage_split_by_tray_changes

        client = _client()

        async def slot(printer_id, ams_id, tray_id):
            return 9 if ams_id == 255 else 20

        with patch("backend.app.services.spoolman_tracking._resolve_spool_id_via_slot_assignment", side_effect=slot):
            await _report_spool_usage_split_by_tray_changes(
                client,
                [{"slot_id": 1, "used_g": 50.0}],
                [(0, 0), (254, 40)],
                {0: {"tray_type": "PLA"}, 254: {"tray_type": "PLA"}},
                LAYER_USAGE,
                {},
                100,
                100,
                "Archive 7",
                "SERIAL",
                printer_id=1,
                swaps=_splitter(_log((9, 60), backend=spool_swaps.SPOOLMAN)),
            )

        charged = [c.args for c in client.use_spool.await_args_list]
        assert charged[0][0] == 20
        assert charged[1:] == [(4, 10.0), (9, 20.0)]


# --- Print start and restart --------------------------------------------------


class TestPrintStartAndRestart:
    def _state(self):
        state = MagicMock()
        state.raw_data = {"ams": [], "vt_tray": [{"id": "254", "tray_type": "PLA"}]}
        state.tray_now = 254
        state.tray_change_log = [(254, 0)]
        state.spool_change_log = []
        return state

    @pytest.mark.asyncio
    @pytest.mark.parametrize("spoolman", [False, True])
    async def test_print_start_logs_the_spool_each_tray_starts_with(self, db_session, printer_factory, spoolman):
        from backend.app.models.spool_assignment import SpoolAssignment
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
        from backend.app.services.usage_tracker import _active_sessions, on_print_start

        printer = await printer_factory()
        spool = Spool(material="PLA", label_weight=1000, weight_used=0)
        db_session.add(spool)
        await db_session.commit()
        db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=255, tray_id=0))
        db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=255, tray_id=0, spoolman_spool_id=77))
        await db_session.commit()
        state = self._state()
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = state

        await on_print_start(
            printer.id, {"subtask_name": "swap"}, printer_manager, db=db_session, spoolman_owns_usage=spoolman
        )
        _active_sessions.pop(printer.id, None)

        backend, spool_id = (spool_swaps.SPOOLMAN, 77) if spoolman else (INV, spool.id)
        assert state.spool_change_log == [spool_swaps.entry(backend, 255, 0, spool_id, 0, start=True)]
        row = await db_session.get(ActivePrintSession, printer.id)
        assert row.spool_change_log == state.spool_change_log

    @pytest.mark.asyncio
    async def test_a_restart_puts_the_log_back(self, db_session, printer_factory):
        from backend.app.main import _restore_usage_tracking_session

        printer = await printer_factory()
        before_restart = _log((9, 40))
        db_session.add(
            ActivePrintSession(
                printer_id=printer.id,
                print_name="swap",
                started_at=datetime.now(),
                spool_change_log=before_restart,
            )
        )
        await db_session.commit()
        since = spool_swaps.entry(INV, *EXT, 12, 70)
        state = SimpleNamespace(
            subtask_name="swap",
            tray_change_log=[],
            tray_now=254,
            layer_num=75,
            last_loaded_tray=254,
            print_ams_mapping=None,
            spool_change_log=[since],
        )

        await _restore_usage_tracking_session(printer.id, state, db_session, MagicMock())

        assert state.spool_change_log == before_restart + [since]
        assert spool_swaps.segments(state.spool_change_log, INV, *EXT) == [(4, 0), (9, 40), (12, 70)]


class TestBuiltInRemainDelta:
    """Without a 3MF the built-in tracker charges a tray's remain% drop. Across a
    swap the start and end readings belong to two different spools."""

    @pytest.fixture(autouse=True)
    def _clean(self):
        from backend.app.services.usage_tracker import _active_sessions

        _active_sessions.clear()
        with patch("backend.app.api.routes.settings.get_setting", new_callable=AsyncMock, return_value=None):
            yield
        _active_sessions.clear()

    async def _complete(self, spool_change_log):
        from backend.app.services.usage_tracker import PrintSession, _active_sessions, on_print_complete

        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Test",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(0, 0): 80},
        )
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 70}]}]},
            tray_now=0,
            last_loaded_tray=-1,
            spool_change_log=spool_change_log,
        )
        assignment = MagicMock(spool_id=2, printer_id=1, ams_id=0, tray_id=0)
        spool = _spool(2)
        responses = iter([None, None, assignment, spool])

        async def execute(*args, **kwargs):
            result = MagicMock()
            value = next(responses, None)
            result.scalar_one_or_none.return_value = value
            result.scalars.return_value.first.return_value = value
            result.scalar.return_value = None
            return result

        db = AsyncMock()
        db.execute = execute
        db.add = MagicMock()
        return await on_print_complete(
            printer_id=1, data={"status": "completed"}, printer_manager=printer_manager, db=db, archive_id=None
        )

    @pytest.mark.asyncio
    async def test_a_swapped_tray_is_not_charged_its_remain_drop(self):
        assert await self._complete(_log((2, 40), start=5, key=(0, 0))) == []

    @pytest.mark.asyncio
    async def test_an_assignment_corrected_before_the_first_layer_is_still_charged(self):
        results = await self._complete(_log((2, 0), start=5, key=(0, 0)))
        assert [(r["spool_id"], r["weight_used"]) for r in results] == [(2, 100.0)]

    @pytest.mark.asyncio
    async def test_a_tray_that_kept_its_spool_still_is(self):
        results = await self._complete(_log(start=2, key=(0, 0)))
        assert [(r["spool_id"], r["weight_used"]) for r in results] == [(2, 100.0)]
