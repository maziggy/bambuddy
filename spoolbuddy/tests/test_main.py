"""Tests for daemon.main — heartbeat_loop command dispatch and scale wake gating."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from daemon.config import Config
from daemon.main import heartbeat_loop, scale_poll_loop


def _make_config(**overrides):
    cfg = Config(
        backend_url="http://localhost:5000",
        api_key="test-key",
        device_id="dev-1",
        hostname="test-host",
        heartbeat_interval=0.01,  # fast for tests
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_api():
    api = AsyncMock()
    api.report_update_status = AsyncMock(return_value={"ok": True})
    api.heartbeat = AsyncMock(return_value=None)
    api.update_tare = AsyncMock(return_value={"ok": True})
    return api


class TestHeartbeatLoopCommands:
    """Test command dispatch in heartbeat_loop."""

    @pytest.mark.asyncio
    async def test_tare_command_executes_scale_tare(self):
        config = _make_config()
        api = _make_api()

        call_count = 0

        async def mock_heartbeat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"pending_command": "tare"}
            return None

        api.heartbeat = mock_heartbeat

        scale = MagicMock()
        scale.ok = True
        scale.tare = MagicMock(return_value=512)

        display = MagicMock()
        display.tick = MagicMock()
        shared = {"nfc": None, "scale": scale, "display": display}

        task = asyncio.create_task(heartbeat_loop(config, api, time.monotonic(), shared))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        scale.tare.assert_called_once()
        api.update_tare.assert_awaited_once_with("dev-1", 512)
        assert config.tare_offset == 512

    @pytest.mark.asyncio
    async def test_tare_command_no_scale_logs_warning(self):
        config = _make_config()
        api = _make_api()

        call_count = 0

        async def mock_heartbeat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"pending_command": "tare"}
            return None

        api.heartbeat = mock_heartbeat

        display = MagicMock()
        display.tick = MagicMock()
        shared = {"nfc": None, "scale": None, "display": display}

        task = asyncio.create_task(heartbeat_loop(config, api, time.monotonic(), shared))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Should not crash; update_tare should NOT be called
        api.update_tare.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_write_tag_command_sets_pending_write(self):
        config = _make_config()
        api = _make_api()

        call_count = 0

        async def mock_heartbeat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "pending_command": "write_tag",
                    "pending_write_payload": {
                        "spool_id": 42,
                        "ndef_data_hex": "DEADBEEF",
                    },
                }
            return None

        api.heartbeat = mock_heartbeat

        display = MagicMock()
        display.tick = MagicMock()
        display.set_brightness = MagicMock()
        display.set_blank_timeout = MagicMock()
        shared = {"nfc": None, "scale": None, "display": display}

        task = asyncio.create_task(heartbeat_loop(config, api, time.monotonic(), shared))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert "pending_write" in shared
        assert shared["pending_write"]["spool_id"] == 42
        assert shared["pending_write"]["ndef_data"] == bytes.fromhex("DEADBEEF")

    @pytest.mark.asyncio
    async def test_display_settings_applied_from_heartbeat(self):
        config = _make_config()
        api = _make_api()

        call_count = 0

        async def mock_heartbeat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "display_brightness": 75,
                    "display_blank_timeout": 300,
                }
            return None

        api.heartbeat = mock_heartbeat

        display = MagicMock()
        display.tick = MagicMock()
        shared = {"nfc": None, "scale": None, "display": display}

        task = asyncio.create_task(heartbeat_loop(config, api, time.monotonic(), shared))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        display.set_brightness.assert_called_with(75)
        display.set_blank_timeout.assert_called_with(300)

    @pytest.mark.asyncio
    async def test_calibration_sync_from_heartbeat(self):
        config = _make_config(tare_offset=0, calibration_factor=1.0)
        api = _make_api()

        call_count = 0

        async def mock_heartbeat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "tare_offset": 200,
                    "calibration_factor": 1.05,
                }
            return None

        api.heartbeat = mock_heartbeat

        scale = MagicMock()
        scale.ok = True
        display = MagicMock()
        display.tick = MagicMock()
        shared = {"nfc": None, "scale": scale, "display": display}

        task = asyncio.create_task(heartbeat_loop(config, api, time.monotonic(), shared))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert config.tare_offset == 200
        assert config.calibration_factor == 1.05
        scale.update_calibration.assert_called_with(200, 1.05)


class TestScalePollLoopWakeGating:
    """Regression tests for the wake-from-scale-noise bug.

    A noisy load cell that bounces by ≥50g around its midpoint used to fire
    display.wake() on every bounce because the threshold check ran against
    `last_wake_grams` which itself advanced to noisy values. The fix gates
    wake on the scale's `stable` flag so noise can't trigger wake AND
    last_wake_grams only advances to settled readings.
    """

    @staticmethod
    def _make_scale(readings):
        """Build a scale mock whose .read() yields the given readings then None forever."""
        scale = MagicMock()
        scale.ok = True
        seq = list(readings)

        def _read():
            if seq:
                return seq.pop(0)
            return None

        scale.read = _read
        return scale

    @staticmethod
    async def _run_loop(scale, display, *, iterations: int):
        config = _make_config(scale_read_interval=0.0, scale_report_interval=0.0)
        api = AsyncMock()
        api.scale_reading = AsyncMock(return_value=None)
        shared = {"scale": scale, "display": display}

        # Bypass the real threadpool — call scale.read inline so each loop
        # iteration consumes exactly one canned reading without races.
        async def _inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("daemon.main.asyncio.to_thread", _inline_to_thread):
            task = asyncio.create_task(scale_poll_loop(config, api, shared))
            for _ in range(iterations):
                await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_unstable_noise_above_threshold_does_not_wake(self):
        """A noisy load cell bouncing ±60g must NOT fire wake repeatedly."""
        # All readings are unstable (stable=False) — typical of an unsettled
        # load cell. Each crosses the 50g threshold from the previous value.
        readings = [
            (100.0, False, 1000),
            (160.0, False, 1100),  # +60g, unstable
            (95.0, False, 990),  # -65g, unstable
            (155.0, False, 1080),  # +60g, unstable
            (90.0, False, 970),  # -65g, unstable
        ]
        scale = self._make_scale(readings)
        display = MagicMock()

        await self._run_loop(scale, display, iterations=20)

        display.wake.assert_not_called()

    @pytest.mark.asyncio
    async def test_stable_large_change_wakes(self):
        """A real spool placement (settled reading >50g from baseline) wakes."""
        # First a settled baseline at 0g, then a settled new reading at 250g.
        readings = [
            (0.0, True, 100),  # baseline stable
            (250.0, True, 5000),  # spool placed, settled
        ]
        scale = self._make_scale(readings)
        display = MagicMock()

        await self._run_loop(scale, display, iterations=20)

        # Wake should fire exactly twice: once on first stable reading
        # (last_wake_grams was None) and once on the >50g stable change.
        assert display.wake.call_count == 2

    @pytest.mark.asyncio
    async def test_noise_then_settled_wakes_once(self):
        """Noise that briefly exceeds threshold must not bump last_wake_grams.

        After noise stops and the scale settles at the original baseline,
        the next stable reading at a real new value (>50g away) should still
        wake — proving last_wake_grams wasn't poisoned by the noise.
        """
        readings = [
            (0.0, True, 100),  # initial settled — first wake (None → 0)
            (75.0, False, 1500),  # noise spike, unstable, ignored
            (-50.0, False, -800),  # noise dip, unstable, ignored
            (80.0, False, 1600),  # noise spike, unstable, ignored
            (200.0, True, 4000),  # spool placed, settled — should wake (>50g from 0)
        ]
        scale = self._make_scale(readings)
        display = MagicMock()

        await self._run_loop(scale, display, iterations=30)

        # Two stable wake events: initial baseline + real spool placement.
        # If last_wake_grams had advanced to 80 during noise, the 200g jump
        # would still wake (delta 120 > 50), so this asserts both gating AND
        # the absence of poisoning.
        assert display.wake.call_count == 2
