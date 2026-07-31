"""Timelapse scan reliability (#2704).

A Bambu printer in LAN-only mode never reaches Bambu's NTP server, so the clock
behind both the timelapse filename and the FTP mtime drifts freely — the P1S in
the report was six and a half days out. That is why the automatic scan works by
diffing the printer's ``/timelapse`` listing against a snapshot taken when the
print started, and why nothing in that path may fall back to comparing times.

These tests pin the parts that make the diff dependable:

* the candidate is chosen by exclusion, never by ordering (ordering could only
  be done on the printer's clock);
* a download that comes up short never attaches and never triggers a delete —
  deleting the printer's copy is only safe because the transfer was verified;
* the baseline persisted at print start is what the manual Scan button uses,
  instead of the clock-based strategies that cannot work on a drifted printer.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

logger = logging.getLogger(__name__)


def _printer():
    p = MagicMock()
    p.id = 1
    p.name = "TestP1S"
    p.ip_address = "192.168.1.100"
    p.access_code = "12345678"
    p.model = "P1S"
    return p


def _video(name: str, size: int = 1000):
    return {"name": name, "is_directory": False, "path": f"/timelapse/{name}", "size": size}


def _session(archive=None):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock()
    if archive is not None:
        session.get = AsyncMock(return_value=archive)
    return session


class TestCandidateSelection:
    """Which of the printer's videos belongs to this print."""

    @pytest.fixture
    def attach(self):
        from backend.app.main import _attach_first_unclaimed_timelapse

        return _attach_first_unclaimed_timelapse

    @pytest.mark.asyncio
    async def test_nothing_new_since_baseline_is_not_an_attach(self, attach):
        result = await attach(
            42,
            _printer(),
            [_video("video_2026-07-21_09-17-37.avi")],
            {"video_2026-07-21_09-17-37.avi"},
            set(),
            1,
            logger,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_attaches_the_one_new_file_and_deletes_it_from_the_printer(self, attach):
        download = AsyncMock(return_value=b"x" * 1000)
        delete = AsyncMock(return_value=True)
        service = MagicMock()
        service.attach_timelapse = AsyncMock(return_value=True)

        with (
            patch("backend.app.services.bambu_ftp.download_file_bytes_async", download),
            patch("backend.app.services.bambu_ftp.remote_file_settled", AsyncMock(return_value=True)),
            patch("backend.app.services.bambu_ftp.delete_archived_timelapse", delete),
            patch("backend.app.main.async_session", return_value=_session()),
            patch("backend.app.main.ArchiveService", return_value=service),
            patch("backend.app.main.ws_manager", MagicMock(send_archive_updated=AsyncMock())),
        ):
            result = await attach(
                42,
                _printer(),
                [_video("old.avi"), _video("video_2026-07-22_06-18-39.avi")],
                {"old.avi"},
                set(),
                1,
                logger,
            )

        assert result is True
        service.attach_timelapse.assert_awaited_once()
        assert service.attach_timelapse.await_args.args[2] == "video_2026-07-22_06-18-39.avi"
        delete.assert_awaited_once()
        assert delete.await_args.args[2] == "/timelapse/video_2026-07-22_06-18-39.avi"

    @pytest.mark.asyncio
    async def test_skips_a_previous_prints_late_landing_video(self, attach):
        """Two files are new since the baseline because the previous print's
        video only landed after this print started. It is already attached to
        another archive, so it is excluded by name — no timestamps involved."""
        download = AsyncMock(return_value=b"y" * 1000)
        service = MagicMock()
        service.attach_timelapse = AsyncMock(return_value=True)

        with (
            patch("backend.app.services.bambu_ftp.download_file_bytes_async", download),
            patch("backend.app.services.bambu_ftp.remote_file_settled", AsyncMock(return_value=True)),
            patch("backend.app.services.bambu_ftp.delete_archived_timelapse", AsyncMock()),
            patch("backend.app.main.async_session", return_value=_session()),
            patch("backend.app.main.ArchiveService", return_value=service),
            patch("backend.app.main.ws_manager", MagicMock(send_archive_updated=AsyncMock())),
        ):
            result = await attach(
                42,
                _printer(),
                # Listing order puts the previous print's video first, so a
                # naive "take the first new one" would grab the wrong video.
                [_video("previous_print.avi"), _video("this_print.avi")],
                set(),
                {"previous_print"},
                1,
                logger,
            )

        assert result is True
        assert service.attach_timelapse.await_args.args[2] == "this_print.avi"

    @pytest.mark.asyncio
    async def test_claimed_match_survives_the_mp4_conversion(self, attach):
        """Attached AVIs are converted to MP4 afterwards, which keeps the stem
        but changes the extension — so exclusion has to compare stems."""
        result = await attach(
            42,
            _printer(),
            [_video("video_2026-07-22_06-18-39.avi")],
            set(),
            {"video_2026-07-22_06-18-39"},  # stored as .mp4 on the archive
            1,
            logger,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_all_new_files_claimed_keeps_polling(self, attach):
        result = await attach(42, _printer(), [_video("a.avi"), _video("b.avi")], set(), {"a", "b"}, 1, logger)
        assert result is False


class TestDownloadVerificationGatesTheDelete:
    """The printer's copy is the only other copy — it goes only after the
    transfer is verified against the size the listing reported."""

    @pytest.fixture
    def attach(self):
        from backend.app.main import _attach_first_unclaimed_timelapse

        return _attach_first_unclaimed_timelapse

    @pytest.mark.asyncio
    async def test_passes_the_listed_size_to_the_downloader(self, attach):
        download = AsyncMock(return_value=b"z" * 4096)
        service = MagicMock()
        service.attach_timelapse = AsyncMock(return_value=True)

        with (
            patch("backend.app.services.bambu_ftp.download_file_bytes_async", download),
            patch("backend.app.services.bambu_ftp.remote_file_settled", AsyncMock(return_value=True)),
            patch("backend.app.services.bambu_ftp.delete_archived_timelapse", AsyncMock()),
            patch("backend.app.main.async_session", return_value=_session()),
            patch("backend.app.main.ArchiveService", return_value=service),
            patch("backend.app.main.ws_manager", MagicMock(send_archive_updated=AsyncMock())),
        ):
            await attach(42, _printer(), [_video("new.avi", size=4096)], set(), set(), 1, logger)

        assert download.await_args.kwargs["expected_size"] == 4096

    @pytest.mark.asyncio
    async def test_short_download_does_not_attach_or_delete(self, attach):
        """download_file_bytes_async returns None on a size mismatch. The
        printer must keep its copy so the next poll round can retry."""
        delete = AsyncMock()
        service = MagicMock()
        service.attach_timelapse = AsyncMock(return_value=True)

        with (
            patch("backend.app.services.bambu_ftp.download_file_bytes_async", AsyncMock(return_value=None)),
            patch("backend.app.services.bambu_ftp.remote_file_settled", AsyncMock(return_value=True)),
            patch("backend.app.services.bambu_ftp.delete_archived_timelapse", delete),
            patch("backend.app.main.async_session", return_value=_session()),
            patch("backend.app.main.ArchiveService", return_value=service),
        ):
            result = await attach(42, _printer(), [_video("new.avi")], set(), set(), 1, logger)

        assert result is False
        service.attach_timelapse.assert_not_awaited()
        delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_attach_does_not_delete(self, attach):
        delete = AsyncMock()
        service = MagicMock()
        service.attach_timelapse = AsyncMock(return_value=False)

        with (
            patch("backend.app.services.bambu_ftp.download_file_bytes_async", AsyncMock(return_value=b"x" * 1000)),
            patch("backend.app.services.bambu_ftp.remote_file_settled", AsyncMock(return_value=True)),
            patch("backend.app.services.bambu_ftp.delete_archived_timelapse", delete),
            patch("backend.app.main.async_session", return_value=_session()),
            patch("backend.app.main.ArchiveService", return_value=service),
        ):
            result = await attach(42, _printer(), [_video("new.avi")], set(), set(), 1, logger)

        assert result is False
        delete.assert_not_awaited()


class TestFtpDownloadSizeCheck:
    """`download_file` is where a truncated FTPS transfer used to pass for a
    complete one — a partial buffer is non-empty, so every caller downstream
    treated it as a good file."""

    def _client(self, payload: bytes):
        from backend.app.services.bambu_ftp import BambuFTPClient

        client = BambuFTPClient("192.168.1.100", "12345678")
        ftp = MagicMock()
        ftp.retrbinary = MagicMock(side_effect=lambda cmd, cb: cb(payload))
        client._ftp = ftp
        return client

    def test_exact_size_passes(self):
        assert self._client(b"a" * 500).download_file("/timelapse/v.avi", expected_size=500) == b"a" * 500

    def test_short_read_is_a_failure(self):
        assert self._client(b"a" * 499).download_file("/timelapse/v.avi", expected_size=500) is None

    def test_long_read_is_a_failure(self):
        """Not expected in practice, but a mismatch either way means we don't
        know what we have, and we're about to delete the original."""
        assert self._client(b"a" * 501).download_file("/timelapse/v.avi", expected_size=500) is None

    def test_zero_bytes_is_a_failure_even_without_an_expected_size(self):
        assert self._client(b"").download_file("/cache/whatever.3mf") is None

    def test_unverified_download_still_works_for_callers_that_do_not_pass_a_size(self):
        assert self._client(b"abc").download_file("/cache/whatever.3mf") == b"abc"


class TestDeleteIsBestEffort:
    """A printer that refuses the delete must not break the flow — the video
    is already in the archive, and the diff excludes it by name from then on."""

    @pytest.mark.asyncio
    async def test_reports_success_on_delete(self):
        from backend.app.services.bambu_ftp import DeleteResult, delete_archived_timelapse

        with patch(
            "backend.app.services.bambu_ftp.delete_file_async", AsyncMock(return_value=DeleteResult.DELETED)
        ) as d:
            assert await delete_archived_timelapse("1.2.3.4", "code", "/timelapse/v.avi", verified=True) is True
        assert d.await_count == 1

    @pytest.mark.asyncio
    async def test_not_found_is_success_and_is_not_retried(self):
        """550 means the printer already cleaned up; waiting cannot change it."""
        from backend.app.services.bambu_ftp import DeleteResult, delete_archived_timelapse

        with patch(
            "backend.app.services.bambu_ftp.delete_file_async", AsyncMock(return_value=DeleteResult.NOT_FOUND)
        ) as d:
            assert await delete_archived_timelapse("1.2.3.4", "code", "/timelapse/v.avi", verified=True) is True
        assert d.await_count == 1

    @pytest.mark.asyncio
    async def test_failure_retries_then_gives_up_without_raising(self):
        from backend.app.services.bambu_ftp import DeleteResult, delete_archived_timelapse

        with (
            patch("backend.app.services.bambu_ftp.delete_file_async", AsyncMock(return_value=DeleteResult.FAILED)),
            patch("backend.app.services.bambu_ftp.asyncio.sleep", AsyncMock()),
        ):
            assert await delete_archived_timelapse("1.2.3.4", "code", "/timelapse/v.avi", verified=True) is False

    @pytest.mark.asyncio
    async def test_raising_transport_does_not_propagate(self):
        from backend.app.services.bambu_ftp import delete_archived_timelapse

        with (
            patch("backend.app.services.bambu_ftp.delete_file_async", AsyncMock(side_effect=OSError("boom"))),
            patch("backend.app.services.bambu_ftp.asyncio.sleep", AsyncMock()),
        ):
            assert await delete_archived_timelapse("1.2.3.4", "code", "/timelapse/v.avi", verified=True) is False


class TestBaselineIsPersisted:
    """The baseline has to outlive the process: a restart mid-print used to
    lose it, and the manual scan never had access to it at all."""

    @pytest.mark.asyncio
    async def test_written_to_the_archive_row_at_print_start(self):
        from backend.app.main import _capture_timelapse_baseline_at_start

        archive = MagicMock()
        archive.timelapse_baseline = None
        session = _session(archive)

        with (
            patch("backend.app.main.async_session", return_value=session),
            patch(
                "backend.app.main._list_timelapse_videos",
                new=AsyncMock(return_value=([_video("a.avi"), _video("b.avi")], "/timelapse")),
            ),
        ):
            await _capture_timelapse_baseline_at_start(_printer(), 1, logger, archive_id=7)

        assert archive.timelapse_baseline == ["a.avi", "b.avi"]
        session.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_archive_id_keeps_it_in_memory_only(self):
        from backend.app.main import _capture_timelapse_baseline_at_start, _timelapse_baselines

        _timelapse_baselines.pop(1, None)
        session = _session(MagicMock())

        with (
            patch("backend.app.main.async_session", return_value=session),
            patch(
                "backend.app.main._list_timelapse_videos",
                new=AsyncMock(return_value=([_video("a.avi")], "/timelapse")),
            ),
        ):
            await _capture_timelapse_baseline_at_start(_printer(), 1, logger)

        assert _timelapse_baselines[1] == {"a.avi"}
        session.commit.assert_not_awaited()
        _timelapse_baselines.pop(1, None)

    @pytest.mark.asyncio
    async def test_listing_failure_stores_null_not_an_empty_baseline(self):
        """An empty list would make every video on the printer look new; NULL
        correctly means "no baseline" and falls back to a fresh snapshot."""
        from backend.app.main import _capture_timelapse_baseline_at_start

        archive = MagicMock()
        session = _session(archive)

        with (
            patch("backend.app.main.async_session", return_value=session),
            patch("backend.app.main._list_timelapse_videos", new=AsyncMock(side_effect=OSError("ftp down"))),
        ):
            await _capture_timelapse_baseline_at_start(_printer(), 1, logger, archive_id=7)

        assert archive.timelapse_baseline is None


class TestManualScanUsesTheBaseline:
    """The reporter's second symptom: pressing "Scan for Timelapse" found
    nothing. Every strategy the endpoint had was clock-based, and their
    printer's clock was days out, so it could not match on any of them."""

    def _archive(self, baseline):
        from datetime import datetime, timezone

        a = MagicMock()
        a.id = 64
        a.printer_id = 1
        a.filename = "mops.3mf"
        a.timelapse_path = None
        a.timelapse_baseline = baseline
        a.started_at = datetime(2026, 7, 28, 20, 30, tzinfo=timezone.utc)
        a.completed_at = datetime(2026, 7, 28, 21, 19, tzinfo=timezone.utc)
        a.created_at = a.completed_at
        return a

    async def _scan(self, archive, listing, download=None, delete=None):
        from backend.app.api.routes import archives as archives_mod

        service = MagicMock()
        service.get_archive = AsyncMock(return_value=archive)
        service.attach_timelapse = AsyncMock(return_value=True)

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock()
        session.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=_printer()),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            )
        )

        with (
            patch("backend.app.core.database.async_session", return_value=session),
            patch("backend.app.api.routes.archives.ArchiveService", return_value=service),
            patch("backend.app.services.bambu_ftp.list_files_async", AsyncMock(return_value=listing)),
            patch(
                "backend.app.services.bambu_ftp.get_ftp_retry_settings",
                AsyncMock(return_value=(False, 3, 2, 30)),
            ),
            patch(
                "backend.app.services.bambu_ftp.download_file_bytes_async",
                download or AsyncMock(return_value=b"x" * 1000),
            ),
            patch("backend.app.services.bambu_ftp.delete_archived_timelapse", delete or AsyncMock()),
        ):
            return await archives_mod.scan_timelapse(archive.id, None)

    @pytest.mark.asyncio
    async def test_attaches_the_single_unclaimed_new_file(self):
        """The printer's clock is six days out here — exactly the reporter's
        case. Nothing in this path looks at a timestamp."""
        archive = self._archive(["video_2026-07-21_22-49-47.avi"])
        listing = [
            _video("video_2026-07-21_22-49-47.avi"),
            _video("video_2026-07-22_06-18-39.avi"),
        ]

        result = await self._scan(archive, listing)

        assert result["status"] == "attached"
        assert result["filename"] == "video_2026-07-22_06-18-39.avi"

    @pytest.mark.asyncio
    async def test_deletes_from_the_printer_after_attaching(self):
        delete = AsyncMock()
        archive = self._archive(["old.avi"])

        await self._scan(archive, [_video("old.avi"), _video("new.avi")], delete=delete)

        delete.assert_awaited_once()
        assert delete.await_args.args[2] == "/timelapse/new.avi"

    @pytest.mark.asyncio
    async def test_baseline_showing_nothing_new_does_not_guess(self):
        """With a baseline saying no new video exists, the clock strategies
        must not run — otherwise a coincidental timestamp match attaches
        someone else's video and calls it this print's."""
        archive = self._archive(["video_2026-07-28_20-30-00.avi"])
        # This file's embedded time is minutes from started_at, so the old
        # timestamp strategy would have matched it confidently.
        listing = [_video("video_2026-07-28_20-30-00.avi")]

        result = await self._scan(archive, listing)

        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_ambiguous_baseline_offers_only_the_plausible_files(self):
        archive = self._archive(["old.avi"])
        listing = [_video("old.avi"), _video("candidate_a.avi"), _video("candidate_b.avi")]

        result = await self._scan(archive, listing)

        assert result["status"] == "not_found"
        assert {f["name"] for f in result["available_files"]} == {"candidate_a.avi", "candidate_b.avi"}

    @pytest.mark.asyncio
    async def test_archives_without_a_baseline_keep_the_old_strategies(self):
        """Rows predating the persisted baseline still get the best guess the
        endpoint can make, rather than nothing at all."""
        archive = self._archive(None)
        listing = [_video("mops_something.avi")]  # matches by print name

        result = await self._scan(archive, listing)

        assert result["status"] == "attached"
        assert result["filename"] == "mops_something.avi"


class TestPollBounds:
    """The poll is bounded twice on purpose."""

    def test_round_cap_tracks_the_wall_clock_budget(self):
        from backend.app.main import (
            _TIMELAPSE_SCAN_POLL_INTERVAL_SECONDS,
            _TIMELAPSE_SCAN_TIMEOUT_SECONDS,
            _timelapse_scan_max_attempts,
        )

        assert (
            _timelapse_scan_max_attempts()
            == int(_TIMELAPSE_SCAN_TIMEOUT_SECONDS // _TIMELAPSE_SCAN_POLL_INTERVAL_SECONDS) + 1
        )

    def test_zero_interval_does_not_divide_by_zero(self, monkeypatch):
        """The deadline alone can't bound the loop once sleeps are shortened to
        nothing, which is exactly what a test or a future tweak would do."""
        import backend.app.main as main_mod

        monkeypatch.setattr(main_mod, "_TIMELAPSE_SCAN_POLL_INTERVAL_SECONDS", 0)
        assert main_mod._timelapse_scan_max_attempts() > 1

    def test_budget_is_much_longer_than_the_ladder_it_replaced(self):
        """The old [5, 10, 20, 30] ladder gave up after ~65 seconds, while the
        support bundles showed videos still arriving at the cutoff."""
        from backend.app.main import _TIMELAPSE_SCAN_TIMEOUT_SECONDS

        assert _TIMELAPSE_SCAN_TIMEOUT_SECONDS >= 300


class TestFinishPhotoUpgrade:
    """The print-complete notification waits ~60s for the timelapse, because
    holding it for minutes is worse than sending a live grab. On a P1S the
    video routinely lands later than that (p90 167s, worst observed 546s), so
    the archive kept the live grab — taken after the end G-code dropped the
    bed, which is the worse of the two photos. The upgrade runs afterwards."""

    @pytest.mark.asyncio
    async def test_puts_the_timelapse_frame_first_and_keeps_the_live_grab(self):
        """First, because the gallery opens at index 0. Kept, because the
        notification that already went out links to that exact file."""
        from backend.app.main import _upgrade_finish_photo_from_timelapse

        archive = MagicMock()
        archive.photos = ["finish_live_grab.jpg"]
        session = _session(archive)

        with (
            patch(
                "backend.app.main._capture_finish_photo_from_timelapse",
                AsyncMock(return_value=("finish_from_timelapse.jpg", False)),
            ),
            patch("backend.app.main.async_session", return_value=session),
            patch("backend.app.main.ws_manager", MagicMock(send_archive_updated=AsyncMock())) as ws,
        ):
            await _upgrade_finish_photo_from_timelapse(7, MagicMock())

        assert archive.photos == ["finish_from_timelapse.jpg", "finish_live_grab.jpg"]
        session.commit.assert_awaited()
        ws.send_archive_updated.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_waits_far_longer_than_the_notification_can(self):
        from backend.app.main import (
            _FINISH_PHOTO_TIMELAPSE_POLL_TIMEOUT_SECONDS,
            _FINISH_PHOTO_UPGRADE_TIMEOUT_SECONDS,
            _upgrade_finish_photo_from_timelapse,
        )

        capture = AsyncMock(return_value=(None, True))
        with patch("backend.app.main._capture_finish_photo_from_timelapse", capture):
            await _upgrade_finish_photo_from_timelapse(7, MagicMock())

        assert capture.await_args.kwargs["timeout"] == _FINISH_PHOTO_UPGRADE_TIMEOUT_SECONDS
        assert _FINISH_PHOTO_UPGRADE_TIMEOUT_SECONDS > _FINISH_PHOTO_TIMELAPSE_POLL_TIMEOUT_SECONDS
        # Covers the 546s worst case seen in the support bundles.
        assert _FINISH_PHOTO_UPGRADE_TIMEOUT_SECONDS >= 600

    @pytest.mark.asyncio
    async def test_video_never_arrives_leaves_the_archive_alone(self):
        from backend.app.main import _upgrade_finish_photo_from_timelapse

        session = _session(MagicMock())
        with (
            patch("backend.app.main._capture_finish_photo_from_timelapse", AsyncMock(return_value=(None, True))),
            patch("backend.app.main.async_session", return_value=session),
        ):
            await _upgrade_finish_photo_from_timelapse(7, MagicMock())

        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_is_idempotent(self):
        """A second run must not list the same photo twice."""
        from backend.app.main import _upgrade_finish_photo_from_timelapse

        archive = MagicMock()
        archive.photos = ["finish_from_timelapse.jpg", "finish_live_grab.jpg"]
        session = _session(archive)

        with (
            patch(
                "backend.app.main._capture_finish_photo_from_timelapse",
                AsyncMock(return_value=("finish_from_timelapse.jpg", False)),
            ),
            patch("backend.app.main.async_session", return_value=session),
        ):
            await _upgrade_finish_photo_from_timelapse(7, MagicMock())

        assert archive.photos == ["finish_from_timelapse.jpg", "finish_live_grab.jpg"]
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_archive_does_not_raise(self):
        from backend.app.main import _upgrade_finish_photo_from_timelapse

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock()
        session.get = AsyncMock(return_value=None)

        with (
            patch("backend.app.main._capture_finish_photo_from_timelapse", AsyncMock(return_value=("f.jpg", False))),
            patch("backend.app.main.async_session", return_value=session),
        ):
            await _upgrade_finish_photo_from_timelapse(7, MagicMock())

        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refuses_to_delete_an_unverified_download(self):
        """The safety rule lives with the destructive call, not at the call
        sites — an unverified transfer may be a truncated file, and deleting
        the source would destroy the only complete copy."""
        from backend.app.services.bambu_ftp import delete_archived_timelapse

        with patch("backend.app.services.bambu_ftp.delete_file_async", AsyncMock()) as d:
            assert await delete_archived_timelapse("1.2.3.4", "code", "/timelapse/v.avi", verified=False) is False
        d.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verified_is_required_not_defaulted(self):
        """A future call site must not be able to silently skip the check."""
        import inspect

        from backend.app.services.bambu_ftp import delete_archived_timelapse

        param = inspect.signature(delete_archived_timelapse).parameters["verified"]
        assert param.default is inspect.Parameter.empty
        assert param.kind is inspect.Parameter.KEYWORD_ONLY


class TestStaleBaselineCannotSurvive:
    """A reprint reuses the archive row, so a baseline left over from the
    previous run would have the scan diff this print against the printer's
    state before the *last* one — and unlike NULL, a stale list reads as
    authoritative and suppresses the fresh-snapshot fallback."""

    @pytest.mark.asyncio
    async def test_failed_capture_clears_rather_than_leaves_the_old_value(self):
        from backend.app.main import _capture_timelapse_baseline_at_start

        archive = MagicMock()
        archive.timelapse_baseline = ["from_the_previous_run.avi"]
        session = _session(archive)

        with (
            patch("backend.app.main.async_session", return_value=session),
            patch("backend.app.main._list_timelapse_videos", new=AsyncMock(side_effect=OSError("ftp down"))),
        ):
            await _capture_timelapse_baseline_at_start(_printer(), 1, logger, archive_id=7)

        assert archive.timelapse_baseline is None
        session.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_successful_capture_overwrites_the_old_value(self):
        from backend.app.main import _capture_timelapse_baseline_at_start

        archive = MagicMock()
        archive.timelapse_baseline = ["from_the_previous_run.avi"]
        session = _session(archive)

        with (
            patch("backend.app.main.async_session", return_value=session),
            patch(
                "backend.app.main._list_timelapse_videos",
                new=AsyncMock(return_value=([_video("now_on_the_printer.avi")], "/timelapse")),
            ),
        ):
            await _capture_timelapse_baseline_at_start(_printer(), 1, logger, archive_id=7)

        assert archive.timelapse_baseline == ["now_on_the_printer.avi"]


class TestFileMustHaveStoppedGrowing:
    """Matching the listing's size proves we received what it said, not that
    the printer had finished writing. The scan's first look lands seconds after
    the print ends — exactly when the video is being written — so a growing
    file can be listed short, served short, and pass the length check. That was
    survivable while the printer kept its copy; it isn't now that a successful
    attach deletes it."""

    @pytest.mark.asyncio
    async def test_same_size_afterwards_is_settled(self):
        from backend.app.services.bambu_ftp import remote_file_settled

        with patch(
            "backend.app.services.bambu_ftp.list_files_async",
            AsyncMock(return_value=[_video("v.avi", size=4096)]),
        ):
            assert await remote_file_settled("1.2.3.4", "code", "/timelapse/v.avi", 4096) is True

    @pytest.mark.asyncio
    async def test_grown_since_download_is_not_settled(self):
        """We hold a prefix of the video, not the video."""
        from backend.app.services.bambu_ftp import remote_file_settled

        with patch(
            "backend.app.services.bambu_ftp.list_files_async",
            AsyncMock(return_value=[_video("v.avi", size=9000)]),
        ):
            assert await remote_file_settled("1.2.3.4", "code", "/timelapse/v.avi", 4096) is False

    @pytest.mark.asyncio
    async def test_vanished_counts_as_settled(self):
        """Nothing left that can grow, and nothing left to delete either."""
        from backend.app.services.bambu_ftp import remote_file_settled

        with patch(
            "backend.app.services.bambu_ftp.list_files_async",
            AsyncMock(return_value=[_video("something_else.avi")]),
        ):
            assert await remote_file_settled("1.2.3.4", "code", "/timelapse/v.avi", 4096) is True

    @pytest.mark.asyncio
    async def test_listing_failure_is_not_settled(self):
        """ "Could not check" must not read as "safe to delete"."""
        from backend.app.services.bambu_ftp import remote_file_settled

        with patch("backend.app.services.bambu_ftp.list_files_async", AsyncMock(return_value=[])):
            assert await remote_file_settled("1.2.3.4", "code", "/timelapse/v.avi", 4096) is False

    @pytest.mark.asyncio
    async def test_scan_discards_a_still_growing_video_without_deleting(self):
        from backend.app.main import _attach_first_unclaimed_timelapse

        delete = AsyncMock()
        service = MagicMock()
        service.attach_timelapse = AsyncMock(return_value=True)

        with (
            patch("backend.app.services.bambu_ftp.download_file_bytes_async", AsyncMock(return_value=b"x" * 1000)),
            patch("backend.app.services.bambu_ftp.remote_file_settled", AsyncMock(return_value=False)),
            patch("backend.app.services.bambu_ftp.delete_archived_timelapse", delete),
            patch("backend.app.main.async_session", return_value=_session()),
            patch("backend.app.main.ArchiveService", return_value=service),
        ):
            result = await _attach_first_unclaimed_timelapse(
                42, _printer(), [_video("new.avi", size=1000)], set(), set(), 1, logger
            )

        assert result is False
        service.attach_timelapse.assert_not_awaited()
        delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_scan_attaches_once_the_video_has_settled(self):
        from backend.app.main import _attach_first_unclaimed_timelapse

        settled = AsyncMock(return_value=True)
        service = MagicMock()
        service.attach_timelapse = AsyncMock(return_value=True)

        with (
            patch("backend.app.services.bambu_ftp.download_file_bytes_async", AsyncMock(return_value=b"x" * 1000)),
            patch("backend.app.services.bambu_ftp.remote_file_settled", settled),
            patch("backend.app.services.bambu_ftp.delete_archived_timelapse", AsyncMock()),
            patch("backend.app.main.async_session", return_value=_session()),
            patch("backend.app.main.ArchiveService", return_value=service),
            patch("backend.app.main.ws_manager", MagicMock(send_archive_updated=AsyncMock())),
        ):
            result = await _attach_first_unclaimed_timelapse(
                42, _printer(), [_video("new.avi", size=1000)], set(), set(), 1, logger
            )

        assert result is True
        # Checked against what we actually received, not against the listing.
        assert settled.await_args.args[3] == 1000
