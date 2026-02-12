"""Integration tests for external folder protection (delete, upload, move)."""

import os
import tempfile

import pytest
from httpx import AsyncClient


class TestExternalFolderDeleteProtection:
    """Tests for Issue #1: external file deletion must not remove files from filesystem by default."""

    @pytest.fixture
    async def folder_factory(self, db_session):
        """Factory to create test folders."""
        _counter = [0]

        async def _create_folder(**kwargs):
            from backend.app.models.library import LibraryFolder

            _counter[0] += 1
            counter = _counter[0]
            defaults = {"name": f"Test Folder {counter}"}
            defaults.update(kwargs)
            folder = LibraryFolder(**defaults)
            db_session.add(folder)
            await db_session.commit()
            await db_session.refresh(folder)
            return folder

        return _create_folder

    @pytest.fixture
    async def file_factory(self, db_session):
        """Factory to create test files."""
        _counter = [0]

        async def _create_file(**kwargs):
            from backend.app.models.library import LibraryFile

            _counter[0] += 1
            counter = _counter[0]
            defaults = {
                "filename": f"test_file_{counter}.3mf",
                "file_path": f"/test/path/test_file_{counter}.3mf",
                "file_size": 1024,
                "file_type": "3mf",
            }
            defaults.update(kwargs)
            lib_file = LibraryFile(**defaults)
            db_session.add(lib_file)
            await db_session.commit()
            await db_session.refresh(lib_file)
            return lib_file

        return _create_file

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_external_file_default_preserves_filesystem(
        self, async_client: AsyncClient, folder_factory, file_factory, db_session
    ):
        """Deleting an external file without delete_from_filesystem should NOT remove from disk."""
        with tempfile.NamedTemporaryFile(suffix=".3mf", delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name

        try:
            folder = await folder_factory(is_external=True, external_path="/tmp", external_readonly=False)
            lib_file = await file_factory(folder_id=folder.id, is_external=True, file_path=tmp_path)

            response = await async_client.delete(f"/api/v1/library/files/{lib_file.id}")
            assert response.status_code == 200
            # File should still exist on filesystem
            assert os.path.exists(tmp_path), "External file was deleted from filesystem when it should not have been"
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_external_file_with_filesystem_flag(
        self, async_client: AsyncClient, folder_factory, file_factory, db_session
    ):
        """Deleting an external file with delete_from_filesystem=True should remove from disk."""
        with tempfile.NamedTemporaryFile(suffix=".3mf", delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name

        try:
            folder = await folder_factory(is_external=True, external_path="/tmp", external_readonly=False)
            lib_file = await file_factory(folder_id=folder.id, is_external=True, file_path=tmp_path)

            response = await async_client.delete(
                f"/api/v1/library/files/{lib_file.id}?delete_from_filesystem=true"
            )
            assert response.status_code == 200
            # File should be deleted from filesystem
            assert not os.path.exists(tmp_path), "External file was NOT deleted from filesystem when it should have been"
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_external_file_readonly_with_filesystem_flag_blocked(
        self, async_client: AsyncClient, folder_factory, file_factory, db_session
    ):
        """Deleting from readonly external folder with delete_from_filesystem=True should return 403."""
        folder = await folder_factory(is_external=True, external_path="/tmp", external_readonly=True)
        lib_file = await file_factory(folder_id=folder.id, is_external=True, file_path="/tmp/fake.3mf")

        response = await async_client.delete(
            f"/api/v1/library/files/{lib_file.id}?delete_from_filesystem=true"
        )
        assert response.status_code == 403
        assert "read-only" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_local_file_always_removes_from_filesystem(
        self, async_client: AsyncClient, file_factory, db_session
    ):
        """Deleting a local (non-external) file should always remove from filesystem."""
        with tempfile.NamedTemporaryFile(suffix=".3mf", delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name

        try:
            lib_file = await file_factory(file_path=tmp_path)

            response = await async_client.delete(f"/api/v1/library/files/{lib_file.id}")
            assert response.status_code == 200
            # Local file should always be deleted from filesystem
            assert not os.path.exists(tmp_path), "Local file was NOT deleted from filesystem"
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_external_folder_preserves_files(
        self, async_client: AsyncClient, folder_factory, file_factory, db_session
    ):
        """Deleting an external folder should NOT remove files from disk by default."""
        with tempfile.NamedTemporaryFile(suffix=".3mf", delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name

        try:
            folder = await folder_factory(is_external=True, external_path="/tmp", external_readonly=False)
            await file_factory(folder_id=folder.id, is_external=True, file_path=tmp_path)

            response = await async_client.delete(f"/api/v1/library/folders/{folder.id}")
            assert response.status_code == 200
            # File should still exist on filesystem
            assert os.path.exists(tmp_path), "External file was deleted when folder was deleted"
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_external_folder_readonly_with_filesystem_flag_blocked(
        self, async_client: AsyncClient, folder_factory, db_session
    ):
        """Deleting readonly external folder with delete_from_filesystem=True should return 403."""
        folder = await folder_factory(is_external=True, external_path="/tmp", external_readonly=True)

        response = await async_client.delete(
            f"/api/v1/library/folders/{folder.id}?delete_from_filesystem=true"
        )
        assert response.status_code == 403
        assert "read-only" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_delete_external_files_preserves_filesystem(
        self, async_client: AsyncClient, folder_factory, file_factory, db_session
    ):
        """Bulk deleting external files without flag should NOT remove from disk."""
        with tempfile.NamedTemporaryFile(suffix=".3mf", delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name

        try:
            folder = await folder_factory(is_external=True, external_path="/tmp", external_readonly=False)
            lib_file = await file_factory(folder_id=folder.id, is_external=True, file_path=tmp_path)

            response = await async_client.post(
                "/api/v1/library/bulk-delete",
                json={"file_ids": [lib_file.id], "folder_ids": []},
            )
            assert response.status_code == 200
            assert os.path.exists(tmp_path), "External file was deleted during bulk delete"
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_delete_external_readonly_with_filesystem_flag_blocked(
        self, async_client: AsyncClient, folder_factory, file_factory, db_session
    ):
        """Bulk deleting from readonly external folder with flag should return 403."""
        folder = await folder_factory(is_external=True, external_path="/tmp", external_readonly=True)
        lib_file = await file_factory(folder_id=folder.id, is_external=True, file_path="/tmp/fake.3mf")

        response = await async_client.post(
            "/api/v1/library/bulk-delete",
            json={"file_ids": [lib_file.id], "folder_ids": [], "delete_from_filesystem": True},
        )
        assert response.status_code == 403
        assert "read-only" in response.json()["detail"].lower()


class TestExternalFolderUploadProtection:
    """Tests for Issue #2: upload validation for read-only external folders."""

    @pytest.fixture
    async def folder_factory(self, db_session):
        """Factory to create test folders."""
        _counter = [0]

        async def _create_folder(**kwargs):
            from backend.app.models.library import LibraryFolder

            _counter[0] += 1
            counter = _counter[0]
            defaults = {"name": f"Test Folder {counter}"}
            defaults.update(kwargs)
            folder = LibraryFolder(**defaults)
            db_session.add(folder)
            await db_session.commit()
            await db_session.refresh(folder)
            return folder

        return _create_folder

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_to_readonly_external_folder_blocked(
        self, async_client: AsyncClient, folder_factory, db_session
    ):
        """Uploading to a readonly external folder should return 403."""
        folder = await folder_factory(is_external=True, external_path="/tmp", external_readonly=True)

        files = {"file": ("test.3mf", b"fake 3mf content", "application/octet-stream")}
        response = await async_client.post(
            f"/api/v1/library/files?folder_id={folder.id}", files=files
        )
        assert response.status_code == 403
        assert "read-only" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_to_writable_external_folder_allowed(
        self, async_client: AsyncClient, folder_factory, db_session
    ):
        """Uploading to a writable external folder should succeed."""
        folder = await folder_factory(is_external=True, external_path="/tmp", external_readonly=False)

        files = {"file": ("test.txt", b"test content", "text/plain")}
        response = await async_client.post(
            f"/api/v1/library/files?folder_id={folder.id}", files=files
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_to_normal_folder_allowed(
        self, async_client: AsyncClient, folder_factory, db_session
    ):
        """Uploading to a normal (non-external) folder should succeed."""
        folder = await folder_factory()

        files = {"file": ("test.txt", b"test content", "text/plain")}
        response = await async_client.post(
            f"/api/v1/library/files?folder_id={folder.id}", files=files
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extract_zip_to_readonly_external_folder_blocked(
        self, async_client: AsyncClient, folder_factory, db_session
    ):
        """Extracting ZIP to readonly external folder should return 403."""
        import io
        import zipfile

        folder = await folder_factory(is_external=True, external_path="/tmp", external_readonly=True)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("test.txt", "Content")
        zip_buffer.seek(0)

        files = {"file": ("test.zip", zip_buffer.read(), "application/zip")}
        response = await async_client.post(
            f"/api/v1/library/files/extract-zip?folder_id={folder.id}", files=files
        )
        assert response.status_code == 403
        assert "read-only" in response.json()["detail"].lower()


class TestExternalFolderMoveProtection:
    """Tests for Issue #3: move operation validation for external folders."""

    @pytest.fixture
    async def folder_factory(self, db_session):
        """Factory to create test folders."""
        _counter = [0]

        async def _create_folder(**kwargs):
            from backend.app.models.library import LibraryFolder

            _counter[0] += 1
            counter = _counter[0]
            defaults = {"name": f"Test Folder {counter}"}
            defaults.update(kwargs)
            folder = LibraryFolder(**defaults)
            db_session.add(folder)
            await db_session.commit()
            await db_session.refresh(folder)
            return folder

        return _create_folder

    @pytest.fixture
    async def file_factory(self, db_session):
        """Factory to create test files."""
        _counter = [0]

        async def _create_file(**kwargs):
            from backend.app.models.library import LibraryFile

            _counter[0] += 1
            counter = _counter[0]
            defaults = {
                "filename": f"test_file_{counter}.3mf",
                "file_path": f"/test/path/test_file_{counter}.3mf",
                "file_size": 1024,
                "file_type": "3mf",
            }
            defaults.update(kwargs)
            lib_file = LibraryFile(**defaults)
            db_session.add(lib_file)
            await db_session.commit()
            await db_session.refresh(lib_file)
            return lib_file

        return _create_file

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_move_to_readonly_external_folder_blocked(
        self, async_client: AsyncClient, folder_factory, file_factory, db_session
    ):
        """Moving files to a readonly external folder should return 403."""
        normal_folder = await folder_factory()
        readonly_folder = await folder_factory(is_external=True, external_path="/tmp", external_readonly=True)
        lib_file = await file_factory(folder_id=normal_folder.id)

        response = await async_client.post(
            "/api/v1/library/files/move",
            json={"file_ids": [lib_file.id], "folder_id": readonly_folder.id},
        )
        assert response.status_code == 403
        assert "read-only" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_move_from_readonly_external_folder_blocked(
        self, async_client: AsyncClient, folder_factory, file_factory, db_session
    ):
        """Moving files from a readonly external folder should return 403."""
        readonly_folder = await folder_factory(is_external=True, external_path="/tmp", external_readonly=True)
        normal_folder = await folder_factory()
        lib_file = await file_factory(folder_id=readonly_folder.id)

        response = await async_client.post(
            "/api/v1/library/files/move",
            json={"file_ids": [lib_file.id], "folder_id": normal_folder.id},
        )
        assert response.status_code == 403
        assert "read-only" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_move_to_writable_external_folder_allowed(
        self, async_client: AsyncClient, folder_factory, file_factory, db_session
    ):
        """Moving files to a writable external folder should succeed."""
        normal_folder = await folder_factory()
        writable_folder = await folder_factory(is_external=True, external_path="/tmp", external_readonly=False)
        lib_file = await file_factory(folder_id=normal_folder.id)

        response = await async_client.post(
            "/api/v1/library/files/move",
            json={"file_ids": [lib_file.id], "folder_id": writable_folder.id},
        )
        assert response.status_code == 200
        assert response.json()["moved"] == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_move_between_normal_folders_allowed(
        self, async_client: AsyncClient, folder_factory, file_factory, db_session
    ):
        """Moving files between normal folders should succeed."""
        folder_a = await folder_factory()
        folder_b = await folder_factory()
        lib_file = await file_factory(folder_id=folder_a.id)

        response = await async_client.post(
            "/api/v1/library/files/move",
            json={"file_ids": [lib_file.id], "folder_id": folder_b.id},
        )
        assert response.status_code == 200
        assert response.json()["moved"] == 1
