"""Integration tests for server-side PDF thumbnails (#2976).

A PDF gets its grid thumbnail when it enters the library - upload, ZIP
extraction, external-folder scan - instead of only after somebody has opened
the browser preview. The "Generate Thumbnails" batch backfills PDFs added
before that.
"""

import io
import zipfile

import pytest
from httpx import AsyncClient
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import select

from backend.app.core.config import settings as app_settings
from backend.app.models.library import LibraryFile


def _pdf_bytes() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.rect(0, A4[1] - 200, 200, 200, fill=1, stroke=0)
    c.drawString(72, 72, "Bambuddy")
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture
def isolated_storage(monkeypatch, tmp_path):
    """Point library and thumbnail storage at a throwaway directory.

    A subdirectory, so an external share created beside it in ``tmp_path``
    is not inside the Bambuddy-managed tree (which the mount refuses).
    """
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setattr(app_settings, "base_dir", base)
    monkeypatch.setattr(app_settings, "archive_dir", base / "archive")
    return base


def _assert_png_thumbnail(base_dir, thumbnail_path):
    assert thumbnail_path
    stored = base_dir / thumbnail_path
    assert stored.exists()
    with Image.open(stored) as img:
        assert img.format == "PNG"
        assert img.size == (256, 256)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_upload_renders_pdf_thumbnail(async_client: AsyncClient, isolated_storage):
    response = await async_client.post(
        "/api/v1/library/files",
        files={"file": ("manual.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["file_type"] == "pdf"
    _assert_png_thumbnail(isolated_storage, body["thumbnail_path"])


@pytest.mark.asyncio
@pytest.mark.integration
async def test_upload_of_unreadable_pdf_still_lands_without_thumbnail(async_client: AsyncClient, isolated_storage):
    response = await async_client.post(
        "/api/v1/library/files",
        files={"file": ("broken.pdf", b"%PDF-1.4 not really", "application/pdf")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["file_type"] == "pdf"
    # The browser preview can still post its own render later.
    assert body["thumbnail_path"] is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_zip_extraction_renders_pdf_thumbnail(async_client: AsyncClient, db_session, isolated_storage):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("docs/manual.pdf", _pdf_bytes())
    response = await async_client.post(
        "/api/v1/library/files/extract-zip",
        files={"file": ("bundle.zip", buf.getvalue(), "application/zip")},
    )
    assert response.status_code == 200

    row = (await db_session.execute(select(LibraryFile).where(LibraryFile.filename == "manual.pdf"))).scalar_one()
    _assert_png_thumbnail(isolated_storage, row.thumbnail_path)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_external_scan_renders_pdf_thumbnail(
    async_client: AsyncClient, db_session, isolated_storage, tmp_path, monkeypatch
):
    monkeypatch.setenv("BAMBUDDY_EXTERNAL_ROOTS", str(tmp_path.parent))
    share = tmp_path / "nas_share"
    share.mkdir()
    (share / "manual.pdf").write_bytes(_pdf_bytes())

    created = await async_client.post(
        "/api/v1/library/folders/external",
        json={"name": "NAS", "external_path": str(share), "readonly": True, "show_hidden": False},
    )
    assert created.status_code == 200, created.text
    folder = created.json()
    response = await async_client.post(f"/api/v1/library/folders/{folder['id']}/scan")
    assert response.status_code == 200

    files = (await async_client.get(f"/api/v1/library/files?folder_id={folder['id']}")).json()
    pdf = next(f for f in files if f["filename"] == "manual.pdf")
    row = await db_session.get(LibraryFile, pdf["id"])
    _assert_png_thumbnail(isolated_storage, row.thumbnail_path)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_batch_generate_backfills_pdf_without_thumbnail(async_client: AsyncClient, db_session, isolated_storage):
    pdf_path = isolated_storage / "archive" / "library" / "files" / "old.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(_pdf_bytes())
    old = LibraryFile(
        filename="old.pdf",
        file_path="archive/library/files/old.pdf",
        file_type="pdf",
        file_size=pdf_path.stat().st_size,
    )
    # A spreadsheet is a client-thumbnail type too, but the server has no
    # renderer for it - the batch must leave it alone.
    sheet = LibraryFile(
        filename="parts.csv",
        file_path="archive/library/files/parts.csv",
        file_type="csv",
        file_size=10,
    )
    db_session.add_all([old, sheet])
    await db_session.commit()

    response = await async_client.post("/api/v1/library/generate-stl-thumbnails", json={"all_missing": True})
    assert response.status_code == 200
    result = response.json()
    assert result["processed"] == 1
    assert result["succeeded"] == 1
    assert result["results"][0]["file_id"] == old.id

    await db_session.refresh(old)
    await db_session.refresh(sheet)
    _assert_png_thumbnail(isolated_storage, old.thumbnail_path)
    assert sheet.thumbnail_path is None
