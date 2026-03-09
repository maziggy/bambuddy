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

    @pytest.mark.asyncio
    async def test_cleanup_removes_stale_entries(self):
        import backend.app.api.routes.camera as cam

        stale_ts = time.monotonic() - cam._FRAME_BUFFER_MAX_AGE - 10
        with (
            patch.dict(cam._last_frame_times, {99: stale_ts}, clear=True),
            patch.dict(cam._stream_start_times, {99: stale_ts}, clear=True),
        ):
            await cam._cleanup_stale_frame_buffers()
            assert 99 not in cam._last_frame_times
            assert 99 not in cam._stream_start_times

    @pytest.mark.asyncio
    async def test_cleanup_preserves_fresh_entries(self):
        import backend.app.api.routes.camera as cam

        fresh_ts = time.monotonic()
        with (
            patch.dict(cam._last_frame_times, {1: fresh_ts}, clear=True),
            patch.dict(cam._stream_start_times, {1: fresh_ts}, clear=True),
        ):
            await cam._cleanup_stale_frame_buffers()
            assert 1 in cam._last_frame_times
            assert 1 in cam._stream_start_times

    @pytest.mark.asyncio
    async def test_cleanup_handles_partial_entries(self):
        """Stale _last_frame_times entry but no matching _stream_start_times."""
        import backend.app.api.routes.camera as cam

        stale_ts = time.monotonic() - cam._FRAME_BUFFER_MAX_AGE - 10
        with (
            patch.dict(cam._last_frame_times, {42: stale_ts}, clear=True),
            patch.dict(cam._stream_start_times, {}, clear=True),
        ):
            # Should not raise
            await cam._cleanup_stale_frame_buffers()
            assert 42 not in cam._last_frame_times

    @pytest.mark.asyncio
    async def test_cleanup_mixed_fresh_and_stale(self):
        import backend.app.api.routes.camera as cam

        now = time.monotonic()
        stale_ts = now - cam._FRAME_BUFFER_MAX_AGE - 10
        fresh_ts = now

        with (
            patch.dict(cam._last_frame_times, {1: stale_ts, 2: fresh_ts}, clear=True),
            patch.dict(cam._stream_start_times, {1: stale_ts, 2: fresh_ts}, clear=True),
        ):
            await cam._cleanup_stale_frame_buffers()
            # Stale removed
            assert 1 not in cam._last_frame_times
            assert 1 not in cam._stream_start_times
            # Fresh preserved
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
                "192.168.1.1",
                "code",
                "X1C",
                fps=5,
                scale=float("nan"),
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
                "192.168.1.1",
                "code",
                "X1C",
                fps=float("inf"),
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
                "192.168.1.1",
                "code",
                "X1C",
                quality=float("-inf"),
            ):
                frames.append(chunk)
                break
            assert any(b"invalid parameters" in f for f in frames)


# ---------------------------------------------------------------------------
# TestEnsureProducerDispatch
# ---------------------------------------------------------------------------


class TestEnsureProducerDispatch:
    """Tests for _ensure_producer() dispatch logic."""

    @pytest.mark.asyncio
    async def test_ensure_producer_external_camera_returns_none(self):
        """External cameras are unsupported in grid mode — should return None."""
        from unittest.mock import AsyncMock, MagicMock

        from backend.app.api.routes.camera import SharedStreamHub, _ensure_producer

        hub = SharedStreamHub()
        db = AsyncMock()

        printer = MagicMock()
        printer.id = 1
        printer.external_camera_enabled = True
        printer.external_camera_url = "http://example.com/stream"

        result = await _ensure_producer(1, db, 5, 15, 0.5, printer=printer, hub=hub)
        assert result is None

    @pytest.mark.asyncio
    async def test_ensure_producer_reuse_does_not_reset_start_time(self):
        """Reusing an existing producer should not reset _stream_start_times (M2)."""
        from unittest.mock import AsyncMock, patch

        import backend.app.api.routes.camera as cam
        from backend.app.api.routes.camera import SharedStreamHub, _ensure_producer, _SharedStream

        hub = SharedStreamHub()
        # Pre-insert an alive producer
        entry = _SharedStream(params_key="5-15-0.5-0-False-False")
        entry.alive = True
        hub._streams[1] = entry

        original_start = time.monotonic() - 100
        with patch.dict(cam._stream_start_times, {1: original_start}, clear=False):
            db = AsyncMock()
            result = await _ensure_producer(1, db, 5, 15, 0.5, hub=hub)
            assert result is entry
            # Start time should NOT have been reset
            assert cam._stream_start_times[1] == original_start

    @pytest.mark.asyncio
    async def test_ensure_producer_force_quality_calls_restart(self):
        """force_quality=True should trigger hub.restart() for param changes."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import backend.app.api.routes.camera as cam
        from backend.app.api.routes.camera import SharedStreamHub, _ensure_producer

        hub = SharedStreamHub()

        # Create a mock printer
        printer = MagicMock()
        printer.id = 1
        printer.model = "X1C"
        printer.ip_address = "192.168.1.100"
        printer.access_code = "12345678"
        printer.external_camera_enabled = False
        printer.external_camera_url = None

        db = AsyncMock()

        # Mock the stream generators to avoid real ffmpeg
        async def fake_stream(**kwargs):
            while True:
                yield b"\xff\xd8fake\xff\xd9"
                import asyncio

                await asyncio.sleep(0.1)

        with (
            patch("backend.app.api.routes.camera.generate_rtsp_mjpeg_stream", fake_stream),
            patch("backend.app.api.routes.camera.is_chamber_image_model", return_value=False),
            patch.dict(cam._stream_start_times, {}, clear=False),
        ):
            # Start initial producer
            entry1 = await _ensure_producer(1, db, 5, 15, 0.5, printer=printer, hub=hub)
            assert entry1 is not None
            assert entry1.alive is True

            # Force restart with different params
            entry2 = await _ensure_producer(1, db, 10, 20, 1.0, printer=printer, force_quality=True, hub=hub)
            assert entry2 is not None
            assert entry2 is not entry1  # Should be a new entry
            assert entry1.alive is False  # Old one should be dead

        await hub.stop_all()


# ---------------------------------------------------------------------------
# TestScanBambuFfmpegPids
# ---------------------------------------------------------------------------


class TestScanBambuFfmpegPids:
    """Tests for _scan_bambu_ffmpeg_pids platform guard (O2)."""

    def test_returns_empty_on_non_linux(self):
        """On macOS/Windows, should return [] without scanning /proc."""
        from backend.app.api.routes.camera import _scan_bambu_ffmpeg_pids

        with patch("backend.app.api.routes.camera.sys") as mock_sys:
            mock_sys.platform = "darwin"
            result = _scan_bambu_ffmpeg_pids()
            assert result == []
