"""Where the print file went, read off the report topic (#1820).

Until this landed, ``current_project_url`` was assigned in exactly one place --
``_handle_request_message`` -- and ``_on_message`` calls that only for the
request topic. A print started from the printer's own touchscreen publishes
nothing there, so the field stayed None for the one case the storage verdict in
``print_storage`` was written for. The verdict then fell through to the
``sdcard`` fallback, and #1820's H2S reports ``sdcard: true`` (its "card" is the
internal eMMC), so every such print swept ~110 doomed FTPS connections and
archived blank with no stated reason.

The printer does announce it, as an unsolicited ``project_file`` *response* on
the report topic ~2 s before ``gcode_state`` reaches PREPARE. The frames below
are from that reporter's sanitised capture, taken on an H2S + AMS 2 Pro.
"""

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.app.services.print_storage import (
    REASON_INTERNAL_HISTORY,
    print_file_reachable_over_ftp,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    return BambuMQTTClient(ip_address="10.0.0.7", serial_number="H2S1820", access_code="12345678", model="H2S")


def screen_start(**overrides) -> dict:
    """The frame an H2S publishes for a print started from its own screen.

    Trimmed of the fields nothing here reads (``ams``, ``vt_tray``, the
    calibration flags); everything kept is verbatim from the capture, including
    the empty ``subtask_name`` and the printer's own ``sequence_id`` counter --
    the two things that distinguish it from a slicer's dispatch.
    """
    print_data = {
        "command": "project_file",
        "result": "SUCCESS",
        "reason": "SUCCESS",
        "err_code": 0,
        "sequence_id": "3338",
        "subtask_name": "",
        "task_type": 1,
        "param": "Metadata/plate_1.gcode",
        "plate": 1,
        "ams_mapping": [0],
        "mapping": [1],
        "url": "file:///userdata/model/history/JOB_A.gcode.3mf",
    }
    print_data.update(overrides)
    return {"print": print_data}


class TestAScreenStartedPrintNamesItsFile:
    def test_the_url_is_captured(self, client):
        client._process_message(screen_start())

        assert client.state.current_project_url == "file:///userdata/model/history/JOB_A.gcode.3mf"

    def test_the_sticky_copy_is_captured_too(self, client):
        """The connection diagnostic runs after the print, by which point the
        per-print field has been cleared."""
        client._process_message(screen_start())

        assert client.state.last_project_url == "file:///userdata/model/history/JOB_A.gcode.3mf"

    def test_the_verdict_now_skips_the_sweep(self, client):
        """The whole point, in one assertion: with the URL in hand the H2S's
        ``sdcard: true`` no longer decides the outcome."""
        client.state.sdcard = True
        client.state.sdcard_reported = True

        client._process_message(screen_start())
        verdict = print_file_reachable_over_ftp(client.state)

        assert verdict.reachable is False
        assert verdict.reason == REASON_INTERNAL_HISTORY
        # Still probed, because this printer keeps screen-started jobs under
        # /cache for a while and that copy archives in full when it is there.
        assert verdict.probe_filename == "JOB_A.gcode.3mf"

    def test_the_mapping_is_captured_when_no_slicer_sent_one(self, client):
        """On a screen start this frame is the only place it appears."""
        client._process_message(screen_start())

        assert client._captured_ams_mapping == [0]

    def test_a_mapping_from_the_request_topic_wins(self, client):
        """The slicer's own mapping describes the same print and arrived first;
        the echo can carry a different shape or none at all."""
        client._captured_ams_mapping = [0, -1, -1, -1]

        client._process_message(screen_start(ams_mapping=[3]))

        assert client._captured_ams_mapping == [0, -1, -1, -1]


class TestWhatMustNotBeCaptured:
    def test_a_refused_dispatch_is_ignored(self, client):
        """It names a file that was never written. Acting on it would pin the
        next print's archive on a destination nothing went to."""
        client._process_message(screen_start(result="FAIL", reason="STORAGE_FULL"))

        assert client.state.current_project_url is None
        assert client._captured_ams_mapping is None

    @pytest.mark.parametrize("url", ["", None, 12345, [], {}])
    def test_a_missing_or_non_string_url_is_ignored(self, client, url):
        """The value is whatever the sender put on the wire."""
        client._process_message(screen_start(url=url))

        assert client.state.current_project_url is None

    def test_another_command_on_the_same_topic_is_ignored(self, client):
        """push_status carries a `url` field of its own on some firmwares."""
        client._process_message({"print": {"command": "push_status", "url": "file:///userdata/model/history/x.3mf"}})

        assert client.state.current_project_url is None

    def test_our_own_dispatch_is_not_logged_as_someone_elses(self, client, caplog):
        """Our publish is echoed on *both* topics. The request-topic echo lands
        first and clears ``_own_project_file_key``, so a report-topic branch
        that reused ``_handle_request_message``'s diagnostic would report every
        Bambuddy-started print as an external one.
        """
        dispatch = {
            "command": "project_file",
            "sequence_id": "20002",
            "file": "JOB_NORMAL.gcode.3mf",
            "url": "brtc://emmc/JOB_NORMAL.gcode.3mf",
            "subtask_name": "JOB_NORMAL",
        }
        client._own_project_file_key = client._project_file_key(dispatch)
        client._handle_request_message({"print": dispatch})
        assert client._own_project_file_key is None

        caplog.clear()
        client._process_message({"print": {**dispatch, "result": "SUCCESS", "is_from_mqtt": True}})

        assert "External project_file payload" not in caplog.text
        # ...and the capture still happened, which is what makes it worth having
        # on this topic at all: some brokers refuse the request subscription.
        assert client.state.current_project_url == "brtc://emmc/JOB_NORMAL.gcode.3mf"


class TestTheSlicerPathIsUnchanged:
    def test_an_external_dispatch_still_sweeps(self, client):
        """The regression to fear: routing more URLs into the matcher must not
        turn an ordinary Studio print into a blank archive."""
        client.state.sdcard = True
        client.state.sdcard_reported = True

        client._process_message(
            {"print": {"command": "project_file", "result": "SUCCESS", "url": "ftp://JOB_NORMAL.gcode.3mf"}}
        )

        assert print_file_reachable_over_ftp(client.state).reachable is True
