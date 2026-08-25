"""A fallback archive is filled in when the 3MF finally turns up (#2957).

The reporter's P1S started a print while Bambuddy was inside the five-minute
FTPS cool-off armed by an earlier failed TLS handshake. The archive flow checks
that cool-off at the top of its path loop and breaks before opening a single
connection, so it gave up 13 ms after print start and wrote an empty fallback
archive. Four minutes later the cool-off cleared and the cover endpoint
downloaded the very same file -- all 8,956,942 bytes of it -- read a thumbnail
out of it, and published it to the shared 3MF cache under the exact key the
archive flow looks up.

Nothing ever looked. Every ``get_cached_3mf`` caller runs before or during the
print-start handler that had already given up, and ``on_print_complete`` drops
the cache as its first statement, deleting the file. The archive stayed an empty
shell for a print whose source Bambuddy had held, parsed and indexed.

These tests pin the recovery: the row is filled in place (its id is load-bearing
-- the energy reading, the timelapse session and the start notification were all
written against it), it is only ever offered a readable 3MF, and it is left
alone once it has a real file.
"""

from __future__ import annotations

import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.archive import PrintArchive
from backend.app.models.printer import Printer

pytestmark = pytest.mark.asyncio

PRINT_NAME = "Desktop_Goose"
DISPATCH_FILENAME = "Desktop_Goose.gcode.3mf"


def _write_3mf(path: Path, print_name: str = PRINT_NAME) -> Path:
    """A 3MF the archive parser can read metadata out of."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "Metadata/slice_info.config",
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<config><plate>"
            "<metadata key='index' value='1'/>"
            "<metadata key='prediction' value='3600'/>"
            "<metadata key='weight' value='42.5'/>"
            "<filament id='1' type='PLA' color='#00AE42' used_g='42.5' used_m='14.2'/>"
            "</plate></config>",
        )
        zf.writestr(
            "Metadata/model_settings.config",
            f"<config><plate><metadata key='name' value='{print_name}'/></plate></config>",
        )
        zf.writestr("3D/3dmodel.model", "<model/>")
    return path


async def _seed(engine, tmp_path: Path) -> tuple[async_sessionmaker, int, int]:
    """A printer plus the empty fallback archive the cool-off produced."""
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        printer = Printer(
            name="P1S",
            serial_number="01P00A3B1200579",
            ip_address="172.25.12.149",
            access_code="12345678",
            model="P1S",
        )
        db.add(printer)
        await db.commit()
        await db.refresh(printer)

        archive = PrintArchive(
            printer_id=printer.id,
            filename=DISPATCH_FILENAME,
            file_path="",  # the shell
            file_size=0,
            print_name=PRINT_NAME,
            status="printing",
            subtask_id="4242",
            extra_data={
                "no_3mf_available": True,
                "no_3mf_reason": "ftps_cooloff",
                "original_subtask": PRINT_NAME,
                "_print_data": {"filename": DISPATCH_FILENAME},
            },
        )
        db.add(archive)
        await db.commit()
        await db.refresh(archive)
        return maker, printer.id, archive.id


class TestRecoveryFillsTheExistingRow:
    async def test_the_cover_endpoints_download_recovers_the_archive(self, test_engine, tmp_path):
        """The reporter's case, end to end from the download onwards."""
        from backend.app import main as main_module

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        # The cover endpoint's own temp name, as it appears in the report:
        # /app/data/archive/temp/cover_1_Desktop_Goose.gcode.3mf
        source = _write_3mf(tmp_path / "temp" / f"cover_{printer_id}_{DISPATCH_FILENAME}")

        with (
            patch.object(main_module, "async_session", maker),
            patch.dict(main_module._active_prints, {(printer_id, DISPATCH_FILENAME): archive_id}, clear=True),
        ):
            recovered = await main_module.try_recover_fallback_archive(printer_id, DISPATCH_FILENAME, source)

        assert recovered is True

        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            # Same row. A second archive would orphan the energy reading, the
            # timelapse session and the notification already sent against it.
            assert archive.id == archive_id
            assert archive.file_path
            assert archive.file_size == source.stat().st_size
            assert archive.subtask_id == "4242"
            assert archive.status == "printing"
            # No longer a fallback, so the Archives banner stops counting it.
            assert not archive.extra_data.get("no_3mf_available")
            assert archive.extra_data.get("recovered_no_3mf") is True
            # The start payload is diagnostic history and survives.
            assert archive.extra_data["_print_data"]["filename"] == DISPATCH_FILENAME

        # And exactly one archive, not the original shell plus a new one.
        async with maker() as db:
            rows = (await db.execute(select(PrintArchive).where(PrintArchive.printer_id == printer_id))).scalars().all()
            assert [row.id for row in rows] == [archive_id]

    async def test_metadata_from_the_3mf_lands_on_the_row(self, test_engine, tmp_path):
        from backend.app import main as main_module

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        source = _write_3mf(tmp_path / "temp" / DISPATCH_FILENAME)

        with (
            patch.object(main_module, "async_session", maker),
            patch.dict(main_module._active_prints, {(printer_id, DISPATCH_FILENAME): archive_id}, clear=True),
        ):
            assert await main_module.try_recover_fallback_archive(printer_id, DISPATCH_FILENAME, source) is True

        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            # The empty shell had none of these.
            assert archive.filament_used_grams == pytest.approx(42.5)
            assert archive.filament_type == "PLA"
            assert archive.print_time_seconds == 3600

    async def test_a_name_variant_still_finds_the_archive(self, test_engine, tmp_path):
        """The cover endpoint arrives with whichever spelling its own path built.

        `_active_prints` is keyed on the raw names seen at print start, so an
        exact-string lookup would miss "Desktop_Goose.gcode.3mf" against an
        archive registered under "Desktop_Goose".
        """
        from backend.app import main as main_module

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        source = _write_3mf(tmp_path / "temp" / DISPATCH_FILENAME)

        with (
            patch.object(main_module, "async_session", maker),
            patch.dict(main_module._active_prints, {(printer_id, PRINT_NAME): archive_id}, clear=True),
        ):
            assert await main_module.try_recover_fallback_archive(printer_id, DISPATCH_FILENAME, source) is True


class TestRecoveryRefusesTheWrongInput:
    async def test_a_truncated_download_is_refused(self, test_engine, tmp_path):
        """Half a file would replace an honest empty archive with wrong metadata."""
        from backend.app import main as main_module

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        source = tmp_path / "temp" / DISPATCH_FILENAME
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"PK\x03\x04 truncated, not a readable zip")

        with (
            patch.object(main_module, "async_session", maker),
            patch.dict(main_module._active_prints, {(printer_id, DISPATCH_FILENAME): archive_id}, clear=True),
        ):
            assert await main_module.try_recover_fallback_archive(printer_id, DISPATCH_FILENAME, source) is False

        async with maker() as db:
            assert (await db.get(PrintArchive, archive_id)).file_path == ""

    async def test_an_empty_file_is_refused(self, test_engine, tmp_path):
        from backend.app import main as main_module

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        source = tmp_path / "temp" / DISPATCH_FILENAME
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"")

        with (
            patch.object(main_module, "async_session", maker),
            patch.dict(main_module._active_prints, {(printer_id, DISPATCH_FILENAME): archive_id}, clear=True),
        ):
            assert await main_module.try_recover_fallback_archive(printer_id, DISPATCH_FILENAME, source) is False

    async def test_an_archive_that_already_has_a_3mf_is_left_alone(self, test_engine, tmp_path):
        """The normal case: every cover request during a healthy print hits this."""
        from backend.app import main as main_module

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            archive.file_path = "archives/1/real/Desktop_Goose.gcode.3mf"
            archive.file_size = 8956942
            await db.commit()

        source = _write_3mf(tmp_path / "temp" / DISPATCH_FILENAME)
        with (
            patch.object(main_module, "async_session", maker),
            patch.dict(main_module._active_prints, {(printer_id, DISPATCH_FILENAME): archive_id}, clear=True),
        ):
            assert await main_module.try_recover_fallback_archive(printer_id, DISPATCH_FILENAME, source) is False

        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            assert archive.file_path == "archives/1/real/Desktop_Goose.gcode.3mf"
            assert archive.file_size == 8956942

    async def test_no_running_print_for_this_printer_is_a_no_op(self, test_engine, tmp_path):
        from backend.app import main as main_module

        maker, printer_id, _archive_id = await _seed(test_engine, tmp_path)
        source = _write_3mf(tmp_path / "temp" / DISPATCH_FILENAME)

        with (
            patch.object(main_module, "async_session", maker),
            patch.dict(main_module._active_prints, {}, clear=True),
        ):
            assert await main_module.try_recover_fallback_archive(printer_id, DISPATCH_FILENAME, source) is False

    async def test_a_deleted_archive_is_not_resurrected(self, test_engine, tmp_path):
        from datetime import datetime, timezone

        from backend.app import main as main_module

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            archive.deleted_at = datetime.now(timezone.utc)
            await db.commit()

        source = _write_3mf(tmp_path / "temp" / DISPATCH_FILENAME)
        with (
            patch.object(main_module, "async_session", maker),
            patch.dict(main_module._active_prints, {(printer_id, DISPATCH_FILENAME): archive_id}, clear=True),
        ):
            assert await main_module.try_recover_fallback_archive(printer_id, DISPATCH_FILENAME, source) is False


class TestTheGiveUpReasonIsRecorded:
    @pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
    async def test_the_cooloff_slug_is_distinct_from_the_storage_verdicts(self):
        """The retry decision keys off it: a cool-off clears in minutes with the
        file still on the printer, while an eMMC job never appears at any FTPS
        path and retrying it is the sweep #2780 removed."""
        from backend.app.services.print_storage import (
            REASON_FTPS_COOLOFF,
            REASON_INTERNAL_STORAGE,
            REASON_NO_EXTERNAL_STORAGE,
        )

        assert REASON_FTPS_COOLOFF not in (REASON_INTERNAL_STORAGE, REASON_NO_EXTERNAL_STORAGE)

    async def test_the_banner_endpoint_does_not_leak_the_new_slug(self):
        """The two storage slugs are a UI contract; a cool-off is not one of them
        and must degrade to the generic banner rather than a missing string."""
        from backend.app.api.routes.archives import REASON_INTERNAL_STORAGE, REASON_NO_EXTERNAL_STORAGE
        from backend.app.services.print_storage import REASON_FTPS_COOLOFF

        assert REASON_FTPS_COOLOFF not in (REASON_INTERNAL_STORAGE, REASON_NO_EXTERNAL_STORAGE)


class TestTheCooloffRetry:
    """The other half: nothing may ever download the file on its own."""

    async def test_the_retry_recovers_from_the_shared_cache(self, test_engine, tmp_path, monkeypatch):
        """The cover endpoint's copy is the same bytes, so the retry spends no
        FTP connection when the cache already holds it."""
        import asyncio

        from backend.app import main as main_module
        from backend.app.services import bambu_ftp

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        source = _write_3mf(tmp_path / "temp" / DISPATCH_FILENAME)
        monkeypatch.setattr(main_module, "_FALLBACK_3MF_RETRY_DELAYS_SECONDS", (0.01,))
        bambu_ftp.cache_3mf_download(printer_id, DISPATCH_FILENAME, source)

        try:
            with patch.object(main_module, "async_session", maker):
                main_module._schedule_fallback_3mf_retry(
                    printer_id=printer_id, archive_id=archive_id, filenames=[DISPATCH_FILENAME]
                )
                task = main_module._fallback_3mf_retry_tasks[printer_id]
                await asyncio.wait_for(task, timeout=5)
        finally:
            bambu_ftp.clear_3mf_cache(printer_id, delete_files=False)

        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            assert archive.file_path
            assert not archive.extra_data.get("no_3mf_available")

    async def test_the_retry_stops_once_the_archive_has_a_3mf(self, test_engine, tmp_path, monkeypatch):
        """Something else recovered it first — usually the cover endpoint."""
        import asyncio

        from backend.app import main as main_module

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            archive.file_path = "archives/1/real/Desktop_Goose.gcode.3mf"
            await db.commit()

        monkeypatch.setattr(main_module, "_FALLBACK_3MF_RETRY_DELAYS_SECONDS", (0.01, 0.01))
        downloads = []

        async def _never(*args, **kwargs):
            downloads.append(args)
            return False

        with (
            patch.object(main_module, "async_session", maker),
            patch.object(main_module, "download_file_try_paths_async", _never),
        ):
            main_module._schedule_fallback_3mf_retry(
                printer_id=printer_id, archive_id=archive_id, filenames=[DISPATCH_FILENAME]
            )
            await asyncio.wait_for(main_module._fallback_3mf_retry_tasks[printer_id], timeout=5)

        assert downloads == []

    async def test_a_second_schedule_replaces_the_first(self, test_engine, tmp_path, monkeypatch):
        """One printer prints one job at a time; two live retry tasks would race
        to write the same row."""
        import asyncio

        from backend.app import main as main_module

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        monkeypatch.setattr(main_module, "_FALLBACK_3MF_RETRY_DELAYS_SECONDS", (30.0,))

        with patch.object(main_module, "async_session", maker):
            main_module._schedule_fallback_3mf_retry(
                printer_id=printer_id, archive_id=archive_id, filenames=[DISPATCH_FILENAME]
            )
            first = main_module._fallback_3mf_retry_tasks[printer_id]
            main_module._schedule_fallback_3mf_retry(
                printer_id=printer_id, archive_id=archive_id, filenames=[DISPATCH_FILENAME]
            )
            second = main_module._fallback_3mf_retry_tasks[printer_id]

            assert first is not second
            await asyncio.sleep(0)
            assert first.cancelled() or first.done()
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
        main_module._fallback_3mf_retry_tasks.pop(printer_id, None)

    async def test_the_retry_downloads_from_the_printer_when_the_cache_is_empty(
        self, test_engine, tmp_path, monkeypatch
    ):
        """Nothing else fetched the file, so the retry has to go and get it —
        the branch the reporter would have hit had they never opened the card."""
        import asyncio

        from backend.app import main as main_module

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        monkeypatch.setattr(main_module, "_FALLBACK_3MF_RETRY_DELAYS_SECONDS", (0.01,))
        # Left on the real archive dir: ArchiveService stores the destination
        # relative to settings.base_dir, so a temp path outside it cannot be
        # archived at all.
        asked: list[list[str]] = []

        async def _serve(ip, code, paths, dest, **kwargs):
            asked.append(list(paths))
            _write_3mf(Path(dest))
            return paths[0]

        with (
            patch.object(main_module, "async_session", maker),
            patch.object(main_module, "ftps_handshake_blocked", return_value=False),
            patch.object(main_module, "get_ftp_retry_settings", return_value=(True, 3, 2.0, 30.0)),
            patch.object(main_module, "download_file_try_paths_async", _serve),
        ):
            main_module._schedule_fallback_3mf_retry(
                printer_id=printer_id, archive_id=archive_id, filenames=[DISPATCH_FILENAME]
            )
            await asyncio.wait_for(main_module._fallback_3mf_retry_tasks[printer_id], timeout=5)

        assert asked, "the retry never asked the printer for the file"
        async with maker() as db:
            archive = await db.get(PrintArchive, archive_id)
            assert archive.file_path
            assert archive.id == archive_id

    async def test_a_printer_still_in_cool_off_is_not_contacted(self, test_engine, tmp_path, monkeypatch):
        """Retrying into a live cool-off is the failure that created the fallback."""
        import asyncio

        from backend.app import main as main_module

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        monkeypatch.setattr(main_module, "_FALLBACK_3MF_RETRY_DELAYS_SECONDS", (0.01,))
        downloads = []

        async def _never(*args, **kwargs):
            downloads.append(args)
            return False

        with (
            patch.object(main_module, "async_session", maker),
            patch.object(main_module, "ftps_handshake_blocked", return_value=True),
            patch.object(main_module, "download_file_try_paths_async", _never),
        ):
            main_module._schedule_fallback_3mf_retry(
                printer_id=printer_id, archive_id=archive_id, filenames=[DISPATCH_FILENAME]
            )
            await asyncio.wait_for(main_module._fallback_3mf_retry_tasks[printer_id], timeout=5)

        assert downloads == []


class TestConcurrentRecoveryIsSerialised:
    async def test_two_racing_callers_produce_one_archive_directory(self, test_engine, tmp_path):
        """The cover endpoint coalesces by view, so two views race each other —
        and the cool-off retry can land on top of either. Unserialised, each
        caller reads file_path == "" and runs its own copy, leaving the row
        pointing at one timestamped directory with the others orphaned."""
        import asyncio

        from backend.app import main as main_module
        from backend.app.core.config import settings as app_config

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        # A name unique to this run. `archive_print` builds its directory as
        # "<second-resolution timestamp>_<file stem>" with exist_ok=True, so a
        # shared stem collides with the directory another test in this file made
        # a moment ago, and the count below would measure that instead.
        unique = f"Racing_{uuid.uuid4().hex[:12]}.gcode.3mf"
        source = _write_3mf(tmp_path / "temp" / unique)
        printer_root = app_config.archive_dir / str(printer_id)
        before = set(printer_root.iterdir()) if printer_root.exists() else set()

        with (
            patch.object(main_module, "async_session", maker),
            patch.dict(main_module._active_prints, {(printer_id, unique): archive_id}, clear=True),
        ):
            results = await asyncio.gather(
                *(main_module.try_recover_fallback_archive(printer_id, unique, source) for _ in range(4))
            )

        # Exactly one caller did the work; the rest saw a recovered archive.
        assert results.count(True) == 1
        created = (set(printer_root.iterdir()) if printer_root.exists() else set()) - before
        assert len(created) == 1, f"expected one archive directory, got {sorted(p.name for p in created)}"

        async with maker() as db:
            rows = (await db.execute(select(PrintArchive).where(PrintArchive.printer_id == printer_id))).scalars().all()
            assert [row.id for row in rows] == [archive_id]
            assert (app_config.base_dir / rows[0].file_path).is_file()


class TestTheRetryWritesInsideTheDataVolume:
    async def test_a_path_shaped_name_cannot_escape_the_temp_directory(self, test_engine, tmp_path, monkeypatch):
        """MQTT hands `filename` over as a path on some firmware — the print-start
        log shows "/data/Metadata/plate_1.gcode". Joining that onto a directory
        with `/` yields the absolute path itself, so the temp write has to reduce
        every candidate to a bare name of its own accord."""
        import asyncio

        from backend.app import main as main_module
        from backend.app.core.config import settings as app_config

        maker, printer_id, archive_id = await _seed(test_engine, tmp_path)
        monkeypatch.setattr(main_module, "_FALLBACK_3MF_RETRY_DELAYS_SECONDS", (0.01,))
        temp_root = (app_config.archive_dir / "temp").resolve()
        written: list[Path] = []

        async def _record(ip, code, paths, dest, **kwargs):
            written.append(Path(dest))
            return None  # a miss, so the loop walks every candidate

        with (
            patch.object(main_module, "async_session", maker),
            patch.object(main_module, "ftps_handshake_blocked", return_value=False),
            patch.object(main_module, "get_ftp_retry_settings", return_value=(True, 3, 2.0, 30.0)),
            patch.object(main_module, "download_file_try_paths_async", _record),
        ):
            main_module._schedule_fallback_3mf_retry(
                printer_id=printer_id,
                archive_id=archive_id,
                filenames=[
                    "/data/Metadata/plate_1.gcode",
                    "../../../../etc/passwd",
                    "/etc/cron.d/evil.3mf",
                    "..",
                ],
            )
            await asyncio.wait_for(main_module._fallback_3mf_retry_tasks[printer_id], timeout=5)

        assert written, "the retry never attempted a download"
        for dest in written:
            assert dest.resolve().parent == temp_root, f"{dest} escaped {temp_root}"


class TestPhotosSurviveRecovery:
    """Recovery moves the archive's directory, because `archive_dir` derives it
    from `file_path` and that goes from empty to a real path. A photo uploaded
    to the empty card while the print ran is still where it was put."""

    async def test_a_photo_written_before_recovery_is_still_found_after(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from backend.app.core.config import settings as app_config
        from backend.app.utils.archive_paths import find_archive_photo

        monkeypatch.setattr(app_config, "archive_dir", tmp_path / "archive")
        monkeypatch.setattr(app_config, "base_dir", tmp_path)

        archive = SimpleNamespace(id=83, file_path="")

        # Uploaded while the archive was still an empty fallback.
        before_dir = tmp_path / "archive" / "83" / "photos"
        before_dir.mkdir(parents=True)
        (before_dir / "snap.jpg").write_bytes(b"jpeg")
        assert find_archive_photo(archive, "snap.jpg") == before_dir / "snap.jpg"

        # The 3MF turns up and the row gains a file_path in a new directory.
        archive.file_path = "archive/1/20260825_000000_Desktop_Goose/Desktop_Goose.gcode.3mf"
        (tmp_path / "archive/1/20260825_000000_Desktop_Goose").mkdir(parents=True)

        assert find_archive_photo(archive, "snap.jpg") == before_dir / "snap.jpg"

    async def test_the_current_directory_still_wins(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from backend.app.core.config import settings as app_config
        from backend.app.utils.archive_paths import find_archive_photo

        monkeypatch.setattr(app_config, "archive_dir", tmp_path / "archive")
        monkeypatch.setattr(app_config, "base_dir", tmp_path)

        archive = SimpleNamespace(id=83, file_path="archive/1/run/Desktop_Goose.gcode.3mf")
        current = tmp_path / "archive/1/run/photos"
        current.mkdir(parents=True)
        (current / "snap.jpg").write_bytes(b"new")
        stale = tmp_path / "archive" / "83" / "photos"
        stale.mkdir(parents=True)
        (stale / "snap.jpg").write_bytes(b"old")

        assert find_archive_photo(archive, "snap.jpg") == current / "snap.jpg"
