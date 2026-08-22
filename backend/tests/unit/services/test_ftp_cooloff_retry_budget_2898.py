"""A dispatch must not spend its retries on a cool-off that outlives them (#2898).

``BambuFTPClient.connect`` refuses to open a socket for 300s after a TLS
handshake failure (#2780). That gate was written for the background sweeps --
the post-print 3MF, cover and timelapse fetches, which walk ~110 candidate
paths against one wedged printer and have nobody waiting on them.

It sat inside ``connect``, so it applied to print dispatch too, which wants the
opposite. On a 10-printer farm one handshake failure took out three queued
jobs: the pre-upload delete armed the cool-off, all four upload attempts were
then answered from the gate 2s apart without a socket being opened, and the
next two jobs for that printer failed the same way inside the same window.

The split these tests pin: work that is bounded and user-initiated (a dispatch
is one delete plus at most four upload attempts) opts out; everything else
keeps #2780's behaviour exactly. Sockets are counted rather than inferred,
because "returned False" looks identical either way -- which is what made the
original report a log dive.
"""

import logging
import ssl
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services import bambu_ftp
from backend.app.services.bambu_ftp import (
    BambuFTPClient,
    DeleteResult,
    delete_file_async,
    upload_file_async,
    with_ftp_retry,
)

pytestmark = pytest.mark.unit

IP = "192.168.50.142"  # the P2S from the report
LOGGER = "backend.app.services.bambu_ftp"


@pytest.fixture(autouse=True)
def _clean_cooloff():
    BambuFTPClient._handshake_blocked_until.clear()
    BambuFTPClient._handshake_skip_logged.clear()
    BambuFTPClient._mode_cache.clear()
    yield
    BambuFTPClient._handshake_blocked_until.clear()
    BambuFTPClient._handshake_skip_logged.clear()
    BambuFTPClient._mode_cache.clear()


@pytest.fixture()
def refusing_printer():
    """Answers port 990 with something that is not TLS, every time.

    Yields the transport mock; ``transport.connect.call_count`` is the number
    of times we actually went near the printer, which is the whole question
    here.
    """
    transport = MagicMock()
    transport.connect.side_effect = ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number")
    with patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=transport):
        yield transport


def _arm(ip=IP):
    """Put *ip* into the cool-off the way a real handshake failure would."""
    BambuFTPClient._handshake_blocked_until[ip] = bambu_ftp.time.monotonic() + bambu_ftp._HANDSHAKE_COOLOFF_SECONDS
    assert BambuFTPClient.handshake_blocked(ip) is True


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------
class TestConnectHonoursTheOptOut:
    def test_the_default_still_refuses_to_open_a_socket(self, refusing_printer):
        """#2780's protection is the default and must stay untouched."""
        _arm()
        assert BambuFTPClient(IP, "12345678", printer_model="P2S").connect() is False
        assert refusing_printer.connect.call_count == 0

    def test_an_exempt_client_reaches_the_printer(self, refusing_printer):
        _arm()
        client = BambuFTPClient(IP, "12345678", printer_model="P2S", respect_handshake_cooloff=False)
        assert client.connect() is False  # the printer is still broken...
        assert refusing_printer.connect.call_count == 1  # ...but we found that out ourselves

    def test_the_opt_out_does_not_leak_to_the_next_client(self, refusing_printer):
        """The flag is per client, not a global switch someone can leave on."""
        _arm()
        BambuFTPClient(IP, "12345678", respect_handshake_cooloff=False).connect()
        refusing_printer.connect.reset_mock()

        BambuFTPClient(IP, "12345678").connect()
        assert refusing_printer.connect.call_count == 0

    def test_the_skip_says_why_at_a_level_operators_see(self, caplog):
        """The reason-free WARNING is what made this a log dive.

        Every other ``connect`` failure path names its cause; this one logged
        at DEBUG, so at default level four identical "FTP connection failed"
        lines gave no hint that nothing had been sent.
        """
        _arm()
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert BambuFTPClient(IP, "12345678").connect() is False

        messages = [r.getMessage() for r in caplog.records]
        assert any("cooling off" in m and IP in m for m in messages), messages
        # And it has to be legible as "we did nothing", not as a network error.
        assert any("Nothing was sent to the printer" in m for m in messages), messages

    def test_it_says_it_once_per_cooloff_and_not_once_per_attempt(self, caplog):
        """Raising this to WARNING must not re-create the flood #2780 stopped.

        Not every caller is gated: downloading a ZIP of files the user picked
        walks the whole selection, so 200 files would otherwise repeat the same
        sentence 200 times.
        """
        _arm()
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            for _ in range(200):
                BambuFTPClient(IP, "12345678").connect()

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1, [r.getMessage() for r in warnings]
        # Still recoverable at DEBUG for anyone reading a support bundle.
        assert sum("still cooling off" in r.getMessage() for r in caplog.records) == 199

    def test_a_fresh_handshake_failure_is_announced_again(self, caplog):
        """Once per cool-off, not once per process.

        A printer that recovers and fails again is a new event, and silence
        would be the DEBUG-level problem this fix set out to remove.
        """
        _arm()
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            BambuFTPClient(IP, "12345678").connect()
            _arm()  # a later handshake failure pushes the deadline out
            BambuFTPClient(IP, "12345678").connect()

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 2, [r.getMessage() for r in warnings]


# ---------------------------------------------------------------------------
# The retry loop
# ---------------------------------------------------------------------------
class TestRetryLoopStopsOnAnArmedCooloff:
    async def _run(self, *, cooloff_ip, calls):
        async def op():
            calls.append(1)
            _arm()  # the first attempt is what arms it, as in the report
            return False

        return await with_ftp_retry(
            op,
            max_retries=3,
            retry_delay=0.01,
            operation_name="Download 3MF",
            cooloff_ip=cooloff_ip,
        )

    async def test_a_respecting_caller_stops_after_the_attempt_that_armed_it(self, caplog):
        calls = []
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert await self._run(cooloff_ip=IP, calls=calls) is None
        assert len(calls) == 1

        messages = [r.getMessage() for r in caplog.records]
        assert any("stopping after attempt 1/4" in m for m in messages), messages
        # The tally has to match what was really tried. "failed after 4
        # attempts" for one attempt is how this read as a network problem.
        assert any("failed after 1 attempts" in m for m in messages), messages

    async def test_a_caller_without_the_ip_keeps_its_full_budget(self):
        """Dispatch ignores the cool-off, so the loop must not stop on it.

        Stopping here would undo the exemption from the other end: the
        attempts would still be refused, just by the retry loop instead of by
        ``connect``.
        """
        calls = []
        assert await self._run(cooloff_ip=None, calls=calls) is None
        assert len(calls) == 4


# ---------------------------------------------------------------------------
# The reported failure, end to end
# ---------------------------------------------------------------------------
class TestDispatchKeepsItsAttempts:
    async def test_every_upload_attempt_reaches_the_printer(self, refusing_printer, tmp_path):
        """The trace from the report: cool-off armed, then four dead attempts.

        The reporter's evidence is that the handshake failure is transient --
        a manual connect a second later completes cleanly -- so the retry the
        gate suppressed is precisely the retry that would have worked.
        """
        _arm()
        local = tmp_path / "job.gcode.3mf"
        local.write_bytes(b"x" * 1024)

        result = await with_ftp_retry(
            upload_file_async,
            IP,
            "12345678",
            local,
            "/job.gcode.3mf",
            timeout=5.0,
            printer_model="P2S",
            respect_handshake_cooloff=False,
            max_retries=3,
            retry_delay=0.01,
            operation_name="Upload print to Bambulab P2S-4",
        )

        assert result is None
        assert refusing_printer.connect.call_count == 4

    async def test_without_the_exemption_the_same_upload_touches_nothing(self, refusing_printer, tmp_path):
        """Mutation guard: revert the exemption and the test above must fail.

        Without this, ``call_count == 4`` above would pass for the wrong reason
        if the cool-off were ever simply removed.
        """
        _arm()
        local = tmp_path / "job.gcode.3mf"
        local.write_bytes(b"x" * 1024)

        result = await with_ftp_retry(
            upload_file_async,
            IP,
            "12345678",
            local,
            "/job.gcode.3mf",
            timeout=5.0,
            printer_model="P2S",
            max_retries=3,
            retry_delay=0.01,
            operation_name="Upload print to Bambulab P2S-4",
        )

        assert result is None
        assert refusing_printer.connect.call_count == 0

    async def test_the_pre_upload_delete_is_exempt_too(self, refusing_printer):
        """In the report's trace the delete is what armed the cool-off.

        It runs 8ms before the upload's first attempt, so leaving it gated
        would keep one whole dispatch's worth of the problem in place.
        """
        _arm()
        result = await delete_file_async(
            IP, "12345678", "/job.gcode.3mf", printer_model="P2S", respect_handshake_cooloff=False
        )
        assert result is DeleteResult.FAILED
        assert refusing_printer.connect.call_count == 1

    async def test_a_background_download_is_still_gated(self, refusing_printer):
        """The sweeps keep #2780 exactly: nobody is waiting, so back off."""
        _arm()
        assert await bambu_ftp.download_file_bytes_async(IP, "12345678", "/timelapse/a.mp4") is None
        assert refusing_printer.connect.call_count == 0
