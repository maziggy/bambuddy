"""#3275: starting plate 2 of a Send All deleted plate 1's only timelapse.

Every plate of a Send All shares one archive. Print start cleared the archive's
timelapse and unlinked the file so a reprint would not reuse the previous run's
video (#1707) -- but for the next plate that file is the only copy of another
plate's run, the printer's having been deleted once it attached. Print start
now unlinks only when the same plate prints again, and attaching never writes
over a video the archive no longer points at.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.core.config import settings as app_settings
from backend.app.main import register_expected_print
from backend.app.services.archive import ArchiveService
from backend.tests.unit.test_reprint_clears_stale_timelapse import _build_mocks, _clear_dicts, _patches  # noqa: F401

RELPATH = "archives/42/video_plate1.mp4"


def _archive(timelapse_plate_id):
    archive = MagicMock()
    archive.id = 42
    archive.filename = "Plates.3mf"
    archive.subtask_id = None
    archive.print_time_seconds = None
    archive.created_by_id = None
    archive.printer_id = 1
    archive.print_name = "Plates"
    archive.status = "completed"
    archive.file_path = "archives/42/Plates.3mf"
    archive.energy_start_kwh = None
    archive.timelapse_path = RELPATH
    archive.timelapse_plate_id = timelapse_plate_id
    return archive


async def _start(tmp_path: Path, archive, plate_id: int) -> Path:
    """Start ``plate_id`` of the shared archive; returns the old video's path."""
    video = tmp_path / RELPATH
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"plate 1 video")

    printer = MagicMock(id=1, auto_archive=True, external_camera_enabled=False, external_camera_url=None)
    register_expected_print(1, "Plates.3mf", archive_id=42, ams_mapping=None, plate_id=plate_id)
    session = _build_mocks(printer, archive)
    (session_p, notif_p, plug_p, ws_p, pm_p, relay_p, *rest) = _patches()
    with (
        session_p as session_maker,
        notif_p as notif,
        plug_p as plug,
        ws_p as ws,
        pm_p as pm,
        relay_p as relay,
        rest[0],
        rest[1],
        rest[2],
        rest[3],
        rest[4],
        patch.object(app_settings, "base_dir", tmp_path),
    ):
        session_maker.return_value = session
        notif.on_print_start = AsyncMock()
        plug.on_print_start = AsyncMock()
        ws.send_print_start = AsyncMock()
        ws.send_archive_updated = AsyncMock()
        relay.on_print_start = AsyncMock()
        pm.get_printer = MagicMock(return_value=MagicMock(name="Test", serial_number="TEST123"))

        from backend.app.main import on_print_start

        await on_print_start(1, {"filename": "Plates.3mf", "subtask_name": "Plates"})
    return video


class TestPrintStart:
    @pytest.mark.asyncio
    async def test_the_next_plate_keeps_the_previous_plates_video(self, tmp_path):
        archive = _archive(timelapse_plate_id=1)
        video = await _start(tmp_path, archive, plate_id=2)

        assert video.read_bytes() == b"plate 1 video"
        # The card still starts fresh, so plate 2's own video gets attached.
        assert archive.timelapse_path is None
        assert archive.timelapse_plate_id is None

    @pytest.mark.asyncio
    async def test_the_same_plate_again_still_replaces_it(self, tmp_path):
        archive = _archive(timelapse_plate_id=2)
        video = await _start(tmp_path, archive, plate_id=2)

        assert not video.exists()
        assert archive.timelapse_path is None

    @pytest.mark.asyncio
    async def test_a_video_of_unknown_plate_is_kept(self, tmp_path):
        """Attached by hand or before the plate was recorded: it cannot be told
        apart from another plate's, so it is not deleted."""
        archive = _archive(timelapse_plate_id=None)
        video = await _start(tmp_path, archive, plate_id=1)

        assert video.exists()
        assert archive.timelapse_path is None


def _service(tmp_path: Path, monkeypatch, current: str | None):
    archive_dir = tmp_path / "archive" / "1" / "20260101_plates"
    archive_dir.mkdir(parents=True)
    fake_settings = MagicMock(base_dir=tmp_path, archive_dir=tmp_path / "archive")
    monkeypatch.setattr("backend.app.services.archive.settings", fake_settings)
    monkeypatch.setattr("backend.app.utils.archive_paths.settings", fake_settings)
    # The .avi case hands over the MP4 conversion; close it unrun.
    monkeypatch.setattr("backend.app.services.archive.spawn_background_task", lambda coro, **_: coro.close())

    db = MagicMock()
    db.commit = AsyncMock()
    # No run logged: a video attached by hand to an archive printed before runs.
    db.scalar = AsyncMock(return_value=None)
    service = ArchiveService(db)
    archive = MagicMock()
    archive.file_path = "archive/1/20260101_plates/Plates.3mf"
    archive.timelapse_path = current
    archive.timelapse_plate_id = None
    service.get_archive = AsyncMock(return_value=archive)
    return service, archive, archive_dir


class TestAttach:
    @pytest.mark.asyncio
    async def test_records_the_plate(self, tmp_path, monkeypatch):
        service, archive, _ = _service(tmp_path, monkeypatch, current=None)

        assert await service.attach_timelapse(1, b"v", "video_a.mp4", plate_id=2)
        assert archive.timelapse_plate_id == 2

    @pytest.mark.asyncio
    async def test_a_manual_attach_records_no_plate(self, tmp_path, monkeypatch):
        service, archive, _ = _service(tmp_path, monkeypatch, current=None)
        archive.timelapse_plate_id = 1

        assert await service.attach_timelapse(1, b"v", "video_a.mp4")
        assert archive.timelapse_plate_id is None

    @pytest.mark.asyncio
    async def test_does_not_write_over_a_kept_video(self, tmp_path, monkeypatch):
        """The external-camera stitch always names its output
        layer_timelapse.mp4, so plate 2's would land on plate 1's."""
        service, archive, archive_dir = _service(tmp_path, monkeypatch, current=None)
        kept = archive_dir / "layer_timelapse.mp4"
        kept.write_bytes(b"plate 1")

        assert await service.attach_timelapse(1, b"plate 2", "layer_timelapse.mp4", plate_id=2)

        assert kept.read_bytes() == b"plate 1"
        assert (archive_dir / "layer_timelapse_2.mp4").read_bytes() == b"plate 2"
        assert archive.timelapse_path == "archive/1/20260101_plates/layer_timelapse_2.mp4"

    @pytest.mark.asyncio
    async def test_an_avi_does_not_convert_over_a_kept_mp4(self, tmp_path, monkeypatch):
        service, archive, archive_dir = _service(tmp_path, monkeypatch, current=None)
        (archive_dir / "video_a.mp4").write_bytes(b"plate 1")

        assert await service.attach_timelapse(1, b"plate 2", "video_a.avi", plate_id=2)

        assert archive.timelapse_path == "archive/1/20260101_plates/video_a_2.avi"

    @pytest.mark.asyncio
    async def test_re_attaching_the_current_video_still_replaces_it(self, tmp_path, monkeypatch):
        service, archive, archive_dir = _service(
            tmp_path, monkeypatch, current="archive/1/20260101_plates/layer_timelapse.mp4"
        )
        (archive_dir / "layer_timelapse.mp4").write_bytes(b"old")

        assert await service.attach_timelapse(1, b"new", "layer_timelapse.mp4")

        assert (archive_dir / "layer_timelapse.mp4").read_bytes() == b"new"
        assert not (archive_dir / "layer_timelapse_2.mp4").exists()


class TestScan:
    @pytest.mark.asyncio
    async def test_the_completed_runs_plate_reaches_the_attach(self):
        from backend.app.main import _attach_first_unclaimed_timelapse
        from backend.tests.unit.test_timelapse_scan_2704 import _printer, _session, _video, logger

        service = MagicMock()
        service.attach_timelapse = AsyncMock(return_value=True)
        with (
            patch("backend.app.services.bambu_ftp.download_file_bytes_async", AsyncMock(return_value=b"x" * 1000)),
            patch("backend.app.services.bambu_ftp.remote_file_settled", AsyncMock(return_value=True)),
            patch("backend.app.services.bambu_ftp.delete_archived_timelapse", AsyncMock(return_value=True)),
            patch("backend.app.main.async_session", return_value=_session()),
            patch("backend.app.main.ArchiveService", return_value=service),
            patch("backend.app.main.ws_manager", MagicMock(send_archive_updated=AsyncMock())),
        ):
            assert await _attach_first_unclaimed_timelapse(
                42, _printer(), [_video("video_b.avi")], set(), set(), 1, logger, plate_id=2
            )

        assert service.attach_timelapse.await_args.kwargs["plate_id"] == 2


class TestEviction:
    """Print start leaves the "<base>.gcode" registration behind. Its eviction
    two hours after dispatch used to drop a running print's plate, so a long
    print's video was attached with no plate on record."""

    def _register_and_start(self):
        import time

        import backend.app.main as m

        register_expected_print(1, "Plates.3mf", archive_id=42, ams_mapping=[0], plate_id=2)
        # What print start pops for filename "Plates.3mf", subtask "Plates".
        for key in [(1, "Plates"), (1, "Plates.3mf"), (1, "Plates.gcode.3mf")]:
            m._expected_prints.pop(key, None)
            m._expected_print_registered_at.pop(key, None)
        assert (1, "Plates.gcode") in m._expected_prints
        for key in list(m._expected_print_registered_at):
            m._expected_print_registered_at[key] = time.monotonic() - m._EXPECTED_PRINT_TTL_SECONDS - 1
        return m

    def test_a_running_print_keeps_its_plate(self):
        m = self._register_and_start()
        m._active_prints[(1, "Plates.3mf")] = 42

        m._evict_stale_expected_prints()

        assert (1, "Plates.gcode") not in m._expected_prints
        assert m._print_plate_ids[(1, 42)] == 2
        assert m._print_ams_mappings[(1, 42)] == [0]

    def test_a_print_that_never_started_still_releases_it(self):
        m = self._register_and_start()

        m._evict_stale_expected_prints()

        assert (1, 42) not in m._print_plate_ids
        assert (1, 42) not in m._print_ams_mappings
