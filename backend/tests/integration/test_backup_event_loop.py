"""A backup must not stall the event loop while it copies and zips files.

On a large install the archive directory holds gigabytes. Copying it and
deflating the ZIP on the event loop froze the whole app for minutes during the
nightly backup: no printer status, no queue, no API.

With that work in threads, what the blocked loop used to rule out by accident
has to be ruled out on purpose: two backups at once (each stages its own copy
of the archive), a cancelled backup deleting its staging folder while a copy
thread still writes into it, and the list, Download, Restore or Delete reaching
a ZIP that is still being written.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import JSON, Column, Integer, MetaData, Table
from sqlalchemy.ext.asyncio import create_async_engine

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


class _Ticks:
    """Slows backup steps and counts how often the event loop ticked during each."""

    def __init__(self):
        self.ticks = 0
        self.during: dict[str, int] = {}

    def slowed(self, step, func):
        def run(*args, **kwargs):
            if step in self.during:  # copytree copies subfolders through itself
                return func(*args, **kwargs)
            before = self.ticks
            time.sleep(0.5)  # stands in for a multi-gigabyte archive
            self.during[step] = self.ticks - before
            return func(*args, **kwargs)

        return run

    async def run(self):
        while True:
            await asyncio.sleep(0.02)
            self.ticks += 1

    def assert_the_loop_kept_running(self, expected: set[str]):
        # A step run on the loop would see no tick at all during its half second.
        assert set(self.during) == expected
        for step, count in self.during.items():
            assert count >= 10, f"the event loop stood still during {step}"


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


def _lose_a_file_during_the_copy(backup_env, monkeypatch):
    """Have one archived file vanish between copytree's listing and its copy."""
    (backup_env.data / "archive" / "1" / "gone.gcode.3mf").write_bytes(b"y" * 16)
    real_copytree = shutil.copytree

    def copy(source, target, **kwargs):
        if Path(source).name == "gone.gcode.3mf":
            raise FileNotFoundError(2, "No such file or directory", source)
        return shutil.copy2(source, target, **kwargs)

    def copytree(src, dst, *args, **kwargs):
        if not args:  # subfolders come back through here with the copy_function
            kwargs["copy_function"] = copy
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copytree", copytree)


@pytest.fixture
async def postgres_export(monkeypatch, tmp_path):
    """Send the backup down its PostgreSQL branch, reading a small table of its own.

    All that branch needs is an async engine and the metadata, so this runs on
    every gate, not only on the PostgreSQL one.
    """
    from backend.app.core import database, db_dialect

    metadata = MetaData()
    table = Table("export_probe", metadata, Column("id", Integer, primary_key=True), Column("data", JSON))
    source = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'source.db'}")
    async with source.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.execute(table.insert(), [{"data": {"colour": "red"}}, {"data": [1, 2]}])

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: False)
    monkeypatch.setattr(database, "engine", source)
    monkeypatch.setattr(database, "Base", SimpleNamespace(metadata=metadata))
    yield metadata
    await source.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_event_loop_keeps_running_while_the_backup_copies_files(
    async_client, backup_env, monkeypatch, tmp_path
):
    from backend.app.api.routes import settings as settings_routes

    if is_sqlite():
        # A row the copy has to carry over; the test app database has no schema of its own.
        _probe_table(["kept"])

    ticks = _Ticks()
    monkeypatch.setattr(shutil, "copytree", ticks.slowed("copytree", shutil.copytree))
    monkeypatch.setattr(settings_routes, "_write_backup_zip", ticks.slowed("zip", settings_routes._write_backup_zip))
    if is_sqlite():
        monkeypatch.setattr(
            settings_routes, "_copy_sqlite_database", ticks.slowed("database", settings_routes._copy_sqlite_database)
        )

    task = asyncio.create_task(ticks.run())
    try:
        zip_path, _filename, _warnings = await settings_routes.create_backup_zip(output_path=backup_env.out)
    finally:
        task.cancel()

    ticks.assert_the_loop_kept_running({"copytree", "zip", "database"} if is_sqlite() else {"copytree", "zip"})

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
        zip_path, _filename, _warnings = await settings_routes.create_backup_zip(output_path=backup_env.out)
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


@pytest.mark.sqlite_only
@pytest.mark.parametrize("name", ["bambuddy.db", "bambuddy.db?timeout=30"])
def test_a_database_that_is_not_there_fails_the_copy_and_is_not_created(tmp_path, name):
    from backend.app.api.routes import settings as settings_routes

    with pytest.raises(sqlite3.OperationalError):
        settings_routes._copy_sqlite_database(tmp_path / name, tmp_path / "copy.db")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.sqlite_only
def test_the_database_copy_finds_a_path_that_needs_escaping(tmp_path):
    from backend.app.api.routes import settings as settings_routes

    folder = tmp_path / "my data #1 100%20"
    folder.mkdir()
    conn = sqlite3.connect(folder / "bambuddy.db")
    try:
        conn.execute("CREATE TABLE backup_probe (value TEXT)")
        conn.execute("INSERT INTO backup_probe VALUES ('kept')")
        conn.commit()
    finally:
        conn.close()

    settings_routes._copy_sqlite_database(folder / "bambuddy.db", tmp_path / "copy.db")

    conn = sqlite3.connect(tmp_path / "copy.db")
    try:
        assert conn.execute("SELECT value FROM backup_probe").fetchall() == [("kept",)]
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_event_loop_keeps_running_through_the_postgres_export(
    backup_env, postgres_export, monkeypatch, tmp_path
):
    from backend.app.api.routes import settings as settings_routes

    ticks = _Ticks()

    class SlowExport(sqlite3.Connection):
        inserted = False

        def executemany(self, *args):
            self.inserted = True
            return ticks.slowed("insert", super().executemany)(*args)

        def commit(self):
            # The schema step commits too, on a connection of its own.
            return ticks.slowed("commit", super().commit)() if self.inserted else super().commit()

    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: real_connect(*args, factory=SlowExport, **kwargs))
    monkeypatch.setattr(postgres_export, "create_all", ticks.slowed("schema", postgres_export.create_all))
    # The first row is the first json.dumps of the backup; the manifest comes later.
    monkeypatch.setattr(json, "dumps", ticks.slowed("serialize", json.dumps))

    task = asyncio.create_task(ticks.run())
    try:
        zip_path, _filename, _warnings = await settings_routes.create_backup_zip(output_path=backup_env.out)
    finally:
        task.cancel()

    ticks.assert_the_loop_kept_running({"schema", "serialize", "insert", "commit"})

    conn = _database_in(zip_path, tmp_path / "check")
    try:
        rows = [json.loads(row[0]) for row in conn.execute("SELECT data FROM export_probe ORDER BY id")]
    finally:
        conn.close()
    assert rows == [{"colour": "red"}, [1, 2]]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_failed_postgres_export_closes_its_database(backup_env, postgres_export, monkeypatch):
    from backend.app.api.routes import settings as settings_routes

    closed = []

    class NoticesClose(sqlite3.Connection):
        def close(self):
            closed.append(True)
            super().close()

    def disk_full(*_args):
        raise OSError(28, "No space left on device")

    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: real_connect(*args, factory=NoticesClose, **kwargs))
    monkeypatch.setattr(settings_routes, "_export_rows", disk_full)

    with pytest.raises(OSError, match="No space left"):
        await settings_routes.create_backup_zip(output_path=backup_env.out)

    assert closed == [True]
    assert list(backup_env.temp.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_cancelled_postgres_export_lets_its_insert_finish_before_closing(
    backup_env, postgres_export, monkeypatch
):
    from backend.app.api.routes import settings as settings_routes

    real_export = settings_routes._export_rows
    started = threading.Event()
    outcomes: list[str] = []

    def slow_export(*args):
        started.set()
        time.sleep(0.5)
        try:
            real_export(*args)
        except sqlite3.Error as e:  # a database closed under the running insert
            outcomes.append(str(e))
            raise
        outcomes.append("inserted")

    monkeypatch.setattr(settings_routes, "_export_rows", slow_export)

    backup = asyncio.create_task(settings_routes.create_backup_zip(output_path=backup_env.out))
    assert await asyncio.to_thread(started.wait, 10)
    backup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await backup

    assert outcomes == ["inserted"]
    assert list(backup_env.temp.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("step", ["copytree", "zip", "rename"])
@pytest.mark.parametrize("shutdown", [False, True], ids=["cancel", "shutdown"])
async def test_a_cancelled_backup_waits_for_its_copy_then_removes_the_staging_folder(
    async_client, backup_env, monkeypatch, step, shutdown
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
    elif step == "zip":
        monkeypatch.setattr(
            settings_routes, "_write_backup_zip", slowed(settings_routes._write_backup_zip, lambda temp, _zip: temp)
        )
    else:
        monkeypatch.setattr(os, "replace", slowed(os.replace, lambda *_paths: next(backup_env.temp.iterdir())))

    tasks_before = asyncio.all_tasks()
    backup = asyncio.create_task(settings_routes.create_backup_zip(output_path=backup_env.out))
    assert await asyncio.to_thread(started.wait, 10)
    backup.cancel()
    if shutdown:
        # asyncio.run cancels every task that is left, not only the backup.
        for task in asyncio.all_tasks() - tasks_before:
            task.cancel()
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
async def test_a_zip_being_written_is_out_of_reach_until_it_is_complete(async_client, backup_env, monkeypatch):
    """The list, Download, Restore and Delete can all run while the ZIP is written."""
    from backend.app.api.routes import settings as settings_routes
    from backend.app.services.local_backup import LocalBackupService

    real_write = settings_routes._write_backup_zip
    written = threading.Event()
    finish = threading.Event()

    def write_then_hold(temp_path, zip_file):
        real_write(temp_path, zip_file)
        written.set()
        finish.wait(10)

    monkeypatch.setattr(settings_routes, "_write_backup_zip", write_then_hold)

    service = LocalBackupService()
    path = str(backup_env.out)
    local = asyncio.create_task(service.run_backup({"path": path, "retention": 5}))
    try:
        assert await asyncio.to_thread(written.wait, 10)
        [part] = backup_env.out.iterdir()
        final_name = part.name.removeprefix(".").removesuffix(".part")
        assert final_name.startswith("bambuddy-backup-")
        assert final_name.endswith(".zip")

        assert service.list_backups(path) == []
        for name in (final_name, part.name):
            # Download and Restore go through resolve_backup_file.
            assert service.resolve_backup_file(path, name) is None
            assert service.delete_backup(path, name)["success"] is False
        assert part.exists()
    finally:
        finish.set()

    result = await local
    assert result["success"] is True
    assert result["filename"] == final_name
    assert [f.name for f in backup_env.out.iterdir()] == [final_name]
    assert [b["filename"] for b in service.list_backups(path)] == [final_name]
    with zipfile.ZipFile(backup_env.out / final_name) as zf:
        assert zf.testzip() is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_failed_zip_leaves_nothing_in_the_backup_folder(async_client, backup_env, monkeypatch):
    from backend.app.api.routes import settings as settings_routes
    from backend.app.services.local_backup import LocalBackupService

    def disk_full(_temp_path, zip_file):
        zip_file.write_bytes(b"PK")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(settings_routes, "_write_backup_zip", disk_full)

    result = await LocalBackupService().run_backup({"path": str(backup_env.out), "retention": 5})

    assert result["success"] is False
    assert list(backup_env.out.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_local_backup_that_lost_a_file_is_not_a_clean_success(async_client, backup_env, monkeypatch):
    from backend.app.services.local_backup import LocalBackupService

    service = LocalBackupService()
    settings = {"path": str(backup_env.out), "retention": 5}

    with monkeypatch.context() as patched:
        _lose_a_file_during_the_copy(backup_env, patched)
        result = await service.run_backup(settings)

    # The rest of the backup is good, so it is kept, and it says what it misses.
    assert result["success"] is True
    assert result["missing_files"] == 1
    assert "archive/1/gone.gcode.3mf" in result["message"]
    with zipfile.ZipFile(backup_env.out / result["filename"]) as zf:
        names = zf.namelist()
    assert "archive/1/part.gcode.3mf" in names
    assert "archive/1/gone.gcode.3mf" not in names

    status = service.get_status()
    assert status["last_status"] == "warning"
    assert status["last_missing_files"] == 1
    assert "archive/1/gone.gcode.3mf" in status["last_message"]

    # The next backup that gets everything is a clean success again.
    result = await service.run_backup(settings)
    assert result["success"] is True
    assert "missing_files" not in result
    status = service.get_status()
    assert status["last_status"] == "success"
    assert status["last_missing_files"] == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_download_that_lost_a_file_says_so(async_client, backup_env, monkeypatch):
    with monkeypatch.context() as patched:
        _lose_a_file_during_the_copy(backup_env, patched)
        resp = await async_client.get("/api/v1/settings/backup")

    assert resp.status_code == 200
    assert resp.headers["X-Backup-Missing-Files"] == "1"

    resp = await async_client.get("/api/v1/settings/backup")
    assert resp.status_code == 200
    assert "X-Backup-Missing-Files" not in resp.headers


def test_a_folder_that_could_not_be_read_counts_as_missing(monkeypatch, tmp_path):
    from backend.app.api.routes import settings as settings_routes

    source = tmp_path / "archive"
    source.mkdir()
    (source / "part.3mf").write_bytes(b"x")

    def denied(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(shutil, "copytree", denied)

    assert settings_routes._copy_backup_dirs([("archive", source)], tmp_path / "stage") == ["archive/"]


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
