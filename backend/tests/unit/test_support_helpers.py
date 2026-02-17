"""Unit tests for support module helper functions.

Tests _anonymize_mqtt_broker, _check_port, _get_container_memory_limit,
_format_bytes, and _collect_support_info diagnostic sections.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestApplyLogLevel:
    """Tests for _apply_log_level() debug noise suppression."""

    def test_debug_mode_suppresses_sqlalchemy_to_warning(self):
        """Verify sqlalchemy.engine is set to WARNING (not INFO) in debug mode."""
        import logging

        from backend.app.api.routes.support import _apply_log_level

        _apply_log_level(True)

        assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING

    def test_debug_mode_suppresses_aiosqlite(self):
        """Verify aiosqlite is set to WARNING in debug mode to prevent cursor noise."""
        import logging

        from backend.app.api.routes.support import _apply_log_level

        _apply_log_level(True)

        assert logging.getLogger("aiosqlite").level == logging.WARNING

    def test_debug_mode_enables_httpcore_debug(self):
        """Verify httpcore stays at DEBUG in debug mode."""
        import logging

        from backend.app.api.routes.support import _apply_log_level

        _apply_log_level(True)

        assert logging.getLogger("httpcore").level == logging.DEBUG

    def test_non_debug_mode_suppresses_all_noisy_loggers(self):
        """Verify all noisy loggers are set to WARNING in non-debug mode."""
        import logging

        from backend.app.api.routes.support import _apply_log_level

        _apply_log_level(False)

        assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("paho.mqtt").level == logging.WARNING


class TestAnonymizeMqttBroker:
    """Tests for _anonymize_mqtt_broker()."""

    def test_empty_string(self):
        from backend.app.api.routes.support import _anonymize_mqtt_broker

        assert _anonymize_mqtt_broker("") == ""

    def test_ipv4_address(self):
        from backend.app.api.routes.support import _anonymize_mqtt_broker

        assert _anonymize_mqtt_broker("192.168.1.100") == "[IP]"

    def test_ipv6_address(self):
        from backend.app.api.routes.support import _anonymize_mqtt_broker

        assert _anonymize_mqtt_broker("::1") == "[IP]"

    def test_hostname_with_domain(self):
        from backend.app.api.routes.support import _anonymize_mqtt_broker

        assert _anonymize_mqtt_broker("mqtt.example.com") == "*.example.com"

    def test_hostname_with_subdomain(self):
        from backend.app.api.routes.support import _anonymize_mqtt_broker

        assert _anonymize_mqtt_broker("broker.mqtt.example.com") == "*.example.com"

    def test_single_part_hostname(self):
        from backend.app.api.routes.support import _anonymize_mqtt_broker

        assert _anonymize_mqtt_broker("localhost") == "localhost"


class TestCheckPort:
    """Tests for _check_port()."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_reachable_port(self):
        from backend.app.api.routes.support import _check_port

        # Mock a successful connection
        mock_writer = AsyncMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        with patch("backend.app.api.routes.support.asyncio.open_connection", return_value=(AsyncMock(), mock_writer)):
            result = await _check_port("192.168.1.1", 8883, timeout=1.0)

        assert result is True

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_unreachable_port(self):
        from backend.app.api.routes.support import _check_port

        with (
            patch(
                "backend.app.api.routes.support.asyncio.open_connection",
                side_effect=ConnectionRefusedError,
            ),
            patch(
                "backend.app.api.routes.support.asyncio.wait_for",
                side_effect=ConnectionRefusedError,
            ),
        ):
            result = await _check_port("192.168.1.1", 8883, timeout=1.0)

        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_timeout(self):
        from backend.app.api.routes.support import _check_port

        with patch(
            "backend.app.api.routes.support.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            result = await _check_port("192.168.1.1", 8883, timeout=0.1)

        assert result is False


class TestGetContainerMemoryLimit:
    """Tests for _get_container_memory_limit()."""

    def test_cgroup_v2_with_limit(self):
        from backend.app.api.routes.support import _get_container_memory_limit

        with tempfile.TemporaryDirectory() as tmpdir:
            v2_path = Path(tmpdir) / "memory.max"
            v2_path.write_text("1073741824\n")

            with patch("backend.app.api.routes.support.Path") as mock_path:
                # v2 path exists with value
                v2_mock = MagicMock()
                v2_mock.exists.return_value = True
                v2_mock.read_text.return_value = "1073741824\n"

                v1_mock = MagicMock()
                v1_mock.exists.return_value = False

                mock_path.side_effect = lambda p: v2_mock if "memory.max" in p else v1_mock

                result = _get_container_memory_limit()

        assert result == 1073741824

    def test_cgroup_v2_unlimited(self):
        from backend.app.api.routes.support import _get_container_memory_limit

        with patch("backend.app.api.routes.support.Path") as mock_path:
            v2_mock = MagicMock()
            v2_mock.exists.return_value = True
            v2_mock.read_text.return_value = "max\n"

            v1_mock = MagicMock()
            v1_mock.exists.return_value = False

            mock_path.side_effect = lambda p: v2_mock if "memory.max" in p else v1_mock

            result = _get_container_memory_limit()

        assert result is None

    def test_no_cgroup_files(self):
        from backend.app.api.routes.support import _get_container_memory_limit

        with patch("backend.app.api.routes.support.Path") as mock_path:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_path.return_value = mock_instance

            result = _get_container_memory_limit()

        assert result is None


class TestFormatBytes:
    """Tests for _format_bytes()."""

    def test_bytes(self):
        from backend.app.api.routes.support import _format_bytes

        assert _format_bytes(500) == "500 B"

    def test_kilobytes(self):
        from backend.app.api.routes.support import _format_bytes

        assert _format_bytes(2048) == "2.0 KB"

    def test_megabytes(self):
        from backend.app.api.routes.support import _format_bytes

        assert _format_bytes(10 * 1024 * 1024) == "10.0 MB"

    def test_gigabytes(self):
        from backend.app.api.routes.support import _format_bytes

        assert _format_bytes(2 * 1024 * 1024 * 1024) == "2.00 GB"

    def test_zero(self):
        from backend.app.api.routes.support import _format_bytes

        assert _format_bytes(0) == "0 B"


class TestCollectSupportInfo:
    """Tests for _collect_support_info() new diagnostic sections."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_environment_has_timezone(self):
        """Verify environment section includes timezone."""
        from backend.app.api.routes.support import _collect_support_info

        with (
            patch("backend.app.api.routes.support.is_running_in_docker", return_value=False),
            patch("backend.app.api.routes.support.async_session") as mock_session_ctx,
            patch("backend.app.api.routes.support.printer_manager") as mock_pm,
            patch("backend.app.api.routes.support.get_network_interfaces", return_value=[]),
            patch("backend.app.api.routes.support.ws_manager") as mock_ws,
            patch.dict("os.environ", {"TZ": "America/New_York"}),
        ):
            mock_pm.get_all_statuses.return_value = {}
            mock_ws.active_connections = []

            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar.return_value = 0
            mock_result.scalar_one_or_none.return_value = None
            mock_result.scalars.return_value.all.return_value = []
            mock_db.execute = AsyncMock(return_value=mock_result)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            info = await _collect_support_info()

        assert info["environment"]["timezone"] == "America/New_York"
        assert info["environment"]["docker"] is False

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_docker_section_present_when_in_docker(self):
        """Verify docker section is added when running in Docker."""
        from backend.app.api.routes.support import _collect_support_info

        with (
            patch("backend.app.api.routes.support.is_running_in_docker", return_value=True),
            patch("backend.app.api.routes.support._get_container_memory_limit", return_value=1073741824),
            patch("backend.app.api.routes.support.async_session") as mock_session_ctx,
            patch("backend.app.api.routes.support.printer_manager") as mock_pm,
            patch(
                "backend.app.api.routes.support.get_network_interfaces",
                return_value=[{"name": "eth0", "subnet": "172.17.0.0/16"}],
            ),
            patch("backend.app.api.routes.support.ws_manager") as mock_ws,
        ):
            mock_pm.get_all_statuses.return_value = {}
            mock_ws.active_connections = []

            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar.return_value = 0
            mock_result.scalar_one_or_none.return_value = None
            mock_result.scalars.return_value.all.return_value = []
            mock_db.execute = AsyncMock(return_value=mock_result)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            info = await _collect_support_info()

        assert "docker" in info
        assert info["docker"]["container_memory_limit_bytes"] == 1073741824
        assert info["docker"]["container_memory_limit_formatted"] == "1.00 GB"
        assert info["docker"]["network_mode_hint"] == "bridge"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_docker_section_absent_when_not_docker(self):
        """Verify docker section is absent when not in Docker."""
        from backend.app.api.routes.support import _collect_support_info

        with (
            patch("backend.app.api.routes.support.is_running_in_docker", return_value=False),
            patch("backend.app.api.routes.support.async_session") as mock_session_ctx,
            patch("backend.app.api.routes.support.printer_manager") as mock_pm,
            patch("backend.app.api.routes.support.get_network_interfaces", return_value=[]),
            patch("backend.app.api.routes.support.ws_manager") as mock_ws,
        ):
            mock_pm.get_all_statuses.return_value = {}
            mock_ws.active_connections = []

            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar.return_value = 0
            mock_result.scalar_one_or_none.return_value = None
            mock_result.scalars.return_value.all.return_value = []
            mock_db.execute = AsyncMock(return_value=mock_result)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            info = await _collect_support_info()

        assert "docker" not in info

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_dependencies_section(self):
        """Verify dependencies section lists package versions."""
        from backend.app.api.routes.support import _collect_support_info

        with (
            patch("backend.app.api.routes.support.is_running_in_docker", return_value=False),
            patch("backend.app.api.routes.support.async_session") as mock_session_ctx,
            patch("backend.app.api.routes.support.printer_manager") as mock_pm,
            patch("backend.app.api.routes.support.get_network_interfaces", return_value=[]),
            patch("backend.app.api.routes.support.ws_manager") as mock_ws,
        ):
            mock_pm.get_all_statuses.return_value = {}
            mock_ws.active_connections = []

            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar.return_value = 0
            mock_result.scalar_one_or_none.return_value = None
            mock_result.scalars.return_value.all.return_value = []
            mock_db.execute = AsyncMock(return_value=mock_result)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            info = await _collect_support_info()

        assert "dependencies" in info
        # fastapi should be installed in test environment
        assert "fastapi" in info["dependencies"]
        assert info["dependencies"]["fastapi"] is not None

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_websockets_section(self):
        """Verify websockets section shows connection count."""
        from backend.app.api.routes.support import _collect_support_info

        with (
            patch("backend.app.api.routes.support.is_running_in_docker", return_value=False),
            patch("backend.app.api.routes.support.async_session") as mock_session_ctx,
            patch("backend.app.api.routes.support.printer_manager") as mock_pm,
            patch("backend.app.api.routes.support.get_network_interfaces", return_value=[]),
            patch("backend.app.api.routes.support.ws_manager") as mock_ws,
        ):
            mock_pm.get_all_statuses.return_value = {}
            mock_ws.active_connections = ["conn1", "conn2"]

            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar.return_value = 0
            mock_result.scalar_one_or_none.return_value = None
            mock_result.scalars.return_value.all.return_value = []
            mock_db.execute = AsyncMock(return_value=mock_result)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            info = await _collect_support_info()

        assert info["websockets"]["active_connections"] == 2

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_network_section(self):
        """Verify network section shows interface subnets."""
        from backend.app.api.routes.support import _collect_support_info

        mock_interfaces = [
            {"name": "eth0", "ip": "192.168.1.100", "netmask": "255.255.255.0", "subnet": "192.168.1.0/24"},
            {"name": "wlan0", "ip": "10.0.0.50", "netmask": "255.255.255.0", "subnet": "10.0.0.0/24"},
        ]

        with (
            patch("backend.app.api.routes.support.is_running_in_docker", return_value=False),
            patch("backend.app.api.routes.support.async_session") as mock_session_ctx,
            patch("backend.app.api.routes.support.printer_manager") as mock_pm,
            patch("backend.app.api.routes.support.get_network_interfaces", return_value=mock_interfaces),
            patch("backend.app.api.routes.support.ws_manager") as mock_ws,
        ):
            mock_pm.get_all_statuses.return_value = {}
            mock_ws.active_connections = []

            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar.return_value = 0
            mock_result.scalar_one_or_none.return_value = None
            mock_result.scalars.return_value.all.return_value = []
            mock_db.execute = AsyncMock(return_value=mock_result)

            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            info = await _collect_support_info()

        assert info["network"]["interface_count"] == 2
        assert info["network"]["interfaces"][0]["name"] == "eth0"
        assert info["network"]["interfaces"][0]["subnet"] == "192.168.1.0/24"
        # Verify IP addresses are NOT included
        for iface in info["network"]["interfaces"]:
            assert "ip" not in iface

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_log_file_section(self):
        """Verify log file section shows size info."""
        from backend.app.api.routes.support import _collect_support_info

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            log_file = log_dir / "bambuddy.log"
            log_file.write_text("some log content\n" * 100)

            with (
                patch("backend.app.api.routes.support.is_running_in_docker", return_value=False),
                patch("backend.app.api.routes.support.async_session") as mock_session_ctx,
                patch("backend.app.api.routes.support.printer_manager") as mock_pm,
                patch("backend.app.api.routes.support.get_network_interfaces", return_value=[]),
                patch("backend.app.api.routes.support.ws_manager") as mock_ws,
                patch("backend.app.api.routes.support.settings") as mock_settings,
            ):
                mock_settings.base_dir = Path(tmpdir)
                mock_settings.log_dir = log_dir
                mock_settings.debug = False
                mock_pm.get_all_statuses.return_value = {}
                mock_ws.active_connections = []

                mock_db = AsyncMock()
                mock_result = MagicMock()
                mock_result.scalar.return_value = 0
                mock_result.scalar_one_or_none.return_value = None
                mock_result.scalars.return_value.all.return_value = []
                mock_db.execute = AsyncMock(return_value=mock_result)

                mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

                info = await _collect_support_info()

        assert "log_file" in info
        assert info["log_file"]["size_bytes"] > 0
        assert "B" in info["log_file"]["size_formatted"] or "KB" in info["log_file"]["size_formatted"]
