"""Integration tests for Library API endpoints."""

import pytest
from httpx import AsyncClient


class TestLibraryFoldersAPI:
    """Integration tests for library folders endpoints."""

    @pytest.fixture
    async def folder_factory(self, db_session):
        """Factory to create test folders."""
        _counter = [0]

        async def _create_folder(**kwargs):
            from backend.app.models.library import LibraryFolder

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "name": f"Test Folder {counter}",
            }
            defaults.update(kwargs)

            folder = LibraryFolder(**defaults)
            db_session.add(folder)
            await db_session.commit()
            await db_session.refresh(folder)
            return folder

        return _create_folder

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_folders_empty(self, async_client: AsyncClient, db_session):
        """Verify empty folder list returns empty array."""
        response = await async_client.get("/api/v1/library/folders")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_folder(self, async_client: AsyncClient, db_session):
        """Verify folder can be created."""
        data = {"name": "New Folder"}
        response = await async_client.post("/api/v1/library/folders", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "New Folder"
        assert result["id"] is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_nested_folder(self, async_client: AsyncClient, folder_factory, db_session):
        """Verify nested folder can be created."""
        parent = await folder_factory(name="Parent")
        data = {"name": "Child", "parent_id": parent.id}
        response = await async_client.post("/api/v1/library/folders", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "Child"
        assert result["parent_id"] == parent.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_folder(self, async_client: AsyncClient, folder_factory, db_session):
        """Verify single folder can be retrieved."""
        folder = await folder_factory(name="Test Folder")
        response = await async_client.get(f"/api/v1/library/folders/{folder.id}")
        assert response.status_code == 200
        result = response.json()
        assert result["id"] == folder.id
        assert result["name"] == "Test Folder"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_folder_not_found(self, async_client: AsyncClient, db_session):
        """Verify 404 for non-existent folder."""
        response = await async_client.get("/api/v1/library/folders/9999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_folder(self, async_client: AsyncClient, folder_factory, db_session):
        """Verify folder can be updated."""
        folder = await folder_factory(name="Old Name")
        data = {"name": "New Name"}
        response = await async_client.put(f"/api/v1/library/folders/{folder.id}", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "New Name"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_folder(self, async_client: AsyncClient, folder_factory, db_session):
        """Verify folder can be deleted."""
        folder = await folder_factory()
        response = await async_client.delete(f"/api/v1/library/folders/{folder.id}")
        assert response.status_code == 200
        result = response.json()
        assert result.get("message") or result.get("success", True)


class TestLibraryFilesAPI:
    """Integration tests for library files endpoints."""

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
    async def test_list_files_empty(self, async_client: AsyncClient, db_session):
        """Verify empty file list returns empty array."""
        response = await async_client.get("/api/v1/library/files")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_files_in_folder(self, async_client: AsyncClient, folder_factory, file_factory, db_session):
        """Verify files can be filtered by folder."""
        folder = await folder_factory()
        file1 = await file_factory(folder_id=folder.id)
        await file_factory()  # File in root (no folder)

        response = await async_client.get(f"/api/v1/library/files?folder_id={folder.id}")
        assert response.status_code == 200
        result = response.json()
        assert len(result) == 1
        assert result[0]["id"] == file1.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_file(self, async_client: AsyncClient, file_factory, db_session):
        """Verify single file can be retrieved."""
        lib_file = await file_factory(filename="test.3mf")
        response = await async_client.get(f"/api/v1/library/files/{lib_file.id}")
        assert response.status_code == 200
        result = response.json()
        assert result["id"] == lib_file.id
        assert result["filename"] == "test.3mf"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_file_not_found(self, async_client: AsyncClient, db_session):
        """Verify 404 for non-existent file."""
        response = await async_client.get("/api/v1/library/files/9999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_file(self, async_client: AsyncClient, file_factory, db_session):
        """Verify file can be deleted."""
        lib_file = await file_factory()
        response = await async_client.delete(f"/api/v1/library/files/{lib_file.id}")
        assert response.status_code == 200
        result = response.json()
        assert result.get("message") or result.get("success", True)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_library_stats(self, async_client: AsyncClient, folder_factory, file_factory, db_session):
        """Verify library stats endpoint returns counts."""
        await folder_factory()
        await folder_factory()
        await file_factory()

        response = await async_client.get("/api/v1/library/stats")
        assert response.status_code == 200
        result = response.json()
        assert result["total_folders"] == 2
        assert result["total_files"] == 1


class TestLibraryAddToQueueAPI:
    """Integration tests for /api/v1/library/files/add-to-queue endpoint."""

    @pytest.fixture
    async def printer_factory(self, db_session):
        """Factory to create test printers."""
        _counter = [0]

        async def _create_printer(**kwargs):
            from backend.app.models.printer import Printer

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "name": f"Test Printer {counter}",
                "ip_address": f"192.168.1.{100 + counter}",
                "serial_number": f"TESTSERIAL{counter:04d}",
                "access_code": "12345678",
                "model": "X1C",
            }
            defaults.update(kwargs)

            printer = Printer(**defaults)
            db_session.add(printer)
            await db_session.commit()
            await db_session.refresh(printer)
            return printer

        return _create_printer

    @pytest.fixture
    async def library_file_factory(self, db_session):
        """Factory to create test library files."""
        _counter = [0]

        async def _create_library_file(**kwargs):
            from backend.app.models.library import LibraryFile

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "filename": f"test_file_{counter}.gcode.3mf",
                "file_path": f"/test/path/test_file_{counter}.gcode.3mf",
                "file_size": 1024,
                "file_type": "3mf",
            }
            defaults.update(kwargs)

            lib_file = LibraryFile(**defaults)
            db_session.add(lib_file)
            await db_session.commit()
            await db_session.refresh(lib_file)
            return lib_file

        return _create_library_file

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_to_queue_file_not_found(self, async_client: AsyncClient, printer_factory, db_session):
        """Verify error for non-existent file."""
        await printer_factory()

        data = {"file_ids": [9999]}
        response = await async_client.post("/api/v1/library/files/add-to-queue", json=data)
        assert response.status_code == 200
        result = response.json()
        assert len(result["added"]) == 0
        assert len(result["errors"]) == 1
        assert result["errors"][0]["file_id"] == 9999

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_add_non_sliced_file_to_queue_fails(
        self, async_client: AsyncClient, printer_factory, library_file_factory, db_session
    ):
        """Verify non-sliced file cannot be added to queue."""
        await printer_factory()
        lib_file = await library_file_factory(
            filename="model.stl",
            file_path="/test/path/model.stl",
            file_type="stl",
        )

        data = {"file_ids": [lib_file.id]}
        response = await async_client.post("/api/v1/library/files/add-to-queue", json=data)
        assert response.status_code == 200
        result = response.json()
        assert len(result["added"]) == 0
        assert len(result["errors"]) == 1
        assert "sliced" in result["errors"][0]["error"].lower()
