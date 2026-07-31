"""The layer total must survive the print-start reset (#2702).

`_update_state` applies `total_layer_num` early and, further down, resets
`total_layers` when it detects a new print (added by #1771 so the previous
print's total can't bleed into the next one's usage split). Those two run in
the same function on the same frame, so a frame that carried both the new
print's total *and* the transition into RUNNING had its total applied and then
zeroed.

That is unrecoverable rather than merely late: Bambu firmware sends only
changed fields, so the printer never re-sends a total it already published.
The value reappears only in a full pushall — i.e. on reconnect or a manual
Force Refresh — which is why the reporter saw `n/0` for nine minutes on a
flawless connection, why it looked random, and why a *stable* link made it
worse.

Frames here are trimmed to the fields the code under test reads. No printer is
needed: the fix is a property of how one function orders its own writes.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def client():
    """A client with a recording stand-in for the MQTT connection."""
    from unittest.mock import MagicMock

    from backend.app.services.bambu_mqtt import BambuMQTTClient

    c = BambuMQTTClient(
        ip_address="192.168.1.100",
        serial_number="TEST123",
        access_code="12345678",
    )
    c._client = MagicMock()
    # A new print is only detected once a previous state has been observed
    # (#1304 guard), so give every test a plausible pre-print history.
    c._previous_gcode_state = "IDLE"
    c._previous_gcode_file = None
    c._was_running = False
    return c


def pushalls(client) -> list[dict]:
    """Every pushall published on this client, decoded."""
    sent = []
    for call in client._client.publish.call_args_list:
        payload = json.loads(call.args[1])
        if payload.get("pushing", {}).get("command") == "pushall":
            sent.append(payload)
    return sent


def running_frame(**extra) -> dict:
    """A frame that flips the printer into RUNNING with a file — a new print."""
    return {"gcode_state": "RUNNING", "gcode_file": "widget.3mf", "subtask_name": "widget", **extra}


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------


def test_total_arriving_with_the_start_frame_survives(client):
    """The reported bug: total and transition in one frame lost the total."""
    client._update_state(running_frame(total_layer_num=33, layer_num=0))

    assert client.state.total_layers == 33


def test_total_arriving_with_the_start_frame_needs_no_pushall(client):
    """We already have the denominator, so don't spend a round-trip on it."""
    client._update_state(running_frame(total_layer_num=33))

    assert pushalls(client) == []
    assert client._total_layers_refresh_armed is False


def test_previous_prints_total_still_cannot_bleed_through(client):
    """#1771's reason for the reset — preserved exactly.

    A start frame with no total of its own must land on 0, never on the
    finished print's denominator.
    """
    client.state.total_layers = 120  # left over from the print that just ended

    client._update_state(running_frame())

    assert client.state.total_layers == 0


def test_start_frame_without_a_total_asks_the_printer_for_one(client):
    """Covers the ordering where the total was published a frame or two early.

    Re-applying this frame's own value can't help there — the value was
    already consumed and zeroed — so recovery has to come from a pushall,
    the only message that re-sends unchanged fields.
    """
    client._update_state(running_frame())

    assert len(pushalls(client)) == 1
    assert client._total_layers_refresh_armed is True


# ---------------------------------------------------------------------------
# The one-shot re-request
# ---------------------------------------------------------------------------


def test_first_layer_advance_without_a_total_re_requests_once(client):
    client._update_state(running_frame())
    assert len(pushalls(client)) == 1  # from print start

    client._update_state({"layer_num": 1})

    assert len(pushalls(client)) == 2
    assert client._total_layers_refresh_armed is False


def test_later_layer_advances_do_not_keep_re_requesting(client):
    """An unanswered pushall must not become a per-layer retry loop."""
    client._update_state(running_frame())
    client._update_state({"layer_num": 1})
    before = len(pushalls(client))

    for layer in range(2, 12):
        client._update_state({"layer_num": layer})

    assert len(pushalls(client)) == before


def test_no_re_request_once_the_total_is_known(client):
    """The pushall answered: layers advance without further traffic."""
    client._update_state(running_frame())
    client._update_state({"total_layer_num": 33})  # the pushall's answer
    before = len(pushalls(client))

    client._update_state({"layer_num": 1})
    client._update_state({"layer_num": 2})

    assert client.state.total_layers == 33
    assert len(pushalls(client)) == before


def test_the_recovered_total_is_what_downstream_reads(client):
    """End-to-end on the reporter's sequence, minus the 9-minute wait.

    Start with no total, layers advance at `n/0`, the pushall answers, and
    from then on the UI, `{total_layers}` notifications and the usage-split
    denominator all see 33 — they read this one field.
    """
    client._update_state(running_frame())
    client._update_state({"layer_num": 1})
    assert client.state.total_layers == 0  # the symptom in the screenshot

    client._update_state({"layer_num": 2, "total_layer_num": 33})

    assert (client.state.layer_num, client.state.total_layers) == (2, 33)


def test_the_pushall_answer_does_not_re_trigger_the_reset(client):
    """Loop safety: the answer is a *full* frame, gcode_state and file included.

    If that re-tripped the new-print detection it would reset the total it just
    delivered and request another pushall, once per round-trip, forever.
    """
    client._update_state(running_frame())
    assert len(pushalls(client)) == 1

    client._update_state(running_frame(total_layer_num=33, layer_num=1, mc_percent=3))

    assert client.state.total_layers == 33
    assert len(pushalls(client)) == 1


# ---------------------------------------------------------------------------
# Interaction with the pre-existing firmware-reset guard
# ---------------------------------------------------------------------------


def test_firmware_reset_to_zero_mid_print_is_still_ignored(client):
    """P1S zeroes total_layer_num at print end; #1771's guard keeps the total."""
    client._update_state(running_frame(total_layer_num=33))

    client._update_state({"layer_num": 33, "total_layer_num": 0})

    assert client.state.total_layers == 33


@pytest.mark.parametrize("value", [None, "", 0, "0", -1, "abc", "33.7", [], {}, 3.9])
def test_unusable_totals_do_not_break_ingest(client, value):
    """A bad total must not escape `_update_state`.

    The old parse did a bare ``int(data["total_layer_num"])``. `_on_message`
    catches only `JSONDecodeError` and paho is left at
    ``suppress_exceptions = False``, so anything this raised was re-raised on
    the network thread and took the printer connection down over one field.
    `None`, `[]` and `{}` all did exactly that.
    """
    client._update_state(running_frame(total_layer_num=value))

    assert client.state.total_layers in (0, 3)  # 3.9 truncates; the rest are 0
    assert client.state.gcode_file == "widget.3mf"  # the rest of the frame landed


def test_an_unusable_total_does_not_stop_the_layer_counter(client):
    """The read happens before the layer block, so it must not be able to raise.

    Otherwise a firmware sending a malformed total would freeze `layer_num` for
    the whole print — the frame would abort before reaching it.
    """
    client._update_state(running_frame())

    client._update_state({"layer_num": 7, "total_layer_num": "not-a-number"})

    assert client.state.layer_num == 7


def test_a_string_total_is_accepted(client):
    """Bambu ships numbers as strings in plenty of other fields."""
    client._update_state(running_frame(total_layer_num="33"))

    assert client.state.total_layers == 33


def test_a_zero_total_on_the_start_frame_counts_as_no_total(client):
    """`total_layer_num: 0` is the firmware's "don't know yet", not a value."""
    client.state.total_layers = 120

    client._update_state(running_frame(total_layer_num=0))

    assert client.state.total_layers == 0
    assert len(pushalls(client)) == 1


# ---------------------------------------------------------------------------
# A restarted print (file change while RUNNING) takes the same path
# ---------------------------------------------------------------------------


def test_file_change_while_running_also_keeps_its_own_total(client):
    """`is_file_change` shares the reset, so it needs the same treatment."""
    client._update_state(running_frame(total_layer_num=33))
    client._was_running = True

    client._update_state(
        {"gcode_state": "RUNNING", "gcode_file": "other.3mf", "subtask_name": "other", "total_layer_num": 77}
    )

    assert client.state.total_layers == 77
