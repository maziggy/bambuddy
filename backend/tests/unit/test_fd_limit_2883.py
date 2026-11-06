"""The open-file limit is raised at startup, and bundles say what holds it (#2883).

#2883 ran out of descriptors at Docker's default soft limit of 1024. From then
on every new SQLite connection failed with "disk I/O error", and after 44 hours
the database was corrupt. The soft limit is the process's own to raise up to the
hard limit, so startup does that on every install. The support bundle now counts
descriptors by kind, so the next report shows what held them.
"""

import resource
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from backend.app.core import fd_limit

INF = resource.RLIM_INFINITY


@pytest.fixture(autouse=True)
def _reset_status():
    fd_limit.startup_status = None
    yield
    fd_limit.startup_status = None


def _run(soft, hard, setrlimit=None):
    calls = []

    def _set(which, limits):
        calls.append(limits)
        if setrlimit is not None:
            setrlimit(limits)

    with (
        patch.object(resource, "getrlimit", return_value=(soft, hard)),
        patch.object(resource, "setrlimit", side_effect=_set),
    ):
        status = fd_limit.raise_open_file_limit()
    return status, calls


class TestRaiseOpenFileLimit:
    def test_the_soft_limit_is_raised_to_the_hard_one(self):
        status, calls = _run(1024, 524288)

        assert calls == [(524288, 524288)]
        assert status == {"soft_at_start": 1024, "hard": 524288, "soft": 524288, "raised": True}
        assert fd_limit.startup_status == status

    def test_a_limit_already_at_the_hard_one_is_left_alone(self):
        status, calls = _run(524288, 524288)

        assert calls == []
        assert status["raised"] is False

    def test_an_unlimited_soft_limit_is_left_alone(self):
        status, calls = _run(INF, INF)

        assert calls == []
        assert status["soft"] == "unlimited"

    def test_an_unlimited_hard_limit_gets_a_finite_soft_one(self):
        """macOS reports RLIM_INFINITY but refuses a soft limit above
        kern.maxfilesperproc, so finite values are tried in turn."""

        def refuse_the_first(limits):
            if limits[0] == 65536:
                raise ValueError("not allowed")

        status, calls = _run(256, INF, setrlimit=refuse_the_first)

        assert calls == [(65536, INF), (10240, INF)]
        assert status["soft"] == 10240
        assert status["hard"] == "unlimited"
        assert status["raised"] is True

    def test_a_refused_raise_is_logged_and_startup_carries_on(self, caplog):
        def refuse(limits):
            raise OSError("operation not permitted")

        status, _ = _run(1024, 4096, setrlimit=refuse)

        assert status["raised"] is False
        assert status["soft"] == 1024
        assert "operation not permitted" in status["error"]
        assert any("Could not raise the open-file limit" in r.getMessage() for r in caplog.records)

    def test_an_unreadable_limit_is_not_fatal(self):
        with patch.object(resource, "getrlimit", side_effect=OSError("nope")):
            assert fd_limit.raise_open_file_limit() is None

    def test_no_rlimit_module_is_not_fatal(self):
        """Windows has no ``resource`` module."""
        import builtins

        real_import = builtins.__import__

        def no_resource(name, *args, **kwargs):
            if name == "resource":
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=no_resource):
            assert fd_limit.raise_open_file_limit() is None


class TestDescriptorsInTheSupportBundle:
    @pytest.mark.parametrize(
        ("target", "kind"),
        [
            ("socket:[12345]", "socket"),
            ("pipe:[678]", "pipe"),
            ("anon_inode:[eventpoll]", "anon_inode"),
            ("/app/data/bambuddy.db", "database"),
            ("/app/data/bambuddy.db-wal", "database_wal"),
            ("/app/data/bambuddy.db-shm", "database_shm"),
            ("/dev/null", "device"),
            ("/app/logs/bambuddy.log", "file"),
        ],
    )
    def test_kinds(self, target, kind):
        from backend.app.api.routes.support import _fd_kind

        assert _fd_kind(target) == kind

    def test_counts_by_kind_against_the_limit_without_paths(self, tmp_path):
        import psutil

        from backend.app.api.routes.support import _collect_fd_info

        db = sqlite3.connect(tmp_path / "secret-name.db")
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("CREATE TABLE t (x)")
        try:
            info = _collect_fd_info(psutil.Process())
        finally:
            db.close()

        assert info["num_fds"] > 0
        assert info["fds_by_type"]["database"] >= 1
        assert info["fds_by_type"]["database_wal"] >= 1
        assert set(info["fd_limit"]) == {"soft", "hard"}
        assert "secret-name" not in repr(info)

    def test_the_startup_result_is_carried(self):
        from backend.app.api.routes.support import _collect_fd_info

        fd_limit.startup_status = {"soft_at_start": 1024, "hard": 524288, "soft": 524288, "raised": True}

        info = _collect_fd_info(MagicMock())

        assert info["fd_limit_at_startup"]["soft_at_start"] == 1024

    def test_a_failing_probe_still_returns(self):
        from backend.app.api.routes.support import _collect_fd_info

        proc = MagicMock()
        proc.num_fds.side_effect = RuntimeError("restricted")
        with patch("os.listdir", side_effect=PermissionError("no /proc")):
            info = _collect_fd_info(proc)

        assert "num_fds" not in info
        assert "fds_by_type" not in info
