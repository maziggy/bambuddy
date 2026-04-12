"""Unit tests for scheduled local backup service (#884)."""

import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.local_backup import LocalBackupService


class TestCalculateNextRun:
    """Tests for _calculate_next_run scheduling logic."""

    def test_hourly_returns_next_full_hour(self):
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 14, 30, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("hourly", "03:00")
        assert result.hour == 15
        assert result.minute == 0

    def test_daily_before_target_time_schedules_today(self):
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 2, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("daily", "03:00")
        assert result.day == 12
        assert result.hour == 3

    def test_daily_after_target_time_schedules_tomorrow(self):
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 4, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("daily", "03:00")
        assert result.day == 13
        assert result.hour == 3

    def test_weekly_adds_full_week(self):
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 2, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("weekly", "03:00")
        expected = datetime(2026, 4, 19, 3, 0, 0, tzinfo=timezone.utc)
        assert result == expected

    def test_weekly_after_target_time_adds_full_week_from_tomorrow(self):
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 4, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("weekly", "03:00")
        expected = datetime(2026, 4, 20, 3, 0, 0, tzinfo=timezone.utc)
        assert result == expected

    def test_invalid_time_defaults_to_0300(self):
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 2, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("daily", "invalid")
        assert result.hour == 3
        assert result.minute == 0

    def test_unknown_schedule_type_defaults_to_daily(self):
        service = LocalBackupService()
        now = datetime(2026, 4, 12, 2, 0, 0, tzinfo=timezone.utc)
        with patch("backend.app.services.local_backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = service._calculate_next_run("every_5_min", "03:00")
        # Should fall through to daily behavior (time-based)
        assert result.hour == 3


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
