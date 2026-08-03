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
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "2024-01-15 Connected to printer at 192.168.1.100 on port 8883"
        result = _sanitize_log_content(content)
        assert "192.168.1.100" not in result
        assert "[IP]" in result
        assert "on port 8883" in result

    def test_multiple_ipv4_addresses_redacted(self):
        """Multiple different IPs in the same line are all redacted."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "Proxy 10.0.0.1 -> 192.168.1.50"
        result = _sanitize_log_content(content)
        assert result == "Proxy [IP] -> [IP]"

    def test_firmware_versions_with_leading_zeros_preserved(self):
        """Firmware versions like 01.09.01.00 have leading zeros and should NOT be redacted."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "Firmware version: 01.09.01.00"
        result = _sanitize_log_content(content)
        assert "01.09.01.00" in result

    def test_firmware_version_mixed_with_ip(self):
        """Firmware versions preserved while real IPs are redacted in the same line."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "Printer at 192.168.1.5 running firmware 01.07.02.00"
        result = _sanitize_log_content(content)
        assert "192.168.1.5" not in result
        assert "01.07.02.00" in result
        assert "[IP] running firmware 01.07.02.00" in result

    def test_printer_ip_from_sensitive_strings(self):
        """Printer IPs in sensitive_strings are replaced before regex pass."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "Connecting to 192.168.1.100"
        result = _sanitize_log_content(content, sensitive_strings={"192.168.1.100": "[IP]"})
        assert result == "Connecting to [IP]"

    def test_edge_case_zero_ip(self):
        """0.0.0.0 is a valid IP and should be redacted."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "Binding to 0.0.0.0"
        result = _sanitize_log_content(content)
        assert result == "Binding to [IP]"

    def test_edge_case_broadcast_ip(self):
        """255.255.255.255 is a valid IP and should be redacted."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "Broadcast to 255.255.255.255"
        result = _sanitize_log_content(content)
        assert result == "Broadcast to [IP]"

    def test_invalid_octet_not_redacted(self):
        """Octets >255 are not valid IPs and should not be redacted."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "Value 999.999.999.999"
        result = _sanitize_log_content(content)
        assert "999.999.999.999" in result

    def test_existing_serial_redaction_still_works(self):
        """Serial number redaction still functions alongside IP redaction."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "Printer 01SABCDEF1234 at 10.0.0.5"
        result = _sanitize_log_content(content)
        assert "[SERIAL]" in result
        assert "[IP]" in result
        assert "01SABCDEF1234" not in result
        assert "10.0.0.5" not in result

    def test_existing_email_redaction_still_works(self):
        """Email redaction still functions alongside IP redaction."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "User user@example.com from 172.16.0.1"
        result = _sanitize_log_content(content)
        assert "[EMAIL]" in result
        assert "[IP]" in result

    def test_existing_path_redaction_still_works(self):
        """Path redaction still functions alongside IP redaction."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "Config at /home/john/config.yaml from 192.168.0.1"
        result = _sanitize_log_content(content)
        assert "/home/[user]/" in result
        assert "[IP]" in result

    def test_ldap_dn_redacted_reporter_line(self):
        """#2681: the exact reporter line — the CN (real name) must not survive."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = (
            "LDAP authentication successful for user: jschmoe "
            "(DN: CN=Joe Schmoe,CN=Users,DC=ad,DC=example,DC=com, groups: 4)"
        )
        result = _sanitize_log_content(content)
        assert "Joe Schmoe" not in result
        assert "DC=example" not in result
        assert result == "LDAP authentication successful for user: jschmoe (DN: [DN], groups: 4)"

    def test_ldap_dn_redacted_in_exception_string(self):
        """DNs that leak indirectly via ldap3 exception text are caught too."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "LDAP bind failed for user jschmoe: invalidCredentials at uid=jschmoe,ou=people,dc=example,dc=org"
        result = _sanitize_log_content(content)
        assert "uid=jschmoe" not in result
        assert "[DN]" in result

    def test_ldap_group_dn_redacted(self):
        """Group DNs (from group-mapping logs) are PII-bearing and redacted."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "Mapped CN=Admins,OU=Groups,DC=corp,DC=local -> Administrators"
        result = _sanitize_log_content(content)
        assert "CN=Admins" not in result
        assert "DC=corp" not in result
        assert "[DN]" in result
        assert "Administrators" in result  # the non-PII target group name survives

    def test_non_dn_key_value_line_not_clobbered(self):
        """An ordinary key=value log line must not be mistaken for a DN."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "Dispatch decision: mode=queue, state=FINISH, printer=1"
        result = _sanitize_log_content(content)
        assert result == content

    def test_single_rdn_not_redacted(self):
        """A lone attr=value (not a multi-component DN) is left alone."""
        from backend.app.services.log_reader import sanitize_log_content as _sanitize_log_content

        content = "Country C=US selected"
        result = _sanitize_log_content(content)
        assert result == content


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
    """Tests for the per-printer obico flag parser used by the bundle.

    The setting is written by the settings UI as a JSON array and read by
    ObicoDetectionService._load_settings as one; the bundle used to split it on
    commas and call empty "no printers", so a default Obico setup was reported
    as monitoring nothing while it was in fact monitoring everything (#2733).
    """

    def test_empty_means_all_printers(self):
        from backend.app.api.routes.support import _parse_obico_enabled_printers

        # None (not "no printers") — the same convention _load_settings uses.
        assert _parse_obico_enabled_printers("") is None
        assert _parse_obico_enabled_printers("   ") is None
        assert _parse_obico_enabled_printers(None) is None

    def test_json_array_is_the_stored_shape(self):
        from backend.app.api.routes.support import _parse_obico_enabled_printers

        assert _parse_obico_enabled_printers("[1, 2, 3]") == {1, 2, 3}
        assert _parse_obico_enabled_printers("[]") == set()

    def test_comma_separated_ids_still_parse(self):
        # Legacy fallback for any install that stored the old shape.
        from backend.app.api.routes.support import _parse_obico_enabled_printers

        assert _parse_obico_enabled_printers("1,2,3") == {1, 2, 3}
        assert _parse_obico_enabled_printers("1, 2 ,3") == {1, 2, 3}

    def test_non_integer_tokens_are_skipped(self):
        # Defensive against legacy/manually-edited setting values.
        from backend.app.api.routes.support import _parse_obico_enabled_printers

        assert _parse_obico_enabled_printers("1,abc,2") == {1, 2}
        assert _parse_obico_enabled_printers(",,1,") == {1}
        assert _parse_obico_enabled_printers('[1, "two", 3]') == {1, 3}

    def test_json_object_is_not_a_printer_list(self):
        from backend.app.api.routes.support import _parse_obico_enabled_printers

        # Falls through to the comma parser, which finds no integers.
        assert _parse_obico_enabled_printers('{"1": true}') == set()


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


class TestFetchSlicerHealth:
    """Tests for the slicer-API health probe that extracts the bundled CLI
    version. Knowing the version in the support bundle lets the reviewer
    confirm the user is running the image they think they are — exactly the
    diagnostic that was missing when issue #1312 surfaced."""

    def _mock_httpx(self, status_code: int, body):
        """Construct a patched httpx.AsyncClient that returns a fixed response."""
        mock_client_cls = MagicMock()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_response = MagicMock()
        mock_response.status_code = status_code
        if isinstance(body, Exception):
            mock_response.json.side_effect = body
        else:
            mock_response.json.return_value = body
        mock_client.get = AsyncMock(return_value=mock_response)
        return mock_client_cls, mock_client

    @pytest.mark.asyncio
    async def test_empty_url_returns_none(self):
        from backend.app.api.routes.support import _fetch_slicer_health

        assert await _fetch_slicer_health("") is None
        assert await _fetch_slicer_health("   ") is None

    @pytest.mark.asyncio
    async def test_parses_version_from_orcaslicer_field(self):
        """The default sidecar wrapper labels both orca and bambu CLIs under
        ``checks.orcaslicer``. The probe must read whichever non-dataPath child
        carries a ``version`` field instead of hardcoding the field name."""
        from backend.app.api.routes.support import _fetch_slicer_health

        body = {
            "status": "healthy",
            "checks": {
                "orcaslicer": {"available": True, "version": "2.3.2"},
                "dataPath": {"accessible": True},
            },
        }
        mock_client_cls, mock_client = self._mock_httpx(200, body)
        with patch("httpx.AsyncClient", mock_client_cls):
            result = await _fetch_slicer_health("http://orca:3003")

        assert result == {"reachable": True, "version": "2.3.2"}
        # And the URL was actually composed as /health.
        mock_client.get.assert_awaited_once()
        assert mock_client.get.await_args[0][0] == "http://orca:3003/health"

    @pytest.mark.asyncio
    async def test_parses_version_when_wrapper_uses_bambustudio_field(self):
        """Future-proofing: if the wrapper is ever fixed to label the bambu CLI
        as ``bambustudio``, the probe must still pick up the version. The probe
        walks every non-dataPath key looking for a ``version`` field rather
        than hardcoding the slicer name."""
        from backend.app.api.routes.support import _fetch_slicer_health

        body = {
            "status": "healthy",
            "checks": {
                "bambustudio": {"available": True, "version": "02.06.00.51"},
                "dataPath": {"accessible": True},
            },
        }
        mock_client_cls, _ = self._mock_httpx(200, body)
        with patch("httpx.AsyncClient", mock_client_cls):
            result = await _fetch_slicer_health("http://bs:3001")

        assert result == {"reachable": True, "version": "02.06.00.51"}

    @pytest.mark.asyncio
    async def test_version_unknown_propagates_as_string(self):
        """The wrapper emits literal ``"unknown"`` when it can't parse the
        slicer's --help output. We surface that as-is — it's diagnostic on
        its own (tells the reviewer the regex didn't match)."""
        from backend.app.api.routes.support import _fetch_slicer_health

        body = {
            "status": "healthy",
            "checks": {
                "orcaslicer": {"available": True, "version": "unknown"},
                "dataPath": {"accessible": True},
            },
        }
        mock_client_cls, _ = self._mock_httpx(200, body)
        with patch("httpx.AsyncClient", mock_client_cls):
            result = await _fetch_slicer_health("http://bs:3001")

        assert result == {"reachable": True, "version": "unknown"}

    @pytest.mark.asyncio
    async def test_non_200_status_is_reachable_but_no_version(self):
        """If the URL responds with a non-200, the host is up but the endpoint
        isn't the expected one — surface reachable=True so the reviewer can
        spot misconfiguration without conflating it with a network failure."""
        from backend.app.api.routes.support import _fetch_slicer_health

        mock_client_cls, _ = self._mock_httpx(404, {})
        with patch("httpx.AsyncClient", mock_client_cls):
            result = await _fetch_slicer_health("http://bs:3001")

        assert result == {"reachable": True, "version": None}

    @pytest.mark.asyncio
    async def test_malformed_json_returns_reachable_no_version(self):
        from backend.app.api.routes.support import _fetch_slicer_health

        mock_client_cls, _ = self._mock_httpx(200, ValueError("not json"))
        with patch("httpx.AsyncClient", mock_client_cls):
            result = await _fetch_slicer_health("http://bs:3001")

        assert result == {"reachable": True, "version": None}

    @pytest.mark.asyncio
    async def test_missing_checks_block_returns_no_version(self):
        from backend.app.api.routes.support import _fetch_slicer_health

        mock_client_cls, _ = self._mock_httpx(200, {"status": "healthy"})
        with patch("httpx.AsyncClient", mock_client_cls):
            result = await _fetch_slicer_health("http://bs:3001")

        assert result == {"reachable": True, "version": None}

    @pytest.mark.asyncio
    async def test_connection_error_returns_unreachable(self):
        from backend.app.api.routes.support import _fetch_slicer_health

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.side_effect = ConnectionError("boom")

            result = await _fetch_slicer_health("http://nowhere:9999")

        assert result == {"reachable": False, "version": None}

    @pytest.mark.asyncio
    async def test_strips_trailing_slash_before_appending_health(self):
        """Defensive: URLs entered with trailing slashes in Settings should
        still produce a well-formed /health URL (no double-slash)."""
        from backend.app.api.routes.support import _fetch_slicer_health

        body = {"status": "healthy", "checks": {"orcaslicer": {"available": True, "version": "2.3.2"}}}
        mock_client_cls, mock_client = self._mock_httpx(200, body)
        with patch("httpx.AsyncClient", mock_client_cls):
            await _fetch_slicer_health("http://bs:3001/")

        assert mock_client.get.await_args[0][0] == "http://bs:3001/health"


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
            patch("backend.app.api.routes.support._fetch_slicer_health") as mock_health,
        ):
            info = await _collect_slicer_api_info()

        mock_health.assert_not_called()
        assert info["enabled"] is False
        assert info["preferred"] == "bambu_studio"
        assert info["bambu_studio_url_set_in_db"] is False
        assert info["orcaslicer_url_set_in_db"] is False
        assert "bambu_studio_reachable" not in info
        assert "orcaslicer_reachable" not in info
        assert "bambu_studio_version" not in info
        assert "orcaslicer_version" not in info

    @pytest.mark.asyncio
    async def test_enabled_runs_reachability_check_for_both_urls(self):
        from backend.app.api.routes.support import _collect_slicer_api_info

        async def fake_health(url, timeout=2.0):
            if "orca" in url:
                return {"reachable": True, "version": "2.3.2"}
            return {"reachable": False, "version": None}

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
            patch("backend.app.api.routes.support._fetch_slicer_health", side_effect=fake_health),
        ):
            info = await _collect_slicer_api_info()

        assert info["enabled"] is True
        assert info["bambu_studio_url_set_in_db"] is True
        assert info["orcaslicer_url_set_in_db"] is True
        assert info["bambu_studio_url_source"] == "db"
        assert info["orcaslicer_url_source"] == "db"
        assert info["bambu_studio_reachable"] is False
        assert info["orcaslicer_reachable"] is True
        assert info["bambu_studio_version"] is None
        assert info["orcaslicer_version"] == "2.3.2"

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

        async def fake_health(url, timeout=2.0):
            seen_urls.append(url)
            return {"reachable": True, "version": "02.06.00.51"}

        # DB has use_slicer_api=true but NO bambu_studio_api_url row, simulating
        # a user who set the URL via the BAMBU_STUDIO_API_URL env var.
        session_ctx = self._make_settings_session({"use_slicer_api": "true", "preferred_slicer": "bambu_studio"})
        with (
            patch("backend.app.api.routes.support.async_session", session_ctx),
            patch("backend.app.api.routes.support._fetch_slicer_health", side_effect=fake_health),
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
        assert info["bambu_studio_version"] == "02.06.00.51"

    @pytest.mark.asyncio
    async def test_reachability_uses_unredacted_url(self):
        """Regression: the collector previously pinged the literal '[REDACTED]'
        from the already-sanitized info["settings"] dict and always returned
        False. The collector must read the un-redacted URL fresh from the DB.
        """
        from backend.app.api.routes.support import _collect_slicer_api_info

        seen_urls: list[str] = []

        async def fake_health(url, timeout=2.0):
            seen_urls.append(url)
            return {"reachable": True, "version": "unknown"}

        session_ctx = self._make_settings_session(
            {
                "use_slicer_api": "true",
                "bambu_studio_api_url": "http://real-bs-host:3001",
                "orcaslicer_api_url": "http://real-orca-host:3003",
            }
        )
        with (
            patch("backend.app.api.routes.support.async_session", session_ctx),
            patch("backend.app.api.routes.support._fetch_slicer_health", side_effect=fake_health),
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


class TestRedactRawPushStatus:
    """Tests for _redact_raw_push_status() — the bundle dump scrubber."""

    def test_drops_user_filename_and_cloud_ids(self):
        from backend.app.api.routes.support import _redact_raw_push_status

        raw = {
            "subtask_name": "private_model.gcode",
            "gcode_file": "Metadata/private.gcode",
            "subtask_id": "1234567890",
            "task_id": "9999",
            "project_id": "proj-abc",
            "design_id": "design-1",
            "profile_id": "p-1",
            "model_id": "m-1",
            "gcode_state": "RUNNING",
            "layer_num": 42,  # control: non-sensitive sibling must survive
        }

        out = _redact_raw_push_status(raw)

        assert "subtask_name" not in out
        assert "gcode_file" not in out
        assert "subtask_id" not in out
        assert "task_id" not in out
        assert "project_id" not in out
        assert "design_id" not in out
        assert "profile_id" not in out
        assert "model_id" not in out
        assert "gcode_state" not in out
        assert out["layer_num"] == 42

    def test_redacts_net_info_ip_addresses(self):
        from backend.app.api.routes.support import _redact_raw_push_status

        raw = {
            "net": {
                "conf": 1,
                "info": [
                    {"ip": "192.168.1.42", "mask": "255.255.255.0"},
                    {"ip": "10.0.0.1", "mask": "255.0.0.0"},
                ],
            },
        }

        out = _redact_raw_push_status(raw)

        # LAN topology must be scrubbed (mirrors the #1429 VP fix).
        assert out["net"]["info"][0]["ip"] == "0.0.0.0"  # nosec B104 - redaction sentinel, not a bind address
        assert out["net"]["info"][1]["ip"] == "0.0.0.0"  # nosec B104 - redaction sentinel, not a bind address
        # Non-IP siblings inside the entry survive so the shape stays
        # diagnosable (interface count, mask presence, etc.).
        assert out["net"]["info"][0]["mask"] == "255.255.255.0"
        assert out["net"]["conf"] == 1

    def test_preserves_print_cfg_and_ams_payloads(self):
        """The point of bundling raw_data is keeping these — print.cfg is what
        unblocks per-model AMS Backup detection (deferred in 85fbd7fc).
        """
        from backend.app.api.routes.support import _redact_raw_push_status

        raw = {
            "print": {
                "cfg": 0x4000000,  # bit-26 — the H2D AMS Backup bit
                "option": 12345,
            },
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "humidity": "3",
                        "tray": [
                            {"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF"},
                        ],
                    }
                ]
            },
            "vt_tray": {"tray_info_idx": "GFA00", "tray_type": "PLA", "tray_color": "00FF00FF"},
            "vir_slot": [{"id": "0", "tray_type": "PLA"}],
            "mapping": [0, 1, 2, 3],
            "ams_extruder_map": {"0": 1},
        }

        out = _redact_raw_push_status(raw)

        assert out["print"]["cfg"] == 0x4000000
        assert out["print"]["option"] == 12345
        assert out["ams"]["ams"][0]["tray"][0]["tray_type"] == "PLA"
        assert out["vt_tray"]["tray_info_idx"] == "GFA00"
        assert out["vir_slot"][0]["tray_type"] == "PLA"
        assert out["mapping"] == [0, 1, 2, 3]
        assert out["ams_extruder_map"] == {"0": 1}

    def test_does_not_mutate_input(self):
        """Live state.raw_data must not be touched — the dispatcher reads it on
        every tick, mutation would race the next push.
        """
        from backend.app.api.routes.support import _redact_raw_push_status

        raw = {
            "subtask_name": "secret.gcode",
            "net": {"info": [{"ip": "192.168.1.5"}]},
            "print": {"cfg": 1},
        }
        original_subtask = raw["subtask_name"]
        original_ip = raw["net"]["info"][0]["ip"]

        _redact_raw_push_status(raw)

        assert raw["subtask_name"] == original_subtask
        assert raw["net"]["info"][0]["ip"] == original_ip

    def test_handles_non_dict_gracefully(self):
        from backend.app.api.routes.support import _redact_raw_push_status

        assert _redact_raw_push_status(None) == {}  # type: ignore[arg-type]
        assert _redact_raw_push_status([]) == {}  # type: ignore[arg-type]
        assert _redact_raw_push_status("") == {}  # type: ignore[arg-type]


class TestSanitizePushStatusValues:
    """The bundled push_status snapshot must stay parseable JSON.

    Sanitization used to run over the *serialised* snapshot. The generic
    Bambu-serial regex in ``log_reader`` (``0[0-3][A-Z0-9][A-Z0-9]{9,13}``)
    matches the decimal expansion of a float as readily as a serial, so an AMS
    ``k`` flow factor came out as ``0.[SERIAL]`` and the whole file stopped
    parsing — found in a real bundle while diagnosing #2702, which is exactly
    the case the snapshot was added to serve.
    """

    def test_float_that_matches_the_serial_regex_survives(self):
        """The observed reproducer, verbatim."""
        import json

        from backend.app.api.routes.support import _sanitize_push_status_values

        raw = {"ams": [{"tray": [{"k": 0.0199999995529652}]}]}

        out = _sanitize_push_status_values(raw, {})

        assert json.loads(json.dumps(out)) == raw

    def test_output_always_parses(self):
        """Whatever it does to values, the result must be valid JSON."""
        import json

        from backend.app.api.routes.support import _sanitize_push_status_values

        raw = {
            "k_values": [0.0199999995529652, 0.019999999552965164, 0.02],
            "home_flag": 7554487,
            "sdcard": True,
            "resolution": "",
            "nozzle": None,
        }

        json.loads(json.dumps(_sanitize_push_status_values(raw, {})))

    def test_still_redacts_strings(self):
        """The point of the pass is not lost — string values are sanitized."""
        from backend.app.api.routes.support import _sanitize_push_status_values

        raw = {"tag_uid": "0123456789ABCDEF", "name": "Martin's P1S", "ip": "192.168.1.50"}

        out = _sanitize_push_status_values(raw, {"Martin's P1S": "[PRINTER]"})

        assert out["tag_uid"] == "[SERIAL]"
        assert out["name"] == "[PRINTER]"
        assert out["ip"] == "[IP]"

    def test_walks_nested_containers(self):
        from backend.app.api.routes.support import _sanitize_push_status_values

        raw = {"ams": [{"tray": [{"tray_uuid": "0123456789ABCDEF"}]}]}

        out = _sanitize_push_status_values(raw, {})

        assert out["ams"][0]["tray"][0]["tray_uuid"] == "[SERIAL]"

    def test_keys_are_left_alone(self):
        """Keys are structural — renaming one would break the schema."""
        from backend.app.api.routes.support import _sanitize_push_status_values

        raw = {"0123456789ABCDEF": 1}

        assert list(_sanitize_push_status_values(raw, {})) == ["0123456789ABCDEF"]

    def test_non_json_scalars_are_sanitized_not_smuggled(self):
        """``json.dumps(default=str)`` runs after this pass, so do it here."""
        from datetime import datetime, timezone

        from backend.app.api.routes.support import _sanitize_push_status_values

        raw = {"seen_at": datetime(2026, 7, 29, 23, 12, 40, tzinfo=timezone.utc), "who": object()}

        out = _sanitize_push_status_values(raw, {"2026-07-29": "[WHEN]"})

        assert out["seen_at"].startswith("[WHEN]")
        assert isinstance(out["who"], str)

    def test_bools_stay_bools(self):
        """`isinstance(True, int)` — a bool must not fall through to str()."""
        from backend.app.api.routes.support import _sanitize_push_status_values

        out = _sanitize_push_status_values({"sdcard": True, "force_upgrade": False}, {})

        assert out["sdcard"] is True
        assert out["force_upgrade"] is False

    def test_the_full_bundle_chain_on_a_real_p1s_payload(self):
        """The route's transform, end to end, on the shape from the #2702 bundle.

        `_redact_raw_push_status` then `_sanitize_push_status_values` then
        `json.dumps(default=str)` — the composition the bundle writer applies.
        The bundle that exposed this had five `k` values corrupted, so the
        snapshot could not be read at all; the field the report was about
        (`total_layer_num`) was sitting in it, intact and unreachable.
        """
        import json

        from backend.app.api.routes.support import (
            _redact_raw_push_status,
            _sanitize_push_status_values,
        )

        raw = {
            "gcode_file": "AMS_Filament_Clip_3MF.3mf",
            "layer_num": 2,
            "total_layer_num": 33,
            "home_flag": 7554487,
            "sdcard": True,
            "net": {"info": [{"ip": "192.168.1.50", "mask": 0}]},
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "humidity": "5",
                        "tray": [
                            {"id": "0", "k": 0.0199999995529652, "tag_uid": "0123456789ABCDEF"},
                            {"id": "1", "k": 0.0209999997168779, "tag_uid": "44F782D000000100"},
                        ],
                    }
                ]
            },
        }

        snapshot = {
            "model": "P1S",
            "firmware_version": "01.10.00.00",
            "raw_data": _redact_raw_push_status(raw),
        }
        text = json.dumps(_sanitize_push_status_values(snapshot, {}), indent=2, default=str)

        parsed = json.loads(text)  # used to raise "Expecting ',' delimiter"
        trays = parsed["raw_data"]["ams"]["ams"][0]["tray"]
        assert [t["k"] for t in trays] == [0.0199999995529652, 0.0209999997168779]
        assert parsed["raw_data"]["total_layer_num"] == 33
        # Redaction still did its job on both fronts.
        assert "gcode_file" not in parsed["raw_data"]
        assert trays[0]["tag_uid"] == "[SERIAL]"
        # The structural pass replaces the printer's LAN address with the
        # sentinel 0.0.0.0, which is itself an IPv4 literal, so the value pass
        # then masks it to [IP]. Harmless — the real address is already gone —
        # and matches what shipped in the bundle behind #2702.
        assert parsed["raw_data"]["net"]["info"][0]["ip"] == "[IP]"

    def test_does_not_mutate_the_live_snapshot(self):
        """`state.raw_data` is read by the dispatcher on every tick.

        The bundle writer passes a redacted copy, but a walker that mutated in
        place would still be one refactor away from redacting the live state.
        """
        import copy

        from backend.app.api.routes.support import _sanitize_push_status_values

        raw = {"tag_uid": "0123456789ABCDEF", "ams": [{"tray": [{"k": 0.02, "n": "0123456789ABCDEF"}]}]}
        before = copy.deepcopy(raw)

        out = _sanitize_push_status_values(raw, {})

        assert raw == before, "input was mutated"
        assert out["tag_uid"] == "[SERIAL]"  # and the copy really was redacted


class TestProcessInfo:
    """Bambuddy's own footprint in the bundle (#2734).

    Bundles carried nothing about the process itself, so "memory climbs over
    days until the OOM killer fires" could not be triaged from a bundle — the
    reporter had to run shell commands by hand, and the numbers that would have
    named the mechanism were unrecoverable afterwards.
    """

    def test_reports_the_figures_that_separate_the_mechanisms(self):
        """RSS vs VMS, threads and children distinguish a heap that is growing
        from address space, a thread leak, and a child-process leak."""
        from backend.app.api.routes.support import _collect_process_info

        info = _collect_process_info()

        assert info["available"] is True
        for key in ("rss_bytes", "vms_bytes", "num_threads", "children_total"):
            assert isinstance(info[key], int), key

    def test_children_are_named_but_never_quoted(self):
        """An ffmpeg argv carries the camera URL, and with it its password. The
        count per executable is what identifies a leak; the arguments are not
        needed and must not travel."""
        from backend.app.api.routes.support import _collect_process_info

        info = _collect_process_info()

        for name in info.get("children_by_name", {}):
            assert " " not in name, f"looks like a command line, not a name: {name!r}"
            assert "://" not in name

    def test_heap_census_is_skipped_on_a_large_process(self):
        """gc.get_objects() materialises every tracked object, so the census
        costs most on the process that can least afford it. A bundle generated
        to diagnose runaway memory must not be the allocation that tips the
        host over."""
        from unittest.mock import MagicMock, patch

        import backend.app.api.routes.support as support_module

        fake = MagicMock()
        fake.memory_info.return_value = MagicMock(rss=8 * 1024**3, vms=12 * 1024**3)
        fake.num_threads.return_value = 40
        fake.create_time.return_value = 0.0
        fake.open_files.return_value = []
        fake.net_connections.return_value = []
        fake.children.return_value = []

        with patch("psutil.Process", return_value=fake):
            info = support_module._collect_process_info()

        assert "gc_top_types" not in info
        assert "skipped" in info["gc_census"]
        # The discriminating numbers still come through — those are the point.
        assert info["rss_bytes"] == 8 * 1024**3
        assert info["num_threads"] == 40

    def test_heap_census_runs_on_a_normal_process(self):
        from backend.app.api.routes.support import _collect_process_info

        info = _collect_process_info()

        assert info["gc_tracked_objects"] > 0
        assert len(info["gc_top_types"]) <= 15

    def test_survives_a_hostile_psutil(self):
        """psutil raises on hardened kernels and in restricted containers. A
        support bundle must still be produced when it does — the bundle is how
        someone reports the problem in the first place."""
        from unittest.mock import patch

        import backend.app.api.routes.support as support_module

        with patch("psutil.Process", side_effect=RuntimeError("no /proc for you")):
            info = support_module._collect_process_info()

        assert info == {"available": False}

    def test_partial_failures_do_not_lose_the_rest(self):
        """One inaccessible metric must not cost the others."""
        from unittest.mock import MagicMock, patch

        import backend.app.api.routes.support as support_module

        fake = MagicMock()
        fake.memory_info.return_value = MagicMock(rss=100, vms=200)
        fake.num_threads.side_effect = PermissionError("denied")
        fake.create_time.return_value = 0.0
        fake.open_files.side_effect = PermissionError("denied")
        fake.net_connections.side_effect = PermissionError("denied")
        fake.children.return_value = []

        with patch("psutil.Process", return_value=fake):
            info = support_module._collect_process_info()

        assert info["rss_bytes"] == 100
        assert "num_threads" not in info
        assert info["children_total"] == 0
