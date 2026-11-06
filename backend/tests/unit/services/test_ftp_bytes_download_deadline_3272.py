"""The whole-file bytes download gives a big file time to arrive (#3272).

A flat 300 s cap failed a 75 MB P1S timelapse that took 358 s on a healthy
link, and every retry started the file again from scratch. The deadline now
follows the listing's size at the pessimistic floor rate, with no ceiling: the
callers that pass a size hold no DB connection across the transfer.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services import bambu_ftp

IP = "192.0.2.10"


async def _deadline_for(**kwargs) -> float:
    """Run the download with a client that never connects; return its deadline."""
    seen: list[float] = []
    real_wait_for = bambu_ftp.asyncio.wait_for

    async def recording_wait_for(aw, timeout):
        seen.append(timeout)
        return await real_wait_for(aw, timeout)

    client = MagicMock()
    client.connect.return_value = False
    with (
        patch.object(bambu_ftp, "BambuFTPClient", return_value=client),
        patch.object(bambu_ftp.asyncio, "wait_for", recording_wait_for),
    ):
        assert await bambu_ftp.download_file_bytes_async(IP, "12345678", "/timelapse/a.avi", **kwargs) is None
    assert len(seen) == 1
    return seen[0]


@pytest.mark.asyncio
async def test_unknown_size_keeps_default_deadline():
    assert await _deadline_for() == 300.0


@pytest.mark.asyncio
async def test_small_file_keeps_default_deadline():
    assert await _deadline_for(expected_size=2 * 1024 * 1024) == 300.0


@pytest.mark.asyncio
async def test_large_file_deadline_follows_size_without_ceiling():
    # The reporter's largest timelapse; at the floor rate it needs ~94 minutes.
    size = 143_788_322
    deadline = await _deadline_for(expected_size=size)
    assert deadline == size / bambu_ftp._DOWNLOAD_FLOOR_BYTES_PER_SEC
    assert deadline > bambu_ftp._DOWNLOAD_MAX_TIMEOUT


@pytest.mark.asyncio
async def test_explicit_longer_timeout_is_never_shortened():
    assert await _deadline_for(expected_size=1024, timeout=900.0) == 900.0


@pytest.mark.asyncio
async def test_cap_log_names_file_and_size(caplog):
    async def expire(aw, timeout):
        # Let the worker finish while the client is still patched, so no real
        # connection is attempted after the patch is gone.
        await aw
        raise TimeoutError

    client = MagicMock()
    client.connect.return_value = False
    with (
        patch.object(bambu_ftp, "BambuFTPClient", return_value=client),
        patch.object(bambu_ftp.asyncio, "wait_for", expire),
        caplog.at_level(logging.WARNING, logger=bambu_ftp.logger.name),
        # A deadline that fitted the file's size is not retried (TO-C1).
        pytest.raises(bambu_ftp.DownloadDeadlineExceeded),
    ):
        await bambu_ftp.download_file_bytes_async(IP, "12345678", "/timelapse/a.avi", expected_size=75_647_046)
    assert "/timelapse/a.avi (75647046 bytes) exceeded its 2955s cap" in caplog.text
