"""Integration tests for the client-rendered preview thumbnail upload (#2976).

STEP/PDF/spreadsheet previews render in the browser and post their first
render to POST /library/files/{id}/preview-thumbnail. These tests pin the
endpoint's contract: PNG-only, capped size, only for the client-preview file
types, and never replacing an existing thumbnail.
"""

import io

import pytest
from httpx import AsyncClient
from PIL import Image

from backend.app.core.config import settings as app_settings
from backend.app.models.library import LibraryFile


def _png_bytes(size: tuple[int, int] = (300, 300), color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def isolated_storage(monkeypatch, tmp_path):
    """Point thumbnail storage at a throwaway directory."""
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")
    return tmp_path


@pytest.fixture
async def file_factory(db_session):
    """Factory for LibraryFile rows of arbitrary file_type."""
    _counter = [0]

    async def _create_file(**kwargs):
        _counter[0] += 1
        counter = _counter[0]
        defaults = {
            "filename": f"part{counter}.step",
            "file_path": f"library/files/part{counter}.step",
            "file_type": "step",
            "file_size": 100,
        }
        defaults.update(kwargs)
        library_file = LibraryFile(**defaults)
        db_session.add(library_file)
        await db_session.commit()
        await db_session.refresh(library_file)
        return library_file

    return _create_file


class TestPreviewThumbnailUpload:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_sets_thumbnail_path(
        self, async_client: AsyncClient, db_session, file_factory, isolated_storage
    ):
        library_file = await file_factory(file_type="step")

        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            files={"thumbnail": ("preview.png", _png_bytes(), "image/png")},
        )
        assert response.status_code == 200
        assert response.json() == {"updated": True}

        await db_session.refresh(library_file)
        assert library_file.thumbnail_path
        stored = isolated_storage / library_file.thumbnail_path
        assert stored.exists()
        with Image.open(stored) as img:
            assert img.format == "PNG"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_downscales_oversized_image(
        self, async_client: AsyncClient, db_session, file_factory, isolated_storage
    ):
        library_file = await file_factory(file_type="pdf", filename="doc.pdf", file_path="library/files/doc.pdf")

        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            files={"thumbnail": ("preview.png", _png_bytes(size=(1024, 1024)), "image/png")},
        )
        assert response.status_code == 200

        await db_session.refresh(library_file)
        with Image.open(isolated_storage / library_file.thumbnail_path) as img:
            assert max(img.size) <= 512

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_skips_when_thumbnail_exists(
        self, async_client: AsyncClient, db_session, file_factory, isolated_storage
    ):
        library_file = await file_factory(file_type="csv", thumbnail_path="archive/library/thumbnails/existing.png")

        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            files={"thumbnail": ("preview.png", _png_bytes(), "image/png")},
        )
        assert response.status_code == 200
        assert response.json() == {"updated": False}

        await db_session.refresh(library_file)
        assert library_file.thumbnail_path == "archive/library/thumbnails/existing.png"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_rejected_for_server_rendered_types(
        self, async_client: AsyncClient, file_factory, isolated_storage
    ):
        # STL thumbnails are generated server-side; the client route must not
        # be able to overwrite them.
        library_file = await file_factory(file_type="stl", filename="part.stl")

        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            files={"thumbnail": ("preview.png", _png_bytes(), "image/png")},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_rejects_non_png(self, async_client: AsyncClient, file_factory, isolated_storage):
        library_file = await file_factory(file_type="step")

        buf = io.BytesIO()
        Image.new("RGB", (64, 64), "blue").save(buf, "JPEG")
        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            files={"thumbnail": ("preview.png", buf.getvalue(), "image/png")},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_rejects_garbage_bytes(self, async_client: AsyncClient, file_factory, isolated_storage):
        library_file = await file_factory(file_type="xlsx")

        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            files={"thumbnail": ("preview.png", b"not an image at all", "image/png")},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_rejects_oversized_payload(self, async_client: AsyncClient, file_factory, isolated_storage):
        library_file = await file_factory(file_type="ods")

        oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024)
        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            files={"thumbnail": ("preview.png", oversized, "image/png")},
        )
        assert response.status_code == 413

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_missing_file_returns_404(self, async_client: AsyncClient, isolated_storage):
        response = await async_client.post(
            "/api/v1/library/files/999999/preview-thumbnail",
            files={"thumbnail": ("preview.png", _png_bytes(), "image/png")},
        )
        assert response.status_code == 404
