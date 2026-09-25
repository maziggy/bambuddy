"""Integration tests for server-rendered PDF thumbnails (#2976).

A PDF gets its first page as the grid thumbnail on upload, through the ZIP
extraction path and through the batch "Generate thumbnails" route that used
to be STL-only. When the renderer is unavailable the upload still succeeds
and the browser-rendered fallback (``preview-thumbnail``) stays open.
"""

import io
import zipfile

import pytest
from httpx import AsyncClient
from PIL import Image

from backend.app.core.config import settings as app_settings
from backend.app.models.library import LibraryFile
from backend.tests.unit.services.test_pdf_thumbnail import minimal_pdf


@pytest.fixture
def isolated_storage(monkeypatch, tmp_path):
    """Point library storage at a throwaway directory."""
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
            "filename": f"drawing{counter}.pdf",
            "file_path": f"library/files/drawing{counter}.pdf",
            "file_type": "pdf",
            "file_size": 100,
        }
        defaults.update(kwargs)
        library_file = LibraryFile(**defaults)
        db_session.add(library_file)
        await db_session.commit()
        await db_session.refresh(library_file)
        return library_file

    return _create_file


def _assert_first_page_thumbnail(path):
    with Image.open(path) as img:
        assert img.format == "PNG"
        assert max(img.size) == 256


class TestPdfUploadThumbnail:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_sets_thumbnail_path(self, async_client: AsyncClient, db_session, isolated_storage):
        response = await async_client.post(
            "/api/v1/library/files",
            files={"file": ("drawing.pdf", minimal_pdf(200, 400), "application/pdf")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["file_type"] == "pdf"
        assert body["thumbnail_path"]

        stored = isolated_storage / body["thumbnail_path"]
        assert stored.exists()
        _assert_first_page_thumbnail(stored)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_ignores_stl_thumbnail_toggle(self, async_client: AsyncClient, isolated_storage):
        # The toggle exists because mesh renders take seconds; a pdfium render
        # does not, so a PDF is thumbnailed either way.
        response = await async_client.post(
            "/api/v1/library/files",
            files={"file": ("drawing.pdf", minimal_pdf(), "application/pdf")},
            params={"generate_stl_thumbnails": "false"},
        )
        assert response.status_code == 200
        assert response.json()["thumbnail_path"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_of_broken_pdf_succeeds_without_thumbnail(self, async_client: AsyncClient, isolated_storage):
        response = await async_client.post(
            "/api/v1/library/files",
            files={"file": ("broken.pdf", b"%PDF-1.4 this is not really a pdf", "application/pdf")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["file_type"] == "pdf"
        assert body["thumbnail_path"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_without_renderer_succeeds_without_thumbnail(
        self, async_client: AsyncClient, isolated_storage, monkeypatch
    ):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "pypdfium2":
                raise ImportError("No module named 'pypdfium2'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        response = await async_client.post(
            "/api/v1/library/files",
            files={"file": ("drawing.pdf", minimal_pdf(), "application/pdf")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["file_type"] == "pdf"
        assert body["thumbnail_path"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_client_fallback_still_fills_missing_pdf_thumbnail(
        self, async_client: AsyncClient, db_session, file_factory, isolated_storage
    ):
        # Renderer unavailable → no server thumbnail; the browser's first
        # preview render must still be accepted.
        library_file = await file_factory(thumbnail_path=None)

        buf = io.BytesIO()
        Image.new("RGB", (256, 256), "white").save(buf, "PNG")
        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            files={"thumbnail": ("preview.png", buf.getvalue(), "image/png")},
        )
        assert response.status_code == 200
        assert response.json() == {"updated": True}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_client_fallback_never_replaces_server_thumbnail(
        self, async_client: AsyncClient, db_session, file_factory, isolated_storage
    ):
        library_file = await file_factory(thumbnail_path="archive/library/thumbnails/server.png")

        buf = io.BytesIO()
        Image.new("RGB", (256, 256), "white").save(buf, "PNG")
        response = await async_client.post(
            f"/api/v1/library/files/{library_file.id}/preview-thumbnail",
            files={"thumbnail": ("preview.png", buf.getvalue(), "image/png")},
        )
        assert response.status_code == 200
        assert response.json() == {"updated": False}

        await db_session.refresh(library_file)
        assert library_file.thumbnail_path == "archive/library/thumbnails/server.png"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_zip_extraction_thumbnails_pdfs(self, async_client: AsyncClient, db_session, isolated_storage):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("docs/drawing.pdf", minimal_pdf())
        zip_buffer.seek(0)

        response = await async_client.post(
            "/api/v1/library/files/extract-zip",
            files={"file": ("bundle.zip", zip_buffer.read(), "application/zip")},
            params={"generate_stl_thumbnails": "false"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["extracted"] == 1

        result = await db_session.execute(LibraryFile.active().where(LibraryFile.id == body["files"][0]["file_id"]))
        library_file = result.scalar_one()
        assert library_file.file_type == "pdf"
        assert library_file.thumbnail_path
        _assert_first_page_thumbnail(isolated_storage / library_file.thumbnail_path)


class TestExternalScanBackfill:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_backfill_covers_pdfs_in_external_folders(
        self, db_session, test_engine, isolated_storage, tmp_path, monkeypatch
    ):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from backend.app.api.routes import library as library_routes
        from backend.app.models.library import LibraryFolder

        # The backfill opens its own sessions (it runs after the scan request
        # returned); point them at the test database.
        monkeypatch.setattr(
            library_routes,
            "async_session",
            async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False),
        )

        mount = tmp_path / "mount"
        mount.mkdir()
        pdf_path = mount / "datasheet.pdf"
        pdf_path.write_bytes(minimal_pdf())
        (mount / "note.step").write_bytes(b"ISO-10303-21;")

        folder = LibraryFolder(name="NAS", is_external=True, external_path=str(mount))
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        pdf_row = LibraryFile(
            folder_id=folder.id,
            is_external=True,
            filename="datasheet.pdf",
            file_path=str(pdf_path),
            file_type="pdf",
            file_size=pdf_path.stat().st_size,
            thumbnail_path=None,
        )
        step_row = LibraryFile(
            folder_id=folder.id,
            is_external=True,
            filename="note.step",
            file_path=str(mount / "note.step"),
            file_type="step",
            file_size=13,
            thumbnail_path=None,
        )
        db_session.add_all([pdf_row, step_row])
        await db_session.commit()
        await db_session.refresh(pdf_row)
        await db_session.refresh(step_row)

        await library_routes._backfill_external_stl_thumbnails([folder.id])

        await db_session.refresh(pdf_row)
        await db_session.refresh(step_row)
        assert pdf_row.thumbnail_path
        _assert_first_page_thumbnail(isolated_storage / pdf_row.thumbnail_path)
        # STEP stays with the browser-rendered path.
        assert step_row.thumbnail_path is None


class TestPdfBatchThumbnails:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_all_missing_counts_pdfs_and_stls(
        self, async_client: AsyncClient, db_session, file_factory, isolated_storage
    ):
        pdf_missing = await file_factory(thumbnail_path=None)
        _pdf_done = await file_factory(thumbnail_path="archive/library/thumbnails/done.png")
        stl_missing = await file_factory(
            filename="part.stl", file_path="library/files/part.stl", file_type="stl", thumbnail_path=None
        )
        _step_missing = await file_factory(
            filename="part.step", file_path="library/files/part.step", file_type="step", thumbnail_path=None
        )

        response = await async_client.post("/api/v1/library/generate-stl-thumbnails", json={"all_missing": True})
        assert response.status_code == 200
        body = response.json()
        assert body["processed"] == 2
        assert {r["file_id"] for r in body["results"]} == {pdf_missing.id, stl_missing.id}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_file_ids_renders_a_real_pdf(self, async_client: AsyncClient, db_session, isolated_storage):
        files_dir = isolated_storage / "archive" / "library" / "files"
        files_dir.mkdir(parents=True)
        pdf_path = files_dir / "drawing.pdf"
        pdf_path.write_bytes(minimal_pdf(300, 500))

        library_file = LibraryFile(
            filename="drawing.pdf",
            file_path="archive/library/files/drawing.pdf",
            file_type="pdf",
            file_size=pdf_path.stat().st_size,
            thumbnail_path=None,
        )
        db_session.add(library_file)
        await db_session.commit()
        await db_session.refresh(library_file)

        response = await async_client.post(
            "/api/v1/library/generate-stl-thumbnails", json={"file_ids": [library_file.id]}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["processed"] == 1
        assert body["succeeded"] == 1
        assert body["failed"] == 0

        await db_session.refresh(library_file)
        assert library_file.thumbnail_path
        _assert_first_page_thumbnail(isolated_storage / library_file.thumbnail_path)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_by_folder_includes_pdfs(self, async_client: AsyncClient, db_session, file_factory, isolated_storage):
        from backend.app.models.library import LibraryFolder

        folder = LibraryFolder(name="Docs")
        db_session.add(folder)
        await db_session.commit()
        await db_session.refresh(folder)

        in_folder = await file_factory(folder_id=folder.id, thumbnail_path=None)
        _at_root = await file_factory(folder_id=None, thumbnail_path=None)

        response = await async_client.post(
            "/api/v1/library/generate-stl-thumbnails", json={"folder_id": folder.id, "all_missing": True}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["processed"] == 1
        assert body["results"][0]["file_id"] == in_folder.id
