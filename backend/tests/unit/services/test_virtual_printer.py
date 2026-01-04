"""Unit tests for Virtual Printer services.

Tests the virtual printer manager, FTP server, and SSDP server components.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestVirtualPrinterManager:
    """Tests for VirtualPrinterManager class."""

    @pytest.fixture
    def manager(self):
        """Create a VirtualPrinterManager instance."""
        from backend.app.services.virtual_printer.manager import VirtualPrinterManager

        return VirtualPrinterManager()

    # ========================================================================
    # Tests for configuration
    # ========================================================================

    @pytest.mark.asyncio
    async def test_configure_sets_parameters(self, manager):
        """Verify configure stores parameters correctly."""
        # Mock the start/stop methods to avoid actually starting services
        manager._start = AsyncMock()

        await manager.configure(
            enabled=True,
            access_code="12345678",
            mode="immediate",
        )

        assert manager._enabled is True
        assert manager._access_code == "12345678"
        assert manager._mode == "immediate"

    @pytest.mark.asyncio
    async def test_configure_disabled_stops_services(self, manager):
        """Verify disabling stops all services."""
        # First simulate enabled state
        manager._enabled = True
        manager._tasks = [MagicMock(done=MagicMock(return_value=False))]
        manager._stop = AsyncMock()

        await manager.configure(enabled=False, access_code="12345678")

        assert manager._enabled is False
        manager._stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_configure_requires_access_code_when_enabling(self, manager):
        """Verify access code is required when enabling."""
        with pytest.raises(ValueError, match="Access code is required"):
            await manager.configure(enabled=True)

    @pytest.mark.asyncio
    async def test_configure_sets_model(self, manager):
        """Verify configure stores model correctly."""
        manager._start = AsyncMock()

        await manager.configure(
            enabled=True,
            access_code="12345678",
            mode="immediate",
            model="C11",  # P1S model code
        )

        assert manager._model == "C11"

    @pytest.mark.asyncio
    async def test_configure_ignores_invalid_model(self, manager):
        """Verify configure ignores invalid model codes."""
        manager._start = AsyncMock()

        await manager.configure(
            enabled=True,
            access_code="12345678",
            model="INVALID",
        )

        # Should keep default model (3DPrinter-X1-Carbon = X1C)
        assert manager._model == "3DPrinter-X1-Carbon"

    @pytest.mark.asyncio
    async def test_configure_restarts_on_model_change(self, manager):
        """Verify model change restarts services when running."""
        # Simulate running state
        manager._enabled = True
        manager._model = "3DPrinter-X1-Carbon"
        manager._tasks = [MagicMock(done=MagicMock(return_value=False))]
        manager._stop = AsyncMock()
        manager._start = AsyncMock()

        await manager.configure(
            enabled=True,
            access_code="12345678",
            model="C11",  # P1P
        )

        # Should have stopped and started
        manager._stop.assert_called_once()
        manager._start.assert_called_once()

    # ========================================================================
    # Tests for status
    # ========================================================================

    def test_get_status_returns_correct_format(self, manager):
        """Verify get_status returns expected fields."""
        manager._enabled = True
        manager._mode = "immediate"
        manager._model = "C11"  # P1P
        manager._pending_files = {"file1.3mf": Path("/tmp/file1.3mf")}
        # Simulate running tasks
        manager._tasks = [MagicMock(done=MagicMock(return_value=False))]

        status = manager.get_status()

        assert status["enabled"] is True
        assert status["running"] is True
        assert status["mode"] == "immediate"
        assert status["name"] == "Bambuddy"
        assert status["serial"] == "01S00A391800001"  # C11 (P1P) serial prefix
        assert status["model"] == "C11"
        assert status["model_name"] == "P1P"
        assert status["pending_files"] == 1

    def test_get_status_when_stopped(self, manager):
        """Verify get_status when not running."""
        manager._enabled = False
        manager._tasks = []

        status = manager.get_status()

        assert status["enabled"] is False
        assert status["running"] is False

    def test_is_running_with_active_tasks(self, manager):
        """Verify is_running is True when tasks are active."""
        mock_task = MagicMock()
        mock_task.done.return_value = False
        manager._tasks = [mock_task]

        assert manager.is_running is True

    def test_is_running_with_no_tasks(self, manager):
        """Verify is_running is False when no tasks."""
        manager._tasks = []

        assert manager.is_running is False

    # ========================================================================
    # Tests for file handling
    # ========================================================================

    @pytest.mark.asyncio
    async def test_on_file_received_adds_to_pending(self, manager):
        """Verify received file is added to pending list."""
        manager._mode = "queue"
        manager._session_factory = None  # Disable actual archiving

        file_path = Path("/tmp/test.3mf")

        with patch.object(manager, "_queue_file", new_callable=AsyncMock) as mock_queue:
            await manager._on_file_received(file_path, "192.168.1.100")

            assert "test.3mf" in manager._pending_files
            mock_queue.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_file_received_archives_immediately(self, manager):
        """Verify file is archived in immediate mode."""
        manager._mode = "immediate"
        manager._session_factory = None  # Will prevent actual archiving

        file_path = Path("/tmp/test.3mf")

        with patch.object(manager, "_archive_file", new_callable=AsyncMock) as mock_archive:
            await manager._on_file_received(file_path, "192.168.1.100")

            mock_archive.assert_called_once_with(file_path, "192.168.1.100")

    @pytest.mark.asyncio
    async def test_archive_file_skips_non_3mf(self, manager):
        """Verify non-3MF files are skipped and cleaned up."""
        manager._session_factory = MagicMock()
        manager._pending_files["verify_job"] = Path("/tmp/verify_job")

        with patch("pathlib.Path.unlink"):
            await manager._archive_file(Path("/tmp/verify_job"), "192.168.1.100")

            # Should be removed from pending
            assert "verify_job" not in manager._pending_files


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
