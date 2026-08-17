"""A connection that never came up still has to be closed (#2780).

Every failure path in ``BambuFTPClient.connect`` used to clear ``self._ftp``
and stop there, leaving a connected socket for the garbage collector to
notice. Once, that is untidy. At the volume this code runs at it is not: a
single print used to walk ~110 candidate paths, so a printer refusing FTPS got
~110 sockets opened and dropped in a couple of minutes, and one support bundle
recorded 1813 in a day.

That matters beyond tidiness, because the leading explanation for the refusal
is the printer running out of connection slots -- a single manual connect to
the same printer completes a clean handshake while Bambuddy is failing, and
vsFTPd answers a per-source limit in cleartext, which is exactly the
``WRONG_VERSION_NUMBER`` we see. If that is right, abandoning sockets is not a
side effect of the problem, it is part of what sustains it.

These tests assert the socket is closed, not merely dereferenced, because
dereferencing is what the old code did and it looked identical from outside.
"""

import ftplib  # nosec B402 — tests need the real ftplib to construct its own error types
import ssl
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.bambu_ftp import BambuFTPClient

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_cooloff():
    """The SSL path opens a per-IP cool-off that outlives the test."""
    BambuFTPClient._handshake_blocked_until.clear()
    yield
    BambuFTPClient._handshake_blocked_until.clear()


def _client(ip="192.168.1.210"):
    return BambuFTPClient(ip, "12345678", printer_model="P2S")


@pytest.mark.parametrize(
    "error",
    [
        # The two the #2780 bundle actually recorded, 1813 and 49 times.
        ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number"),
        TimeoutError("_ssl.c:1015: The handshake operation timed out"),
        ftplib.error_perm("530 Login incorrect."),
        ftplib.error_temp("421 Service not available."),
        OSError("Connection reset by peer"),
    ],
    ids=["ssl", "timeout", "perm", "temp", "oserror"],
)
def test_a_failed_connect_closes_its_socket(error):
    fake_ftp = MagicMock()
    fake_ftp.connect.side_effect = error

    with patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=fake_ftp):
        client = _client()
        assert client.connect() is False

    fake_ftp.close.assert_called_once()
    # Never QUIT: that is a command, and there is no working control channel
    # to send it on.
    fake_ftp.quit.assert_not_called()
    assert client._ftp is None


def test_a_failure_after_connect_still_closes():
    """Login and prot_p run on a live socket, so a failure there leaks a
    genuinely established connection -- the worst case for a session limit."""
    fake_ftp = MagicMock()
    fake_ftp.login.side_effect = ftplib.error_perm("530 Login incorrect.")

    with patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=fake_ftp):
        client = _client()
        assert client.connect() is False

    fake_ftp.close.assert_called_once()


def test_close_raising_does_not_propagate():
    """Cleanup is best-effort; the socket may already be gone. A raise here
    would turn a handled connect failure into an unhandled one."""
    fake_ftp = MagicMock()
    fake_ftp.connect.side_effect = OSError("boom")
    fake_ftp.close.side_effect = OSError("already closed")

    with patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=fake_ftp):
        assert _client().connect() is False


class TestDisconnect:
    def test_a_healthy_disconnect_quits(self):
        fake_ftp = MagicMock()

        with patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=fake_ftp):
            client = _client()
            assert client.connect() is True
        client.disconnect()

        fake_ftp.quit.assert_called_once()
        assert client._ftp is None

    def test_a_failing_quit_falls_back_to_closing(self):
        """``ftplib.FTP.quit`` sends QUIT and only then closes, so when the
        send raises it never reaches its own close and the socket stays open.
        The old handler swallowed that exception and left it there.
        """
        fake_ftp = MagicMock()
        fake_ftp.quit.side_effect = OSError("Broken pipe")

        with patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=fake_ftp):
            client = _client()
            assert client.connect() is True
        client.disconnect()

        fake_ftp.close.assert_called_once()
        assert client._ftp is None


def test_the_ssl_path_still_opens_the_cooloff():
    """The cleanup change must not disturb the gate that stops the storm."""
    fake_ftp = MagicMock()
    fake_ftp.connect.side_effect = ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number")

    with patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=fake_ftp):
        client = _client("192.168.1.211")
        assert client.connect() is False

    assert BambuFTPClient.handshake_blocked("192.168.1.211") is True
