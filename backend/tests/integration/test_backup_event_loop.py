"""A backup must not stall the event loop while it copies and zips files.

On a large install the archive directory holds gigabytes. Copying it and
deflating the ZIP on the event loop froze the whole app for minutes during the
nightly backup: no printer status, no queue, no API.

With that work in threads, two things the blocked loop used to rule out by
accident have to be ruled out on purpose: two backups at once (each stages its
own copy of the archive), and a cancelled backup deleting its staging folder
while a copy thread still writes into it.
"""

from __future__ import annotations

import asyncio
import io
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.core.config import settings as app_settings
from backend.app.core.db_dialect import is_sqlite


@pytest.fixture
def backup_env(monkeypatch, tmp_path):
    """A data dir with one archived file, an output folder and a temp dir of its own."""
    from backend.app.api.routes import settings as settings_routes

    data = tmp_path / "data"
    (data / "archive" / "1").mkdir(parents=True)
    (data / "archive" / "1" / "part.gcode.3mf").write_bytes(b"x" * 4096)
    out = tmp_path / "out"
    out.mkdir()
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setattr(app_settings, "base_dir", data)
    monkeypatch.setattr(app_settings, "plate_calibration_dir", data / "plate_calibration")
    # Staging folders and temp ZIPs land here, where a leftover shows.
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    # A lock that once had to wait is bound to that event loop, and every test
    # runs in a loop of its own.
    monkeypatch.setattr(settings_routes, "_backup_lock", asyncio.Lock())
    return SimpleNamespace(data=data, out=out, temp=temp)


def _probe_table(rows: list[str]) -> str:
    """Give the test app database a table with these rows; returns its path."""
    db_path = app_settings.database_url.replace("sqlite+aiosqlite:///", "")
    conn = sqlite3.connect(db_path)
    try:
        # The app runs its database in WAL mode (core/database.py).
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS backup_probe (value TEXT)")
        conn.execute("DELETE FROM backup_probe")
        conn.executemany("INSERT INTO backup_probe VALUES (?)", [(row,) for row in rows])
        conn.commit()
    finally:
        conn.close()
    return db_path


def _database_in(zip_path: Path, into: Path) -> sqlite3.Connection:
    with zipfile.ZipFile(zip_path) as zf:
        return sqlite3.connect(zf.extract("bambuddy.db", into))


class _Gate:
    """Holds the backups' directory copies until released; counts them and how many ran at once."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.most_at_once = 0
        self._running = 0
        self._lock = threading.Lock()

    def wrap(self, func):
        def run(*args, **kwargs):
            with self._lock:
                self.calls += 1
                self._running += 1
                self.most_at_once = max(self.most_at_once, self._running)
            self.started.set()
            try:
                self.release.wait(10)
                return func(*args, **kwargs)
            finally:
                with self._lock:
                    self._running -= 1

        return run


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_event_loop_keeps_running_while_the_backup_copies_files(
    async_client, backup_env, monkeypatch, tmp_path
):
    from backend.app.api.routes import settings as settings_routes

    if is_sqlite():
        # A row the copy has to carry over; the test app database has no schema of its own.
        _probe_table(["kept"])

    ticks = 0
    ticks_during: dict[str, int] = {}

    def slowed(step, func):
        def run(*args, **kwargs):
            if step in ticks_during:  # copytree copies subfolders through itself
                return func(*args, **kwargs)
            before = ticks
            time.sleep(0.5)  # stands in for a multi-gigabyte archive
            ticks_during[step] = ticks - before
            return func(*args, **kwargs)

        return run

    monkeypatch.setattr(shutil, "copytree", slowed("copytree", shutil.copytree))
    monkeypatch.setattr(settings_routes, "_write_backup_zip", slowed("zip", settings_routes._write_backup_zip))
    if is_sqlite():
        monkeypatch.setattr(
            settings_routes, "_copy_sqlite_database", slowed("database", settings_routes._copy_sqlite_database)
        )

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        zip_path, _filename = await settings_routes.create_backup_zip(output_path=backup_env.out)
    finally:
        task.cancel()

    # A step run on the loop would see no tick at all during its half second.
    expected = {"copytree", "zip", "database"} if is_sqlite() else {"copytree", "zip"}
    assert set(ticks_during) == expected
    for step, count in ticks_during.items():
        assert count >= 10, f"the event loop stood still during {step}"

    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert "archive/1/part.gcode.3mf" in names
        assert "manifest.json" in names
        if is_sqlite():
            conn = _database_in(zip_path, tmp_path / "check")
            try:
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert conn.execute("SELECT value FROM backup_probe").fetchall() == [("kept",)]
            finally:
                conn.close()
    finally:
        zip_path.unlink(missing_ok=True)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.sqlite_only
async def test_the_database_copy_is_consistent_while_the_app_keeps_writing(
    async_client, backup_env, monkeypatch, tmp_path
):
    """The app goes on writing while the database is copied in its thread."""
    from backend.app.api.routes import settings as settings_routes

    db_path = _probe_table([])
    writer = sqlite3.connect(db_path, check_same_thread=False)
    writing = threading.Event()
    stop = threading.Event()

    def keep_writing():
        while not stop.is_set():
            writer.execute("INSERT INTO backup_probe VALUES ('while copying')")
            writer.commit()
            writing.set()

    real_copy = settings_routes._copy_sqlite_database

    def copy_while_the_app_writes(src, dest):
        # Committed after the backup's checkpoint, so it sits in the WAL, which
        # a copy of the bare database file leaves out.
        writer.execute("INSERT INTO backup_probe VALUES ('before the copy')")
        writer.commit()
        thread = threading.Thread(target=keep_writing)
        thread.start()
        try:
            assert writing.wait(10)
            real_copy(src, dest)
        finally:
            stop.set()
            thread.join()

    monkeypatch.setattr(settings_routes, "_copy_sqlite_database", copy_while_the_app_writes)
    try:
        zip_path, _filename = await settings_routes.create_backup_zip(output_path=backup_env.out)
    finally:
        writer.close()

    conn = _database_in(zip_path, tmp_path / "check")
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        values = {row[0] for row in conn.execute("SELECT value FROM backup_probe")}
    finally:
        conn.close()
        zip_path.unlink(missing_ok=True)
    assert "before the copy" in values


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("step", ["copytree", "zip"])
async def test_a_cancelled_backup_waits_for_its_copy_then_removes_the_staging_folder(
    async_client, backup_env, monkeypatch, step
):
    from backend.app.api.routes import settings as settings_routes

    started = threading.Event()
    finished = threading.Event()
    staging: list[Path] = []

    def slowed(func, staging_folder):
        def run(*args, **kwargs):
            if started.is_set():  # copytree copies subfolders through itself
                return func(*args, **kwargs)
            staging.append(staging_folder(*args))
            started.set()
            try:
                time.sleep(0.5)
                return func(*args, **kwargs)
            finally:
                finished.set()

        return run

    if step == "copytree":
        monkeypatch.setattr(shutil, "copytree", slowed(shutil.copytree, lambda _src, dst: Path(dst).parent))
    else:
        monkeypatch.setattr(
            settings_routes, "_write_backup_zip", slowed(settings_routes._write_backup_zip, lambda temp, _zip: temp)
        )

    backup = asyncio.create_task(settings_routes.create_backup_zip(output_path=backup_env.out))
    assert await asyncio.to_thread(started.wait, 10)
    backup.cancel()
    # The cancel comes out as it went in, not as an error from the clean-up.
    with pytest.raises(asyncio.CancelledError):
        await backup
    returned_after_the_copy = finished.is_set()

    # A copy still running at this point would recreate the folder.
    assert await asyncio.to_thread(finished.wait, 10)
    assert returned_after_the_copy
    assert not staging[0].exists()
    assert list(backup_env.temp.iterdir()) == []
    # Nor is there a ZIP the caller never got.
    assert list(backup_env.out.iterdir()) == []
    assert not settings_routes._backup_lock.locked()
    assert settings_routes._backup_claims == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_download_during_a_local_backup_is_refused_with_409(async_client, backup_env, monkeypatch):
    from backend.app.api.routes import settings as settings_routes
    from backend.app.services.local_backup import LocalBackupService

    gate = _Gate()
    monkeypatch.setattr(settings_routes, "_copy_backup_dirs", gate.wrap(settings_routes._copy_backup_dirs))

    local = asyncio.create_task(LocalBackupService().run_backup({"path": str(backup_env.out), "retention": 5}))
    try:
        assert await asyncio.to_thread(gate.started.wait, 10)
        resp = await async_client.get("/api/v1/settings/backup")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "A backup is already running"
        # Refused before staging anything: the local backup's folder is the only one.
        assert len(list(backup_env.temp.iterdir())) == 1
    finally:
        gate.release.set()

    result = await local
    assert result["success"] is True
    assert gate.calls == 1
    assert list(backup_env.temp.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_local_backup_waits_for_a_running_download(async_client, backup_env, monkeypatch):
    from backend.app.api.routes import settings as settings_routes
    from backend.app.services.local_backup import LocalBackupService

    gate = _Gate()
    monkeypatch.setattr(settings_routes, "_copy_backup_dirs", gate.wrap(settings_routes._copy_backup_dirs))

    download = asyncio.create_task(async_client.get("/api/v1/settings/backup"))
    try:
        assert await asyncio.to_thread(gate.started.wait, 10)
        local = asyncio.create_task(LocalBackupService().run_backup({"path": str(backup_env.out), "retention": 5}))
        await asyncio.sleep(0.3)
        # Queued: neither refused nor copying next to the download.
        assert not local.done()
        assert gate.calls == 1
    finally:
        gate.release.set()

    resp = await download
    assert resp.status_code == 200
    assert "archive/1/part.gcode.3mf" in zipfile.ZipFile(io.BytesIO(resp.content)).namelist()

    result = await local
    assert result["success"] is True
    assert gate.calls == 2
    assert gate.most_at_once == 1
    assert len(list(backup_env.out.glob("bambuddy-backup-*.zip"))) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_failed_backup_lets_the_next_one_run(async_client, backup_env, monkeypatch):
    from backend.app.api.routes import settings as settings_routes
    from backend.app.services.local_backup import LocalBackupService

    real_write = settings_routes._write_backup_zip

    def disk_full(_temp_path, zip_file):
        zip_file.write_bytes(b"PK")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(settings_routes, "_write_backup_zip", disk_full)
    resp = await async_client.get("/api/v1/settings/backup")
    assert resp.status_code == 500
    # Neither the staging folder nor the half-written temp ZIP stays behind.
    assert list(backup_env.temp.iterdir()) == []
    assert not settings_routes._backup_lock.locked()
    assert settings_routes._backup_claims == 0

    # Both kinds of caller get through afterwards; a waiting one would hang on a held lock.
    monkeypatch.setattr(settings_routes, "_write_backup_zip", real_write)
    resp = await asyncio.wait_for(async_client.get("/api/v1/settings/backup"), 30)
    assert resp.status_code == 200
    service = LocalBackupService()
    result = await asyncio.wait_for(service.run_backup({"path": str(backup_env.out), "retention": 5}), 30)
    assert result["success"] is True
