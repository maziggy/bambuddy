"""Photos on an archive that has no 3MF (#1820).

The finish-photo capture writes to ``<archive_dir>/<id>/photos/`` when the
archive has no ``file_path``. Every reader derived the directory from
``file_path`` instead, and ``Path("").parent`` is ``Path(".")`` -- so they all
resolved to ``<base_dir>/photos``. The photo was written to one place and
looked for in another: reads 404'd, deletes removed the name and left the file,
and the notification attachment never found the image.

The reporter hit the read. These cover all of it, plus the photos already
written to the old location, which must not become unreachable in the fix.
"""

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

PHOTO = "deadbeef.jpg"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


@pytest.fixture
def base_dir(monkeypatch, tmp_path):
    """Point both roots at a tmp dir, keeping their real relationship."""
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")
    return tmp_path


async def _fallback_archive(archive_factory, printer_factory, **kwargs):
    printer = await printer_factory()
    return await archive_factory(
        printer.id,
        print_name="Started From The Printer",
        filename="Started From The Printer.3mf",
        file_path="",
        **kwargs,
    )


def _write(directory, name=PHOTO, content=JPEG):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(content)


class TestReadingACapturedPhoto:
    async def test_a_captured_finish_photo_is_served(
        self, async_client: AsyncClient, archive_factory, printer_factory, base_dir
    ):
        """The reporter's 404: written by the capture, unreadable forever."""
        archive = await _fallback_archive(archive_factory, printer_factory, photos=[PHOTO])
        _write(base_dir / "archive" / str(archive.id) / "photos")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/photos/{PHOTO}")

        assert response.status_code == 200
        assert response.content == JPEG

    async def test_a_photo_in_the_old_shared_location_is_still_served(
        self, async_client: AsyncClient, archive_factory, printer_factory, base_dir
    ):
        """Manual uploads landed in <base_dir>/photos, where reads also looked,
        so those worked. Moving the lookup must not orphan them."""
        archive = await _fallback_archive(archive_factory, printer_factory, photos=[PHOTO])
        _write(base_dir / "photos")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/photos/{PHOTO}")

        assert response.status_code == 200
        assert response.content == JPEG

    async def test_a_photo_that_is_in_neither_place_is_404(
        self, async_client: AsyncClient, archive_factory, printer_factory, base_dir
    ):
        archive = await _fallback_archive(archive_factory, printer_factory, photos=[PHOTO])

        response = await async_client.get(f"/api/v1/archives/{archive.id}/photos/{PHOTO}")

        assert response.status_code == 404

    async def test_a_name_not_on_the_archive_is_404(
        self, async_client: AsyncClient, archive_factory, printer_factory, base_dir
    ):
        """The membership check comes first and still does."""
        archive = await _fallback_archive(archive_factory, printer_factory, photos=[PHOTO])
        _write(base_dir / "archive" / str(archive.id) / "photos", name="someone_elses.jpg")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/photos/someone_elses.jpg")

        assert response.status_code == 404


class TestANormalArchiveIsUnaffected:
    async def test_it_reads_from_its_own_directory(
        self, async_client: AsyncClient, archive_factory, printer_factory, base_dir
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, file_path="archives/test/print.gcode.3mf", photos=[PHOTO])
        _write(base_dir / "archives" / "test" / "photos")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/photos/{PHOTO}")

        assert response.status_code == 200

    async def test_it_does_not_borrow_from_the_shared_location(
        self, async_client: AsyncClient, archive_factory, printer_factory, base_dir
    ):
        """An archive with a real path has one location and only one. Looking
        in the shared pile as well would serve another archive's photo when
        the names ever collided."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, file_path="archives/test/print.gcode.3mf", photos=[PHOTO])
        _write(base_dir / "photos")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/photos/{PHOTO}")

        assert response.status_code == 404


class TestDeleting:
    async def test_a_captured_photo_is_removed_from_disk(
        self, async_client: AsyncClient, archive_factory, printer_factory, base_dir
    ):
        """It used to drop the name and leave the file, so the photo became
        both invisible and unremovable."""
        archive = await _fallback_archive(archive_factory, printer_factory, photos=[PHOTO])
        photos_dir = base_dir / "archive" / str(archive.id) / "photos"
        _write(photos_dir)

        response = await async_client.delete(f"/api/v1/archives/{archive.id}/photos/{PHOTO}")

        assert response.status_code == 200
        assert not (photos_dir / PHOTO).exists()

    async def test_a_photo_in_the_old_location_is_removed_too(
        self, async_client: AsyncClient, archive_factory, printer_factory, base_dir
    ):
        archive = await _fallback_archive(archive_factory, printer_factory, photos=[PHOTO])
        _write(base_dir / "photos")

        response = await async_client.delete(f"/api/v1/archives/{archive.id}/photos/{PHOTO}")

        assert response.status_code == 200
        assert not (base_dir / "photos" / PHOTO).exists()

    async def test_a_missing_file_still_clears_the_name(
        self, async_client: AsyncClient, archive_factory, printer_factory, base_dir
    ):
        archive = await _fallback_archive(archive_factory, printer_factory, photos=[PHOTO])

        response = await async_client.delete(f"/api/v1/archives/{archive.id}/photos/{PHOTO}")

        assert response.status_code == 200
        assert response.json()["photos"] is None


class TestUploading:
    async def test_an_uploaded_photo_can_be_read_back(
        self, async_client: AsyncClient, archive_factory, printer_factory, base_dir
    ):
        """Upload and read now agree on the location for these archives, which
        also means the directory has to be created with its parents."""
        archive = await _fallback_archive(archive_factory, printer_factory)

        upload = await async_client.post(
            f"/api/v1/archives/{archive.id}/photos",
            files={"file": ("shot.jpg", JPEG, "image/jpeg")},
        )
        assert upload.status_code == 200, upload.text
        filename = upload.json()["filename"]

        assert (base_dir / "archive" / str(archive.id) / "photos" / filename).is_file()

        read_back = await async_client.get(f"/api/v1/archives/{archive.id}/photos/{filename}")
        assert read_back.status_code == 200
        assert read_back.content == JPEG
