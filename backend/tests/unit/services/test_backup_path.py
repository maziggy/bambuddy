"""A backup directory the service cannot write to must say so, and say why (#2544).

The reporting bug this guards against: our own systemd unit ships
``ProtectSystem=strict``, so a NAS share the operator mounted and can write to
from their shell is read-only *for the service*. The kernel calls that EROFS,
the UI showed the raw ``[Errno 30] Read-only file system``, and the reporter
spent a week checking folder permissions — which were fine, because EROFS is not
a permission error.
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from backend.app.services import backup_path
from backend.app.services.backup_path import (
    classify_backup_dir_error,
    probe_backup_dir,
    systemd_unit_name,
)

NAS = Path("/mnt/nasbackup")


class TestSystemdUnitName:
    def test_none_when_not_started_by_systemd(self, monkeypatch):
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        assert systemd_unit_name() is None

    @pytest.mark.parametrize(
        ("cgroup", "expected"),
        [
            ("0::/system.slice/bambuddy.service\n", "bambuddy.service"),
            ("0::/system.slice/system-bambuddy.slice/bambuddy@1.service\n", "bambuddy@1.service"),
            # No .service in the path (a user scope, say) — still name something usable.
            ("0::/user.slice/user-1000.slice/session-3.scope\n", "bambuddy.service"),
        ],
    )
    def test_reads_the_unit_name_from_the_cgroup(self, monkeypatch, cgroup, expected):
        monkeypatch.setenv("INVOCATION_ID", "deadbeef")
        monkeypatch.setattr(Path, "read_text", lambda _self, *a, **k: cgroup)

        assert systemd_unit_name() == expected

    def test_falls_back_to_bambuddy_when_the_cgroup_is_unreadable(self, monkeypatch):
        monkeypatch.setenv("INVOCATION_ID", "deadbeef")

        def boom(_path):
            raise OSError("no /proc here")

        monkeypatch.setattr(Path, "read_text", boom)
        assert systemd_unit_name() == "bambuddy.service"


class TestClassifyReadOnly:
    def test_erofs_under_systemd_blames_the_sandbox_and_hands_over_the_fix(self, monkeypatch):
        monkeypatch.setattr(backup_path, "systemd_unit_name", lambda: "bambuddy.service")

        result = classify_backup_dir_error(OSError(errno.EROFS, "Read-only file system"), NAS)

        assert result["writable"] is False
        assert result["code"] == "sandboxed"
        assert "ProtectSystem=strict" in result["message"]
        # The remedy has to be copy-pasteable, with their path already in it.
        assert "systemctl edit bambuddy.service" in result["remedy"]
        assert "ReadWritePaths=/mnt/nasbackup" in result["remedy"]

    def test_erofs_outside_systemd_does_not_blame_a_unit_that_does_not_exist(self, monkeypatch):
        monkeypatch.setattr(backup_path, "systemd_unit_name", lambda: None)

        result = classify_backup_dir_error(OSError(errno.EROFS, "Read-only file system"), NAS)

        assert result["code"] == "read_only"
        assert result["remedy"] is None
        assert "systemd" not in result["message"]

    def test_eacces_is_a_permission_problem_not_a_sandbox_one(self, monkeypatch):
        monkeypatch.setattr(backup_path, "systemd_unit_name", lambda: "bambuddy.service")

        result = classify_backup_dir_error(OSError(errno.EACCES, "Permission denied"), NAS)

        assert result["code"] == "permission_denied"
        assert result["remedy"] is None

    @pytest.mark.parametrize(
        ("errno_value", "expected"),
        [
            (errno.ENOSPC, "no_space"),
            (errno.ENOTDIR, "not_a_directory"),
            (errno.ENOENT, "missing"),
            (errno.EIO, "error"),
        ],
    )
    def test_other_errnos_keep_their_own_identity(self, errno_value, expected):
        result = classify_backup_dir_error(OSError(errno_value, "boom"), NAS)
        assert result["code"] == expected
        assert result["writable"] is False


class TestProbe:
    def test_a_writable_directory_is_reported_writable_and_left_clean(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_path, "is_running_in_docker", lambda: False)
        target = tmp_path / "backups"

        result = probe_backup_dir(target)

        assert result["writable"] is True
        assert result["code"] == "ok"
        assert result["warning"] is None
        assert target.is_dir()
        # The probe file must not survive — it would show up in the backup list.
        assert list(target.iterdir()) == []

    def test_a_read_only_directory_is_diagnosed_not_just_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_path, "systemd_unit_name", lambda: "bambuddy.service")
        target = tmp_path / "nasbackup"
        target.mkdir()

        def refuse(*_args, **_kwargs):
            raise OSError(errno.EROFS, "Read-only file system")

        monkeypatch.setattr(backup_path.tempfile, "NamedTemporaryFile", refuse)

        result = probe_backup_dir(target)

        assert result["writable"] is False
        assert result["code"] == "sandboxed"
        assert str(target) in result["remedy"]

    def test_docker_path_on_the_container_layer_is_writable_but_flagged(self, tmp_path, monkeypatch):
        """Writable is not the same as persistent: an un-mounted host path inside a
        container accepts the write and then loses it on the next `up`.
        """
        monkeypatch.setattr(backup_path, "is_running_in_docker", lambda: True)
        monkeypatch.setattr(backup_path, "_is_container_ephemeral", lambda _p: True)

        result = probe_backup_dir(tmp_path / "backups")

        assert result["writable"] is True
        assert result["warning"] == "container_ephemeral"
        assert "volumes:" in result["remedy"]

    def test_docker_path_on_a_mounted_volume_is_not_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_path, "is_running_in_docker", lambda: True)
        monkeypatch.setattr(backup_path, "_is_container_ephemeral", lambda _p: False)

        result = probe_backup_dir(tmp_path / "backups")

        assert result["writable"] is True
        assert result["warning"] is None
