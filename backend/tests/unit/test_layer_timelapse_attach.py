"""The external camera's timelapse and the printer's own (TL-3).

With both on, both videos arrive for one archive. The camera's, the one set
up in Bambuddy, wins: it replaces the printer's (usually there first, since
stitching takes minutes), and the replaced file is deleted instead of being
left in the archive folder with nothing pointing at it. Frames taken for one
run never go to a newer run of the same archive.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.models.archive import PrintArchive
from backend.tests.unit.test_fallback_timelapse_baseline_2957 import _seed

pytestmark = pytest.mark.asyncio

RUN = datetime(2026, 10, 19, 8, 0)


async def _attach(test_engine, tmp_path, monkeypatch, *, existing: str | None, run_started_at=RUN):
    from backend.app import main as main_module
    from backend.app.core.config import settings as app_config

    monkeypatch.setattr(app_config, "base_dir", tmp_path)
    monkeypatch.setattr(app_config, "archive_dir", tmp_path / "archive")
    maker, _printer_id, archive_id = await _seed(test_engine)
    async with maker() as db:
        archive = await db.get(PrintArchive, archive_id)
        archive.started_at = RUN
        archive.timelapse_path = existing
        await db.commit()
    if existing:
        (tmp_path / existing).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / existing).write_bytes(b"printer video")
    stitched = tmp_path / "stitched.mp4"
    stitched.write_bytes(b"layer video")
    announce = AsyncMock()

    with (
        patch.object(main_module, "async_session", maker),
        patch("backend.app.services.archive._announce_timelapse", announce),
    ):
        await main_module._attach_layer_timelapse(archive_id, stitched, None, run_started_at)

    async with maker() as db:
        attached = (await db.get(PrintArchive, archive_id)).timelapse_path
    return attached, stitched, announce


async def test_it_replaces_the_printer_s_video_and_deletes_that_file(test_engine, tmp_path, monkeypatch):
    attached, stitched, announce = await _attach(test_engine, tmp_path, monkeypatch, existing="archive/1/video_a.mp4")

    assert attached and attached.endswith("layer_timelapse.mp4")
    assert not (tmp_path / "archive/1/video_a.mp4").exists(), "the replaced video would be left orphaned"
    assert not stitched.exists(), "the stitched temporary is removed either way"
    assert announce.await_args.kwargs == {"attached": True}


async def test_without_one_it_is_attached(test_engine, tmp_path, monkeypatch):
    attached, stitched, _announce = await _attach(test_engine, tmp_path, monkeypatch, existing=None)

    assert attached and attached.endswith("layer_timelapse.mp4")
    assert not stitched.exists()


async def test_a_newer_run_of_the_archive_does_not_get_it(test_engine, tmp_path, monkeypatch):
    """The stitch took long enough for the archive to start printing again."""
    attached, stitched, announce = await _attach(
        test_engine,
        tmp_path,
        monkeypatch,
        existing="archive/1/video_a.mp4",
        run_started_at=datetime(2026, 10, 19, 7, 0),
    )

    assert attached == "archive/1/video_a.mp4"
    assert (tmp_path / "archive/1/video_a.mp4").exists()
    assert not stitched.exists()
    announce.assert_not_awaited()
