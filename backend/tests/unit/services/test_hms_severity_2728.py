"""HMS severity is Bambu's alert level, not the part byte (#2728).

Every payload here is real: the reporter's P2S, the H2C from #1840 and the H2S
cancel echo from #2728's thread, exactly as the report topic sent them.
"""

from types import SimpleNamespace

import pytest

from backend.app import main as main_module
from backend.app.main import _hms_errors_to_notify, _hms_fault_counts, _hms_notify_key, _take_new_hms_faults
from backend.app.services.bambu_mqtt import BambuMQTTClient


@pytest.fixture
def client():
    return BambuMQTTClient(ip_address="192.168.1.100", serial_number="TESTSEV", access_code="12345678")


def _parse(client, hms: list[dict]):
    client._update_state({"hms": hms})
    return client.state.hms_errors


@pytest.mark.parametrize(
    ("attr", "code", "level", "part_byte"),
    [
        # P2S (#2728): 0500-0300-0002-000E, part byte 03 -> was shown as 3.
        (83886848, 131086, 2, 3),
        # P2S (#2728): 0500-0600-0002-0070, part byte 06 -> fell through to "Info".
        (83887616, 131184, 2, 6),
        # P2S (#2728): 0500-0200-0003-000A, part byte 02 -> was shown as "Serious".
        (83886592, 196618, 3, 2),
        # H2C (#1840): 0500-0600-0002-0005 paused the printer and read as "Info".
        (0x05000600, 0x00020005, 2, 6),
    ],
)
def test_severity_is_the_alert_level_in_the_code(client, attr, code, level, part_byte):
    """The level is the high half of `code`, as Bambu Studio decodes it; the
    old expression read attr bits 8-15, the part byte, and got every one of
    these wrong."""
    (error,) = _parse(client, [{"attr": attr, "code": code}])
    assert (attr >> 8) & 0xFF == part_byte  # what used to be reported
    assert error.severity == level


def test_a_task_stopping_fault_is_level_one(client):
    """0300-0200-0001-0008, nozzle temperature abnormal, a level-1 code in
    Bambu's catalogue."""
    (error,) = _parse(client, [{"attr": 0x03000200, "code": 0x00010008}])
    assert error.severity == 1
    assert error.description


def test_level_zero_is_not_promoted(client):
    """Bambu defines 0 as an invalid level. It used to be bumped to 2 and so
    rendered as a fault; it now stays 0 and reads as unknown."""
    (error,) = _parse(client, [{"attr": 0x05000000, "code": 0x00004038}])
    assert error.severity == 0


def test_the_h2s_cancel_echo_is_kept_but_has_no_text(client):
    """0C00-0100-0002-001B arrives after a user cancel. Bambu lists it with no
    text, so the UI does not count it; it cannot be filtered by its short code
    because 0C00-0300-0003-001B, a real spaghetti notice, shares that."""
    (error,) = _parse(client, [{"attr": 201326848, "code": 131099}])
    assert error.full_code == "0C0001000002001B"
    assert error.severity == 2
    assert error.description is None


@pytest.mark.parametrize(
    ("print_error", "level"),
    [(0x03004000, 1), (0x03008004, 2), (0x0C00C003, 3)],
)
def test_print_error_takes_its_level_from_the_error_number(client, print_error, level):
    """print_error has no level field, and every entry used to get a flat 3."""
    client._update_state({"print_error": print_error})
    (error,) = client.state.hms_errors
    assert error.severity == level


def test_print_error_text_follows_the_printer_model():
    """The serial prefix picks the model's own text: 0300_8001 means opposite
    things on a 20P and a 093."""
    texts = {}
    for prefix in ("20P", "093"):
        c = BambuMQTTClient(ip_address="192.168.1.100", serial_number=f"{prefix}XXXX", access_code="12345678")
        c._update_state({"print_error": 0x03008001})
        texts[prefix] = c.state.hms_errors[0].description
    assert "paused by the user" in texts["20P"]
    assert "pause command" in texts["093"]


def _fault(full_code: str, severity: int = 2, description: str | None = "Described.", actions: list | None = None):
    return SimpleNamespace(
        full_code=full_code,
        severity=severity,
        attr=int(full_code[:8], 16),
        code=f"0x{int(full_code[8:], 16):x}",
        description=description,
        actions=actions or [],
    )


class TestNotificationFilter:
    """ojimpo's note on #2728: the notification path filtered `severity >= 2`.
    On the real level that would drop the task-stopping errors."""

    def test_errors_and_warnings_are_notified(self):
        errors = [_fault("0300020000010008", 1), _fault("0300030000020001", 2)]
        keys = {_hms_notify_key(e) for e in errors}
        assert _hms_errors_to_notify(errors, keys) == errors

    def test_level_zero_is_not(self):
        error = _fault("0500000000004038", 0)
        assert _hms_errors_to_notify([error], {_hms_notify_key(error)}) == []


class TestWhatCounts:
    """One rule for the printer card, notifications and the MQTT relay; the
    frontend's filterKnownHMSErrors mirrors it."""

    def test_an_hms_notice_without_actions_does_not_count(self):
        """0300-9700-0003-0001 "The top cover is open": a printer can hold it
        through a whole print, and it must not turn the card into a problem or
        send a notification each time it reappears."""
        assert not _hms_fault_counts(_fault("0300970000030001", 3, "The top cover is open."))

    def test_an_hms_notice_with_actions_counts(self):
        """It is waiting on the user, so its buttons have to be reachable."""
        assert _hms_fault_counts(_fault("0300970000030001", 3, None, ["OK_BUTTON"]))

    def test_a_print_error_prompt_still_counts(self):
        """0xCxxx print_error prompts counted before #2728 and still do."""
        prompt = SimpleNamespace(full_code="1880C003", severity=3, description="Unable to start drying.", actions=[])
        assert _hms_fault_counts(prompt)

    def test_a_fault_without_text_or_actions_does_not_count(self):
        """The H2S cancel echo, which the relay used to drop only by accident."""
        assert not _hms_fault_counts(_fault("0C0001000002001B", 2, None))

    def test_an_actionable_fault_without_text_counts(self):
        assert _hms_fault_counts(_fault("0500020000020070", 2, None, ["IGNORE_RESUME"]))

    def test_level_zero_never_counts(self):
        assert not _hms_fault_counts(_fault("0500000000004038", 0, "Text", ["OK_BUTTON"]))


class TestNotificationDeduplication:
    """Faults were keyed by `attr` alone, which for an `hms[]` fault is only the
    module and part, so two faults on one part shared a key."""

    PRINTER = 9001

    @pytest.fixture(autouse=True)
    def _clean(self):
        main_module._notified_hms_errors.pop(self.PRINTER, None)
        main_module._hms_last_seen.pop(self.PRINTER, None)
        yield
        main_module._notified_hms_errors.pop(self.PRINTER, None)
        main_module._hms_last_seen.pop(self.PRINTER, None)

    def test_two_faults_on_one_part_are_both_notified(self):
        """#1840's H2C held both of these at once; they share attr 05000600."""
        first, second = _fault("0500060000020005"), _fault("0500060000020006")
        assert first.attr == second.attr
        assert _take_new_hms_faults(self.PRINTER, [first, second]) == [first, second]

    def test_a_fault_replaced_on_the_same_part_is_notified(self):
        """The first clears and a different one on the same part appears in the
        same update: the old key did not change, so it looked already sent."""
        first, second = _fault("0500060000020005"), _fault("0500060000020006")
        assert _take_new_hms_faults(self.PRINTER, [first]) == [first]
        assert _take_new_hms_faults(self.PRINTER, [second]) == [second]

    def test_a_held_fault_is_notified_once(self):
        fault = _fault("0500060000020005")
        assert _take_new_hms_faults(self.PRINTER, [fault]) == [fault]
        assert _take_new_hms_faults(self.PRINTER, [fault]) == []
        assert _take_new_hms_faults(self.PRINTER, [fault, _fault("0300020000010008", 1)])[0].full_code == (
            "0300020000010008"
        )

    def test_a_print_error_keeps_its_own_key(self):
        """For a print_error the full code is the whole value, as attr was."""
        error = SimpleNamespace(full_code="03008004", attr=0x03008004, code="0x8004", severity=2)
        assert _hms_notify_key(error) == "03008004"

    def test_an_entry_without_a_full_code_still_tells_faults_apart(self):
        a = SimpleNamespace(full_code="", attr=0x05000600, code="0x20005", severity=2)
        b = SimpleNamespace(full_code="", attr=0x05000600, code="0x20006", severity=2)
        assert _hms_notify_key(a) != _hms_notify_key(b)
