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
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.core.config import APP_VERSION, settings as app_settings


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_backup_records_the_version_that_made_it(async_client, monkeypatch, tmp_path):
    from backend.app.api.routes.settings import create_backup_zip

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)

    zip_path, _filename = await create_backup_zip(output_path=tmp_path)
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
