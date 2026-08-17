"""A no-3MF archive still owns a directory, and still explains itself (#2843).

Both cases below are the same underlying situation: an H2-series or P2S print
sent from the slicer goes to internal eMMC, Bambuddy cannot fetch the 3MF, and
the archive is created with ``file_path == ""``.
"""

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def data_dirs(monkeypatch, tmp_path):
    """Point base_dir/archive_dir at a scratch tree, as a real install has them."""
    from backend.app.core.config import settings

    base = tmp_path / "data"
    (base / "archive").mkdir(parents=True)
    monkeypatch.setattr(settings, "base_dir", base)
    monkeypatch.setattr(settings, "archive_dir", base / "archive")
    return base


class TestTimelapseDestination:
    """``attach_timelapse`` must stay inside the data directory."""

    @staticmethod
    async def _archive(db_session, file_path: str):
        from backend.app.models.archive import PrintArchive

        archive = PrintArchive(
            printer_id=1,
            print_name="Cube",
            filename="/data/Metadata/plate_1.gcode" if not file_path else "Cube.gcode.3mf",
            file_path=file_path,
            file_size=0,
            status="completed",
        )
        db_session.add(archive)
        await db_session.commit()
        return archive

    @pytest.mark.asyncio
    async def test_no_3mf_archive_writes_under_the_data_dir(self, db_session, data_dirs):
        """Regression: this resolved to ``base_dir.parent`` — /app in Docker, so
        the write failed EACCES; where the parent was writable it dropped a stray
        video beside the install and then failed on relative_to() anyway."""
        from backend.app.services.archive import ArchiveService

        archive = await self._archive(db_session, "")

        ok = await ArchiveService(db_session).attach_timelapse(archive.id, b"video-bytes", "video_2026.mp4")

        assert ok is True
        written = data_dirs / "archive" / str(archive.id) / "video_2026.mp4"
        assert written.read_bytes() == b"video-bytes"
        # Nothing may appear above the data directory.
        assert not list(data_dirs.parent.glob("*.mp4"))

    @pytest.mark.asyncio
    async def test_timelapse_path_is_stored_relative_to_base_dir(self, db_session, data_dirs):
        """The old path could not be made relative to base_dir at all, which is
        what raised ValueError and lost the video after a successful download."""
        from backend.app.services.archive import ArchiveService

        archive = await self._archive(db_session, "")

        await ArchiveService(db_session).attach_timelapse(archive.id, b"video-bytes", "video_2026.mp4")

        assert archive.timelapse_path == f"archive/{archive.id}/video_2026.mp4"

    @pytest.mark.asyncio
    async def test_a_normal_archive_is_unaffected(self, db_session, data_dirs):
        """An archive with a 3MF keeps writing beside it, exactly as before."""
        from backend.app.services.archive import ArchiveService

        archive = await self._archive(db_session, "archive/1/20260817_Cube/Cube.gcode.3mf")
        (data_dirs / "archive" / "1" / "20260817_Cube").mkdir(parents=True)

        ok = await ArchiveService(db_session).attach_timelapse(archive.id, b"video-bytes", "video_2026.mp4")

        assert ok is True
        assert archive.timelapse_path == "archive/1/20260817_Cube/video_2026.mp4"


class TestUnchargeableTrayIsAnnounced:
    """A tray the print used but could not be charged must say so."""

    @pytest.fixture(autouse=True)
    def _clear_sessions(self):
        from backend.app.services.usage_tracker import _active_sessions

        _active_sessions.clear()
        yield
        _active_sessions.clear()

    @pytest.mark.asyncio
    async def test_used_tray_with_no_start_remain_is_logged(self, db_session, caplog):
        """Non-RFID spools report remain = -1, so they never enter
        ``tray_remain_start`` — and the loop skipped them with a bare
        ``continue``. Nothing deducted, no reason given anywhere."""
        from backend.app.services.usage_tracker import PrintSession, _active_sessions, on_print_complete

        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Cube",
            started_at=datetime.now(timezone.utc),
            # AMS0-T0 is missing: it read -1 when the print began.
            tray_remain_start={(1, 0): 50},
            tray_now_at_start=0,
        )
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 0, "tray": [{"id": 0, "remain": 40}]}]},
            progress=100,
            layer_num=50,
            tray_now=0,
            tray_change_log=[],
        )

        with caplog.at_level(logging.INFO, logger="backend.app.services.usage_tracker"):
            await on_print_complete(
                printer_id=1,
                data={"status": "completed"},
                printer_manager=printer_manager,
                db=db_session,
                archive_id=None,
                ams_mapping=[0],
            )

        assert "AMS0-T0: no valid remain% at print start" in caplog.text

    @pytest.mark.asyncio
    async def test_a_tray_the_print_never_touched_stays_quiet(self, db_session, caplog):
        """The loop walks every tray on the printer, so logging unconditionally
        would narrate slots that had nothing to do with this print."""
        from backend.app.services.usage_tracker import PrintSession, _active_sessions, on_print_complete

        _active_sessions[1] = PrintSession(
            printer_id=1,
            print_name="Cube",
            started_at=datetime.now(timezone.utc),
            tray_remain_start={(1, 0): 50},
            tray_now_at_start=0,
        )
        printer_manager = MagicMock()
        printer_manager.get_status.return_value = SimpleNamespace(
            raw_data={"ams": [{"id": 3, "tray": [{"id": 2, "remain": 40}]}]},
            progress=100,
            layer_num=50,
            tray_now=0,
            tray_change_log=[],
        )

        with caplog.at_level(logging.INFO, logger="backend.app.services.usage_tracker"):
            await on_print_complete(
                printer_id=1,
                data={"status": "completed"},
                printer_manager=printer_manager,
                db=db_session,
                archive_id=None,
                ams_mapping=[0],
            )

        assert "AMS3-T2: no valid remain%" not in caplog.text
