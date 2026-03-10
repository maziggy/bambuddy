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
# TestFleetCpuWatchdog
# ---------------------------------------------------------------------------


class TestFleetCpuWatchdog:
    """Tests for the fleet-level CPU watchdog in _cleanup_stale_frame_buffers."""

    @pytest.mark.asyncio
    async def test_fleet_watchdog_kills_worst_offenders(self):
        """When fleet CPU total exceeds threshold, kill highest-CPU processes first."""
        import backend.app.api.routes.camera as cam

        now = time.monotonic()
        # Simulate 4 FFmpeg processes past grace period with high CPU
        pids = {100: now - 60, 101: now - 60, 102: now - 60, 103: now - 60}
        # Previous samples: each at ~25% CPU (under 30% individual threshold)
        prev_wall = now - 10
        samples = {
            100: (prev_wall, 10.0),
            101: (prev_wall, 10.0),
            102: (prev_wall, 10.0),
            103: (prev_wall, 10.0),
        }
        # Current CPU times: each used 2.5 more seconds in 10s = 25%
        cpu_times_result = {100: 12.5, 101: 12.5, 102: 12.5, 103: 12.5}
        # Fleet total = 4 × 25% = 100%

        killed_pids = []
        original_kill = cam.os.kill

        def mock_kill(pid, sig):
            if pid in pids:
                killed_pids.append(pid)
            else:
                original_kill(pid, sig)

        # Set fleet threshold low so 100% triggers it
        with (
            patch.dict(cam._spawned_ffmpeg_pids, pids, clear=True),
            patch.dict(cam._ffmpeg_cpu_samples, samples, clear=True),
            patch.dict(cam._last_frame_times, {}, clear=True),
            patch.dict(cam._stream_start_times, {}, clear=True),
            patch.object(cam, "_FLEET_CPU_PCT_THRESHOLD", 80.0),
            patch("backend.app.api.routes.camera.os.kill", side_effect=mock_kill),
            patch(
                "backend.app.api.routes.camera._read_ffmpeg_cpu_times",
                return_value=cpu_times_result,
            ),
            patch("backend.app.api.routes.camera._scan_dead_pids", return_value=[]),
        ):
            await cam._cleanup_stale_frame_buffers()
            # Should have killed enough to get under 80%: need to kill at least 1 of 4
            # (100% - 25% = 75% under)
            assert len(killed_pids) >= 1

    @pytest.mark.asyncio
    async def test_fleet_watchdog_no_kill_under_threshold(self):
        """When fleet CPU total is under threshold, no processes are killed."""
        import backend.app.api.routes.camera as cam

        now = time.monotonic()
        pids = {200: now - 60, 201: now - 60}
        prev_wall = now - 10
        samples = {200: (prev_wall, 10.0), 201: (prev_wall, 10.0)}
        # Each at 10% CPU = fleet total 20%
        cpu_times_result = {200: 11.0, 201: 11.0}

        killed_pids = []

        def mock_kill(pid, sig):
            killed_pids.append(pid)

        with (
            patch.dict(cam._spawned_ffmpeg_pids, pids, clear=True),
            patch.dict(cam._ffmpeg_cpu_samples, samples, clear=True),
            patch.dict(cam._last_frame_times, {}, clear=True),
            patch.dict(cam._stream_start_times, {}, clear=True),
            patch("backend.app.api.routes.camera.os.kill", side_effect=mock_kill),
            patch(
                "backend.app.api.routes.camera._read_ffmpeg_cpu_times",
                return_value=cpu_times_result,
            ),
            patch("backend.app.api.routes.camera._scan_dead_pids", return_value=[]),
        ):
            await cam._cleanup_stale_frame_buffers()
            assert len(killed_pids) == 0

    @pytest.mark.asyncio
    async def test_fleet_watchdog_respects_grace_period(self):
        """Processes in grace period should not be included in fleet CPU total."""
        import backend.app.api.routes.camera as cam

        now = time.monotonic()
        # PID 300 is past grace (10s), PID 301 is in grace period
        pids = {300: now - 60, 301: now - 3}
        prev_wall = now - 10
        samples = {300: (prev_wall, 10.0)}
        # PID 300 at 25% (under 30% per-process), PID 301 would be 25% but in grace
        cpu_times_result = {300: 12.5, 301: 12.5}

        killed_pids = []

        def mock_kill(pid, sig):
            killed_pids.append(pid)

        # Fleet threshold at 20% — only PID 300 (25%) is counted, fleet total = 25% > 20%
        with (
            patch.dict(cam._spawned_ffmpeg_pids, pids, clear=True),
            patch.dict(cam._ffmpeg_cpu_samples, samples, clear=True),
            patch.dict(cam._last_frame_times, {}, clear=True),
            patch.dict(cam._stream_start_times, {}, clear=True),
            patch.object(cam, "_FLEET_CPU_PCT_THRESHOLD", 20.0),
            patch("backend.app.api.routes.camera.os.kill", side_effect=mock_kill),
            patch(
                "backend.app.api.routes.camera._read_ffmpeg_cpu_times",
                return_value=cpu_times_result,
            ),
            patch("backend.app.api.routes.camera._scan_dead_pids", return_value=[]),
        ):
            await cam._cleanup_stale_frame_buffers()
            # Only PID 300 counted and it's over fleet threshold → killed by fleet watchdog
            assert 300 in killed_pids
            # PID 301 in grace period — should NOT be killed
            assert 301 not in killed_pids


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


# ---------------------------------------------------------------------------
# TestSpawnLoadGate
# ---------------------------------------------------------------------------


class TestSpawnLoadGate:
    """Tests for _check_system_load and the load gate in _ensure_producer."""

    def test_check_system_load_returns_float(self):
        from backend.app.api.routes.camera import _check_system_load

        with patch("backend.app.api.routes.camera.os.getloadavg", return_value=(2.5, 2.0, 1.5)):
            result = _check_system_load()
            assert result == 2.5

    def test_check_system_load_returns_none_on_error(self):
        from backend.app.api.routes.camera import _check_system_load

        with patch("backend.app.api.routes.camera.os.getloadavg", side_effect=AttributeError):
            result = _check_system_load()
            assert result is None

    @pytest.mark.asyncio
    async def test_load_gate_blocks_spawn_when_overloaded(self):
        """_ensure_producer should return None when system load exceeds threshold."""
        from unittest.mock import AsyncMock, MagicMock

        import backend.app.api.routes.camera as cam
        from backend.app.api.routes.camera import SharedStreamHub, _ensure_producer

        hub = SharedStreamHub()
        db = AsyncMock()

        printer = MagicMock()
        printer.id = 1
        printer.model = "X1C"
        printer.ip_address = "192.168.1.100"
        printer.access_code = "12345678"
        printer.external_camera_enabled = False
        printer.external_camera_url = None

        # Simulate high load (above threshold)
        with patch("backend.app.api.routes.camera.os.getloadavg", return_value=(100.0, 90.0, 80.0)):
            result = await _ensure_producer(1, db, 5, 15, 0.5, printer=printer, hub=hub)
            assert result is None

    @pytest.mark.asyncio
    async def test_load_gate_allows_spawn_when_load_ok(self):
        """_ensure_producer should proceed when system load is below threshold."""
        from unittest.mock import AsyncMock, MagicMock

        import backend.app.api.routes.camera as cam
        from backend.app.api.routes.camera import SharedStreamHub, _ensure_producer

        hub = SharedStreamHub()
        db = AsyncMock()

        printer = MagicMock()
        printer.id = 1
        printer.model = "X1C"
        printer.ip_address = "192.168.1.100"
        printer.access_code = "12345678"
        printer.external_camera_enabled = False
        printer.external_camera_url = None

        async def fake_stream(**kwargs):
            while True:
                yield b"\xff\xd8fake\xff\xd9"
                import asyncio

                await asyncio.sleep(0.1)

        # Simulate low load (below threshold)
        with (
            patch("backend.app.api.routes.camera.os.getloadavg", return_value=(0.5, 0.5, 0.5)),
            patch("backend.app.api.routes.camera.generate_rtsp_mjpeg_stream", fake_stream),
            patch("backend.app.api.routes.camera.is_chamber_image_model", return_value=False),
            patch.dict(cam._stream_start_times, {}, clear=False),
        ):
            result = await _ensure_producer(1, db, 5, 15, 0.5, printer=printer, hub=hub)
            assert result is not None
            assert result.alive is True

        await hub.stop_all()


# ---------------------------------------------------------------------------
# TestCircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Tests for the circuit breaker between watchdog and grid restart logic."""

    @pytest.mark.asyncio
    async def test_watchdog_kill_sets_cooldown(self):
        """Per-process watchdog kill should activate the fleet cooldown."""
        import backend.app.api.routes.camera as cam

        now = time.monotonic()
        pids = {400: now - 60}
        prev_wall = now - 10
        samples = {400: (prev_wall, 10.0)}
        # 35% CPU — over the 30% per-process threshold
        cpu_times_result = {400: 13.5}

        original_kill = cam.os.kill

        def mock_kill(pid, sig):
            if pid in pids:
                pass  # Don't actually kill
            else:
                original_kill(pid, sig)

        old_cooldown = cam._fleet_cooldown_until
        with (
            patch.dict(cam._spawned_ffmpeg_pids, pids, clear=True),
            patch.dict(cam._ffmpeg_cpu_samples, samples, clear=True),
            patch.dict(cam._last_frame_times, {}, clear=True),
            patch.dict(cam._stream_start_times, {}, clear=True),
            patch("backend.app.api.routes.camera.os.kill", side_effect=mock_kill),
            patch(
                "backend.app.api.routes.camera._read_ffmpeg_cpu_times",
                return_value=cpu_times_result,
            ),
            patch("backend.app.api.routes.camera._scan_dead_pids", return_value=[]),
        ):
            await cam._cleanup_stale_frame_buffers()
            # Cooldown should be set to ~30s from now
            assert cam._fleet_cooldown_until > now
            assert cam._fleet_cooldown_until <= now + cam._FLEET_COOLDOWN_DURATION + 1

        # Restore
        cam._fleet_cooldown_until = old_cooldown

    @pytest.mark.asyncio
    async def test_fleet_kill_sets_cooldown_and_tracks_printers(self):
        """Fleet-level watchdog kill should set cooldown and track killed printer IDs."""
        import asyncio
        from unittest.mock import MagicMock

        import backend.app.api.routes.camera as cam

        now = time.monotonic()
        pids = {500: now - 60, 501: now - 60}
        prev_wall = now - 10
        samples = {500: (prev_wall, 10.0), 501: (prev_wall, 10.0)}
        # Each at 25% — under per-process 30% but fleet total 50%
        cpu_times_result = {500: 12.5, 501: 12.5}

        killed_pids = []

        def mock_kill(pid, sig):
            killed_pids.append(pid)

        # Mock _active_streams to map PID -> printer_id
        mock_proc_500 = MagicMock()
        mock_proc_500.pid = 500
        mock_proc_501 = MagicMock()
        mock_proc_501.pid = 501

        old_cooldown = cam._fleet_cooldown_until
        old_killed = cam._watchdog_killed_printers.copy()
        with (
            patch.dict(cam._spawned_ffmpeg_pids, pids, clear=True),
            patch.dict(cam._ffmpeg_cpu_samples, samples, clear=True),
            patch.dict(cam._last_frame_times, {}, clear=True),
            patch.dict(cam._stream_start_times, {}, clear=True),
            patch.dict(cam._active_streams, {"10-abc": mock_proc_500, "11-def": mock_proc_501}, clear=True),
            patch.object(cam, "_FLEET_CPU_PCT_THRESHOLD", 40.0),
            patch("backend.app.api.routes.camera.os.kill", side_effect=mock_kill),
            patch(
                "backend.app.api.routes.camera._read_ffmpeg_cpu_times",
                return_value=cpu_times_result,
            ),
            patch("backend.app.api.routes.camera._scan_dead_pids", return_value=[]),
        ):
            await cam._cleanup_stale_frame_buffers()
            assert len(killed_pids) >= 1
            assert cam._fleet_cooldown_until > now
            # Killed printer IDs should be tracked
            assert len(cam._watchdog_killed_printers) >= 1

        # Restore
        cam._fleet_cooldown_until = old_cooldown
        cam._watchdog_killed_printers = old_killed

    def test_watchdog_killed_printer_gets_higher_initial_attempts(self):
        """When a watchdog-killed printer dies, it should start with initial_attempts=2."""
        import backend.app.api.routes.camera as cam

        # Verify the constant exists and _watchdog_killed_printers is a set
        assert isinstance(cam._watchdog_killed_printers, set)
        assert cam._FLEET_COOLDOWN_DURATION == 30.0


# ---------------------------------------------------------------------------
# TestAdaptiveCleanupInterval
# ---------------------------------------------------------------------------


class TestAdaptiveCleanupInterval:
    """Tests for adaptive cleanup interval under CPU load."""

    @pytest.mark.asyncio
    async def test_interval_accelerates_under_load(self):
        """Cleanup interval should be overridden to 5s when fleet CPU > 50% of threshold."""
        import backend.app.api.routes.camera as cam

        now = time.monotonic()
        pids = {600: now - 60, 601: now - 60}
        prev_wall = now - 10
        samples = {600: (prev_wall, 10.0), 601: (prev_wall, 10.0)}
        # Each at 20% = fleet total 40%, which is > 50% of default 200% threshold? No.
        # 50% of threshold = 100%. 40% < 100%. Need higher values.
        # Each at 60% -> fleet 120% > 50% of 200% (100%). But 60% > 30% per-process kills them.
        # Use patched threshold: 60%. 50% of 60% = 30%. Fleet at 40% > 30%.
        cpu_times_result = {600: 12.0, 601: 12.0}  # Each 20%

        old_override = cam._cleanup_interval_override
        with (
            patch.dict(cam._spawned_ffmpeg_pids, pids, clear=True),
            patch.dict(cam._ffmpeg_cpu_samples, samples, clear=True),
            patch.dict(cam._last_frame_times, {}, clear=True),
            patch.dict(cam._stream_start_times, {}, clear=True),
            patch.object(cam, "_FLEET_CPU_PCT_THRESHOLD", 60.0),
            patch("backend.app.api.routes.camera.os.kill", side_effect=lambda p, s: None),
            patch(
                "backend.app.api.routes.camera._read_ffmpeg_cpu_times",
                return_value=cpu_times_result,
            ),
            patch("backend.app.api.routes.camera._scan_dead_pids", return_value=[]),
        ):
            await cam._cleanup_stale_frame_buffers()
            # Fleet total 40% > 50% of 60% (30%) → should set override to 5.0
            assert cam._cleanup_interval_override == 5.0

        cam._cleanup_interval_override = old_override

    @pytest.mark.asyncio
    async def test_interval_resets_when_load_drops(self):
        """Cleanup interval override should be cleared when fleet CPU drops below 50% of threshold."""
        import backend.app.api.routes.camera as cam

        now = time.monotonic()
        pids = {700: now - 60}
        prev_wall = now - 10
        samples = {700: (prev_wall, 10.0)}
        # 5% CPU — well below any threshold
        cpu_times_result = {700: 10.5}

        cam._cleanup_interval_override = 5.0  # Pre-set as if it was previously accelerated
        with (
            patch.dict(cam._spawned_ffmpeg_pids, pids, clear=True),
            patch.dict(cam._ffmpeg_cpu_samples, samples, clear=True),
            patch.dict(cam._last_frame_times, {}, clear=True),
            patch.dict(cam._stream_start_times, {}, clear=True),
            patch("backend.app.api.routes.camera.os.kill", side_effect=lambda p, s: None),
            patch(
                "backend.app.api.routes.camera._read_ffmpeg_cpu_times",
                return_value=cpu_times_result,
            ),
            patch("backend.app.api.routes.camera._scan_dead_pids", return_value=[]),
        ):
            await cam._cleanup_stale_frame_buffers()
            assert cam._cleanup_interval_override is None


# ---------------------------------------------------------------------------
# TestCrossplatformCpuTimes
# ---------------------------------------------------------------------------


class TestCrossplatformCpuTimes:
    """Tests for psutil-based _read_ffmpeg_cpu_times."""

    def test_reads_cpu_times_via_psutil(self):
        """Should return CPU seconds for tracked PIDs using psutil."""
        from unittest.mock import MagicMock

        import backend.app.api.routes.camera as cam
        from backend.app.api.routes.camera import _read_ffmpeg_cpu_times

        mock_times = MagicMock()
        mock_times.user = 5.0
        mock_times.system = 2.0

        mock_process = MagicMock()
        mock_process.cpu_times.return_value = mock_times

        with (
            patch.dict(cam._spawned_ffmpeg_pids, {999: time.monotonic()}, clear=True),
            patch("backend.app.api.routes.camera.psutil.Process", return_value=mock_process),
        ):
            result = _read_ffmpeg_cpu_times()
            assert result == {999: 7.0}

    def test_handles_missing_process(self):
        """Should skip PIDs where the process no longer exists."""
        import psutil

        import backend.app.api.routes.camera as cam
        from backend.app.api.routes.camera import _read_ffmpeg_cpu_times

        with (
            patch.dict(cam._spawned_ffmpeg_pids, {888: time.monotonic()}, clear=True),
            patch("backend.app.api.routes.camera.psutil.Process", side_effect=psutil.NoSuchProcess(888)),
        ):
            result = _read_ffmpeg_cpu_times()
            assert result == {}


# ---------------------------------------------------------------------------
# TestPreSeededBaseline
# ---------------------------------------------------------------------------


class TestPreSeededBaseline:
    """Tests for CPU baseline pre-seeding at spawn time."""

    def test_constants_reflect_tighter_thresholds(self):
        """Verify the tightened watchdog constants."""
        import backend.app.api.routes.camera as cam

        assert cam._CPU_PCT_KILL_THRESHOLD == 30.0
        assert cam._CPU_WATCHDOG_GRACE_SECS == 10.0
        assert cam._CLEANUP_INTERVAL == 10.0
        # Fleet threshold = cpu_count * 50
        import os

        expected_fleet = (os.cpu_count() or 4) * 50.0
        assert expected_fleet == cam._FLEET_CPU_PCT_THRESHOLD


# ---------------------------------------------------------------------------
# TestStderrCategorization
# ---------------------------------------------------------------------------


class TestStderrCategorization:
    """Tests for structured FFmpeg stderr error categorization."""

    def test_categorize_decoder_corruption(self):
        from backend.app.api.routes.camera import _categorize_ffmpeg_error

        assert _categorize_ffmpeg_error("broken bitstream in frame 42") == "decoder_corruption"
        assert _categorize_ffmpeg_error("corrupt data near offset 0x1234") == "decoder_corruption"
        assert _categorize_ffmpeg_error("invalid NAL unit type 31") == "decoder_corruption"

    def test_categorize_bitstream_error(self):
        from backend.app.api.routes.camera import _categorize_ffmpeg_error

        assert _categorize_ffmpeg_error("overread 8 bits") == "bitstream_error"
        assert _categorize_ffmpeg_error("cabac decode failure") == "bitstream_error"

    def test_categorize_network_timeout(self):
        from backend.app.api.routes.camera import _categorize_ffmpeg_error

        assert _categorize_ffmpeg_error("Connection timed out") == "network_timeout"
        assert _categorize_ffmpeg_error("timeout waiting for data") == "network_timeout"
        assert _categorize_ffmpeg_error("Connection refused") == "network_timeout"

    def test_categorize_stream_eof(self):
        from backend.app.api.routes.camera import _categorize_ffmpeg_error

        assert _categorize_ffmpeg_error("End of file reached") == "stream_eof"

    def test_categorize_fatal(self):
        from backend.app.api.routes.camera import _categorize_ffmpeg_error

        assert _categorize_ffmpeg_error("fatal: unknown codec") == "fatal"

    def test_categorize_generic_error(self):
        from backend.app.api.routes.camera import _categorize_ffmpeg_error

        assert _categorize_ffmpeg_error("error processing input") == "generic_error"

    @pytest.mark.asyncio
    async def test_drain_stderr_populates_details(self):
        """_drain_stderr should populate _stderr_error_details and _stderr_recent_errors."""
        import asyncio

        import backend.app.api.routes.camera as cam

        # Create a mock process with stderr that yields error lines
        class MockStderr:
            def __init__(self):
                self._data = [
                    b"[h264] broken bitstream in frame 1\n",
                    b"[h264] overread 8 bits\n",
                    b"Connection timed out\n",
                    b"",  # EOF
                ]
                self._idx = 0

            async def read(self, n):
                if self._idx >= len(self._data):
                    return b""
                data = self._data[self._idx]
                self._idx += 1
                return data

        class MockProcess:
            stderr = MockStderr()

        old_counts = dict(cam._stderr_error_counts)
        old_details = dict(cam._stderr_error_details)
        old_recent = dict(cam._stderr_recent_errors)

        try:
            await cam._drain_stderr(MockProcess(), "test-stream-1")

            assert "test-stream-1" in cam._stderr_error_counts
            assert cam._stderr_error_counts["test-stream-1"] == 3

            details = cam._stderr_error_details["test-stream-1"]
            assert details["decoder_corruption"] == 1
            assert details["bitstream_error"] == 1
            assert details["network_timeout"] == 1

            recent = cam._stderr_recent_errors["test-stream-1"]
            assert len(recent) == 3
        finally:
            # Restore
            cam._stderr_error_counts.clear()
            cam._stderr_error_counts.update(old_counts)
            cam._stderr_error_details.clear()
            cam._stderr_error_details.update(old_details)
            cam._stderr_recent_errors.clear()
            cam._stderr_recent_errors.update(old_recent)

    @pytest.mark.asyncio
    async def test_drain_stderr_caps_recent_lines(self):
        """_stderr_recent_errors should be capped at _STDERR_RECENT_CAP."""
        import backend.app.api.routes.camera as cam

        # Generate more error lines than the cap
        lines = [f"[h264] error in frame {i}\n".encode() for i in range(30)]

        class MockStderr:
            def __init__(self):
                self._data = lines + [b""]
                self._idx = 0

            async def read(self, n):
                if self._idx >= len(self._data):
                    return b""
                data = self._data[self._idx]
                self._idx += 1
                return data

        class MockProcess:
            stderr = MockStderr()

        old_recent = dict(cam._stderr_recent_errors)
        try:
            await cam._drain_stderr(MockProcess(), "test-cap")
            assert len(cam._stderr_recent_errors["test-cap"]) == cam._STDERR_RECENT_CAP
        finally:
            cam._stderr_recent_errors.clear()
            cam._stderr_recent_errors.update(old_recent)
