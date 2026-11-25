"""The cleanup tick leaves a reconnecting stream's entry alone (RC-C13).

An X1C ends every RTSP session after about a minute. Between its ffmpeg
exiting and the next one starting, the stream's entry still holds the dead
process while the generator tears it down. A cleanup tick in that window
removed the entry; the generator then read "removed" as "stopped", logged
"stopping reconnect" and ended the live view, and one-shot captures
meanwhile saw no stream and dialled the camera themselves.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.app.api.routes import camera

pytestmark = pytest.mark.asyncio

SID = "7-fanout-deadbeef"


@pytest.fixture
def dead_stream():
    camera._active_streams[SID] = SimpleNamespace(pid=999_999_999, returncode=0)
    yield
    camera._active_streams.pop(SID, None)
    camera._disconnect_events.pop(SID, None)


async def _cleanup():
    with patch.object(camera, "_scan_bambu_ffmpeg_pids", return_value=[]):
        await asyncio.wait_for(camera.cleanup_orphaned_streams(), timeout=2.0)


async def test_an_entry_whose_generator_will_redial_is_kept(dead_stream):
    camera._disconnect_events[SID] = asyncio.Event()

    await _cleanup()

    assert SID in camera._active_streams
    assert camera.is_stream_active(7)


async def test_a_stopped_stream_s_entry_is_removed(dead_stream):
    stopped = asyncio.Event()
    stopped.set()
    camera._disconnect_events[SID] = stopped

    await _cleanup()

    assert SID not in camera._active_streams


async def test_an_entry_nothing_owns_is_removed(dead_stream):
    await _cleanup()

    assert SID not in camera._active_streams
