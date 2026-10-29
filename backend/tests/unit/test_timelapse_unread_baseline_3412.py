"""A print-start baseline taken off a card that could not be read (#3412).

The capture at print start knew the card was unreadable -- it logged so -- and
then recorded the empty listing exactly like a card that held nothing, both in
memory and on the archive row. The completion scan believed either copy, so
with two old videos still on the printer it attached the first of them to this
print, deleted it off the printer and took the finish photo from it. The
``require_unambiguous`` guard from #2957 only covered a baseline the scan took
itself at completion.

Now the baseline carries whether the card was read; an unread one keeps the
scan from choosing between several videos, and is read again while the print
runs, since the printer writes the video only once the print has ended.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.archive import PrintArchive
from backend.app.models.printer import Printer
from backend.app.services import bambu_ftp
from backend.app.services.bambu_ftp import FileListResult

pytestmark = pytest.mark.asyncio

PRINTER_IP = "172.25.12.150"
# Two videos of earlier prints that nothing claimed, as on the reporter's X2D.
OLD_A = "video_2026-08-22_20-19-02_4.mp4"
OLD_B = "video_2026-08-23_09-00-00.mp4"
NEW_VIDEO = "video_2026-10-24_14-00-00.mp4"
STARTED = datetime(2026, 10, 24, 11, 5, 1)


def _entry(name: str) -> dict:
    return {"name": name, "size": 1024, "is_directory": False, "path": f"/timelapse/{name}"}


@pytest.fixture(autouse=True)
def _forget_baselines():
    from backend.app import main

    yield
    main._timelapse_baselines.clear()
    main._timelapse_baselines_unread.clear()


async def _seed(engine, *, baseline: list[str] | None = None, unread: bool = False):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        printer = Printer(
            name="X2D", serial_number="20P9AJ5A0700123", ip_address=PRINTER_IP, access_code="12345678", model="X2D"
        )
        db.add(printer)
        await db.commit()
        await db.refresh(printer)
        archive = PrintArchive(
            printer_id=printer.id,
            filename="Bracket.gcode.3mf",
            file_path="",
            file_size=0,
            print_name="Bracket",
            status="printing",
            started_at=STARTED,
            timelapse_baseline=baseline,
            timelapse_baseline_unread=unread,
        )
        db.add(archive)
        await db.commit()
        await db.refresh(archive)
        return maker, printer, archive.id


def _ftp(main_module, maker, monkeypatch, tmp_path, listing, *, state="RUNNING", others=None):
    """Stand in for the FTP layer and the printer's live status.

    ``listing()`` is what ``/timelapse`` holds at that moment, or None while
    the card cannot be read -- what both listing calls then answer. The other
    candidate directories answer "no such directory" unless ``others`` gives
    them a FileListResult of their own.
    """
    from backend.app.core.config import settings as app_config
    from backend.app.services import bambu_ftp

    monkeypatch.setattr(app_config, "base_dir", tmp_path)
    monkeypatch.setattr(app_config, "archive_dir", tmp_path / "archive")
    monkeypatch.setattr(main_module, "_TIMELAPSE_SCAN_FIRST_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(main_module, "_TIMELAPSE_SCAN_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(main_module, "_TIMELAPSE_SCAN_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(main_module, "_TIMELAPSE_BASELINE_REREAD_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(main_module, "_TIMELAPSE_BASELINE_REREAD_MAX_ATTEMPTS", 20)

    async def _list(ip, code, path, printer_model=None):
        return (listing() or []) if path == "/timelapse" else []

    async def _list_result(ip, code, path, timeout=30.0, socket_timeout=None, printer_model=None):
        if path != "/timelapse":
            return (others or {}).get(path, FileListResult(files=[], available=False, missing=True))
        files = listing()
        return FileListResult(files=files or [], available=files is not None)

    status = SimpleNamespace(connected=True, state=state, sdcard=True, sdcard_reported=True)
    monkeypatch.setattr(bambu_ftp, "list_files_async", _list)
    monkeypatch.setattr(bambu_ftp, "list_files_result_async", _list_result)
    monkeypatch.setattr(bambu_ftp, "download_file_bytes_async", AsyncMock(return_value=b"x" * 1024))
    monkeypatch.setattr(bambu_ftp, "remote_file_settled", AsyncMock(return_value=True))
    monkeypatch.setattr(bambu_ftp, "delete_archived_timelapse", AsyncMock(return_value=True))
    monkeypatch.setattr(main_module.printer_manager, "get_status", lambda _pid: status)
    return status, patch.object(main_module, "async_session", maker)


async def _complete(main, archive_id, printer_id):
    """What on_print_complete does: schedule the scan, here run inline."""
    spawned = []
    with patch.object(main, "spawn_background_task", lambda coro, name=None: spawned.append(coro)):
        main._schedule_timelapse_scan(archive_id, printer_id, plate_id=None)
    assert len(spawned) == 1
    await spawned[0]


async def _attached(maker, archive_id) -> str | None:
    async with maker() as db:
        return (await db.get(PrintArchive, archive_id)).timelapse_path


class TestTheScanDoesNotGuess:
    """The reporter's chain, from the stored baseline on: [] at print start, and
    by completion the card readable again with this print's video beside two
    old ones."""

    async def test_a_stored_unread_baseline_leaves_several_videos_alone(self, test_engine, tmp_path, monkeypatch):
        from backend.app import main

        maker, _, archive_id = await _seed(test_engine, baseline=[], unread=True)
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: [_entry(OLD_A), _entry(OLD_B), _entry(NEW_VIDEO)])

        with session:
            await main._scan_for_timelapse_with_retries(archive_id, None)

        assert await _attached(maker, archive_id) is None, "an unread baseline must not pick between three videos"

    async def test_an_unread_baseline_handed_over_in_memory_leaves_them_alone(self, test_engine, tmp_path, monkeypatch):
        """The in-process path: on_print_complete hands the in-memory copy over,
        and the archive row is never consulted."""
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine)
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: [_entry(OLD_A), _entry(OLD_B), _entry(NEW_VIDEO)])
        main._timelapse_baselines[printer.id] = set()
        main._timelapse_baselines_unread.add(printer.id)

        with session:
            await _complete(main, archive_id, printer.id)
        assert printer.id not in main._timelapse_baselines
        assert printer.id not in main._timelapse_baselines_unread

        assert await _attached(maker, archive_id) is None

    async def test_a_stored_read_baseline_beats_an_unread_one_taken_after_a_restart(
        self, test_engine, tmp_path, monkeypatch
    ):
        """Read at print start; after a restart the running-observed capture
        could not read the card. The stored one is this run's."""
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine, baseline=[OLD_A, OLD_B], unread=False)
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: [_entry(OLD_A), _entry(OLD_B), _entry(NEW_VIDEO)])
        main._timelapse_baselines[printer.id] = set()
        main._timelapse_baselines_unread.add(printer.id)

        with session:
            await _complete(main, archive_id, printer.id)

        attached = await _attached(maker, archive_id)
        assert attached is not None and NEW_VIDEO in attached

    async def test_a_lone_video_still_resolves_off_an_unread_baseline(self, test_engine, tmp_path, monkeypatch):
        """The usual card: Bambuddy deletes each video once attached, so only
        this print's is there, and one candidate needs no baseline."""
        from backend.app import main

        maker, _, archive_id = await _seed(test_engine, baseline=[], unread=True)
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: [_entry(NEW_VIDEO)])

        with session:
            await main._scan_for_timelapse_with_retries(archive_id, None)

        attached = await _attached(maker, archive_id)
        assert attached is not None and NEW_VIDEO in attached

    async def test_a_read_baseline_still_picks_the_new_video(self, test_engine, tmp_path, monkeypatch):
        from backend.app import main

        maker, _, archive_id = await _seed(test_engine, baseline=[OLD_A, OLD_B], unread=False)
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: [_entry(OLD_A), _entry(OLD_B), _entry(NEW_VIDEO)])

        with session:
            await main._scan_for_timelapse_with_retries(archive_id, None)

        attached = await _attached(maker, archive_id)
        assert attached is not None and NEW_VIDEO in attached

    async def test_a_baseline_the_scan_takes_itself_off_a_failed_listing(self, test_engine, tmp_path, monkeypatch):
        """No cool-off running, the listing simply failed (a timeout, a 421 on
        this very connection): the old check only knew about the cool-off."""
        from backend.app import main

        maker, _, archive_id = await _seed(test_engine)
        rounds = iter([None, None])  # both baseline listings fail, the poll's succeed
        _, session = _ftp(
            main,
            maker,
            monkeypatch,
            tmp_path,
            lambda: next(rounds, [_entry(OLD_A), _entry(OLD_B), _entry(NEW_VIDEO)]),
        )

        with session:
            await main._scan_for_timelapse_with_retries(archive_id, None)

        assert await _attached(maker, archive_id) is None


class TestTheCaptureAtPrintStart:
    async def _capture(self, main, printer, archive_id):
        spawned = []

        def _spawn(coro, name=None):
            spawned.append(name)
            coro.close()

        with patch.object(main, "spawn_background_task", _spawn):
            await main._capture_timelapse_baseline_at_start(
                printer, printer.id, logging.getLogger(__name__), archive_id=archive_id
            )
        return spawned

    async def test_an_unreadable_card_is_recorded_as_unread(self, test_engine, tmp_path, monkeypatch):
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine)
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: None)

        with session:
            spawned = await self._capture(main, printer, archive_id)

        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            assert (archive.timelapse_baseline, archive.timelapse_baseline_unread) == ([], True)
        assert main._timelapse_baselines[printer.id] == set()
        assert printer.id in main._timelapse_baselines_unread
        assert spawned == [f"reread-timelapse-baseline-{printer.id}"]

    async def test_a_card_in_cooloff_is_recorded_as_unread(self, test_engine, tmp_path, monkeypatch):
        """The reporter's log line: the cool-off was running at print start."""
        from backend.app import main
        from backend.app.services.bambu_ftp import BambuFTPClient

        maker, printer, archive_id = await _seed(test_engine)
        # A listing would find videos -- it must not be asked while blocked.
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: [])
        BambuFTPClient._handshake_blocked_until[PRINTER_IP] = time.monotonic() + 60
        try:
            with session:
                await self._capture(main, printer, archive_id)
        finally:
            BambuFTPClient._handshake_blocked_until.pop(PRINTER_IP, None)

        async with maker() as db:
            assert (await db.get(PrintArchive, archive_id)).timelapse_baseline_unread is True

    async def test_an_empty_card_that_answered_is_read(self, test_engine, tmp_path, monkeypatch):
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine)
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: [])

        with session:
            spawned = await self._capture(main, printer, archive_id)

        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            assert (archive.timelapse_baseline, archive.timelapse_baseline_unread) == ([], False)
        assert printer.id not in main._timelapse_baselines_unread
        assert spawned == []

    async def test_a_failed_directory_is_not_outvoted_by_one_that_answered(self, test_engine, tmp_path, monkeypatch):
        """/timelapse answers empty, /timelapse/video -- where this card keeps
        its videos -- fails: the card was not read."""
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine)
        failed = {"/timelapse/video": FileListResult(files=[], available=False)}
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: [], others=failed)

        with session:
            await self._capture(main, printer, archive_id)

        async with maker() as db:
            assert (await db.get(PrintArchive, archive_id)).timelapse_baseline_unread is True

    async def test_a_card_without_any_timelapse_directory_is_read(self, test_engine, tmp_path, monkeypatch):
        """A fresh card: every directory answers 550. That is an answer, and a
        re-read could only get the same one."""
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine)
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: [])
        monkeypatch.setattr(
            bambu_ftp,
            "list_files_result_async",
            AsyncMock(return_value=FileListResult(files=[], available=False, missing=True)),
        )

        with session:
            spawned = await self._capture(main, printer, archive_id)

        async with maker() as db:
            assert (await db.get(PrintArchive, archive_id)).timelapse_baseline_unread is False
        assert spawned == []

    async def test_an_empty_first_listing_is_replaced_by_the_one_that_answered(
        self, test_engine, tmp_path, monkeypatch
    ):
        """The first listing fails, the second reads the card: its names are the
        baseline, not the empty set the first one returned."""
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine)
        rounds = iter([None])
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: next(rounds, [_entry(OLD_A), _entry(OLD_B)]))

        with session:
            await self._capture(main, printer, archive_id)

        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            assert (archive.timelapse_baseline, archive.timelapse_baseline_unread) == ([OLD_A, OLD_B], False)


class TestTheCardIsReadAgainDuringThePrint:
    async def _start(self, main, printer):
        names: set[str] = set()
        main._timelapse_baselines[printer.id] = names
        main._timelapse_baselines_unread.add(printer.id)
        return names

    async def test_a_later_reading_becomes_the_baseline(self, test_engine, tmp_path, monkeypatch):
        """And with it the scan picks this print's video out of three."""
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine, baseline=[], unread=True)
        card: list[list[dict] | None] = [None]
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: card[0])
        names = await self._start(main, printer)

        with session:
            task = asyncio.create_task(main._reread_unread_timelapse_baseline(printer.id, names, archive_id, STARTED))
            await asyncio.sleep(0.05)
            assert printer.id in main._timelapse_baselines_unread, "nothing to record while the card is unreadable"
            card[0] = [_entry(OLD_A), _entry(OLD_B)]  # the cool-off ends
            await asyncio.wait_for(task, 2)

            assert main._timelapse_baselines[printer.id] is names
            assert names == {OLD_A, OLD_B}
            assert printer.id not in main._timelapse_baselines_unread
            async with maker() as db:
                archive = await db.get(PrintArchive, archive_id)
                assert (archive.timelapse_baseline, archive.timelapse_baseline_unread) == ([OLD_A, OLD_B], False)

            card[0] = [_entry(OLD_A), _entry(OLD_B), _entry(NEW_VIDEO)]  # the print ends
            await _complete(main, archive_id, printer.id)

        attached = await _attached(maker, archive_id)
        assert attached is not None and NEW_VIDEO in attached

    async def test_a_disconnect_skips_a_round_and_does_not_end_the_rereading(self, test_engine, tmp_path, monkeypatch):
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine, baseline=[], unread=True)
        status, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: [_entry(OLD_A)])
        status.connected = False
        names = await self._start(main, printer)

        with session:
            task = asyncio.create_task(main._reread_unread_timelapse_baseline(printer.id, names, archive_id, STARTED))
            await asyncio.sleep(0.05)
            assert not task.done(), "a dropped connection says nothing about the print"
            assert names == set()
            status.connected = True
            await asyncio.wait_for(task, 2)

        assert names == {OLD_A}

    async def test_a_reading_that_returns_to_a_disconnected_printer_is_not_kept(
        self, test_engine, tmp_path, monkeypatch
    ):
        """The printer dropped off while the listing was out, so nothing says
        the print had not ended meanwhile."""
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine, baseline=[], unread=True)
        names = await self._start(main, printer)
        status = None
        card = [[_entry(OLD_A), _entry(NEW_VIDEO)]]

        def listing():
            if status.connected:
                status.connected = False  # drops while this listing is out
                return card[0]
            return None

        status, session = _ftp(main, maker, monkeypatch, tmp_path, listing)

        with session:
            task = asyncio.create_task(main._reread_unread_timelapse_baseline(printer.id, names, archive_id, STARTED))
            await asyncio.sleep(0.05)
            task.cancel()

        assert names == set()
        assert printer.id in main._timelapse_baselines_unread

    async def test_nothing_is_read_once_the_print_has_ended(self, test_engine, tmp_path, monkeypatch):
        """After the end the printer may already be writing this print's video,
        which a reading would put into the baseline."""
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine, baseline=[], unread=True)
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: [_entry(NEW_VIDEO)], state="FINISH")
        names = await self._start(main, printer)

        with session:
            await asyncio.wait_for(main._reread_unread_timelapse_baseline(printer.id, names, archive_id, STARTED), 2)

        assert names == set()
        async with maker() as db:
            assert (await db.get(PrintArchive, archive_id)).timelapse_baseline_unread is True

    async def test_a_reading_that_returns_after_completion_is_dropped(self, test_engine, tmp_path, monkeypatch):
        """The print ends while the listing is out: completion has taken the
        baseline by the time it comes back."""
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine, baseline=[], unread=True)
        names = await self._start(main, printer)
        status = None

        def listing():
            status.state = "FINISH"
            main._timelapse_baselines.pop(printer.id)
            main._timelapse_baselines_unread.discard(printer.id)
            return [_entry(OLD_A), _entry(NEW_VIDEO)]

        status, session = _ftp(main, maker, monkeypatch, tmp_path, listing)

        with session:
            await asyncio.wait_for(main._reread_unread_timelapse_baseline(printer.id, names, archive_id, STARTED), 2)

        assert names == set()
        async with maker() as db:
            assert (await db.get(PrintArchive, archive_id)).timelapse_baseline == []

    async def test_a_reading_still_in_the_print_but_with_the_baseline_gone_is_dropped(
        self, test_engine, tmp_path, monkeypatch
    ):
        """State alone is not the run: the next print can already be running."""
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine, baseline=[], unread=True)
        names = await self._start(main, printer)

        def listing():
            main._timelapse_baselines[printer.id] = {"the next print's"}
            return [_entry(OLD_A)]

        _, session = _ftp(main, maker, monkeypatch, tmp_path, listing)

        with session:
            await asyncio.wait_for(main._reread_unread_timelapse_baseline(printer.id, names, archive_id, STARTED), 2)

        assert names == set()
        async with maker() as db:
            assert (await db.get(PrintArchive, archive_id)).timelapse_baseline_unread is True

    async def test_a_reprinted_row_keeps_its_own_baseline(self, test_engine, tmp_path, monkeypatch):
        """The row passed to a newer run meanwhile: the reading belongs to the
        old run, so memory takes it and the row does not."""
        from backend.app import main

        maker, printer, archive_id = await _seed(test_engine, baseline=[], unread=True)
        _, session = _ftp(main, maker, monkeypatch, tmp_path, lambda: [_entry(OLD_A)])
        names = await self._start(main, printer)

        with session:
            await asyncio.wait_for(
                main._reread_unread_timelapse_baseline(printer.id, names, archive_id, datetime(2026, 10, 1)), 2
            )

        assert names == {OLD_A}
        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            assert (archive.timelapse_baseline, archive.timelapse_baseline_unread) == ([], True)
