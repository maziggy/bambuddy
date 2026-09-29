"""Integration tests for the client-rendered preview thumbnail upload (#2976).

STEP/PDF/spreadsheet previews render in the browser and post their first
render to POST /library/files/{id}/preview-thumbnail. These tests pin the
endpoint's contract: PNG-only, capped size, only for the client-preview file
types, and never replacing an existing thumbnail.
"""

import io
import zlib

import pytest
from httpx import AsyncClient
from PIL import Image

from backend.app.core.config import settings as app_settings
from backend.app.models.library import LibraryFile
from backend.tests.integration.test_ownership_permissions import TestOwnershipPermissionsSetup


def _png_bytes(size: tuple[int, int] = (300, 300), color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _png_claiming(width: int, height: int) -> bytes:
    """A ~70-byte PNG whose IHDR declares a canvas it never delivers.

    This is the shape that costs memory: the header is what a decoder sizes
    its buffer from, and the payload stays small enough to pass any upload cap.
    """
    raw = bytearray(_png_bytes(size=(1, 1)))
    # 8-byte signature, then IHDR: length(4) type(4) data(13) crc(4).
    ihdr = raw[8:33]
    ihdr[8:12] = width.to_bytes(4, "big")
    ihdr[12:16] = height.to_bytes(4, "big")
    ihdr[21:25] = zlib.crc32(bytes(ihdr[4:21])).to_bytes(4, "big")
    raw[8:33] = ihdr
    return bytes(raw)


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

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_rejects_a_canvas_it_would_have_to_allocate(
        self, async_client: AsyncClient, db_session, file_factory, isolated_storage
    ):
        """12000x7000 is under PIL's bomb limit and would decode for real.

        A few KB of upload turns into ~340 MB of pixels, so the declared size
        has to be refused from the header, before load() is ever reached.
        """
        library_file = await file_factory(file_type="step")

        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            files={"thumbnail": ("preview.png", _png_claiming(12000, 7000), "image/png")},
        )

        assert response.status_code == 400
        await db_session.refresh(library_file)
        assert library_file.thumbnail_path is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_rejects_a_decompression_bomb_header(
        self, async_client: AsyncClient, file_factory, isolated_storage
    ):
        """PIL raises DecompressionBombError straight off Exception.

        It is neither an OSError nor a ValueError, so it escaped the decode
        guard and surfaced as a 500 — it is a bad request like any other.
        """
        library_file = await file_factory(file_type="pdf", filename="doc.pdf", file_path="library/files/doc.pdf")

        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            files={"thumbnail": ("preview.png", _png_claiming(20000, 20000), "image/png")},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_storage_failure_is_not_reported_as_a_bad_image(
        self, async_client: AsyncClient, monkeypatch, file_factory, isolated_storage
    ):
        """A full disk is ours to own, not "Invalid thumbnail image"."""

        payload = _png_bytes()
        library_file = await file_factory(file_type="xlsx")

        def _no_space(*args, **kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(Image.Image, "save", _no_space)

        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            files={"thumbnail": ("preview.png", payload, "image/png")},
        )

        assert response.status_code == 500
        assert response.json()["detail"] != "Invalid thumbnail image"


class TestPreviewThumbnailOwnership(TestOwnershipPermissionsSetup):
    """The ownership branch of the upload route (#2976).

    Reuses the shared auth setup: Operators hold library:update_own only, so
    they are the group that can tell the two branches apart.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_can_upload_for_their_own_file(
        self, async_client: AsyncClient, db_session, auth_setup, file_factory, isolated_storage
    ):
        library_file = await file_factory(file_type="step", created_by_id=auth_setup["operator_user"]["id"])

        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            files={"thumbnail": ("preview.png", _png_bytes(), "image/png")},
        )

        assert response.status_code == 200
        await db_session.refresh(library_file)
        assert library_file.thumbnail_path

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_cannot_upload_for_someone_elses_file(
        self, async_client: AsyncClient, db_session, auth_setup, file_factory, isolated_storage
    ):
        library_file = await file_factory(file_type="step", created_by_id=auth_setup["operator2_user"]["id"])

        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            files={"thumbnail": ("preview.png", _png_bytes(), "image/png")},
        )

        assert response.status_code == 403
        await db_session.refresh(library_file)
        assert library_file.thumbnail_path is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_cannot_upload_for_an_ownerless_file(
        self, async_client: AsyncClient, auth_setup, file_factory, isolated_storage
    ):
        """created_by_id is NULL — an *_own permission owns nothing here."""
        library_file = await file_factory(file_type="step", created_by_id=None)

        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            files={"thumbnail": ("preview.png", _png_bytes(), "image/png")},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_can_upload_for_an_ownerless_file(
        self, async_client: AsyncClient, auth_setup, file_factory, isolated_storage
    ):
        library_file = await file_factory(file_type="step", created_by_id=None)

        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
            files={"thumbnail": ("preview.png", _png_bytes(), "image/png")},
        )

        assert response.status_code == 200
