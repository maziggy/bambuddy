"""Does a no-3MF fallback archive ever get its timelapse? (#2957 follow-up)

The reporter of #2957 confirmed the archive recovery works and then noticed the
timelapse is not recovered with it, "even though it's there".

``_capture_timelapse_baseline_at_start`` says in its own docstring that it must
be called from every ``on_print_start`` path that proceeds to a real print, and
what breaks when it is not: the completion scan falls back to snapshotting the
card *after* the video has landed, so the new file ends up inside the baseline
and no diff can ever match. ``on_print_start`` has three such paths. The
new-archive and expected-archive branches call it. The fallback-archive branch
does not, and nothing else covers it -- ``on_print_running_observed`` is
restart-recovery only and is suppressed whenever ``on_print_start`` fires.

So a fallback archive reaches completion with no baseline in memory and none on
the row. These tests pin what happens then. Both drive the scan the way
``on_print_complete`` does for such an archive: ``_timelapse_baselines.pop``
misses, so ``baseline_names`` is None.

Two cases, split by whether the five-minute FTPS cool-off that caused the
fallback has expired by the time the print ends:

  * Longer than the cool-off -- the card is readable at completion, and the
    self-taken baseline swallows the new video.
  * Shorter than the cool-off -- the card is *not* readable, so the baseline is
    empty rather than merely late, and every video on the card reads as new once
    the cool-off clears inside the poll window.

The fix is two parts. The fallback branch now takes the same baseline as the
other two whenever the card is readable, and an *empty* listing taken while the
card is unreadable is no longer believed -- ``list_files_async`` answers [] when
its connect fails rather than raising, so it looks exactly like an empty card.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.archive import PrintArchive
from backend.app.models.printer import Printer

pytestmark = pytest.mark.asyncio

PRINTER_IP = "172.25.12.149"
OLD_VIDEO = "video_2019-01-01_00-00-00.mp4"
NEW_VIDEO = "video_2026-08-27_01-30-00.mp4"


def _entry(name: str) -> dict:
    return {"name": name, "size": 1024, "is_directory": False, "path": f"/timelapse/{name}"}


async def _seed(engine) -> tuple[async_sessionmaker, int, int]:
    """A printer plus the empty fallback archive, exactly as the cool-off
    branch of ``on_print_start`` writes it -- note ``timelapse_baseline`` is
    never set there, which is the whole point."""
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        printer = Printer(
            name="P1S",
            serial_number="01P00A3B1200579",
            ip_address=PRINTER_IP,
            access_code="12345678",
            model="P1S",
        )
        db.add(printer)
        await db.commit()
        await db.refresh(printer)

        archive = PrintArchive(
            printer_id=printer.id,
            filename="Desktop_Goose.gcode.3mf",
            file_path="",
            file_size=0,
            print_name="Desktop_Goose",
            status="completed",
            extra_data={"no_3mf_available": True, "no_3mf_reason": "ftps_cooloff"},
        )
        db.add(archive)
        await db.commit()
        await db.refresh(archive)
        assert archive.timelapse_baseline is None, "the fallback branch never captures one"
        return maker, printer.id, archive.id


def _patches(main_module, maker, monkeypatch, tmp_path, listing):
    """Shrink the poll to test speed and stand in for the FTP layer.

    ``listing`` is called per request and returns what ``/timelapse`` holds at
    that moment, so a test can let the cool-off expire mid-poll.
    """
    from backend.app.core.config import settings as app_config
    from backend.app.services import bambu_ftp

    # archive_dir is its own setting rather than derived, so both have to move
    # or attach_timelapse writes under the real one and then fails its
    # relative_to(base_dir).
    monkeypatch.setattr(app_config, "base_dir", tmp_path)
    monkeypatch.setattr(app_config, "archive_dir", tmp_path / "archive")
    monkeypatch.setattr(main_module, "_TIMELAPSE_SCAN_FIRST_DELAY_SECONDS", 0.05)
    monkeypatch.setattr(main_module, "_TIMELAPSE_SCAN_POLL_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(main_module, "_TIMELAPSE_SCAN_TIMEOUT_SECONDS", 3.0)

    async def _list(ip, code, path, printer_model=None):
        if path != "/timelapse":
            return []
        return listing()

    monkeypatch.setattr(bambu_ftp, "list_files_async", _list)
    monkeypatch.setattr(bambu_ftp, "download_file_bytes_async", AsyncMock(return_value=b"x" * 1024))
    monkeypatch.setattr(bambu_ftp, "remote_file_settled", AsyncMock(return_value=True))
    monkeypatch.setattr(bambu_ftp, "delete_archived_timelapse", AsyncMock(return_value=True))
    return patch.object(main_module, "async_session", maker)


class TestTheCooloffOutlastsThePrint:
    """Print shorter than the five-minute cool-off, so the card is still
    unreadable when the scan takes its baseline."""

    async def test_an_unclaimed_old_video_is_attached_to_this_print(self, test_engine, tmp_path, monkeypatch):
        from backend.app import main as main_module
        from backend.app.services.bambu_ftp import BambuFTPClient as BambuFTP

        maker, printer_id, archive_id = await _seed(test_engine)

        # The real cool-off, armed the way a failed TLS handshake arms it.
        # Short enough to expire inside the poll window, as it does on a print
        # that ends before the five minutes are up.
        BambuFTP._handshake_blocked_until[PRINTER_IP] = time.monotonic() + 0.25

        def listing():
            if BambuFTP.handshake_blocked(PRINTER_IP):
                # What the real path yields while blocked: list_files_async
                # returns [] when its connect fails rather than raising, so this
                # is indistinguishable from a card holding no videos.
                return []
            # The printer wrote this print's video at completion; the older one
            # was already there and belongs to no archive.
            return [_entry(OLD_VIDEO), _entry(NEW_VIDEO)]

        try:
            with _patches(main_module, maker, monkeypatch, tmp_path, listing):
                await main_module._scan_for_timelapse_with_retries(archive_id, None)
        finally:
            BambuFTP._handshake_blocked_until.pop(PRINTER_IP, None)

        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            attached = archive.timelapse_path

        # Before the fix the empty baseline licensed both videos as "new", the
        # first in listing order won, and the stale video was attached to this
        # print and then deleted off the printer. With no baseline to tell them
        # apart, both now stay on the printer for manual selection.
        assert attached is None, f"expected no attach, got {attached!r}"

    async def test_a_lone_video_still_resolves(self, test_engine, tmp_path, monkeypatch):
        """The steady state, and why the scan must not simply abort here:
        Bambuddy deletes each video from the printer once it is attached, so the
        usual card holds exactly this print's video and nothing else. One
        unclaimed candidate is unambiguous with or without a readable baseline,
        and aborting would lose the common case to protect the rare one."""
        from backend.app import main as main_module
        from backend.app.services.bambu_ftp import BambuFTPClient as BambuFTP

        maker, printer_id, archive_id = await _seed(test_engine)
        BambuFTP._handshake_blocked_until[PRINTER_IP] = time.monotonic() + 0.25

        def listing():
            if BambuFTP.handshake_blocked(PRINTER_IP):
                return []
            return [_entry(NEW_VIDEO)]

        try:
            with _patches(main_module, maker, monkeypatch, tmp_path, listing):
                await main_module._scan_for_timelapse_with_retries(archive_id, None)
        finally:
            BambuFTP._handshake_blocked_until.pop(PRINTER_IP, None)

        async with maker() as db:
            attached = (await db.get(PrintArchive, archive_id)).timelapse_path

        assert attached is not None, "one unclaimed video needs no baseline to disambiguate"
        assert NEW_VIDEO in attached, f"expected this print's video, got {attached!r}"


class TestTheCardWasReadableAtPrintStart:
    """The reporter's case, and every fallback archive on an H2/P2S: FTPS is
    healthy, so the fallback branch now takes a baseline like the other two."""

    async def test_the_persisted_baseline_picks_this_prints_video(self, test_engine, tmp_path, monkeypatch):
        from backend.app import main as main_module

        maker, printer_id, archive_id = await _seed(test_engine)

        # What the fallback branch now writes at print start: the card as it was
        # before this print, holding only the older video.
        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            archive.timelapse_baseline = [OLD_VIDEO]
            await db.commit()

        # By completion the printer has written this print's video alongside it.
        def listing():
            return [_entry(OLD_VIDEO), _entry(NEW_VIDEO)]

        with _patches(main_module, maker, monkeypatch, tmp_path, listing):
            await main_module._scan_for_timelapse_with_retries(archive_id, None)

        async with maker() as db:
            attached = (await db.get(PrintArchive, archive_id)).timelapse_path

        assert attached is not None, "the baseline makes this print's video the only new one"
        assert NEW_VIDEO in attached, f"expected this print's video, got {attached!r}"


class TestTheBaselineCaptureItself:
    """Part one, at the source: what the fallback branch calls."""

    async def test_an_unreadable_card_still_records_the_empty_baseline(self, test_engine, tmp_path, monkeypatch):
        """Deliberately ``[]`` rather than NULL. NULL sends completion off to
        take its own snapshot, by which point this print's video is on the card
        and gets swallowed by the very baseline meant to exclude it. The
        ambiguity an unread card creates is handled at the attach step."""
        from backend.app import main as main_module
        from backend.app.services.bambu_ftp import BambuFTPClient as BambuFTP

        maker, printer_id, archive_id = await _seed(test_engine)
        BambuFTP._handshake_blocked_until[PRINTER_IP] = time.monotonic() + 60

        async with maker() as db:
            printer = await db.get(Printer, printer_id)

        try:
            with _patches(main_module, maker, monkeypatch, tmp_path, lambda: []):
                await main_module._capture_timelapse_baseline_at_start(
                    printer, printer_id, main_module.logging.getLogger(__name__), archive_id=archive_id
                )
        finally:
            BambuFTP._handshake_blocked_until.pop(PRINTER_IP, None)

        async with maker() as db:
            assert (await db.get(PrintArchive, archive_id)).timelapse_baseline == []
        assert main_module._timelapse_baselines[printer_id] == set()
        main_module._timelapse_baselines.pop(printer_id, None)

    async def test_a_readable_card_is_captured_and_persisted(self, test_engine, tmp_path, monkeypatch):
        from backend.app import main as main_module

        maker, printer_id, archive_id = await _seed(test_engine)
        async with maker() as db:
            printer = await db.get(Printer, printer_id)

        try:
            with _patches(main_module, maker, monkeypatch, tmp_path, lambda: [_entry(OLD_VIDEO)]):
                await main_module._capture_timelapse_baseline_at_start(
                    printer, printer_id, main_module.logging.getLogger(__name__), archive_id=archive_id
                )

            async with maker() as db:
                assert (await db.get(PrintArchive, archive_id)).timelapse_baseline == [OLD_VIDEO]
            assert main_module._timelapse_baselines[printer_id] == {OLD_VIDEO}
        finally:
            main_module._timelapse_baselines.pop(printer_id, None)
