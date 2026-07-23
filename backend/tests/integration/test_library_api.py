"""Integration tests for Library API endpoints."""

import io
import tempfile
import zipfile
from pathlib import Path

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
    async def test_folder_tree_exposes_latest_activity_at_from_files(
        self, async_client: AsyncClient, folder_factory, db_session
    ):
        """#1770: folder list returns latest_activity_at = MAX(folder.updated_at,
        MAX(immediate-child file.updated_at)) so the frontend can sort by
        recent activity. Adding a file with a later updated_at must bubble it.
        """
        from datetime import datetime, timedelta

        from backend.app.models.library import LibraryFile

        folder = await folder_factory(name="Active Folder")
        # File whose updated_at is well after the folder's. Activity should
        # surface this timestamp, not the folder's stale one.
        future = datetime.utcnow() + timedelta(hours=24)
        db_session.add(
            LibraryFile(
                folder_id=folder.id,
                filename="model.3mf",
                file_path="library/model.3mf",
                file_type="3mf",
                file_size=123,
                updated_at=future,
            )
        )
        await db_session.commit()

        response = await async_client.get("/api/v1/library/folders")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        item = items[0]
        assert item["id"] == folder.id
        assert item["latest_activity_at"] is not None
        # latest_activity_at should be at least the future stamp we set.
        assert item["latest_activity_at"] >= future.isoformat()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_folder_tree_latest_activity_at_falls_back_to_folder_updated_at(
        self, async_client: AsyncClient, folder_factory, db_session
    ):
        """#1770: a folder with no files reports its own updated_at, not null —
        otherwise the activity sort would dump every empty folder to one end."""
        await folder_factory(name="Empty Folder")
        response = await async_client.get("/api/v1/library/folders")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        item = items[0]
        # latest_activity_at == folder.updated_at when there are no files
        assert item["latest_activity_at"] is not None

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
    async def test_list_files_by_project_id(self, async_client: AsyncClient, folder_factory, file_factory, db_session):
        """#932: project_id filter returns files across all folders linked to the project.

        Replaces the prior N+1 pattern where the frontend fired one request per
        linked folder. A single JOIN query must return every file in folders whose
        project_id matches, while excluding files from unlinked folders.
        """
        from backend.app.models.project import Project

        project = Project(name="Test Project for Files", color="#00ff00")
        db_session.add(project)
        await db_session.commit()
        await db_session.refresh(project)

        folder_a = await folder_factory(name="Folder A", project_id=project.id)
        folder_b = await folder_factory(name="Folder B", project_id=project.id)
        other_folder = await folder_factory(name="Unlinked")

        linked_a = await file_factory(folder_id=folder_a.id, filename="a.3mf")
        linked_b = await file_factory(folder_id=folder_b.id, filename="b.3mf")
        await file_factory(folder_id=other_folder.id, filename="unlinked.3mf")
        await file_factory(filename="root.3mf")  # no folder → not part of any project

        response = await async_client.get(f"/api/v1/library/files?project_id={project.id}")
        assert response.status_code == 200
        result = response.json()
        ids = {f["id"] for f in result}
        assert ids == {linked_a.id, linked_b.id}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_files_folder_id_takes_precedence_over_project_id(
        self, async_client: AsyncClient, folder_factory, file_factory, db_session
    ):
        """When both folder_id and project_id are passed, folder_id wins.

        Documented precedence in list_files(): folder_id > project_id > include_root.
        This guards the behavior so a future refactor can't silently flip it.
        """
        from backend.app.models.project import Project

        project = Project(name="Precedence Project")
        db_session.add(project)
        await db_session.commit()
        await db_session.refresh(project)

        folder_linked = await folder_factory(name="Linked", project_id=project.id)
        folder_other = await folder_factory(name="Other")

        await file_factory(folder_id=folder_linked.id, filename="linked.3mf")
        other_file = await file_factory(folder_id=folder_other.id, filename="other.3mf")

        # folder_id points at a folder that is NOT in the project — must return
        # that folder's contents and ignore project_id entirely.
        response = await async_client.get(f"/api/v1/library/files?folder_id={folder_other.id}&project_id={project.id}")
        assert response.status_code == 200
        result = response.json()
        assert len(result) == 1
        assert result[0]["id"] == other_file.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_files_internal_only(self, async_client: AsyncClient, folder_factory, file_factory, db_session):
        """#1621: `internal_only=true` restricts the listing to files in managed
        storage (`is_external=False`) so a linked NAS with hundreds of files
        doesn't drown the user's own uploads in the "All Files" sidebar view."""
        internal_folder = await folder_factory(name="My uploads")
        external_folder = await folder_factory(name="NAS", is_external=True, external_path="/mnt/nas")

        internal_file = await file_factory(folder_id=internal_folder.id, filename="mine.3mf", is_external=False)
        await file_factory(folder_id=external_folder.id, filename="nas.3mf", is_external=True)
        root_file = await file_factory(filename="root.3mf", is_external=False)  # Root-uploaded is always internal.

        response = await async_client.get("/api/v1/library/files?include_root=false&internal_only=true")
        assert response.status_code == 200
        ids = {f["id"] for f in response.json()}
        assert ids == {internal_file.id, root_file.id}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_files_external_only(self, async_client: AsyncClient, folder_factory, file_factory, db_session):
        """#1621 symmetric: `external_only=true` returns the combined view
        across every linked external folder so users with several mounts can
        see all external content in one place without clicking each folder."""
        internal_folder = await folder_factory(name="My uploads")
        nas_a = await folder_factory(name="NAS A", is_external=True, external_path="/mnt/a")
        nas_b = await folder_factory(name="NAS B", is_external=True, external_path="/mnt/b")

        await file_factory(folder_id=internal_folder.id, filename="mine.3mf", is_external=False)
        ext_a = await file_factory(folder_id=nas_a.id, filename="a.3mf", is_external=True)
        ext_b = await file_factory(folder_id=nas_b.id, filename="b.3mf", is_external=True)

        response = await async_client.get("/api/v1/library/files?include_root=false&external_only=true")
        assert response.status_code == 200
        ids = {f["id"] for f in response.json()}
        assert ids == {ext_a.id, ext_b.id}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_files_internal_and_external_mutually_exclusive(self, async_client: AsyncClient, db_session):
        """Both flags together is a caller bug — fail loud (400) rather than
        silently picking one, so a frontend regression is caught immediately."""
        response = await async_client.get("/api/v1/library/files?internal_only=true&external_only=true")
        assert response.status_code == 400
        assert "mutually exclusive" in response.json()["detail"]

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
    async def test_rename_file(self, async_client: AsyncClient, file_factory, db_session):
        """Verify file can be renamed."""
        lib_file = await file_factory(filename="old_name.3mf")
        data = {"filename": "new_name.3mf"}
        response = await async_client.put(f"/api/v1/library/files/{lib_file.id}", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["filename"] == "new_name.3mf"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rename_file_invalid_path_separator(self, async_client: AsyncClient, file_factory, db_session):
        """Verify file rename fails with a forward slash (FAT32-illegal, #1540)."""
        lib_file = await file_factory(filename="test.3mf")
        data = {"filename": "path/to/file.3mf"}
        response = await async_client.put(f"/api/v1/library/files/{lib_file.id}", json=data)
        assert response.status_code == 400
        assert "invalid character" in response.json()["detail"].lower()
        assert "/" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rename_file_invalid_backslash(self, async_client: AsyncClient, file_factory, db_session):
        """Verify file rename fails with a backslash (FAT32-illegal, #1540)."""
        lib_file = await file_factory(filename="test.3mf")
        data = {"filename": "path\\to\\file.3mf"}
        response = await async_client.put(f"/api/v1/library/files/{lib_file.id}", json=data)
        assert response.status_code == 400
        assert "invalid character" in response.json()["detail"].lower()
        assert "\\" in response.json()["detail"]

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

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_file_list_includes_user_tracking_fields(self, async_client: AsyncClient, file_factory, db_session):
        """Verify file list response includes user tracking fields (Issue #206)."""
        lib_file = await file_factory(filename="test.3mf")
        response = await async_client.get("/api/v1/library/files?include_root=false")
        assert response.status_code == 200
        result = response.json()
        assert len(result) >= 1
        # Find our test file
        test_file = next((f for f in result if f["id"] == lib_file.id), None)
        assert test_file is not None
        # User tracking fields should be present (even if null)
        assert "created_by_id" in test_file
        assert "created_by_username" in test_file

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_file_detail_includes_user_tracking_fields(self, async_client: AsyncClient, file_factory, db_session):
        """Verify file detail response includes user tracking fields (Issue #206)."""
        lib_file = await file_factory(filename="test_detail.3mf")
        response = await async_client.get(f"/api/v1/library/files/{lib_file.id}")
        assert response.status_code == 200
        result = response.json()
        # User tracking fields should be present (even if null)
        assert "created_by_id" in result
        assert "created_by_username" in result

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_file_with_user_tracking(self, async_client: AsyncClient, db_session):
        """Verify file created with user shows username in response (Issue #206)."""
        from backend.app.models.library import LibraryFile
        from backend.app.models.user import User

        # Create a test user
        user = User(username="testuploader", password_hash="fakehash", role="user")
        db_session.add(user)
        await db_session.flush()

        # Create a file with created_by_id set
        lib_file = LibraryFile(
            filename="user_uploaded.3mf",
            file_path="/test/user_uploaded.3mf",
            file_size=2048,
            file_type="3mf",
            created_by_id=user.id,
        )
        db_session.add(lib_file)
        await db_session.commit()
        await db_session.refresh(lib_file)

        # Verify file detail shows username
        response = await async_client.get(f"/api/v1/library/files/{lib_file.id}")
        assert response.status_code == 200
        result = response.json()
        assert result["created_by_id"] == user.id
        assert result["created_by_username"] == "testuploader"

        # Verify file list also shows username
        response = await async_client.get("/api/v1/library/files?include_root=false")
        assert response.status_code == 200
        files = response.json()
        test_file = next((f for f in files if f["id"] == lib_file.id), None)
        assert test_file is not None
        assert test_file["created_by_id"] == user.id
        assert test_file["created_by_username"] == "testuploader"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_files_recursive_includes_subfolders(
        self, async_client: AsyncClient, folder_factory, file_factory
    ):
        """#1268: ?recursive=true with folder_id must include every descendant.

        Tree:
            toys/             ← f_toys, direct file "robot_top.3mf"
              cars/           ← child of toys, file "robot_car.3mf"
                race/         ← grandchild, file "robot_race.3mf"
            other/            ← unrelated, file "robot_other.3mf" (must NOT appear)
        """
        toys = await folder_factory(name="toys")
        cars = await folder_factory(name="cars", parent_id=toys.id)
        race = await folder_factory(name="race", parent_id=cars.id)
        other = await folder_factory(name="other")

        top = await file_factory(folder_id=toys.id, filename="robot_top.3mf")
        mid = await file_factory(folder_id=cars.id, filename="robot_car.3mf")
        deep = await file_factory(folder_id=race.id, filename="robot_race.3mf")
        await file_factory(folder_id=other.id, filename="robot_other.3mf")

        # Non-recursive: only the file directly under toys.
        r = await async_client.get(f"/api/v1/library/files?folder_id={toys.id}")
        assert r.status_code == 200
        assert {f["id"] for f in r.json()} == {top.id}

        # Recursive: toys + cars + race files, but NOT other/.
        r = await async_client.get(f"/api/v1/library/files?folder_id={toys.id}&recursive=true")
        assert r.status_code == 200
        assert {f["id"] for f in r.json()} == {top.id, mid.id, deep.id}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_files_recursive_without_folder_id_is_noop(
        self, async_client: AsyncClient, folder_factory, file_factory
    ):
        """recursive=true is meaningful only with folder_id — without it the
        existing include_root branch handles scoping. Just confirming the new
        param doesn't shadow that path."""
        folder = await folder_factory()
        f_in = await file_factory(folder_id=folder.id)
        f_root = await file_factory()

        r = await async_client.get("/api/v1/library/files?include_root=false&recursive=true")
        assert r.status_code == 200
        assert {f["id"] for f in r.json()} == {f_in.id, f_root.id}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_folder_readme_returns_first_markdown(
        self, async_client: AsyncClient, folder_factory, file_factory
    ):
        """#1268: /folders/{id}/readme reads on-disk content of the first .md."""
        folder = await folder_factory()
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
            f.write("# Robot\n\nA cute little robot.")
            md_path = f.name
        try:
            await file_factory(
                folder_id=folder.id,
                filename="README.md",
                file_path=md_path,
                file_type="md",
                file_size=Path(md_path).stat().st_size,
            )
            r = await async_client.get(f"/api/v1/library/folders/{folder.id}/readme")
            assert r.status_code == 200
            body = r.json()
            assert body["filename"] == "README.md"
            assert body["content"] == "# Robot\n\nA cute little robot."
            assert body["truncated"] is False
        finally:
            import os

            os.unlink(md_path)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_folder_readme_prefers_readme_over_other_md(
        self, async_client: AsyncClient, folder_factory, file_factory
    ):
        """When the folder has multiple .md files, README.md / description.md
        wins regardless of insertion order or filename case."""
        folder = await folder_factory()
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
            f.write("notes notes notes")
            notes_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
            f.write("the real one")
            readme_path = f.name
        try:
            # notes.md inserted FIRST — naive ordering would pick this one.
            await file_factory(
                folder_id=folder.id,
                filename="notes.md",
                file_path=notes_path,
                file_type="md",
            )
            await file_factory(
                folder_id=folder.id,
                filename="readme.md",  # lowercase to confirm case-insensitive match
                file_path=readme_path,
                file_type="md",
            )
            r = await async_client.get(f"/api/v1/library/folders/{folder.id}/readme")
            assert r.status_code == 200
            assert r.json()["filename"] == "readme.md"
            assert r.json()["content"] == "the real one"
        finally:
            import os

            os.unlink(notes_path)
            os.unlink(readme_path)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_folder_readme_404_when_no_markdown(
        self, async_client: AsyncClient, folder_factory, file_factory
    ):
        """No .md in the folder → 404 so the FE can hide the side panel."""
        folder = await folder_factory()
        await file_factory(folder_id=folder.id, filename="model.3mf", file_type="3mf")
        r = await async_client.get(f"/api/v1/library/folders/{folder.id}/readme")
        assert r.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_folder_readme_404_when_folder_missing(self, async_client: AsyncClient):
        r = await async_client.get("/api/v1/library/folders/999999/readme")
        assert r.status_code == 404


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


class TestLibraryZipExtractAPI:
    """Integration tests for ZIP extraction endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extract_zip_invalid_file_type(self, async_client: AsyncClient, db_session):
        """Verify non-ZIP files are rejected."""
        # Create a fake file that's not a ZIP
        files = {"file": ("test.txt", b"This is not a zip file", "text/plain")}
        response = await async_client.post("/api/v1/library/files/extract-zip", files=files)
        assert response.status_code == 400
        assert "ZIP" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extract_zip_basic(self, async_client: AsyncClient, db_session):
        """Verify basic ZIP extraction works."""
        import io

        # Create a simple ZIP file in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("test1.txt", "Content of file 1")
            zf.writestr("test2.txt", "Content of file 2")
        zip_buffer.seek(0)

        files = {"file": ("test.zip", zip_buffer.read(), "application/zip")}
        response = await async_client.post("/api/v1/library/files/extract-zip", files=files)
        assert response.status_code == 200
        result = response.json()
        assert result["extracted"] == 2
        assert len(result["files"]) == 2
        assert len(result["errors"]) == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extract_zip_with_folders(self, async_client: AsyncClient, db_session):
        """Verify ZIP extraction preserves folder structure."""
        import io

        # Create a ZIP file with folder structure
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("folder1/file1.txt", "Content 1")
            zf.writestr("folder1/subfolder/file2.txt", "Content 2")
            zf.writestr("folder2/file3.txt", "Content 3")
        zip_buffer.seek(0)

        files = {"file": ("test.zip", zip_buffer.read(), "application/zip")}
        params = {"preserve_structure": "true"}
        response = await async_client.post("/api/v1/library/files/extract-zip", files=files, params=params)
        assert response.status_code == 200
        result = response.json()
        assert result["extracted"] == 3
        assert result["folders_created"] >= 3  # folder1, folder1/subfolder, folder2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extract_zip_flat(self, async_client: AsyncClient, db_session):
        """Verify ZIP extraction can extract flat (no folders)."""
        import io

        # Create a ZIP file with folder structure
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("folder/file1.txt", "Content 1")
            zf.writestr("folder/file2.txt", "Content 2")
        zip_buffer.seek(0)

        files = {"file": ("test.zip", zip_buffer.read(), "application/zip")}
        params = {"preserve_structure": "false"}
        response = await async_client.post("/api/v1/library/files/extract-zip", files=files, params=params)
        assert response.status_code == 200
        result = response.json()
        assert result["extracted"] == 2
        assert result["folders_created"] == 0  # No folders created when flat

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extract_zip_skips_macos_files(self, async_client: AsyncClient, db_session):
        """Verify ZIP extraction skips __MACOSX and hidden files."""
        import io

        # Create a ZIP file with macOS junk files
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("real_file.txt", "Real content")
            zf.writestr("__MACOSX/._real_file.txt", "macOS metadata")
            zf.writestr(".hidden_file", "Hidden content")
        zip_buffer.seek(0)

        files = {"file": ("test.zip", zip_buffer.read(), "application/zip")}
        response = await async_client.post("/api/v1/library/files/extract-zip", files=files)
        assert response.status_code == 200
        result = response.json()
        assert result["extracted"] == 1  # Only real_file.txt
        assert result["files"][0]["filename"] == "real_file.txt"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extract_zip_create_folder_from_zip(self, async_client: AsyncClient, db_session):
        """Verify ZIP extraction creates a folder from the ZIP filename."""
        import io

        # Create a ZIP file with some files
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("file1.txt", "Content 1")
            zf.writestr("file2.txt", "Content 2")
        zip_buffer.seek(0)

        files = {"file": ("MyProject.zip", zip_buffer.read(), "application/zip")}
        params = {"create_folder_from_zip": "true", "preserve_structure": "false"}
        response = await async_client.post("/api/v1/library/files/extract-zip", files=files, params=params)
        assert response.status_code == 200
        result = response.json()
        assert result["extracted"] == 2
        assert result["folders_created"] == 1  # MyProject folder created

        # Verify the files are in a folder
        assert result["files"][0]["folder_id"] is not None
        assert result["files"][1]["folder_id"] is not None
        # Both files should be in the same folder
        assert result["files"][0]["folder_id"] == result["files"][1]["folder_id"]

        # Verify the folder was created with the right name
        folder_response = await async_client.get(f"/api/v1/library/folders/{result['files'][0]['folder_id']}")
        assert folder_response.status_code == 200
        folder = folder_response.json()
        assert folder["name"] == "MyProject"


class TestLibraryStlThumbnailAPI:
    """Integration tests for STL thumbnail generation endpoints."""

    @pytest.fixture
    async def file_factory(self, db_session):
        """Factory to create test files."""
        _counter = [0]

        async def _create_file(**kwargs):
            from backend.app.models.library import LibraryFile

            _counter[0] += 1
            counter = _counter[0]

            defaults = {
                "filename": f"test_model_{counter}.stl",
                "file_path": f"/test/path/test_model_{counter}.stl",
                "file_size": 1024,
                "file_type": "stl",
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
    async def test_batch_generate_thumbnails_empty(self, async_client: AsyncClient, db_session):
        """Verify batch thumbnail generation with no files."""
        data = {"all_missing": True}
        response = await async_client.post("/api/v1/library/generate-stl-thumbnails", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["processed"] == 0
        assert result["succeeded"] == 0
        assert result["failed"] == 0
        assert result["results"] == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_batch_generate_thumbnails_no_criteria(self, async_client: AsyncClient, db_session):
        """Verify batch thumbnail generation with no criteria returns empty."""
        data = {}
        response = await async_client.post("/api/v1/library/generate-stl-thumbnails", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["processed"] == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_batch_generate_thumbnails_file_not_on_disk(
        self, async_client: AsyncClient, file_factory, db_session
    ):
        """Verify batch thumbnail generation handles missing files gracefully."""
        # Create a file in DB but not on disk
        stl_file = await file_factory(
            filename="missing.stl",
            file_path="/nonexistent/path/missing.stl",
            thumbnail_path=None,
        )

        data = {"file_ids": [stl_file.id]}
        response = await async_client.post("/api/v1/library/generate-stl-thumbnails", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["processed"] == 1
        assert result["succeeded"] == 0
        assert result["failed"] == 1
        assert result["results"][0]["success"] is False
        assert "not found" in result["results"][0]["error"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_batch_generate_thumbnails_with_real_stl(self, async_client: AsyncClient, db_session):
        """Verify batch thumbnail generation with a real STL file."""
        from backend.app.models.library import LibraryFile

        # Create a simple ASCII STL cube
        stl_content = """solid cube
facet normal 0 0 -1
  outer loop
    vertex 0 0 0
    vertex 1 0 0
    vertex 1 1 0
  endloop
endfacet
facet normal 0 0 1
  outer loop
    vertex 0 0 1
    vertex 1 1 1
    vertex 1 0 1
  endloop
endfacet
endsolid cube"""

        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False, mode="w") as f:
            f.write(stl_content)
            stl_path = f.name

        try:
            # Create file in DB pointing to real STL
            lib_file = LibraryFile(
                filename="test_cube.stl",
                file_path=stl_path,
                file_size=len(stl_content),
                file_type="stl",
                thumbnail_path=None,
            )
            db_session.add(lib_file)
            await db_session.commit()
            await db_session.refresh(lib_file)

            data = {"file_ids": [lib_file.id]}
            response = await async_client.post("/api/v1/library/generate-stl-thumbnails", json=data)
            assert response.status_code == 200
            result = response.json()
            assert result["processed"] == 1
            # Result depends on whether trimesh/matplotlib are installed
            # Either succeeds or fails gracefully
            assert result["succeeded"] + result["failed"] == 1
        finally:
            import os

            if os.path.exists(stl_path):
                os.unlink(stl_path)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_file_with_stl_thumbnail_param(self, async_client: AsyncClient, db_session):
        """Verify file upload accepts generate_stl_thumbnails parameter."""
        # Create a simple STL file
        stl_content = b"solid test\nendsolid test"

        files = {"file": ("test.stl", stl_content, "application/octet-stream")}
        params = {"generate_stl_thumbnails": "false"}
        response = await async_client.post("/api/v1/library/files", files=files, params=params)
        assert response.status_code == 200
        result = response.json()
        assert result["filename"] == "test.stl"
        assert result["file_type"] == "stl"
        # No thumbnail should be generated when disabled
        assert result["thumbnail_path"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_extract_zip_with_stl_thumbnail_param(self, async_client: AsyncClient, db_session):
        """Verify ZIP extraction accepts generate_stl_thumbnails parameter."""
        # Create a ZIP file containing an STL
        stl_content = b"solid test\nendsolid test"
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("model.stl", stl_content)
        zip_buffer.seek(0)

        files = {"file": ("test.zip", zip_buffer.read(), "application/zip")}
        params = {"generate_stl_thumbnails": "false"}
        response = await async_client.post("/api/v1/library/files/extract-zip", files=files, params=params)
        assert response.status_code == 200
        result = response.json()
        assert result["extracted"] == 1
        assert result["files"][0]["filename"] == "model.stl"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_batch_generate_thumbnails_by_folder(self, async_client: AsyncClient, file_factory, db_session):
        """Verify batch thumbnail generation can filter by folder."""
        from backend.app.models.library import LibraryFolder

        # Create a folder
        folder = LibraryFolder(name="STL Folder")
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        # Create STL file in folder (no thumbnail)
        stl_in_folder = await file_factory(
            filename="in_folder.stl",
            folder_id=folder.id,
            thumbnail_path=None,
        )

        # Create STL file at root (no thumbnail)
        _stl_at_root = await file_factory(
            filename="at_root.stl",
            folder_id=None,
            thumbnail_path=None,
        )

        # Request thumbnails only for files in folder
        data = {"folder_id": folder.id, "all_missing": True}
        response = await async_client.post("/api/v1/library/generate-stl-thumbnails", json=data)
        assert response.status_code == 200
        result = response.json()
        # Should only process the file in the folder
        assert result["processed"] == 1
        assert result["results"][0]["file_id"] == stl_in_folder.id

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_batch_generate_thumbnails_all_missing(self, async_client: AsyncClient, file_factory, db_session):
        """Verify batch thumbnail generation finds all STL files missing thumbnails."""
        # Create files with and without thumbnails
        _stl_with_thumb = await file_factory(
            filename="with_thumb.stl",
            thumbnail_path="/some/path/thumb.png",
        )
        stl_without_thumb1 = await file_factory(
            filename="without_thumb1.stl",
            thumbnail_path=None,
        )
        stl_without_thumb2 = await file_factory(
            filename="without_thumb2.stl",
            thumbnail_path=None,
        )

        data = {"all_missing": True}
        response = await async_client.post("/api/v1/library/generate-stl-thumbnails", json=data)
        assert response.status_code == 200
        result = response.json()
        # Should only process files without thumbnails
        assert result["processed"] == 2
        file_ids = {r["file_id"] for r in result["results"]}
        assert stl_without_thumb1.id in file_ids
        assert stl_without_thumb2.id in file_ids


class TestLibraryPathHelpers:
    """Tests for path handling utilities used for backup portability."""

    def test_to_relative_path_converts_absolute(self):
        """Verify absolute paths are converted to relative paths."""
        from backend.app.api.routes.library import to_relative_path
        from backend.app.core.config import settings

        base_dir = str(settings.base_dir)
        abs_path = f"{base_dir}/archive/library/files/test.3mf"
        rel_path = to_relative_path(abs_path)

        assert not rel_path.startswith("/")
        assert rel_path == "archive/library/files/test.3mf"

    def test_to_relative_path_handles_path_object(self):
        """Verify Path objects are handled correctly."""
        from pathlib import Path

        from backend.app.api.routes.library import to_relative_path
        from backend.app.core.config import settings

        abs_path = Path(settings.base_dir) / "archive" / "test.3mf"
        rel_path = to_relative_path(abs_path)

        assert not rel_path.startswith("/")
        assert rel_path == "archive/test.3mf"

    def test_to_relative_path_returns_empty_for_empty_input(self):
        """Verify empty input returns empty string."""
        from backend.app.api.routes.library import to_relative_path

        assert to_relative_path("") == ""
        assert to_relative_path(None) == ""

    def test_to_absolute_path_converts_relative(self):
        """Verify relative paths are converted to absolute paths."""
        from backend.app.api.routes.library import to_absolute_path
        from backend.app.core.config import settings

        rel_path = "archive/library/files/test.3mf"
        abs_path = to_absolute_path(rel_path)

        assert abs_path is not None
        assert abs_path.is_absolute()
        assert str(abs_path) == f"{settings.base_dir}/archive/library/files/test.3mf"

    def test_to_absolute_path_handles_already_absolute(self):
        """Verify already absolute paths are returned as-is (for backwards compatibility)."""
        from backend.app.api.routes.library import to_absolute_path

        abs_path_str = "/data/archive/test.3mf"
        result = to_absolute_path(abs_path_str)

        assert result is not None
        assert str(result) == abs_path_str

    def test_to_absolute_path_returns_none_for_empty(self):
        """Verify None/empty input returns None."""
        from backend.app.api.routes.library import to_absolute_path

        assert to_absolute_path(None) is None
        assert to_absolute_path("") is None


class TestLibraryPermissions:
    """Tests for library permission enforcement."""

    @pytest.fixture
    async def auth_setup(self, db_session):
        """Set up auth with users of different permission levels."""
        from backend.app.core.auth import create_access_token, get_password_hash
        from backend.app.models.group import Group
        from backend.app.models.settings import Settings
        from backend.app.models.user import User

        # Enable auth
        settings = Settings(key="auth_enabled", value="true")
        db_session.add(settings)
        await db_session.commit()

        # Groups are auto-seeded during db init, but we need to commit them
        await db_session.commit()

        # Get groups
        from sqlalchemy import select

        admin_group = (await db_session.execute(select(Group).where(Group.name == "Administrators"))).scalar_one()
        operator_group = (await db_session.execute(select(Group).where(Group.name == "Operators"))).scalar_one()
        viewer_group = (await db_session.execute(select(Group).where(Group.name == "Viewers"))).scalar_one()

        password_hash = get_password_hash("password")

        # Create users
        admin_user = User(username="admin_lib", password_hash=password_hash, role="user", is_active=True)
        admin_user.groups.append(admin_group)

        operator_user = User(username="operator_lib", password_hash=password_hash, is_active=True)
        operator_user.groups.append(operator_group)

        viewer_user = User(username="viewer_lib", password_hash=password_hash, is_active=True)
        viewer_user.groups.append(viewer_group)

        db_session.add_all([admin_user, operator_user, viewer_user])
        await db_session.commit()

        # Create tokens
        admin_token = create_access_token(data={"sub": admin_user.username})
        operator_token = create_access_token(data={"sub": operator_user.username})
        viewer_token = create_access_token(data={"sub": viewer_user.username})

        return {
            "admin_user": admin_user,
            "operator_user": operator_user,
            "viewer_user": viewer_user,
            "admin_token": admin_token,
            "operator_token": operator_token,
            "viewer_token": viewer_token,
        }

    @pytest.fixture
    async def test_file(self, db_session, auth_setup):
        """Create a test file owned by the operator user."""
        from backend.app.models.library import LibraryFile

        operator_user = auth_setup["operator_user"]
        lib_file = LibraryFile(
            filename="test.txt",
            file_path="data/archive/library/files/test.txt",
            file_type="txt",
            file_size=100,
            created_by_id=operator_user.id,
        )
        db_session.add(lib_file)
        await db_session.commit()
        await db_session.refresh(lib_file)
        return lib_file

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_files_requires_library_read(self, async_client: AsyncClient, db_session, auth_setup):
        """Verify list_files requires library:read permission."""
        viewer_token = auth_setup["viewer_token"]

        # Viewers have library:read, should succeed
        response = await async_client.get("/api/v1/library/files", headers={"Authorization": f"Bearer {viewer_token}"})
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_files_denied_without_permission(self, async_client: AsyncClient, db_session):
        """Verify list_files denied without auth when auth is enabled."""
        from backend.app.models.settings import Settings

        # Enable auth
        settings = Settings(key="auth_enabled", value="true")
        db_session.add(settings)
        await db_session.commit()

        # Request without token should fail
        response = await async_client.get("/api/v1/library/files")
        assert response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_file_own_by_owner(self, async_client: AsyncClient, db_session, auth_setup, test_file):
        """Verify operator can delete their own files."""
        from pathlib import Path

        # Create actual file on disk so delete doesn't fail
        from backend.app.core.config import settings as app_settings

        file_path = Path(app_settings.base_dir) / test_file.file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("test content")

        operator_token = auth_setup["operator_token"]

        response = await async_client.delete(
            f"/api/v1/library/files/{test_file.id}", headers={"Authorization": f"Bearer {operator_token}"}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_file_own_denied_for_others_file(self, async_client: AsyncClient, db_session, auth_setup):
        """Verify operator cannot delete files owned by others."""
        # Create another operator user with a file
        from sqlalchemy import select

        from backend.app.core.auth import create_access_token
        from backend.app.models.group import Group
        from backend.app.models.library import LibraryFile
        from backend.app.models.user import User

        operator_group = (await db_session.execute(select(Group).where(Group.name == "Operators"))).scalar_one()

        from backend.app.core.auth import get_password_hash as get_pw_hash

        other_user = User(username="other_op", password_hash=get_pw_hash("password"), is_active=True)
        other_user.groups.append(operator_group)
        db_session.add(other_user)
        await db_session.commit()
        await db_session.refresh(other_user)

        # Create file owned by other user
        other_file = LibraryFile(
            filename="other.txt",
            file_path="data/archive/library/files/other.txt",
            file_type="txt",
            file_size=100,
            created_by_id=other_user.id,
        )
        db_session.add(other_file)
        await db_session.commit()
        await db_session.refresh(other_file)

        # Original operator should not be able to delete it
        operator_token = auth_setup["operator_token"]
        response = await async_client.delete(
            f"/api/v1/library/files/{other_file.id}", headers={"Authorization": f"Bearer {operator_token}"}
        )
        assert response.status_code == 403
        assert "your own files" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_file_admin_can_delete_any(self, async_client: AsyncClient, db_session, auth_setup):
        """Verify admin can delete any file."""
        from pathlib import Path

        from backend.app.core.config import settings as app_settings
        from backend.app.models.library import LibraryFile

        # Create file owned by operator
        operator_user = auth_setup["operator_user"]
        lib_file = LibraryFile(
            filename="admin_can_delete.txt",
            file_path="data/archive/library/files/admin_can_delete.txt",
            file_type="txt",
            file_size=100,
            created_by_id=operator_user.id,
        )
        db_session.add(lib_file)
        await db_session.commit()
        await db_session.refresh(lib_file)

        # Create actual file on disk
        file_path = Path(app_settings.base_dir) / lib_file.file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("test content")

        # Admin should be able to delete it
        admin_token = auth_setup["admin_token"]
        response = await async_client.delete(
            f"/api/v1/library/files/{lib_file.id}", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_viewer_cannot_delete_files(self, async_client: AsyncClient, db_session, auth_setup, test_file):
        """Verify viewer cannot delete any files."""
        viewer_token = auth_setup["viewer_token"]

        response = await async_client.delete(
            f"/api/v1/library/files/{test_file.id}", headers={"Authorization": f"Bearer {viewer_token}"}
        )
        # Viewers don't have delete_own or delete_all permissions
        assert response.status_code == 403

    # ---------- #1832: API-key curation under can_manage_library ----------
    #
    # require_ownership_permission gates API keys on `all_perm`, but the
    # library deliberately split UPDATE_OWN/DELETE_OWN (allowed under
    # can_manage_library) from UPDATE_ALL/DELETE_ALL (previously denied).
    # That made the entire curation surface (DELETE, PUT rename, POST move)
    # unreachable for API keys, including for files the key's owner uploaded.
    # The fix folds UPDATE_ALL/DELETE_ALL into can_manage_library so the
    # checker passes; LIBRARY_PURGE stays admin-only.

    @pytest.fixture
    async def manage_library_key(self, db_session, auth_setup):
        """Mint an API key owned by the admin user with can_manage_library."""
        from backend.app.core.auth import generate_api_key
        from backend.app.models.api_key import APIKey

        admin = auth_setup["admin_user"]
        full_key, key_hash, key_prefix = generate_api_key()
        row = APIKey(
            name="lib-curation",
            key_hash=key_hash,
            key_prefix=key_prefix,
            user_id=admin.id,
            can_manage_library=True,
        )
        db_session.add(row)
        await db_session.commit()
        return full_key

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_apikey_with_manage_library_can_delete_file(
        self, async_client: AsyncClient, db_session, auth_setup, test_file, manage_library_key
    ):
        """Pre-#1832 this 403'd with "administrative operations" because
        LIBRARY_DELETE_ALL wasn't in _APIKEY_SCOPE_BY_PERMISSION."""
        from pathlib import Path

        from backend.app.core.config import settings as app_settings

        # Materialise the file on disk so the delete handler doesn't 500 on
        # the path it tries to unlink.
        file_path = Path(app_settings.base_dir) / test_file.file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("test content")

        response = await async_client.delete(
            f"/api/v1/library/files/{test_file.id}",
            headers={"X-API-Key": manage_library_key},
        )
        assert response.status_code == 200, response.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_apikey_with_manage_library_can_rename_file(
        self, async_client: AsyncClient, db_session, auth_setup, test_file, manage_library_key
    ):
        """PUT /library/files/{id} is gated on LIBRARY_UPDATE_ALL/OWN. Same
        #1832 path as delete."""
        response = await async_client.put(
            f"/api/v1/library/files/{test_file.id}",
            headers={"X-API-Key": manage_library_key},
            json={"filename": "renamed.txt"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["filename"] == "renamed.txt"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_apikey_with_manage_library_can_move_file(
        self, async_client: AsyncClient, db_session, auth_setup, test_file, manage_library_key
    ):
        """POST /library/files/move (bulk) is gated on LIBRARY_UPDATE_ALL/OWN
        — same checker, same #1832 path."""
        # Create a target folder the move can land in.
        from backend.app.models.library import LibraryFolder

        folder = LibraryFolder(name="target")
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        response = await async_client.post(
            "/api/v1/library/files/move",
            headers={"X-API-Key": manage_library_key},
            json={"file_ids": [test_file.id], "folder_id": folder.id},
        )
        assert response.status_code == 200, response.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_apikey_without_manage_library_still_blocked(
        self, async_client: AsyncClient, db_session, auth_setup, test_file
    ):
        """Regression guard: a key WITHOUT can_manage_library must still get
        403 — the fix widens the allowed-permission set, it doesn't bypass
        the per-key scope check."""
        from backend.app.core.auth import generate_api_key
        from backend.app.models.api_key import APIKey

        admin = auth_setup["admin_user"]
        full_key, key_hash, key_prefix = generate_api_key()
        row = APIKey(
            name="read-only",
            key_hash=key_hash,
            key_prefix=key_prefix,
            user_id=admin.id,
            can_read_status=True,
            can_manage_library=False,
        )
        db_session.add(row)
        await db_session.commit()

        response = await async_client.delete(
            f"/api/v1/library/files/{test_file.id}",
            headers={"X-API-Key": full_key},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_apikey_with_manage_library_still_cannot_purge(
        self, async_client: AsyncClient, db_session, auth_setup, manage_library_key
    ):
        """LIBRARY_PURGE deliberately stays in _APIKEY_DENIED_PERMISSIONS as
        a genuinely destructive op that bypasses the soft-delete window.
        can_manage_library does NOT grant it."""
        response = await async_client.post(
            "/api/v1/library/purge",
            headers={"X-API-Key": manage_library_key},
            json={"days_in_trash": 30},
        )
        assert response.status_code == 403


class TestPrintFileUploadValidation:
    """#1401: pre-flight rejection of unprintable uploads at the library +
    archive routes. Smoke tests the shared ``validate_print_file_upload``
    helper through both surfaces a user can reach with a drag-drop."""

    def _valid_3mf_bytes(self, name: str = "Metadata/plate_1.gcode") -> bytes:
        """Build a minimal-but-real zip with the gcode-3mf magic in it so
        the validator's ``startswith(b"PK\\x03\\x04")`` check passes."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(name, "; G-code\nG28\n")
        return buf.getvalue()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_library_rejects_raw_gcode_upload(self, async_client: AsyncClient, db_session):
        """``Foo.gcode`` direct uploads are blocked at the library route —
        the dispatcher would otherwise append ``.3mf`` and ship raw gcode
        to the printer as a fake 3MF."""
        files = {"file": ("plate_1.gcode", b"; raw gcode\nG28\n", "application/octet-stream")}
        response = await async_client.post("/api/v1/library/files", files=files)
        assert response.status_code == 400
        # Error message must name the actual remedy, not just say "invalid".
        assert "gcode.3mf" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_library_rejects_non_zip_3mf_upload(self, async_client: AsyncClient, db_session):
        """A ``.3mf`` upload whose body isn't a zip is rejected — covers
        raw gcode renamed to .3mf, corrupted downloads, etc."""
        files = {"file": ("model.3mf", b"; raw gcode\nG28\n", "application/octet-stream")}
        response = await async_client.post("/api/v1/library/files", files=files)
        assert response.status_code == 400
        assert "ZIP container" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_library_rejects_non_zip_gcode_3mf_upload(self, async_client: AsyncClient, db_session):
        """The compound-extension ``.gcode.3mf`` case is gated by the same
        zip-magic check — splitext returns just ``.3mf``, but the suffix
        match covers both."""
        files = {"file": ("plate_1.gcode.3mf", b"; raw gcode\nG28\n", "application/octet-stream")}
        response = await async_client.post("/api/v1/library/files", files=files)
        assert response.status_code == 400
        assert "ZIP container" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_library_accepts_valid_gcode_3mf_upload(self, async_client: AsyncClient, db_session):
        """A real ``.gcode.3mf`` zip uploads successfully — the existing
        happy path is not regressed by the new validation."""
        files = {
            "file": (
                "plate_1.gcode.3mf",
                self._valid_3mf_bytes(),
                "application/zip",
            )
        }
        response = await async_client.post("/api/v1/library/files", files=files)
        assert response.status_code == 200
        result = response.json()
        assert result["filename"] == "plate_1.gcode.3mf"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_library_upload_classifies_gcode_3mf_as_compound(self, async_client: AsyncClient, db_session):
        """#1600 follow-up: upload path used to strip to the trailing
        extension and store ``file_type='3mf'`` for sliced outputs, while
        the external-folder scan stored ``file_type='gcode.3mf'``. Now
        every ingest path goes through ``classify_file_type`` and
        produces the canonical compound name."""
        files = {
            "file": (
                "sliced.gcode.3mf",
                self._valid_3mf_bytes(),
                "application/zip",
            )
        }
        response = await async_client.post("/api/v1/library/files", files=files)
        assert response.status_code == 200
        assert response.json()["file_type"] == "gcode.3mf"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_library_get_gcode_endpoint_accepts_compound_file_type(self, async_client: AsyncClient, db_session):
        """#1600 follow-up: pre-fix, ``GET /files/{id}/gcode`` only handled
        ``file_type`` of ``gcode`` or ``3mf`` and 400'd on a row whose
        ``file_type`` was ``gcode.3mf`` — exactly the rows the external-
        folder scan was creating. The gate now treats both as 3MF and
        unzips the embedded gcode the same way."""
        from backend.app.models.library import LibraryFile

        # Persist a real `.gcode.3mf` zip under file_type='gcode.3mf' so
        # the endpoint hits the new branch.
        with tempfile.NamedTemporaryFile(suffix=".gcode.3mf", delete=False) as tmp:
            tmp.write(self._valid_3mf_bytes(name="Metadata/plate_1.gcode"))
            tmp_path = tmp.name

        lib_file = LibraryFile(
            filename="sliced.gcode.3mf",
            file_path=tmp_path,
            file_type="gcode.3mf",
            file_size=Path(tmp_path).stat().st_size,
        )
        db_session.add(lib_file)
        await db_session.commit()
        await db_session.refresh(lib_file)

        response = await async_client.get(f"/api/v1/library/files/{lib_file.id}/gcode")
        assert response.status_code == 200
        assert b"G28" in response.content

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_library_get_gcode_recovers_legacy_gcode_type_for_3mf(self, async_client: AsyncClient, db_session):
        """#1709 regression guard. Before the fix, ``slice_and_persist``
        wrote a `.gcode.3mf` ZIP container to disk but stored the row with
        ``file_type='gcode'`` — the preview endpoint then streamed the
        ZIP body as ``text/plain`` and the embedded G-code viewer saw
        ``PK\\x03\\x04...`` instead of the toolpath. New sliced rows now
        store ``file_type='gcode.3mf'``; rows already written under the
        bug self-heal because the endpoint also detects the ZIP via the
        ``.gcode.3mf`` filename suffix when the column is still legacy."""
        from backend.app.models.library import LibraryFile

        with tempfile.NamedTemporaryFile(suffix=".gcode.3mf", delete=False) as tmp:
            tmp.write(self._valid_3mf_bytes(name="Metadata/plate_1.gcode"))
            tmp_path = tmp.name

        lib_file = LibraryFile(
            filename="legacy-sliced.gcode.3mf",
            file_path=tmp_path,
            file_type="gcode",
            file_size=Path(tmp_path).stat().st_size,
        )
        db_session.add(lib_file)
        await db_session.commit()
        await db_session.refresh(lib_file)

        response = await async_client.get(f"/api/v1/library/files/{lib_file.id}/gcode")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert b"G28" in response.content
        # The whole point of #1709: must NOT be ZIP bytes shoved at the viewer.
        assert not response.content.startswith(b"PK")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_library_still_accepts_non_print_extensions(self, async_client: AsyncClient, db_session):
        """STL / image / other non-print uploads bypass the validator
        entirely — Bambuddy is also a library, not just a print dispatcher."""
        files = {"file": ("model.stl", b"solid test\nendsolid test", "application/octet-stream")}
        response = await async_client.post(
            "/api/v1/library/files", files=files, params={"generate_stl_thumbnails": "false"}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_upload_rejects_non_zip(self, async_client: AsyncClient, db_session):
        """``POST /archives/upload`` shares the same validator — covers the
        manual archive-upload entry point too."""
        files = {"file": ("model.3mf", b"; raw gcode\nG28\n", "application/octet-stream")}
        response = await async_client.post("/api/v1/archives/upload", files=files)
        assert response.status_code == 400
        assert "ZIP container" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_archive_bulk_upload_collects_per_file_errors(self, async_client: AsyncClient, db_session):
        """The bulk-archive route reports validation failures per file and
        continues processing the remaining items — one bad upload in a
        10-file drag-drop must not abort the whole batch."""
        good = self._valid_3mf_bytes()
        bad = b"; raw gcode\nG28\n"
        # httpx multipart with a list-of-tuples preserves order + same field name.
        files = [
            ("files", ("good.3mf", good, "application/zip")),
            ("files", ("bad.3mf", bad, "application/octet-stream")),
        ]
        response = await async_client.post("/api/v1/archives/upload-bulk", files=files)
        assert response.status_code == 200
        body = response.json()
        # The bulk route's archive_print may still reject the "good" file
        # downstream (no printer match, etc.) — we don't care about that
        # here; what matters is the bad file lands in `errors` with the
        # validator's message and the route didn't 500.
        assert body["failed"] >= 1
        bad_errors = [e for e in body["errors"] if e["filename"] == "bad.3mf"]
        assert bad_errors, body
        assert "ZIP container" in bad_errors[0]["error"]
