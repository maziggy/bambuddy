"""Tests for daemon.main — heartbeat_loop command dispatch and scale wake gating."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from daemon.config import Config
from daemon.main import heartbeat_loop, nfc_poll_loop, scale_poll_loop
from daemon.nfc_reader import NFCState


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
    async def test_heartbeat_without_write_tag_drops_armed_write(self):
        """A heartbeat that no longer carries write_tag cancels the armed copy.

        The backend drops pending_command when the write is cancelled or another
        command replaces it, but it keeps sending the same response until then.
        The daemon re-reads it every heartbeat, so the absence of write_tag is
        the only signal that the request is gone (#3503).
        """
        config = _make_config()
        api = _make_api()

        call_count = 0

        async def mock_heartbeat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "pending_command": "write_tag",
                    "pending_write_payload": {"spool_id": 42, "ndef_data_hex": "DEADBEEF"},
                }
            return {"pending_command": None}

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

        assert call_count >= 2
        assert "pending_write" not in shared

    @pytest.mark.asyncio
    async def test_failed_heartbeat_keeps_armed_write(self):
        """A heartbeat that never reached the backend must not cancel the write.

        api.heartbeat returns None both for a network failure and for a buffered
        retry, so None says nothing about whether the request still stands.
        """
        config = _make_config()
        api = _make_api()

        call_count = 0

        async def mock_heartbeat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "pending_command": "write_tag",
                    "pending_write_payload": {"spool_id": 42, "ndef_data_hex": "DEADBEEF"},
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

        assert call_count >= 2
        assert shared["pending_write"]["spool_id"] == 42

    @pytest.mark.asyncio
    async def test_a_second_write_starts_its_own_deadline(self):
        """A write queued while another is armed gets the full wait (#3503).

        Cancel-and-requeue can happen inside one heartbeat interval, so the
        daemon never sees a heartbeat without write_tag. Keying the deadline on
        "is something already armed" let the new write inherit the old one's
        start and expire almost immediately.
        """
        config = _make_config()
        api = _make_api()

        call_count = 0

        async def mock_heartbeat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "pending_command": "write_tag",
                    "pending_write_payload": {"spool_id": 42, "ndef_data_hex": "DEADBEEF"},
                }
            return {
                "pending_command": "write_tag",
                "pending_write_payload": {"spool_id": 43, "ndef_data_hex": "CAFEBABE"},
            }

        api.heartbeat = mock_heartbeat

        display = MagicMock()
        display.tick = MagicMock()
        # The previous write has been waiting long enough to expire.
        shared = {
            "nfc": None,
            "scale": None,
            "display": display,
            "pending_write": {"spool_id": 42, "ndef_data": b"\xde\xad\xbe\xef"},
            "pending_write_armed_at": time.monotonic() - 600.0,
        }

        task = asyncio.create_task(heartbeat_loop(config, api, time.monotonic(), shared))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert shared["pending_write"]["spool_id"] == 43
        assert time.monotonic() - shared["pending_write_armed_at"] < 60.0

    @pytest.mark.asyncio
    async def test_requeueing_the_same_spool_starts_a_new_deadline(self):
        """Re-queuing an identical write still gets the full wait (#3503).

        The payload is derived from the spool, so a cancel and re-queue of the
        same spool produces byte-identical content. Comparing what is armed
        cannot tell the two requests apart; the backend's queued_at can.
        """
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
                        "queued_at": "2026-11-11T00:00:00+00:00",
                    },
                }
            return {
                "pending_command": "write_tag",
                "pending_write_payload": {
                    "spool_id": 42,
                    "ndef_data_hex": "DEADBEEF",
                    "queued_at": "2026-11-11T00:05:00+00:00",
                },
            }

        api.heartbeat = mock_heartbeat

        display = MagicMock()
        display.tick = MagicMock()
        shared = {
            "nfc": None,
            "scale": None,
            "display": display,
            "pending_write": {"spool_id": 42, "ndef_data": bytes.fromhex("DEADBEEF")},
            "pending_write_queued_at": "2026-11-11T00:00:00+00:00",
            "pending_write_armed_at": time.monotonic() - 600.0,
        }

        task = asyncio.create_task(heartbeat_loop(config, api, time.monotonic(), shared))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert shared["pending_write_queued_at"] == "2026-11-11T00:05:00+00:00"
        assert time.monotonic() - shared["pending_write_armed_at"] < 60.0

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


class TestReRegistration:
    """An admin removed the device while the daemon kept running: the next
    heartbeat is refused, and the daemon registers again (#sb-1)."""

    @pytest.mark.asyncio
    async def test_a_refused_heartbeat_registers_the_device_again(self):
        config = _make_config()
        api = _make_api()
        api.unregistered = False
        api.register_device = AsyncMock(return_value={"tare_offset": 5, "calibration_factor": 2.0})

        async def mock_heartbeat(*args, **kwargs):
            api.unregistered = True
            return None

        api.heartbeat = mock_heartbeat
        display = MagicMock()
        display.has_backlight = False
        shared = {"nfc": None, "scale": None, "display": display}

        task = asyncio.create_task(heartbeat_loop(config, api, time.monotonic(), shared))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        api.register_device.assert_awaited()
        assert api.register_device.await_args.kwargs["device_id"] == "dev-1"
        assert config.tare_offset == 5


class TestNfcPollLoopTagScanned:
    """nfc_poll_loop forwards everything a tag_detected event carries to the backend (#2685)."""

    @pytest.mark.asyncio
    async def test_raw_blocks_reach_tag_scanned(self):
        raw_blocks = {"1": "41" * 16, "9": "36B6C12DB3DA4A39A50C275F510BD399"}
        events = [
            (
                "tag_detected",
                {
                    "tag_uid": "865947E2",
                    "sak": 0x08,
                    "tag_type": "mifare_classic",
                    "tray_uuid": "36B6C12DB3DA4A39A50C275F510BD399",
                    "raw_blocks": raw_blocks,
                },
            )
        ]
        nfc = MagicMock()
        nfc.ok = True
        nfc.poll = lambda: events.pop(0) if events else ("none", None)
        api = AsyncMock()
        config = _make_config(nfc_poll_interval=0.0)
        shared = {"nfc": nfc, "display": MagicMock(), "pending_write": None}

        async def _inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("daemon.main.asyncio.to_thread", _inline_to_thread):
            task = asyncio.create_task(nfc_poll_loop(config, api, shared))
            for _ in range(20):
                await asyncio.sleep(0)
                if api.tag_scanned.await_count:
                    break
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        api.tag_scanned.assert_awaited_once_with(
            device_id="dev-1",
            tag_uid="865947E2",
            tray_uuid="36B6C12DB3DA4A39A50C275F510BD399",
            sak=0x08,
            tag_type="mifare_classic",
            raw_blocks=raw_blocks,
        )


class TestArmedWriteExpiry:
    """An armed write that never gets a tag ends instead of waiting forever (#3503).

    The user can walk away with the write still queued; nothing on either side
    ends it. The daemon gives up after a fixed wait and tells the backend, so
    the Write page stops showing "Waiting for SpoolBuddy".
    """

    @pytest.mark.asyncio
    async def test_armed_write_expires_when_no_tag_arrives(self):
        config = _make_config(nfc_poll_interval=0.0)
        nfc = MagicMock()
        nfc.ok = True
        nfc.state = NFCState.IDLE
        nfc.poll = lambda: ("none", None)
        api = AsyncMock()
        api.write_tag_result = AsyncMock(return_value=None)
        shared = {
            "nfc": nfc,
            "display": MagicMock(),
            "pending_write": {"spool_id": 42, "ndef_data": b"\xde\xad"},
            "pending_write_armed_at": time.monotonic() - 1.0,
        }

        async def _inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with (
            patch("daemon.main.asyncio.to_thread", _inline_to_thread),
            patch("daemon.main.WRITE_ARM_TIMEOUT_SECONDS", 0.05),
        ):
            task = asyncio.create_task(nfc_poll_loop(config, api, shared))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        api.write_tag_result.assert_awaited_once()
        assert api.write_tag_result.await_args.kwargs["success"] is False
        assert api.write_tag_result.await_args.kwargs["spool_id"] == 42
        assert "pending_write" not in shared

    @pytest.mark.asyncio
    async def test_a_tag_presented_before_the_deadline_is_still_written(self):
        config = _make_config(nfc_poll_interval=0.0)
        nfc = MagicMock()
        nfc.ok = True
        nfc.state = NFCState.TAG_PRESENT
        nfc.current_sak = 0x00
        nfc.current_uid = "04AABB"
        nfc.poll = lambda: ("none", None)
        nfc.write_ntag = MagicMock(return_value=(True, "ok"))
        api = AsyncMock()
        api.write_tag_result = AsyncMock(return_value=None)
        shared = {
            "nfc": nfc,
            "display": MagicMock(),
            "pending_write": {"spool_id": 42, "ndef_data": b"\xde\xad"},
            "pending_write_armed_at": time.monotonic(),
        }

        async def _inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with (
            patch("daemon.main.asyncio.to_thread", _inline_to_thread),
            patch("daemon.main.WRITE_ARM_TIMEOUT_SECONDS", 60.0),
        ):
            task = asyncio.create_task(nfc_poll_loop(config, api, shared))
            for _ in range(20):
                await asyncio.sleep(0)
                if api.write_tag_result.await_count:
                    break
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        api.write_tag_result.assert_awaited_once()
        assert api.write_tag_result.await_args.kwargs["success"] is True
        assert "pending_write" not in shared
