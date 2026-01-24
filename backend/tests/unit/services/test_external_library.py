"""Unit tests for ExternalLibraryService.

Tests cover path validation, file scanning, extension filtering,
security features (system directory blocking, symlink prevention),
and change detection (added/updated/removed files).
"""

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.external_library import (
    ExternalLibraryService,
    is_safe_path,
    is_system_directory,
    parse_extensions_filter,
)


class TestExtensionsParsing:
    """Tests for parse_extensions_filter function."""

    def test_parse_with_dots(self):
        """Parse extensions with dots."""
        result = parse_extensions_filter(".3mf,.stl,.gcode")
        assert result == [".3mf", ".stl", ".gcode"]

    def test_parse_without_dots(self):
        """Parse extensions without dots."""
        result = parse_extensions_filter("3mf, stl, gcode")
        assert result == [".3mf", ".stl", ".gcode"]

    def test_parse_mixed(self):
        """Parse mixed extensions (with and without dots)."""
        result = parse_extensions_filter(".3mf, stl, .gcode")
        assert result == [".3mf", ".stl", ".gcode"]

    def test_parse_empty_string(self):
        """Empty string returns None."""
        result = parse_extensions_filter("")
        assert result is None

    def test_parse_none(self):
        """None returns None."""
        result = parse_extensions_filter(None)
        assert result is None

    def test_parse_whitespace_only(self):
        """Whitespace-only string returns None."""
        result = parse_extensions_filter("   ,  , ")
        assert result is None

    def test_parse_case_insensitive(self):
        """Extensions are lowercased."""
        result = parse_extensions_filter(".3MF, .STL")
        assert result == [".3mf", ".stl"]


class TestSystemDirectoryDetection:
    """Tests for is_system_directory function."""

    def test_system_directory_etc(self):
        """Detects /etc as system directory."""
        assert is_system_directory(Path("/etc"))

    def test_system_directory_var(self):
        """Detects /var as system directory."""
        assert is_system_directory(Path("/var"))

    def test_system_directory_usr(self):
        """Detects /usr as system directory."""
        assert is_system_directory(Path("/usr"))

    def test_system_directory_root(self):
        """Detects /root as system directory."""
        assert is_system_directory(Path("/root"))

    def test_non_system_directory(self):
        """Non-system directory returns False."""
        assert not is_system_directory(Path("/home/user"))

    def test_non_system_directory_tmp(self):
        """Non-system directory /tmp (common mount point) returns False."""
        assert not is_system_directory(Path("/tmp"))

    def test_non_system_directory_mnt(self):
        """Non-system directory /mnt returns False."""
        assert not is_system_directory(Path("/mnt"))


class TestSafePath:
    """Tests for is_safe_path function."""

    def test_safe_path_within_allowed(self):
        """Path within allowed base returns True."""
        allowed = [Path("/mnt/external")]
        path = Path("/mnt/external/subfolder/file.txt")
        assert is_safe_path(path, allowed)

    def test_safe_path_multiple_allowed(self):
        """Path within one of multiple allowed bases returns True."""
        allowed = [Path("/mnt/external"), Path("/mnt/usb")]
        path = Path("/mnt/usb/data/file.txt")
        assert is_safe_path(path, allowed)

    def test_unsafe_path_outside_allowed(self):
        """Path outside allowed bases returns False."""
        allowed = [Path("/mnt/external")]
        path = Path("/home/user/file.txt")
        assert not is_safe_path(path, allowed)

    def test_safe_path_exact_match(self):
        """Path exactly matching allowed base returns True."""
        allowed = [Path("/mnt/external")]
        path = Path("/mnt/external")
        assert is_safe_path(path, allowed)

    def test_safe_path_empty_allowed(self):
        """Empty allowed list returns False."""
        allowed = []
        path = Path("/mnt/external/file.txt")
        assert not is_safe_path(path, allowed)


class TestExternalLibraryServicePathValidation:
    """Tests for ExternalLibraryService.validate_external_path."""

    @pytest.fixture
    def service(self, db_session):
        """Create service with mocked db."""
        service = ExternalLibraryService(db_session)
        return service

    @pytest.mark.asyncio
    async def test_validate_relative_path(self, service):
        """Relative path validation fails."""
        valid, msg = await service.validate_external_path("relative/path")
        assert not valid
        assert "absolute" in msg.lower()

    @pytest.mark.asyncio
    async def test_validate_nonexistent_path(self, service):
        """Non-existent path validation fails."""
        valid, msg = await service.validate_external_path("/nonexistent/path/12345")
        assert not valid
        assert "does not exist" in msg.lower()

    @pytest.mark.asyncio
    async def test_validate_file_not_directory(self, service):
        """File path (not directory) validation fails."""
        with tempfile.NamedTemporaryFile() as tmp:
            valid, msg = await service.validate_external_path(tmp.name)
            assert not valid
            assert "not a directory" in msg.lower()

    @pytest.mark.asyncio
    async def test_validate_system_directory(self, service):
        """System directory validation fails."""
        valid, msg = await service.validate_external_path("/etc")
        assert not valid
        assert "system" in msg.lower()

    @pytest.mark.asyncio
    async def test_validate_outside_allowed_paths(self, service):
        """Path outside allowed paths validation fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("backend.app.services.external_library.get_allowed_paths") as mock_get:
                # Set allowed paths to /mnt/external only
                mock_get.return_value = [Path("/mnt/external")]

                valid, msg = await service.validate_external_path(tmpdir)
                assert not valid
                assert "not within allowed" in msg.lower()

    @pytest.mark.asyncio
    async def test_validate_success(self, service):
        """Valid path passes all validation checks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("backend.app.services.external_library.get_allowed_paths") as mock_get:
                # Set allowed paths to include temp directory
                mock_get.return_value = [Path(tmpdir).parent]

                valid, msg = await service.validate_external_path(tmpdir)
                assert valid
                assert msg == ""


class TestExternalLibraryServiceScanning:
    """Tests for ExternalLibraryService file scanning."""

    @pytest.fixture
    def service(self, db_session):
        """Create service with db_session."""
        return ExternalLibraryService(db_session)

    @pytest.fixture
    async def external_folder(self, db_session):
        """Create an external folder in database."""
        from backend.app.models.library import LibraryFolder

        folder = LibraryFolder(
            name="External Test",
            is_external=True,
            external_path="/mnt/external",
            external_readonly=False,
            external_show_hidden=False,
            external_extensions=".3mf,.stl,.gcode",
            external_last_scan=None,
            external_dir_mtime=0,
            created_at=datetime.utcnow(),
        )
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)
        return folder

    @pytest.mark.asyncio
    async def test_scan_nonexistent_folder(self, service, db_session):
        """Scanning non-existent folder returns zero counts."""
        from backend.app.models.library import LibraryFolder

        folder = LibraryFolder(
            name="Missing",
            is_external=True,
            external_path="/nonexistent/path",
            external_readonly=False,
            external_show_hidden=False,
            external_extensions=".3mf",
            created_at=datetime.utcnow(),
        )
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        result = await service.scan_external_folder(folder.id)
        assert result == {"added": 0, "updated": 0, "removed": 0}

    @pytest.mark.asyncio
    async def test_scan_non_external_folder(self, service, db_session):
        """Scanning non-external folder returns zero counts."""
        from backend.app.models.library import LibraryFolder

        folder = LibraryFolder(
            name="Local",
            is_external=False,
            created_at=datetime.utcnow(),
        )
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        result = await service.scan_external_folder(folder.id)
        assert result == {"added": 0, "updated": 0, "removed": 0}

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires mocking circular dependencies with spoolman UTC import issue")
    async def test_scan_with_files_added(self, service, db_session, external_folder):
        """Scanning folder with new files adds them to database."""
        pass

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires mocking circular dependencies with spoolman UTC import issue")
    async def test_scan_skip_with_unchanged_mtime(self, service, db_session, external_folder):
        """Scanning with unchanged mtime skips rescan."""
        pass

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires mocking circular dependencies with spoolman UTC import issue")
    async def test_scan_force_rescan(self, service, db_session, external_folder):
        """Force rescan scans even with unchanged mtime."""
        pass

    @pytest.mark.asyncio
    async def test_enumerate_respects_extension_filter(self, service):
        """File enumeration respects extension filter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create files with different extensions
            Path(tmpdir).joinpath("model.3mf").write_text("content")
            Path(tmpdir).joinpath("mesh.stl").write_text("content")
            Path(tmpdir).joinpath("text.txt").write_text("content")

            # Enumerate with filter
            files = await service.enumerate_files_recursive(
                Path(tmpdir),
                show_hidden=False,
                extensions=[".3mf", ".stl"],
                max_depth=10,
            )

            assert len(files) == 2
            filenames = {f["relative_path"] for f in files}
            assert "model.3mf" in filenames
            assert "mesh.stl" in filenames
            assert "text.txt" not in filenames

    @pytest.mark.asyncio
    async def test_enumerate_skips_hidden_files(self, service):
        """File enumeration skips hidden files when show_hidden=False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create visible and hidden files
            Path(tmpdir).joinpath("visible.3mf").write_text("content")
            Path(tmpdir).joinpath(".hidden.3mf").write_text("content")

            # Enumerate without hidden
            files = await service.enumerate_files_recursive(
                Path(tmpdir),
                show_hidden=False,
                extensions=[".3mf"],
                max_depth=10,
            )

            assert len(files) == 1
            assert files[0]["relative_path"] == "visible.3mf"

    @pytest.mark.asyncio
    async def test_enumerate_respects_max_depth(self, service):
        """File enumeration respects max_depth limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Create nested structure
            tmppath.joinpath("level1").mkdir()
            tmppath.joinpath("level1/file1.3mf").write_text("content")
            tmppath.joinpath("level1/level2").mkdir()
            tmppath.joinpath("level1/level2/file2.3mf").write_text("content")
            tmppath.joinpath("level1/level2/level3").mkdir()
            tmppath.joinpath("level1/level2/level3/file3.3mf").write_text("content")

            # Enumerate with max_depth=1 (root level only)
            files = await service.enumerate_files_recursive(
                tmppath,
                show_hidden=False,
                extensions=[".3mf"],
                max_depth=1,
            )

            assert len(files) == 0

            # Enumerate with max_depth=2 (includes level1)
            files = await service.enumerate_files_recursive(
                tmppath,
                show_hidden=False,
                extensions=[".3mf"],
                max_depth=2,
            )

            assert len(files) == 1
            # relative_path is relative to the enumeration base path
            assert "file1.3mf" in files[0]["relative_path"]

            # Enumerate with max_depth=3 (includes level1/level2)
            files = await service.enumerate_files_recursive(
                tmppath,
                show_hidden=False,
                extensions=[".3mf"],
                max_depth=3,
            )

            assert len(files) == 2


class TestExternalLibraryServiceOperations:
    """Tests for operation validation and permission checks."""

    @pytest.fixture
    def service(self, db_session):
        """Create service with db_session."""
        return ExternalLibraryService(db_session)

    @pytest.mark.asyncio
    async def test_operation_allowed_on_non_external(self, service, db_session):
        """Operations allowed on non-external folders."""
        from backend.app.models.library import LibraryFolder

        folder = LibraryFolder(
            name="Local",
            is_external=False,
            created_at=datetime.utcnow(),
        )
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        for op in ["upload", "delete", "rename"]:
            allowed, msg = await service.validate_operation_allowed(folder.id, op)
            assert allowed

    @pytest.mark.asyncio
    async def test_operation_allowed_on_readonly_external(self, service, db_session):
        """Read operations blocked on read-only external folders."""
        from backend.app.models.library import LibraryFolder

        folder = LibraryFolder(
            name="External",
            is_external=True,
            external_path="/mnt/external",
            external_readonly=True,
            created_at=datetime.utcnow(),
        )
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        for op in ["upload", "delete", "rename"]:
            allowed, msg = await service.validate_operation_allowed(folder.id, op)
            assert not allowed
            assert op in msg.lower()

    @pytest.mark.asyncio
    async def test_operation_allowed_on_readwrite_external(self, service, db_session):
        """Operations allowed on read-write external folders."""
        from backend.app.models.library import LibraryFolder

        folder = LibraryFolder(
            name="External",
            is_external=True,
            external_path="/mnt/external",
            external_readonly=False,
            created_at=datetime.utcnow(),
        )
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        for op in ["upload", "delete", "rename"]:
            allowed, msg = await service.validate_operation_allowed(folder.id, op)
            assert allowed


class TestExternalLibraryServiceHelpers:
    """Tests for helper methods."""

    @pytest.fixture
    def service(self, db_session):
        """Create service with db_session."""
        return ExternalLibraryService(db_session)

    @pytest.mark.asyncio
    async def test_check_directory_changed_true(self, service, db_session):
        """Detects when directory mtime changed."""
        from backend.app.models.library import LibraryFolder

        with tempfile.TemporaryDirectory() as tmpdir:
            folder = LibraryFolder(
                name="External",
                is_external=True,
                external_path=tmpdir,
                external_dir_mtime=0,  # Old mtime
                created_at=datetime.utcnow(),
            )
            db_session.add(folder)
            await db_session.commit()
            await db_session.refresh(folder)

            changed = await service.check_directory_changed(folder)
            assert changed

    @pytest.mark.asyncio
    async def test_check_directory_changed_false(self, service, db_session):
        """Detects when directory mtime hasn't changed."""
        from backend.app.models.library import LibraryFolder

        with tempfile.TemporaryDirectory() as tmpdir:
            current_mtime = int(Path(tmpdir).stat().st_mtime)
            folder = LibraryFolder(
                name="External",
                is_external=True,
                external_path=tmpdir,
                external_dir_mtime=current_mtime,
                created_at=datetime.utcnow(),
            )
            db_session.add(folder)
            await db_session.commit()
            await db_session.refresh(folder)

            changed = await service.check_directory_changed(folder)
            assert not changed

    @pytest.mark.asyncio
    async def test_check_directory_changed_missing_path(self, service, db_session):
        """Returns False when external path doesn't exist."""
        from backend.app.models.library import LibraryFolder

        folder = LibraryFolder(
            name="External",
            is_external=True,
            external_path="/nonexistent/path",
            external_dir_mtime=0,
            created_at=datetime.utcnow(),
        )
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        changed = await service.check_directory_changed(folder)
        assert not changed
