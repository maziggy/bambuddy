"""A slow FTP listing is reported as slow, and refreshes do not stack (TO-C13).

Listing a large /ipcam on a slow SD card can take longer than the wait. That
came back as "printer unavailable", the same as a printer that is not there,
and the worker thread -- not cancellable -- kept running while every refresh
started another one in the shared FTP pool.
"""

import asyncio
import threading
from unittest.mock import patch

import pytest

from backend.app.services import bambu_ftp

pytestmark = pytest.mark.asyncio


class SlowClient:
    made = 0
    release = threading.Event()

    def __init__(self, *args, **kwargs):
        type(self).made += 1

    def connect(self):
        return True

    def list_files(self, path, raise_on_error=False):
        type(self).release.wait(5)
        return [{"name": "ipcam-record.1.mp4"}]

    def disconnect(self):
        pass


@pytest.fixture
def slow():
    SlowClient.made = 0
    SlowClient.release = threading.Event()
    bambu_ftp._inflight_listings.clear()
    with patch.object(bambu_ftp, "BambuFTPClient", SlowClient):
        yield SlowClient
    SlowClient.release.set()
    bambu_ftp._inflight_listings.clear()


async def test_a_listing_that_takes_too_long_says_so(slow):
    result = await bambu_ftp.list_files_result_async("10.0.0.5", "code", "/ipcam", timeout=0.1)

    assert result.available is False
    assert result.timed_out is True


async def test_a_refresh_joins_the_listing_still_running(slow):
    first, second = await asyncio.gather(
        bambu_ftp.list_files_result_async("10.0.0.5", "code", "/ipcam", timeout=0.2),
        bambu_ftp.list_files_result_async("10.0.0.5", "code", "/ipcam", timeout=0.2),
    )
    third = await bambu_ftp.list_files_result_async("10.0.0.5", "code", "/ipcam", timeout=0.1)

    assert slow.made == 1
    assert first.timed_out and second.timed_out and third.timed_out


async def test_once_it_finishes_a_new_listing_starts(slow):
    slow.release.set()
    done = await bambu_ftp.list_files_result_async("10.0.0.5", "code", "/ipcam", timeout=2)
    again = await bambu_ftp.list_files_result_async("10.0.0.5", "code", "/ipcam", timeout=2)

    assert done.available and again.available
    assert slow.made == 2


class NeverConnects(SlowClient):
    def connect(self):
        # A printer behind a router that drops its packets: the connect blocks.
        type(self).release.wait(5)
        return False


async def test_a_printer_that_never_answered_is_unavailable_not_slow():
    """Only a listing that got through connecting is "slow"; one still
    waiting for the printer to answer is a printer that is not there."""
    NeverConnects.made = 0
    NeverConnects.release = threading.Event()
    bambu_ftp._inflight_listings.clear()
    try:
        with patch.object(bambu_ftp, "BambuFTPClient", NeverConnects):
            result = await bambu_ftp.list_files_result_async("10.0.0.6", "code", "/ipcam", timeout=0.1)
    finally:
        NeverConnects.release.set()
        bambu_ftp._inflight_listings.clear()

    assert result.available is False
    assert result.timed_out is False
