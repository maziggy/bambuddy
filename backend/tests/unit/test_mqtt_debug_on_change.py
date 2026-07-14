"""Per-frame debug dumps must log transitions, not every frame (#2555).

The state dumps in the push_status handler fired whenever their field was
*present* in the frame. A full push_status carries every field, so they fired on
every frame regardless of whether anything had changed — several while their own
comment claimed to log "when X changes".

On one printer that is ~1.5 lines/s and nobody noticed. On a 19-printer farm it
is ~100 lines/s: the reporter turned on debug logging as asked, and the 5 MB log
rolled over in under five minutes. 27,727 of the 29,830 lines in the support
bundle were these dumps, and the queue problem we were chasing was nowhere in the
window.
"""

import logging
from unittest.mock import MagicMock, patch

from backend.app.services.bambu_mqtt import BambuMQTTClient


def _client() -> BambuMQTTClient:
    return BambuMQTTClient(ip_address="10.0.0.1", serial_number="SERIAL", access_code="code", model="A1")


class TestDebugOnChange:
    def test_repeated_identical_values_log_once(self):
        client = _client()
        with patch("backend.app.services.bambu_mqtt.logger") as log:
            for _ in range(50):
                client._debug_on_change("wifi_signal", -52, "[%s] wifi_signal: %s", "SERIAL", -52)

        assert log.debug.call_count == 1, (
            f"50 identical frames produced {log.debug.call_count} log lines — this is the flood"
        )

    def test_each_change_is_logged(self):
        """Suppressing repeats must not suppress transitions — the transitions are
        the entire reason anyone reads these lines."""
        client = _client()
        with patch("backend.app.services.bambu_mqtt.logger") as log:
            for value in (-52, -52, -60, -60, -52):
                client._debug_on_change("wifi_signal", value, "[%s] wifi_signal: %s", "SERIAL", value)

        assert log.debug.call_count == 3
        assert [c.args[-1] for c in log.debug.call_args_list] == [-52, -60, -52]

    def test_keys_are_tracked_independently(self):
        client = _client()
        with patch("backend.app.services.bambu_mqtt.logger") as log:
            client._debug_on_change("tray_now", 1, "tray_now: %s", 1)
            client._debug_on_change("ams_status", 1, "ams_status: %s", 1)
            client._debug_on_change("tray_now", 1, "tray_now: %s", 1)  # repeat, suppressed

        assert log.debug.call_count == 2, "same value under a different key must not be swallowed"

    def test_printers_are_tracked_independently(self):
        """State is per-client. Two printers reporting the same value must each
        get their own line — a farm is exactly where this matters."""
        a, b = _client(), _client()
        with patch("backend.app.services.bambu_mqtt.logger") as log:
            a._debug_on_change("tray_now", 3, "tray_now: %s", 3)
            b._debug_on_change("tray_now", 3, "tray_now: %s", 3)

        assert log.debug.call_count == 2

    def test_composite_values_detect_a_change_in_any_field(self):
        """Messages that render several fields must pass all of them, or a change
        in the unwatched field is silently dropped."""
        client = _client()
        with patch("backend.app.services.bambu_mqtt.logger") as log:
            client._debug_on_change("chamber", (40.0, 0.0, False), "chamber %s %s %s", 40.0, 0.0, False)
            client._debug_on_change("chamber", (40.0, 60.0, True), "chamber %s %s %s", 40.0, 60.0, True)

        assert log.debug.call_count == 2, "target/heating changed while current stayed 40.0 — must still log"

    def test_dict_values_compare_by_content(self):
        """The AMS dict dump is the biggest line by volume; it is a fresh dict every
        frame, so identity comparison would never suppress anything."""
        client = _client()
        with patch("backend.app.services.bambu_mqtt.logger") as log:
            for _ in range(10):
                client._debug_on_change("ams", {"tray_now": "0", "bits": "7000000"}, "ams: %s", {})
            client._debug_on_change("ams", {"tray_now": "1", "bits": "7000000"}, "ams: %s", {})

        assert log.debug.call_count == 2


class TestRuntimeDebugToggle:
    """Debug logging is turned on at RUNTIME (POST /support/debug-logging) and these
    clients outlive the toggle — which is the whole workflow this change serves:
    "enable debug logging, reproduce, send the bundle".

    So the cache must not be warmed while running at INFO. If it were, the operator
    would enable debug, and every steady-state value would already be "seen" — an
    idle printer's bundle would contain none of these lines at all, which is worse
    than the flood it replaced.
    """

    def test_enabling_debug_at_runtime_still_dumps_a_baseline(self):
        client = _client()
        mqtt_logger = logging.getLogger("backend.app.services.bambu_mqtt")
        original = mqtt_logger.level
        try:
            # Steady state at INFO: the app has been running for hours.
            mqtt_logger.setLevel(logging.INFO)
            with patch("backend.app.services.bambu_mqtt.logger", wraps=mqtt_logger) as log:
                for _ in range(200):
                    client._debug_on_change("wifi_signal", -52, "wifi %s", -52)
                assert log.debug.call_count == 0, "nothing should be emitted at INFO"

            # Operator flips debug on. The value has NOT changed — but they turned
            # this on to see the printer's state, so the very next frame must dump it.
            mqtt_logger.setLevel(logging.DEBUG)
            with patch("backend.app.services.bambu_mqtt.logger", wraps=mqtt_logger) as log:
                client._debug_on_change("wifi_signal", -52, "wifi %s", -52)
                assert log.debug.call_count == 1, (
                    "no baseline after enabling debug — the cache was warmed while at "
                    "INFO, so the operator sees nothing until the value happens to change"
                )
                # ...and it still dedups from there.
                for _ in range(50):
                    client._debug_on_change("wifi_signal", -52, "wifi %s", -52)
                assert log.debug.call_count == 1
        finally:
            mqtt_logger.setLevel(original)

    def test_disabling_debug_drops_the_cache(self):
        """Off -> on must be as cold as a fresh process, not just first-ever-on."""
        client = _client()
        mqtt_logger = logging.getLogger("backend.app.services.bambu_mqtt")
        original = mqtt_logger.level
        try:
            mqtt_logger.setLevel(logging.DEBUG)
            client._debug_on_change("tray_now", 2, "tray_now %s", 2)
            assert client._debug_last

            mqtt_logger.setLevel(logging.INFO)
            client._debug_on_change("tray_now", 2, "tray_now %s", 2)
            assert client._debug_last == {}, "cache must be dropped while debug is off"
        finally:
            mqtt_logger.setLevel(original)


class TestRealDumpSitesAreGated:
    """End-to-end: feed the same push_status frame twice and count the lines."""

    def test_identical_push_status_frames_do_not_re_dump_state(self):
        # Deliberately the client's real PrinterState, not a mock: a MagicMock
        # state would return the same stub object for every attribute read, so
        # the values would compare equal and the test would pass even with the
        # gating removed.
        client = _client()
        assert not isinstance(client.state, MagicMock)
        frame = {
            "print": {
                "ams": {
                    "ams": [],
                    "ams_exist_bits": "1",
                    "tray_exist_bits": "f",
                    "tray_now": "0",
                },
                "wifi_signal": "-52dBm",
                "ipcam": {"ipcam_record": "enable"},
            }
        }

        logging.getLogger("backend.app.services.bambu_mqtt").setLevel(logging.DEBUG)
        with patch("backend.app.services.bambu_mqtt.logger") as log:
            log.isEnabledFor.return_value = True
            client._process_message(dict(frame))
            first = [c.args[0] for c in log.debug.call_args_list]
            log.debug.reset_mock()
            client._process_message(dict(frame))
            second = [c.args[0] for c in log.debug.call_args_list]

        # Frame 1 must still dump — the point is to log transitions, not to go quiet.
        assert first, "the first frame stopped dumping state entirely — the logs are now useless"

        # Frame 2 is byte-identical, so it must produce NOTHING. Asserting merely
        # "fewer than frame 1" is not enough: a couple of these sites happen to be
        # naturally one-shot, so an ungated build still measures 3 < 5 and the
        # assertion passes while every real dump keeps firing on every frame.
        assert second == [], f"an identical push_status frame re-dumped {len(second)} line(s): {second}"
