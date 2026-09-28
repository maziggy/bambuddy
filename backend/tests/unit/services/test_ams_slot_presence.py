"""Unit tests for the firmware presence bit helper.

#3084: a slot can read ``exists=True, state=9`` — the bit says a spool is in
it, the state says the opposite — because ``apply_tray_exist_bits`` writes the
9 itself and never takes it back. Everything downstream has to know which of
the two to believe, and this helper is the single place that says so.
"""

from backend.app.services.ams_slot_presence import spool_present


class TestSpoolPresent:
    def test_the_bit_is_reported_as_it_stands(self):
        assert spool_present({"id": 0, "exists": True}) is True
        assert spool_present({"id": 0, "exists": False}) is False

    def test_the_bit_answers_over_a_contradicting_state(self):
        # The #3084 slot: non-Bambu spool swapped in, so the bit is set, while
        # the 9 Bambuddy stamped on the slot when it was briefly empty is still
        # sitting there.
        assert spool_present({"id": 0, "exists": True, "state": 9}) is True
        # And the converse — a spool pulled from a slot the firmware last
        # described as loaded.
        assert spool_present({"id": 0, "exists": False, "state": 11}) is False

    def test_a_tray_with_no_annotation_answers_nothing(self):
        # vt_tray entries and the VP bridge's cache carry no presence bit, so
        # callers have to fall back to their own reading rather than be handed
        # a guess dressed up as firmware's answer.
        assert spool_present({"id": 0, "state": 11, "tray_type": "PLA"}) is None
        assert spool_present({}) is None

    def test_a_non_bool_exists_is_not_a_presence_bit(self):
        # Only apply_tray_exist_bits writes this key, and it writes a bool.
        # Anything else reached the dict some other way and is not firmware's
        # answer — 0 and "" would otherwise read as a confident "empty".
        assert spool_present({"exists": 0}) is None
        assert spool_present({"exists": ""}) is None
        assert spool_present({"exists": "true"}) is None
        assert spool_present({"exists": None}) is None

    def test_a_missing_tray_answers_nothing(self):
        assert spool_present(None) is None
        assert spool_present([]) is None
        assert spool_present("tray") is None
