"""Tests for daemon.main — _perform_update() and heartbeat_loop command dispatch."""

import asyncio
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from daemon.config import Config
from daemon.main import _perform_update, heartbeat_loop


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


def _mock_process(returncode=0, stdout=b"", stderr=b""):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    return proc


class TestPerformUpdate:
    @pytest.mark.asyncio
    async def test_successful_update(self):
        config = _make_config()
        api = _make_api()

        proc_ok = _mock_process(returncode=0)

        with (
            patch("daemon.main.asyncio.create_subprocess_exec", return_value=proc_ok),
            patch("daemon.main.shutil.which", return_value="/usr/bin/git"),
            patch("daemon.main.Path") as mock_path_cls,
            pytest.raises(SystemExit) as exc_info,
        ):
            # Make venv pip not exist so it uses sys.executable path
            mock_path_inst = MagicMock()
            mock_path_cls.return_value.resolve.return_value.parent.parent.parent = mock_path_inst
            mock_path_inst.__truediv__ = MagicMock(
                side_effect=lambda x: MagicMock(
                    exists=MagicMock(return_value=False),
                    __truediv__=MagicMock(return_value=MagicMock(exists=MagicMock(return_value=False))),
                    __str__=MagicMock(return_value="/fake/repo"),
                )
            )
            mock_path_inst.__str__ = MagicMock(return_value="/fake/repo")

            await _perform_update(config, api)

        assert exc_info.value.code == 0

        # Should have reported status multiple times
        assert api.report_update_status.await_count >= 3
        # Last call should be "complete"
        last_call = api.report_update_status.call_args_list[-1]
        assert last_call[0][1] == "complete"

    @pytest.mark.asyncio
    async def test_git_fetch_failure(self):
        config = _make_config()
        api = _make_api()

        proc_fail = _mock_process(returncode=1, stderr=b"fatal: cannot fetch")

        with (
            patch("daemon.main.asyncio.create_subprocess_exec", return_value=proc_fail),
            patch("daemon.main.shutil.which", return_value="/usr/bin/git"),
            patch("daemon.main.Path") as mock_path_cls,
        ):
            mock_path_inst = MagicMock()
            mock_path_cls.return_value.resolve.return_value.parent.parent.parent = mock_path_inst
            mock_path_inst.__str__ = MagicMock(return_value="/fake/repo")

            await _perform_update(config, api)

        # Should report error status
        error_calls = [c for c in api.report_update_status.call_args_list if c[0][1] == "error"]
        assert len(error_calls) == 1
        assert "git fetch failed" in error_calls[0][0][2]

    @pytest.mark.asyncio
    async def test_git_reset_failure(self):
        config = _make_config()
        api = _make_api()

        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # git fetch succeeds
                return _mock_process(returncode=0)
            else:
                # git reset fails
                return _mock_process(returncode=1, stderr=b"reset error")

        with (
            patch("daemon.main.asyncio.create_subprocess_exec", side_effect=mock_exec),
            patch("daemon.main.shutil.which", return_value="/usr/bin/git"),
            patch("daemon.main.Path") as mock_path_cls,
        ):
            mock_path_inst = MagicMock()
            mock_path_cls.return_value.resolve.return_value.parent.parent.parent = mock_path_inst
            mock_path_inst.__str__ = MagicMock(return_value="/fake/repo")

            await _perform_update(config, api)

        error_calls = [c for c in api.report_update_status.call_args_list if c[0][1] == "error"]
        assert len(error_calls) == 1
        assert "git reset failed" in error_calls[0][0][2]


class TestHeartbeatLoopCommands:
    """Test command dispatch in heartbeat_loop."""

    @pytest.mark.asyncio
    async def test_update_command_triggers_perform_update(self):
        config = _make_config()
        api = _make_api()

        # First heartbeat returns update command, second returns None to break
        call_count = 0

        async def mock_heartbeat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"pending_command": "update"}
            return None

        api.heartbeat = mock_heartbeat

        display = MagicMock()
        display.set_brightness = MagicMock()
        display.set_blank_timeout = MagicMock()
        display.tick = MagicMock()

        shared = {"nfc": None, "scale": None, "display": display}

        with patch("daemon.main._perform_update", new_callable=AsyncMock) as mock_update:
            # Run for 2 iterations then cancel
            task = asyncio.create_task(heartbeat_loop(config, api, time.monotonic(), shared))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            mock_update.assert_awaited_once_with(config, api)

    @pytest.mark.asyncio
    async def test_update_command_reports_error_on_exception(self):
        config = _make_config()
        api = _make_api()

        call_count = 0

        async def mock_heartbeat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"pending_command": "update"}
            return None

        api.heartbeat = mock_heartbeat

        display = MagicMock()
        display.tick = MagicMock()
        shared = {"nfc": None, "scale": None, "display": display}

        with patch("daemon.main._perform_update", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            task = asyncio.create_task(heartbeat_loop(config, api, time.monotonic(), shared))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            api.report_update_status.assert_awaited()
            error_call = api.report_update_status.call_args
            assert error_call[0][1] == "error"

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
