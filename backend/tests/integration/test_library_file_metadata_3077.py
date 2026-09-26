"""Integration tests for library file notes, external link and photos (#3077).

Pins the contracts of the details modal's backend: the PUT round-trip for
``external_url`` (empty string clears), the list-view indicators
(``has_notes`` / ``photo_count``), and the photo routes — membership check
before any disk access, extension allowlist, size cap, and the photo
directory going away with the file.
"""

import io

import pytest
from httpx import AsyncClient
from PIL import Image

from backend.app.core.config import settings as app_settings
from backend.app.models.library import LibraryFile
from backend.app.models.user import User
from backend.app.utils.library_paths import library_photos_dir


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def isolated_storage(monkeypatch, tmp_path):
    """Point library storage at a throwaway directory."""
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")
    return tmp_path


@pytest.fixture
async def file_factory(db_session):
    """Factory for LibraryFile rows with sensible defaults."""
    _counter = [0]

    async def _create_file(**kwargs):
        _counter[0] += 1
        counter = _counter[0]
        defaults = {
            "filename": f"part{counter}.3mf",
            "file_path": f"library/files/part{counter}.3mf",
            "file_type": "3mf",
            "file_size": 100,
        }
        defaults.update(kwargs)
        library_file = LibraryFile(**defaults)
        db_session.add(library_file)
        await db_session.commit()
        await db_session.refresh(library_file)
        return library_file

    return _create_file


async def _upload(async_client: AsyncClient, file_id: int, name: str = "result.jpg", content: bytes | None = None):
    return await async_client.post(
        f"/api/v1/library/files/{file_id}/photos",
        files={"file": (name, content if content is not None else _jpeg_bytes(), "image/jpeg")},
    )


class TestExternalUrlAndNotes:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_external_url_round_trip(self, async_client: AsyncClient, file_factory, isolated_storage):
        library_file = await file_factory()

        response = await async_client.put(
            f"/api/v1/library/files/{library_file.id}",
            json={"external_url": "https://www.printables.com/model/1234", "notes": "Print at 0.2mm"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["external_url"] == "https://www.printables.com/model/1234"
        assert body["notes"] == "Print at 0.2mm"
        assert body["photos"] == []

        detail = await async_client.get(f"/api/v1/library/files/{library_file.id}")
        assert detail.json()["external_url"] == "https://www.printables.com/model/1234"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_empty_string_clears_external_url(self, async_client: AsyncClient, file_factory, isolated_storage):
        library_file = await file_factory(external_url="https://example.com/x")

        response = await async_client.put(f"/api/v1/library/files/{library_file.id}", json={"external_url": ""})
        assert response.status_code == 200
        assert response.json()["external_url"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "url",
        ["javascript:alert(1)", "data:text/html,hi", "ftp://example.com/x", "www.printables.com/model/1"],
    )
    async def test_non_http_external_url_is_rejected(
        self, async_client: AsyncClient, file_factory, isolated_storage, url: str
    ):
        library_file = await file_factory(external_url="https://example.com/x")

        response = await async_client.put(f"/api/v1/library/files/{library_file.id}", json={"external_url": url})
        assert response.status_code == 422

        detail = await async_client.get(f"/api/v1/library/files/{library_file.id}")
        assert detail.json()["external_url"] == "https://example.com/x"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_omitted_external_url_is_left_alone(self, async_client: AsyncClient, file_factory, isolated_storage):
        library_file = await file_factory(external_url="https://example.com/x")

        response = await async_client.put(f"/api/v1/library/files/{library_file.id}", json={"notes": "hi"})
        assert response.status_code == 200
        assert response.json()["external_url"] == "https://example.com/x"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_detail_exposes_source_url_read_only(self, async_client: AsyncClient, file_factory, isolated_storage):
        library_file = await file_factory(source_type="makerworld", source_url="https://makerworld.com/models/1")

        detail = await async_client.get(f"/api/v1/library/files/{library_file.id}")
        assert detail.json()["source_url"] == "https://makerworld.com/models/1"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_carries_indicators_but_not_notes(
        self, async_client: AsyncClient, file_factory, isolated_storage
    ):
        with_meta = await file_factory(
            notes="secret notes", external_url="https://example.com/a", photos=["a.jpg", "b.png"]
        )
        bare = await file_factory()

        response = await async_client.get("/api/v1/library/files")
        assert response.status_code == 200
        by_id = {item["id"]: item for item in response.json()}

        assert by_id[with_meta.id]["has_notes"] is True
        assert by_id[with_meta.id]["photo_count"] == 2
        assert by_id[with_meta.id]["external_url"] == "https://example.com/a"
        assert "notes" not in by_id[with_meta.id]
        assert "photos" not in by_id[with_meta.id]

        assert by_id[bare.id]["has_notes"] is False
        assert by_id[bare.id]["photo_count"] == 0
        assert by_id[bare.id]["external_url"] is None


class TestPhotos:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_serve_delete_round_trip(
        self, async_client: AsyncClient, db_session, file_factory, isolated_storage
    ):
        library_file = await file_factory()

        upload = await _upload(async_client, library_file.id)
        assert upload.status_code == 200
        body = upload.json()
        filename = body["filename"]
        assert body["status"] == "uploaded"
        assert body["photos"] == [filename]
        assert filename.endswith(".jpg")
        assert (library_photos_dir(library_file.id) / filename).is_file()

        await db_session.refresh(library_file)
        assert library_file.photos == [filename]

        served = await async_client.get(f"/api/v1/library/files/{library_file.id}/photos/{filename}")
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/jpeg"
        assert served.content == _jpeg_bytes()

        detail = await async_client.get(f"/api/v1/library/files/{library_file.id}")
        assert detail.json()["photos"] == [filename]

        deleted = await async_client.delete(f"/api/v1/library/files/{library_file.id}/photos/{filename}")
        assert deleted.status_code == 200
        assert deleted.json() == {"status": "deleted", "photos": []}
        assert not (library_photos_dir(library_file.id) / filename).exists()

        gone = await async_client.get(f"/api/v1/library/files/{library_file.id}/photos/{filename}")
        assert gone.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_external_file_takes_photos_too(self, async_client: AsyncClient, file_factory, isolated_storage):
        library_file = await file_factory(is_external=True, file_path="/mnt/nas/part.stl", file_type="stl")

        upload = await _upload(async_client, library_file.id, name="shot.png")
        assert upload.status_code == 200
        assert (library_photos_dir(library_file.id) / upload.json()["filename"]).is_file()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unlisted_filename_is_404_even_when_on_disk(
        self, async_client: AsyncClient, file_factory, isolated_storage
    ):
        library_file = await file_factory()
        photos_dir = library_photos_dir(library_file.id)
        photos_dir.mkdir(parents=True)
        (photos_dir / "stray.jpg").write_bytes(_jpeg_bytes())

        response = await async_client.get(f"/api/v1/library/files/{library_file.id}/photos/stray.jpg")
        assert response.status_code == 404

        response = await async_client.delete(f"/api/v1/library/files/{library_file.id}/photos/stray.jpg")
        assert response.status_code == 404
        assert (photos_dir / "stray.jpg").exists()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_traversal_filename_is_rejected(self, async_client: AsyncClient, file_factory, isolated_storage):
        # Even a traversal-looking name that IS in the stored list never leaves
        # the photo directory — the membership check is not the only guard.
        library_file = await file_factory(photos=["../../secret.jpg"])
        (isolated_storage / "archive" / "secret.jpg").parent.mkdir(parents=True, exist_ok=True)
        (isolated_storage / "archive" / "secret.jpg").write_bytes(_jpeg_bytes())

        response = await async_client.get(
            f"/api/v1/library/files/{library_file.id}/photos/..%2F..%2Fsecret.jpg",
        )
        assert response.status_code in (400, 404)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_wrong_extension_is_rejected(self, async_client: AsyncClient, file_factory, isolated_storage):
        library_file = await file_factory()

        response = await _upload(async_client, library_file.id, name="notes.txt", content=b"hello")
        assert response.status_code == 400
        assert not library_photos_dir(library_file.id).exists()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_oversized_upload_is_rejected(self, async_client: AsyncClient, file_factory, isolated_storage):
        library_file = await file_factory()

        response = await _upload(async_client, library_file.id, content=b"\xff" * (10 * 1024 * 1024 + 1))
        assert response.status_code == 413
        assert not library_photos_dir(library_file.id).exists()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_to_missing_file_is_404(self, async_client: AsyncClient, isolated_storage):
        response = await _upload(async_client, 999999)
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_trash_purge_removes_photo_dir(
        self, async_client: AsyncClient, db_session, file_factory, isolated_storage
    ):
        library_file = await file_factory()
        upload = await _upload(async_client, library_file.id)
        assert upload.status_code == 200
        photos_dir = library_photos_dir(library_file.id)
        assert photos_dir.is_dir()

        trashed = await async_client.delete(f"/api/v1/library/files/{library_file.id}")
        assert trashed.status_code == 200
        # Soft-delete keeps the photos, like the file bytes and thumbnail.
        assert photos_dir.is_dir()

        purged = await async_client.delete(f"/api/v1/library/trash/{library_file.id}")
        assert purged.status_code == 200
        assert not photos_dir.exists()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_external_file_delete_removes_photo_dir(
        self, async_client: AsyncClient, file_factory, isolated_storage
    ):
        library_file = await file_factory(is_external=True, file_path="/mnt/nas/part.stl", file_type="stl")
        upload = await _upload(async_client, library_file.id)
        assert upload.status_code == 200
        photos_dir = library_photos_dir(library_file.id)

        response = await async_client.delete(f"/api/v1/library/files/{library_file.id}")
        assert response.status_code == 200
        assert response.json()["trashed"] is False
        assert not photos_dir.exists()


class TestPhotoDirectoryCleanup:
    """Every path that hard-deletes a library row takes its photos with it.

    The upload/delete round-trip, the trash purge and the external single-file
    delete are covered above; these are the remaining ones — folder delete,
    bulk delete of files and of whole folders, the external-folder scan that
    drops rows for files that vanished from the share, and the admin user
    delete that takes the user's items with them.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_folder_delete_removes_photo_dir(self, async_client: AsyncClient, file_factory, isolated_storage):
        folder = await async_client.post("/api/v1/library/folders", json={"name": "Brackets"})
        assert folder.status_code == 200
        folder_id = folder.json()["id"]
        library_file = await file_factory(folder_id=folder_id)
        assert (await _upload(async_client, library_file.id)).status_code == 200
        photos_dir = library_photos_dir(library_file.id)
        assert photos_dir.is_dir()

        response = await async_client.delete(f"/api/v1/library/folders/{folder_id}")
        assert response.status_code == 200
        assert not photos_dir.exists()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_delete_removes_photo_dir_of_hard_deleted_file(
        self, async_client: AsyncClient, file_factory, isolated_storage
    ):
        # External files bypass the trash, so bulk delete hard-deletes them;
        # a managed file is only soft-deleted and keeps its photos until the
        # sweeper runs.
        external = await file_factory(is_external=True, file_path="/mnt/nas/ext.stl", file_type="stl")
        managed = await file_factory()
        for library_file in (external, managed):
            assert (await _upload(async_client, library_file.id)).status_code == 200

        response = await async_client.post(
            "/api/v1/library/bulk-delete",
            json={"file_ids": [external.id, managed.id], "folder_ids": []},
        )
        assert response.status_code == 200
        assert response.json()["deleted_files"] == 2
        assert not library_photos_dir(external.id).exists()
        assert library_photos_dir(managed.id).is_dir()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_delete_removes_photo_dirs_under_a_deleted_folder(
        self, async_client: AsyncClient, file_factory, isolated_storage
    ):
        # The folder branch of bulk-delete lets the cascade hard-delete every
        # row in the subtree, so it owes the same photo cleanup the file
        # branch above it does — including files nested a level down.
        parent = await async_client.post("/api/v1/library/folders", json={"name": "Jigs"})
        assert parent.status_code == 200
        parent_id = parent.json()["id"]
        child = await async_client.post("/api/v1/library/folders", json={"name": "V2", "parent_id": parent_id})
        assert child.status_code == 200

        top_file = await file_factory(folder_id=parent_id)
        nested_file = await file_factory(folder_id=child.json()["id"])
        for library_file in (top_file, nested_file):
            assert (await _upload(async_client, library_file.id)).status_code == 200
            assert library_photos_dir(library_file.id).is_dir()

        response = await async_client.post(
            "/api/v1/library/bulk-delete",
            json={"file_ids": [], "folder_ids": [parent_id]},
        )
        assert response.status_code == 200
        assert response.json()["deleted_folders"] == 1
        assert (await async_client.get(f"/api/v1/library/files/{top_file.id}")).status_code == 404
        assert not library_photos_dir(top_file.id).exists()
        assert not library_photos_dir(nested_file.id).exists()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_deleting_a_user_with_their_items_removes_photo_dirs(
        self, async_client: AsyncClient, db_session, file_factory, isolated_storage
    ):
        # DELETE /users/{id}?delete_items=true bulk-deletes the rows, which is
        # a hard delete like any other and owes the photos with it.
        owner = User(username="photo-owner", password_hash="x", role="user")
        db_session.add(owner)
        await db_session.commit()
        await db_session.refresh(owner)

        owned = await file_factory(created_by_id=owner.id)
        someone_elses = await file_factory()
        for library_file in (owned, someone_elses):
            assert (await _upload(async_client, library_file.id)).status_code == 200
            assert library_photos_dir(library_file.id).is_dir()

        response = await async_client.delete(f"/api/v1/users/{owner.id}?delete_items=true")
        assert response.status_code == 204
        assert (await async_client.get(f"/api/v1/library/files/{owned.id}")).status_code == 404
        assert not library_photos_dir(owned.id).exists()
        assert library_photos_dir(someone_elses.id).is_dir()

    @pytest.fixture
    def external_share(self, monkeypatch, tmp_path):
        """Bambuddy's data dir and an opted-in external share, as siblings.

        The share cannot live under ``base_dir`` — ``_validate_external_path``
        refuses to mount a Bambuddy-managed directory, and the module's
        ``isolated_storage`` points ``base_dir`` at ``tmp_path`` itself.
        """
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(app_settings, "base_dir", data_dir)
        monkeypatch.setattr(app_settings, "archive_dir", data_dir / "archive")
        share = tmp_path / "share"
        share.mkdir()
        monkeypatch.setenv("BAMBUDDY_EXTERNAL_ROOTS", str(share))
        return share

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_external_scan_removes_photo_dir_of_vanished_file(self, async_client: AsyncClient, external_share):
        share = external_share
        (share / "bracket.stl").write_bytes(b"fakestl")

        folder = await async_client.post(
            "/api/v1/library/folders/external",
            json={"name": "Share", "external_path": str(share), "readonly": True, "show_hidden": False},
        )
        assert folder.status_code == 200
        folder_id = folder.json()["id"]

        scan = await async_client.post(f"/api/v1/library/folders/{folder_id}/scan")
        assert scan.status_code == 200
        assert scan.json()["added"] == 1

        listing = await async_client.get(f"/api/v1/library/files?folder_id={folder_id}")
        file_id = listing.json()[0]["id"]
        assert (await _upload(async_client, file_id)).status_code == 200
        photos_dir = library_photos_dir(file_id)
        assert photos_dir.is_dir()

        (share / "bracket.stl").unlink()

        rescan = await async_client.post(f"/api/v1/library/folders/{folder_id}/scan")
        assert rescan.status_code == 200
        assert rescan.json()["removed"] == 1
        assert not photos_dir.exists()
