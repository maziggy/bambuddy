"""Unit tests for scheduled local backup service (#884)."""

import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.local_backup import LocalBackupService


class TestCalculateNextRun:
    """Tests for _calculate_next_run scheduling logic.

    The HH:MM picker is interpreted in the container's local timezone (TZ env
    var, UTC fallback). Each test pins TZ so the assertions don't depend on
    the test runner's environment.
    """

    def test_hourly_returns_next_full_hour(self, monkeypatch):
        monkeypatch.setenv("TZ", "UTC")
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 14, 30, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("hourly", "03:00")
        assert result.hour == 15
        assert result.minute == 0

    def test_daily_before_target_time_schedules_today_utc(self, monkeypatch):
        monkeypatch.setenv("TZ", "UTC")
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 2, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("daily", "03:00")
        assert result == datetime(2026, 4, 12, 3, 0, 0, tzinfo=timezone.utc)

    def test_daily_after_target_time_schedules_tomorrow_utc(self, monkeypatch):
        monkeypatch.setenv("TZ", "UTC")
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 4, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("daily", "03:00")
        assert result == datetime(2026, 4, 13, 3, 0, 0, tzinfo=timezone.utc)

    def test_weekly_adds_full_week_utc(self, monkeypatch):
        monkeypatch.setenv("TZ", "UTC")
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 2, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("weekly", "03:00")
        assert result == datetime(2026, 4, 19, 3, 0, 0, tzinfo=timezone.utc)

    def test_weekly_after_target_time_adds_full_week_from_tomorrow_utc(self, monkeypatch):
        monkeypatch.setenv("TZ", "UTC")
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 4, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("weekly", "03:00")
        assert result == datetime(2026, 4, 20, 3, 0, 0, tzinfo=timezone.utc)

    def test_invalid_time_defaults_to_0300(self, monkeypatch):
        monkeypatch.setenv("TZ", "UTC")
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 2, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("daily", "invalid")
        assert result.hour == 3
        assert result.minute == 0

    def test_unknown_schedule_type_defaults_to_daily(self, monkeypatch):
        monkeypatch.setenv("TZ", "UTC")
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 2, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("every_5_min", "03:00")
        # Should fall through to daily behavior (time-based)
        assert result.hour == 3

    def test_daily_berlin_local_time_converts_to_utc(self, monkeypatch):
        """User in Europe/Berlin entering 21:00 should run at 19:00 UTC (CEST/UTC+2)."""
        monkeypatch.setenv("TZ", "Europe/Berlin")
        service = LocalBackupService()
        # Mid-June: Europe/Berlin is CEST (+02:00)
        now = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)  # 12:00 Berlin
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("daily", "21:00")
        # 21:00 Berlin (CEST, +02:00) on 2026-06-15 == 19:00 UTC same day
        assert result == datetime(2026, 6, 15, 19, 0, 0, tzinfo=timezone.utc)

    def test_daily_istanbul_local_time_converts_to_utc(self, monkeypatch):
        """The #1602 reporter: UTC+3 user entering 21:00 should run at 18:00 UTC."""
        monkeypatch.setenv("TZ", "Europe/Istanbul")
        service = LocalBackupService()
        now = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)  # 13:00 Istanbul
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("daily", "21:00")
        assert result == datetime(2026, 6, 15, 18, 0, 0, tzinfo=timezone.utc)

    def test_no_tz_env_falls_back_to_utc(self, monkeypatch):
        monkeypatch.delenv("TZ", raising=False)
        service = LocalBackupService()
        now = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("daily", "21:00")
        # No TZ → behaves as UTC: 21:00 today is in the future of 10:00, so today
        assert result == datetime(2026, 6, 15, 21, 0, 0, tzinfo=timezone.utc)

    def test_unrecognised_tz_falls_back_to_utc(self, monkeypatch):
        monkeypatch.setenv("TZ", "Not/A_Real_Zone")
        service = LocalBackupService()
        now = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("daily", "21:00")
        assert result == datetime(2026, 6, 15, 21, 0, 0, tzinfo=timezone.utc)

    def test_zoneinfo_completely_unavailable_falls_back_to_stdlib_utc(self, monkeypatch):
        """Windows installer ships an embedded Python without the IANA tz DB
        (no system tzdata, no ``tzdata`` PyPI package). Even ``ZoneInfo("UTC")``
        raises ``ZoneInfoNotFoundError`` then, and /api/local-backup/status
        500s. The fallback must catch that and return ``datetime.timezone.utc``
        so scheduling still works without the DB.
        """
        from zoneinfo import ZoneInfoNotFoundError

        # The resolver moved to utils/local_time in #2539, when the smart-plug
        # energy history needed the same local day boundary. local_backup still
        # calls it, so this still guards the behaviour local_backup depends on.
        from backend.app.utils import local_time as tz_module

        monkeypatch.delenv("TZ", raising=False)

        def _always_missing(_key):
            raise ZoneInfoNotFoundError("no tz database on this platform")

        monkeypatch.setattr(tz_module, "ZoneInfo", _always_missing)
        assert tz_module.local_zone() is timezone.utc

    def test_dst_spring_forward_gap_does_not_crash(self, monkeypatch):
        """Europe/Berlin spring-forward 2026-03-29 jumps 02:00 → 03:00 local;
        02:30 wall-clock does not exist. ``replace(hour=2, minute=30)`` should
        still normalise to a valid UTC instant via astimezone, not raise.
        """
        monkeypatch.setenv("TZ", "Europe/Berlin")
        service = LocalBackupService()
        # 2026-03-29 00:30 UTC == 01:30 Berlin (CET, just before the gap)
        now = datetime(2026, 3, 29, 0, 30, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            # 02:30 local is in the non-existent gap on that day.
            result = service._calculate_next_run("daily", "02:30")
        # Result must be a UTC-aware datetime — exact value depends on
        # zoneinfo's gap normalisation; we just guarantee no crash and that
        # the run is in the future of ``now``.
        assert result.tzinfo == timezone.utc
        assert result > now


class TestPruneBackups:
    """Tests for backup retention pruning."""

    def test_prune_keeps_retention_count(self, tmp_path):
        service = LocalBackupService()
        # Create 5 backup files
        for i in range(5):
            f = tmp_path / f"bambuddy-backup-20260412-{i:06d}.zip"
            f.write_text(f"backup{i}")
        service._prune_backups(tmp_path, retention=3)
        remaining = list(tmp_path.glob("bambuddy-backup-*.zip"))
        assert len(remaining) == 3

    def test_prune_noop_when_under_retention(self, tmp_path):
        service = LocalBackupService()
        for i in range(2):
            f = tmp_path / f"bambuddy-backup-20260412-{i:06d}.zip"
            f.write_text(f"backup{i}")
        service._prune_backups(tmp_path, retention=5)
        remaining = list(tmp_path.glob("bambuddy-backup-*.zip"))
        assert len(remaining) == 2

    def test_prune_only_touches_matching_files(self, tmp_path):
        service = LocalBackupService()
        # Create backup files and a non-backup file
        for i in range(3):
            f = tmp_path / f"bambuddy-backup-20260412-{i:06d}.zip"
            f.write_text(f"backup{i}")
        other = tmp_path / "other_file.txt"
        other.write_text("keep me")
        service._prune_backups(tmp_path, retention=1)
        assert other.exists()
        remaining = list(tmp_path.glob("bambuddy-backup-*.zip"))
        assert len(remaining) == 1


class TestResolveBackupFile:
    """Tests for backup file resolution with path traversal protection."""

    def test_valid_filename(self, tmp_path):
        service = LocalBackupService()
        f = tmp_path / "bambuddy-backup-20260412-120000.zip"
        f.write_text("data")
        result = service.resolve_backup_file(str(tmp_path), "bambuddy-backup-20260412-120000.zip")
        assert result == f

    def test_path_traversal_blocked(self, tmp_path):
        service = LocalBackupService()
        result = service.resolve_backup_file(str(tmp_path), "../etc/passwd")
        assert result is None

    def test_backslash_blocked(self, tmp_path):
        service = LocalBackupService()
        result = service.resolve_backup_file(str(tmp_path), "..\\etc\\passwd")
        assert result is None

    def test_dotdot_blocked(self, tmp_path):
        service = LocalBackupService()
        result = service.resolve_backup_file(str(tmp_path), "..bambuddy-backup.zip")
        assert result is None

    def test_wrong_prefix_blocked(self, tmp_path):
        service = LocalBackupService()
        f = tmp_path / "evil-file.zip"
        f.write_text("data")
        result = service.resolve_backup_file(str(tmp_path), "evil-file.zip")
        assert result is None

    def test_nonexistent_file(self, tmp_path):
        service = LocalBackupService()
        result = service.resolve_backup_file(str(tmp_path), "bambuddy-backup-20260412-120000.zip")
        assert result is None


class TestDeleteBackup:
    """Tests for backup deletion."""

    def test_delete_valid_backup(self, tmp_path):
        service = LocalBackupService()
        f = tmp_path / "bambuddy-backup-20260412-120000.zip"
        f.write_text("data")
        result = service.delete_backup(str(tmp_path), "bambuddy-backup-20260412-120000.zip")
        assert result["success"] is True
        assert not f.exists()

    def test_delete_nonexistent_backup(self, tmp_path):
        service = LocalBackupService()
        result = service.delete_backup(str(tmp_path), "bambuddy-backup-20260412-120000.zip")
        assert result["success"] is False

    def test_delete_path_traversal_blocked(self, tmp_path):
        service = LocalBackupService()
        result = service.delete_backup(str(tmp_path), "../important.zip")
        assert result["success"] is False


class TestListBackups:
    """Tests for backup listing."""

    def test_list_empty_dir(self, tmp_path):
        service = LocalBackupService()
        result = service.list_backups(str(tmp_path))
        assert result == []

    def test_list_nonexistent_dir(self):
        service = LocalBackupService()
        result = service.list_backups("/nonexistent/path/12345")
        assert result == []

    def test_list_only_matching_files(self, tmp_path):
        service = LocalBackupService()
        (tmp_path / "bambuddy-backup-20260412-120000.zip").write_text("a")
        (tmp_path / "bambuddy-backup-20260412-130000.zip").write_text("bb")
        (tmp_path / "other-file.txt").write_text("ccc")
        result = service.list_backups(str(tmp_path))
        assert len(result) == 2
        assert all(r["filename"].startswith("bambuddy-backup-") for r in result)

    def test_list_sorted_newest_first(self, tmp_path):
        import time

        service = LocalBackupService()
        f1 = tmp_path / "bambuddy-backup-20260412-120000.zip"
        f1.write_text("a")
        time.sleep(0.05)
        f2 = tmp_path / "bambuddy-backup-20260412-130000.zip"
        f2.write_text("b")
        result = service.list_backups(str(tmp_path))
        assert result[0]["filename"] == "bambuddy-backup-20260412-130000.zip"

    def test_list_includes_size(self, tmp_path):
        service = LocalBackupService()
        (tmp_path / "bambuddy-backup-20260412-120000.zip").write_bytes(b"x" * 1024)
        result = service.list_backups(str(tmp_path))
        assert result[0]["size"] == 1024


class TestGetStatus:
    """Tests for status reporting."""

    def test_initial_status(self):
        service = LocalBackupService()
        status = service.get_status()
        assert status["is_running"] is False
        assert status["last_backup_at"] is None
        assert status["last_status"] is None
        assert status["next_run"] is None
