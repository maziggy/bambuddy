"""Every FTP session the client opens says how it closed (#3009).

The reporter of #3009 read a print-completion trace that showed two FTP
connects, one DELE and then nothing, and concluded the connections were never
closed -- the SD-card corruption they were chasing being the consequence.

They were closed. ``disconnect()`` and ``_abandon_connection()`` simply logged
nothing at any level, so a clean close and a genuinely leaked socket produced
the same log: silence. These tests pin the close line down, because a
diagnostic that only exists until someone tidies it away is worth nothing to
the next person reading a support bundle.
"""

import logging

import pytest

from backend.app.services.bambu_ftp import BambuFTPClient
from backend.tests.unit.services.mock_ftp_server import MockBambuFTPServer

from .conftest import _find_free_port


def _close_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "FTP session to" in r.getMessage()]


class TestACleanSessionSaysSo:
    """The ordinary path: connect, work, QUIT."""

    def test_a_clean_close_is_logged_once(self, ftp_client_factory, caplog):
        client = ftp_client_factory()
        assert client.connect() is True
        with caplog.at_level(logging.DEBUG, logger="backend.app.services.bambu_ftp"):
            client.disconnect()

        lines = _close_lines(caplog)
        assert len(lines) == 1, lines
        assert "closed after QUIT" in lines[0]
        assert "127.0.0.1" in lines[0]

    def test_the_line_carries_how_long_the_session_was_held(self, ftp_client_factory, caplog):
        """Without a duration the line cannot distinguish a short delete from a
        session that sat open for the length of a print -- which is the exact
        question #3009 asked."""
        client = ftp_client_factory()
        client.connect()
        with caplog.at_level(logging.DEBUG, logger="backend.app.services.bambu_ftp"):
            client.disconnect()

        assert "held 0." in _close_lines(caplog)[0]

    def test_a_delete_through_the_async_wrapper_closes_and_says_so(self, ftp_server, ftp_root, caplog):
        """The path #3009 actually traced: the post-print SD-card cleanup in
        ``on_print_complete`` calls ``delete_file_async`` once per candidate."""
        import asyncio

        from backend.app.services.bambu_ftp import DeleteResult, delete_file_async

        (ftp_root / "cube.gcode").write_bytes(b"G28\n")
        original_port = BambuFTPClient.FTP_PORT
        BambuFTPClient.FTP_PORT = ftp_server.port
        try:
            with caplog.at_level(logging.DEBUG, logger="backend.app.services.bambu_ftp"):
                result = asyncio.run(delete_file_async("127.0.0.1", "12345678", "/cube.gcode", printer_model="X1C"))
        finally:
            BambuFTPClient.FTP_PORT = original_port

        assert result == DeleteResult.DELETED
        assert len(_close_lines(caplog)) == 1

    def test_the_550_path_closes_too(self, ftp_server, caplog):
        """The line #3009's log ends on. A candidate the printer does not have
        answers 550, and that session has to close like any other."""
        import asyncio

        from backend.app.services.bambu_ftp import DeleteResult, delete_file_async

        original_port = BambuFTPClient.FTP_PORT
        BambuFTPClient.FTP_PORT = ftp_server.port
        try:
            with caplog.at_level(logging.DEBUG, logger="backend.app.services.bambu_ftp"):
                result = asyncio.run(delete_file_async("127.0.0.1", "12345678", "/not_here.3mf", printer_model="X1C"))
        finally:
            BambuFTPClient.FTP_PORT = original_port

        assert result == DeleteResult.NOT_FOUND
        lines = _close_lines(caplog)
        assert len(lines) == 1, lines
        assert "closed after QUIT" in lines[0]

    def test_disconnect_without_a_session_says_nothing(self, ftp_client_factory, caplog):
        """No socket was opened, so there is no session to account for. A line
        here would be worse than none: it would pair with no connect."""
        client = ftp_client_factory()
        with caplog.at_level(logging.DEBUG, logger="backend.app.services.bambu_ftp"):
            client.disconnect()

        assert _close_lines(caplog) == []


class TestAFailedConnectIsAccountedForToo:
    """A connect that opens a socket and then fails still closed something."""

    def test_a_rejected_login_reports_the_close(self, ftp_client_factory, caplog):
        with caplog.at_level(logging.DEBUG, logger="backend.app.services.bambu_ftp"):
            assert ftp_client_factory(access_code="wrongcode").connect() is False

        lines = _close_lines(caplog)
        assert len(lines) == 1, lines
        assert "closed without QUIT" in lines[0]
        assert "login rejected" in lines[0]

    def test_an_unreachable_printer_reports_the_close(self, ftp_server, caplog):
        client = BambuFTPClient("192.0.2.1", "12345678", timeout=1.0, printer_model="X1C")
        client.FTP_PORT = ftp_server.port
        with caplog.at_level(logging.DEBUG, logger="backend.app.services.bambu_ftp"):
            assert client.connect() is False

        lines = _close_lines(caplog)
        assert len(lines) == 1, lines
        assert "closed without QUIT" in lines[0]
        # No socket was ever established, so there is no duration to claim.
        assert "held unknown" in lines[0]


class TestTheSessionIsNotDoubleCounted:
    """Isolated class: ``server.stop()`` calls ``close_all()``, which nukes every
    asyncore socket in the process."""

    def test_a_failing_quit_reports_one_close_not_two(self, ftp_certs, tmp_path, caplog):
        """``disconnect()`` falls through to ``_abandon_connection()`` when QUIT
        cannot be sent. Both log, so the fallback must not produce a second line
        for one session."""
        cert_path, key_path = ftp_certs
        server = MockBambuFTPServer("127.0.0.1", _find_free_port(), str(tmp_path), cert_path, key_path)
        server.start()

        client = BambuFTPClient("127.0.0.1", "12345678", timeout=5.0)
        client.FTP_PORT = server.port
        assert client.connect() is True

        server.stop()
        with caplog.at_level(logging.DEBUG, logger="backend.app.services.bambu_ftp"):
            client.disconnect()

        lines = _close_lines(caplog)
        assert len(lines) == 1, lines
        assert "closed without QUIT" in lines[0]
        assert "QUIT failed" in lines[0]
        assert client._ftp is None


class TestTheCoolOffSkipStaysSilent:
    """No connect was attempted, so there is nothing to close."""

    def test_a_skipped_connect_logs_no_close(self, ftp_client_factory, caplog):
        import time

        BambuFTPClient._handshake_blocked_until["127.0.0.1"] = time.monotonic() + 300
        client = ftp_client_factory()
        with caplog.at_level(logging.DEBUG, logger="backend.app.services.bambu_ftp"):
            assert client.connect() is False

        assert _close_lines(caplog) == []
