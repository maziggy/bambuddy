"""The backup says which version made it, and restore refuses one it cannot import.

Restoring a backup into a different version of Bambuddy is ordinary -- an
upgrade, a rebuild, a move to another host. What is not ordinary is the Postgres
restore path, which throws the backup's schema away and rebuilds from the
running ORM. A NOT NULL column the running version has and the backup does not
then has nothing to put in it, and the import fails on the INSERT: after the
drop, with the install's data already gone.

So the incompatibility has to be found before any of that, and it has to say
something an operator can act on. A column name does not; a pair of version
numbers does, which is what the manifest is for.
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.core.config import APP_VERSION, settings as app_settings


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_backup_records_the_version_that_made_it(async_client, monkeypatch, tmp_path):
    from backend.app.api.routes.settings import create_backup_zip

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)

    zip_path, _filename, _warnings = await create_backup_zip(output_path=tmp_path)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            assert "manifest.json" in zf.namelist()
            manifest = json.loads(zf.read("manifest.json"))
    finally:
        zip_path.unlink(missing_ok=True)

    assert manifest["app_version"] == APP_VERSION
    assert manifest["format"] == 1
    assert manifest["database"] in ("sqlite", "postgresql")
    assert manifest["created_at"]


def _incompatible_backup(tmp_path: Path, *, version: str) -> bytes:
    """A backup whose `cost_centers` has no `name` -- NOT NULL, no default."""
    db = tmp_path / "bambuddy.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE cost_centers (id INTEGER PRIMARY KEY, code TEXT)")
    conn.execute("INSERT INTO cost_centers (id, code) VALUES (1, 'abc')")
    conn.commit()
    conn.close()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.write(db, "bambuddy.db")
        zf.writestr("manifest.json", json.dumps({"format": 1, "app_version": version}))
    return buffer.getvalue()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_unimportable_backup_is_refused_with_both_versions(async_client, tmp_path):
    """A 400 naming the two versions, and -- the point -- nothing touched.

    is_sqlite is patched false because this is the PostgreSQL path: a SQLite
    install restores by copying the backup's pages, schema and all, and has
    never had this problem.
    """
    payload = _incompatible_backup(tmp_path, version="99.9.9")

    with patch("backend.app.core.db_dialect.is_sqlite", return_value=False):
        response = await async_client.post(
            "/api/v1/settings/restore",
            files={"file": ("backup.zip", payload, "application/zip")},
        )

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "cost_centers.name" in detail
    assert "99.9.9" in detail
    assert APP_VERSION in detail
    assert "Nothing has been changed" in detail


@pytest.fixture
def restore_env(monkeypatch, tmp_path):
    # Keep the restored database separate from the integration-test engine.
    db_path = tmp_path / "bambuddy.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE backup_marker (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(app_settings, "plate_calibration_dir", tmp_path / "plate_calibration")
    with (
        patch("backend.app.core.database.close_all_connections", new_callable=AsyncMock),
        patch("backend.app.core.database.reinitialize_database", new_callable=AsyncMock),
        patch("backend.app.core.database.init_db", new_callable=AsyncMock),
        patch("backend.app.core.database.invalidate_all_sessions", new_callable=AsyncMock),
        patch("backend.app.services.print_scheduler.scheduler.stop"),
        patch("backend.app.services.smart_plug_manager.smart_plug_manager.stop_scheduler"),
        patch("backend.app.services.notification_service.notification_service.stop_digest_scheduler"),
    ):
        yield tmp_path


async def _restore_zip(client, payload):
    response = await client.post(
        "/api/v1/settings/restore",
        files={"file": ("backup.zip", payload, "application/zip")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"]
    return response.json()


@pytest.mark.sqlite_only
@pytest.mark.asyncio
@pytest.mark.integration
async def test_overlay_logo_survives_backup_and_restore(async_client, restore_env):
    from backend.app.api.routes.settings import create_backup_zip

    logo_dir = restore_env / "overlay-branding"
    logo_dir.mkdir()
    logo = logo_dir / "logo.png"
    original = b"saved overlay logo"
    logo.write_bytes(original)

    zip_path, *_ = await create_backup_zip(output_path=restore_env)
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.read("overlay-branding/logo.png") == original
    logo.write_bytes(b"replacement logo")
    (logo_dir / "stale.png").write_bytes(b"stale")

    await _restore_zip(async_client, zip_path.read_bytes())
    assert logo.read_bytes() == original
    assert not (logo_dir / "stale.png").exists()


@pytest.mark.sqlite_only
@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("legacy", [False, True], ids=["captured", "legacy"])
async def test_restore_of_empty_data_directories(async_client, restore_env, legacy):
    names = ["archive", "virtual_printer", "plate_calibration", "icons", "projects", "overlay-branding"]
    for name in names:
        (restore_env / name).mkdir()
    saved = restore_env / "projects" / "design.pdf"
    if legacy:
        saved.write_bytes(b"saved")

    snapshot = await async_client.get("/api/v1/settings/backup")
    assert snapshot.status_code == 200
    payload = snapshot.content
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if legacy:
            manifest.pop("captured_dirs", None)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as old:
                for member in archive.namelist():
                    old.writestr(member, json.dumps(manifest) if member == "manifest.json" else archive.read(member))
            payload = buffer.getvalue()

    saved.write_bytes(b"changed")
    for name in names:
        (restore_env / name / "later").write_bytes(b"later")
    if not legacy:
        shutil.rmtree(restore_env / "icons")  # A captured empty folder can be absent at restore time.
    await _restore_zip(async_client, payload)
    for name in names:
        assert (restore_env / name).is_dir()
        assert (restore_env / name / "later").exists() == (legacy and name != "projects"), name
    if legacy:
        assert saved.read_bytes() == b"saved"
    else:
        assert not saved.exists()
        assert set(manifest["captured_dirs"]) == set(names)


@pytest.mark.sqlite_only
@pytest.mark.asyncio
@pytest.mark.integration
async def test_restore_preserves_a_partly_unreadable_directory(async_client, restore_env, monkeypatch):
    from backend.app.api.routes.settings import create_backup_zip

    projects = restore_env / "projects"
    projects.mkdir()
    nested = projects / "nested"
    nested.mkdir()
    (nested / "readable").write_text("saved")
    (nested / "recover").write_text("recover me")
    (nested / "unreadable").write_text("private")
    (restore_env / "icons").mkdir()
    copyfile = shutil.copyfile

    def copy_file(src, dst, **kwargs):
        if Path(src).name == "unreadable":
            Path(dst).write_text("truncated")
            raise PermissionError("File is unreadable")
        return copyfile(src, dst, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(shutil, "copyfile", copy_file)
        path, _, warnings = await create_backup_zip(output_path=restore_env)
    assert warnings == ["projects/nested/unreadable"]
    with zipfile.ZipFile(path) as archive:
        assert archive.read("projects/nested/readable") == b"saved"
        assert "projects/nested/unreadable" not in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))

    (nested / "unreadable").write_text("still here")
    (nested / "readable").write_text("changed")
    (nested / "recover").unlink()
    (projects / "later").write_text("still here")
    (restore_env / "icons" / "later").touch()
    archive_dir = restore_env / "archive"  # Did not exist at backup time.
    archive_dir.mkdir()
    (archive_dir / "later").touch()
    result = await _restore_zip(async_client, path.read_bytes())
    assert "could not be fully restored (projects)" in result["message"]
    assert (nested / "unreadable").read_text() == "still here"
    assert (nested / "readable").read_text() == "saved"
    assert (nested / "recover").read_text() == "recover me"
    assert (projects / "later").read_text() == "still here"
    assert list((restore_env / "icons").iterdir()) == []
    assert (archive_dir / "later").is_file()
    assert manifest["captured_dirs"] == ["icons"]


@pytest.mark.sqlite_only
@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("invalid", ["icons", ["icons", None]])
async def test_invalid_directory_list_is_rejected_before_restore(async_client, restore_env, invalid):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("bambuddy.db", b"invalid database")
        archive.writestr("manifest.json", json.dumps({"captured_dirs": invalid}))
    icons = restore_env / "icons"
    icons.mkdir()
    (icons / "current").write_bytes(b"current")
    response = await async_client.post(
        "/api/v1/settings/restore", files={"file": ("backup.zip", payload.getvalue(), "application/zip")}
    )
    assert response.status_code == 400
    assert "captured_dirs" in response.json()["detail"]
    with sqlite3.connect(restore_env / "bambuddy.db") as db:
        assert db.execute("SELECT COUNT(*) FROM backup_marker").fetchone() == (0,)
    assert (icons / "current").read_bytes() == b"current"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.sqlite_only  # backs up and restores the app's own SQLite database file
async def test_a_restore_stops_the_virtual_printers(async_client, monkeypatch, tmp_path):
    """vp-2: the "stop virtual printer" step called the legacy configure(),
    which ignores its arguments and re-syncs from the database not yet
    replaced, so their FTP, MQTT and SSDP services ran on through the file
    swap. A successful restore asks for a restart, as for the other services."""
    from backend.app.api.routes.settings import create_backup_zip
    from backend.app.services.virtual_printer import virtual_printer_manager

    db_path = tmp_path / "bambuddy.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE backup_marker (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    zip_path, *_ = await create_backup_zip(output_path=tmp_path)
    monkeypatch.setitem(virtual_printer_manager._instances, 4242, object())

    with (
        patch("backend.app.core.database.close_all_connections", new_callable=AsyncMock),
        patch("backend.app.core.database.reinitialize_database", new_callable=AsyncMock),
        patch("backend.app.core.database.init_db", new_callable=AsyncMock),
        patch("backend.app.core.database.invalidate_all_sessions", new_callable=AsyncMock),
        patch("backend.app.services.print_scheduler.scheduler.stop"),
        patch("backend.app.services.smart_plug_manager.smart_plug_manager.stop_scheduler"),
        patch("backend.app.services.notification_service.notification_service.stop_digest_scheduler"),
        patch.object(virtual_printer_manager, "stop_all", new_callable=AsyncMock) as stop_all,
        patch.object(virtual_printer_manager, "configure", new_callable=AsyncMock) as configure,
    ):
        response = await async_client.post(
            "/api/v1/settings/restore",
            files={"file": ("backup.zip", zip_path.read_bytes(), "application/zip")},
        )

    assert response.status_code == 200, response.text
    stop_all.assert_awaited_once()
    configure.assert_not_awaited()
