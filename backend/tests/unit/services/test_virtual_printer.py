"""Unit tests for Virtual Printer services.

Tests the virtual printer manager, FTP server, and SSDP server components.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestVirtualPrinterInstance:
    """Tests for VirtualPrinterInstance class."""

    @pytest.fixture
    def instance(self, tmp_path):
        """Create a VirtualPrinterInstance with test defaults."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        return VirtualPrinterInstance(
            vp_id=1,
            name="TestPrinter",
            mode="immediate",
            model="C11",
            access_code="12345678",
            serial_suffix="391800001",
            base_dir=tmp_path,
        )

    # ========================================================================
    # Tests for instance properties
    # ========================================================================

    def test_instance_stores_parameters(self, instance):
        """Verify constructor stores parameters correctly."""
        assert instance.id == 1
        assert instance.name == "TestPrinter"
        assert instance.mode == "immediate"
        assert instance.model == "C11"
        assert instance.access_code == "12345678"
        assert instance.serial_suffix == "391800001"

    def test_instance_serial_property(self, instance):
        """Verify serial is generated from model prefix + suffix."""
        # C11 = P1P, prefix = 01S00A
        assert instance.serial == "01S00A391800001"

    def test_instance_serial_x1c(self, tmp_path):
        """Verify X1C serial uses correct prefix."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        inst = VirtualPrinterInstance(
            vp_id=2,
            name="X1C",
            mode="immediate",
            model="3DPrinter-X1-Carbon",
            access_code="12345678",
            serial_suffix="391800002",
            base_dir=tmp_path,
        )
        assert inst.serial == "00M00A391800002"

    def test_instance_is_proxy_false(self, instance):
        """Verify is_proxy is False for non-proxy mode."""
        assert instance.is_proxy is False

    def test_instance_is_proxy_true(self, tmp_path):
        """Verify is_proxy is True for proxy mode."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        inst = VirtualPrinterInstance(
            vp_id=3,
            name="Proxy",
            mode="proxy",
            model="C11",
            access_code="",
            serial_suffix="391800003",
            target_printer_ip="192.168.1.100",
            base_dir=tmp_path,
        )
        assert inst.is_proxy is True

    def test_instance_is_running_with_active_tasks(self, instance):
        """Verify is_running is True when tasks are active."""
        mock_task = MagicMock()
        mock_task.done.return_value = False
        instance._tasks = [mock_task]
        assert instance.is_running is True

    def test_instance_is_running_with_no_tasks(self, instance):
        """Verify is_running is False when no tasks."""
        assert instance.is_running is False

    def test_instance_creates_directories(self, instance, tmp_path):
        """Verify instance creates upload and cert directories."""
        assert (tmp_path / "uploads" / "1").exists()
        assert (tmp_path / "uploads" / "1" / "cache").exists()
        assert (tmp_path / "certs" / "1").exists()

    # ========================================================================
    # Tests for status
    # ========================================================================

    def test_get_status_returns_correct_format(self, instance):
        """Verify get_status returns expected fields."""
        instance._pending_files = {"file1.3mf": Path("/tmp/file1.3mf")}
        mock_task = MagicMock(done=MagicMock(return_value=False))
        instance._tasks = [mock_task]

        status = instance.get_status()
        assert status["running"] is True
        assert status["pending_files"] == 1

    def test_get_status_not_running(self, instance):
        """Verify get_status when no tasks."""
        status = instance.get_status()
        assert status["running"] is False
        assert status["pending_files"] == 0

    # ========================================================================
    # Tests for file handling
    # ========================================================================

    @pytest.mark.asyncio
    async def test_on_file_received_adds_to_pending(self, instance):
        """Verify received file is added to pending list in review mode."""
        instance.mode = "review"

        file_path = Path("/tmp/test.3mf")

        with patch.object(instance, "_queue_file", new_callable=AsyncMock) as mock_queue:
            await instance.on_file_received(file_path, "192.168.1.100")

            assert "test.3mf" in instance._pending_files
            mock_queue.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_file_received_archives_immediately(self, instance):
        """Verify file is archived in immediate mode."""
        file_path = Path("/tmp/test.3mf")

        with patch.object(instance, "_archive_file", new_callable=AsyncMock) as mock_archive:
            await instance.on_file_received(file_path, "192.168.1.100")

            mock_archive.assert_called_once_with(file_path, "192.168.1.100")

    @pytest.mark.asyncio
    async def test_archive_file_skips_non_3mf(self, instance):
        """Verify non-3MF files are skipped and cleaned up."""
        instance._session_factory = MagicMock()
        instance._pending_files["verify_job"] = Path("/tmp/verify_job")

        with patch("pathlib.Path.unlink"):
            await instance._archive_file(Path("/tmp/verify_job"), "192.168.1.100")

            assert "verify_job" not in instance._pending_files


class TestVirtualPrinterManager:
    """Tests for VirtualPrinterManager orchestrator."""

    @pytest.fixture
    def manager(self):
        """Create a VirtualPrinterManager instance."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterManager

        return VirtualPrinterManager()

    def test_manager_starts_empty(self, manager):
        """Verify manager starts with no instances."""
        assert len(manager._instances) == 0
        assert manager.is_enabled is False

    def test_manager_get_status_empty(self, manager):
        """Verify get_status returns disabled state when no instances."""
        status = manager.get_status()
        assert status["enabled"] is False
        assert status["running"] is False
        assert status["mode"] == "immediate"

    def test_manager_is_enabled_with_instance(self, manager, tmp_path):
        """Verify is_enabled is True when instances exist."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        inst = VirtualPrinterInstance(
            vp_id=1,
            name="Test",
            mode="immediate",
            model="C11",
            access_code="12345678",
            serial_suffix="391800001",
            base_dir=tmp_path,
        )
        manager._instances[1] = inst
        assert manager.is_enabled is True

    @pytest.mark.asyncio
    async def test_manager_remove_instance_server(self, manager, tmp_path):
        """Verify remove_instance stops and removes a server-mode instance."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        inst = VirtualPrinterInstance(
            vp_id=1,
            name="Test",
            mode="immediate",
            model="C11",
            access_code="12345678",
            serial_suffix="391800001",
            base_dir=tmp_path,
        )
        inst.stop_server = AsyncMock()
        manager._instances[1] = inst

        await manager.remove_instance(1)

        assert 1 not in manager._instances
        inst.stop_server.assert_called_once()

    @pytest.mark.asyncio
    async def test_manager_remove_instance_proxy(self, manager, tmp_path):
        """Verify remove_instance stops proxy-mode instance."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        inst = VirtualPrinterInstance(
            vp_id=2,
            name="Proxy",
            mode="proxy",
            model="C11",
            access_code="",
            serial_suffix="391800002",
            target_printer_ip="192.168.1.100",
            base_dir=tmp_path,
        )
        inst.stop_proxy = AsyncMock()
        manager._instances[2] = inst

        await manager.remove_instance(2)

        assert 2 not in manager._instances
        inst.stop_proxy.assert_called_once()

    def test_manager_get_status_with_instance(self, manager, tmp_path):
        """Verify legacy get_status returns first instance data."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        inst = VirtualPrinterInstance(
            vp_id=1,
            name="Bambuddy",
            mode="immediate",
            model="C11",
            access_code="12345678",
            serial_suffix="391800001",
            base_dir=tmp_path,
        )
        mock_task = MagicMock(done=MagicMock(return_value=False))
        inst._tasks = [mock_task]
        inst._pending_files = {"file1.3mf": Path("/tmp/file1.3mf")}
        manager._instances[1] = inst

        status = manager.get_status()
        assert status["enabled"] is True
        assert status["running"] is True
        assert status["mode"] == "immediate"
        assert status["name"] == "Bambuddy"
        assert status["serial"] == "01S00A391800001"
        assert status["model"] == "C11"
        assert status["model_name"] == "P1P"
        assert status["pending_files"] == 1

    def test_manager_get_all_status(self, manager, tmp_path):
        """Verify get_all_status returns status for all instances."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        for i in range(1, 3):
            inst = VirtualPrinterInstance(
                vp_id=i,
                name=f"VP{i}",
                mode="immediate",
                model="C11",
                access_code="12345678",
                serial_suffix=f"39180000{i}",
                base_dir=tmp_path,
            )
            manager._instances[i] = inst

        statuses = manager.get_all_status()
        assert len(statuses) == 2
        assert statuses[0]["name"] == "VP1"
        assert statuses[1]["name"] == "VP2"

    @pytest.mark.asyncio
    async def test_manager_stop_all(self, manager, tmp_path):
        """Verify stop_all removes all instances."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        for i in range(1, 3):
            inst = VirtualPrinterInstance(
                vp_id=i,
                name=f"VP{i}",
                mode="immediate",
                model="C11",
                access_code="12345678",
                serial_suffix=f"39180000{i}",
                base_dir=tmp_path,
            )
            inst.stop_server = AsyncMock()
            manager._instances[i] = inst

        await manager.stop_all()
        assert len(manager._instances) == 0


class TestFTPSession:
    """Tests for FTP session handling."""

    @pytest.fixture
    def mock_reader(self):
        """Create a mock StreamReader."""
        reader = AsyncMock()
        return reader

    @pytest.fixture
    def mock_writer(self):
        """Create a mock StreamWriter."""
        writer = MagicMock()
        writer.get_extra_info = MagicMock(return_value=("192.168.1.100", 12345))
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        writer.is_closing = MagicMock(return_value=False)
        return writer

    @pytest.fixture
    def ssl_context(self):
        """Create a mock SSL context."""
        return MagicMock()

    @pytest.fixture
    def session(self, mock_reader, mock_writer, ssl_context, tmp_path):
        """Create an FTPSession instance."""
        from backend.app.services.virtual_printer.ftp_server import FTPSession

        return FTPSession(
            reader=mock_reader,
            writer=mock_writer,
            upload_dir=tmp_path,
            access_code="12345678",
            ssl_context=ssl_context,
            on_file_received=None,
        )

    # ========================================================================
    # Tests for authentication
    # ========================================================================

    @pytest.mark.asyncio
    async def test_user_command_accepts_bblp(self, session):
        """Verify USER command accepts bblp user."""
        await session.cmd_USER("bblp")

        assert session.username == "bblp"

    @pytest.mark.asyncio
    async def test_pass_command_authenticates(self, session):
        """Verify PASS command authenticates with correct code."""
        session.username = "bblp"

        await session.cmd_PASS("12345678")

        assert session.authenticated is True

    @pytest.mark.asyncio
    async def test_pass_command_rejects_wrong_code(self, session):
        """Verify PASS command rejects wrong access code."""
        session.username = "bblp"

        await session.cmd_PASS("wrongcode")

        assert session.authenticated is False

    # ========================================================================
    # Tests for FTP commands
    # ========================================================================

    @pytest.mark.asyncio
    async def test_syst_command(self, session):
        """Verify SYST returns UNIX type."""
        await session.cmd_SYST("")

        session.writer.write.assert_called()
        call_args = session.writer.write.call_args[0][0].decode()
        assert "215" in call_args
        assert "UNIX" in call_args

    @pytest.mark.asyncio
    async def test_pwd_command_requires_auth(self, session):
        """Verify PWD requires authentication."""
        session.authenticated = False

        await session.cmd_PWD("")

        call_args = session.writer.write.call_args[0][0].decode()
        assert "530" in call_args

    @pytest.mark.asyncio
    async def test_pwd_command_when_authenticated(self, session):
        """Verify PWD returns root directory when authenticated."""
        session.authenticated = True

        await session.cmd_PWD("")

        call_args = session.writer.write.call_args[0][0].decode()
        assert "257" in call_args

    @pytest.mark.asyncio
    async def test_type_command_sets_binary(self, session):
        """Verify TYPE I sets binary mode."""
        session.authenticated = True

        await session.cmd_TYPE("I")

        assert session.transfer_type == "I"

    @pytest.mark.asyncio
    async def test_pbsz_command(self, session):
        """Verify PBSZ returns success."""
        await session.cmd_PBSZ("0")

        call_args = session.writer.write.call_args[0][0].decode()
        assert "200" in call_args

    @pytest.mark.asyncio
    async def test_prot_command_accepts_p(self, session):
        """Verify PROT P is accepted."""
        await session.cmd_PROT("P")

        call_args = session.writer.write.call_args[0][0].decode()
        assert "200" in call_args

    @pytest.mark.asyncio
    async def test_quit_command(self, session):
        """Verify QUIT sends goodbye and raises CancelledError."""
        with pytest.raises(asyncio.CancelledError):
            await session.cmd_QUIT("")


class TestSSDPServer:
    """Tests for Virtual Printer SSDP server."""

    @pytest.fixture
    def ssdp_server(self):
        """Create a VirtualPrinterSSDPServer instance."""
        from backend.app.services.virtual_printer.ssdp_server import VirtualPrinterSSDPServer

        return VirtualPrinterSSDPServer(
            serial="TEST123",
            name="TestPrinter",
            model="BL-P001",
        )

    # ========================================================================
    # Tests for SSDP response
    # ========================================================================

    def test_build_notify_message(self, ssdp_server):
        """Verify NOTIFY packet contains required headers."""
        # Set a known IP for testing
        ssdp_server._local_ip = "192.168.1.100"

        message = ssdp_server._build_notify_message()

        assert b"NOTIFY" in message
        assert b"DevName.bambu.com: TestPrinter" in message
        assert b"USN: TEST123" in message

    def test_build_response_message(self, ssdp_server):
        """Verify response packet contains required headers."""
        # Set a known IP for testing
        ssdp_server._local_ip = "192.168.1.100"

        message = ssdp_server._build_response_message()

        assert b"HTTP/1.1 200 OK" in message
        assert b"DevName.bambu.com: TestPrinter" in message
        assert b"USN: TEST123" in message

    def test_ssdp_server_uses_correct_model(self, ssdp_server):
        """Verify SSDP server uses the provided model."""
        ssdp_server._local_ip = "192.168.1.100"

        message = ssdp_server._build_notify_message()

        assert b"DevModel.bambu.com: BL-P001" in message

    # ========================================================================
    # Tests for advertise_ip parameter
    # ========================================================================

    def test_advertise_ip_sets_local_ip(self):
        """Verify advertise_ip overrides auto-detection."""
        from backend.app.services.virtual_printer.ssdp_server import VirtualPrinterSSDPServer

        server = VirtualPrinterSSDPServer(
            serial="TEST123",
            name="TestPrinter",
            model="BL-P001",
            advertise_ip="10.0.0.50",
        )

        assert server._local_ip == "10.0.0.50"

    def test_advertise_ip_empty_string_uses_auto_detect(self):
        """Verify empty advertise_ip falls back to auto-detection."""
        from backend.app.services.virtual_printer.ssdp_server import VirtualPrinterSSDPServer

        server = VirtualPrinterSSDPServer(
            serial="TEST123",
            name="TestPrinter",
            model="BL-P001",
            advertise_ip="",
        )

        assert server._local_ip is None

    def test_advertise_ip_in_notify_message(self):
        """Verify NOTIFY message uses the advertise_ip."""
        from backend.app.services.virtual_printer.ssdp_server import VirtualPrinterSSDPServer

        server = VirtualPrinterSSDPServer(
            serial="TEST123",
            name="TestPrinter",
            model="BL-P001",
            advertise_ip="10.0.0.50",
        )

        message = server._build_notify_message()

        assert b"Location: 10.0.0.50" in message

    def test_advertise_ip_in_response_message(self):
        """Verify M-SEARCH response uses the advertise_ip."""
        from backend.app.services.virtual_printer.ssdp_server import VirtualPrinterSSDPServer

        server = VirtualPrinterSSDPServer(
            serial="TEST123",
            name="TestPrinter",
            model="BL-P001",
            advertise_ip="10.0.0.50",
        )

        message = server._build_response_message()

        assert b"Location: 10.0.0.50" in message

    def test_default_no_advertise_ip(self):
        """Verify default constructor has None local_ip (auto-detect)."""
        from backend.app.services.virtual_printer.ssdp_server import VirtualPrinterSSDPServer

        server = VirtualPrinterSSDPServer()

        assert server._local_ip is None


class TestCertificateService:
    """Tests for TLS certificate generation."""

    @pytest.fixture
    def cert_service(self, tmp_path):
        """Create a CertificateService instance."""
        from backend.app.services.virtual_printer.certificate import CertificateService

        return CertificateService(cert_dir=tmp_path, serial="TEST123")

    def test_generate_certificates(self, cert_service, tmp_path):
        """Verify certificates are generated correctly."""
        cert_path, key_path = cert_service.generate_certificates()

        assert cert_path.exists()
        assert key_path.exists()

        # Verify certificate content
        cert_content = cert_path.read_text()
        assert "BEGIN CERTIFICATE" in cert_content

        key_content = key_path.read_text()
        assert "BEGIN" in key_content and "KEY" in key_content

    def test_certificates_reused_if_exist(self, cert_service):
        """Verify existing certificates are reused."""
        # First generation
        cert_path1, key_path1 = cert_service.generate_certificates()
        mtime1 = cert_path1.stat().st_mtime

        # Second call should reuse (via ensure_certificates)
        cert_path2, key_path2 = cert_service.ensure_certificates()
        mtime2 = cert_path2.stat().st_mtime

        assert mtime1 == mtime2  # File wasn't regenerated

    def test_delete_certificates(self, cert_service):
        """Verify certificates can be deleted."""
        cert_service.generate_certificates()

        assert cert_service.cert_path.exists()
        assert cert_service.key_path.exists()

        cert_service.delete_certificates()

        assert not cert_service.cert_path.exists()
        assert not cert_service.key_path.exists()

    def test_ensure_creates_if_not_exist(self, cert_service):
        """Verify ensure_certificates generates if not existing."""
        assert not cert_service.cert_path.exists()

        cert_path, key_path = cert_service.ensure_certificates()

        assert cert_path.exists()
        assert key_path.exists()


class TestBindServer:
    """Tests for BindServer (port 3002 bind/detect protocol)."""

    @pytest.fixture
    def bind_server(self):
        """Create a BindServer instance."""
        from backend.app.services.virtual_printer.bind_server import BindServer

        return BindServer(
            serial="09400A391800001",
            model="O1D",
            name="Bambuddy",
        )

    def test_build_frame(self, bind_server):
        """Verify frame building produces correct format."""
        payload = {"login": {"command": "detect"}}
        frame = bind_server._build_frame(payload)

        # Header: 0xA5A5
        assert frame[:2] == b"\xa5\xa5"
        # Trailer: 0xA7A7
        assert frame[-2:] == b"\xa7\xa7"
        # Length field is total message size (LE uint16)
        import struct

        total_len = struct.unpack_from("<H", frame, 2)[0]
        assert total_len == len(frame)
        # JSON payload is between header and trailer
        import json

        json_bytes = frame[4:-2]
        parsed = json.loads(json_bytes)
        assert parsed == payload

    def test_parse_frame_valid(self, bind_server):
        """Verify valid frame parsing extracts JSON correctly."""
        import json
        import struct

        payload = {"login": {"command": "detect", "sequence_id": "20000"}}
        json_bytes = json.dumps(payload, separators=(",", ":")).encode()
        total_len = 4 + len(json_bytes) + 2
        frame = b"\xa5\xa5" + struct.pack("<H", total_len) + json_bytes + b"\xa7\xa7"

        result = bind_server._parse_frame(frame)

        assert result is not None
        assert result["login"]["command"] == "detect"
        assert result["login"]["sequence_id"] == "20000"

    def test_parse_frame_invalid_header(self, bind_server):
        """Verify invalid header returns None."""
        result = bind_server._parse_frame(b"\xbb\xbb\x06\x00{}\xa7\xa7")
        assert result is None

    def test_parse_frame_invalid_trailer(self, bind_server):
        """Verify invalid trailer returns None."""
        result = bind_server._parse_frame(b"\xa5\xa5\x06\x00{}\xbb\xbb")
        assert result is None

    def test_parse_frame_too_short(self, bind_server):
        """Verify short data returns None."""
        result = bind_server._parse_frame(b"\xa5\xa5\x00")
        assert result is None

    def test_parse_frame_invalid_json(self, bind_server):
        """Verify invalid JSON returns None."""
        import struct

        bad_json = b"not json"
        total_len = 4 + len(bad_json) + 2
        frame = b"\xa5\xa5" + struct.pack("<H", total_len) + bad_json + b"\xa7\xa7"
        result = bind_server._parse_frame(frame)
        assert result is None

    def test_build_frame_roundtrip(self, bind_server):
        """Verify build_frame output can be parsed back."""
        payload = {
            "login": {
                "bind": "free",
                "command": "detect",
                "connect": "lan",
                "dev_cap": 1,
                "id": "09400A391800001",
                "model": "O1D",
                "name": "Bambuddy",
                "sequence_id": 3021,
                "version": "01.00.00.00",
            }
        }
        frame = bind_server._build_frame(payload)
        parsed = bind_server._parse_frame(frame)

        assert parsed is not None
        assert parsed["login"]["id"] == "09400A391800001"
        assert parsed["login"]["model"] == "O1D"
        assert parsed["login"]["name"] == "Bambuddy"
        assert parsed["login"]["bind"] == "free"

    def test_bind_server_stores_config(self, bind_server):
        """Verify bind server stores serial, model, name."""
        assert bind_server.serial == "09400A391800001"
        assert bind_server.model == "O1D"
        assert bind_server.name == "Bambuddy"
        assert bind_server.version == "01.00.00.00"

    def test_bind_server_custom_version(self):
        """Verify custom firmware version is stored."""
        from backend.app.services.virtual_printer.bind_server import BindServer

        server = BindServer(
            serial="TEST123",
            model="C13",
            name="Test",
            version="02.03.04.05",
        )
        assert server.version == "02.03.04.05"

    def test_bind_ports_constant(self):
        """Verify BIND_PORTS includes both 3000 and 3002 for slicer compatibility."""
        from backend.app.services.virtual_printer.bind_server import BIND_PORTS

        assert 3000 in BIND_PORTS
        assert 3002 in BIND_PORTS

    def test_bind_server_initializes_empty_servers_list(self, bind_server):
        """Verify bind server starts with empty servers list."""
        assert bind_server._servers == []
        assert bind_server._running is False


class TestSlicerProxyManager:
    """Tests for SlicerProxyManager (proxy mode)."""

    @pytest.fixture
    def proxy_manager(self, tmp_path):
        """Create a SlicerProxyManager instance."""
        from backend.app.services.virtual_printer.tcp_proxy import SlicerProxyManager

        # Create dummy cert files
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"
        cert_path.write_text("-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----")
        # Split string to avoid pre-commit hook false positive on test data
        key_path.write_text("-----BEGIN " + "PRIVATE KEY-----\ntest\n-----END " + "PRIVATE KEY-----")

        return SlicerProxyManager(
            target_host="192.168.1.100",
            cert_path=cert_path,
            key_path=key_path,
        )

    def test_proxy_manager_initializes_ports(self, proxy_manager):
        """Verify proxy manager has correct port constants."""
        # FTP proxy uses privileged port 990 to match what Bambu Studio expects
        assert proxy_manager.LOCAL_FTP_PORT == 990
        assert proxy_manager.LOCAL_MQTT_PORT == 8883
        assert proxy_manager.PRINTER_FTP_PORT == 990
        assert proxy_manager.PRINTER_MQTT_PORT == 8883
        # Bind ports: both 3000 and 3002 for slicer compatibility
        assert proxy_manager.PRINTER_BIND_PORTS == [3000, 3002]

    def test_proxy_manager_stores_target_host(self, proxy_manager):
        """Verify proxy manager stores target host."""
        assert proxy_manager.target_host == "192.168.1.100"

    def test_get_status_before_start(self, proxy_manager):
        """Verify get_status returns zeros before start."""
        status = proxy_manager.get_status()

        assert status["running"] is False
        assert status["ftp_connections"] == 0
        assert status["mqtt_connections"] == 0


class TestSSDPProxy:
    """Tests for SSDPProxy (cross-network SSDP relay)."""

    @pytest.fixture
    def ssdp_proxy(self):
        """Create an SSDPProxy instance."""
        from backend.app.services.virtual_printer.ssdp_server import SSDPProxy

        return SSDPProxy(
            local_interface_ip="192.168.1.100",
            remote_interface_ip="10.0.0.100",
            target_printer_ip="192.168.1.50",
        )

    def test_ssdp_proxy_stores_interface_ips(self, ssdp_proxy):
        """Verify SSDPProxy stores interface IPs correctly."""
        assert ssdp_proxy.local_interface_ip == "192.168.1.100"
        assert ssdp_proxy.remote_interface_ip == "10.0.0.100"
        assert ssdp_proxy.target_printer_ip == "192.168.1.50"

    def test_rewrite_ssdp_location(self, ssdp_proxy):
        """Verify SSDP Location header is rewritten to remote interface IP."""
        original_packet = b"NOTIFY * HTTP/1.1\r\nLocation: 192.168.1.50\r\nDevName.bambu.com: TestPrinter\r\n\r\n"

        rewritten = ssdp_proxy._rewrite_ssdp(original_packet)

        # Location should be changed to remote interface IP
        assert b"Location: 10.0.0.100" in rewritten
        assert b"Location: 192.168.1.50" not in rewritten
        # Other headers should be preserved
        assert b"DevName.bambu.com: TestPrinter" in rewritten

    def test_rewrite_ssdp_location_case_insensitive(self, ssdp_proxy):
        """Verify SSDP Location rewrite is case insensitive."""
        original_packet = b"NOTIFY * HTTP/1.1\r\nlocation: 192.168.1.50\r\n\r\n"

        rewritten = ssdp_proxy._rewrite_ssdp(original_packet)

        assert b"10.0.0.100" in rewritten

    def test_rewrite_ssdp_location_no_match(self, ssdp_proxy):
        """Verify packet without Location header is returned unchanged."""
        original_packet = b"NOTIFY * HTTP/1.1\r\nDevName.bambu.com: Test\r\n\r\n"

        rewritten = ssdp_proxy._rewrite_ssdp(original_packet)

        # No Location header, but _rewrite_ssdp logs a warning and returns as-is
        assert b"DevName.bambu.com: Test" in rewritten

    def test_parse_ssdp_message(self, ssdp_proxy):
        """Verify SSDP message parsing extracts headers."""
        packet = (
            b"NOTIFY * HTTP/1.1\r\n"
            b"Location: 192.168.1.50\r\n"
            b"DevName.bambu.com: TestPrinter\r\n"
            b"DevModel.bambu.com: BL-P001\r\n"
            b"\r\n"
        )

        headers = ssdp_proxy._parse_ssdp_message(packet)

        assert headers["location"] == "192.168.1.50"
        assert headers["devname.bambu.com"] == "TestPrinter"
        assert headers["devmodel.bambu.com"] == "BL-P001"


class TestVirtualPrinterManagerDirectories:
    """Tests for VirtualPrinterManager directory management."""

    def test_ensure_base_directories_creates_subdirs(self, tmp_path):
        """Verify _ensure_base_directories creates required base directories."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterManager

        manager = VirtualPrinterManager()
        manager._base_dir = tmp_path / "virtual_printer"
        manager._ensure_base_directories()

        assert (tmp_path / "virtual_printer").exists()
        assert (tmp_path / "virtual_printer" / "uploads").exists()
        assert (tmp_path / "virtual_printer" / "certs").exists()

    def test_ensure_base_directories_handles_permission_error(self, tmp_path, caplog):
        """Verify _ensure_base_directories logs error on permission failure."""
        import logging

        from backend.app.services.virtual_printer.manager import VirtualPrinterManager

        manager = VirtualPrinterManager()
        vp_dir = tmp_path / "virtual_printer"
        manager._base_dir = vp_dir

        original_mkdir = type(vp_dir).mkdir

        def mock_mkdir(self, *args, **kwargs):
            if "virtual_printer" in str(self):
                raise PermissionError("Permission denied")
            return original_mkdir(self, *args, **kwargs)

        with caplog.at_level(logging.ERROR), patch.object(type(vp_dir), "mkdir", mock_mkdir):
            manager._ensure_base_directories()
            assert "Permission denied" in caplog.text

    def test_instance_creates_per_vp_directories(self, tmp_path):
        """Verify VirtualPrinterInstance creates per-VP upload and cert dirs."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        VirtualPrinterInstance(
            vp_id=42,
            name="Test",
            mode="immediate",
            model="C11",
            access_code="12345678",
            serial_suffix="391800042",
            base_dir=tmp_path,
        )

        assert (tmp_path / "uploads" / "42").exists()
        assert (tmp_path / "uploads" / "42" / "cache").exists()
        assert (tmp_path / "certs" / "42").exists()


class TestVirtualPrinterInstanceProxyMode:
    """Tests for VirtualPrinterInstance proxy mode."""

    @pytest.fixture
    def proxy_instance(self, tmp_path):
        """Create a proxy-mode VirtualPrinterInstance."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        return VirtualPrinterInstance(
            vp_id=10,
            name="ProxyTest",
            mode="proxy",
            model="C11",
            access_code="",
            serial_suffix="391800010",
            target_printer_ip="192.168.1.100",
            target_printer_serial="01P00A000000001",
            base_dir=tmp_path,
        )

    def test_proxy_instance_properties(self, proxy_instance):
        """Verify proxy instance stores config correctly."""
        assert proxy_instance.is_proxy is True
        assert proxy_instance.mode == "proxy"
        assert proxy_instance.target_printer_ip == "192.168.1.100"
        assert proxy_instance.target_printer_serial == "01P00A000000001"

    def test_proxy_instance_does_not_require_access_code(self, proxy_instance):
        """Verify proxy mode can have empty access code."""
        assert proxy_instance.access_code == ""

    def test_get_status_proxy_includes_proxy_fields(self, proxy_instance):
        """Verify get_status includes proxy fields when proxy is active."""
        mock_proxy = MagicMock()
        mock_proxy.get_status.return_value = {
            "running": True,
            "ftp_port": 990,
            "mqtt_port": 8883,
            "ftp_connections": 1,
            "mqtt_connections": 2,
            "target_host": "192.168.1.100",
        }
        proxy_instance._proxy = mock_proxy

        status = proxy_instance.get_status()
        assert "proxy" in status
        assert status["proxy"]["ftp_port"] == 990
        assert status["proxy"]["mqtt_connections"] == 2

    def test_proxy_instance_stores_remote_interface(self, tmp_path):
        """Verify proxy instance stores remote_interface_ip."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        inst = VirtualPrinterInstance(
            vp_id=11,
            name="Proxy2",
            mode="proxy",
            model="C11",
            access_code="",
            serial_suffix="391800011",
            target_printer_ip="192.168.1.100",
            remote_interface_ip="10.0.0.50",
            base_dir=tmp_path,
        )
        assert inst.remote_interface_ip == "10.0.0.50"


class TestVirtualPrinterInstanceIPOverride:
    """Tests for remote_interface_ip and bind_ip on VirtualPrinterInstance."""

    @pytest.fixture
    def instance_with_remote_ip(self, tmp_path):
        """Create an instance with remote_interface_ip set."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        return VirtualPrinterInstance(
            vp_id=20,
            name="IPTest",
            mode="immediate",
            model="3DPrinter-X1-Carbon",
            access_code="12345678",
            serial_suffix="391800020",
            bind_ip="192.168.1.50",
            remote_interface_ip="10.0.0.50",
            base_dir=tmp_path,
        )

    def test_instance_stores_bind_ip(self, instance_with_remote_ip):
        """Verify bind_ip is stored."""
        assert instance_with_remote_ip.bind_ip == "192.168.1.50"

    def test_instance_stores_remote_interface_ip(self, instance_with_remote_ip):
        """Verify remote_interface_ip is stored."""
        assert instance_with_remote_ip.remote_interface_ip == "10.0.0.50"

    def test_generate_certificates_includes_remote_and_bind_ip(self, instance_with_remote_ip):
        """Verify generate_certificates passes remote_interface_ip and bind_ip as SANs."""
        with (
            patch.object(instance_with_remote_ip._cert_service, "delete_printer_certificate"),
            patch.object(
                instance_with_remote_ip._cert_service,
                "generate_certificates",
                return_value=(Path("/tmp/cert.pem"), Path("/tmp/key.pem")),
            ) as mock_gen,
        ):
            instance_with_remote_ip.generate_certificates()
            mock_gen.assert_called_once_with(additional_ips=["10.0.0.50", "192.168.1.50"])

    def test_generate_certificates_no_remote_ip(self, tmp_path):
        """Verify generate_certificates passes only bind_ip when no remote_interface_ip."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        inst = VirtualPrinterInstance(
            vp_id=21,
            name="NoRemote",
            mode="immediate",
            model="3DPrinter-X1-Carbon",
            access_code="12345678",
            serial_suffix="391800021",
            bind_ip="192.168.1.50",
            base_dir=tmp_path,
        )

        with (
            patch.object(inst._cert_service, "delete_printer_certificate"),
            patch.object(
                inst._cert_service,
                "generate_certificates",
                return_value=(Path("/tmp/cert.pem"), Path("/tmp/key.pem")),
            ) as mock_gen,
        ):
            inst.generate_certificates()
            mock_gen.assert_called_once_with(additional_ips=["192.168.1.50"])

    def test_generate_certificates_no_ips(self, tmp_path):
        """Verify generate_certificates passes None when no IPs configured."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        inst = VirtualPrinterInstance(
            vp_id=22,
            name="NoIPs",
            mode="immediate",
            model="3DPrinter-X1-Carbon",
            access_code="12345678",
            serial_suffix="391800022",
            base_dir=tmp_path,
        )

        with (
            patch.object(inst._cert_service, "delete_printer_certificate"),
            patch.object(
                inst._cert_service,
                "generate_certificates",
                return_value=(Path("/tmp/cert.pem"), Path("/tmp/key.pem")),
            ) as mock_gen,
        ):
            inst.generate_certificates()
            mock_gen.assert_called_once_with(additional_ips=None)


class TestBindServer:
    """Tests for the BindServer (port 3002 bind/detect protocol)."""

    @pytest.fixture
    def bind_server(self):
        """Create a BindServer instance."""
        from backend.app.services.virtual_printer.bind_server import BindServer

        return BindServer(
            serial="01S00C000000001",
            model="3DPrinter-X1-Carbon",
            name="Bambuddy",
        )

    def test_build_frame(self, bind_server):
        """Verify frame format: 0xA5A5 + len(u16le) + JSON + 0xA7A7."""
        payload = {"login": {"command": "detect"}}
        frame = bind_server._build_frame(payload)

        assert frame[:2] == b"\xa5\xa5"
        assert frame[-2:] == b"\xa7\xa7"

        # Length field is total message size
        import struct

        total_len = struct.unpack_from("<H", frame, 2)[0]
        assert total_len == len(frame)

        # JSON payload is between header and trailer
        import json

        json_bytes = frame[4:-2]
        parsed = json.loads(json_bytes)
        assert parsed == payload

    def test_parse_frame_valid(self, bind_server):
        """Verify valid frame parsing."""
        frame = bind_server._build_frame({"login": {"command": "detect", "sequence_id": "20000"}})
        result = bind_server._parse_frame(frame)

        assert result is not None
        assert result["login"]["command"] == "detect"
        assert result["login"]["sequence_id"] == "20000"

    def test_parse_frame_invalid_header(self, bind_server):
        """Verify invalid header returns None."""
        frame = b"\xb5\xb5\x10\x00" + b'{"login":{}}' + b"\xa7\xa7"
        assert bind_server._parse_frame(frame) is None

    def test_parse_frame_invalid_trailer(self, bind_server):
        """Verify invalid trailer returns None."""
        frame = b"\xa5\xa5\x10\x00" + b'{"login":{}}' + b"\xb7\xb7"
        assert bind_server._parse_frame(frame) is None

    def test_parse_frame_too_short(self, bind_server):
        """Verify short data returns None."""
        assert bind_server._parse_frame(b"\xa5\xa5\x00") is None
        assert bind_server._parse_frame(b"") is None

    def test_parse_frame_invalid_json(self, bind_server):
        """Verify invalid JSON returns None."""
        import struct

        bad_json = b"not json"
        total_len = 4 + len(bad_json) + 2
        frame = b"\xa5\xa5" + struct.pack("<H", total_len) + bad_json + b"\xa7\xa7"
        assert bind_server._parse_frame(frame) is None

    def test_build_frame_roundtrip(self, bind_server):
        """Verify build then parse roundtrip."""
        original = {"login": {"bind": "free", "command": "detect", "id": "01S00C000000001"}}
        frame = bind_server._build_frame(original)
        parsed = bind_server._parse_frame(frame)
        assert parsed == original

    def test_bind_server_stores_config(self, bind_server):
        """Verify config is stored correctly."""
        assert bind_server.serial == "01S00C000000001"
        assert bind_server.model == "3DPrinter-X1-Carbon"
        assert bind_server.name == "Bambuddy"
        assert bind_server.version == "01.00.00.00"

    def test_bind_server_custom_version(self):
        """Verify custom firmware version is stored."""
        from backend.app.services.virtual_printer.bind_server import BindServer

        server = BindServer(
            serial="01S00C000000001",
            model="3DPrinter-X1-Carbon",
            name="Bambuddy",
            version="01.09.00.10",
        )
        assert server.version == "01.09.00.10"

    def test_bind_ports_includes_both(self):
        """Verify BIND_PORTS includes both 3000 and 3002 for slicer compatibility."""
        from backend.app.services.virtual_printer.bind_server import BIND_PORTS

        assert 3000 in BIND_PORTS
        assert 3002 in BIND_PORTS

    def test_bind_server_initializes_empty_servers_list(self, bind_server):
        """Verify bind server starts with empty servers list."""
        assert bind_server._servers == []
        assert bind_server._running is False

    @pytest.mark.asyncio
    async def test_start_server_creates_bind_server(self, tmp_path):
        """Verify start_server creates BindServer with correct params."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterInstance

        inst = VirtualPrinterInstance(
            vp_id=99,
            name="Bambuddy",
            mode="immediate",
            model="3DPrinter-X1-Carbon",
            access_code="12345678",
            serial_suffix="391800099",
            bind_ip="192.168.1.50",
            base_dir=tmp_path,
        )

        with (
            patch("backend.app.services.virtual_printer.manager.VirtualPrinterSSDPServer"),
            patch("backend.app.services.virtual_printer.manager.VirtualPrinterFTPServer"),
            patch("backend.app.services.virtual_printer.manager.SimpleMQTTServer"),
            patch("backend.app.services.virtual_printer.manager.BindServer") as mock_bind_cls,
            patch.object(inst._cert_service, "delete_printer_certificate"),
            patch.object(
                inst._cert_service,
                "generate_certificates",
                return_value=(Path("/tmp/cert.pem"), Path("/tmp/key.pem")),  # nosec B108
            ),
        ):
            await inst.start_server()

            mock_bind_cls.assert_called_once_with(
                serial=inst.serial,
                model="3DPrinter-X1-Carbon",
                name="Bambuddy",
                bind_address="192.168.1.50",
            )
