"""Ask the printer what it actually said (#2780).

``[SSL: WRONG_VERSION_NUMBER]`` on port 990 means the printer's first bytes
were not a TLS record. That is measured rather than assumed, and the two tests
at the top of this file are the measurement: a cleartext banner reproduces the
exact error the field reports, while a genuine TLS version mismatch produces a
different one. Both matter, because the profile registry used to explain this
failure as a TLS 1.3 problem and prescribe a version cap for it -- which cannot
work, since the error was never about the negotiated version.

What the error does not say is *which* cleartext message, and that is the part
that would identify the fault. OpenSSL has consumed those bytes by the time the
exception surfaces, so the client now opens one plain connection and reads them.
The reporter with the affected farm offered a packet capture; this gets the same
answer from every affected install instead of one.
"""

import logging
import socket
import ssl
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services import bambu_ftp
from backend.app.services.bambu_ftp import BambuFTPClient

pytestmark = pytest.mark.unit

LOGGER = "backend.app.services.bambu_ftp"
REFUSAL = b"421 Too many connections. Try again later.\r\n"


@pytest.fixture(autouse=True)
def _clean_state():
    BambuFTPClient._handshake_blocked_until.clear()
    BambuFTPClient._handshake_skip_logged.clear()
    BambuFTPClient._mode_cache.clear()
    yield
    BambuFTPClient._handshake_blocked_until.clear()
    BambuFTPClient._handshake_skip_logged.clear()
    BambuFTPClient._mode_cache.clear()


class _Listener:
    """A socket on an ephemeral port that answers however the test says.

    ``mode="cleartext"`` sends an FTP refusal in the clear, the way a vsFTPd
    that is turning connections away does. ``mode="silent"`` accepts and says
    nothing, which is what a healthy implicit-FTPS service does while it waits
    for a ClientHello.
    """

    def __init__(self, mode: str):
        self.mode = mode
        self.accepts = 0
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._conns: list[socket.socket] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        # A blocking accept() is not reliably woken by closing the socket from
        # another thread, which left every teardown here waiting out its join.
        self._sock.settimeout(0.1)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.accepts += 1
            if self.mode == "cleartext":
                try:
                    conn.sendall(REFUSAL)
                except OSError:
                    pass
                conn.close()
            else:
                # Hold it open and stay quiet, so the probe has to time out.
                self._conns.append(conn)

    def stop(self):
        self._stop.set()
        self._sock.close()
        for c in self._conns:
            try:
                c.close()
            except OSError:
                pass
        self._thread.join(timeout=2)


@pytest.fixture()
def cleartext_printer():
    server = _Listener("cleartext")
    yield server
    server.stop()


@pytest.fixture()
def silent_printer():
    server = _Listener("silent")
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _fast_probe(monkeypatch):
    """A real timeout would make the silent case a two-second test."""
    monkeypatch.setattr(bambu_ftp, "_CLEARTEXT_PROBE_TIMEOUT", 0.25)


# ---------------------------------------------------------------------------
# The measurement the rest of this rests on
# ---------------------------------------------------------------------------
def test_a_cleartext_banner_is_what_produces_wrong_version_number(cleartext_printer):
    """The exact error the affected farm logs, from a non-TLS answer."""
    ctx = ssl.create_default_context()
    # `create_default_context()` leaves `minimum_version` at MINIMUM_SUPPORTED,
    # which is the build's floor rather than a guarantee -- the same reason
    # every context in `backend/app` pins it, and the reason the TLS-13 case
    # further down this file already does. The listener answers with a plain
    # FTP banner and speaks no TLS at all, so the floor cannot change what this
    # measures; it only stops the file asking for a protocol we would refuse.
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection(("127.0.0.1", cleartext_printer.port), 5)

    with pytest.raises(ssl.SSLError) as caught:
        ctx.wrap_socket(raw, server_hostname="printer").do_handshake()

    assert caught.value.reason == "WRONG_VERSION_NUMBER"


def test_a_version_mismatch_produces_a_different_error(tmp_path):
    """So "cap the TLS version" cannot be the fix for WRONG_VERSION_NUMBER.

    Two of the cap_tls_v1_2 profile entries were written on the belief that it
    was. A real mismatch reports itself as a protocol-version alert, and a
    server that only speaks 1.2 negotiates fine against our own context without
    any cap -- so neither half of that reasoning holds.
    """
    import subprocess  # nosec B404 -- generating a throwaway cert for a local server

    subprocess.run(  # nosec B603 B607
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(tmp_path / "k.pem"),
            "-out",
            str(tmp_path / "c.pem"),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=printer",
        ],
        check=True,
        capture_output=True,
    )
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(tmp_path / "c.pem"), str(tmp_path / "k.pem"))
    server_ctx.maximum_version = ssl.TLSVersion.TLSv1_2

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    port = listener.getsockname()[1]

    def serve():
        for _ in range(2):
            try:
                conn, _addr = listener.accept()
            except OSError:
                return
            try:
                server_ctx.wrap_socket(conn, server_side=True).close()
            except (ssl.SSLError, OSError):
                try:
                    conn.close()
                except OSError:
                    pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:

        def attempt(*, force_tls13: bool):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.minimum_version = ssl.TLSVersion.TLSv1_3 if force_tls13 else ssl.TLSVersion.TLSv1_2
            if force_tls13:
                ctx.maximum_version = ssl.TLSVersion.TLSv1_3
            raw = socket.create_connection(("127.0.0.1", port), 5)
            try:
                ctx.wrap_socket(raw, server_hostname="printer").do_handshake()
                return None
            finally:
                try:
                    raw.close()
                except OSError:
                    pass

        with pytest.raises(ssl.SSLError) as caught:
            attempt(force_tls13=True)
        assert caught.value.reason != "WRONG_VERSION_NUMBER"
        assert "PROTOCOL_VERSION" in caught.value.reason

        # And the half that makes the caps no-ops: a 1.2-only peer needs no help.
        assert attempt(force_tls13=False) is None
    finally:
        listener.close()
        thread.join(timeout=2)


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------
class TestTheProbe:
    def _client(self, server):
        client = BambuFTPClient("127.0.0.1", "12345678", timeout=5.0, printer_model="P2S")
        client.FTP_PORT = server.port
        return client

    def test_it_puts_the_printers_own_words_in_the_log(self, cleartext_printer, caplog):
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert self._client(cleartext_printer).connect() is False

        messages = [r.getMessage() for r in caplog.records]
        assert any("421 Too many connections" in m for m in messages), messages
        # And it has to be findable by someone filing a report.
        assert any("include this line if you report it" in m for m in messages), messages

    def test_the_reason_carries_it_too(self, cleartext_printer):
        """So the failure reaches the user's message, not only the log."""
        client = self._client(cleartext_printer)
        client.connect()

        assert client.last_failure is not None
        assert "421 Too many connections" in client.last_failure.detail

    def test_silence_is_reported_as_the_fault_having_passed(self, caplog):
        """The printer sent non-TLS bytes, then had recovered a moment later.

        Driven through a stubbed probe rather than a silent server, because a
        server that accepts and stays quiet never reaches this branch at all --
        it produces a handshake *timeout*, not WRONG_VERSION_NUMBER, and the
        TimeoutError branch handles that one. Reading nothing here means the
        refusal passed between the handshake and the question, which is worth
        saying rather than logging nothing at all.
        """
        transport = MagicMock()
        error = ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number")
        error.reason = "WRONG_VERSION_NUMBER"
        transport.connect.side_effect = error

        with (
            patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=transport),
            patch("backend.app.services.bambu_ftp._read_cleartext_reply", return_value=None),
            caplog.at_level(logging.WARNING, logger=LOGGER),
        ):
            assert BambuFTPClient("192.0.2.10", "12345678").connect() is False

        assert any("nothing readable in cleartext" in r.getMessage() for r in caplog.records)
        # And a probe that finds nothing must not cost the cool-off: the
        # handshake still failed, whatever the printer said a moment later.
        assert BambuFTPClient.handshake_blocked("192.0.2.10") is True

    def test_an_accept_and_stay_quiet_printer_is_a_timeout_not_this(self, silent_printer):
        """The other half of #2780's theory, and it lands somewhere else.

        A vsFTPd answering its global connection limit by accepting and never
        speaking produces a handshake timeout. Probing that would read nothing
        by definition, so this branch is deliberately not reached.
        """
        client = self._client(silent_printer)
        client.timeout = 0.5

        with patch("backend.app.services.bambu_ftp._read_cleartext_reply") as probe:
            assert client.connect() is False

        probe.assert_not_called()
        assert client.last_failure is not None
        assert client.last_failure.kind.value == "timeout"

    def test_it_asks_once_per_cooloff_not_once_per_attempt(self, cleartext_printer):
        """A dispatch ignores the cool-off, so it reaches this branch four times.

        Probing each time would add a connection per attempt to a printer whose
        suspected fault is having too many -- the opposite of what #2780's
        socket-leak fix was for.
        """
        for _ in range(4):
            client = self._client(cleartext_printer)
            client.respect_handshake_cooloff = False
            client.connect()

        # Four handshakes, and exactly one probe on top of them.
        assert cleartext_printer.accepts == 5

    def test_a_fresh_cooloff_window_asks_again(self, cleartext_printer):
        """A printer that recovers and fails later is a new event to diagnose."""
        self._client(cleartext_printer).connect()
        before = cleartext_printer.accepts
        BambuFTPClient._handshake_blocked_until.clear()

        self._client(cleartext_printer).connect()
        assert cleartext_printer.accepts == before + 2  # handshake + probe

    def test_a_real_version_mismatch_is_not_probed(self):
        """Nothing to read: that peer spoke TLS, it just would not agree on one.

        Without this check the probe would connect and sit out its whole
        timeout on every such failure.
        """
        transport = MagicMock()
        error = ssl.SSLError(1, "[SSL: TLSV1_ALERT_PROTOCOL_VERSION] tlsv1 alert protocol version")
        error.reason = "TLSV1_ALERT_PROTOCOL_VERSION"
        transport.connect.side_effect = error

        with (
            patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=transport),
            patch("backend.app.services.bambu_ftp._read_cleartext_reply") as probe,
        ):
            assert BambuFTPClient("192.0.2.10", "12345678").connect() is False

        probe.assert_not_called()

    def test_a_refused_probe_reads_as_nothing_rather_than_raising(self):
        """Nothing is listening, so it must come back None, not blow up.

        Uses a port that was just released rather than patching
        ``socket.create_connection``, which is process-wide and would sit under
        anything else running in this worker.
        """
        released = socket.socket()
        released.bind(("127.0.0.1", 0))
        port = released.getsockname()[1]
        released.close()

        assert bambu_ftp._read_cleartext_reply("127.0.0.1", port) is None

    def test_the_dead_socket_is_closed_before_the_printer_is_asked_again(self):
        """Ordering, and it is the whole reason this is safe to do at all.

        The probe opens a second connection to a printer whose suspected fault
        is having no connection slots left. Holding the failed handshake open
        across that would be the leak #2780's cleanup was added to stop, with
        an extra connection layered on top.
        """
        transport = MagicMock()
        error = ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number")
        error.reason = "WRONG_VERSION_NUMBER"
        transport.connect.side_effect = error
        client = BambuFTPClient("192.0.2.10", "12345678")
        observed = {}

        def _probe(*_args):
            observed["still_open"] = client._ftp is not None
            observed["closed"] = transport.close.called
            return "421 Too many connections."

        with (
            patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=transport),
            patch("backend.app.services.bambu_ftp._read_cleartext_reply", _probe),
        ):
            assert client.connect() is False

        assert observed == {"still_open": False, "closed": True}

    def test_the_probe_does_not_outlive_its_timeout(self, silent_printer):
        started = time.monotonic()
        assert bambu_ftp._read_cleartext_reply("127.0.0.1", silent_printer.port) is None
        assert time.monotonic() - started < 2.0
