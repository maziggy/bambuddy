"""Each print of an archive keeps its own timelapse and photos (archive redesign P1b).

Plates of one archive printed on two printers are two runs. Each run's scan,
layer stitch and finish photo find its own video by the run's id, and the
archive shows its newest run's. A reprint of a plate still replaces that
plate's earlier video, as when the archive held the only one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.config import settings as app_settings
from backend.app.models.archive import PrintArchive
from backend.app.models.print_log import INCLUDE_OPEN_RUNS, RUN_OPEN, PrintLogEntry

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

T1 = datetime(2026, 10, 30, 9)
T2 = T1 + timedelta(hours=1)
ARCHIVE_DIR = "archives/test"
logger = logging.getLogger(__name__)


@pytest.fixture
def files(tmp_path, monkeypatch):
    """The data directory, in tmp_path rather than the real one."""
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")
    (tmp_path / ARCHIVE_DIR).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def sessions(test_engine):
    """The sessions main and the services open, on the test database."""
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.main.async_session", maker),
        patch("backend.app.core.database.async_session", maker),
        patch("backend.app.services.archive._announce_timelapse", AsyncMock()),
        patch("backend.app.main.ws_manager", MagicMock(send_archive_updated=AsyncMock())),
    ):
        yield maker


@pytest.fixture
async def two_plates(db_session, printer_factory, files, sessions):
    """One archive: plate 1 printed on A, then plate 2 started on B since.
    Neither has a video yet."""
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    archive = PrintArchive(
        printer_id=b.id,
        filename="two.gcode.3mf",
        file_path=f"{ARCHIVE_DIR}/two.gcode.3mf",
        file_size=1,
        print_name="two",
        status="completed",
        started_at=T2,
    )
    db_session.add(archive)
    await db_session.flush()
    first = PrintLogEntry(
        archive_id=archive.id, printer_id=a.id, plate_id=1, status="completed", started_at=T1, photos=[]
    )
    second = PrintLogEntry(
        archive_id=archive.id, printer_id=b.id, plate_id=2, status="completed", started_at=T2, photos=[]
    )
    db_session.add_all([first, second])
    await db_session.commit()
    return SimpleNamespace(archive=archive.id, a=a.id, b=b.id, first=first.id, second=second.id, files=files)


async def _row(db_session, model, row_id):
    db_session.expire_all()
    return await db_session.get(model, row_id)


async def _set(db_session, model, row_id, **values):
    row = await _row(db_session, model, row_id)
    for name, value in values.items():
        setattr(row, name, value)
    await db_session.commit()


async def _attach(archive_id, data, name, **kwargs):
    from backend.app.core.database import async_session
    from backend.app.services.archive import ArchiveService

    async with async_session() as db:
        return await ArchiveService(db).attach_timelapse(archive_id, data, name, **kwargs)


class TestAttach:
    async def test_an_earlier_plates_video_is_its_runs_only(self, db_session, two_plates):
        assert await _attach(two_plates.archive, b"one", "video_1.mp4", plate_id=1, run_id=two_plates.first)

        first = await _row(db_session, PrintLogEntry, two_plates.first)
        assert first.timelapse_path == f"{ARCHIVE_DIR}/video_1.mp4"
        archive = await _row(db_session, PrintArchive, two_plates.archive)
        assert archive.timelapse_path is None
        assert (await _row(db_session, PrintLogEntry, two_plates.second)).timelapse_path is None

    async def test_the_newest_runs_video_is_the_archives_too(self, db_session, two_plates):
        assert await _attach(two_plates.archive, b"two", "video_2.mp4", plate_id=2, run_id=two_plates.second)

        archive = await _row(db_session, PrintArchive, two_plates.archive)
        assert (archive.timelapse_path, archive.timelapse_plate_id) == (f"{ARCHIVE_DIR}/video_2.mp4", 2)
        assert (await _row(db_session, PrintLogEntry, two_plates.second)).timelapse_path == f"{ARCHIVE_DIR}/video_2.mp4"

    async def test_a_printing_run_is_the_newest(self, db_session, two_plates):
        """A print starting clears the archive's video: plate 2's landing
        after plate 3 started is not the archive's."""
        db_session.add(
            PrintLogEntry(
                archive_id=two_plates.archive,
                printer_id=two_plates.a,
                status=RUN_OPEN,
                started_at=T2 + timedelta(hours=1),
            )
        )
        await db_session.commit()

        assert await _attach(two_plates.archive, b"two", "video_2.mp4", run_id=two_plates.second)

        assert (await _row(db_session, PrintArchive, two_plates.archive)).timelapse_path is None

    async def test_one_attached_by_hand_is_the_newest_runs(self, db_session, two_plates):
        assert await _attach(two_plates.archive, b"hand", "picked.mp4")

        assert (await _row(db_session, PrintArchive, two_plates.archive)).timelapse_path == f"{ARCHIVE_DIR}/picked.mp4"
        assert (await _row(db_session, PrintLogEntry, two_plates.second)).timelapse_path == f"{ARCHIVE_DIR}/picked.mp4"
        assert (await _row(db_session, PrintLogEntry, two_plates.first)).timelapse_path is None

    async def test_a_gone_run_gets_nothing(self, db_session, two_plates):
        assert not await _attach(two_plates.archive, b"x", "video_9.mp4", run_id=999999)

        assert not (two_plates.files / ARCHIVE_DIR / "video_9.mp4").exists()

    async def test_does_not_write_over_another_runs_video(self, db_session, two_plates):
        """The external-camera stitch always names its output layer_timelapse.mp4."""
        assert await _attach(two_plates.archive, b"one", "layer_timelapse.mp4", run_id=two_plates.first)
        assert await _attach(two_plates.archive, b"two", "layer_timelapse.mp4", run_id=two_plates.second)

        assert (two_plates.files / ARCHIVE_DIR / "layer_timelapse.mp4").read_bytes() == b"one"
        second = await _row(db_session, PrintLogEntry, two_plates.second)
        assert second.timelapse_path == f"{ARCHIVE_DIR}/layer_timelapse_2.mp4"

    async def test_an_earlier_runs_stitch_keeps_the_archives_video(self, db_session, two_plates):
        """Plate 2's stitch is the archive's; plate 1's, done later under the
        same name, is plate 1's own file."""
        assert await _attach(two_plates.archive, b"two", "layer_timelapse.mp4", run_id=two_plates.second)
        assert await _attach(two_plates.archive, b"one", "layer_timelapse.mp4", run_id=two_plates.first)

        assert (two_plates.files / ARCHIVE_DIR / "layer_timelapse.mp4").read_bytes() == b"two"
        first = await _row(db_session, PrintLogEntry, two_plates.first)
        assert first.timelapse_path == f"{ARCHIVE_DIR}/layer_timelapse_2.mp4"

    async def test_a_plates_video_replaces_its_earlier_prints(self, db_session, two_plates):
        """Plate 1 printed again on B while A's copy ran: once B's video is
        in, A's goes (a plate keeps its latest print's)."""
        (two_plates.files / ARCHIVE_DIR / "video_1.mp4").write_bytes(b"one")
        await _set(db_session, PrintLogEntry, two_plates.first, timelapse_path=f"{ARCHIVE_DIR}/video_1.mp4")
        later = PrintLogEntry(
            archive_id=two_plates.archive,
            printer_id=two_plates.b,
            plate_id=1,
            status="completed",
            started_at=T2 + timedelta(hours=1),
            photos=[],
        )
        db_session.add(later)
        await db_session.commit()
        later_id = later.id

        assert await _attach(two_plates.archive, b"again", "video_3.mp4", plate_id=1, run_id=later_id)

        assert (await _row(db_session, PrintLogEntry, two_plates.first)).timelapse_path is None
        assert not (two_plates.files / ARCHIVE_DIR / "video_1.mp4").exists()
        assert (await _row(db_session, PrintLogEntry, later_id)).timelapse_path == f"{ARCHIVE_DIR}/video_3.mp4"

    async def test_an_earlier_prints_late_video_leaves_the_later_ones(self, db_session, two_plates):
        later = PrintLogEntry(
            archive_id=two_plates.archive,
            printer_id=two_plates.b,
            plate_id=1,
            status="completed",
            started_at=T2 + timedelta(hours=1),
            timelapse_path=f"{ARCHIVE_DIR}/video_3.mp4",
        )
        (two_plates.files / ARCHIVE_DIR / "video_3.mp4").write_bytes(b"again")
        db_session.add(later)
        await db_session.commit()

        assert await _attach(two_plates.archive, b"one", "video_1.mp4", plate_id=1, run_id=two_plates.first)

        assert (two_plates.files / ARCHIVE_DIR / "video_3.mp4").exists()


class TestConversion:
    async def _convert(self, archive_id, source):
        from backend.app.services.archive import _convert_timelapse_to_mp4

        process = AsyncMock(returncode=0)
        process.communicate = AsyncMock(return_value=(b"", b""))
        source.with_suffix(".mp4").write_bytes(b"mp4")
        with (
            patch("backend.app.services.camera.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
        ):
            await _convert_timelapse_to_mp4(archive_id, source)

    async def test_an_earlier_runs_video_is_converted_for_it(self, db_session, two_plates):
        source = two_plates.files / ARCHIVE_DIR / "video_1.avi"
        source.write_bytes(b"avi")
        await _set(db_session, PrintLogEntry, two_plates.first, timelapse_path=f"{ARCHIVE_DIR}/video_1.avi")

        await self._convert(two_plates.archive, source)

        first = await _row(db_session, PrintLogEntry, two_plates.first)
        assert first.timelapse_path == f"{ARCHIVE_DIR}/video_1.mp4"
        assert (two_plates.files / ARCHIVE_DIR / "video_1.mp4").exists()
        assert not source.exists()
        assert (await _row(db_session, PrintArchive, two_plates.archive)).timelapse_path is None

    async def test_the_archive_and_its_run_both_move(self, db_session, two_plates):
        source = two_plates.files / ARCHIVE_DIR / "video_2.avi"
        source.write_bytes(b"avi")
        await _set(db_session, PrintLogEntry, two_plates.second, timelapse_path=f"{ARCHIVE_DIR}/video_2.avi")
        await _set(db_session, PrintArchive, two_plates.archive, timelapse_path=f"{ARCHIVE_DIR}/video_2.avi")

        await self._convert(two_plates.archive, source)

        assert (await _row(db_session, PrintLogEntry, two_plates.second)).timelapse_path == f"{ARCHIVE_DIR}/video_2.mp4"
        assert (await _row(db_session, PrintArchive, two_plates.archive)).timelapse_path == f"{ARCHIVE_DIR}/video_2.mp4"


class TestClaimed:
    async def test_another_plate_of_the_archive_holds_its_video(self, db_session, two_plates):
        from backend.app.services.print_log import claimed_timelapse_stems

        await _set(db_session, PrintLogEntry, two_plates.first, timelapse_path=f"{ARCHIVE_DIR}/video_1.mp4")
        third = PrintLogEntry(archive_id=two_plates.archive, printer_id=two_plates.a, status="completed", started_at=T2)
        db_session.add(third)
        await db_session.commit()

        claimed = await claimed_timelapse_stems(
            db_session, two_plates.a, archive_id=two_plates.archive, run_id=third.id
        )
        assert claimed == {"video_1"}
        own = await claimed_timelapse_stems(
            db_session, two_plates.a, archive_id=two_plates.archive, run_id=two_plates.first
        )
        assert own == set()

    async def test_without_a_run_the_archives_runs_are_its_own(self, db_session, two_plates):
        from backend.app.services.print_log import claimed_timelapse_stems

        await _set(db_session, PrintLogEntry, two_plates.first, timelapse_path=f"{ARCHIVE_DIR}/video_1.mp4")

        assert (
            await claimed_timelapse_stems(db_session, two_plates.a, archive_id=two_plates.archive, run_id=None) == set()
        )
        other = PrintArchive(filename="o.3mf", file_path="o/o.3mf", file_size=1, printer_id=two_plates.b)
        db_session.add(other)
        await db_session.commit()
        assert await claimed_timelapse_stems(db_session, two_plates.a, archive_id=other.id, run_id=None) == {"video_1"}


def _video(name, size=3):
    return {"name": name, "size": size, "path": f"/timelapse/{name}"}


class TestScan:
    async def _scan(self, plates, listings, *, baseline=None, on_list=None):
        from backend.app.main import _scan_for_timelapse_with_retries

        listed_printers = []

        async def list_videos(printer):
            listed_printers.append(printer.id)
            if on_list is not None:
                await on_list(len(listed_printers))
            return listings[min(len(listed_printers), len(listings)) - 1], "/timelapse"

        async def download(*_args, expected_size=None, **_kwargs):
            return b"v" * expected_size

        with (
            patch("backend.app.main._list_timelapse_videos", side_effect=list_videos),
            patch("backend.app.main.asyncio.sleep", AsyncMock()),
            patch("backend.app.main._timelapse_scan_max_attempts", return_value=3),
            patch("backend.app.services.bambu_ftp.download_file_bytes_async", side_effect=download),
            patch("backend.app.services.bambu_ftp.remote_file_settled", AsyncMock(return_value=True)),
            patch("backend.app.services.bambu_ftp.delete_archived_timelapse", AsyncMock()),
        ):
            await _scan_for_timelapse_with_retries(plates.archive, baseline, plate_id=1, run_id=plates.first)
        return listed_printers

    async def test_scans_the_printer_the_run_printed_on(self, db_session, two_plates):
        printers = await self._scan(two_plates, [[_video("new.mp4")]], baseline=set())

        assert printers == [two_plates.a]
        first = await _row(db_session, PrintLogEntry, two_plates.first)
        assert first.timelapse_path == f"{ARCHIVE_DIR}/new.mp4"

    async def test_the_next_plates_start_does_not_end_the_scan(self, db_session, two_plates):
        async def next_plate_starts(round_no):
            if round_no == 1:
                await _set(db_session, PrintArchive, two_plates.archive, started_at=T2 + timedelta(hours=1))

        await self._scan(two_plates, [[], [_video("new.mp4")]], baseline=set(), on_list=next_plate_starts)

        assert (await _row(db_session, PrintLogEntry, two_plates.first)).timelapse_path == f"{ARCHIVE_DIR}/new.mp4"

    async def test_reads_the_baseline_stored_on_its_run(self, db_session, two_plates):
        """After a restart: the archive's baseline is plate 2's start's."""
        await _set(
            db_session, PrintLogEntry, two_plates.first, timelapse_baseline=["old.mp4"], timelapse_baseline_unread=False
        )
        await _set(db_session, PrintArchive, two_plates.archive, timelapse_baseline=["old.mp4", "new.mp4"])

        await self._scan(two_plates, [[_video("old.mp4"), _video("new.mp4")]])

        assert (await _row(db_session, PrintLogEntry, two_plates.first)).timelapse_path == f"{ARCHIVE_DIR}/new.mp4"

    async def test_another_plates_video_on_the_archive_does_not_stop_it(self, db_session, two_plates):
        await _set(db_session, PrintArchive, two_plates.archive, timelapse_path=f"{ARCHIVE_DIR}/plate2.mp4")

        await self._scan(two_plates, [[_video("new.mp4")]], baseline=set())

        assert (await _row(db_session, PrintLogEntry, two_plates.first)).timelapse_path == f"{ARCHIVE_DIR}/new.mp4"

    async def _later_print(self, db_session, plates, *, printer_id, plate_id, video=None):
        db_session.add(
            PrintLogEntry(
                archive_id=plates.archive,
                printer_id=printer_id,
                plate_id=plate_id,
                status=RUN_OPEN if video is None else "completed",
                started_at=T2 + timedelta(hours=1),
                timelapse_path=video,
            )
        )
        await db_session.commit()

    async def test_a_reprint_of_its_plate_with_its_video_ends_it(self, db_session, two_plates):
        """The reprint's video is the plate's one (it replaces this one)."""
        await self._later_print(
            db_session, two_plates, printer_id=two_plates.b, plate_id=1, video=f"{ARCHIVE_DIR}/later.mp4"
        )

        await self._scan(two_plates, [[_video("new.mp4")]], baseline=set())

        assert (await _row(db_session, PrintLogEntry, two_plates.first)).timelapse_path is None

    async def test_a_reprint_of_its_plate_still_printing_elsewhere_does_not_end_it(self, db_session, two_plates):
        """The same plate printing on two printers at once: until the later
        print has a video, which it may never get, this one keeps its own."""
        await self._later_print(db_session, two_plates, printer_id=two_plates.b, plate_id=1)

        await self._scan(two_plates, [[_video("new.mp4")]], baseline=set())

        assert (await _row(db_session, PrintLogEntry, two_plates.first)).timelapse_path == f"{ARCHIVE_DIR}/new.mp4"

    async def test_the_next_print_on_its_printer_ends_it(self, db_session, two_plates):
        """A video landing now can be that print's."""
        await self._later_print(db_session, two_plates, printer_id=two_plates.a, plate_id=3)

        await self._scan(two_plates, [[_video("new.mp4")]], baseline=set())

        assert (await _row(db_session, PrintLogEntry, two_plates.first)).timelapse_path is None

    async def test_an_earlier_print_of_its_plate_does_not_end_it(self, db_session, two_plates):
        db_session.add(
            PrintLogEntry(
                archive_id=two_plates.archive,
                printer_id=two_plates.a,
                plate_id=1,
                status="completed",
                started_at=T1 - timedelta(hours=1),
            )
        )
        await db_session.commit()

        await self._scan(two_plates, [[_video("new.mp4")]], baseline=set())

        assert (await _row(db_session, PrintLogEntry, two_plates.first)).timelapse_path == f"{ARCHIVE_DIR}/new.mp4"

    async def test_another_plate_on_another_printer_does_not_end_it(self, db_session, two_plates):
        await self._later_print(db_session, two_plates, printer_id=two_plates.b, plate_id=3)

        await self._scan(two_plates, [[_video("new.mp4")]], baseline=set())

        assert (await _row(db_session, PrintLogEntry, two_plates.first)).timelapse_path == f"{ARCHIVE_DIR}/new.mp4"

    async def test_stops_once_its_run_has_a_video(self, db_session, two_plates):
        await _set(db_session, PrintLogEntry, two_plates.first, timelapse_path=f"{ARCHIVE_DIR}/kept.mp4")

        printers = await self._scan(two_plates, [[_video("new.mp4")]], baseline=set())

        assert printers == []
        assert (await _row(db_session, PrintLogEntry, two_plates.first)).timelapse_path == f"{ARCHIVE_DIR}/kept.mp4"


class TestBaseline:
    async def test_print_start_writes_it_on_the_run(self, db_session, two_plates):
        from backend.app.main import _capture_timelapse_baseline_at_start

        run = PrintLogEntry(archive_id=two_plates.archive, printer_id=two_plates.b, status=RUN_OPEN, started_at=T2)
        db_session.add(run)
        await db_session.commit()
        run_id = run.id
        with patch("backend.app.main._read_timelapse_baseline", AsyncMock(return_value=({"a.mp4"}, True))):
            await _capture_timelapse_baseline_at_start(MagicMock(), two_plates.b, logger, archive_id=two_plates.archive)

        db_session.expire_all()
        run = await db_session.get(PrintLogEntry, run_id, execution_options={INCLUDE_OPEN_RUNS: True})
        assert (run.timelapse_baseline, run.timelapse_baseline_unread) == (["a.mp4"], False)
        assert (await _row(db_session, PrintArchive, two_plates.archive)).timelapse_baseline == ["a.mp4"]

    async def test_a_reread_fills_in_its_run(self, db_session, two_plates):
        from backend.app.main import _reread_unread_timelapse_baseline

        await _set(db_session, PrintLogEntry, two_plates.first, timelapse_baseline=[], timelapse_baseline_unread=True)
        with (
            patch("backend.app.main._TIMELAPSE_BASELINE_REREAD_INTERVAL_SECONDS", 0),
            patch("backend.app.main._unread_baseline_round", return_value=True),
            patch("backend.app.main._read_timelapse_baseline", AsyncMock(return_value=({"a.mp4"}, True))),
        ):
            await _reread_unread_timelapse_baseline(two_plates.a, set(), two_plates.archive, T1, two_plates.first)

        first = await _row(db_session, PrintLogEntry, two_plates.first)
        assert (first.timelapse_baseline, first.timelapse_baseline_unread) == (["a.mp4"], False)


class TestLayerTimelapse:
    async def test_lands_on_its_run_after_the_next_plate_started(self, db_session, two_plates):
        from backend.app.main import _attach_layer_timelapse

        printer_video = two_plates.files / ARCHIVE_DIR / "video_1.mp4"
        printer_video.write_bytes(b"printer")
        await _set(db_session, PrintLogEntry, two_plates.first, timelapse_path=f"{ARCHIVE_DIR}/video_1.mp4")
        stitched = two_plates.files / "stitched.mp4"
        stitched.write_bytes(b"camera")

        await _attach_layer_timelapse(two_plates.archive, stitched, 1, T1, run_id=two_plates.first)

        first = await _row(db_session, PrintLogEntry, two_plates.first)
        assert first.timelapse_path == f"{ARCHIVE_DIR}/layer_timelapse.mp4"
        assert not printer_video.exists()
        assert not stitched.exists()
        assert (await _row(db_session, PrintArchive, two_plates.archive)).timelapse_path is None

    async def test_a_reprint_of_its_plate_with_its_video_drops_it(self, db_session, two_plates):
        from backend.app.main import _attach_layer_timelapse

        db_session.add(
            PrintLogEntry(
                archive_id=two_plates.archive,
                printer_id=two_plates.b,
                plate_id=1,
                status="completed",
                started_at=T2 + timedelta(hours=1),
                timelapse_path=f"{ARCHIVE_DIR}/later.mp4",
            )
        )
        await db_session.commit()
        stitched = two_plates.files / "stitched.mp4"
        stitched.write_bytes(b"camera")

        await _attach_layer_timelapse(two_plates.archive, stitched, 1, T1, run_id=two_plates.first)

        assert (await _row(db_session, PrintLogEntry, two_plates.first)).timelapse_path is None
        assert not stitched.exists()


class TestFinishPhoto:
    async def test_waits_for_its_runs_video_not_the_archives(self, db_session, two_plates):
        from backend.app.main import _capture_finish_photo_from_timelapse

        (two_plates.files / ARCHIVE_DIR / "video_2.mp4").write_bytes(b"plate 2")
        await _set(db_session, PrintArchive, two_plates.archive, timelapse_path=f"{ARCHIVE_DIR}/video_2.mp4")
        extract = AsyncMock(return_value=True)
        with patch("backend.app.services.camera.extract_video_last_frame", extract):
            photo, pending = await _capture_finish_photo_from_timelapse(
                two_plates.archive, two_plates.files / ARCHIVE_DIR, timeout=0, run_id=two_plates.first
            )

        assert (photo, pending) == (None, True)
        extract.assert_not_awaited()

    async def test_the_upgrade_leads_its_runs_photos(self, db_session, two_plates):
        from backend.app.main import _upgrade_finish_photo_from_timelapse

        await _set(db_session, PrintLogEntry, two_plates.first, photos=["live.jpg"])
        await _set(db_session, PrintArchive, two_plates.archive, photos=["live.jpg", "plate2.jpg"])
        with patch(
            "backend.app.main._capture_finish_photo_from_timelapse", AsyncMock(return_value=("frame.jpg", False))
        ) as capture:
            await _upgrade_finish_photo_from_timelapse(two_plates.archive, two_plates.files, run_id=two_plates.first)

        assert capture.await_args.kwargs["run_id"] == two_plates.first
        assert (await _row(db_session, PrintLogEntry, two_plates.first)).photos == ["frame.jpg", "live.jpg"]
        assert (await _row(db_session, PrintLogEntry, two_plates.second)).photos == []
        assert (await _row(db_session, PrintArchive, two_plates.archive)).photos == [
            "frame.jpg",
            "live.jpg",
            "plate2.jpg",
        ]


class TestReprint:
    async def test_replaces_the_plates_earlier_videos_only(self, db_session, two_plates):
        from backend.app.main import _drop_replaced_plate_videos

        for run_id, name in ((two_plates.first, "video_1.mp4"), (two_plates.second, "video_2.mp4")):
            (two_plates.files / ARCHIVE_DIR / name).write_bytes(b"v")
            await _set(db_session, PrintLogEntry, run_id, timelapse_path=f"{ARCHIVE_DIR}/{name}")

        await _drop_replaced_plate_videos(db_session, two_plates.archive, 1, None, logger)

        assert (await _row(db_session, PrintLogEntry, two_plates.first)).timelapse_path is None
        assert not (two_plates.files / ARCHIVE_DIR / "video_1.mp4").exists()
        assert (await _row(db_session, PrintLogEntry, two_plates.second)).timelapse_path == f"{ARCHIVE_DIR}/video_2.mp4"
        assert (two_plates.files / ARCHIVE_DIR / "video_2.mp4").exists()

    async def test_the_archives_video_judged_this_plates_goes_with_its_run(self, db_session, two_plates):
        from backend.app.main import _drop_replaced_plate_videos

        (two_plates.files / ARCHIVE_DIR / "old.mp4").write_bytes(b"v")
        await _set(db_session, PrintLogEntry, two_plates.second, timelapse_path=f"{ARCHIVE_DIR}/old.mp4")

        await _drop_replaced_plate_videos(db_session, two_plates.archive, None, f"{ARCHIVE_DIR}/old.mp4", logger)

        assert (await _row(db_session, PrintLogEntry, two_plates.second)).timelapse_path is None
        assert not (two_plates.files / ARCHIVE_DIR / "old.mp4").exists()

    async def test_another_plates_video_is_kept_even_if_named(self, db_session, two_plates):
        from backend.app.main import _drop_replaced_plate_videos

        (two_plates.files / ARCHIVE_DIR / "video_2.mp4").write_bytes(b"v")
        await _set(db_session, PrintLogEntry, two_plates.second, timelapse_path=f"{ARCHIVE_DIR}/video_2.mp4")

        await _drop_replaced_plate_videos(db_session, two_plates.archive, 1, f"{ARCHIVE_DIR}/video_2.mp4", logger)

        assert (two_plates.files / ARCHIVE_DIR / "video_2.mp4").exists()
        assert (await _row(db_session, PrintLogEntry, two_plates.second)).timelapse_path == f"{ARCHIVE_DIR}/video_2.mp4"


class TestRoutes:
    async def test_deleting_the_video_clears_its_run(self, async_client, db_session, two_plates):
        (two_plates.files / ARCHIVE_DIR / "video_2.mp4").write_bytes(b"v")
        await _set(db_session, PrintLogEntry, two_plates.second, timelapse_path=f"{ARCHIVE_DIR}/video_2.mp4")
        await _set(db_session, PrintArchive, two_plates.archive, timelapse_path=f"{ARCHIVE_DIR}/video_2.mp4")

        response = await async_client.delete(f"/api/v1/archives/{two_plates.archive}/timelapse")
        assert response.status_code == 200, response.text

        assert (await _row(db_session, PrintLogEntry, two_plates.second)).timelapse_path is None

    async def test_deleting_a_photo_takes_it_off_its_run(self, async_client, db_session, two_plates):
        await _set(db_session, PrintLogEntry, two_plates.first, photos=["a.jpg", "b.jpg"])
        await _set(db_session, PrintArchive, two_plates.archive, photos=["a.jpg", "b.jpg"])

        response = await async_client.delete(f"/api/v1/archives/{two_plates.archive}/photos/a.jpg")
        assert response.status_code == 200, response.text

        assert (await _row(db_session, PrintLogEntry, two_plates.first)).photos == ["b.jpg"]

    async def test_a_run_shows_its_own_video_and_photos(self, async_client, db_session, two_plates):
        await _set(db_session, PrintLogEntry, two_plates.first, photos=["one.jpg"], timelapse_path="a/one.mp4")
        await _set(
            db_session, PrintArchive, two_plates.archive, photos=["one.jpg", "two.jpg"], timelapse_path="a/two.mp4"
        )

        response = await async_client.get(f"/api/v1/archives/{two_plates.archive}?run_id={two_plates.first}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["photos"], body["timelapse_path"]) == (["one.jpg"], "a/one.mp4")

        none_yet = await async_client.get(f"/api/v1/archives/{two_plates.archive}?run_id={two_plates.second}")
        assert (none_yet.json()["photos"], none_yet.json()["timelapse_path"]) == (None, None)

    async def test_a_run_logged_before_keeps_the_archives_photos_with_a_video(
        self, async_client, db_session, two_plates
    ):
        """A video attached by hand to it is its own; photos it never had are the archive's."""
        await _set(db_session, PrintLogEntry, two_plates.second, photos=None, timelapse_path="a/picked.mp4")
        await _set(db_session, PrintArchive, two_plates.archive, photos=["two.jpg"], timelapse_path="a/picked.mp4")

        response = await async_client.get(f"/api/v1/archives/{two_plates.archive}?run_id={two_plates.second}")
        assert (response.json()["photos"], response.json()["timelapse_path"]) == (["two.jpg"], "a/picked.mp4")

    async def test_a_run_logged_before_shows_the_archives(self, async_client, db_session, two_plates):
        await _set(db_session, PrintLogEntry, two_plates.second, photos=None)
        await _set(db_session, PrintArchive, two_plates.archive, photos=["two.jpg"], timelapse_path="a/two.mp4")

        response = await async_client.get(f"/api/v1/archives/{two_plates.archive}?run_id={two_plates.second}")
        assert (response.json()["photos"], response.json()["timelapse_path"]) == (["two.jpg"], "a/two.mp4")


async def test_a_run_opened_at_its_start_keeps_its_own_photos(db_session, two_plates):
    from backend.app.services.print_log import open_run

    archive = await _row(db_session, PrintArchive, two_plates.archive)
    archive.started_at = T2 + timedelta(hours=1)
    run = await open_run(db_session, archive, printer_id=two_plates.a)
    assert run is not None and run.photos == []
