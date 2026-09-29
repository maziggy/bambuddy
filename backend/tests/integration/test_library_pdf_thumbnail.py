"""Integration tests for server-side PDF thumbnails (#2976).

A PDF gets its grid thumbnail when it enters the library - upload, ZIP
extraction, external-folder scan - instead of only after somebody has opened
the browser preview. The "Generate Thumbnails" batch backfills PDFs added
before that. An external scan renders them in its background backfill, not
inside the scan request.
"""

import io
import zipfile

import pytest
from httpx import AsyncClient
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.api.routes import library as library_routes
from backend.app.core.config import settings as app_settings
from backend.app.models.library import LibraryFile
from backend.tests.integration.test_ownership_permissions import TestOwnershipPermissionsSetup


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

    # The scan hands thumbnails to a background task. Capture it instead of
    # letting it run on its own, and give it the test database: the route
    # module holds its own reference to the real async_session.
    spawned = []
    monkeypatch.setattr(library_routes, "spawn_background_task", lambda coro, **_: spawned.append(coro))
    monkeypatch.setattr(
        library_routes,
        "async_session",
        async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False),
    )

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
    # The scan request itself leaves the rendering to the backfill...
    assert row.thumbnail_path is None
    assert len(spawned) == 1

    # ...which fills it in.
    await spawned[0]
    await db_session.refresh(row)
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


class TestBatchThumbnailOwnership(TestOwnershipPermissionsSetup):
    """Operators hold library:update_own only. The File Manager offers them the
    toolbar button and the per-file "Generate Thumbnail" entry, so the batch
    route must serve them - narrowed to their own files."""

    @staticmethod
    async def _pdf_row(db_session, base_dir, name, owner_id):
        pdf_path = base_dir / "archive" / "library" / "files" / name
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(_pdf_bytes())
        row = LibraryFile(
            filename=name,
            file_path=f"archive/library/files/{name}",
            file_type="pdf",
            file_size=pdf_path.stat().st_size,
            created_by_id=owner_id,
        )
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_all_missing_covers_only_their_own_files(
        self, async_client: AsyncClient, db_session, auth_setup, isolated_storage
    ):
        mine = await self._pdf_row(db_session, isolated_storage, "mine.pdf", auth_setup["operator_user"]["id"])
        theirs = await self._pdf_row(db_session, isolated_storage, "theirs.pdf", auth_setup["operator2_user"]["id"])
        ownerless = await self._pdf_row(db_session, isolated_storage, "ownerless.pdf", None)

        response = await async_client.post(
            "/api/v1/library/generate-stl-thumbnails",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            json={"all_missing": True},
        )

        assert response.status_code == 200, response.text
        assert [r["file_id"] for r in response.json()["results"]] == [mine.id]
        for row in (mine, theirs, ownerless):
            await db_session.refresh(row)
        _assert_png_thumbnail(isolated_storage, mine.thumbnail_path)
        assert theirs.thumbnail_path is None
        assert ownerless.thumbnail_path is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_operator_cannot_generate_for_someone_elses_file(
        self, async_client: AsyncClient, db_session, auth_setup, isolated_storage
    ):
        theirs = await self._pdf_row(db_session, isolated_storage, "theirs.pdf", auth_setup["operator2_user"]["id"])

        response = await async_client.post(
            "/api/v1/library/generate-stl-thumbnails",
            headers={"Authorization": f"Bearer {auth_setup['operator_token']}"},
            json={"file_ids": [theirs.id]},
        )

        assert response.status_code == 200, response.text
        assert response.json()["processed"] == 0
        await db_session.refresh(theirs)
        assert theirs.thumbnail_path is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_all_missing_covers_everyone(
        self, async_client: AsyncClient, db_session, auth_setup, isolated_storage
    ):
        mine = await self._pdf_row(db_session, isolated_storage, "mine.pdf", auth_setup["operator_user"]["id"])
        ownerless = await self._pdf_row(db_session, isolated_storage, "ownerless.pdf", None)

        response = await async_client.post(
            "/api/v1/library/generate-stl-thumbnails",
            headers={"Authorization": f"Bearer {auth_setup['admin_token']}"},
            json={"all_missing": True},
        )

        assert response.status_code == 200, response.text
        assert sorted(r["file_id"] for r in response.json()["results"]) == sorted([mine.id, ownerless.id])
