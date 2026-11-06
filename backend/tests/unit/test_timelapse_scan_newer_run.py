"""A timelapse scan attaches only to the run it was started for (TL-2).

The scan polls the printer for minutes after a print ends. When the same
archive starts printing again meanwhile (the next plate of a shared archive, a
reprint), the archive row belongs to the new run: attaching then gave the new
run the old run's video, and the new run's own video was never imported
because the archive already had one.
"""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from backend.app.models.archive import PrintArchive
from backend.tests.unit.test_fallback_timelapse_baseline_2957 import (
    NEW_VIDEO,
    OLD_VIDEO,
    _entry,
    _patches,
    _seed,
)

pytestmark = pytest.mark.asyncio

FIRST_RUN = datetime(2026, 10, 19, 8, 0)
NEXT_RUN = datetime(2026, 10, 19, 9, 30)


async def _first_run_ended(maker, archive_id):
    async with maker() as db:
        archive = await db.get(PrintArchive, archive_id)
        archive.started_at = FIRST_RUN
        archive.timelapse_baseline = [OLD_VIDEO]
        await db.commit()


async def _next_run_starts(maker, archive_id):
    async with maker() as db:
        archive = await db.get(PrintArchive, archive_id)
        archive.started_at = NEXT_RUN
        archive.status = "printing"
        await db.commit()


async def test_a_run_that_started_during_the_download_does_not_get_the_video(test_engine, tmp_path, monkeypatch):
    from backend.app import main as main_module
    from backend.app.services import bambu_ftp

    maker, _printer_id, archive_id = await _seed(test_engine)
    await _first_run_ended(maker, archive_id)

    with _patches(main_module, maker, monkeypatch, tmp_path, lambda: [_entry(OLD_VIDEO), _entry(NEW_VIDEO)]):

        async def download(*_args, **_kwargs):
            await _next_run_starts(maker, archive_id)
            return b"x" * 1024

        monkeypatch.setattr(bambu_ftp, "download_file_bytes_async", AsyncMock(side_effect=download))
        await main_module._scan_for_timelapse_with_retries(archive_id, None)

    async with maker() as db:
        assert (await db.get(PrintArchive, archive_id)).timelapse_path is None
    bambu_ftp.delete_archived_timelapse.assert_not_awaited()


async def test_the_poll_stops_once_a_newer_run_has_started(test_engine, tmp_path, monkeypatch):
    from backend.app import main as main_module
    from backend.app.services import bambu_ftp

    maker, _printer_id, archive_id = await _seed(test_engine)
    await _first_run_ended(maker, archive_id)
    rounds = []

    with _patches(main_module, maker, monkeypatch, tmp_path, lambda: []):

        async def listing(_ip, _code, path, printer_model=None):
            if path != "/timelapse":
                return []
            rounds.append(path)
            if len(rounds) == 1:
                await _next_run_starts(maker, archive_id)
                return [_entry(OLD_VIDEO)]
            return [_entry(OLD_VIDEO), _entry(NEW_VIDEO)]

        monkeypatch.setattr(bambu_ftp, "list_files_async", listing)
        await main_module._scan_for_timelapse_with_retries(archive_id, None)

    assert len(rounds) == 1, "the poll went on for a run that is not its own"
    bambu_ftp.download_file_bytes_async.assert_not_awaited()


async def test_a_video_attached_during_the_download_is_kept(test_engine, tmp_path, monkeypatch):
    """Picked by hand (Scan for Timelapse) while the automatic scan downloaded."""
    from backend.app import main as main_module
    from backend.app.services import bambu_ftp

    maker, _printer_id, archive_id = await _seed(test_engine)
    await _first_run_ended(maker, archive_id)

    with _patches(main_module, maker, monkeypatch, tmp_path, lambda: [_entry(OLD_VIDEO), _entry(NEW_VIDEO)]):

        async def download(*_args, **_kwargs):
            async with maker() as db:
                archive = await db.get(PrintArchive, archive_id)
                archive.timelapse_path = "archive/1/picked_by_hand.mp4"
                await db.commit()
            return b"x" * 1024

        monkeypatch.setattr(bambu_ftp, "download_file_bytes_async", AsyncMock(side_effect=download))
        await main_module._scan_for_timelapse_with_retries(archive_id, None)

    async with maker() as db:
        assert (await db.get(PrintArchive, archive_id)).timelapse_path == "archive/1/picked_by_hand.mp4"
    bambu_ftp.delete_archived_timelapse.assert_not_awaited()
