"""A print stage Bambuddy cannot name has to leave a trace at the default log level.

``STAGE_NAMES`` is a hand-maintained table and every new printer adds to it: an
H2C first print surfaced stage 72, where the table runs 0-66 and then jumps
straight to 74. The card showed "Unknown stage (72)" and there was nothing
behind it -- stage transitions are logged at DEBUG, which is off in normal
running, so the only record of the event was a screenshot.

The asymmetry is the point. A stage we can name is worth DEBUG; one we cannot
is the interesting one, and it is the one that was invisible. Naming it later
needs the number, the model, the stage it came from and what the printer was
doing, so all of that is recorded -- once per stage number, because a stage can
be entered repeatedly in one print.
"""

import logging

import pytest

from backend.app.services.bambu_mqtt import STAGE_NAMES, BambuMQTTClient


@pytest.fixture
def client():
    return BambuMQTTClient(
        ip_address="192.168.1.100",
        serial_number="TEST-H2C",
        access_code="12345678",
        model="H2C",
    )


def _stage_records(caplog):
    return [r for r in caplog.records if "Unnamed print stage" in r.getMessage()]


class TestUnnamedStageIsReported:
    def test_the_h2c_stage_that_prompted_this(self, client, caplog):
        with caplog.at_level(logging.INFO):
            client._update_state({"stg_cur": 72})
        records = _stage_records(caplog)
        assert len(records) == 1
        message = records[0].getMessage()
        assert "72" in message
        assert "H2C" in message

    def test_the_message_carries_what_naming_it_later_needs(self, client, caplog):
        client._update_state({"gcode_state": "RUNNING", "layer_num": 7, "total_layer_num": 240})
        with caplog.at_level(logging.INFO):
            client._update_state({"stg_cur": 72})
        message = _stage_records(caplog)[0].getMessage()
        # Where it came from, named, so a sequence can be reconstructed from
        # several of these lines rather than only the stage in isolation.
        assert "entered from -1" in message
        assert "layer=7/240" in message

    def test_reported_once_per_stage_not_once_per_transition(self, client, caplog):
        """A stage can be entered repeatedly within a single print."""
        with caplog.at_level(logging.INFO):
            client._update_state({"stg_cur": 72})
            client._update_state({"stg_cur": 0})
            client._update_state({"stg_cur": 72})
        assert len(_stage_records(caplog)) == 1

    def test_a_second_unnamed_stage_is_still_reported(self, client, caplog):
        with caplog.at_level(logging.INFO):
            client._update_state({"stg_cur": 72})
            client._update_state({"stg_cur": 71})
        assert len(_stage_records(caplog)) == 2


class TestQuietWhereItShouldBe:
    @pytest.mark.parametrize("stage", [0, 22, 39, 66, 74])
    def test_a_stage_we_can_name_says_nothing_at_info(self, client, caplog, stage):
        assert stage in STAGE_NAMES
        with caplog.at_level(logging.INFO):
            client._update_state({"stg_cur": stage})
        assert _stage_records(caplog) == []

    def test_idle_is_not_an_unnamed_stage(self, client, caplog):
        """-1 is Bambuddy's own "not in a stage" sentinel, not a firmware value."""
        with caplog.at_level(logging.INFO):
            client._update_state({"stg_cur": 0})
            client._update_state({"stg_cur": -1})
        assert _stage_records(caplog) == []

    @pytest.mark.parametrize("junk", ["72", 72.5, None, True, [72]])
    def test_a_non_integer_stage_is_not_reported_and_never_raises(self, client, caplog, junk):
        """The field is whatever the firmware sent, and this runs on every push."""
        with caplog.at_level(logging.INFO):
            client._update_state({"stg_cur": junk})
        assert _stage_records(caplog) == []
