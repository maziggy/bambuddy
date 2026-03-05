"""Unit tests for camera grid code-review fixes.

Tests _cleanup_stale_frame_buffers(), SharedStreamHub.get_existing/get_existing_batch,
and the NaN/Inf guard in generate_rtsp_mjpeg_stream.
"""

import time
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# TestCleanupStaleFrameBuffers
# ---------------------------------------------------------------------------

class TestCleanupStaleFrameBuffers:
    """Tests for _cleanup_stale_frame_buffers (camera routes module)."""

    def _import_cleanup(self):
        from backend.app.api.routes.camera import _cleanup_stale_frame_buffers
        return _cleanup_stale_frame_buffers

    def test_cleanup_removes_stale_entries_from_all_three_dicts(self):
        import backend.app.api.routes.camera as cam

        stale_ts = time.monotonic() - cam._FRAME_BUFFER_MAX_AGE - 10
        with (
            patch.dict(cam._last_frames, {99: b"jpeg"}),
            patch.dict(cam._last_frame_times, {99: stale_ts}),
            patch.dict(cam._stream_start_times, {99: stale_ts}),
        ):
            cam._cleanup_stale_frame_buffers()
            assert 99 not in cam._last_frames
            assert 99 not in cam._last_frame_times
            assert 99 not in cam._stream_start_times

    def test_cleanup_preserves_fresh_entries(self):
        import backend.app.api.routes.camera as cam

        fresh_ts = time.monotonic()
        with (
            patch.dict(cam._last_frames, {1: b"jpeg"}, clear=True),
            patch.dict(cam._last_frame_times, {1: fresh_ts}, clear=True),
            patch.dict(cam._stream_start_times, {1: fresh_ts}, clear=True),
        ):
            cam._cleanup_stale_frame_buffers()
            assert 1 in cam._last_frames
            assert 1 in cam._last_frame_times
            assert 1 in cam._stream_start_times

    def test_cleanup_handles_partial_entries(self):
        """Stale _last_frame_times entry but no matching _last_frames or _stream_start_times."""
        import backend.app.api.routes.camera as cam

        stale_ts = time.monotonic() - cam._FRAME_BUFFER_MAX_AGE - 10
        with (
            patch.dict(cam._last_frames, {}, clear=True),
            patch.dict(cam._last_frame_times, {42: stale_ts}, clear=True),
            patch.dict(cam._stream_start_times, {}, clear=True),
        ):
            # Should not raise
            cam._cleanup_stale_frame_buffers()
            assert 42 not in cam._last_frame_times

    def test_cleanup_mixed_fresh_and_stale(self):
        import backend.app.api.routes.camera as cam

        now = time.monotonic()
        stale_ts = now - cam._FRAME_BUFFER_MAX_AGE - 10
        fresh_ts = now

        with (
            patch.dict(cam._last_frames, {1: b"old", 2: b"new"}, clear=True),
            patch.dict(cam._last_frame_times, {1: stale_ts, 2: fresh_ts}, clear=True),
            patch.dict(cam._stream_start_times, {1: stale_ts, 2: fresh_ts}, clear=True),
        ):
            cam._cleanup_stale_frame_buffers()
            # Stale removed
            assert 1 not in cam._last_frames
            assert 1 not in cam._last_frame_times
            assert 1 not in cam._stream_start_times
            # Fresh preserved
            assert 2 in cam._last_frames
            assert 2 in cam._last_frame_times
            assert 2 in cam._stream_start_times


# ---------------------------------------------------------------------------
# TestSharedStreamHubGetExisting
# ---------------------------------------------------------------------------

class TestSharedStreamHubGetExisting:
    """Tests for SharedStreamHub.get_existing()."""

    @pytest.mark.asyncio
    async def test_get_existing_returns_alive_entry(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()
        entry = _SharedStream(params_key="5-15-0.5")
        entry.alive = True
        old_accessed = entry.last_accessed - 10
        entry.last_accessed = old_accessed
        hub._streams[1] = entry

        result = await hub.get_existing(1)
        assert result is entry
        assert result.last_accessed > old_accessed

    @pytest.mark.asyncio
    async def test_get_existing_returns_none_for_missing(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()
        result = await hub.get_existing(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_existing_returns_none_for_dead_entry(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()
        entry = _SharedStream()
        entry.alive = False
        hub._streams[1] = entry

        result = await hub.get_existing(1)
        assert result is None


# ---------------------------------------------------------------------------
# TestSharedStreamHubGetExistingBatch
# ---------------------------------------------------------------------------

class TestSharedStreamHubGetExistingBatch:
    """Tests for SharedStreamHub.get_existing_batch()."""

    @pytest.mark.asyncio
    async def test_batch_partitions_correctly(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()

        alive_entry = _SharedStream()
        alive_entry.alive = True
        hub._streams[1] = alive_entry

        dead_entry = _SharedStream()
        dead_entry.alive = False
        hub._streams[2] = dead_entry

        # 3 is absent

        found, missing = await hub.get_existing_batch([1, 2, 3])
        assert set(found.keys()) == {1}
        assert found[1] is alive_entry
        assert missing == [2, 3]

    @pytest.mark.asyncio
    async def test_batch_updates_last_accessed_only_for_found(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()

        alive_entry = _SharedStream()
        alive_entry.alive = True
        old_ts = alive_entry.last_accessed - 100
        alive_entry.last_accessed = old_ts
        hub._streams[1] = alive_entry

        dead_entry = _SharedStream()
        dead_entry.alive = False
        dead_ts = dead_entry.last_accessed
        hub._streams[2] = dead_entry

        await hub.get_existing_batch([1, 2])
        assert alive_entry.last_accessed > old_ts
        assert dead_entry.last_accessed == dead_ts

    @pytest.mark.asyncio
    async def test_batch_all_missing(self):
        from backend.app.api.routes.camera import SharedStreamHub

        hub = SharedStreamHub()

        found, missing = await hub.get_existing_batch([1, 2, 3])
        assert found == {}
        assert missing == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_batch_all_found(self):
        from backend.app.api.routes.camera import SharedStreamHub, _SharedStream

        hub = SharedStreamHub()

        for pid in [1, 2]:
            entry = _SharedStream()
            entry.alive = True
            hub._streams[pid] = entry

        found, missing = await hub.get_existing_batch([1, 2])
        assert set(found.keys()) == {1, 2}
        assert missing == []


# ---------------------------------------------------------------------------
# TestGenerateRtspNonFiniteGuard
# ---------------------------------------------------------------------------

class TestGenerateRtspNonFiniteGuard:
    """Tests for the NaN/Inf guard in generate_rtsp_mjpeg_stream."""

    @pytest.mark.asyncio
    async def test_nan_scale(self):
        from backend.app.api.routes.camera import generate_rtsp_mjpeg_stream

        with patch("backend.app.api.routes.camera.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"):
            frames = []
            async for chunk in generate_rtsp_mjpeg_stream(
                "192.168.1.1", "code", "X1C", fps=5, scale=float("nan"),
            ):
                frames.append(chunk)
                break
            assert any(b"invalid parameters" in f for f in frames)

    @pytest.mark.asyncio
    async def test_inf_fps(self):
        from backend.app.api.routes.camera import generate_rtsp_mjpeg_stream

        with patch("backend.app.api.routes.camera.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"):
            frames = []
            async for chunk in generate_rtsp_mjpeg_stream(
                "192.168.1.1", "code", "X1C", fps=float("inf"),
            ):
                frames.append(chunk)
                break
            assert any(b"invalid parameters" in f for f in frames)

    @pytest.mark.asyncio
    async def test_neg_inf_quality(self):
        from backend.app.api.routes.camera import generate_rtsp_mjpeg_stream

        with patch("backend.app.api.routes.camera.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"):
            frames = []
            async for chunk in generate_rtsp_mjpeg_stream(
                "192.168.1.1", "code", "X1C", quality=float("-inf"),
            ):
                frames.append(chunk)
                break
            assert any(b"invalid parameters" in f for f in frames)
