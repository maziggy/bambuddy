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

    def test_debug_mode_keeps_httpx_pinned_to_warning(self):
        """httpx/httpcore must stay at WARNING even in debug mode — at INFO/DEBUG
        they log full request URLs, leaking webhook tokens (Discord etc.)."""
        import logging

        from backend.app.api.routes.support import _apply_log_level

        _apply_log_level(True)

        assert logging.getLogger("httpcore").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING

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


class TestSanitizeLogContent:
    """Tests for _sanitize_log_content() redaction."""

    def test_ipv4_addresses_redacted(self):
        """IPv4 addresses in log lines are replaced with [IP]."""
        from backend.app.api.routes.support import _sanitize_log_content

        content = "2024-01-15 Connected to printer at 192.168.1.100 on port 8883"
        result = _sanitize_log_content(content)
        assert "192.168.1.100" not in result
        assert "[IP]" in result
        assert "on port 8883" in result

    def test_multiple_ipv4_addresses_redacted(self):
        """Multiple different IPs in the same line are all redacted."""
        from backend.app.api.routes.support import _sanitize_log_content

        content = "Proxy 10.0.0.1 -> 192.168.1.50"
        result = _sanitize_log_content(content)
        assert result == "Proxy [IP] -> [IP]"

    def test_firmware_versions_with_leading_zeros_preserved(self):
        """Firmware versions like 01.09.01.00 have leading zeros and should NOT be redacted."""
        from backend.app.api.routes.support import _sanitize_log_content

        content = "Firmware version: 01.09.01.00"
        result = _sanitize_log_content(content)
        assert "01.09.01.00" in result

    def test_firmware_version_mixed_with_ip(self):
        """Firmware versions preserved while real IPs are redacted in the same line."""
        from backend.app.api.routes.support import _sanitize_log_content

        content = "Printer at 192.168.1.5 running firmware 01.07.02.00"
        result = _sanitize_log_content(content)
        assert "192.168.1.5" not in result
        assert "01.07.02.00" in result
        assert "[IP] running firmware 01.07.02.00" in result

    def test_printer_ip_from_sensitive_strings(self):
        """Printer IPs in sensitive_strings are replaced before regex pass."""
        from backend.app.api.routes.support import _sanitize_log_content

        content = "Connecting to 192.168.1.100"
        result = _sanitize_log_content(content, sensitive_strings={"192.168.1.100": "[IP]"})
        assert result == "Connecting to [IP]"

    def test_edge_case_zero_ip(self):
        """0.0.0.0 is a valid IP and should be redacted."""
        from backend.app.api.routes.support import _sanitize_log_content

        content = "Binding to 0.0.0.0"
        result = _sanitize_log_content(content)
        assert result == "Binding to [IP]"

    def test_edge_case_broadcast_ip(self):
        """255.255.255.255 is a valid IP and should be redacted."""
        from backend.app.api.routes.support import _sanitize_log_content

        content = "Broadcast to 255.255.255.255"
        result = _sanitize_log_content(content)
        assert result == "Broadcast to [IP]"

    def test_invalid_octet_not_redacted(self):
        """Octets >255 are not valid IPs and should not be redacted."""
        from backend.app.api.routes.support import _sanitize_log_content

        content = "Value 999.999.999.999"
        result = _sanitize_log_content(content)
        assert "999.999.999.999" in result

    def test_existing_serial_redaction_still_works(self):
        """Serial number redaction still functions alongside IP redaction."""
        from backend.app.api.routes.support import _sanitize_log_content

        content = "Printer 01SABCDEF1234 at 10.0.0.5"
        result = _sanitize_log_content(content)
        assert "[SERIAL]" in result
        assert "[IP]" in result
        assert "01SABCDEF1234" not in result
        assert "10.0.0.5" not in result

    def test_existing_email_redaction_still_works(self):
        """Email redaction still functions alongside IP redaction."""
        from backend.app.api.routes.support import _sanitize_log_content

        content = "User user@example.com from 172.16.0.1"
        result = _sanitize_log_content(content)
        assert "[EMAIL]" in result
        assert "[IP]" in result

    def test_existing_path_redaction_still_works(self):
        """Path redaction still functions alongside IP redaction."""
        from backend.app.api.routes.support import _sanitize_log_content

        content = "Config at /home/john/config.yaml from 192.168.0.1"
        result = _sanitize_log_content(content)
        assert "/home/[user]/" in result
        assert "[IP]" in result


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
            patch("backend.app.api.routes.support._detect_docker_network_mode", return_value="bridge"),
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
        assert info["network"]["interfaces"][0]["subnet"] == "x.x.1.0/24"
        # Verify IP addresses are NOT included (first two octets masked)
        for iface in info["network"]["interfaces"]:
            assert "ip" not in iface
            assert iface["subnet"].startswith("x.x.")

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

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_settings_include_all_keys_with_sensitive_redacted(self):
        """All settings keys must appear in output; sensitive values are replaced with [REDACTED]."""
        from backend.app.api.routes.support import _collect_support_info

        fake_settings = [
            MagicMock(key="benign_flag", value="true"),
            MagicMock(key="bambu_cloud_token", value="super-secret"),
            MagicMock(key="github_webhook", value="https://hooks.example/abc"),
            MagicMock(key="empty_password", value=""),
            MagicMock(key="local_backup_path", value="/data/backups"),
            # Regression: setting was leaking before the `broker` keyword was added.
            MagicMock(key="mqtt_broker", value="192.168.255.16"),
            # Regression: setting was leaking before the `auth_key` keyword was
            # added — and a value-prefix safety net (`tskey-`) was introduced
            # so future Tailscale settings auto-redact even if we forget the key.
            MagicMock(key="virtual_printer_tailscale_auth_key", value="tskey-auth-secrettokenhere"),
            # Value-prefix safety net standalone: a hypothetical future setting
            # named without "auth_key" but whose value starts with the Tailscale
            # prefix must still redact.
            MagicMock(key="some_future_ts_setting", value="tskey-other-secret"),
        ]

        def make_result(rows=None):
            r = MagicMock()
            r.scalar.return_value = 0
            r.scalar_one_or_none.return_value = None
            r.scalars.return_value.all.return_value = rows or []
            r.all.return_value = []
            return r

        async def fake_execute(stmt, *_a, **_kw):
            sql = str(stmt).lower()
            # Route by table name in the compiled SQL
            if "from settings" in sql or "settings.key" in sql:
                return make_result(fake_settings)
            return make_result([])

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("backend.app.api.routes.support.is_running_in_docker", return_value=False),
            patch("backend.app.api.routes.support.async_session") as mock_session_ctx,
            patch("backend.app.api.routes.support.printer_manager") as mock_pm,
            patch("backend.app.api.routes.support.get_network_interfaces", return_value=[]),
            patch("backend.app.api.routes.support.ws_manager") as mock_ws,
            patch("backend.app.api.routes.support.settings") as mock_settings,
        ):
            mock_settings.base_dir = Path(tmpdir)
            mock_settings.log_dir = Path(tmpdir)
            mock_settings.debug = False
            mock_pm.get_all_statuses.return_value = {}
            mock_ws.active_connections = []

            mock_db = AsyncMock()
            mock_db.execute = fake_execute
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            info = await _collect_support_info()

        s = info["settings"]
        assert s.get("bambu_cloud_token") == "[REDACTED]"
        assert s.get("github_webhook") == "[REDACTED]"
        assert s.get("local_backup_path") == "[REDACTED]"
        assert s.get("empty_password") == ""
        assert s.get("benign_flag") == "true"
        assert s.get("mqtt_broker") == "[REDACTED]"
        assert s.get("virtual_printer_tailscale_auth_key") == "[REDACTED]"
        assert s.get("some_future_ts_setting") == "[REDACTED]"


class TestParseObicoEnabledPrinters:
    """Tests for the per-printer obico flag parser used by the bundle."""

    def test_empty_string_returns_empty_set(self):
        from backend.app.api.routes.support import _parse_obico_enabled_printers

        assert _parse_obico_enabled_printers("") == set()
        assert _parse_obico_enabled_printers("   ") == set()

    def test_comma_separated_ids(self):
        from backend.app.api.routes.support import _parse_obico_enabled_printers

        assert _parse_obico_enabled_printers("1,2,3") == {1, 2, 3}
        # Whitespace around tokens is forgiven (matches obico_detection's parser).
        assert _parse_obico_enabled_printers("1, 2 ,3") == {1, 2, 3}

    def test_non_integer_tokens_are_skipped(self):
        # Defensive against legacy/manually-edited setting values.
        from backend.app.api.routes.support import _parse_obico_enabled_printers

        assert _parse_obico_enabled_printers("1,abc,2") == {1, 2}
        assert _parse_obico_enabled_printers(",,1,") == {1}


class TestCheckUrlReachable:
    """Tests for the slicer-API reachability ping."""

    @pytest.mark.asyncio
    async def test_empty_url_returns_none(self):
        from backend.app.api.routes.support import _check_url_reachable

        assert await _check_url_reachable("") is None
        assert await _check_url_reachable("   ") is None

    @pytest.mark.asyncio
    async def test_successful_response_is_reachable_even_on_404(self):
        # A 404 means the API is up; we want to separate network failure from
        # configuration mistakes, so non-empty status counts as reachable.
        from backend.app.api.routes.support import _check_url_reachable

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_client.get = AsyncMock(return_value=mock_response)

            result = await _check_url_reachable("http://localhost:3001/api")

        assert result is True

    @pytest.mark.asyncio
    async def test_connection_error_returns_false(self):
        from backend.app.api.routes.support import _check_url_reachable

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.side_effect = ConnectionError("boom")

            result = await _check_url_reachable("http://nowhere:9999")

        assert result is False


class TestCollectSlicerApiInfo:
    """Tests for the slicer-API info block (configured URLs + reachability).

    The collector reads URLs DIRECTLY from the DB rather than from the
    already-redacted ``info["settings"]`` dict — the previous version was
    pinging the literal string "[REDACTED]" (which httpx rejects) and getting
    ``False`` for any installation that actually had a slicer-API configured.
    These tests inject the raw URLs via a mocked `async_session` so the
    collector sees them as if they came from the unredacted Settings table.
    """

    def _make_settings_session(self, settings_dict):
        rows = [MagicMock(key=k, value=v) for k, v in settings_dict.items()]
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=result)
        ctx = MagicMock()
        ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        return ctx

    @pytest.mark.asyncio
    async def test_disabled_does_not_run_reachability_check(self):
        from backend.app.api.routes.support import _collect_slicer_api_info

        session_ctx = self._make_settings_session({"use_slicer_api": "false", "preferred_slicer": "bambu_studio"})
        with (
            patch("backend.app.api.routes.support.async_session", session_ctx),
            patch("backend.app.api.routes.support._check_url_reachable") as mock_check,
        ):
            info = await _collect_slicer_api_info()

        mock_check.assert_not_called()
        assert info["enabled"] is False
        assert info["preferred"] == "bambu_studio"
        assert info["bambu_studio_url_set_in_db"] is False
        assert info["orcaslicer_url_set_in_db"] is False
        assert "bambu_studio_reachable" not in info
        assert "orcaslicer_reachable" not in info

    @pytest.mark.asyncio
    async def test_enabled_runs_reachability_check_for_both_urls(self):
        from backend.app.api.routes.support import _collect_slicer_api_info

        async def fake_check(url, timeout=2.0):
            return "orca" in url

        session_ctx = self._make_settings_session(
            {
                "use_slicer_api": "true",
                "preferred_slicer": "orcaslicer",
                "bambu_studio_api_url": "http://bs:3001",
                "orcaslicer_api_url": "http://orca:3003",
            }
        )
        with (
            patch("backend.app.api.routes.support.async_session", session_ctx),
            patch("backend.app.api.routes.support._check_url_reachable", side_effect=fake_check),
        ):
            info = await _collect_slicer_api_info()

        assert info["enabled"] is True
        assert info["bambu_studio_url_set_in_db"] is True
        assert info["orcaslicer_url_set_in_db"] is True
        assert info["bambu_studio_url_source"] == "db"
        assert info["orcaslicer_url_source"] == "db"
        assert info["bambu_studio_reachable"] is False
        assert info["orcaslicer_reachable"] is True

    @pytest.mark.asyncio
    async def test_env_var_fallback_url_pinged_when_db_setting_empty(self):
        """Regression for the second pass on #support-bundle audit: the
        previous version returned `null` for `bambu_studio_reachable` on every
        installation that ran the sidecar via env var rather than via the DB
        setting (the common case for the default `http://localhost:3001`).
        The resolver now mirrors the precedence used by `archives.py:3174-3180`
        — DB setting first, then `app_settings.bambu_studio_api_url` (which
        reads the `BAMBU_STUDIO_API_URL` env var or the built-in default).
        """
        from backend.app.api.routes.support import _collect_slicer_api_info

        seen_urls: list[str] = []

        async def fake_check(url, timeout=2.0):
            seen_urls.append(url)
            return True

        # DB has use_slicer_api=true but NO bambu_studio_api_url row, simulating
        # a user who set the URL via the BAMBU_STUDIO_API_URL env var.
        session_ctx = self._make_settings_session({"use_slicer_api": "true", "preferred_slicer": "bambu_studio"})
        with (
            patch("backend.app.api.routes.support.async_session", session_ctx),
            patch("backend.app.api.routes.support._check_url_reachable", side_effect=fake_check),
            patch("backend.app.api.routes.support.settings") as mock_app_settings,
        ):
            # Pydantic-settings would normally do this for us when reading the
            # env var — we mock the resolved value directly.
            mock_app_settings.bambu_studio_api_url = "http://my-sidecar:3001"
            mock_app_settings.slicer_api_url = "http://localhost:3003"

            info = await _collect_slicer_api_info()

        # The env-var URL was the one actually pinged.
        assert "http://my-sidecar:3001" in seen_urls
        # And the source-tracking field shows we fell back from the DB to env.
        assert info["bambu_studio_url_set_in_db"] is False
        assert info["bambu_studio_url_source"] == "env_or_default"
        assert info["bambu_studio_reachable"] is True

    @pytest.mark.asyncio
    async def test_reachability_uses_unredacted_url(self):
        """Regression: the collector previously pinged the literal '[REDACTED]'
        from the already-sanitized info["settings"] dict and always returned
        False. The collector must read the un-redacted URL fresh from the DB.
        """
        from backend.app.api.routes.support import _collect_slicer_api_info

        seen_urls: list[str] = []

        async def fake_check(url, timeout=2.0):
            seen_urls.append(url)
            return True

        session_ctx = self._make_settings_session(
            {
                "use_slicer_api": "true",
                "bambu_studio_api_url": "http://real-bs-host:3001",
                "orcaslicer_api_url": "http://real-orca-host:3003",
            }
        )
        with (
            patch("backend.app.api.routes.support.async_session", session_ctx),
            patch("backend.app.api.routes.support._check_url_reachable", side_effect=fake_check),
        ):
            await _collect_slicer_api_info()

        assert "http://real-bs-host:3001" in seen_urls
        assert "http://real-orca-host:3003" in seen_urls
        assert "[REDACTED]" not in seen_urls


class TestCollectAuthInfo:
    """Tests for the OIDC / 2FA / API-key / group bundle block."""

    @pytest.mark.asyncio
    async def test_empty_database_returns_zero_counts_and_empty_list(self):
        from backend.app.api.routes.support import _collect_auth_info

        def make_count(value):
            r = MagicMock()
            r.scalar.return_value = value
            r.scalar_one_or_none.return_value = None
            r.scalars.return_value.all.return_value = []
            r.all.return_value = []
            return r

        async def fake_execute(stmt, *_a, **_kw):
            return make_count(0)

        db = AsyncMock()
        db.execute = fake_execute

        info = await _collect_auth_info(db)

        assert info["oidc_providers"] == []
        assert info["users_with_totp"] == 0
        assert info["email_otp_codes_pending"] == 0
        assert info["api_keys_total"] == 0
        assert info["api_keys_enabled"] == 0
        assert info["api_keys_expired"] == 0
        assert info["long_lived_tokens_total"] == 0
        assert info["long_lived_tokens_active"] == 0
        assert info["groups_system"] == 0
        assert info["groups_custom"] == 0

    @pytest.mark.asyncio
    async def test_oidc_provider_names_exported_in_cleartext(self):
        """Provider names are login-button labels — public, not a secret. Triage
        for SSO bugs is significantly easier when the provider is identified."""
        from backend.app.api.routes.support import _collect_auth_info

        provider = MagicMock()
        provider.id = 1
        provider.name = "PocketID"
        provider.is_enabled = True
        provider.scopes = "openid email profile"
        provider.email_claim = "email"
        provider.require_email_verified = True
        provider.auto_create_users = False
        provider.auto_link_existing_accounts = False
        provider.default_group_id = None
        provider.icon_url = None

        def make_result(rows=None, count=0):
            r = MagicMock()
            r.scalar.return_value = count
            r.scalar_one_or_none.return_value = None
            r.scalars.return_value.all.return_value = rows or []
            r.all.return_value = []
            return r

        async def fake_execute(stmt, *_a, **_kw):
            sql = str(stmt).lower()
            if "oidc_providers" in sql and "user_oidc_link" not in sql:
                return make_result([provider])
            return make_result(count=0)

        db = AsyncMock()
        db.execute = fake_execute

        info = await _collect_auth_info(db)

        assert len(info["oidc_providers"]) == 1
        oidc = info["oidc_providers"][0]
        assert oidc["name"] == "PocketID"
        # No secrets leak through — these fields don't exist on the dict.
        assert "client_id" not in oidc
        assert "client_secret" not in oidc
        assert "issuer_url" not in oidc


class TestCollectGitHubBackupInfo:
    """Tests for the GitHub-backup provider/failure-count block."""

    @pytest.mark.asyncio
    async def test_aggregates_providers_and_recent_failures(self):
        from backend.app.api.routes.support import _collect_github_backup_info

        c1 = MagicMock(provider="github", last_backup_status="success", schedule_enabled=True)
        c2 = MagicMock(provider="github", last_backup_status="failed", schedule_enabled=False)
        c3 = MagicMock(provider="gitea", last_backup_status="failed", schedule_enabled=True)

        result = MagicMock()
        result.scalars.return_value.all.return_value = [c1, c2, c3]
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)

        info = await _collect_github_backup_info(db)

        assert info["configs_total"] == 3
        assert info["providers_used"] == {"github": 2, "gitea": 1}
        assert info["schedule_enabled_count"] == 2
        assert info["last_failure_count"] == 2
