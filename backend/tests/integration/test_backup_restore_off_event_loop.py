"""Backup and restore do their file work off the event loop.

Unzipping a backup, copying the data directories back and writing a backup ZIP
ran on the event loop. With a large archive folder that froze the whole server
for over half a minute -- no page, no websocket, no printer message -- and the
loop watchdog dumped every thread's stack in the middle of a restore that then
succeeded.

Each test makes one step slow and counts how often a ticker on the loop gets to
run meanwhile. On the loop, the step starves it; in a worker thread it ticks on.
"""

from __future__ import annotations

import asyncio
import io
import sqlite3
import time
import zipfile
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.api.routes import settings as settings_routes
from backend.app.core.config import settings as app_settings

STEP_SECONDS = 0.5
TICK_SECONDS = 0.02
# Off the loop the ticker runs ~25 times during the step; on it, not at all.
MIN_TICKS = 8


class _Ticker:
    """Counts loop ticks, and how many happened while the slow step ran."""

    def __init__(self):
        self.ticks = 0
        self.during_step: list[int] = []

    def slow(self, fn):
        def wrapper(*args, **kwargs):
            before = self.ticks
            time.sleep(STEP_SECONDS)
            self.during_step.append(self.ticks - before)
            return fn(*args, **kwargs)

        return wrapper

    async def run_while(self, coro):
        done = False

        async def tick():
            while not done:
                await asyncio.sleep(TICK_SECONDS)
                self.ticks += 1

        task = asyncio.create_task(tick())
        try:
            return await coro
        finally:
            done = True
            await task


def _data_dir(monkeypatch, tmp_path):
    db_path = tmp_path / "bambuddy.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE backup_marker (id INTEGER PRIMARY KEY)")
    (tmp_path / "archive" / "1").mkdir(parents=True)
    (tmp_path / "archive" / "1" / "print.3mf").write_bytes(b"x" * 1024)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "database_url", f"sqlite+aiosqlite:///{db_path}")


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.sqlite_only  # copies the app's own SQLite database file
@pytest.mark.parametrize("step", ["_copy_backup_dirs", "_write_backup_zip", "shutil.copy2"])
async def test_a_slow_backup_step_does_not_block_the_loop(async_client, monkeypatch, tmp_path, step):
    import shutil

    _data_dir(monkeypatch, tmp_path)
    ticker = _Ticker()
    if step == "shutil.copy2":
        monkeypatch.setattr(shutil, "copy2", ticker.slow(shutil.copy2))
    else:
        monkeypatch.setattr(settings_routes, step, ticker.slow(getattr(settings_routes, step)))

    out = tmp_path / "out"
    out.mkdir()
    zip_path, _ = await ticker.run_while(settings_routes.create_backup_zip(output_path=out))

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert "bambuddy.db" in names
    assert "archive/1/print.3mf" in names
    assert ticker.during_step, f"{step} was not called"
    assert min(ticker.during_step) >= MIN_TICKS, f"{step} blocked the event loop: {ticker.during_step}"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.sqlite_only  # restores into the app's own SQLite database file
@pytest.mark.parametrize("step", ["_extract_backup_zip", "_restore_sqlite_db", "_restore_data_dirs"])
async def test_a_slow_restore_step_does_not_block_the_loop(async_client, monkeypatch, tmp_path, step):
    _data_dir(monkeypatch, tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    zip_path, _ = await settings_routes.create_backup_zip(output_path=out)
    payload = zip_path.read_bytes()
    # The restore must put the archive back, so take it away first.
    (tmp_path / "archive" / "1" / "print.3mf").unlink()

    ticker = _Ticker()
    monkeypatch.setattr(settings_routes, step, ticker.slow(getattr(settings_routes, step)))
    with (
        patch("backend.app.core.database.close_all_connections", new_callable=AsyncMock),
        patch("backend.app.core.database.reinitialize_database", new_callable=AsyncMock),
        patch("backend.app.core.database.init_db", new_callable=AsyncMock),
        patch("backend.app.core.database.invalidate_all_sessions", new_callable=AsyncMock),
        patch("backend.app.services.backup_paths.convert_windows_paths", new_callable=AsyncMock),
        patch("backend.app.services.print_scheduler.scheduler.stop"),
        patch("backend.app.services.smart_plug_manager.smart_plug_manager.stop_scheduler"),
        patch("backend.app.services.notification_service.notification_service.stop_digest_scheduler"),
    ):
        response = await ticker.run_while(
            async_client.post(
                "/api/v1/settings/restore",
                files={"file": ("backup.zip", payload, "application/zip")},
            )
        )

    assert response.status_code == 200, response.text
    assert (tmp_path / "archive" / "1" / "print.3mf").read_bytes() == b"x" * 1024
    assert ticker.during_step, f"{step} was not called"
    assert min(ticker.during_step) >= MIN_TICKS, f"{step} blocked the event loop: {ticker.during_step}"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_unsafe_path_is_still_refused(async_client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("../evil.txt", "x")
        zf.writestr("bambuddy.db", "x")

    response = await async_client.post(
        "/api/v1/settings/restore",
        files={"file": ("backup.zip", buffer.getvalue(), "application/zip")},
    )

    assert response.status_code == 400
    assert "unsafe path" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_file_that_is_not_a_zip_is_still_refused(async_client):
    response = await async_client.post(
        "/api/v1/settings/restore",
        files={"file": ("backup.zip", b"not a zip", "application/zip")},
    )

    assert response.status_code == 400
    assert "not a valid ZIP" in response.json()["detail"]
