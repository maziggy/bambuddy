"""Every change to an archive's timelapse tells open Archives pages (TL-5).

Only the automatic scan sent ``archive_updated``. A video attached any other
way (picked or uploaded by hand, the external camera's, the MP4 conversion)
or deleted showed on other open pages only once they refetched.
"""

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_an_attach_is_announced(db_session, archive_factory, printer_factory, tmp_path, monkeypatch):
    from backend.app.core.config import settings as app_config
    from backend.app.services.archive import ArchiveService

    monkeypatch.setattr(app_config, "base_dir", tmp_path)
    monkeypatch.setattr(app_config, "archive_dir", tmp_path / "archive")
    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    announce = AsyncMock()

    with patch("backend.app.services.archive._announce_timelapse", announce):
        assert await ArchiveService(db_session).attach_timelapse(archive.id, b"video", "clip.mp4")

    announce.assert_awaited_once_with(archive.id, attached=True)


async def test_a_delete_is_announced(async_client, db_session, archive_factory, printer_factory, tmp_path, monkeypatch):
    from backend.app.core.config import settings as app_config

    monkeypatch.setattr(app_config, "base_dir", tmp_path)
    printer = await printer_factory()
    archive = await archive_factory(printer.id, timelapse_path="archive/x/clip.mp4")
    announce = AsyncMock()

    with patch("backend.app.services.archive._announce_timelapse", announce):
        response = await async_client.delete(f"/api/v1/archives/{archive.id}/timelapse")

    assert response.status_code == 200, response.text
    announce.assert_awaited_once_with(archive.id, attached=False)
