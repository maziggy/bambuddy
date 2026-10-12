"""A backup must not stall the event loop while it copies and zips files.

On a large install the archive directory holds gigabytes. Copying it and
deflating the ZIP on the event loop froze the whole app for minutes during the
nightly backup: no printer status, no queue, no API.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import time
import zipfile

import pytest

from backend.app.core.config import settings as app_settings
from backend.app.core.db_dialect import is_sqlite


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_event_loop_keeps_running_while_the_backup_copies_files(async_client, monkeypatch, tmp_path):
    from backend.app.api.routes.settings import create_backup_zip

    data = tmp_path / "data"
    (data / "archive" / "1").mkdir(parents=True)
    (data / "archive" / "1" / "part.gcode.3mf").write_bytes(b"x" * 4096)
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setattr(app_settings, "base_dir", data)

    real_copytree = shutil.copytree

    def slow_copytree(*args, **kwargs):
        time.sleep(0.5)  # stands in for a multi-gigabyte archive
        return real_copytree(*args, **kwargs)

    monkeypatch.setattr(shutil, "copytree", slow_copytree)

    if is_sqlite():
        # A row the copy has to carry over; the test app database has no schema of its own.
        db_path = app_settings.database_url.replace("sqlite+aiosqlite:///", "")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS backup_probe (value TEXT)")
            conn.execute("DELETE FROM backup_probe")
            conn.execute("INSERT INTO backup_probe VALUES ('kept')")
            conn.commit()
        finally:
            conn.close()

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        zip_path, _filename = await create_backup_zip(output_path=out)
    finally:
        task.cancel()

    # Blocking, the loop would not tick at all during the half second.
    assert ticks >= 10

    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert "archive/1/part.gcode.3mf" in names
            assert "manifest.json" in names
            if is_sqlite():
                extracted = zf.extract("bambuddy.db", tmp_path / "check")
    finally:
        zip_path.unlink(missing_ok=True)

    if is_sqlite():
        conn = sqlite3.connect(extracted)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            probe = conn.execute("SELECT value FROM backup_probe").fetchall()
        finally:
            conn.close()
        assert probe == [("kept",)]
