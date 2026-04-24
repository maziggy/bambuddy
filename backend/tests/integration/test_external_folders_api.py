"""Integration tests for External Folder API endpoints."""

import os
import tempfile
from pathlib import Path

import pytest
from httpx import AsyncClient


class TestExternalFolderCreation:
    """Tests for POST /library/folders/external."""

    @pytest.fixture
    def external_dir(self, tmp_path):
        """Create a temporary directory to act as an external folder."""
        ext_dir = tmp_path / "nas_share"
        ext_dir.mkdir()
        # Add some test files
        (ext_dir / "benchy.3mf").write_bytes(b"fake3mf")
        (ext_dir / "bracket.stl").write_bytes(b"fakestl")
        (ext_dir / "print.gcode").write_text("G28\nG1 X10 Y10")
        (ext_dir / "readme.txt").write_text("not a print file")
        (ext_dir / ".hidden.3mf").write_bytes(b"hidden")
        return ext_dir

    @pytest.fixture
    def nested_external_dir(self, external_dir):
        """Create a nested subdirectory in the external folder."""
        sub = external_dir / "subfolder"
        sub.mkdir()
        (sub / "nested_part.stl").write_bytes(b"nestedstl")
        return external_dir

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_external_folder(self, async_client: AsyncClient, db_session, external_dir):
        """Verify external folder can be created with valid path."""
        data = {
            "name": "NAS Prints",
            "external_path": str(external_dir),
            "readonly": True,
            "show_hidden": False,
        }
        response = await async_client.post("/api/v1/library/folders/external", json=data)
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "NAS Prints"
        assert result["is_external"] is True
        assert result["external_readonly"] is True
        assert result["external_show_hidden"] is False
        assert result["external_path"] == str(external_dir.resolve())

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_external_folder_nonexistent_path(self, async_client: AsyncClient, db_session):
        """Verify 400 for non-existent path."""
        data = {
            "name": "Bad Path",
            "external_path": "/nonexistent/path/that/does/not/exist",
        }
        response = await async_client.post("/api/v1/library/folders/external", json=data)
        assert response.status_code == 400
        assert "does not exist" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_external_folder_system_dir_blocked(self, async_client: AsyncClient, db_session):
        """Verify system directories are blocked."""
        data = {
            "name": "System",
            "external_path": "/proc",
        }
        response = await async_client.post("/api/v1/library/folders/external", json=data)
        assert response.status_code == 400
        assert "system directory" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_external_folder_file_not_dir(self, async_client: AsyncClient, db_session, tmp_path):
        """Verify 400 when path is a file, not directory."""
        file_path = tmp_path / "not_a_dir.txt"
        file_path.write_text("hello")
        data = {
            "name": "Not A Dir",
            "external_path": str(file_path),
        }
        response = await async_client.post("/api/v1/library/folders/external", json=data)
        assert response.status_code == 400
        assert "not a directory" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_external_folder_duplicate_path(self, async_client: AsyncClient, db_session, external_dir):
        """Verify 409 when same path already linked."""
        data = {
            "name": "First",
            "external_path": str(external_dir),
        }
        response = await async_client.post("/api/v1/library/folders/external", json=data)
        assert response.status_code == 200

        data["name"] = "Duplicate"
        response = await async_client.post("/api/v1/library/folders/external", json=data)
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_external_folder_appears_in_tree(self, async_client: AsyncClient, db_session, external_dir):
        """Verify external folder shows up in folder tree with external fields."""
        data = {
            "name": "My NAS",
            "external_path": str(external_dir),
            "readonly": True,
        }
        await async_client.post("/api/v1/library/folders/external", json=data)

        response = await async_client.get("/api/v1/library/folders")
        assert response.status_code == 200
        folders = response.json()
        ext_folder = next((f for f in folders if f["name"] == "My NAS"), None)
        assert ext_folder is not None
        assert ext_folder["is_external"] is True
        assert ext_folder["external_readonly"] is True


def find_folder_in_tree(folders: list, name: str) -> dict | None:
    """Recursively search a folder tree for a folder by name."""
    for f in folders:
        if f["name"] == name:
            return f
        result = find_folder_in_tree(f.get("children", []), name)
        if result:
            return result
    return None


def collect_folder_names(folders: list) -> list[str]:
    """Recursively collect all folder names from a tree."""
    names = []
    for f in folders:
        names.append(f["name"])
        names.extend(collect_folder_names(f.get("children", [])))
    return names


class TestExternalFolderScan:
    """Tests for POST /library/folders/{id}/scan."""

    @pytest.fixture
    def external_dir(self, tmp_path):
        """Create a temporary directory with test files."""
        ext_dir = tmp_path / "prints"
        ext_dir.mkdir()
        (ext_dir / "benchy.3mf").write_bytes(b"fake3mf")
        (ext_dir / "bracket.stl").write_bytes(b"fakestl")
        (ext_dir / "print.gcode").write_text("G28\nG1 X10 Y10")
        (ext_dir / "readme.txt").write_text("not a print file")
        (ext_dir / ".hidden.3mf").write_bytes(b"hidden")
        sub = ext_dir / "subfolder"
        sub.mkdir()
        (sub / "nested.stl").write_bytes(b"nested")
        return ext_dir

    @pytest.fixture
    async def external_folder(self, async_client, db_session, external_dir):
        """Create an external folder via API."""
        data = {
            "name": "Scan Test",
            "external_path": str(external_dir),
            "readonly": True,
            "show_hidden": False,
        }
        response = await async_client.post("/api/v1/library/folders/external", json=data)
        return response.json()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_scan_discovers_files(self, async_client: AsyncClient, db_session, external_folder):
        """Verify scan discovers supported files and creates subfolders."""
        response = await async_client.post(f"/api/v1/library/folders/{external_folder['id']}/scan")
        assert response.status_code == 200
        result = response.json()
        # Should find: benchy.3mf, bracket.stl, print.gcode (root) + subfolder/nested.stl
        # Should skip: readme.txt (unsupported), .hidden.3mf (hidden)
        assert result["added"] == 4
        assert result["removed"] == 0

        # Root folder should have 3 files (nested.stl is in subfolder)
        response = await async_client.get(f"/api/v1/library/files?folder_id={external_folder['id']}")
        root_files = response.json()
        assert len(root_files) == 3
        root_filenames = {f["filename"] for f in root_files}
        assert root_filenames == {"benchy.3mf", "bracket.stl", "print.gcode"}

        # Subfolder should exist in the tree and contain nested.stl
        response = await async_client.get("/api/v1/library/folders")
        folders = response.json()
        subfolder = find_folder_in_tree(folders, "subfolder")
        assert subfolder is not None
        assert subfolder["is_external"] is True
        assert subfolder["parent_id"] == external_folder["id"]

        response = await async_client.get(f"/api/v1/library/files?folder_id={subfolder['id']}")
        sub_files = response.json()
        assert len(sub_files) == 1
        assert sub_files[0]["filename"] == "nested.stl"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_scan_skips_hidden_files(self, async_client: AsyncClient, db_session, external_folder):
        """Verify hidden files are skipped by default."""
        await async_client.post(f"/api/v1/library/folders/{external_folder['id']}/scan")

        # List files in root folder
        response = await async_client.get(f"/api/v1/library/files?folder_id={external_folder['id']}")
        assert response.status_code == 200
        files = response.json()
        filenames = [f["filename"] for f in files]
        assert ".hidden.3mf" not in filenames

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_scan_shows_hidden_when_enabled(self, async_client: AsyncClient, db_session, external_dir):
        """Verify hidden files found when show_hidden=True."""
        data = {
            "name": "Show Hidden Test",
            "external_path": str(external_dir),
            "show_hidden": True,
        }
        response = await async_client.post("/api/v1/library/folders/external", json=data)
        folder = response.json()

        response = await async_client.post(f"/api/v1/library/folders/{folder['id']}/scan")
        result = response.json()
        # Now should also find .hidden.3mf → 5 total
        assert result["added"] == 5

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_scan_idempotent(self, async_client: AsyncClient, db_session, external_folder):
        """Verify scanning twice doesn't duplicate files."""
        response1 = await async_client.post(f"/api/v1/library/folders/{external_folder['id']}/scan")
        assert response1.json()["added"] == 4

        response2 = await async_client.post(f"/api/v1/library/folders/{external_folder['id']}/scan")
        assert response2.json()["added"] == 0
        assert response2.json()["removed"] == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_scan_removes_deleted_files(
        self, async_client: AsyncClient, db_session, external_folder, external_dir
    ):
        """Verify scan removes entries for files no longer on disk."""
        await async_client.post(f"/api/v1/library/folders/{external_folder['id']}/scan")

        # Delete a file from disk
        (external_dir / "bracket.stl").unlink()

        response = await async_client.post(f"/api/v1/library/folders/{external_folder['id']}/scan")
        result = response.json()
        assert result["removed"] == 1
        assert result["added"] == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_scan_non_external_folder_fails(self, async_client: AsyncClient, db_session):
        """Verify scan fails on regular (non-external) folder."""
        # Create a regular folder
        data = {"name": "Regular Folder"}
        response = await async_client.post("/api/v1/library/folders", json=data)
        folder = response.json()

        response = await async_client.post(f"/api/v1/library/folders/{folder['id']}/scan")
        assert response.status_code == 400
        assert "not an external" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_scan_files_marked_external(self, async_client: AsyncClient, db_session, external_folder):
        """Verify scanned files have is_external=True in root and subfolders."""
        await async_client.post(f"/api/v1/library/folders/{external_folder['id']}/scan")

        # Check root folder files
        response = await async_client.get(f"/api/v1/library/files?folder_id={external_folder['id']}")
        files = response.json()
        assert len(files) > 0
        for f in files:
            assert f["is_external"] is True

        # Check subfolder files
        response = await async_client.get("/api/v1/library/folders")
        folders = response.json()
        subfolder = find_folder_in_tree(folders, "subfolder")
        assert subfolder is not None
        response = await async_client.get(f"/api/v1/library/files?folder_id={subfolder['id']}")
        sub_files = response.json()
        for f in sub_files:
            assert f["is_external"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_scan_creates_nested_subfolders(self, async_client: AsyncClient, db_session, external_dir):
        """Verify deeply nested directories create correct folder hierarchy."""
        # Create nested structure: deep/nested/dir/model.stl
        deep = external_dir / "deep" / "nested" / "dir"
        deep.mkdir(parents=True)
        (deep / "model.stl").write_bytes(b"deepstl")

        data = {
            "name": "Nested Test",
            "external_path": str(external_dir),
            "readonly": True,
            "show_hidden": False,
        }
        response = await async_client.post("/api/v1/library/folders/external", json=data)
        root = response.json()

        response = await async_client.post(f"/api/v1/library/folders/{root['id']}/scan")
        assert response.status_code == 200

        # Verify folder chain: root -> deep -> nested -> dir
        response = await async_client.get("/api/v1/library/folders")
        all_folders = response.json()

        deep = find_folder_in_tree(all_folders, "deep")
        assert deep is not None
        assert deep["parent_id"] == root["id"]
        assert deep["is_external"] is True

        nested = find_folder_in_tree(all_folders, "nested")
        assert nested is not None
        assert nested["parent_id"] == deep["id"]

        dir_folder = find_folder_in_tree(all_folders, "dir")
        assert dir_folder is not None
        assert dir_folder["parent_id"] == nested["id"]

        # model.stl should be in the "dir" folder
        response = await async_client.get(f"/api/v1/library/files?folder_id={dir_folder['id']}")
        files = response.json()
        assert len(files) == 1
        assert files[0]["filename"] == "model.stl"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_scan_skips_hidden_directories(self, async_client: AsyncClient, db_session, external_dir):
        """Verify hidden directories are skipped when show_hidden=False."""
        hidden_dir = external_dir / ".hidden_dir"
        hidden_dir.mkdir()
        (hidden_dir / "secret.stl").write_bytes(b"secret")

        data = {
            "name": "Hidden Dir Test",
            "external_path": str(external_dir),
            "readonly": True,
            "show_hidden": False,
        }
        response = await async_client.post("/api/v1/library/folders/external", json=data)
        root = response.json()

        response = await async_client.post(f"/api/v1/library/folders/{root['id']}/scan")
        result = response.json()
        # Should find 4 files (root 3 + subfolder/nested.stl) but NOT .hidden_dir/secret.stl
        assert result["added"] == 4

        # No ".hidden_dir" folder should be created
        response = await async_client.get("/api/v1/library/folders")
        folder_names = collect_folder_names(response.json())
        assert ".hidden_dir" not in folder_names

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_scan_removes_deleted_subfolder(
        self, async_client: AsyncClient, db_session, external_folder, external_dir
    ):
        """Verify scan removes empty subfolder entries when directory deleted from disk."""
        await async_client.post(f"/api/v1/library/folders/{external_folder['id']}/scan")

        # Verify subfolder exists
        response = await async_client.get("/api/v1/library/folders")
        subfolder = find_folder_in_tree(response.json(), "subfolder")
        assert subfolder is not None

        # Delete the subfolder from disk
        import shutil

        shutil.rmtree(external_dir / "subfolder")

        # Re-scan
        response = await async_client.post(f"/api/v1/library/folders/{external_folder['id']}/scan")
        result = response.json()
        assert result["removed"] == 1  # nested.stl removed

        # Subfolder should be cleaned up (empty + directory gone)
        response = await async_client.get("/api/v1/library/folders")
        subfolder = find_folder_in_tree(response.json(), "subfolder")
        assert subfolder is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_scan_subfolder_inherits_readonly(
        self, async_client: AsyncClient, db_session, external_folder, external_dir
    ):
        """Verify created subfolders inherit external_readonly from parent."""
        await async_client.post(f"/api/v1/library/folders/{external_folder['id']}/scan")

        response = await async_client.get("/api/v1/library/folders")
        subfolder = find_folder_in_tree(response.json(), "subfolder")
        assert subfolder is not None
        assert subfolder["external_readonly"] is True


class TestExternalFolderProtections:
    """Tests for read-only protections on external folders."""

    @pytest.fixture
    def external_dir(self, tmp_path):
        ext_dir = tmp_path / "readonly_share"
        ext_dir.mkdir()
        (ext_dir / "test.stl").write_bytes(b"fakestl")
        return ext_dir

    @pytest.fixture
    async def readonly_folder(self, async_client, db_session, external_dir):
        """Create a read-only external folder with files scanned."""
        data = {
            "name": "Read Only",
            "external_path": str(external_dir),
            "readonly": True,
        }
        response = await async_client.post("/api/v1/library/folders/external", json=data)
        folder = response.json()
        await async_client.post(f"/api/v1/library/folders/{folder['id']}/scan")
        return folder

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_to_readonly_folder_blocked(self, async_client: AsyncClient, db_session, readonly_folder):
        """Verify uploads to read-only external folders are blocked."""
        import io

        file_content = io.BytesIO(b"test content")
        response = await async_client.post(
            f"/api/v1/library/files?folder_id={readonly_folder['id']}",
            files={"file": ("test.gcode", file_content, "application/octet-stream")},
        )
        assert response.status_code == 403
        assert "read-only" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_move_to_readonly_folder_blocked(self, async_client: AsyncClient, db_session, readonly_folder):
        """Verify moving files to read-only external folder is blocked."""
        from backend.app.models.library import LibraryFile

        # Create a regular file
        lib_file = LibraryFile(
            filename="regular.3mf",
            file_path="/test/regular.3mf",
            file_size=1024,
            file_type="3mf",
        )
        db_session.add(lib_file)
        await db_session.commit()
        await db_session.refresh(lib_file)

        data = {"file_ids": [lib_file.id], "folder_id": readonly_folder["id"]}
        response = await async_client.post("/api/v1/library/files/move", json=data)
        assert response.status_code == 403
        assert "read-only" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_external_files_cannot_be_moved_out(self, async_client: AsyncClient, db_session, readonly_folder):
        """Verify external files can't be moved to other folders."""
        # Get the external file ID
        response = await async_client.get(f"/api/v1/library/files?folder_id={readonly_folder['id']}")
        files = response.json()
        assert len(files) > 0
        ext_file_id = files[0]["id"]

        # Try to move to root
        data = {"file_ids": [ext_file_id], "folder_id": None}
        response = await async_client.post("/api/v1/library/files/move", json=data)
        assert response.status_code == 200
        # File should be skipped, not moved
        result = response.json()
        assert result["moved"] == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_external_file_removes_db_only(
        self, async_client: AsyncClient, db_session, readonly_folder, external_dir
    ):
        """Verify deleting an external file only removes DB entry, not the file on disk."""
        response = await async_client.get(f"/api/v1/library/files?folder_id={readonly_folder['id']}")
        files = response.json()
        ext_file_id = files[0]["id"]
        ext_filename = files[0]["filename"]

        # Delete via API
        response = await async_client.delete(f"/api/v1/library/files/{ext_file_id}")
        assert response.status_code == 200

        # File should still exist on disk
        assert (external_dir / ext_filename).exists()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_external_folder_preserves_files(
        self, async_client: AsyncClient, db_session, readonly_folder, external_dir
    ):
        """Verify deleting an external folder doesn't delete files from disk."""
        response = await async_client.delete(f"/api/v1/library/folders/{readonly_folder['id']}")
        assert response.status_code == 200

        # Files should still exist on disk
        assert (external_dir / "test.stl").exists()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_zip_to_readonly_folder_blocked(self, async_client: AsyncClient, db_session, readonly_folder):
        """Verify ZIP extraction to read-only external folder is blocked."""
        import io
        import zipfile

        # Create a minimal zip
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("test.stl", b"fakestl")
        buf.seek(0)

        response = await async_client.post(
            f"/api/v1/library/files/extract-zip?folder_id={readonly_folder['id']}",
            files={"file": ("test.zip", buf, "application/zip")},
        )
        assert response.status_code == 403
        assert "read-only" in response.json()["detail"].lower()


class TestExternalFolderWritableUpload:
    """Tests for upload write-through to writable external folders (#1112).

    Before the fix, uploads to writable external folders silently landed in the
    internal library dir while the DB row pointed at the external folder —
    files were invisible when the mount was viewed from another machine.
    """

    @pytest.fixture
    def external_dir(self, tmp_path):
        ext_dir = tmp_path / "writable_share"
        ext_dir.mkdir()
        return ext_dir

    @pytest.fixture
    async def writable_folder(self, async_client, db_session, external_dir):
        data = {
            "name": "Writable NAS",
            "external_path": str(external_dir),
            "readonly": False,
        }
        response = await async_client.post("/api/v1/library/folders/external", json=data)
        assert response.status_code == 200
        return response.json()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_lands_on_external_mount(
        self, async_client: AsyncClient, db_session, writable_folder, external_dir
    ):
        """Bytes are written to ``<external_path>/<filename>``, not the internal library dir."""
        import io

        content = b"hello-external-world"
        response = await async_client.post(
            f"/api/v1/library/files?folder_id={writable_folder['id']}",
            files={"file": ("upload.stl", io.BytesIO(content), "application/octet-stream")},
        )
        assert response.status_code == 200, response.text

        on_disk = external_dir / "upload.stl"
        assert on_disk.exists(), "file must be written to the external mount"
        assert on_disk.read_bytes() == content

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_persists_correct_db_shape(
        self, async_client: AsyncClient, db_session, writable_folder, external_dir
    ):
        """DB row must have ``is_external=True`` and ``file_path`` = absolute external path,
        so scan-dedupe and deletion behaviour match scanned files."""
        import io

        from backend.app.models.library import LibraryFile

        response = await async_client.post(
            f"/api/v1/library/files?folder_id={writable_folder['id']}",
            files={"file": ("model.3mf", io.BytesIO(b"x"), "application/octet-stream")},
        )
        assert response.status_code == 200
        file_id = response.json()["id"]

        row = await db_session.get(LibraryFile, file_id)
        await db_session.refresh(row)
        assert row.is_external is True
        assert row.file_path == str((external_dir / "model.3mf").resolve())

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_filename_collision_returns_409(
        self, async_client: AsyncClient, db_session, writable_folder, external_dir
    ):
        """Re-uploading a filename that already exists on the mount must 409,
        not silently overwrite — matches scan's treatment of external files as
        externally-owned bytes."""
        import io

        (external_dir / "already.stl").write_bytes(b"prior")
        response = await async_client.post(
            f"/api/v1/library/files?folder_id={writable_folder['id']}",
            files={"file": ("already.stl", io.BytesIO(b"new"), "application/octet-stream")},
        )
        assert response.status_code == 409
        assert (external_dir / "already.stl").read_bytes() == b"prior"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_to_missing_external_path_returns_400(
        self, async_client: AsyncClient, db_session, writable_folder, external_dir
    ):
        """If the external mount has gone away between folder-create and
        upload, fail loud rather than silently misroute to internal storage."""
        import io
        import shutil

        shutil.rmtree(external_dir)

        response = await async_client.post(
            f"/api/v1/library/files?folder_id={writable_folder['id']}",
            files={"file": ("x.stl", io.BytesIO(b"x"), "application/octet-stream")},
        )
        assert response.status_code == 400
        assert "not accessible" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_rejects_path_traversal_filename(
        self, async_client: AsyncClient, db_session, writable_folder, external_dir
    ):
        """A malicious filename like ``../escape.stl`` must not write outside
        the external folder. Defence-in-depth — FastAPI already strips these
        on parse, but the resolve-and-relative_to guard is the final gate."""
        import io

        response = await async_client.post(
            f"/api/v1/library/files?folder_id={writable_folder['id']}",
            files={"file": ("../escape.stl", io.BytesIO(b"x"), "application/octet-stream")},
        )
        # Either a 400 from our traversal guard or a 200 with basename-stripped
        # filename inside the external dir — both prove nothing escaped.
        if response.status_code == 200:
            assert not (external_dir.parent / "escape.stl").exists()
            assert (external_dir / "escape.stl").exists() or (external_dir / "..escape.stl").exists()
        else:
            assert response.status_code in (400, 422)
            assert not (external_dir.parent / "escape.stl").exists()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_zip_to_writable_external_folder_rejected(
        self, async_client: AsyncClient, db_session, writable_folder
    ):
        """Extract-zip into writable external folders isn't supported (nested
        subfolder creation on the mount is a separate design). Users are
        pointed at the Scan flow instead."""
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a/b/c.stl", b"x")
        buf.seek(0)

        response = await async_client.post(
            f"/api/v1/library/files/extract-zip?folder_id={writable_folder['id']}",
            files={"file": ("test.zip", buf, "application/zip")},
        )
        assert response.status_code == 400
        assert "scan" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_non_external_upload_unchanged(self, async_client: AsyncClient, db_session):
        """Uploads with no folder_id (root) keep the existing internal-storage behaviour."""
        import io

        from backend.app.models.library import LibraryFile

        response = await async_client.post(
            "/api/v1/library/files",
            files={"file": ("root.stl", io.BytesIO(b"x"), "application/octet-stream")},
        )
        assert response.status_code == 200
        file_id = response.json()["id"]
        row = await db_session.get(LibraryFile, file_id)
        await db_session.refresh(row)
        assert row.is_external is False
        # Internal storage: file_path is UUID-scoped, stored as a relative path.
        assert not row.file_path.startswith("/")
