"""Unit tests for SharedStreamHub lifecycle, race-condition guards, and frame buffer cleanup.

Covers get_or_start(), restart() three-phase protocol, idle timeout, stop(),
is_active(), get_last_frame(), status(), and cleanup start/stop.
"""

import asyncio
import time
from unittest.mock import patch

import pytest


def _make_frame_source(frames=5, interval=0.01):
    """Create a simple async frame generator for testing producers."""

    async def source():
        for i in range(frames):
            yield f"frame-{i}".encode()
            await asyncio.sleep(interval)

    return source


class TestSharedStreamHubGetOrStart:
    """Tests for SharedStreamHub.get_or_start()."""

    @pytest.mark.asyncio
    async def test_starts_new_producer_when_none_exists(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        starter = _make_frame_source()
        entry = await hub.get_or_start(1, starter, params_key="5-15-0.5")

        assert entry.alive is True
        assert entry.params_key == "5-15-0.5"
        assert entry.task is not None
        assert 1 in hub._streams
        await hub.stop_all()

    @pytest.mark.asyncio
    async def test_reuses_existing_alive_producer(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        starter = _make_frame_source(frames=100, interval=0.1)
        entry1 = await hub.get_or_start(1, starter, params_key="5-15-0.5")

        # Second call with different params should still reuse existing
        entry2 = await hub.get_or_start(1, _make_frame_source(), params_key="10-20-1.0")
        assert entry1 is entry2
        await hub.stop_all()

    @pytest.mark.asyncio
    async def test_starts_new_producer_when_existing_is_dead(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()
        # Insert a dead entry
        dead_entry = _SharedStream(params_key="old")
        dead_entry.alive = False
        hub._streams[1] = dead_entry

        starter = _make_frame_source()
        new_entry = await hub.get_or_start(1, starter, params_key="new")
        assert new_entry is not dead_entry
        assert new_entry.alive is True
        assert new_entry.params_key == "new"
        await hub.stop_all()


class TestSharedStreamHubRestart:
    """Tests for SharedStreamHub.restart() three-phase protocol."""

    @pytest.mark.asyncio
    async def test_restart_with_different_params(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        starter1 = _make_frame_source(frames=100, interval=0.1)
        entry1 = await hub.get_or_start(1, starter1, params_key="5-15-0.5")

        starter2 = _make_frame_source(frames=100, interval=0.1)
        entry2 = await hub.restart(1, starter2, params_key="10-20-1.0")

        assert entry2 is not entry1
        assert entry1.alive is False
        assert entry2.alive is True
        assert entry2.params_key == "10-20-1.0"
        await hub.stop_all()

    @pytest.mark.asyncio
    async def test_restart_same_params_returns_existing(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        starter = _make_frame_source(frames=100, interval=0.1)
        entry1 = await hub.get_or_start(1, starter, params_key="5-15-0.5")

        entry2 = await hub.restart(1, _make_frame_source(), params_key="5-15-0.5")
        assert entry2 is entry1
        assert entry2.alive is True
        await hub.stop_all()

    @pytest.mark.asyncio
    async def test_restart_no_existing_creates_new(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        starter = _make_frame_source()
        entry = await hub.restart(1, starter, params_key="5-15-0.5")

        assert entry.alive is True
        assert entry.params_key == "5-15-0.5"
        await hub.stop_all()

    @pytest.mark.asyncio
    async def test_restart_identity_check_prevents_stale_removal(self):
        """Producer's finally block uses identity check to avoid removing a replacement entry."""
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        # Start a producer that will finish quickly
        starter1 = _make_frame_source(frames=2, interval=0.01)
        entry1 = await hub.get_or_start(1, starter1, params_key="old")

        # Let the first producer finish naturally
        await asyncio.sleep(0.1)

        # Now start a new one — it should NOT be removed when old producer's finally runs
        starter2 = _make_frame_source(frames=100, interval=0.1)
        entry2 = await hub.get_or_start(1, starter2, params_key="new")

        assert entry2 is not entry1
        assert 1 in hub._streams
        assert hub._streams[1] is entry2
        await hub.stop_all()


class TestSharedStreamHubIdleTimeout:
    """Tests for producer auto-stop after IDLE_TIMEOUT without viewer activity."""

    @pytest.mark.asyncio
    async def test_producer_auto_stops_when_idle(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        # Set a very short idle timeout for testing
        original_timeout = hub.IDLE_TIMEOUT
        hub.IDLE_TIMEOUT = 0.05  # 50ms

        starter = _make_frame_source(frames=100, interval=0.01)
        entry = await hub.get_or_start(1, starter, params_key="test")

        # Set last_accessed far in the past so idle check triggers immediately
        entry.last_accessed = time.monotonic() - 10

        # Wait for the producer to detect idle and stop
        await asyncio.sleep(0.2)

        assert entry.alive is False
        hub.IDLE_TIMEOUT = original_timeout

    @pytest.mark.asyncio
    async def test_producer_stays_alive_when_accessed(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        original_timeout = hub.IDLE_TIMEOUT
        hub.IDLE_TIMEOUT = 0.5

        starter = _make_frame_source(frames=20, interval=0.05)
        entry = await hub.get_or_start(1, starter, params_key="test")

        # Keep touching last_accessed
        for _ in range(5):
            entry.last_accessed = time.monotonic()
            await asyncio.sleep(0.05)

        assert entry.alive is True
        hub.IDLE_TIMEOUT = original_timeout
        await hub.stop_all()


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


class TestRestartStaleSameParams:
    """Tests for restart() with stale producer and same params (B1 regression)."""

    @pytest.mark.asyncio
    async def test_restart_stale_same_params_does_not_keyerror(self):
        """Stale producer with same params should be replaced without KeyError."""
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        starter = _make_frame_source(frames=100, interval=0.1)
        entry = await hub.get_or_start(1, starter, params_key="5-15-0.5")

        # Wait for at least one frame so frame_seq > 0
        await asyncio.sleep(0.05)
        assert entry.frame_seq > 0

        # Simulate stale: last frame was produced long ago
        entry.last_frame_produced = time.monotonic() - 60.0

        # This should NOT raise KeyError
        new_entry = await hub.restart(1, _make_frame_source(frames=100, interval=0.1), params_key="5-15-0.5")
        assert new_entry is not entry
        assert new_entry.alive is True
        assert new_entry.params_key == "5-15-0.5"
        assert 1 in hub._streams
        await hub.stop_all()

    @pytest.mark.asyncio
    async def test_restart_stale_same_params_old_entry_marked_dead(self):
        """The old stale entry should be marked dead after restart."""
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        starter = _make_frame_source(frames=100, interval=0.1)
        old_entry = await hub.get_or_start(1, starter, params_key="5-15-0.5")

        await asyncio.sleep(0.05)
        old_entry.last_frame_produced = time.monotonic() - 60.0

        await hub.restart(1, _make_frame_source(frames=100, interval=0.1), params_key="5-15-0.5")
        assert old_entry.alive is False
        await hub.stop_all()


class TestMakeViewer:
    """Tests for SharedStreamHub.make_viewer() frame delivery."""

    @pytest.mark.asyncio
    async def test_viewer_yields_mjpeg_formatted_frames(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        starter = _make_frame_source(frames=3, interval=0.01)
        entry = await hub.get_or_start(1, starter, params_key="test")

        viewer = hub.make_viewer(entry, fps=30)
        chunks = []
        async for chunk in viewer:
            chunks.append(chunk)
            if len(chunks) >= 9:  # 3 frames x 3 chunks each (header, data, boundary)
                break

        # Each frame produces 3 chunks: MJPEG header, frame data, trailing CRLF
        assert len(chunks) >= 3
        assert b"--frame" in chunks[0]
        assert b"Content-Type: image/jpeg" in chunks[0]
        assert chunks[2] == b"\r\n"
        await hub.stop_all()

    @pytest.mark.asyncio
    async def test_viewer_exits_when_entry_marked_dead(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        starter = _make_frame_source(frames=100, interval=0.1)
        entry = await hub.get_or_start(1, starter, params_key="test")

        viewer = hub.make_viewer(entry, fps=30)
        # Kill the entry
        entry.alive = False

        chunks = []
        async for chunk in viewer:
            chunks.append(chunk)
        # Should exit promptly
        assert len(chunks) == 0 or len(chunks) <= 3  # at most one frame in flight
        await hub.stop_all()

    @pytest.mark.asyncio
    async def test_viewer_count_incremented_and_decremented(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        starter = _make_frame_source(frames=3, interval=0.01)
        entry = await hub.get_or_start(1, starter, params_key="test")

        assert entry.viewer_count == 0

        viewer = hub.make_viewer(entry, fps=30)
        # Consume one chunk to trigger the viewer_count increment
        chunk_iter = viewer.__aiter__()
        try:
            await asyncio.wait_for(chunk_iter.__anext__(), timeout=1.0)
            assert entry.viewer_count == 1
        except (StopAsyncIteration, TimeoutError):
            pass

        # Close the viewer
        await chunk_iter.aclose()
        assert entry.viewer_count == 0
        await hub.stop_all()

    @pytest.mark.asyncio
    async def test_viewer_skips_duplicate_sequences(self):
        """Viewer should not yield the same frame twice (same seq)."""
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()
        entry = _SharedStream(params_key="test")
        entry.alive = True

        # Manually set a frame
        entry.frame = b"\xff\xd8test\xff\xd9"
        entry.frame_seq = 1

        viewer = hub.make_viewer(entry, fps=30)
        chunks = []

        async def consume():
            async for chunk in viewer:
                chunks.append(chunk)
                if len(chunks) >= 3:
                    # After getting one full frame, don't produce new frames
                    # Give enough time for the viewer to poll again
                    await asyncio.sleep(0.1)
                    entry.alive = False

        await asyncio.wait_for(consume(), timeout=2.0)
        # Should have exactly 3 chunks (one frame: header + data + boundary)
        assert len(chunks) == 3


class TestProducerErrorHandling:
    """Tests for _run_producer() error handling paths."""

    @pytest.mark.asyncio
    async def test_producer_sets_error_on_source_exception(self):
        from backend.app.api.routes.camera import SharedStreamHub

        async def failing_source():
            yield b"frame-0"
            raise RuntimeError("stream broke")

        hub = SharedStreamHub()
        entry = await hub.get_or_start(1, failing_source, params_key="test")

        # Wait for producer to hit the error
        await asyncio.sleep(0.1)
        assert entry.alive is False
        assert entry.error == "stream broke"

    @pytest.mark.asyncio
    async def test_producer_identity_check_preserves_replacement(self):
        """Producer's finally block should not remove a replacement entry."""
        from backend.app.api.routes.camera import SharedStreamHub

        async def short_source():
            yield b"frame"

        hub = SharedStreamHub()
        entry1 = await hub.get_or_start(1, short_source, params_key="old")

        # Wait for first producer to finish
        await asyncio.sleep(0.1)

        # Start a replacement
        entry2 = await hub.get_or_start(1, _make_frame_source(frames=100, interval=0.1), params_key="new")
        assert entry2 is not entry1
        assert hub._streams.get(1) is entry2
        await hub.stop_all()

    @pytest.mark.asyncio
    async def test_producer_auto_stops_on_idle_timeout(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        original_timeout = hub.IDLE_TIMEOUT
        hub.IDLE_TIMEOUT = 0.05

        entry = await hub.get_or_start(1, _make_frame_source(frames=100, interval=0.01), params_key="test")
        entry.last_accessed = time.monotonic() - 10

        await asyncio.sleep(0.2)
        assert entry.alive is False
        hub.IDLE_TIMEOUT = original_timeout


class TestStaleProducerTimeoutConstant:
    """Tests that STALE_PRODUCER_TIMEOUT is a class constant (M3)."""

    def test_stale_timeout_is_class_constant(self):
        from backend.app.api.routes.camera import SharedStreamHub

        assert hasattr(SharedStreamHub, "STALE_PRODUCER_TIMEOUT")
        assert SharedStreamHub.STALE_PRODUCER_TIMEOUT == 45.0

    def test_stale_timeout_is_overridable_per_instance(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        hub.STALE_PRODUCER_TIMEOUT = 10.0
        assert hub.STALE_PRODUCER_TIMEOUT == 10.0
        # Class default unchanged
        assert SharedStreamHub.STALE_PRODUCER_TIMEOUT == 45.0
