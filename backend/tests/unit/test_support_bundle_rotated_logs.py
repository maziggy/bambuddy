"""The support bundle must span the rotated logs, not just the live file (#2555).

``bambuddy.log`` is capped at 5 MB by the RotatingFileHandler, and the bundle
used to ship only that one file — the three rotated backups sat on disk unread.

That cap is invisible on a single printer and brutal on a farm. The 19-printer
fleet in #2555 emits ~100 lines/s of MQTT frame dumps with debug logging on,
which fills 5 MB in under five minutes: we asked the reporter for debug logs to
diagnose a *queue* problem and the bundle came back holding 4m49s of history,
almost none of it about the queue. The byte budget for the bundle was 10 MB all
along; it just never looked past the newest file.
"""

from unittest.mock import patch

from backend.app.api.routes.support import _get_log_content


def _write_rotation(log_dir, live: str, backups: list[str]):
    """Lay out a RotatingFileHandler set: bambuddy.log plus .log.1 .. .log.N.

    ``backups[0]`` becomes ``.log.1``, which the handler defines as the *newest*
    backup — i.e. the one immediately preceding the live file.
    """
    (log_dir / "bambuddy.log").write_text(live, encoding="utf-8")
    for index, text in enumerate(backups, start=1):
        (log_dir / f"bambuddy.log.{index}").write_text(text, encoding="utf-8")


def test_reads_rotated_backups_as_well_as_the_live_file(tmp_path):
    _write_rotation(tmp_path, "live line\n", ["newest backup\n", "middle backup\n", "oldest backup\n"])

    with (
        patch("backend.app.api.routes.support.settings.log_dir", tmp_path),
        patch("backend.app.api.routes.support.settings.log_backup_count", 3),
    ):
        content = _get_log_content().decode()

    for expected in ("oldest backup", "middle backup", "newest backup", "live line"):
        assert expected in content, f"{expected!r} missing — the bundle dropped rotated history"


def test_output_is_chronological_oldest_first(tmp_path):
    """A log you have to read backwards is not a log. .log.3 is the oldest."""
    _write_rotation(tmp_path, "D\n", ["C\n", "B\n", "A\n"])

    with (
        patch("backend.app.api.routes.support.settings.log_dir", tmp_path),
        patch("backend.app.api.routes.support.settings.log_backup_count", 3),
    ):
        content = _get_log_content().decode()

    assert content.split() == ["A", "B", "C", "D"]


def test_byte_budget_is_spent_on_the_newest_history(tmp_path):
    """When the rotation exceeds max_bytes, drop the OLD end, keep the recent.

    Truncating from the wrong end would hand us a bundle full of history that
    predates the problem being reported.
    """
    _write_rotation(tmp_path, "live\n", ["recent\n", "ancient\n"])

    with (
        patch("backend.app.api.routes.support.settings.log_dir", tmp_path),
        patch("backend.app.api.routes.support.settings.log_backup_count", 2),
    ):
        # Enough for "live\n" + "recent\n" but not for "ancient\n" as well.
        content = _get_log_content(max_bytes=12).decode()

    assert "live" in content
    assert "recent" in content
    assert "ancient" not in content


def test_partial_line_at_the_truncation_point_is_discarded(tmp_path):
    """Seeking into the middle of a line must not emit a mangled fragment."""
    (tmp_path / "bambuddy.log").write_text("aaaaaaaaaa\nbbbbbbbbbb\n", encoding="utf-8")

    with (
        patch("backend.app.api.routes.support.settings.log_dir", tmp_path),
        patch("backend.app.api.routes.support.settings.log_backup_count", 3),
    ):
        content = _get_log_content(max_bytes=15).decode()

    assert content == "bbbbbbbbbb\n", "a half-line leaked through the seek"


def test_missing_backups_are_skipped_not_fatal(tmp_path):
    """A fresh install has no .log.N yet; a gap must not abort the bundle."""
    (tmp_path / "bambuddy.log").write_text("live only\n", encoding="utf-8")
    (tmp_path / "bambuddy.log.2").write_text("older\n", encoding="utf-8")  # .log.1 absent

    with (
        patch("backend.app.api.routes.support.settings.log_dir", tmp_path),
        patch("backend.app.api.routes.support.settings.log_backup_count", 3),
    ):
        content = _get_log_content().decode()

    assert content == "older\nlive only\n"


def test_absent_log_file_still_reports_cleanly(tmp_path):
    with (
        patch("backend.app.api.routes.support.settings.log_dir", tmp_path),
        patch("backend.app.api.routes.support.settings.log_backup_count", 3),
    ):
        assert _get_log_content() == b"Log file not found"
