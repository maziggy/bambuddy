"""The restore converts a Windows backup's paths, and a failure there does not fail it (#3501)."""

import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.core.config import settings as app_settings


async def _restore(async_client, monkeypatch, tmp_path, convert):
    from backend.app.api.routes.settings import create_backup_zip

    # Keep the restored database separate from the integration-test engine.
    db_path = tmp_path / "bambuddy.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE backup_marker (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    zip_path, _ = await create_backup_zip(output_path=tmp_path)

    with (
        patch("backend.app.core.database.close_all_connections", new_callable=AsyncMock),
        patch("backend.app.core.database.reinitialize_database", new_callable=AsyncMock),
        patch("backend.app.core.database.init_db", new_callable=AsyncMock),
        patch("backend.app.core.database.invalidate_all_sessions", new_callable=AsyncMock),
        patch("backend.app.services.print_scheduler.scheduler.stop"),
        patch("backend.app.services.smart_plug_manager.smart_plug_manager.stop_scheduler"),
        patch("backend.app.services.notification_service.notification_service.stop_digest_scheduler"),
        patch("backend.app.services.backup_paths.convert_windows_paths", convert),
    ):
        return await async_client.post(
            "/api/v1/settings/restore",
            files={"file": ("backup.zip", zip_path.read_bytes(), "application/zip")},
        )


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.sqlite_only  # backs up and restores the app's own SQLite database file
async def test_the_restore_converts_the_paths(async_client, monkeypatch, tmp_path):
    convert = AsyncMock(return_value=0)

    response = await _restore(async_client, monkeypatch, tmp_path, convert)

    assert response.status_code == 200, response.text
    convert.assert_awaited_once()
    assert convert.await_args.args[1] == tmp_path


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.sqlite_only
async def test_a_failed_conversion_does_not_fail_the_restore(async_client, monkeypatch, tmp_path):
    convert = AsyncMock(side_effect=RuntimeError("boom"))

    response = await _restore(async_client, monkeypatch, tmp_path, convert)

    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
