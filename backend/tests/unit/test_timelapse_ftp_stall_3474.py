"""A hung printer file service is not asked again and again (#3474).

At the start of one print an H2S's FTPS service stopped answering. The
print-start baseline came back unread, and the re-read (#3412) then read the
card every 120 s for the whole print: two walks of four directories a round,
each a new connection that hung for 30 s. The printer kept a session for every
one of them and ended up answering every connection with "421 There are too
many connections from your internet address", which no upload got past until
the printer was restarted.

Pinned here:
- a connect or listing that times out opens a cool-off for the background
  timelapse scans only, which a listing that completes lifts;
- the baseline reads the card in one walk, and stops at the cool-off;
- the re-read only runs while the print records a timelapse, backs off on
  failure, and gives up after a few failed rounds;
- a 421 "too many connections" is its own failure, and the queue says so.
"""

import asyncio
import ssl
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import bambu_ftp
from backend.app.services.bambu_ftp import (
    BambuFTPClient,
    FileListResult,
    FtpFailureKind,
    ftps_background_blocked,
    ftps_handshake_blocked,
)

pytestmark = pytest.mark.unit

IP = "192.168.50.77"
TOO_MANY = "421 There are too many connections from your internet address."


@pytest.fixture(autouse=True)
def _clean_state():
    BambuFTPClient._handshake_blocked_until.clear()
    BambuFTPClient._handshake_skip_logged.clear()
    BambuFTPClient._mode_cache.clear()
    # Listings joined by printer and folder: a hung one left by a test would be
    # joined by the next, on a loop that has since closed.
    bambu_ftp._inflight_listings.clear()
    yield
    BambuFTPClient._handshake_blocked_until.clear()
    BambuFTPClient._handshake_skip_logged.clear()
    BambuFTPClient._mode_cache.clear()
    bambu_ftp._inflight_listings.clear()


def _connect_failing_with(error, **client_kwargs) -> BambuFTPClient:
    transport = MagicMock()
    transport.connect.side_effect = error
    with patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=transport):
        client = BambuFTPClient(IP, "12345678", printer_model="H2S", **client_kwargs)
        assert client.connect() is False
    return client


def _wrong_version() -> ssl.SSLError:
    error = ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number")
    error.reason = "WRONG_VERSION_NUMBER"
    return error


# ---------------------------------------------------------------------------
# The stall cool-off
# ---------------------------------------------------------------------------
class TestATimeoutHoldsOffTheBackgroundScans:
    def test_a_connect_timeout_opens_it(self):
        _connect_failing_with(TimeoutError("The handshake operation timed out"))

        assert ftps_background_blocked(IP)

    def test_it_does_not_hold_off_anyone_else(self):
        """The File Manager and dispatch read the handshake gate, which a
        timeout leaves alone: one slow handshake must not lock a user out of
        browsing the printer for five minutes."""
        _connect_failing_with(TimeoutError("The handshake operation timed out"))

        assert not ftps_handshake_blocked(IP)
        transport = MagicMock()
        with patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=transport):
            assert BambuFTPClient(IP, "12345678").connect() is True
        transport.connect.assert_called_once()

    def test_a_completed_listing_lifts_it(self):
        _connect_failing_with(TimeoutError("The handshake operation timed out"))
        client = BambuFTPClient(IP, "12345678")
        client._ftp = MagicMock()

        client.list_files("/timelapse")

        assert not ftps_background_blocked(IP)

    def test_a_connect_alone_does_not(self):
        """The reporter's printer connected fine and then never answered LIST:
        lifting the gate on connect let every next directory through again."""
        _connect_failing_with(TimeoutError("The handshake operation timed out"))

        with patch("backend.app.services.bambu_ftp.ImplicitFTP_TLS", return_value=MagicMock()):
            assert BambuFTPClient(IP, "12345678").connect() is True

        assert ftps_background_blocked(IP)

    def test_it_ends_on_its_own(self, monkeypatch):
        monkeypatch.setattr(bambu_ftp, "_STALL_COOLOFF_SECONDS", 0.0)
        _connect_failing_with(TimeoutError("The handshake operation timed out"))

        assert not ftps_background_blocked(IP)

    def test_a_side_check_does_not_open_it(self):
        """``records_handshake_cooloff=False`` is for a check whose failure
        says nothing about the printer's file service at large."""
        _connect_failing_with(TimeoutError("timed out"), records_handshake_cooloff=False)

        assert not ftps_background_blocked(IP)

    def test_other_connect_failures_do_not_open_it(self):
        _connect_failing_with(OSError("Connection refused"))

        assert not ftps_background_blocked(IP)

    def test_a_listing_that_times_out_on_its_socket_opens_it(self):
        client = BambuFTPClient(IP, "12345678")
        client._ftp = MagicMock()
        client._ftp.retrlines.side_effect = TimeoutError("timed out")

        assert client.list_files("/timelapse") == []
        assert ftps_background_blocked(IP)

    def test_a_listing_that_fails_otherwise_does_not(self):
        client = BambuFTPClient(IP, "12345678")
        client._ftp = MagicMock()
        client._ftp.cwd.side_effect = bambu_ftp.ftplib.error_perm("550 Failed to change directory.")

        assert client.list_files("/record") == []
        assert not ftps_background_blocked(IP)

    async def test_a_listing_that_connected_and_then_hung_opens_it(self, monkeypatch):
        """The reporter's first failure: connected, then LIST /timelapse never
        came back. The worker thread is still inside it when the wait ends."""
        release = asyncio.Event()
        loop = asyncio.get_running_loop()

        class HungClient:
            def __init__(self, *args, **kwargs):
                pass

            def connect(self):
                return True

            def list_files(self, path, raise_on_error=False):
                asyncio.run_coroutine_threadsafe(release.wait(), loop).result(5)
                return []

            def disconnect(self):
                pass

        monkeypatch.setattr(bambu_ftp, "BambuFTPClient", HungClient)
        try:
            result = await bambu_ftp.list_files_result_async(IP, "code", "/timelapse", timeout=0.1)
        finally:
            release.set()

        assert result.timed_out
        assert bambu_ftp._ftps_stalled(IP)

    async def test_a_listing_still_connecting_when_the_wait_ends_opens_it(self, monkeypatch):
        """A hung TLS handshake: the wait and the worker's socket timeout are
        the same length and the wait starts first, so leaving it to ``connect``
        let the next directory open a connection before the gate was set."""
        release = asyncio.Event()
        loop = asyncio.get_running_loop()

        class SlowConnect:
            def __init__(self, *args, **kwargs):
                pass

            def connect(self):
                asyncio.run_coroutine_threadsafe(release.wait(), loop).result(5)
                return False

        monkeypatch.setattr(bambu_ftp, "BambuFTPClient", SlowConnect)
        try:
            result = await bambu_ftp.list_files_result_async(IP, "code", "/timelapse", timeout=0.1)
        finally:
            release.set()

        assert not result.timed_out
        assert bambu_ftp._ftps_stalled(IP)

    async def test_the_plain_listing_opens_it_when_its_wait_ends(self, monkeypatch):
        """The completion scan's listing has no "connected" signal at all."""
        release = asyncio.Event()
        loop = asyncio.get_running_loop()

        class HungClient:
            def __init__(self, *args, **kwargs):
                pass

            def connect(self):
                asyncio.run_coroutine_threadsafe(release.wait(), loop).result(5)
                return False

        monkeypatch.setattr(bambu_ftp, "BambuFTPClient", HungClient)
        try:
            assert await bambu_ftp.list_files_async(IP, "code", "/timelapse", timeout=0.1) == []
        finally:
            release.set()

        assert bambu_ftp._ftps_stalled(IP)

    async def test_a_hung_walk_opens_one_connection(self, monkeypatch):
        """End to end through the real wrapper: the baseline walk stops after
        the first directory whose wait ran out."""
        from backend.app import main

        release = asyncio.Event()
        loop = asyncio.get_running_loop()
        opened: list[str] = []

        class HungClient:
            handshake_blocked = BambuFTPClient.handshake_blocked

            def __init__(self, *args, **kwargs):
                pass

            def connect(self):
                opened.append("connect")
                asyncio.run_coroutine_threadsafe(release.wait(), loop).result(5)
                return False

        real = bambu_ftp.list_files_result_async

        async def _short(ip, code, path, timeout=30.0, socket_timeout=None, printer_model=None):
            return await real(ip, code, path, timeout=0.1, printer_model=printer_model)

        monkeypatch.setattr(bambu_ftp, "BambuFTPClient", HungClient)
        monkeypatch.setattr(bambu_ftp, "list_files_result_async", _short)
        try:
            assert await main._read_timelapse_baseline(_printer()) == (set(), False)
        finally:
            release.set()

        assert opened == ["connect"]


# ---------------------------------------------------------------------------
# One walk of the card
# ---------------------------------------------------------------------------
def _printer():
    # No id: skips the external-storage check, which needs a live status.
    return SimpleNamespace(id=None, ip_address=IP, access_code="12345678", model="H2S")


def _video(name):
    return {"name": name, "is_directory": False, "size": 10, "path": f"/x/{name}"}


class TestTheBaselineIsOneWalk:
    async def test_a_hung_listing_ends_the_round_after_one_connection(self, monkeypatch):
        from backend.app import main

        asked: list[str] = []

        async def _list_result(ip, code, path, timeout=30.0, socket_timeout=None, printer_model=None):
            asked.append(path)
            bambu_ftp._record_ftps_stall(ip, f"listing {path} timed out")
            return FileListResult(files=[], available=False, timed_out=True)

        other_walk = AsyncMock(return_value=[])
        monkeypatch.setattr(bambu_ftp, "list_files_result_async", _list_result)
        monkeypatch.setattr(bambu_ftp, "list_files_async", other_walk)

        assert await main._read_timelapse_baseline(_printer()) == (set(), False)
        assert asked == ["/timelapse"]
        other_walk.assert_not_called()

    async def test_nothing_is_asked_while_the_printer_cools_off(self, monkeypatch):
        from backend.app import main

        bambu_ftp._record_ftps_stall(IP, "test")
        listing = AsyncMock()
        monkeypatch.setattr(bambu_ftp, "list_files_result_async", listing)

        assert await main._read_timelapse_baseline(_printer()) == (set(), False)
        listing.assert_not_called()

    async def test_a_card_without_videos_is_walked_once(self, monkeypatch):
        """It used to be walked twice: once by the video listing, and again by
        the walk that tells "empty" from "unreadable"."""
        from backend.app import main

        asked: list[str] = []

        async def _list_result(ip, code, path, timeout=30.0, socket_timeout=None, printer_model=None):
            asked.append(path)
            if path == "/timelapse":
                return FileListResult(files=[], available=True)
            return FileListResult(files=[], available=False, missing=True)

        monkeypatch.setattr(bambu_ftp, "list_files_result_async", _list_result)
        monkeypatch.setattr(bambu_ftp, "list_files_async", AsyncMock(side_effect=AssertionError("second walk")))

        assert await main._read_timelapse_baseline(_printer()) == (set(), True)
        assert asked == list(main._TIMELAPSE_DIRECTORIES)

    async def test_videos_behind_a_failed_directory_still_answer(self, monkeypatch):
        """A directory that fails without a cool-off does not end the walk: a
        later one holding the videos says what is on the card."""
        from backend.app import main

        async def _list_result(ip, code, path, timeout=30.0, socket_timeout=None, printer_model=None):
            if path == "/timelapse":
                return FileListResult(files=[], available=False)
            if path == "/record":
                return FileListResult(files=[_video("video_1.avi")], available=True)
            return FileListResult(files=[], available=False, missing=True)

        monkeypatch.setattr(bambu_ftp, "list_files_result_async", _list_result)

        assert await main._read_timelapse_baseline(_printer()) == ({"video_1.avi"}, True)

    async def test_a_failed_directory_with_nothing_after_it_is_unread(self, monkeypatch):
        from backend.app import main

        async def _list_result(ip, code, path, timeout=30.0, socket_timeout=None, printer_model=None):
            if path == "/timelapse":
                return FileListResult(files=[], available=False)
            return FileListResult(files=[], available=False, missing=True)

        monkeypatch.setattr(bambu_ftp, "list_files_result_async", _list_result)

        assert await main._read_timelapse_baseline(_printer()) == (set(), False)

    async def test_the_completion_scan_stops_at_the_cool_off(self, monkeypatch):
        from backend.app import main

        asked: list[str] = []

        async def _list(ip, code, path, printer_model=None):
            asked.append(path)
            bambu_ftp._record_ftps_stall(ip, f"listing {path} timed out")
            return []

        monkeypatch.setattr(bambu_ftp, "list_files_async", _list)

        assert await main._list_timelapse_videos(_printer()) == ([], None)
        assert asked == ["/timelapse"]


# ---------------------------------------------------------------------------
# The re-read
# ---------------------------------------------------------------------------
PRINTER_ID = 4747


@asynccontextmanager
async def _session():
    db = MagicMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id=PRINTER_ID))
    yield db


class _Reread:
    """Runs the re-read with its sleeps recorded instead of waited out."""

    def __init__(self, main, monkeypatch, *, timelapse=True, reads=None):
        self.main = main
        self.sleeps: list[float] = []
        self.status = SimpleNamespace(connected=True, state="RUNNING", timelapse=timelapse)
        self.read = AsyncMock(side_effect=reads) if reads is not None else AsyncMock(return_value=(set(), False))
        self.names: set[str] = set()

        async def _sleep(seconds):
            self.sleeps.append(seconds)

        monkeypatch.setattr(main.asyncio, "sleep", _sleep)
        monkeypatch.setattr(main, "_read_timelapse_baseline", self.read)
        monkeypatch.setattr(main, "async_session", _session)
        monkeypatch.setattr(main.printer_manager, "get_status", lambda _pid: self.status)
        monkeypatch.setattr(main, "_TIMELAPSE_BASELINE_REREAD_MAX_ATTEMPTS", 40)
        main._timelapse_baselines[PRINTER_ID] = self.names
        main._timelapse_baselines_unread.add(PRINTER_ID)

    async def run(self):
        try:
            await asyncio.wait_for(self.main._reread_unread_timelapse_baseline(PRINTER_ID, self.names, None, None), 5)
        finally:
            self.main._timelapse_baselines.pop(PRINTER_ID, None)
            self.main._timelapse_baselines_unread.discard(PRINTER_ID)


class TestTheReread:
    async def test_a_print_without_a_timelapse_never_reads_the_card(self, monkeypatch):
        """The reporter's printer records no timelapses, and the completion
        scan only looks for a video when one was recorded."""
        from backend.app import main

        reread = _Reread(main, monkeypatch, timelapse=False)
        await reread.run()

        reread.read.assert_not_called()
        # Skipped, not ended: the flag can arrive after the print has started.
        assert len(reread.sleeps) == main._TIMELAPSE_BASELINE_REREAD_MAX_ATTEMPTS

    async def test_failed_rounds_back_off_and_then_stop(self, monkeypatch):
        from backend.app import main

        reread = _Reread(main, monkeypatch)
        await reread.run()

        assert reread.read.await_count == main._TIMELAPSE_BASELINE_REREAD_MAX_FAILED_ROUNDS
        assert reread.sleeps == [120.0, 240.0, 480.0, 960.0, 1800.0]

    async def test_the_wait_is_capped(self, monkeypatch):
        from backend.app import main

        monkeypatch.setattr(main, "_TIMELAPSE_BASELINE_REREAD_MAX_FAILED_ROUNDS", 8)
        reread = _Reread(main, monkeypatch)
        await reread.run()

        assert max(reread.sleeps) == main._TIMELAPSE_BASELINE_REREAD_MAX_INTERVAL_SECONDS
        assert reread.sleeps[-3:] == [1800.0, 1800.0, 1800.0]

    async def test_a_round_that_raises_counts_as_failed(self, monkeypatch):
        from backend.app import main

        reread = _Reread(main, monkeypatch, reads=[OSError("boom")] * 5)
        await reread.run()

        assert reread.read.await_count == 5
        assert reread.sleeps == [120.0, 240.0, 480.0, 960.0, 1800.0]

    async def test_a_reading_after_failures_still_becomes_the_baseline(self, monkeypatch):
        from backend.app import main

        reread = _Reread(main, monkeypatch, reads=[(set(), False), (set(), False), ({"video_1.avi"}, True)])
        names = reread.names
        await reread.run()

        assert names == {"video_1.avi"}
        assert reread.sleeps == [120.0, 240.0, 480.0]


# ---------------------------------------------------------------------------
# 421 too many connections
# ---------------------------------------------------------------------------
class TestAPrinterOutOfSessions:
    def test_a_421_is_its_own_failure(self):
        with patch("backend.app.services.bambu_ftp._read_cleartext_reply", return_value=TOO_MANY):
            client = _connect_failing_with(_wrong_version())

        assert client.last_failure is not None
        assert client.last_failure.kind is FtpFailureKind.SESSIONS
        assert client.last_failure.code == "421"
        assert TOO_MANY in client.last_failure.detail

    def test_attempts_later_in_the_window_still_know_it(self):
        """Only the first failure in a cool-off asks the printer what it said;
        a dispatch ignores the cool-off and makes up to four attempts."""
        with patch("backend.app.services.bambu_ftp._read_cleartext_reply", return_value=TOO_MANY) as probe:
            _connect_failing_with(_wrong_version())
            again = _connect_failing_with(_wrong_version(), respect_handshake_cooloff=False)

        probe.assert_called_once()
        assert again.last_failure is not None
        assert again.last_failure.kind is FtpFailureKind.SESSIONS

    def test_a_new_window_forgets_the_old_reply(self):
        with patch("backend.app.services.bambu_ftp._read_cleartext_reply", return_value=TOO_MANY):
            _connect_failing_with(_wrong_version())
        BambuFTPClient._handshake_blocked_until[IP] = 0.0  # expired

        with patch("backend.app.services.bambu_ftp._read_cleartext_reply", return_value=None):
            client = _connect_failing_with(_wrong_version(), respect_handshake_cooloff=False)

        assert client.last_failure is not None
        assert client.last_failure.kind is FtpFailureKind.HANDSHAKE

    @pytest.mark.parametrize(
        "reply",
        ["421 Service not available, closing control connection.", "500 OOPS: vsftpd: refusing to run"],
    )
    def test_other_cleartext_replies_stay_a_handshake_failure(self, reply):
        with patch("backend.app.services.bambu_ftp._read_cleartext_reply", return_value=reply):
            client = _connect_failing_with(_wrong_version())

        assert client.last_failure is not None
        assert client.last_failure.kind is FtpFailureKind.HANDSHAKE

    def test_the_queue_says_so_and_still_retries(self):
        from backend.app.services.print_scheduler import (
            _UPLOAD_REQUEUE_KINDS,
            _asks_nothing_new,
            _refused_upload_hold_reason,
        )

        reason = _refused_upload_hold_reason("F3DP", FtpFailureKind.SESSIONS)

        assert "421" in reason
        assert "restart the printer" in reason
        assert FtpFailureKind.SESSIONS in _UPLOAD_REQUEUE_KINDS
        # Still reads as a refusal to the model-job summary.
        assert _asks_nothing_new(reason)
