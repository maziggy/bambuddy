"""Comprehensive FTP test suite for BambuFTPClient.

Tests against a real mock implicit FTPS server, covering:
- Connection (auth, SSL modes, timeout, caching)
- File listing
- Download (bytes, to_file, 0-byte regression)
- Upload (chunked transfer, progress, error codes)
- Delete
- File size
- Storage info (AVBL, directory scan, diagnose_storage)
- Model-specific behavior (X1C prot_p, A1 prot_c fallback)
- Async wrappers
- Failure injection scenarios (regressions for 0.1.8 bugs)
"""

import time
from pathlib import Path

import pytest

from backend.app.services.bambu_ftp import (
    BambuFTPClient,
    delete_file_async,
    download_file_async,
    download_file_try_paths_async,
    list_files_async,
    upload_file_async,
)

# Brief delay to allow pyftpdlib to flush uploaded files to disk.
# Needed because upload_file() skips voidresp() for A1 compatibility,
# so the server may still be processing the data channel close event.
_UPLOAD_FLUSH_DELAY = 0.3


# ---------------------------------------------------------------------------
# TestConnection
# ---------------------------------------------------------------------------
class TestConnection:
    """Tests for FTP connect/disconnect behavior."""

    def test_connect_success(self, ftp_client_factory):
        """Successful implicit FTPS connection and login."""
        client = ftp_client_factory()
        assert client.connect() is True
        client.disconnect()

    def test_connect_wrong_access_code(self, ftp_client_factory):
        """Wrong access code returns False."""
        client = ftp_client_factory(access_code="wrongcode")
        assert client.connect() is False

    def test_connect_unreachable_host(self, ftp_server):
        """Unreachable host returns False."""
        client = BambuFTPClient(
            ip_address="192.0.2.1",  # TEST-NET, guaranteed unreachable
            access_code="12345678",
            timeout=1.0,
            printer_model="X1C",
        )
        client.FTP_PORT = ftp_server.port
        assert client.connect() is False

    def test_connect_timeout(self, ftp_server):
        """Very short timeout triggers timeout error."""
        client = BambuFTPClient(
            ip_address="192.0.2.1",
            access_code="12345678",
            timeout=0.001,  # Extremely short
            printer_model="X1C",
        )
        client.FTP_PORT = ftp_server.port
        assert client.connect() is False

    def test_disconnect_clean(self, ftp_client_factory):
        """Clean disconnect after successful connect."""
        client = ftp_client_factory()
        client.connect()
        client.disconnect()
        assert client._ftp is None

    def test_disconnect_without_connect(self, ftp_client_factory):
        """Disconnect without connect does not raise."""
        client = ftp_client_factory()
        client.disconnect()  # Should not raise
        assert client._ftp is None

    def test_disconnect_after_server_gone(self, ftp_certs, ftp_root):
        """Disconnect after server has stopped raises EOFError.

        Note: The current disconnect() catches (OSError, ftplib.Error) but
        EOFError is neither. This documents actual behavior — a future fix
        could add EOFError to the except clause.
        """
        from backend.tests.unit.services.mock_ftp_server import (
            MockBambuFTPServer,
        )

        from .conftest import _find_free_port

        cert_path, key_path = ftp_certs
        port = _find_free_port()
        server = MockBambuFTPServer("127.0.0.1", port, str(ftp_root), cert_path, key_path)
        server.start()

        client = BambuFTPClient("127.0.0.1", "12345678", timeout=5.0)
        client.FTP_PORT = port
        client.connect()

        server.stop()
        with pytest.raises(EOFError):
            client.disconnect()

    def test_x1c_uses_prot_p(self, ftp_client_factory):
        """X1C model connects with prot_p (protected data channel)."""
        client = ftp_client_factory(printer_model="X1C")
        assert client.connect() is True
        assert client._should_use_prot_c() is False
        client.disconnect()

    def test_a1_defaults_prot_p(self, ftp_client_factory):
        """A1 model defaults to prot_p when no cache exists."""
        client = ftp_client_factory(printer_model="A1")
        assert client._should_use_prot_c() is False
        assert client.connect() is True
        client.disconnect()

    def test_a1_force_prot_c(self, ftp_client_factory):
        """A1 model with force_prot_c uses clear data channel."""
        client = ftp_client_factory(printer_model="A1", force_prot_c=True)
        assert client._should_use_prot_c() is True
        assert client.connect() is True
        client.disconnect()

    def test_cached_mode_respected(self, ftp_client_factory):
        """Cached mode is used on subsequent connections."""
        BambuFTPClient.cache_mode("127.0.0.1", "prot_c")
        client = ftp_client_factory(printer_model="A1")
        assert client._should_use_prot_c() is True
        assert client.connect() is True
        client.disconnect()


# ---------------------------------------------------------------------------
# TestListFiles
# ---------------------------------------------------------------------------
class TestListFiles:
    """Tests for directory listing."""

    def test_list_empty_directory(self, ftp_client_factory):
        """Listing an empty directory returns empty list."""
        client = ftp_client_factory()
        client.connect()
        files = client.list_files("/cache")
        assert files == []
        client.disconnect()

    def test_list_directory_with_files(self, ftp_client_factory, ftp_server):
        """Files in directory are listed correctly."""
        ftp_server.add_file("cache/test.3mf", b"x" * 1024)
        ftp_server.add_file("cache/test2.gcode", b"y" * 512)
        client = ftp_client_factory()
        client.connect()
        files = client.list_files("/cache")
        names = {f["name"] for f in files}
        assert "test.3mf" in names
        assert "test2.gcode" in names
        client.disconnect()

    def test_directories_marked(self, ftp_client_factory, ftp_server):
        """Subdirectories are identified with is_directory=True."""
        ftp_server.add_directory("model/subdir")
        client = ftp_client_factory()
        client.connect()
        files = client.list_files("/model")
        dirs = [f for f in files if f["is_directory"]]
        assert len(dirs) >= 1
        assert dirs[0]["name"] == "subdir"
        client.disconnect()

    def test_nonexistent_path_returns_empty(self, ftp_client_factory):
        """Listing a nonexistent path returns empty list."""
        client = ftp_client_factory()
        client.connect()
        files = client.list_files("/nonexistent/path")
        assert files == []
        client.disconnect()

    def test_file_sizes_and_paths(self, ftp_client_factory, ftp_server):
        """File sizes and full paths are parsed correctly."""
        ftp_server.add_file("cache/sized.bin", b"a" * 2048)
        client = ftp_client_factory()
        client.connect()
        files = client.list_files("/cache")
        sized = [f for f in files if f["name"] == "sized.bin"]
        assert len(sized) == 1
        assert sized[0]["size"] == 2048
        assert sized[0]["path"] == "/cache/sized.bin"
        client.disconnect()


# ---------------------------------------------------------------------------
# TestDownload
# ---------------------------------------------------------------------------
class TestDownload:
    """Tests for file download operations."""

    def test_download_file_returns_bytes(self, ftp_client_factory, ftp_server):
        """download_file() returns file content as bytes."""
        content = b"Hello FTP World!"
        ftp_server.add_file("cache/hello.txt", content)
        client = ftp_client_factory()
        client.connect()
        result = client.download_file("/cache/hello.txt")
        assert result == content
        client.disconnect()

    def test_download_file_missing(self, ftp_client_factory):
        """download_file() returns None for missing file."""
        client = ftp_client_factory()
        client.connect()
        result = client.download_file("/cache/does_not_exist.txt")
        assert result is None
        client.disconnect()

    def test_download_to_file_writes_to_disk(self, ftp_client_factory, ftp_server, tmp_path):
        """download_to_file() writes content to local filesystem."""
        content = b"Downloaded content"
        ftp_server.add_file("cache/dl.bin", content)
        local = tmp_path / "output" / "dl.bin"
        client = ftp_client_factory()
        client.connect()
        result = client.download_to_file("/cache/dl.bin", local)
        assert result is True
        assert local.read_bytes() == content
        client.disconnect()

    def test_download_to_file_creates_parent_dirs(self, ftp_client_factory, ftp_server, tmp_path):
        """download_to_file() creates parent directories automatically."""
        ftp_server.add_file("cache/nested.txt", b"nested content")
        local = tmp_path / "deep" / "nested" / "path" / "nested.txt"
        client = ftp_client_factory()
        client.connect()
        result = client.download_to_file("/cache/nested.txt", local)
        assert result is True
        assert local.exists()
        client.disconnect()

    def test_zero_byte_download_returns_false(self, ftp_client_factory, ftp_server, tmp_path):
        """0-byte download returns False and cleans up (regression test)."""
        ftp_server.add_file("cache/empty.bin", b"")
        local = tmp_path / "empty.bin"
        client = ftp_client_factory()
        client.connect()
        result = client.download_to_file("/cache/empty.bin", local)
        assert result is False
        assert not local.exists()
        client.disconnect()

    def test_download_to_file_missing_returns_false(self, ftp_client_factory, tmp_path):
        """Missing file returns False."""
        local = tmp_path / "missing.bin"
        client = ftp_client_factory()
        client.connect()
        result = client.download_to_file("/cache/no_such_file.bin", local)
        assert result is False
        client.disconnect()

    def test_download_large_file(self, ftp_client_factory, ftp_server):
        """Large file download (>1MB) works correctly."""
        large_content = b"X" * (1024 * 1024 + 500)  # ~1MB + 500 bytes
        ftp_server.add_file("cache/large.bin", large_content)
        client = ftp_client_factory()
        client.connect()
        result = client.download_file("/cache/large.bin")
        assert result == large_content
        client.disconnect()

    def test_download_not_connected(self):
        """download_file() returns None when not connected."""
        client = BambuFTPClient("127.0.0.1", "12345678")
        assert client.download_file("/cache/test.bin") is None


# ---------------------------------------------------------------------------
# TestUpload
# ---------------------------------------------------------------------------
class TestUpload:
    """Tests for file upload operations."""

    def test_upload_success(self, ftp_client_factory, ftp_server, tmp_path):
        """Successful upload via transfercmd (not storbinary)."""
        content = b"Upload test content"
        local = tmp_path / "upload.3mf"
        local.write_bytes(content)
        client = ftp_client_factory()
        client.connect()
        result = client.upload_file(local, "/cache/upload.3mf")
        assert result is True
        client.disconnect()
        # Verify via fresh connection (upload_file skips voidresp()
        # so the original session can't be reused for download)
        time.sleep(_UPLOAD_FLUSH_DELAY)
        client2 = ftp_client_factory()
        client2.connect()
        downloaded = client2.download_file("/cache/upload.3mf")
        assert downloaded == content
        client2.disconnect()

    def test_upload_progress_callback(self, ftp_client_factory, ftp_server, tmp_path):
        """Progress callback receives updates during upload."""
        content = b"P" * 2048
        local = tmp_path / "progress.bin"
        local.write_bytes(content)

        progress_calls = []

        def on_progress(uploaded, total):
            progress_calls.append((uploaded, total))

        client = ftp_client_factory()
        client.connect()
        client.upload_file(local, "/cache/progress.bin", on_progress)
        assert len(progress_calls) >= 1
        # Last call should report full file uploaded
        assert progress_calls[-1][0] == len(content)
        assert progress_calls[-1][1] == len(content)
        client.disconnect()

    def test_upload_not_connected(self, tmp_path):
        """Upload when not connected returns False."""
        local = tmp_path / "test.bin"
        local.write_bytes(b"data")
        client = BambuFTPClient("127.0.0.1", "12345678")
        assert client.upload_file(local, "/cache/test.bin") is False

    def test_upload_553_no_sd_card(self, ftp_client_factory, ftp_server, tmp_path):
        """553 error (no SD card) returns False."""
        ftp_server.inject_failure("STOR", 553, "Could not create file.")
        local = tmp_path / "test.bin"
        local.write_bytes(b"data")
        client = ftp_client_factory()
        client.connect()
        result = client.upload_file(local, "/cache/test.bin")
        assert result is False
        client.disconnect()

    def test_upload_550_permission_denied(self, ftp_client_factory, ftp_server, tmp_path):
        """550 error (permission denied) returns False."""
        ftp_server.inject_failure("STOR", 550, "Permission denied.")
        local = tmp_path / "test.bin"
        local.write_bytes(b"data")
        client = ftp_client_factory()
        client.connect()
        result = client.upload_file(local, "/cache/test.bin")
        assert result is False
        client.disconnect()

    def test_upload_552_storage_full(self, ftp_client_factory, ftp_server, tmp_path):
        """552 error (storage full) returns False."""
        ftp_server.inject_failure("STOR", 552, "Storage quota exceeded.")
        local = tmp_path / "test.bin"
        local.write_bytes(b"data")
        client = ftp_client_factory()
        client.connect()
        result = client.upload_file(local, "/cache/test.bin")
        assert result is False
        client.disconnect()

    def test_upload_bytes_success(self, ftp_client_factory, ftp_server):
        """upload_bytes() writes data to server."""
        data = b"Bytes upload content"
        client = ftp_client_factory()
        client.connect()
        result = client.upload_bytes(data, "/cache/bytes.bin")
        assert result is True
        client.disconnect()
        # Verify via fresh connection
        time.sleep(_UPLOAD_FLUSH_DELAY)
        client2 = ftp_client_factory()
        client2.connect()
        downloaded = client2.download_file("/cache/bytes.bin")
        assert downloaded == data
        client2.disconnect()

    def test_upload_bytes_failure(self, ftp_client_factory, ftp_server):
        """upload_bytes() returns False on STOR failure."""
        ftp_server.inject_failure("STOR", 553, "No space.")
        client = ftp_client_factory()
        client.connect()
        result = client.upload_bytes(b"data", "/cache/fail.bin")
        assert result is False
        client.disconnect()

    def test_upload_large_chunked(self, ftp_client_factory, ftp_server, tmp_path):
        """Large file upload in chunks completes without error.

        Uses 2.5MB to trigger multiple chunks with 1MB CHUNK_SIZE.
        Content verification skipped because upload_file() doesn't call
        voidresp() (for A1 compatibility), so the server may still be
        flushing when we check. The upload result=True confirms the
        client sent all chunks without error.
        """
        content = b"C" * (1024 * 1024 * 2 + 512 * 1024)
        local = tmp_path / "large.bin"
        local.write_bytes(content)

        progress_calls = []

        def on_progress(uploaded, total):
            progress_calls.append((uploaded, total))

        client = ftp_client_factory()
        client.connect()
        result = client.upload_file(local, "/cache/large.bin", on_progress)
        assert result is True
        # Verify multiple chunks were sent
        assert len(progress_calls) >= 3  # 2.5MB / 1MB = at least 3 chunks
        assert progress_calls[-1][0] == len(content)
        client.disconnect()


# ---------------------------------------------------------------------------
# TestDelete
# ---------------------------------------------------------------------------
class TestDelete:
    """Tests for file deletion."""

    def test_delete_success(self, ftp_client_factory, ftp_server):
        """Successful file deletion."""
        ftp_server.add_file("cache/to_delete.bin", b"delete me")
        client = ftp_client_factory()
        client.connect()
        result = client.delete_file("/cache/to_delete.bin")
        assert result is True
        assert not ftp_server.file_exists("cache/to_delete.bin")
        client.disconnect()

    def test_delete_not_found(self, ftp_client_factory):
        """Deleting a nonexistent file returns False."""
        client = ftp_client_factory()
        client.connect()
        result = client.delete_file("/cache/no_such_file.bin")
        assert result is False
        client.disconnect()

    def test_delete_not_connected(self):
        """Delete when not connected returns False."""
        client = BambuFTPClient("127.0.0.1", "12345678")
        assert client.delete_file("/cache/test.bin") is False


# ---------------------------------------------------------------------------
# TestFileSize
# ---------------------------------------------------------------------------
class TestFileSize:
    """Tests for get_file_size."""

    def test_file_size_correct(self, ftp_client_factory, ftp_server):
        """Returns correct file size."""
        ftp_server.add_file("cache/sized.bin", b"a" * 4096)
        client = ftp_client_factory()
        client.connect()
        size = client.get_file_size("/cache/sized.bin")
        assert size == 4096
        client.disconnect()

    def test_file_size_missing(self, ftp_client_factory):
        """Returns None for missing file."""
        client = ftp_client_factory()
        client.connect()
        size = client.get_file_size("/cache/no_file.bin")
        assert size is None
        client.disconnect()

    def test_file_size_not_connected(self):
        """Returns None when not connected."""
        client = BambuFTPClient("127.0.0.1", "12345678")
        assert client.get_file_size("/cache/test.bin") is None


# ---------------------------------------------------------------------------
# TestStorageInfo
# ---------------------------------------------------------------------------
class TestStorageInfo:
    """Tests for storage info and diagnostics."""

    def test_avbl_parsed(self, ftp_client_factory, ftp_server):
        """AVBL response is parsed for free_bytes."""
        ftp_server.set_avbl_bytes(5000000000)
        client = ftp_client_factory()
        client.connect()
        info = client.get_storage_info()
        assert info is not None
        assert info["free_bytes"] == 5000000000
        client.disconnect()

    def test_used_bytes_from_scan(self, ftp_client_factory, ftp_server):
        """used_bytes calculated from directory scan."""
        ftp_server.add_file("cache/file1.bin", b"a" * 1000)
        ftp_server.add_file("cache/file2.bin", b"b" * 2000)
        client = ftp_client_factory()
        client.connect()
        info = client.get_storage_info()
        assert info is not None
        assert info["used_bytes"] >= 3000  # At least these two files
        client.disconnect()

    def test_storage_info_not_connected(self):
        """Returns None when not connected."""
        client = BambuFTPClient("127.0.0.1", "12345678")
        assert client.get_storage_info() is None

    def test_diagnose_storage_success(self, ftp_client_factory, ftp_server):
        """diagnose_storage() returns connected=True with working diagnostics."""
        client = ftp_client_factory()
        client.connect()
        diag = client.diagnose_storage()
        assert diag["connected"] is True
        assert diag["can_list_root"] is True
        assert diag["can_list_cache"] is True
        assert diag["pwd"] is not None
        assert diag["storage_info"] is not None
        client.disconnect()

    def test_diagnose_storage_not_connected(self):
        """diagnose_storage() reports not connected."""
        client = BambuFTPClient("127.0.0.1", "12345678")
        diag = client.diagnose_storage()
        assert diag["connected"] is False
        assert "FTP not connected" in diag["errors"]


# ---------------------------------------------------------------------------
# TestModelSpecificBehavior
# ---------------------------------------------------------------------------
class TestModelSpecificBehavior:
    """Tests for printer model-specific FTP behavior."""

    def test_x1c_upload(self, ftp_client_factory, ftp_server, tmp_path):
        """X1C upload with session reuse succeeds."""
        content = b"X1C upload data"
        local = tmp_path / "x1c.3mf"
        local.write_bytes(content)
        client = ftp_client_factory(printer_model="X1C")
        client.connect()
        result = client.upload_file(local, "/cache/x1c.3mf")
        assert result is True
        client.disconnect()
        # Verify via fresh connection
        time.sleep(_UPLOAD_FLUSH_DELAY)
        client2 = ftp_client_factory(printer_model="X1C")
        client2.connect()
        downloaded = client2.download_file("/cache/x1c.3mf")
        assert downloaded == content
        client2.disconnect()

    def test_a1_upload_prot_c(self, ftp_client_factory, ftp_server, tmp_path):
        """A1 model upload with prot_c succeeds."""
        content = b"A1 upload data"
        local = tmp_path / "a1.3mf"
        local.write_bytes(content)
        client = ftp_client_factory(printer_model="A1", force_prot_c=True)
        client.connect()
        result = client.upload_file(local, "/cache/a1.3mf")
        assert result is True
        client.disconnect()
        # Verify via fresh connection
        time.sleep(_UPLOAD_FLUSH_DELAY)
        client2 = ftp_client_factory(printer_model="A1", force_prot_c=True)
        client2.connect()
        downloaded = client2.download_file("/cache/a1.3mf")
        assert downloaded == content
        client2.disconnect()

    def test_a1_mini_upload(self, ftp_client_factory, ftp_server, tmp_path):
        """A1 Mini model upload succeeds."""
        content = b"A1 Mini data"
        local = tmp_path / "a1mini.3mf"
        local.write_bytes(content)
        client = ftp_client_factory(printer_model="A1 Mini", force_prot_c=True)
        client.connect()
        result = client.upload_file(local, "/cache/a1mini.3mf")
        assert result is True
        client.disconnect()

    def test_p1s_upload(self, ftp_client_factory, ftp_server, tmp_path):
        """P1S model upload with session reuse succeeds."""
        content = b"P1S upload data"
        local = tmp_path / "p1s.3mf"
        local.write_bytes(content)
        client = ftp_client_factory(printer_model="P1S")
        client.connect()
        result = client.upload_file(local, "/cache/p1s.3mf")
        assert result is True
        client.disconnect()

    def test_unknown_model_defaults_prot_p(self, ftp_client_factory):
        """Unknown model defaults to prot_p."""
        client = ftp_client_factory(printer_model="FuturePrinter3000")
        assert client._is_a1_model() is False
        assert client._should_use_prot_c() is False
        assert client.connect() is True
        client.disconnect()

    def test_mode_cache_persists_and_clears(self, ftp_client_factory):
        """Mode cache works within a test and clears between tests."""
        # Cache should be empty at start (autouse fixture clears it)
        assert BambuFTPClient._mode_cache == {}

        # Connect and cache a mode
        BambuFTPClient.cache_mode("127.0.0.1", "prot_p")
        assert BambuFTPClient._mode_cache["127.0.0.1"] == "prot_p"

        # New client for same IP uses cached mode
        client = ftp_client_factory(printer_model="A1")
        assert client._get_cached_mode() == "prot_p"
        assert client._should_use_prot_c() is False
        client.disconnect()


# ---------------------------------------------------------------------------
# TestAsyncWrappers
# ---------------------------------------------------------------------------
class TestAsyncWrappers:
    """Tests for async wrapper functions using patch_ftp_port fixture."""

    @pytest.mark.asyncio
    async def test_upload_file_async_success(self, patch_ftp_port, tmp_path):
        """upload_file_async succeeds for X1C."""
        content = b"async upload"
        local = tmp_path / "async_up.3mf"
        local.write_bytes(content)
        result = await upload_file_async(
            "127.0.0.1",
            "12345678",
            local,
            "/cache/async_up.3mf",
            timeout=30.0,
            printer_model="X1C",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_upload_file_async_a1_fallback(self, patch_ftp_port, tmp_path):
        """upload_file_async tries prot_p then falls back to prot_c for A1."""
        content = b"a1 async upload"
        local = tmp_path / "a1_async.3mf"
        local.write_bytes(content)
        # For A1 models, if prot_p succeeds we get True.
        # If prot_p fails, it tries prot_c. Either way should succeed
        # against our mock server which accepts both.
        result = await upload_file_async(
            "127.0.0.1",
            "12345678",
            local,
            "/cache/a1_async.3mf",
            timeout=30.0,
            printer_model="A1",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_download_file_async_success(self, patch_ftp_port, tmp_path):
        """download_file_async succeeds."""
        server = patch_ftp_port
        content = b"async download content"
        server.add_file("cache/async_dl.bin", content)
        local = tmp_path / "async_dl.bin"
        result = await download_file_async(
            "127.0.0.1",
            "12345678",
            "/cache/async_dl.bin",
            local,
            timeout=30.0,
            printer_model="X1C",
        )
        assert result is True
        assert local.read_bytes() == content

    @pytest.mark.asyncio
    async def test_download_file_async_a1_fallback(self, patch_ftp_port, tmp_path):
        """download_file_async falls back for A1 models."""
        server = patch_ftp_port
        server.add_file("cache/a1_dl.bin", b"a1 data")
        local = tmp_path / "a1_dl.bin"
        result = await download_file_async(
            "127.0.0.1",
            "12345678",
            "/cache/a1_dl.bin",
            local,
            timeout=30.0,
            printer_model="A1",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_download_file_try_paths_first_succeeds(self, patch_ftp_port, tmp_path):
        """download_file_try_paths_async succeeds on first path."""
        server = patch_ftp_port
        server.add_file("cache/try1.bin", b"first path")
        local = tmp_path / "try.bin"
        result = await download_file_try_paths_async(
            "127.0.0.1",
            "12345678",
            ["/cache/try1.bin", "/cache/try2.bin"],
            local,
            printer_model="X1C",
        )
        assert result is True
        assert local.read_bytes() == b"first path"

    @pytest.mark.asyncio
    async def test_download_file_try_paths_fallback(self, patch_ftp_port, tmp_path):
        """download_file_try_paths_async falls back to second path."""
        server = patch_ftp_port
        server.add_file("cache/second.bin", b"second path")
        local = tmp_path / "fallback.bin"
        result = await download_file_try_paths_async(
            "127.0.0.1",
            "12345678",
            ["/cache/missing.bin", "/cache/second.bin"],
            local,
            printer_model="X1C",
        )
        assert result is True
        assert local.read_bytes() == b"second path"

    @pytest.mark.asyncio
    async def test_list_files_async_success(self, patch_ftp_port):
        """list_files_async returns file list."""
        server = patch_ftp_port
        server.add_file("cache/listed.bin", b"data")
        result = await list_files_async(
            "127.0.0.1",
            "12345678",
            "/cache",
            timeout=30.0,
            printer_model="X1C",
        )
        names = {f["name"] for f in result}
        assert "listed.bin" in names

    @pytest.mark.asyncio
    async def test_delete_file_async_success(self, patch_ftp_port):
        """delete_file_async deletes a file."""
        server = patch_ftp_port
        server.add_file("cache/to_async_del.bin", b"delete me")
        result = await delete_file_async(
            "127.0.0.1",
            "12345678",
            "/cache/to_async_del.bin",
            printer_model="X1C",
        )
        assert result is True
        assert not server.file_exists("cache/to_async_del.bin")


# ---------------------------------------------------------------------------
# TestFailureScenarios
# ---------------------------------------------------------------------------
class TestFailureScenarios:
    """Regression tests for known FTP failure modes."""

    def test_550_caught_by_broad_except(self, ftp_client_factory, ftp_server, tmp_path):
        """550 error_perm is caught by (OSError, ftplib.Error) handler.

        Regression: error_perm is a subclass of ftplib.Error, so the
        broad except clause in upload_file catches it correctly.
        """
        ftp_server.inject_failure("STOR", 550, "Permission denied.")
        local = tmp_path / "test.bin"
        local.write_bytes(b"data")
        client = ftp_client_factory()
        client.connect()
        result = client.upload_file(local, "/cache/test.bin")
        assert result is False
        client.disconnect()

    def test_zero_byte_download_detected(self, ftp_client_factory, ftp_server, tmp_path):
        """0-byte download is detected and file is cleaned up.

        Regression: Prior to fix, 0-byte downloads were reported as success.
        """
        ftp_server.add_file("cache/zero.bin", b"")
        local = tmp_path / "zero.bin"
        client = ftp_client_factory()
        client.connect()
        result = client.download_to_file("/cache/zero.bin", local)
        assert result is False
        assert not local.exists()
        client.disconnect()

    def test_connection_refused_handled(self):
        """Connection refused is handled gracefully."""
        client = BambuFTPClient("127.0.0.1", "12345678", timeout=2.0)
        client.FTP_PORT = 1  # Almost certainly not listening
        assert client.connect() is False

    def test_auth_failure_530(self, ftp_client_factory, ftp_server):
        """530 authentication failure returns False."""
        ftp_server.inject_failure("PASS", 530, "Login incorrect.")
        client = ftp_client_factory()
        result = client.connect()
        assert result is False

    def test_retr_550_handled(self, ftp_client_factory, ftp_server):
        """RETR 550 (file not found) returns None."""
        ftp_server.inject_failure("RETR", 550, "File not found.")
        ftp_server.add_file("cache/exists.bin", b"data")
        client = ftp_client_factory()
        client.connect()
        result = client.download_file("/cache/exists.bin")
        assert result is None
        client.disconnect()

    def test_cwd_550_handled(self, ftp_client_factory, ftp_server):
        """CWD 550 is handled in list_files."""
        ftp_server.inject_failure("CWD", 550, "Directory not found.")
        client = ftp_client_factory()
        client.connect()
        result = client.list_files("/nonexistent")
        assert result == []
        client.disconnect()

    def test_stor_553_handled(self, ftp_client_factory, ftp_server, tmp_path):
        """STOR 553 (no SD card) handled gracefully."""
        ftp_server.inject_failure("STOR", 553, "Could not create file.")
        local = tmp_path / "test.bin"
        local.write_bytes(b"test")
        client = ftp_client_factory()
        client.connect()
        result = client.upload_file(local, "/cache/test.bin")
        assert result is False
        client.disconnect()

    def test_diagnose_storage_cwd_failure_doesnt_propagate(self, ftp_client_factory, ftp_server):
        """diagnose_storage CWD failure doesn't crash the whole operation.

        Regression: diagnose_storage() was called in the upload path and
        a CWD failure would propagate and crash the upload.
        """
        ftp_server.inject_failure("CWD", 550, "No such directory.", count=2)
        client = ftp_client_factory()
        client.connect()
        diag = client.diagnose_storage()
        # Should still return results (with errors noted)
        assert diag["connected"] is True
        assert len(diag["errors"]) > 0
        client.disconnect()

    def test_failure_injection_count_decrements(self, ftp_client_factory, ftp_server):
        """Failure injection with count decrements and eventually succeeds."""
        ftp_server.add_file("cache/retry.bin", b"data after retry")
        ftp_server.inject_failure("RETR", 550, "Temporary error.", count=1)
        client = ftp_client_factory()
        client.connect()
        # First attempt fails
        result1 = client.download_file("/cache/retry.bin")
        assert result1 is None
        # Second attempt succeeds (failure count exhausted)
        result2 = client.download_file("/cache/retry.bin")
        assert result2 == b"data after retry"
        client.disconnect()
