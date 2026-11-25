"""FTP downloads stop at their deadline, and the deadline fits the file (TO-C1, TO-C3).

The bytes download used to give up on its caller at the deadline and leave the
transfer running in its worker thread, and ``with_ftp_retry`` read that as a
retryable failure, so each retry opened another FTP session alongside the
first. The printer file download was held at a flat 600 s whatever the size:
the size-based extension was capped at 300 s in total, below that base.
"""

import asyncio
import threading
from pathlib import Path

import pytest

from backend.app.services import bambu_ftp, printer_media
from backend.app.services.bambu_ftp import DownloadDeadlineExceeded


class _StuckClient:
    """A printer that keeps a transfer going until it is told to stop."""

    instances: list["_StuckClient"] = []

    def __init__(self, *_args, **_kwargs) -> None:
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        _StuckClient.instances.append(self)

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    def download_file(self, _remote_path, expected_size=None, cancel_event=None, **_kwargs):
        try:
            while cancel_event is None or not cancel_event.is_set():
                if self.cancelled.wait(0.01):
                    break
            self.cancelled.set()
            return None
        finally:
            self.finished.set()


@pytest.fixture
def stuck(monkeypatch):
    _StuckClient.instances = []
    monkeypatch.setattr(bambu_ftp, "BambuFTPClient", _StuckClient)
    return _StuckClient.instances


@pytest.mark.asyncio
async def test_a_sized_download_past_its_deadline_stops_and_says_so(stuck):
    with pytest.raises(DownloadDeadlineExceeded):
        await bambu_ftp.download_file_bytes_async("10.0.0.5", "code", "/timelapse/v.mp4", timeout=0.1, expected_size=10)

    # The transfer was stopped before the call returned, not left running.
    assert stuck[0].cancelled.is_set() and stuck[0].finished.is_set()


@pytest.mark.asyncio
async def test_an_unsized_download_past_its_deadline_stops_and_fails(stuck):
    result = await bambu_ftp.download_file_bytes_async("10.0.0.5", "code", "/model.3mf", timeout=0.1)

    assert result is None
    assert stuck[0].cancelled.is_set() and stuck[0].finished.is_set()


@pytest.mark.asyncio
async def test_a_retry_never_starts_a_second_session_beside_the_first(stuck):
    with pytest.raises(DownloadDeadlineExceeded):
        await bambu_ftp.with_ftp_retry(
            bambu_ftp.download_file_bytes_async,
            "10.0.0.5",
            "code",
            "/timelapse/v.mp4",
            timeout=0.1,
            expected_size=10,
            max_retries=3,
            retry_delay=0,
            operation_name="test",
        )

    assert len(stuck) == 1


@pytest.mark.asyncio
async def test_a_cancelled_caller_stops_the_transfer(stuck):
    task = asyncio.create_task(bambu_ftp.download_file_bytes_async("10.0.0.5", "code", "/model.3mf", timeout=30))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert stuck[0].cancelled.is_set() and stuck[0].finished.is_set()


@pytest.mark.asyncio
async def test_a_download_that_finishes_returns_its_bytes(monkeypatch):
    class _Client(_StuckClient):
        def download_file(self, _remote_path, expected_size=None, cancel_event=None, **_kwargs):
            return b"video"

    monkeypatch.setattr(bambu_ftp, "BambuFTPClient", _Client)

    assert await bambu_ftp.download_file_bytes_async("10.0.0.5", "code", "/v.mp4", expected_size=5) == b"video"


@pytest.mark.asyncio
async def test_an_unlisted_download_gets_the_time_its_reported_size_buys(monkeypatch):
    """No listing size: the SIZE the printer reports at the start extends the
    deadline, as for download_file_async (a 60 MB 3MF on an A1)."""

    class _Slow(_StuckClient):
        def download_file(self, _remote_path, expected_size=None, cancel_event=None, size_callback=None, **_kwargs):
            size_callback(10 * 1024 * 1024)
            if self.cancelled.wait(0.3) or (cancel_event and cancel_event.is_set()):
                return None
            return b"3mf"

    monkeypatch.setattr(bambu_ftp, "BambuFTPClient", _Slow)

    assert await bambu_ftp.download_file_bytes_async("10.0.0.5", "code", "/model.3mf", timeout=0.1) == b"3mf"


def _client_with(error: Exception) -> bambu_ftp.BambuFTPClient:
    from unittest.mock import MagicMock

    client = bambu_ftp.BambuFTPClient("10.0.0.5", "code")
    client._ftp = MagicMock()
    client._ftp.retrbinary.side_effect = error
    client._ftp.size.side_effect = error
    return client


def test_a_missing_path_is_told_apart_when_asked():
    import ftplib  # nosec B402 -- tests construct real ftplib error types

    missing = ftplib.error_perm("550 No such file")
    with pytest.raises(bambu_ftp.FileNotOnPrinterError):
        _client_with(missing).download_file("/gone.3mf", report_missing=True)
    with pytest.raises(bambu_ftp.FileNotOnPrinterError):
        _client_with(missing).download_file("/gone.3mf", size_callback=lambda _n: None, report_missing=True)
    # Every other caller still reads it as a failed download.
    assert _client_with(missing).download_file("/gone.3mf") is None


def test_other_ftp_errors_are_failures_not_missing():
    import ftplib  # nosec B402 -- tests construct real ftplib error types

    assert _client_with(ftplib.error_perm("530 Not logged in")).download_file("/x", report_missing=True) is None
    assert _client_with(OSError("reset")).download_file("/x", report_missing=True) is None


def test_the_size_extension_can_reach_past_the_print_start_ceiling():
    size = 250 * 1024 * 1024  # a 250 MB /ipcam chunk
    assert bambu_ftp._download_extension(size, 600) == 0.0  # print start keeps its 300 s ceiling
    assert bambu_ftp._download_extension(size, 600, ceiling=1800) == pytest.approx(1200)
    # A preview of a 60 MB 3MF at the default 300 s base, with the file browser's budget.
    assert bambu_ftp._download_extension(60 * 1024 * 1024, 300, ceiling=1800) == pytest.approx(1500)


@pytest.mark.asyncio
async def test_a_transfer_that_does_not_stop_at_once_is_still_waited_for(monkeypatch):
    """A read blocked on the socket only notices the cancel when it returns:
    the call must not come back (and a retry must not start) before that."""

    class _Deaf(_StuckClient):
        def download_file(self, _remote_path, expected_size=None, cancel_event=None, **_kwargs):
            try:
                self.cancelled.wait(0.8)  # ignores the cancel until the "socket" returns
                return None
            finally:
                self.finished.set()

    monkeypatch.setattr(bambu_ftp, "BambuFTPClient", _Deaf)
    _StuckClient.instances = []

    with pytest.raises(DownloadDeadlineExceeded):
        await bambu_ftp.download_file_bytes_async("10.0.0.5", "code", "/v.mp4", timeout=0.1, expected_size=10)

    assert _StuckClient.instances[0].finished.is_set()


@pytest.mark.asyncio
async def test_a_printer_file_download_may_use_the_whole_job_budget(monkeypatch, tmp_path):
    seen = {}

    async def fake_download(*_args, **kwargs):
        seen.update(kwargs)
        return False

    monkeypatch.setattr(printer_media, "download_file_async", fake_download)
    monkeypatch.setattr(printer_media, "_ensure_printer_zip_root", lambda: tmp_path)

    printer = type("P", (), {"id": 1, "ip_address": "10.0.0.5", "access_code": "c", "model": "P1S"})()
    with pytest.raises(printer_media.PrinterFileDownloadError):
        await printer_media.build_printer_file(printer, "/ipcam/clip.mp4", 250 * 1024 * 1024, bundle_key="k1")

    assert seen["max_total_timeout"] == printer_media.MAX_PRINTER_ZIP_PREPARE_SECONDS


@pytest.mark.asyncio
async def test_a_printer_file_past_its_deadline_is_a_timeout(monkeypatch, tmp_path):
    async def fake_download(*_args, **_kwargs):
        raise DownloadDeadlineExceeded("/ipcam/clip.mp4")

    monkeypatch.setattr(printer_media, "download_file_async", fake_download)
    monkeypatch.setattr(printer_media, "_ensure_printer_zip_root", lambda: tmp_path)

    printer = type("P", (), {"id": 1, "ip_address": "10.0.0.5", "access_code": "c", "model": "P1S"})()
    with pytest.raises(TimeoutError):
        await printer_media.build_printer_file(printer, "/ipcam/clip.mp4", 1000, bundle_key="k2")
    assert not (Path(tmp_path) / "k2").exists()


def test_a_size_refusal_is_left_to_retr():
    """A 550 to SIZE (no such command, a file still being written) does not
    make a file missing that RETR then serves."""
    import ftplib  # nosec B402 -- tests construct real ftplib error types
    from unittest.mock import MagicMock

    client = bambu_ftp.BambuFTPClient("10.0.0.5", "code")
    client._ftp = MagicMock()
    client._ftp.size.side_effect = ftplib.error_perm("550 Could not get file size")
    client._ftp.retrbinary.side_effect = lambda _cmd, write: write(b"3mf-bytes")

    assert client.download_file("/model.3mf", size_callback=lambda _n: None, report_missing=True) == b"3mf-bytes"
