"""Capturing where the printer put the sliced file (#2780).

The ``project_file`` command has always carried a ``url`` saying which storage
the file landed on -- ``ftp://<name>`` for the card, ``brtc://emmc/<name>`` for
the printer's internal storage. Bambuddy discarded it and swept FTPS regardless,
which on an H2C or P2S is ~110 connections that cannot succeed followed by a
blank archive card with no stated reason.

These tests pin the capture itself. The decision made from it lives in
:mod:`backend.app.services.print_storage` and is tested separately.
"""

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def mqtt_client():
    from backend.app.services.bambu_mqtt import BambuMQTTClient

    return BambuMQTTClient(ip_address="192.168.1.210", serial_number="TEST2780", access_code="12345678")


class TestProjectUrlCapture:
    def test_nothing_is_claimed_before_a_dispatch_is_seen(self, mqtt_client):
        """None is the "we don't know" answer, and consumers depend on it
        being distinguishable from a known-bad value."""
        assert mqtt_client.state.current_project_url is None

    def test_an_internal_storage_dispatch_is_recorded(self, mqtt_client):
        """The shape every H2C and P2S dispatch had in #2780's bundle."""
        mqtt_client._handle_request_message(
            {
                "print": {
                    "command": "project_file",
                    "url": "brtc://emmc/169356_204314.STEP.gcode.3mf",
                    "sequence_id": "20002",
                }
            }
        )

        assert mqtt_client.state.current_project_url == "brtc://emmc/169356_204314.STEP.gcode.3mf"

    def test_our_own_dispatch_is_recorded_too(self, mqtt_client):
        """We publish to the request topic and subscribe to it, so our own
        commands come back. Capturing them is wanted, not incidental: after a
        Bambuddy-launched print the file really is on external storage, and a
        stale internal-storage URL from the slicer's last job would otherwise
        suppress a sweep that would have worked.

        ``sequence_id`` 20000 is ours; the external-payload log line keys off
        that, and this capture must not.
        """
        mqtt_client._handle_request_message(
            {"print": {"command": "project_file", "url": "ftp://Benchy.gcode.3mf", "sequence_id": "20000"}}
        )

        assert mqtt_client.state.current_project_url == "ftp://Benchy.gcode.3mf"

    def test_the_latest_dispatch_wins(self, mqtt_client):
        for url in ("brtc://emmc/first.3mf", "ftp://second.3mf"):
            mqtt_client._handle_request_message({"print": {"command": "project_file", "url": url}})

        assert mqtt_client.state.current_project_url == "ftp://second.3mf"

    def test_a_dispatch_without_a_url_leaves_the_last_one_alone(self, mqtt_client):
        """Better a slightly stale answer than a wrongly-cleared one: clearing
        would drop us to "unknown" and re-run the sweep we just learned was
        pointless."""
        mqtt_client._handle_request_message({"print": {"command": "project_file", "url": "brtc://emmc/x.3mf"}})
        mqtt_client._handle_request_message({"print": {"command": "project_file", "ams_mapping": [0]}})

        assert mqtt_client.state.current_project_url == "brtc://emmc/x.3mf"

    @pytest.mark.parametrize("url", [None, "", 12345, [], {}])
    def test_a_non_string_url_is_ignored(self, mqtt_client, url):
        """Straight off the wire, so it is whatever the sender sent."""
        mqtt_client._handle_request_message({"print": {"command": "project_file", "url": url}})

        assert mqtt_client.state.current_project_url is None

    def test_other_commands_do_not_touch_it(self, mqtt_client):
        mqtt_client._handle_request_message({"print": {"command": "project_file", "url": "ftp://kept.3mf"}})
        for command in ("pause", "resume", "stop", "gcode_line"):
            mqtt_client._handle_request_message({"print": {"command": command, "url": "ftp://ignored.3mf"}})

        assert mqtt_client.state.current_project_url == "ftp://kept.3mf"


class TestTheTwoFieldsHaveDifferentLifetimes:
    """``current_project_url`` describes the print now running and is cleared
    when it ends; ``last_project_url`` is sticky, for reporting after the fact.

    The clearing is the load-bearing half. In #2780's support bundle 14 of 79
    print starts arrived with no ``project_file`` on the request topic at all
    -- touchscreen reprints and restart recovery. If those inherited the
    previous job's destination, one Studio print to internal storage would
    suppress the FTPS sweep for every screen-started print after it.
    """

    def test_a_dispatch_sets_both(self, mqtt_client):
        mqtt_client._handle_request_message({"print": {"command": "project_file", "url": "brtc://emmc/x.3mf"}})

        assert mqtt_client.state.current_project_url == "brtc://emmc/x.3mf"
        assert mqtt_client.state.last_project_url == "brtc://emmc/x.3mf"

    def test_finishing_a_print_clears_only_the_per_print_one(self, mqtt_client):
        mqtt_client._handle_request_message({"print": {"command": "project_file", "url": "brtc://emmc/x.3mf"}})
        # Drive a full RUNNING -> FINISH transition so the real completion
        # path runs, rather than reaching in and clearing the field by hand.
        mqtt_client.on_print_complete = lambda data: None
        mqtt_client._update_state({"gcode_state": "RUNNING"})
        mqtt_client._update_state({"gcode_state": "FINISH"})

        assert mqtt_client.state.current_project_url is None, (
            "a print Bambuddy saw no dispatch for must read as unknown, not inherit this one"
        )
        assert mqtt_client.state.last_project_url == "brtc://emmc/x.3mf", (
            "the diagnostic runs after the print that prompted it and still needs the answer"
        )

    def test_an_aborted_print_clears_it_too(self, mqtt_client):
        mqtt_client._handle_request_message({"print": {"command": "project_file", "url": "brtc://emmc/x.3mf"}})
        mqtt_client.on_print_complete = lambda data: None
        mqtt_client._update_state({"gcode_state": "RUNNING"})
        mqtt_client._update_state({"gcode_state": "FAILED"})

        assert mqtt_client.state.current_project_url is None

    def test_the_regression_sequence_end_to_end(self, mqtt_client):
        """Studio print to internal storage, then a print started from the
        printer's own screen.

        The capture and the gate are pinned separately; this is the sequence
        that made the split necessary, walked through both at once. The second
        print's file may well be on the card -- it has to be swept for, not
        written off on the strength of the first print's destination.
        """
        from backend.app.services.print_storage import print_file_reachable_over_ftp

        mqtt_client._update_state({"sdcard": True})
        mqtt_client.on_print_complete = lambda data: None

        # 1. Studio dispatches to internal storage and the print runs.
        mqtt_client._handle_request_message({"print": {"command": "project_file", "url": "brtc://emmc/studio.3mf"}})
        mqtt_client._update_state({"gcode_state": "RUNNING"})
        assert print_file_reachable_over_ftp(mqtt_client.state).reachable is False

        # 2. It finishes.
        mqtt_client._update_state({"gcode_state": "FINISH"})

        # 3. The operator reprints from the touchscreen. No project_file
        #    reaches the request topic at all -- 14 of 79 print starts in the
        #    reporter's bundle looked exactly like this.
        mqtt_client._update_state({"gcode_state": "RUNNING"})

        assert print_file_reachable_over_ftp(mqtt_client.state).reachable, (
            "the screen-started print inherited the Studio print's destination and lost its archive"
        )


class TestSdcardReported:
    """`sdcard` defaults to False, so "no card" and "never said" look alike.

    Every consumer that treats False as evidence needs the difference, and
    getting it wrong skips FTP sweeps for printers whose archives work fine.
    """

    def test_nothing_is_claimed_before_a_status_frame(self, mqtt_client):
        assert mqtt_client.state.sdcard_reported is False
        assert mqtt_client.state.sdcard is False

    def test_a_frame_carrying_the_field_marks_it_reported(self, mqtt_client):
        mqtt_client._update_state({"sdcard": False})

        assert mqtt_client.state.sdcard_reported is True
        assert mqtt_client.state.sdcard is False

    def test_a_frame_without_the_field_does_not(self, mqtt_client):
        mqtt_client._update_state({"gcode_state": "RUNNING"})

        assert mqtt_client.state.sdcard_reported is False

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (True, True),
            (1, True),
            ("HAS_SDCARD_NORMAL", True),
            ("normal", True),
            ("HAS_SDCARD_ABNORMAL", True),
            (False, False),
            (0, False),
            ("", False),
        ],
    )
    def test_the_reported_flag_is_set_for_every_accepted_shape(self, mqtt_client, raw, expected):
        """Whatever the field means, having seen it is what is recorded."""
        mqtt_client._update_state({"sdcard": raw})

        assert mqtt_client.state.sdcard_reported is True
        assert mqtt_client.state.sdcard is expected
