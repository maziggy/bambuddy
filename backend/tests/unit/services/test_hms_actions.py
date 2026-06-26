"""Tests for HMS-action lookup and the MQTT dispatcher in execute_hms_action.

The lookup tests confirm the bundled catalog round-trips correctly. The
dispatcher tests are payload-shape contracts — wrong shape sends a bogus
command to the printer, which is the failure mode this PR is most exposed to,
so each HMSAction case publishes the expected JSON.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.app.services.hms_actions import (
    HMSAction,
    get_actions_for_error_code,
)


class TestActionLookup:
    def test_known_a1_error_returns_actions(self):
        # 03W is the A1 model code; 03008070 is "Heat the nozzle…" and Bambu's
        # catalog lists CHECK_ASSISTANT for it.
        actions = get_actions_for_error_code("03W", "03008070")
        assert isinstance(actions, list)
        assert len(actions) > 0
        for a in actions:
            assert isinstance(a, str)

    def test_unknown_device_returns_empty_list(self):
        assert get_actions_for_error_code("ZZZ", "03008070") == []

    def test_unknown_error_returns_empty_list(self):
        # Real model code, made-up error.
        assert get_actions_for_error_code("03W", "DEADBEEF") == []

    def test_underscore_form_does_not_match(self):
        # Caller is responsible for stripping the `_` before lookup. Guards
        # against accidental rewires that pass the underscore form.
        assert get_actions_for_error_code("03W", "0300_8070") == []

    def test_action_enum_values_are_uppercase_strings(self):
        # The catalog stores actions verbatim from BambuStudio. Drift here
        # silently breaks the dispatcher's `match` because StrEnum compares
        # by value.
        assert HMSAction.RESUME_PRINTING == "RESUME_PRINTING"
        assert HMSAction.CANCLE == "CANCLE"  # sic — kept from BambuStudio


class TestExecuteHmsActionDispatch:
    """Each case in the `match` publishes a specific JSON shape. These tests
    pin those shapes so silent regressions surface as test failures, not as
    a printer receiving a malformed command on a live print.
    """

    @pytest.fixture
    def client(self):
        c = BambuMQTTClient(
            ip_address="192.168.1.100",
            serial_number="03W-TEST",
            access_code="12345678",
        )
        c._client = MagicMock()
        c.state.connected = True
        return c

    def _published_commands(self, client):
        """Return the list of `print`/`system` command dicts from publish calls,
        skipping the `pushing.pushall` echoes that follow every action."""
        out = []
        for call in client._client.publish.call_args_list:
            _topic, payload = call.args[0], call.args[1]
            data = json.loads(payload)
            if "pushing" in data:
                continue
            out.append(data)
        return out

    def test_returns_false_when_disconnected(self, client):
        client.state.connected = False
        assert client.execute_hms_action("03008070", HMSAction.OK_BUTTON) is False
        client._client.publish.assert_not_called()

    def test_returns_false_on_unknown_action(self, client):
        assert client.execute_hms_action("03008070", "DOES_NOT_EXIST") is False
        # No printer command, but the publish-list check tolerates the pushall
        # tail — just confirm no command went out by inspecting the helper.
        assert self._published_commands(client) == []

    def test_resume_carries_err_param_and_job_id(self, client):
        ok = client.execute_hms_action("03008070", HMSAction.RESUME_PRINTING, job_id="task-42")
        assert ok is True
        cmds = self._published_commands(client)
        assert cmds == [
            {
                "print": {
                    "command": "resume",
                    "err": "03008070",
                    "param": "reserve",
                    "job_id": "task-42",
                    "sequence_id": "0",
                }
            }
        ]

    def test_proceed_falls_through_to_resume(self, client):
        client.execute_hms_action("03008070", HMSAction.PROCEED, job_id="task-1")
        cmds = self._published_commands(client)
        assert cmds[0]["print"]["command"] == "resume"
        assert cmds[0]["print"]["err"] == "03008070"

    def test_stop_carries_err_and_job_id(self, client):
        client.execute_hms_action("03008070", HMSAction.STOP_PRINTING, job_id="task-1")
        cmds = self._published_commands(client)
        assert cmds[0]["print"]["command"] == "stop"
        assert cmds[0]["print"]["job_id"] == "task-1"

    def test_ignore_resume_uses_idle_ignore_type_zero(self, client):
        client.execute_hms_action("03008070", HMSAction.IGNORE_RESUME)
        cmds = self._published_commands(client)
        assert cmds[0] == {
            "print": {
                "command": "idle_ignore",
                "err": "03008070",
                "type": 0,
                "sequence_id": "0",
            }
        }

    def test_dont_remind_uses_idle_ignore_type_one(self, client):
        # DONT_REMIND_NEXT_TIME and IGNORE_NO_REMINDER_NEXT_TIME are the
        # persistent variants — Bambu hides the warning for future prints.
        client.execute_hms_action("03008070", HMSAction.DONT_REMIND_NEXT_TIME)
        cmds = self._published_commands(client)
        assert cmds[0]["print"]["command"] == "idle_ignore"
        assert cmds[0]["print"]["type"] == 1

    def test_filament_extruded_sends_ams_done(self, client):
        client.execute_hms_action("07008029", HMSAction.FILAMENT_EXTRUDED)
        cmds = self._published_commands(client)
        assert cmds[0] == {"print": {"command": "ams_control", "param": "done", "sequence_id": "0"}}

    def test_retry_sends_ams_resume(self, client):
        client.execute_hms_action("07008029", HMSAction.RETRY_FILAMENT_EXTRUDED)
        cmds = self._published_commands(client)
        assert cmds[0]["print"]["param"] == "resume"
        assert cmds[0]["print"]["command"] == "ams_control"

    def test_abort_sends_ams_abort(self, client):
        client.execute_hms_action("07008029", HMSAction.ABORT)
        cmds = self._published_commands(client)
        assert cmds[0]["print"]["param"] == "abort"

    def test_ok_button_sends_bare_clean_print_error(self, client):
        # Matches the existing `clear_hms_errors` shape — no `print_error` body
        # field, which the original PR mistakenly added.
        client.execute_hms_action("03008070", HMSAction.OK_BUTTON)
        cmds = self._published_commands(client)
        assert cmds[0] == {"print": {"command": "clean_print_error", "sequence_id": "0"}}

    def test_dbl_check_ok_sends_clean_then_uiop_close(self, client):
        client.execute_hms_action("03008070", HMSAction.DBL_CHECK_OK)
        cmds = self._published_commands(client)
        assert len(cmds) == 2
        assert cmds[0]["print"]["command"] == "clean_print_error"
        assert cmds[1]["system"]["command"] == "uiop"
        # `err` is the already-string short code, NOT `f"{x:08X}"` against a
        # str (which would TypeError on the old code path).
        assert cmds[1]["system"]["err"] == "03008070"

    def test_uiop_close_uppercases_lowercase_input(self, client):
        # Frontend may send the short code in either case; we normalise.
        client.execute_hms_action("0300abcd", HMSAction.DBL_CHECK_OK)
        cmds = self._published_commands(client)
        assert cmds[1]["system"]["err"] == "0300ABCD"

    def test_dbl_check_resume_is_plain_resume(self, client):
        # No err/job_id — explicitly different from RESUME_PRINTING.
        client.execute_hms_action("03008070", HMSAction.DBL_CHECK_RESUME)
        cmds = self._published_commands(client)
        assert cmds[0] == {"print": {"command": "resume", "param": "", "sequence_id": "0"}}
        assert "err" not in cmds[0]["print"]

    def test_refresh_nozzle(self, client):
        client.execute_hms_action("03008070", HMSAction.REFRESH_NOZZLE)
        cmds = self._published_commands(client)
        assert cmds[0] == {"print": {"command": "refresh_nozzle", "sequence_id": "0"}}

    def test_turn_off_fire_alarm_sends_buzzer_off(self, client):
        client.execute_hms_action("03008044", HMSAction.TURN_OFF_FIRE_ALARM)
        cmds = self._published_commands(client)
        assert cmds[0]["print"]["command"] == "buzzer_ctrl"
        assert cmds[0]["print"]["mode"] == 0

    def test_stop_drying_sends_auto_stop_ams_dry(self, client):
        client.execute_hms_action("07008017", HMSAction.STOP_DRYING)
        cmds = self._published_commands(client)
        assert cmds[0]["print"]["command"] == "auto_stop_ams_dry"

    def test_disable_purification_sends_close_air_filt(self, client):
        client.execute_hms_action("03008063", HMSAction.DISABLE_PURIFICATION)
        cmds = self._published_commands(client)
        assert cmds[0]["print"]["command"] == "close_air_filt"

    @pytest.mark.parametrize(
        "action",
        [
            HMSAction.CHECK_ASSISTANT,
            HMSAction.JUMP_TO_LIVEVIEW,
            HMSAction.OK_JUMP_RACK,
            HMSAction.REMOVE_CLOSE_BTN,
            HMSAction.LOAD_VIRTUAL_TRAY,
            HMSAction.CANCLE,
            HMSAction.DBL_CHECK_CANCEL,
        ],
    )
    def test_ui_only_actions_publish_nothing(self, client, action):
        # These actions exist for parity with BambuStudio's modal but have no
        # MQTT counterpart — the printer's own screen drives them.
        assert client.execute_hms_action("03008070", action) is True
        assert self._published_commands(client) == []

    def test_every_publish_is_followed_by_pushall(self, client):
        # The dispatcher pairs every command with a `pushing.pushall` echo so
        # the state stream refreshes on the next tick. Regression guard.
        client.execute_hms_action("03008070", HMSAction.RESUME_PRINTING)
        payloads = [json.loads(c.args[1]) for c in client._client.publish.call_args_list]
        assert any("pushing" in p for p in payloads)
