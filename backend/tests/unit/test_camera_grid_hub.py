"""Unit tests for SharedStreamHub additional methods and frame buffer cleanup lifecycle.

Covers stop(), is_active(), get_last_frame(), status(), and cleanup start/stop.
"""

import asyncio
import time
from unittest.mock import patch

import pytest


class TestSharedStreamHubStop:
    """Tests for SharedStreamHub.stop()."""

    @pytest.mark.asyncio
    async def test_stop_returns_false_for_missing(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        assert await hub.stop(999) is False

    @pytest.mark.asyncio
    async def test_stop_marks_entry_dead(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()
        entry = _SharedStream(params_key="5-15-0.5")
        entry.alive = True
        # Provide a completed task so stop() doesn't hang
        entry.task = asyncio.ensure_future(asyncio.sleep(0))
        await entry.task  # let it finish
        hub._streams[1] = entry

        result = await hub.stop(1)
        assert result is True
        assert entry.alive is False
        assert entry.frame is None
        assert 1 not in hub._streams

    @pytest.mark.asyncio
    async def test_stop_cancels_running_task(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()
        entry = _SharedStream(params_key="5-15-0.5")
        entry.alive = True

        # Create a long-running task
        async def long_running():
            await asyncio.sleep(100)

        entry.task = asyncio.create_task(long_running())
        hub._streams[1] = entry

        result = await hub.stop(1)
        assert result is True
        assert entry.task.cancelled() or entry.task.done()


class TestSharedStreamHubIsActive:
    """Tests for SharedStreamHub.is_active()."""

    @pytest.mark.asyncio
    async def test_returns_false_for_missing(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        assert hub.is_active(999) is False

    @pytest.mark.asyncio
    async def test_returns_true_for_alive(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()
        entry = _SharedStream()
        entry.alive = True
        hub._streams[1] = entry
        assert hub.is_active(1) is True

    @pytest.mark.asyncio
    async def test_returns_false_for_dead(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()
        entry = _SharedStream()
        entry.alive = False
        hub._streams[1] = entry
        assert hub.is_active(1) is False


class TestSharedStreamHubGetLastFrame:
    """Tests for SharedStreamHub.get_last_frame()."""

    def test_returns_none_for_missing(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        assert hub.get_last_frame(999) is None

    def test_returns_none_for_dead_entry(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()
        entry = _SharedStream()
        entry.alive = False
        entry.frame = b"\xff\xd8frame\xff\xd9"
        hub._streams[1] = entry
        assert hub.get_last_frame(1) is None

    def test_returns_frame_for_alive_entry(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()
        entry = _SharedStream()
        entry.alive = True
        entry.frame = b"\xff\xd8frame\xff\xd9"
        hub._streams[1] = entry
        assert hub.get_last_frame(1) == b"\xff\xd8frame\xff\xd9"

    def test_returns_none_when_no_frame_yet(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()
        entry = _SharedStream()
        entry.alive = True
        entry.frame = None
        hub._streams[1] = entry
        assert hub.get_last_frame(1) is None


class TestSharedStreamHubStatus:
    """Tests for SharedStreamHub.status() debugging endpoint."""

    def test_empty_hub(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        s = hub.status()
        assert s["producer_count"] == 0
        assert s["producers"] == {}

    def test_status_includes_all_entries(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()
        for pid in [1, 2, 3]:
            entry = _SharedStream(params_key=f"5-15-{pid}")
            entry.alive = pid != 3  # Third is dead
            entry.viewer_count = pid
            entry.frame_seq = pid * 10
            hub._streams[pid] = entry

        s = hub.status()
        assert s["producer_count"] == 3
        assert set(s["producers"].keys()) == {1, 2, 3}
        assert s["producers"][1]["alive"] is True
        assert s["producers"][3]["alive"] is False
        assert s["producers"][2]["viewers"] == 2
        assert s["producers"][1]["frames_produced"] == 10


class TestGetBufferedFrame:
    """Tests for get_buffered_frame() checking hub then fallback."""

    def test_returns_hub_frame(self):
        import backend.app.api.routes.camera as cam
        from backend.app.api.routes.camera import _SharedStream

        entry = _SharedStream()
        entry.alive = True
        entry.frame = b"hub_frame"

        original_streams = dict(cam._hub._streams)
        cam._hub._streams[42] = entry
        try:
            result = cam.get_buffered_frame(42)
            assert result == b"hub_frame"
        finally:
            cam._hub._streams.clear()
            cam._hub._streams.update(original_streams)

    def test_returns_none_when_no_hub_entry(self):
        import backend.app.api.routes.camera as cam

        result = cam.get_buffered_frame(9999)
        assert result is None


class TestFrameBufferCleanupLifecycle:
    """Tests for start_frame_buffer_cleanup / stop_frame_buffer_cleanup."""

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        import backend.app.api.routes.camera as cam

        # Ensure clean state
        cam.stop_frame_buffer_cleanup()
        assert cam._cleanup_task is None

        cam.start_frame_buffer_cleanup()
        assert cam._cleanup_task is not None
        assert not cam._cleanup_task.done()

        # Cleanup
        cam.stop_frame_buffer_cleanup()
        assert cam._cleanup_task is None

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        import backend.app.api.routes.camera as cam

        cam.stop_frame_buffer_cleanup()
        cam.start_frame_buffer_cleanup()
        task = cam._cleanup_task

        cam.stop_frame_buffer_cleanup()
        # Yield control so the cancellation propagates
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        import backend.app.api.routes.camera as cam

        cam.stop_frame_buffer_cleanup()
        cam.start_frame_buffer_cleanup()
        first_task = cam._cleanup_task

        cam.start_frame_buffer_cleanup()
        assert cam._cleanup_task is first_task  # Same task, not a new one

        cam.stop_frame_buffer_cleanup()
