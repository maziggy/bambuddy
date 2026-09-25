"""Tests for daemon.nfc_reader — tag presence state machine.

The PN5180 hardware driver is unavailable off the Pi (spidev/RPi.GPIO), so
NFCReader() constructs in its degraded no-hardware mode and the tests inject a
fake PN5180 that returns scripted activate_type_a() results.
"""

from daemon.nfc_reader import MISS_THRESHOLD, NFCReader, NFCState

UID_A = bytes.fromhex("72DB77EB")
UID_B = bytes.fromhex("662AC3E6")
NTAG_SAK = 0x00


class FakePN5180:
    """Scripted PN5180: activate_type_a() pops from a queue of results."""

    def __init__(self, results):
        self.results = list(results)

    def activate_type_a(self):
        return self.results.pop(0) if self.results else None

    # RF plumbing called by poll()/_init_rf — no-ops for the fake.
    def reset(self):
        pass

    def load_rf_config(self, tx, rx):
        pass

    def rf_on(self):
        pass

    def rf_off(self):
        pass

    def set_transceive_mode(self):
        pass


def make_reader(results):
    reader = NFCReader()
    reader._nfc = FakePN5180(results)
    return reader


def test_tag_detected_from_idle():
    reader = make_reader([(UID_A, NTAG_SAK)])
    event, data = reader.poll()
    assert event == "tag_detected"
    assert data["tag_uid"] == "72DB77EB"
    assert data["tag_type"] == "ntag"
    assert reader.state == NFCState.TAG_PRESENT


def test_same_tag_still_present_no_event():
    reader = make_reader([(UID_A, NTAG_SAK), (UID_A, NTAG_SAK)])
    reader.poll()
    event, data = reader.poll()
    assert (event, data) == ("none", None)
    assert reader.current_uid == "72DB77EB"


def test_tag_removed_after_miss_threshold():
    reader = make_reader([(UID_A, NTAG_SAK)] + [None] * MISS_THRESHOLD)
    reader.poll()
    events = [reader.poll()[0] for _ in range(MISS_THRESHOLD)]
    assert events == ["none"] * (MISS_THRESHOLD - 1) + ["tag_removed"]
    assert reader.state == NFCState.IDLE
    assert reader.current_uid is None


def test_fast_tag_swap_reports_removed_then_detected():
    # Regression: a different UID appearing while a tag is considered present
    # (rolls swapped faster than MISS_THRESHOLD could declare removal) must not
    # be silently treated as "still present" — the kiosk would keep showing the
    # old tag forever and attach it to the next added spool.
    reader = make_reader([(UID_A, NTAG_SAK), (UID_B, NTAG_SAK), (UID_B, NTAG_SAK)])
    assert reader.poll()[0] == "tag_detected"

    event, data = reader.poll()
    assert event == "tag_removed"
    assert data["tag_uid"] == "72DB77EB"
    assert reader.state == NFCState.IDLE

    event, data = reader.poll()
    assert event == "tag_detected"
    assert data["tag_uid"] == "662AC3E6"


def test_miss_below_threshold_then_same_tag_no_removal():
    reader = make_reader([(UID_A, NTAG_SAK), None, (UID_A, NTAG_SAK)])
    reader.poll()
    assert reader.poll() == ("none", None)
    assert reader.poll() == ("none", None)
    assert reader.current_uid == "72DB77EB"
