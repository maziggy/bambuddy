"""Tests for the gates that hold back AMS humidity / temperature alarms.

Two independent gates, both sitting in ``record_ams_history``'s dispatch: the
empty-AMS gate (#1619) documented below, and the drying gate (#1802) that stops
the temperature alarm firing throughout a drying cycle and the cool-down after
it.

Empty-AMS alarm gate (#1619).

Empty AMS units still emit humidity/temperature sensor readings, but those
readings are ambient and not actionable — there's no filament to dry. Without
the gate every empty AMS spammed an hourly alarm. ``_ams_has_filament``
inspects the firmware-reported ``tray_exist_bits`` bitmap (fallback: ``tray``
array's ``tray_type`` strings) so the alarm dispatch in ``record_ams_history``
can skip empty units while still alarming on loaded ones in the same printer.
"""

from datetime import datetime, timedelta, timezone

from backend.app.main import _ams_has_filament
from backend.app.utils.ams_drying import is_drying_active, temperature_alarm_suppressed


class TestAmsHasFilament:
    def test_tray_exist_bits_zero_means_empty(self):
        assert _ams_has_filament({"tray_exist_bits": "0"}) is False
        # Real firmware sometimes pads with extra zeros or prefixes; all
        # parseable forms of zero should resolve to "empty".
        assert _ams_has_filament({"tray_exist_bits": "00"}) is False
        assert _ams_has_filament({"tray_exist_bits": "0x0"}) is False

    def test_tray_exist_bits_nonzero_means_loaded(self):
        # Single tray loaded — e.g. AMS-Lite or AMS-HT.
        assert _ams_has_filament({"tray_exist_bits": "1"}) is True
        # Four-slot AMS with all slots full (bitmap 0xf == 0b1111).
        assert _ams_has_filament({"tray_exist_bits": "f"}) is True
        # Mixed — 0xa == 0b1010, two slots loaded.
        assert _ams_has_filament({"tray_exist_bits": "a"}) is True
        # The exact bitmap seen in #1622 / #1602 logs.
        assert _ams_has_filament({"tray_exist_bits": "ed"}) is True

    def test_falls_back_to_tray_array_when_bits_missing(self):
        # Empty tray_type strings across the whole tray array → empty AMS.
        ams_empty = {
            "tray": [
                {"id": 0, "tray_type": ""},
                {"id": 1, "tray_type": ""},
            ]
        }
        assert _ams_has_filament(ams_empty) is False
        # Any non-empty tray_type → loaded AMS.
        ams_loaded = {
            "tray": [
                {"id": 0, "tray_type": ""},
                {"id": 1, "tray_type": "PLA"},
            ]
        }
        assert _ams_has_filament(ams_loaded) is True

    def test_missing_both_signals_returns_false(self):
        # No tray_exist_bits AND no tray array — early-pushall shape; we
        # treat it as "no info → don't alarm" rather than guessing loaded.
        assert _ams_has_filament({}) is False

    def test_unparseable_bitmap_falls_back_to_tray_array(self):
        # Garbage in tray_exist_bits — must not raise and must fall through
        # to the tray array check.
        loaded = {"tray_exist_bits": "garbage", "tray": [{"id": 0, "tray_type": "PETG"}]}
        assert _ams_has_filament(loaded) is True
        empty = {"tray_exist_bits": "garbage", "tray": []}
        assert _ams_has_filament(empty) is False

    def test_empty_bits_string_falls_back_to_tray_array(self):
        # Some pre-handshake pushall shapes set the field but leave it blank.
        loaded = {"tray_exist_bits": "", "tray": [{"id": 0, "tray_type": "ABS"}]}
        assert _ams_has_filament(loaded) is True

    def test_whitespace_tray_type_is_not_loaded(self):
        # A tray_type that's all whitespace doesn't count as a real material.
        assert _ams_has_filament({"tray": [{"id": 0, "tray_type": "   "}]}) is False

    def test_non_dict_tray_entries_are_skipped(self):
        # Defensive: malformed tray array shouldn't crash the helper.
        assert _ams_has_filament({"tray": [None, "junk", 42]}) is False

    def test_non_string_bits_falls_back(self):
        # Some MQTT shapes send tray_exist_bits as int; we only parse strings,
        # so an int falls through to the tray array.
        loaded = {"tray_exist_bits": 0xED, "tray": [{"id": 0, "tray_type": "PLA"}]}
        assert _ams_has_filament(loaded) is True
        empty_int = {"tray_exist_bits": 0xED}  # no tray array, int ignored
        assert _ams_has_filament(empty_int) is False


class TestIsDryingActive:
    """The two firmware signals that mean "a drying cycle is running" (#1802)."""

    def test_countdown_running_is_active(self):
        assert is_drying_active({"dry_time": 720}) is True
        # Strings appear in some payload shapes.
        assert is_drying_active({"dry_time": "45"}) is True

    def test_idle_unit_is_not_active(self):
        assert is_drying_active({"dry_time": 0, "dry_status": 0}) is False
        assert is_drying_active({}) is False

    def test_cooling_phase_counts_as_active(self):
        # The reason dry_time alone is not enough: the cycle's own cooling phase
        # runs with the countdown already at 0.
        assert is_drying_active({"dry_time": 0, "dry_status": 3}) is True

    def test_checking_and_drying_phases_count_as_active(self):
        assert is_drying_active({"dry_time": 0, "dry_status": 1}) is True
        assert is_drying_active({"dry_time": 0, "dry_status": 2}) is True

    def test_ending_phases_do_not_count_as_active(self):
        # 4=Stopping, 5=Error — the cycle is over or aborting.
        assert is_drying_active({"dry_time": 0, "dry_status": 4}) is False
        assert is_drying_active({"dry_time": 0, "dry_status": 5}) is False

    def test_heat_out_of_control_is_not_active(self):
        # 6=HeatOutOfControl is the one phase where a high-temperature alarm is
        # exactly what the user needs, so it must never read as expected heat.
        assert is_drying_active({"dry_time": 0, "dry_status": 6}) is False

    def test_missing_dry_status_falls_back_to_countdown(self):
        # Firmware that never sends a parseable `info` has no dry_status at all.
        assert is_drying_active({"dry_time": 30}) is True
        assert is_drying_active({"dry_time": 0}) is False

    def test_unparseable_values_do_not_raise(self):
        assert is_drying_active({"dry_time": "junk", "dry_status": 2}) is True
        assert is_drying_active({"dry_time": None, "dry_status": None}) is False
        assert is_drying_active({"dry_time": "junk", "dry_status": "junk"}) is False

    def test_non_mapping_input_is_not_active(self):
        assert is_drying_active(None) is False
        assert is_drying_active("drying") is False
        assert is_drying_active(42) is False


class TestTemperatureAlarmSuppressed:
    """Latch behaviour for the AMS high-temperature alarm during drying (#1802)."""

    NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    GRACE = 120

    def _call(self, **overrides):
        kwargs = {
            "drying_active": False,
            "temperature": 50.0,
            "threshold": 35.0,
            "latched_at": None,
            "now": self.NOW,
            "grace_minutes": self.GRACE,
        }
        kwargs.update(overrides)
        return temperature_alarm_suppressed(**kwargs)

    def test_no_drying_no_latch_alarms_normally(self):
        # The pre-#1802 behaviour has to survive untouched for units that never dry.
        suppress, latch = self._call(temperature=40.0)
        assert suppress is False
        assert latch is None

    def test_drying_suppresses_and_sets_latch(self):
        suppress, latch = self._call(drying_active=True, temperature=65.0)
        assert suppress is True
        assert latch == self.NOW

    def test_drying_latches_even_when_below_threshold(self):
        # Early in a cycle the unit is still heating up. The latch has to be set
        # then too, or the cool-down afterwards starts unprotected.
        suppress, latch = self._call(drying_active=True, temperature=28.0)
        assert suppress is True
        assert latch == self.NOW

    def test_still_hot_after_cycle_stays_suppressed(self):
        # The reported symptom: alarms kept arriving while the unit cooled.
        suppress, latch = self._call(
            temperature=52.0,
            latched_at=self.NOW - timedelta(minutes=20),
        )
        assert suppress is True
        assert latch == self.NOW - timedelta(minutes=20)

    def test_cooled_back_to_normal_clears_latch(self):
        suppress, latch = self._call(
            temperature=34.0,
            latched_at=self.NOW - timedelta(minutes=40),
        )
        assert suppress is False
        assert latch is None

    def test_exactly_at_threshold_counts_as_cooled(self):
        # The alarm itself fires on `> threshold`, so `== threshold` is not hot.
        suppress, latch = self._call(
            temperature=35.0,
            latched_at=self.NOW - timedelta(minutes=40),
        )
        assert suppress is False
        assert latch is None

    def test_alarms_again_after_the_latch_is_cleared(self):
        # Having cooled once, a later genuine overheat is not swallowed.
        _, latch = self._call(temperature=34.0, latched_at=self.NOW - timedelta(minutes=40))
        suppress, latch = self._call(temperature=48.0, latched_at=latch)
        assert suppress is False
        assert latch is None

    def test_grace_cap_releases_a_unit_that_never_cools(self):
        # A unit stuck above the threshold would have alarmed with no drying
        # involved, so the cap restores that rather than inventing an alert.
        suppress, latch = self._call(
            temperature=45.0,
            latched_at=self.NOW - timedelta(minutes=self.GRACE + 1),
        )
        assert suppress is False
        assert latch is None

    def test_grace_cap_boundary_releases(self):
        suppress, _ = self._call(
            temperature=45.0,
            latched_at=self.NOW - timedelta(minutes=self.GRACE),
        )
        assert suppress is False

    def test_just_inside_the_grace_cap_still_suppresses(self):
        suppress, _ = self._call(
            temperature=45.0,
            latched_at=self.NOW - timedelta(minutes=self.GRACE - 1),
        )
        assert suppress is True

    def test_a_new_cycle_refreshes_the_latch(self):
        # Starting a second dry inside the grace window must restart the clock,
        # otherwise the cap could expire midway through the new cycle.
        suppress, latch = self._call(
            drying_active=True,
            temperature=60.0,
            latched_at=self.NOW - timedelta(minutes=self.GRACE - 5),
        )
        assert suppress is True
        assert latch == self.NOW

    def test_unreadable_temperature_holds_the_latch(self):
        # A dropped reading is not evidence the unit cooled, and there is no
        # alarm to fire on this pass anyway.
        suppress, latch = self._call(
            temperature=None,
            latched_at=self.NOW - timedelta(minutes=10),
        )
        assert suppress is True
        assert latch == self.NOW - timedelta(minutes=10)

    def test_the_cap_is_measured_from_the_latch(self):
        # Guards the precondition the loader's clamp exists to maintain: with a
        # non-future latch, suppression expires exactly one cap after it, so the
        # cap is a real bound rather than a floor. A future latch would push the
        # release out by the skew as well, which is why the clamp is at the read
        # — see _load_ams_drying_latch and its persistence tests.
        latched = self.NOW - timedelta(minutes=self.GRACE)
        suppress, latch = temperature_alarm_suppressed(
            drying_active=False,
            temperature=45.0,
            threshold=35.0,
            latched_at=latched,
            now=self.NOW,
            grace_minutes=self.GRACE,
        )
        assert suppress is False
        assert latch is None
