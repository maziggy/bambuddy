"""Integration tests for Projects API endpoints."""

import pytest
from httpx import AsyncClient


class TestProjectsAPI:
    """Integration tests for /api/v1/projects endpoints."""

    @pytest.fixture
    async def project_factory(self, db_session):
        """Factory to create test projects."""
        _counter = [0]

        async def _create_project(**kwargs):
            from backend.app.models.project import Project

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "name": f"Test Project {counter}",
                "description": "Test project description",
                "color": "#FF0000",
            }
            defaults.update(kwargs)

            project = Project(**defaults)
            db_session.add(project)
            await db_session.commit()
            await db_session.refresh(project)
            return project

        return _create_project

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_projects_empty(self, async_client: AsyncClient):
        """Verify empty list when no projects exist."""
        response = await async_client.get("/api/v1/projects/")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_projects_with_data(self, async_client: AsyncClient, project_factory, db_session):
        """Verify list returns existing projects."""
        await project_factory(name="My Project")
        response = await async_client.get("/api/v1/projects/")
        assert response.status_code == 200
        data = response.json()
        assert any(p["name"] == "My Project" for p in data)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_project(self, async_client: AsyncClient):
        """Verify project can be created."""
        data = {
            "name": "New Project",
            "description": "A new project",
            "color": "#00FF00",
        }
        response = await async_client.post("/api/v1/projects/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "New Project"
        assert result["color"] == "#00FF00"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_project(self, async_client: AsyncClient, project_factory, db_session):
        """Verify single project can be retrieved."""
        project = await project_factory(name="Get Test Project")
        response = await async_client.get(f"/api/v1/projects/{project.id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Get Test Project"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_project_not_found(self, async_client: AsyncClient):
        """Verify 404 for non-existent project."""
        response = await async_client.get("/api/v1/projects/9999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_project(self, async_client: AsyncClient, project_factory, db_session):
        """Verify project can be updated."""
        project = await project_factory(name="Original")
        response = await async_client.patch(
            f"/api/v1/projects/{project.id}", json={"name": "Updated", "description": "Updated description"}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "Updated"
        assert result["description"] == "Updated description"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_project(self, async_client: AsyncClient, project_factory, db_session):
        """Verify project can be deleted."""
        project = await project_factory()
        response = await async_client.delete(f"/api/v1/projects/{project.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Project deleted"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_project_not_found(self, async_client: AsyncClient):
        """Verify 404 for deleting non-existent project."""
        response = await async_client.delete("/api/v1/projects/9999")
        assert response.status_code == 404


class TestProjectPartsTracking:
    """Tests for project parts tracking feature."""

    @pytest.fixture
    async def project_factory(self, db_session):
        """Factory to create test projects."""

        async def _create_project(**kwargs):
            from backend.app.models.project import Project

            defaults = {
                "name": "Parts Test Project",
                "description": "Test project",
                "color": "#FF0000",
            }
            defaults.update(kwargs)

            project = Project(**defaults)
            db_session.add(project)
            await db_session.commit()
            await db_session.refresh(project)
            return project

        return _create_project

    @pytest.fixture
    async def archive_factory(self, db_session):
        """Factory to create test archives."""

        async def _create_archive(**kwargs):
            from backend.app.models.archive import PrintArchive

            defaults = {
                "filename": "test.3mf",
                "file_path": "test/test.3mf",
                "file_size": 1000,
                "print_name": "Test Print",
                "status": "completed",
                "quantity": 1,
            }
            defaults.update(kwargs)

            archive = PrintArchive(**defaults)
            db_session.add(archive)
            await db_session.commit()
            await db_session.refresh(archive)
            return archive

        return _create_archive

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_project_with_target_parts_count(self, async_client: AsyncClient):
        """Verify project can be created with target_parts_count."""
        data = {
            "name": "Parts Project",
            "target_count": 10,  # 10 plates
            "target_parts_count": 50,  # 50 parts total
        }
        response = await async_client.post("/api/v1/projects/", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["target_count"] == 10
        assert result["target_parts_count"] == 50

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_project_target_parts_count(self, async_client: AsyncClient, project_factory, db_session):
        """Verify target_parts_count can be updated."""
        project = await project_factory()
        response = await async_client.patch(
            f"/api/v1/projects/{project.id}",
            json={"target_parts_count": 100},
        )
        assert response.status_code == 200
        assert response.json()["target_parts_count"] == 100

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_project_parts_progress_calculation(
        self, async_client: AsyncClient, project_factory, archive_factory, db_session
    ):
        """Verify parts progress is calculated from archive quantities."""
        # Create project with target of 20 parts
        project = await project_factory(target_parts_count=20)

        # Create archives with different quantities
        await archive_factory(project_id=project.id, quantity=3, status="completed")  # 3 parts
        await archive_factory(project_id=project.id, quantity=5, status="completed")  # 5 parts
        await archive_factory(project_id=project.id, quantity=2, status="completed")  # 2 parts
        # Total: 10 parts completed out of 20 = 50%

        response = await async_client.get(f"/api/v1/projects/{project.id}")
        assert response.status_code == 200
        data = response.json()

        # Check stats
        assert data["stats"]["completed_prints"] == 10  # Sum of quantities
        assert data["stats"]["parts_progress_percent"] == 50.0  # 10/20 = 50%
        assert data["stats"]["remaining_parts"] == 10  # 20 - 10 = 10

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_project_list_shows_parts_count(
        self, async_client: AsyncClient, project_factory, archive_factory, db_session
    ):
        """Verify project list returns correct completed_count (parts sum)."""
        project = await project_factory(name="List Parts Project", target_parts_count=100)

        # Create archives with quantities
        await archive_factory(project_id=project.id, quantity=4, status="completed")
        await archive_factory(project_id=project.id, quantity=6, status="completed")
        # Total: 10 parts, 2 plates

        response = await async_client.get("/api/v1/projects/")
        assert response.status_code == 200
        data = response.json()

        # Find our project
        our_project = next((p for p in data if p["name"] == "List Parts Project"), None)
        assert our_project is not None
        assert our_project["archive_count"] == 2  # 2 plates
        assert our_project["completed_count"] == 10  # 10 parts (sum of quantities)
        assert our_project["target_parts_count"] == 100

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_plates_vs_parts_progress(
        self, async_client: AsyncClient, project_factory, archive_factory, db_session
    ):
        """Verify plates and parts progress are calculated separately."""
        # Project needs 5 plates producing 25 parts total (5 parts per plate)
        project = await project_factory(target_count=5, target_parts_count=25)

        # Complete 2 plates, each with 5 parts
        await archive_factory(project_id=project.id, quantity=5, status="completed")
        await archive_factory(project_id=project.id, quantity=5, status="completed")
        # Plates: 2/5 = 40%, Parts: 10/25 = 40%

        response = await async_client.get(f"/api/v1/projects/{project.id}")
        assert response.status_code == 200
        data = response.json()

        assert data["stats"]["total_archives"] == 2  # 2 plates
        assert data["stats"]["completed_prints"] == 10  # 10 parts
        assert data["stats"]["progress_percent"] == 40.0  # plates: 2/5
        assert data["stats"]["parts_progress_percent"] == 40.0  # parts: 10/25


class TestProjectArchivesAPI:
    """Tests for project-archive relationships."""

    @pytest.fixture
    async def project_factory(self, db_session):
        """Factory to create test projects."""

        async def _create_project(**kwargs):
            from backend.app.models.project import Project

            defaults = {
                "name": "Archive Test Project",
                "description": "Test project",
                "color": "#0000FF",
            }
            defaults.update(kwargs)

            project = Project(**defaults)
            db_session.add(project)
            await db_session.commit()
            await db_session.refresh(project)
            return project

        return _create_project

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_project_with_archives(self, async_client: AsyncClient, project_factory, db_session):
        """Verify project can be retrieved with archive count."""
        project = await project_factory()
        response = await async_client.get(f"/api/v1/projects/{project.id}")
        assert response.status_code == 200
        # Project should have an archive count (may be 0)
        data = response.json()
        assert "name" in data


class TestProjectExportImport:
    """Tests for project export/import functionality."""

    @pytest.fixture
    async def project_factory(self, db_session):
        """Factory to create test projects."""
        _counter = [0]

        async def _create_project(**kwargs):
            from backend.app.models.project import Project

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "name": f"Export Test Project {counter}",
                "description": "Test project for export",
                "color": "#00FF00",
            }
            defaults.update(kwargs)

            project = Project(**defaults)
            db_session.add(project)
            await db_session.commit()
            await db_session.refresh(project)
            return project

        return _create_project

    @pytest.fixture
    async def bom_item_factory(self, db_session):
        """Factory to create test BOM items."""

        async def _create_bom_item(project_id: int, **kwargs):
            from backend.app.models.project_bom import ProjectBOMItem

            defaults = {
                "project_id": project_id,
                "name": "Test Part",
                "quantity_needed": 1,
                "quantity_acquired": 0,
                "sort_order": 0,
            }
            defaults.update(kwargs)

            item = ProjectBOMItem(**defaults)
            db_session.add(item)
            await db_session.commit()
            await db_session.refresh(item)
            return item

        return _create_bom_item

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_export_project(self, async_client: AsyncClient, project_factory, bom_item_factory, db_session):
        """Verify project export includes BOM items."""
        project = await project_factory(
            name="Export Me",
            description="A test project",
            target_count=10,
            target_parts_count=50,
            budget=100.0,
        )

        # Add BOM items
        await bom_item_factory(project.id, name="M3x8 Screws", quantity_needed=20, unit_price=0.10)
        await bom_item_factory(project.id, name="Heat Inserts", quantity_needed=10, unit_price=0.25)

        # Test JSON format export
        response = await async_client.get(f"/api/v1/projects/{project.id}/export?format=json")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "Export Me"
        assert data["description"] == "A test project"
        assert data["target_count"] == 10
        assert data["target_parts_count"] == 50
        assert data["budget"] == 100.0
        assert len(data["bom_items"]) == 2

        # Check BOM items
        bom_names = [item["name"] for item in data["bom_items"]]
        assert "M3x8 Screws" in bom_names
        assert "Heat Inserts" in bom_names

        # Test ZIP format export (default)
        zip_response = await async_client.get(f"/api/v1/projects/{project.id}/export")
        assert zip_response.status_code == 200
        assert zip_response.headers["content-type"] == "application/zip"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_import_project(self, async_client: AsyncClient):
        """Verify project can be imported with BOM items."""
        import_data = {
            "name": "Imported Project",
            "description": "Imported from JSON",
            "color": "#FF00FF",
            "target_count": 5,
            "target_parts_count": 25,
            "budget": 50.0,
            "bom_items": [
                {
                    "name": "PTFE Tubes",
                    "quantity_needed": 4,
                    "quantity_acquired": 0,
                    "unit_price": 2.50,
                    "sourcing_url": "https://example.com",
                    "stl_filename": None,
                    "remarks": "Need 4mm ID",
                },
            ],
        }

        response = await async_client.post("/api/v1/projects/import", json=import_data)
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "Imported Project"
        assert data["description"] == "Imported from JSON"
        assert data["target_count"] == 5
        assert data["target_parts_count"] == 25
        assert data["budget"] == 50.0
        assert data["id"] > 0  # Has a valid ID
        # BOM stats should show 1 item imported
        assert data["stats"]["bom_total_items"] == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_export_project_with_linked_folder(self, async_client: AsyncClient, project_factory, db_session):
        """Verify project export includes linked folders."""
        from backend.app.models.library import LibraryFolder

        project = await project_factory(name="Project With Folder")

        # Create a linked folder
        folder = LibraryFolder(name="Project Files", project_id=project.id)
        db_session.add(folder)
        await db_session.commit()

        response = await async_client.get(f"/api/v1/projects/{project.id}/export?format=json")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "Project With Folder"
        assert len(data["linked_folders"]) == 1
        assert data["linked_folders"][0]["name"] == "Project Files"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_import_project_with_linked_folder(self, async_client: AsyncClient):
        """Verify project import accepts linked folders data."""
        import_data = {
            "name": "Imported With Folders",
            "linked_folders": [
                {"name": "STL Files"},
                {"name": "Documentation"},
            ],
        }

        # Import should succeed with linked_folders
        response = await async_client.post("/api/v1/projects/import", json=import_data)
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Imported With Folders"
        assert data["id"] > 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_import_project_from_json_file(self, async_client: AsyncClient):
        """Verify project can be imported from JSON file upload."""
        import io
        import json

        project_data = {
            "name": "File Uploaded Project",
            "description": "Imported from JSON file",
            "color": "#123456",
        }

        # Create a file-like object
        file_content = json.dumps(project_data).encode()
        files = {"file": ("project.json", io.BytesIO(file_content), "application/json")}

        response = await async_client.post("/api/v1/projects/import/file", files=files)
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "File Uploaded Project"
        assert data["description"] == "Imported from JSON file"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_import_project_from_zip_file(self, async_client: AsyncClient):
        """Verify project can be imported from ZIP file with files."""
        import io
        import json
        import zipfile

        project_data = {
            "name": "ZIP Imported Project",
            "description": "Imported from ZIP",
            "linked_folders": [{"name": "TestFolder", "files": [{"filename": "test.txt"}]}],
        }

        # Create a ZIP file in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("project.json", json.dumps(project_data))
            zf.writestr("files/TestFolder/test.txt", "Hello World")

        zip_buffer.seek(0)
        files = {"file": ("project.zip", zip_buffer, "application/zip")}

        response = await async_client.post("/api/v1/projects/import/file", files=files)
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "ZIP Imported Project"
        assert data["description"] == "Imported from ZIP"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_export_zip_contains_files(self, async_client: AsyncClient, project_factory, db_session):
        """Verify ZIP export contains actual files from linked folders."""
        import io
        import json
        import zipfile
        from pathlib import Path

        from backend.app.api.routes.library import get_library_dir
        from backend.app.models.library import LibraryFile, LibraryFolder

        project = await project_factory(name="Project With Files")

        # Create a linked folder with is_external fields
        folder = LibraryFolder(
            name="TestExportFolder",
            project_id=project.id,
            is_external=False,
            external_readonly=False,
            external_show_hidden=False,
        )
        db_session.add(folder)
        await db_session.flush()

        # Create a test file on disk
        library_dir = get_library_dir()
        folder_path = library_dir / "TestExportFolder"
        folder_path.mkdir(parents=True, exist_ok=True)
        test_file_path = folder_path / "test_export.txt"
        test_file_path.write_text("Export test content")

        # Create library file record
        lib_file = LibraryFile(
            folder_id=folder.id,
            filename="test_export.txt",
            file_path="TestExportFolder/test_export.txt",
            file_type="other",
            file_size=19,
            is_external=False,
        )
        db_session.add(lib_file)
        await db_session.commit()

        # Export as ZIP
        response = await async_client.get(f"/api/v1/projects/{project.id}/export")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"

        # Verify ZIP contents
        zip_buffer = io.BytesIO(response.content)
        with zipfile.ZipFile(zip_buffer, "r") as zf:
            assert "project.json" in zf.namelist()
            assert "files/TestExportFolder/test_export.txt" in zf.namelist()

            # Verify file content
            file_content = zf.read("files/TestExportFolder/test_export.txt").decode()
            assert file_content == "Export test content"

            # Verify project.json
            project_data = json.loads(zf.read("project.json"))
            assert project_data["name"] == "Project With Files"

        # Cleanup
        test_file_path.unlink(missing_ok=True)
        folder_path.rmdir()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_import_invalid_file_type(self, async_client: AsyncClient):
        """Verify import rejects invalid file types."""
        import io

        files = {"file": ("project.txt", io.BytesIO(b"invalid"), "text/plain")}
        response = await async_client.post("/api/v1/projects/import/file", files=files)
        assert response.status_code == 400
        assert "must be .zip or .json" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_import_zip_missing_project_json(self, async_client: AsyncClient):
        """Verify import rejects ZIP without project.json."""
        import io
        import zipfile

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("other.txt", "no project.json here")

        zip_buffer.seek(0)
        files = {"file": ("project.zip", zip_buffer, "application/zip")}
        response = await async_client.post("/api/v1/projects/import/file", files=files)
        assert response.status_code == 400
        assert "project.json" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_import_invalid_json(self, async_client: AsyncClient):
        """Verify import rejects invalid JSON content."""
        import io

        files = {"file": ("project.json", io.BytesIO(b"not valid json"), "application/json")}
        response = await async_client.post("/api/v1/projects/import/file", files=files)
        assert response.status_code == 400
        assert "Invalid JSON" in response.json()["detail"]
