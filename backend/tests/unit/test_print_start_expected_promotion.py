"""Tests for expected print promotion when auto_archive is disabled (#839).

When auto_archive=False but a print was dispatched by BamBuddy (queue/reprint),
the on_print_start callback must still promote the expected print to _active_prints
so that at print completion the archive_id and ams_mapping are available for
filament usage tracking.

These are pure unit tests that verify the module-level dict manipulation logic
directly, NOT by calling the full on_print_start callback.
"""

import time

import pytest

from backend.app.main import (
    _active_prints,
    _expected_print_creators,
    _expected_print_registered_at,
    _expected_prints,
    _print_ams_mappings,
    register_expected_print,
)


@pytest.fixture(autouse=True)
def _clear_dicts():
    """Clear module-level tracking dicts before and after each test."""
    _expected_prints.clear()
    _expected_print_registered_at.clear()
    _expected_print_creators.clear()
    _print_ams_mappings.clear()
    _active_prints.clear()
    yield
    _expected_prints.clear()
    _expected_print_registered_at.clear()
    _expected_print_creators.clear()
    _print_ams_mappings.clear()
    _active_prints.clear()


class TestRegisterExpectedPrint:
    """Verify register_expected_print populates all tracking dicts."""

    def test_registers_filename_and_variants(self):
        register_expected_print(1, "Box.3mf", archive_id=54, ams_mapping=[1])

        assert _expected_prints[(1, "Box.3mf")] == 54
        assert _expected_prints[(1, "Box")] == 54
        assert _expected_prints[(1, "Box.gcode")] == 54

    def test_stores_ams_mapping(self):
        register_expected_print(1, "test.3mf", archive_id=10, ams_mapping=[2, -1, 3])
        assert _print_ams_mappings[10] == [2, -1, 3]

    def test_no_ams_mapping_when_none(self):
        register_expected_print(1, "test.3mf", archive_id=10, ams_mapping=None)
        assert 10 not in _print_ams_mappings

    def test_stores_creator(self):
        register_expected_print(1, "test.3mf", archive_id=10, created_by_id=5)
        assert _expected_print_creators[(1, "test.3mf")] == 5

    def test_stores_registered_at(self):
        before = time.monotonic()
        register_expected_print(1, "test.3mf", archive_id=10)
        after = time.monotonic()

        ts = _expected_print_registered_at[(1, "test.3mf")]
        assert before <= ts <= after


class TestExpectedPrintDetection:
    """Verify the expected-print detection logic used in on_print_start.

    Reproduces the key-building and lookup logic from the auto_archive=False
    block in on_print_start to verify that expected prints are correctly
    detected across all filename variations.
    """

    @staticmethod
    def _build_check_keys(printer_id: int, filename: str, subtask_name: str):
        """Reproduce the key-building logic from on_print_start."""
        check_keys = []
        if subtask_name:
            check_keys += [
                (printer_id, subtask_name),
                (printer_id, f"{subtask_name}.3mf"),
                (printer_id, f"{subtask_name}.gcode.3mf"),
            ]
        if filename:
            base_fn = filename.split("/")[-1] if "/" in filename else filename
            check_keys.append((printer_id, base_fn))
            no_archive_base = base_fn.replace(".gcode", "").replace(".3mf", "")
            check_keys += [
                (printer_id, no_archive_base),
                (printer_id, f"{no_archive_base}.3mf"),
            ]
        return check_keys

    def test_detects_expected_print_by_subtask(self):
        """Expected print is found when subtask_name matches."""
        register_expected_print(1, "Box.3mf", archive_id=54, ams_mapping=[1])
        keys = self._build_check_keys(1, filename="", subtask_name="Box")
        assert any(k in _expected_prints for k in keys)

    def test_detects_expected_print_by_filename(self):
        """Expected print is found when filename matches."""
        register_expected_print(1, "Box.3mf", archive_id=54, ams_mapping=[1])
        keys = self._build_check_keys(1, filename="Box.3mf", subtask_name="")
        assert any(k in _expected_prints for k in keys)

    def test_detects_expected_print_by_gcode_filename(self):
        """Expected print is found when MQTT reports .gcode filename."""
        register_expected_print(1, "Box.3mf", archive_id=54, ams_mapping=[1])
        # MQTT sometimes reports gcode filename
        keys = self._build_check_keys(1, filename="Box.gcode", subtask_name="Box")
        assert any(k in _expected_prints for k in keys)

    def test_no_false_positive_for_different_file(self):
        """Expected print NOT found for a different filename."""
        register_expected_print(1, "Box.3mf", archive_id=54, ams_mapping=[1])
        keys = self._build_check_keys(1, filename="Benchy.3mf", subtask_name="Benchy")
        assert not any(k in _expected_prints for k in keys)

    def test_no_false_positive_for_different_printer(self):
        """Expected print NOT found when printer_id doesn't match."""
        register_expected_print(1, "Box.3mf", archive_id=54, ams_mapping=[1])
        keys = self._build_check_keys(2, filename="Box.3mf", subtask_name="Box")
        assert not any(k in _expected_prints for k in keys)

    def test_empty_expected_prints_returns_false(self):
        """No detection when _expected_prints is empty."""
        keys = self._build_check_keys(1, filename="test.3mf", subtask_name="test")
        assert not any(k in _expected_prints for k in keys)

    def test_filename_with_spaces_and_parens(self):
        """Handles filenames with spaces and parentheses (e.g. 'Box3.0_(2)_plate_5.3mf')."""
        register_expected_print(1, "Box3.0_(2)_plate_5.3mf", archive_id=54, ams_mapping=[1])
        keys = self._build_check_keys(
            1,
            filename="Box3.0_(2)_plate_5.gcode",
            subtask_name="Box3.0_(2)_plate_5",
        )
        assert any(k in _expected_prints for k in keys)


class TestExpectedPrintPromotion:
    """Verify that expected prints are correctly promoted to _active_prints.

    Reproduces the expected-print pop + promotion logic from on_print_start
    (lines 1468-1496) to verify that _active_prints is populated and
    _expected_prints is cleaned up.
    """

    @staticmethod
    def _simulate_expected_print_promotion(printer_id: int, subtask_name: str, filename: str, archive_filename: str):
        """Simulate the expected-print lookup and promotion from on_print_start."""
        expected_keys = []
        if subtask_name:
            expected_keys.append((printer_id, subtask_name))
            expected_keys.append((printer_id, f"{subtask_name}.3mf"))
            expected_keys.append((printer_id, f"{subtask_name}.gcode.3mf"))
        if filename:
            fname = filename.split("/")[-1] if "/" in filename else filename
            expected_keys.append((printer_id, fname))
            base = fname.replace(".gcode", "").replace(".3mf", "")
            expected_keys.append((printer_id, base))
            expected_keys.append((printer_id, f"{base}.3mf"))

        expected_archive_id = None
        for key in expected_keys:
            expected_archive_id = _expected_prints.pop(key, None)
            _expected_print_registered_at.pop(key, None)
            if expected_archive_id:
                for other_key in expected_keys:
                    _expected_prints.pop(other_key, None)
                    _expected_print_registered_at.pop(other_key, None)
                break

        if expected_archive_id:
            _active_prints[(printer_id, archive_filename)] = expected_archive_id
            if subtask_name:
                _active_prints[(printer_id, f"{subtask_name}.3mf")] = expected_archive_id

        return expected_archive_id

    def test_promotion_populates_active_prints(self):
        """After promotion, archive is in _active_prints."""
        register_expected_print(1, "Box.3mf", archive_id=54, ams_mapping=[1])

        archive_id = self._simulate_expected_print_promotion(
            printer_id=1,
            subtask_name="Box",
            filename="Box.gcode",
            archive_filename="Box.3mf",
        )

        assert archive_id == 54
        assert _active_prints[(1, "Box.3mf")] == 54

    def test_promotion_cleans_up_expected_prints(self):
        """After promotion, _expected_prints is empty for this print."""
        register_expected_print(1, "Box.3mf", archive_id=54, ams_mapping=[1])

        self._simulate_expected_print_promotion(
            printer_id=1,
            subtask_name="Box",
            filename="Box.gcode",
            archive_filename="Box.3mf",
        )

        # All variants should be cleaned up
        assert (1, "Box.3mf") not in _expected_prints
        assert (1, "Box") not in _expected_prints
        assert (1, "Box.gcode") not in _expected_prints

    def test_ams_mapping_survives_promotion(self):
        """_print_ams_mappings is NOT consumed during promotion — it's needed at completion."""
        register_expected_print(1, "Box.3mf", archive_id=54, ams_mapping=[1])

        self._simulate_expected_print_promotion(
            printer_id=1,
            subtask_name="Box",
            filename="Box.gcode",
            archive_filename="Box.3mf",
        )

        # ams_mapping should still be available for on_print_complete
        assert _print_ams_mappings[54] == [1]

    def test_completion_lookup_finds_promoted_archive(self):
        """Simulate on_print_complete finding the archive in _active_prints."""
        register_expected_print(1, "Box.3mf", archive_id=54, ams_mapping=[1])

        self._simulate_expected_print_promotion(
            printer_id=1,
            subtask_name="Box",
            filename="Box.gcode",
            archive_filename="Box.3mf",
        )

        # Simulate on_print_complete key building
        completion_keys = [
            (1, "Box.3mf"),
            (1, "Box.gcode.3mf"),
            (1, "Box"),
        ]
        found_id = None
        for key in completion_keys:
            found_id = _active_prints.pop(key, None)
            if found_id:
                break

        assert found_id == 54
        # And ams_mapping is retrievable
        assert _print_ams_mappings.pop(54, None) == [1]

    def test_no_promotion_for_external_print(self):
        """When no expected print exists, nothing is promoted."""
        archive_id = self._simulate_expected_print_promotion(
            printer_id=1,
            subtask_name="Benchy",
            filename="Benchy.gcode",
            archive_filename="Benchy.3mf",
        )

        assert archive_id is None
        assert len(_active_prints) == 0


class TestAMSMappingInjection:
    """Verify ams_mapping injection into usage tracker session."""

    def test_injection_into_session(self):
        """ams_mapping from _print_ams_mappings is injectable into a session."""
        from datetime import datetime, timezone

        from backend.app.services.usage_tracker import PrintSession, _active_sessions

        _active_sessions.clear()

        # Create a session without ams_mapping (simulates MQTT not providing it)
        session = PrintSession(
            printer_id=1,
            print_name="Box",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={},
            tray_now_at_start=-1,
            spool_assignments={},
            ams_mapping=None,
        )
        _active_sessions[1] = session

        # Register expected print with ams_mapping
        register_expected_print(1, "Box.3mf", archive_id=54, ams_mapping=[1])

        # Simulate the injection logic from on_print_start
        _stored_map = _print_ams_mappings.get(54)
        assert _stored_map == [1]

        ut_session = _active_sessions.get(1)
        assert ut_session is not None
        assert ut_session.ams_mapping is None  # before injection

        ut_session.ams_mapping = _stored_map  # injection
        assert ut_session.ams_mapping == [1]

        _active_sessions.clear()

    def test_no_injection_when_session_already_has_mapping(self):
        """Don't overwrite existing ams_mapping in session."""
        from datetime import datetime, timezone

        from backend.app.services.usage_tracker import PrintSession, _active_sessions

        _active_sessions.clear()

        session = PrintSession(
            printer_id=1,
            print_name="Box",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={},
            tray_now_at_start=-1,
            spool_assignments={},
            ams_mapping=[5, 6],  # already has mapping from MQTT
        )
        _active_sessions[1] = session

        register_expected_print(1, "Box.3mf", archive_id=54, ams_mapping=[1])

        _stored_map = _print_ams_mappings.get(54)
        ut_session = _active_sessions.get(1)

        # Guard: don't overwrite if session already has a mapping
        if ut_session and not ut_session.ams_mapping:
            ut_session.ams_mapping = _stored_map

        assert ut_session.ams_mapping == [5, 6]  # unchanged

        _active_sessions.clear()
