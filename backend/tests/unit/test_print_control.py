"""One way to stop a print on a user's behalf.

The printer card, the queue and the two webhook routes each used to send the
stop and set the "stopped by the user" mark themselves, and one of them forgot
the mark: the print then ended as failed, with a "print failed" notification
and, on some printers, a mislabelled error such as a layer shift. They now all
call print_control.stop_print_by_user, and only it (and the automatic kill
switch, which is not a user stop) may send a stop command.
"""

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.services.print_control import stop_print_by_user

BACKEND_APP = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def marks():
    from backend.app.main import _user_stopped_printers

    _user_stopped_printers.clear()
    yield _user_stopped_printers
    _user_stopped_printers.clear()


def test_a_sent_stop_marks_the_printer(marks):
    with patch("backend.app.services.print_control.printer_manager.stop_print", return_value=True) as send:
        assert stop_print_by_user(4) is True
    send.assert_called_once_with(4)
    assert 4 in marks


def test_an_unsent_stop_leaves_no_mark(marks):
    with patch("backend.app.services.print_control.printer_manager.stop_print", return_value=False):
        assert stop_print_by_user(4) is False
    assert 4 not in marks


def test_the_queue_can_mark_an_unsent_stop(marks):
    """An offline printer that later reports the job as failed must still read
    it as cancelled by the user (the queue closes the job at once)."""
    with patch("backend.app.services.print_control.printer_manager.stop_print", return_value=False):
        assert stop_print_by_user(4, mark_when_unsent=True) is False
    assert 4 in marks


def test_a_failing_send_raises_and_marks_only_when_asked(marks):
    with patch("backend.app.services.print_control.printer_manager.stop_print", side_effect=OSError("mqtt down")):
        with pytest.raises(OSError):
            stop_print_by_user(4)
        assert 4 not in marks
        with pytest.raises(OSError):
            stop_print_by_user(4, mark_when_unsent=True)
    assert 4 in marks


# Files allowed to send a stop command themselves: the shared function, the
# printer manager and MQTT client that implement it, and main.py's kill switch,
# which stops unauthorised prints automatically (not a user stop, no mark).
ALLOWED = {
    "services/print_control.py",
    "services/printer_manager.py",
    "services/bambu_mqtt.py",
    "main.py",
}


def test_no_other_code_sends_a_stop():
    """Catches a new ``.stop_print(`` call. It cannot see a stop sent as a raw
    MQTT payload (the HMS dialog's "Stop Printing" in bambu_mqtt.py, whose
    route marks the stop itself); such a path must call mark_stopped_by_user."""
    offenders = []
    for path in sorted(BACKEND_APP.rglob("*.py")):
        rel = path.relative_to(BACKEND_APP).as_posix()
        if rel in ALLOWED:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "stop_print":
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "Stop a print through services.print_control.stop_print_by_user, which also sets the "
        f"stopped-by-user mark: {offenders}"
    )
