"""Filament attribution has to survive a restart mid-print.

A 14-hour print that spans a Bambuddy restart used to lose everything the
completion path needs: the plate (so the 3MF parser summed every plate of a
multi-plate file), the dispatched slot-to-tray mapping (so it fell back to the
live MQTT ``mapping`` field, which AMS filament backup rewrites to the
substitute tray), the spool-assignment snapshot, and the tray-change log that
splits weight across a runout. The whole print was then charged to whichever
spool happened to finish it, while the spool that actually ran dry was charged
nothing.

These tests cover the durable ``active_print_sessions`` row that fixes that,
plus the plate and mapping fallbacks the completion path now applies.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from backend.app.models.active_print_session import ActivePrintSession
from backend.app.models.printer import Printer
from backend.app.services.usage_tracker import (
    PrintSession,
    _active_sessions,
    _track_from_3mf,
    clear_persisted_session,
    get_persisted_print_name,
    on_print_complete,
    persist_session,
    record_tray_change,
    restore_session,
)


def _make_spool(spool_id=1, label_weight=1000, weight_used=0):
    spool = MagicMock()
    spool.id = spool_id
    spool.label_weight = label_weight
    spool.weight_used = weight_used
    spool.tag_uid = None
    spool.tray_uuid = None
    spool.last_used = None
    spool.cost_per_kg = None
    spool.material = "ABS"
    spool.rgba = "616777FF"
    return spool


def _make_assignment(spool_id=1, ams_id=0, tray_id=0):
    assignment = MagicMock()
    assignment.spool_id = spool_id
    assignment.printer_id = 1
    assignment.ams_id = ams_id
    assignment.tray_id = tray_id
    assignment.created_at = None
    return assignment


def _make_archive(archive_id=1, plate_id=None, file_path="archives/1/multi_plate.3mf"):
    archive = MagicMock()
    archive.id = archive_id
    archive.file_path = file_path
    archive.plate_id = plate_id
    archive.extra_data = None
    return archive


def _make_queue_item(item_id=629, ams_mapping=None, plate_id=None):
    item = MagicMock()
    item.id = item_id
    item.ams_mapping = ams_mapping
    item.plate_id = plate_id
    item.status = "printing"
    return item


def _mock_db_sequential(responses):
    """Mock db whose execute() yields the given rows in order."""
    db = AsyncMock()
    call_count = [0]

    async def mock_execute(*args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        result = MagicMock()
        value = responses[idx] if idx < len(responses) else None
        result.scalar_one_or_none.return_value = value
        result.scalars.return_value.first.return_value = value
        result.scalar.return_value = None
        return result

    db.execute = mock_execute
    return db


def _patched_3mf(filament_usage, capture=None):
    """Patch the 3MF extract, optionally recording the plate_id it was given."""

    def _extract(path, plate_id=None):
        if capture is not None:
            capture.append(plate_id)
        return filament_usage

    return patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", side_effect=_extract)


def _settings_patch():
    mock_settings = patch("backend.app.core.config.settings")
    return mock_settings


class TestPersistedSessionRoundTrip:
    """The row is the only thing that outlives the process."""

    @pytest.fixture(autouse=True)
    def _clear_sessions(self):
        _active_sessions.clear()
        yield
        _active_sessions.clear()

    @pytest.fixture
    async def printer(self, db_session):
        row = Printer(name="H2D-1", ip_address="192.168.0.10", access_code="1234", serial_number="TESTSERIAL")
        db_session.add(row)
        await db_session.commit()
        return row

    def _session(self, printer_id):
        return PrintSession(
            printer_id=printer_id,
            print_name="AMS_Rack",
            started_at=datetime(2026, 8, 11, 9, 25, 6, tzinfo=timezone.utc),
            tray_remain_start={(0, 2): 84, (0, 3): 100},
            tray_now_at_start=2,
            spool_assignments={(0, 2): 69, (0, 3): 68},
            ams_mapping=[2],
            plate_id=1,
        )

    @pytest.mark.asyncio
    async def test_restore_rebuilds_the_session_and_returns_the_tray_log(self, db_session, printer):
        await persist_session(db_session, self._session(printer.id), [(2, 0)])
        _active_sessions.clear()

        log = await restore_session(db_session, printer.id)

        assert log == [[2, 0]]
        restored = _active_sessions[printer.id]
        assert restored.plate_id == 1
        assert restored.ams_mapping == [2]
        assert restored.tray_now_at_start == 2
        # Tuple keys survive the JSON round trip — the completion path indexes
        # the snapshot by (ams_id, tray_id).
        assert restored.spool_assignments == {(0, 2): 69, (0, 3): 68}
        assert restored.tray_remain_start == {(0, 2): 84, (0, 3): 100}
        assert restored.started_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_tray_changes_accumulate_in_order(self, db_session, printer):
        await persist_session(db_session, self._session(printer.id), [(2, 0)])

        # The runout sequence from the reported print: A3 empties, the AMS
        # parks, then filament backup brings A4 in.
        await record_tray_change(db_session, printer.id, 254, 670)
        await record_tray_change(db_session, printer.id, 3, 675)

        assert await restore_session(db_session, printer.id) == [[2, 0], [254, 670], [3, 675]]

    @pytest.mark.asyncio
    async def test_tray_change_without_a_session_is_a_noop(self, db_session, printer):
        await record_tray_change(db_session, printer.id, 3, 675)

        row = await db_session.get(ActivePrintSession, printer.id)
        assert row is None

    @pytest.mark.asyncio
    async def test_print_start_overwrites_a_row_left_by_a_missed_completion(self, db_session, printer):
        await persist_session(db_session, self._session(printer.id), [(2, 0), (3, 675)])

        second = self._session(printer.id)
        second.print_name = "Cover"
        second.plate_id = 2
        second.ams_mapping = [5]
        second.spool_assignments = {(1, 0): 60}
        await persist_session(db_session, second, [(5, 0)])

        rows = (await db_session.execute(select(ActivePrintSession))).scalars().all()
        assert len(rows) == 1
        log = await restore_session(db_session, printer.id)
        assert log == [[5, 0]]
        assert _active_sessions[printer.id].plate_id == 2
        assert _active_sessions[printer.id].spool_assignments == {(1, 0): 60}

    @pytest.mark.asyncio
    async def test_clear_removes_the_row(self, db_session, printer):
        await persist_session(db_session, self._session(printer.id), [(2, 0)])

        await clear_persisted_session(db_session, printer.id)

        assert await restore_session(db_session, printer.id) is None
        assert await get_persisted_print_name(db_session, printer.id) is None

    @pytest.mark.asyncio
    async def test_clear_is_safe_without_a_row(self, db_session, printer):
        await clear_persisted_session(db_session, printer.id)

    @pytest.mark.asyncio
    async def test_print_name_is_readable_for_the_identity_check(self, db_session, printer):
        await persist_session(db_session, self._session(printer.id), None)

        assert await get_persisted_print_name(db_session, printer.id) == "AMS_Rack"

    @pytest.mark.asyncio
    async def test_completion_falls_back_to_the_persisted_row(self, db_session, printer):
        """No in-memory session (the restart case): the plate, the mapping and
        the assignment snapshot must still reach the 3MF path."""
        await persist_session(db_session, self._session(printer.id), [(2, 0), (3, 675)])
        _active_sessions.clear()

        captured = {}

        async def _fake_track(*args, **kwargs):
            captured.update(kwargs)
            return []

        with (
            patch("backend.app.api.routes.settings.get_setting", new_callable=AsyncMock, return_value=None),
            patch("backend.app.services.usage_tracker._track_from_3mf", side_effect=_fake_track),
        ):
            await on_print_complete(
                printer.id,
                {"status": "completed", "subtask_name": "AMS_Rack"},
                MagicMock(),
                db_session,
                archive_id=312,
            )

        assert captured["plate_id"] == 1
        assert captured["ams_mapping"] == [2]
        assert captured["tray_now_at_start"] == 2
        assert captured["spool_assignments"] == {(0, 2): 69, (0, 3): 68}


class TestPlateIdRecovery:
    """Without the plate, the 3MF parser sums every plate in the file and the
    whole multi-plate total lands on one spool."""

    @pytest.mark.asyncio
    async def test_archive_plate_id_is_used_when_the_session_is_gone(self):
        archive = _make_archive(archive_id=312, plate_id=1)
        spool = _make_spool(spool_id=68)
        assignment = _make_assignment(spool_id=68, ams_id=0, tray_id=3)
        db = _mock_db_sequential([archive, None, assignment, spool])
        seen_plate_ids: list = []

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"mapping": [3]},
            progress=100,
            layer_num=809,
            tray_now=255,
            tray_change_log=[],
            total_layers=809,
        )

        with (
            _settings_patch() as mock_settings,
            _patched_3mf([{"slot_id": 1, "used_g": 1122.44, "type": "ABS", "color": "#808080"}], seen_plate_ids),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            await _track_from_3mf(
                printer_id=1,
                archive_id=312,
                status="completed",
                print_name="AMS_Rack",
                handled_trays=set(),
                printer_manager=printer_manager,
                db=db,
                plate_id=None,
            )

        assert seen_plate_ids == [1]

    @pytest.mark.asyncio
    async def test_queue_item_plate_id_is_used_when_the_archive_has_none(self):
        archive = _make_archive(archive_id=312, plate_id=None)
        queue_item = _make_queue_item(plate_id=2)
        spool = _make_spool(spool_id=68)
        assignment = _make_assignment(spool_id=68, ams_id=0, tray_id=3)
        # db: archive, the single queue lookup (plate + mapping share it),
        # then assignment and spool
        db = _mock_db_sequential([archive, queue_item, assignment, spool])
        seen_plate_ids: list = []

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"mapping": [3]},
            progress=100,
            layer_num=361,
            tray_now=255,
            tray_change_log=[],
            total_layers=361,
        )

        with (
            _settings_patch() as mock_settings,
            _patched_3mf([{"slot_id": 1, "used_g": 318.82, "type": "ABS", "color": "#808080"}], seen_plate_ids),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            await _track_from_3mf(
                printer_id=1,
                archive_id=312,
                status="completed",
                print_name="AMS_Rack",
                handled_trays=set(),
                printer_manager=printer_manager,
                db=db,
                plate_id=None,
            )

        assert seen_plate_ids == [2]

    @pytest.mark.asyncio
    async def test_caller_plate_id_wins_over_the_database(self):
        archive = _make_archive(archive_id=312, plate_id=1)
        spool = _make_spool(spool_id=68)
        assignment = _make_assignment(spool_id=68, ams_id=0, tray_id=3)
        db = _mock_db_sequential([archive, None, assignment, spool])
        seen_plate_ids: list = []

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"mapping": [3]},
            progress=100,
            layer_num=361,
            tray_now=255,
            tray_change_log=[],
            total_layers=361,
        )

        with (
            _settings_patch() as mock_settings,
            _patched_3mf([{"slot_id": 1, "used_g": 318.82, "type": "ABS", "color": "#808080"}], seen_plate_ids),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            await _track_from_3mf(
                printer_id=1,
                archive_id=312,
                status="completed",
                print_name="AMS_Rack",
                handled_trays=set(),
                printer_manager=printer_manager,
                db=db,
                plate_id=2,
            )

        assert seen_plate_ids == [2]


class TestMappingPriority:
    """AMS filament backup rewrites the printer's live ``mapping`` field to the
    substitute tray. Read at completion it names the tray that finished the
    print, not the one the slicer assigned."""

    @pytest.mark.asyncio
    async def test_queue_mapping_beats_the_live_mqtt_mapping(self):
        archive = _make_archive(archive_id=312, plate_id=1)
        # Dispatched against AMS0-T2 (global tray 2); the printer now reports
        # tray 3 because backup swapped in the neighbouring spool.
        queue_item = _make_queue_item(ams_mapping="[2]")
        spool_69 = _make_spool(spool_id=69)
        assign_69 = _make_assignment(spool_id=69, ams_id=0, tray_id=2)
        db = _mock_db_sequential([archive, queue_item, assign_69, spool_69])

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"mapping": [3]},
            progress=100,
            layer_num=809,
            tray_now=255,
            tray_change_log=[],
            total_layers=809,
        )

        with (
            _settings_patch() as mock_settings,
            _patched_3mf([{"slot_id": 1, "used_g": 1122.44, "type": "ABS", "color": "#808080"}]),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=312,
                status="completed",
                print_name="AMS_Rack",
                handled_trays=set(),
                printer_manager=printer_manager,
                db=db,
                plate_id=1,
            )

        assert len(results) == 1
        assert results[0]["spool_id"] == 69
        assert (results[0]["ams_id"], results[0]["tray_id"]) == (0, 2)

    @pytest.mark.asyncio
    async def test_mqtt_mapping_still_used_for_a_direct_print(self):
        """No queue item — the live field is the only mapping there is."""
        archive = _make_archive(archive_id=400, plate_id=1)
        spool = _make_spool(spool_id=68)
        assignment = _make_assignment(spool_id=68, ams_id=0, tray_id=3)
        db = _mock_db_sequential([archive, None, assignment, spool])

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"mapping": [3]},
            progress=100,
            layer_num=100,
            tray_now=255,
            tray_change_log=[],
            total_layers=100,
        )

        with (
            _settings_patch() as mock_settings,
            _patched_3mf([{"slot_id": 1, "used_g": 50.0, "type": "ABS", "color": "#808080"}]),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=400,
                status="completed",
                print_name="Cover",
                handled_trays=set(),
                printer_manager=printer_manager,
                db=db,
                plate_id=1,
            )

        assert len(results) == 1
        assert (results[0]["ams_id"], results[0]["tray_id"]) == (0, 3)


class TestRestoreOnRestartRecovery:
    """``on_print_running_observed`` is the only hook that fires when Bambuddy
    comes up mid-print — the #1304 guard suppresses ``on_print_start``."""

    @pytest.fixture(autouse=True)
    def _clear_sessions(self):
        _active_sessions.clear()
        yield
        _active_sessions.clear()

    @pytest.fixture
    async def printer(self, db_session):
        row = Printer(name="H2D-1", ip_address="192.168.0.10", access_code="1234", serial_number="TESTSERIAL")
        db_session.add(row)
        await db_session.commit()
        return row

    def _state(self, **overrides):
        state = SimpleNamespace(
            subtask_name="AMS_Rack",
            tray_change_log=[],
            tray_now=3,
            layer_num=700,
            last_loaded_tray=-1,
        )
        for key, value in overrides.items():
            setattr(state, key, value)
        return state

    def _session(self, printer_id, print_name="AMS_Rack"):
        return PrintSession(
            printer_id=printer_id,
            print_name=print_name,
            started_at=datetime(2026, 8, 11, 9, 25, 6, tzinfo=timezone.utc),
            tray_now_at_start=2,
            spool_assignments={(0, 2): 69},
            ams_mapping=[2],
            plate_id=1,
        )

    @pytest.mark.asyncio
    async def test_persisted_log_comes_back_onto_the_printer_state(self, db_session, printer):
        from backend.app.main import _restore_usage_tracking_session

        await persist_session(db_session, self._session(printer.id), [(2, 0)])
        _active_sessions.clear()
        state = self._state()

        await _restore_usage_tracking_session(printer.id, state, db_session, MagicMock())

        assert state.tray_change_log == [(2, 0)]
        assert _active_sessions[printer.id].plate_id == 1

    @pytest.mark.asyncio
    async def test_entries_seen_by_this_process_are_kept_after_the_persisted_ones(self, db_session, printer):
        from backend.app.main import _restore_usage_tracking_session

        await persist_session(db_session, self._session(printer.id), [(2, 0)])
        state = self._state(tray_change_log=[(3, 675)])

        await _restore_usage_tracking_session(printer.id, state, db_session, MagicMock())

        assert state.tray_change_log == [(2, 0), (3, 675)]

    @pytest.mark.asyncio
    async def test_no_persisted_row_seeds_from_the_tray_feeding_now(self, db_session, printer):
        """A print that started before this build still gets its remaining
        segment attributed to the right spool."""
        from backend.app.main import _restore_usage_tracking_session

        state = self._state()

        await _restore_usage_tracking_session(printer.id, state, db_session, MagicMock())

        assert state.tray_change_log == [(3, 700)]
        assert state.last_loaded_tray == 3

    @pytest.mark.asyncio
    async def test_unloaded_tray_seeds_nothing(self, db_session, printer):
        from backend.app.main import _restore_usage_tracking_session

        state = self._state(tray_now=255)

        await _restore_usage_tracking_session(printer.id, state, db_session, MagicMock())

        assert state.tray_change_log == []

    @pytest.mark.asyncio
    async def test_a_row_from_a_different_print_is_discarded(self, db_session, printer):
        """A completion Bambuddy never saw leaves a row behind; it must not
        attach itself to whatever is running now."""
        from backend.app.main import _restore_usage_tracking_session

        await persist_session(db_session, self._session(printer.id, print_name="Old_Print"), [(2, 0)])
        _active_sessions.clear()
        state = self._state(subtask_name="AMS_Rack")

        await _restore_usage_tracking_session(printer.id, state, db_session, MagicMock())

        assert printer.id not in _active_sessions
        assert await restore_session(db_session, printer.id) is None
        # Still seeded, so the rest of the running print stays attributable.
        assert state.tray_change_log == [(3, 700)]

    @pytest.mark.asyncio
    async def test_an_unloaded_tray_does_not_clobber_last_loaded_tray(self, db_session, printer):
        """``last_loaded_tray`` is the fallback that survives the end-of-print
        retract to 255; writing 255 into it would defeat its whole purpose."""
        from backend.app.main import _restore_usage_tracking_session

        state = self._state(tray_now=255, last_loaded_tray=2)

        await _restore_usage_tracking_session(printer.id, state, db_session, MagicMock())

        assert state.last_loaded_tray == 2

    @pytest.mark.asyncio
    async def test_a_failure_is_swallowed_so_the_caller_keeps_going(self, db_session, printer):
        """The caller still has to capture its timelapse baseline before the
        printer uploads the in-flight recording — there is no second chance."""
        from backend.app.main import _restore_usage_tracking_session

        broken = SimpleNamespace()  # no subtask_name, no tray fields at all

        await _restore_usage_tracking_session(printer.id, broken, db_session, MagicMock())


class TestPlateNotInTheFile:
    """A recovered plate has to be treated as a hint, not a filter that can
    silently zero out a print's usage."""

    @pytest.mark.asyncio
    async def test_falls_back_to_the_whole_file_when_the_plate_is_absent(self):
        """The archive's own 3MF can be gone, with a same-named library file
        substituted that was sliced with different plates."""
        archive = _make_archive(archive_id=312, plate_id=7)
        spool = _make_spool(spool_id=68)
        assignment = _make_assignment(spool_id=68, ams_id=0, tray_id=3)
        db = _mock_db_sequential([archive, None, assignment, spool])
        calls: list = []

        def _extract(path, plate_id=None):
            calls.append(plate_id)
            return [] if plate_id is not None else [{"slot_id": 1, "used_g": 12.0, "type": "ABS", "color": ""}]

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"mapping": [3]},
            progress=100,
            layer_num=10,
            tray_now=255,
            tray_change_log=[],
            total_layers=10,
        )

        with (
            _settings_patch() as mock_settings,
            patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", side_effect=_extract),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=312,
                status="completed",
                print_name="AMS_Rack",
                handled_trays=set(),
                printer_manager=printer_manager,
                db=db,
                plate_id=None,
            )

        assert calls == [7, None]
        assert len(results) == 1
        assert results[0]["weight_used"] == 12.0

    @pytest.mark.asyncio
    async def test_a_file_with_no_usage_at_all_still_records_nothing(self):
        archive = _make_archive(archive_id=312, plate_id=1)
        db = _mock_db_sequential([archive, None])

        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"mapping": [3]},
            progress=100,
            layer_num=10,
            tray_now=255,
            tray_change_log=[],
            total_layers=10,
        )

        with (
            _settings_patch() as mock_settings,
            patch("backend.app.utils.threemf_tools.extract_filament_usage_from_3mf", return_value=[]),
        ):
            mock_settings.base_dir = MagicMock()
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_settings.base_dir.__truediv__ = MagicMock(return_value=mock_path)

            results = await _track_from_3mf(
                printer_id=1,
                archive_id=312,
                status="completed",
                print_name="AMS_Rack",
                handled_trays=set(),
                printer_manager=printer_manager,
                db=db,
                plate_id=1,
            )

        assert results == []


class TestSpoolmanParity:
    """Both inventory backends need the same restart protection.

    Spoolman's own durable row (#1820) already carries its plate-scoped 3MF
    figures and the mapping it was dispatched with, but not the tray-change
    log — and that log is the only record of which spool fed which layers when
    AMS Filament Backup swaps trays. Capturing it for one backend only would
    leave Spoolman users with the bug this fixes for everyone else.
    """

    @pytest.fixture(autouse=True)
    def _clear_sessions(self):
        _active_sessions.clear()
        yield
        _active_sessions.clear()

    @pytest.fixture
    async def printer(self, db_session):
        row = Printer(name="H2D-1", ip_address="192.168.0.10", access_code="1234", serial_number="TESTSERIAL")
        db_session.add(row)
        await db_session.commit()
        return row

    def _printer_manager(self):
        pm = MagicMock()
        pm.get_status.return_value = SimpleNamespace(
            raw_data={
                "ams": {"ams": [{"id": 0, "tray": [{"id": 2, "remain": 84, "tray_type": "ABS"}]}]},
                "vt_tray": [],
                "mapping": [2],
            },
            tray_now=2,
            last_loaded_tray=2,
            tray_change_log=[(2, 0)],
        )
        return pm

    @pytest.mark.asyncio
    async def test_the_row_is_written_with_spoolman_enabled(self, db_session, printer):
        from backend.app.services.usage_tracker import on_print_start

        await on_print_start(
            printer.id,
            {"subtask_name": "AMS_Rack", "ams_mapping": [2]},
            self._printer_manager(),
            db=db_session,
            spoolman_owns_usage=True,
        )

        row = await db_session.get(ActivePrintSession, printer.id)
        assert row is not None
        assert row.print_name == "AMS_Rack"
        assert row.tray_change_log == [[2, 0]]

    @pytest.mark.asyncio
    async def test_spoolman_does_not_get_an_in_memory_session(self, db_session, printer):
        """``_active_sessions`` doubles as on_ams_change's "skip the remain%
        weight sync, the internal tracker will deduct precisely" flag (#880).
        A session the internal tracker will never complete would suppress a
        sync Spoolman users still need."""
        from backend.app.services.usage_tracker import on_print_start

        await on_print_start(
            printer.id,
            {"subtask_name": "AMS_Rack", "ams_mapping": [2]},
            self._printer_manager(),
            db=db_session,
            spoolman_owns_usage=True,
        )

        assert printer.id not in _active_sessions

    @pytest.mark.asyncio
    async def test_the_internal_tracker_still_gets_one(self, db_session, printer):
        from backend.app.services.usage_tracker import on_print_start

        await on_print_start(
            printer.id,
            {"subtask_name": "AMS_Rack", "ams_mapping": [2]},
            self._printer_manager(),
            db=db_session,
            spoolman_owns_usage=False,
        )

        assert _active_sessions[printer.id].ams_mapping == [2]
        assert await db_session.get(ActivePrintSession, printer.id) is not None

    @pytest.mark.asyncio
    async def test_restore_can_return_the_log_without_publishing_a_session(self, db_session, printer):
        await persist_session(
            db_session,
            PrintSession(
                printer_id=printer.id,
                print_name="AMS_Rack",
                started_at=datetime(2026, 8, 11, 9, 25, 6, tzinfo=timezone.utc),
            ),
            [(2, 0), (3, 675)],
        )
        _active_sessions.clear()

        log = await restore_session(db_session, printer.id, register_active=False)

        assert log == [[2, 0], [3, 675]]
        assert printer.id not in _active_sessions

    @pytest.mark.asyncio
    async def test_discard_clears_both_halves(self, db_session, printer):
        from backend.app.services.usage_tracker import discard_session

        session = PrintSession(
            printer_id=printer.id,
            print_name="AMS_Rack",
            started_at=datetime(2026, 8, 11, 9, 25, 6, tzinfo=timezone.utc),
        )
        _active_sessions[printer.id] = session
        await persist_session(db_session, session, [(2, 0)])

        await discard_session(db_session, printer.id)

        assert printer.id not in _active_sessions
        assert await db_session.get(ActivePrintSession, printer.id) is None
