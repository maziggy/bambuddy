"""Tests for daemon.main — heartbeat_loop command dispatch and scale wake gating."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from daemon.config import Config
from daemon.main import heartbeat_loop, nfc_poll_loop, scale_poll_loop


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
    api.has_buffered = MagicMock(return_value=False)
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
        # An older Bambuddy sends no v2 encoding: v1 only.
        assert shared["pending_write"]["ndef_v2"] is None

    @pytest.mark.asyncio
    async def test_write_tag_command_keeps_the_v2_encoding(self):
        # #3500: Bambuddy sends OpenTag3D v2 beside v1 for a tag with room for it.
        config = _make_config()
        api = _make_api()
        sent = False

        async def mock_heartbeat(*args, **kwargs):
            nonlocal sent
            if sent:
                return None
            sent = True
            return {
                "pending_command": "write_tag",
                "pending_write_payload": {"spool_id": 42, "ndef_data_hex": "DEADBEEF", "ndef_v2_hex": "CAFE"},
            }

        api.heartbeat = mock_heartbeat
        shared = {"nfc": None, "scale": None, "display": MagicMock()}

        task = asyncio.create_task(heartbeat_loop(config, api, time.monotonic(), shared))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert shared["pending_write"]["ndef_v2"] == bytes.fromhex("CAFE")

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


class TestNfcPollLoopWrite:
    @pytest.mark.asyncio
    async def test_the_v2_encoding_reaches_the_write(self):
        # #3500: the reader picks v1 or v2 by the tag's size, so it needs both.
        from daemon.nfc_reader import NFCState

        nfc = MagicMock()
        nfc.ok = True
        nfc.poll = lambda: ("none", None)
        nfc.state = NFCState.TAG_PRESENT
        nfc.current_sak = 0x00
        nfc.current_uid = "04A1B2C3"
        nfc.write_ntag = MagicMock(return_value=(True, "Write successful (OpenTag3D v2)"))
        api = AsyncMock()
        api.has_buffered = MagicMock(return_value=False)
        config = _make_config(nfc_poll_interval=0.0)
        shared = {
            "nfc": nfc,
            "display": MagicMock(),
            "pending_write": {"spool_id": 7, "ndef_data": b"v1", "ndef_v2": b"v2"},
        }

        async def _inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("daemon.main.asyncio.to_thread", _inline_to_thread):
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

        nfc.write_ntag.assert_called_once_with(b"v1", b"v2")


class TestNoSecondWrite:
    """#3076: the backend keeps the write command until it has the result, so a
    result stuck in the buffer brought the same write back with the next
    heartbeat, and it ran again on whatever tag was on the reader."""

    async def _write_once(self, buffered: bool) -> dict:
        from daemon.nfc_reader import NFCState

        nfc = MagicMock()
        nfc.ok = True
        nfc.poll = lambda: ("none", None)
        nfc.state = NFCState.TAG_PRESENT
        nfc.current_sak = 0x00
        nfc.current_uid = "04A1B2C3"
        nfc.write_ntag = MagicMock(return_value=(True, "Write successful (OpenTag3D v1)"))
        api = AsyncMock()
        api.has_buffered = MagicMock(return_value=buffered)
        shared = {
            "nfc": nfc,
            "display": MagicMock(),
            "pending_write": {"spool_id": 7, "ndef_data": b"v1", "ndef_v2": None},
        }

        async def _inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("daemon.main.asyncio.to_thread", _inline_to_thread):
            task = asyncio.create_task(nfc_poll_loop(_make_config(nfc_poll_interval=0.0), api, shared))
            for _ in range(20):
                await asyncio.sleep(0)
                if "pending_write" not in shared:
                    break
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return shared

    @pytest.mark.asyncio
    async def test_an_unsent_result_is_remembered(self):
        shared = await self._write_once(buffered=True)
        assert shared["write_result_unsent"] == 7

    @pytest.mark.asyncio
    async def test_a_delivered_result_leaves_nothing_behind(self):
        shared = await self._write_once(buffered=False)
        assert "write_result_unsent" not in shared

    async def _heartbeat(self, shared: dict, spool_id: int, still_buffered: bool) -> None:
        api = _make_api()
        api.has_buffered = MagicMock(return_value=still_buffered)
        sent = False

        async def mock_heartbeat(*args, **kwargs):
            nonlocal sent
            if sent:
                return None
            sent = True
            return {
                "pending_command": "write_tag",
                "pending_write_payload": {"spool_id": spool_id, "ndef_data_hex": "DEADBEEF"},
            }

        api.heartbeat = mock_heartbeat
        task = asyncio.create_task(heartbeat_loop(_make_config(), api, time.monotonic(), shared))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_the_same_write_is_not_armed_while_its_result_is_unsent(self):
        shared = {"nfc": None, "scale": None, "display": MagicMock(), "write_result_unsent": 7}

        await self._heartbeat(shared, spool_id=7, still_buffered=True)

        assert "pending_write" not in shared
        assert shared["write_result_unsent"] == 7

    @pytest.mark.asyncio
    async def test_a_result_sent_during_the_heartbeat_still_skips_its_stale_command(self):
        # The heartbeat was answered before the buffer went out, so its
        # command is the old one; the next heartbeat no longer carries it.
        shared = {"nfc": None, "scale": None, "display": MagicMock(), "write_result_unsent": 7}

        await self._heartbeat(shared, spool_id=7, still_buffered=False)

        assert "pending_write" not in shared
        assert "write_result_unsent" not in shared

    @pytest.mark.asyncio
    async def test_a_write_for_another_spool_is_armed(self):
        shared = {"nfc": None, "scale": None, "display": MagicMock(), "write_result_unsent": 7}

        await self._heartbeat(shared, spool_id=8, still_buffered=True)

        assert shared["pending_write"]["spool_id"] == 8
