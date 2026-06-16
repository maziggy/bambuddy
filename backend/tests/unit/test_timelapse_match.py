"""Unit tests for the timelapse-by-timestamp matcher used by /archives/scan.

Regression coverage for #1278: when the printer cannot reach NTP (LAN-Only mode),
its clock is offset from the server's, and an older video's filename can land just
before a later print's completion. The previous matcher:

1. Treated the filename as either start- or end-time evidence — semantically wrong
   for a filename that's always print-start.
2. Probed a dense set of timezone offsets, so an unrelated video could
   coincidentally land within minutes of a later print at *some* offset.

The new matcher matches only against start time and refuses to auto-pick when the
top two candidates (from different videos) are within an ambiguity margin —
forcing the manual-selection fallback the reporter explicitly asked for.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend.app.api.routes.archives import _match_timelapse_by_timestamp


def _video(name: str, mtime: datetime | None = None) -> dict:
    return {
        "name": name,
        "path": f"/timelapse/{name}",
        "is_directory": False,
        "size": 1024,
        "mtime": mtime,
    }


class TestMatchTimelapseByTimestamp:
    """Cover the bug from issue #1278 plus baseline cases."""

    def test_issue_1278_archive2_refuses_to_auto_pick_ambiguous(self):
        """Archive 2 (start 16:39:09) used to wrongly attach the older 09-41-29 video.

        The wrong video matches at offset -7 (diff 2m20s), the correct video at
        offset +8 (diff 3m33s). The two are within ~1 minute of each other —
        too close to call. Matcher must return None so the route surfaces the
        manual-selection list to the user.
        """
        videos = [
            _video("video_2026-05-08_09-41-29.mp4"),  # belongs to Archive 1
            _video("video_2026-05-09_00-42-42.mp4"),  # belongs to Archive 2 — correct
        ]
        archive_start = datetime(2026, 5, 8, 16, 39, 9)

        match, diff = _match_timelapse_by_timestamp(videos, archive_start)

        assert match is None
        assert diff is None

    def test_issue_1278_archive1_still_matches_unambiguously(self):
        """Archive 1 (start 01:27:14) — only one candidate within tolerance,
        so the matcher should still pick it cleanly."""
        videos = [
            _video("video_2026-05-08_09-41-29.mp4"),  # correct
            _video("video_2026-05-09_00-42-42.mp4"),  # 15h+ away at any common offset
        ]
        archive_start = datetime(2026, 5, 8, 1, 27, 14)

        match, diff = _match_timelapse_by_timestamp(videos, archive_start)

        assert match is not None
        assert match["name"] == "video_2026-05-08_09-41-29.mp4"
        assert diff is not None
        assert diff < timedelta(minutes=20)

    def test_archive2_resolves_when_stale_video_removed(self):
        """If the user has cleaned up the stale Archive-1 video, Archive 2's correct
        video is the only candidate and auto-match should succeed."""
        videos = [_video("video_2026-05-09_00-42-42.mp4")]
        archive_start = datetime(2026, 5, 8, 16, 39, 9)

        match, diff = _match_timelapse_by_timestamp(videos, archive_start)

        assert match is not None
        assert match["name"] == "video_2026-05-09_00-42-42.mp4"
        assert diff is not None
        assert diff < timedelta(minutes=5)

    def test_no_match_when_outside_tolerance(self):
        """All candidates outside the 4h tolerance → no match."""
        videos = [_video("video_2026-05-08_09-41-29.mp4")]
        # A week later, far beyond any offset's reach
        archive_start = datetime(2026, 5, 15, 12, 0, 0)

        match, diff = _match_timelapse_by_timestamp(videos, archive_start)

        assert match is None
        assert diff is None

    def test_returns_none_when_started_at_missing(self):
        """No archive start time = no signal; should return None."""
        videos = [_video("video_2026-05-08_09-41-29.mp4")]

        match, diff = _match_timelapse_by_timestamp(videos, None)

        assert match is None
        assert diff is None

    def test_zero_offset_when_clocks_agree(self):
        """When printer and server clocks agree, offset=0 should pick the video cleanly."""
        videos = [_video("video_2026-05-08_16-40-00.mp4")]
        archive_start = datetime(2026, 5, 8, 16, 39, 0)

        match, diff = _match_timelapse_by_timestamp(videos, archive_start)

        assert match is not None
        assert match["name"] == "video_2026-05-08_16-40-00.mp4"
        assert diff == timedelta(minutes=1)

    def test_skips_videos_without_timestamp_in_name(self):
        """Non-standard names (e.g., manually uploaded) should be skipped, not crash."""
        videos = [
            _video("my_custom_video.mp4"),
            _video("video_2026-05-08_16-40-00.mp4"),
        ]
        archive_start = datetime(2026, 5, 8, 16, 39, 0)

        match, _diff = _match_timelapse_by_timestamp(videos, archive_start)

        assert match is not None
        assert match["name"] == "video_2026-05-08_16-40-00.mp4"

    def test_empty_video_list_returns_none(self):
        match, diff = _match_timelapse_by_timestamp([], datetime(2026, 5, 8, 0, 0, 0))
        assert match is None
        assert diff is None

    @pytest.mark.parametrize("offset_hours", [0, 1, -1, 7, -7, 8, -8])
    def test_supports_common_timezone_offsets_with_single_candidate(self, offset_hours: int):
        """Each offset in the search list must be able to produce a match when
        only one video exists (so ambiguity check is vacuous)."""
        archive_start = datetime(2026, 5, 8, 12, 0, 0)
        # Printer's filename reflects archive_start in printer-local time
        printer_time = archive_start + timedelta(hours=offset_hours)
        videos = [_video(printer_time.strftime("video_%Y-%m-%d_%H-%M-%S.mp4"))]

        match, diff = _match_timelapse_by_timestamp(videos, archive_start)

        assert match is not None
        assert diff == timedelta(0)

    def test_returns_match_when_runner_up_is_same_video_different_offset(self):
        """A single video matching at two offsets is not ambiguous — pick it."""
        videos = [_video("video_2026-05-08_09-41-29.mp4")]
        # +7h adjusted = 02:41:29; +8h adjusted = 01:41:29. Both within 4h of 01:27:14.
        archive_start = datetime(2026, 5, 8, 1, 27, 14)

        match, diff = _match_timelapse_by_timestamp(videos, archive_start)

        assert match is not None
        assert match["name"] == "video_2026-05-08_09-41-29.mp4"
        # Best is offset +8 → diff 14m15s
        assert diff is not None
        assert diff < timedelta(minutes=20)

    def test_unambiguous_when_runner_up_is_well_separated(self):
        """If the next-best different video is comfortably outside the ambiguity
        margin, auto-pick the winner."""
        videos = [
            _video("video_2026-05-08_09-41-29.mp4"),  # +8h → 01:41:29, diff 14m15s
            _video("video_2026-05-08_12-00-00.mp4"),  # +8h → 04:00:00, diff 2h32m
        ]
        archive_start = datetime(2026, 5, 8, 1, 27, 14)

        match, diff = _match_timelapse_by_timestamp(videos, archive_start)

        assert match is not None
        assert match["name"] == "video_2026-05-08_09-41-29.mp4"
        assert diff is not None
