"""Regression tests for the #2572 timelapse-scan session-boundary refactor.

``POST /archives/{id}/timelapse/scan`` used to hold its ``Depends(get_db)``
session open across the FTP directory listing *and* the multi-MB video
download. It now (1) reads the archive + printer in a short session and
releases the pooled connection *before* the FTP work, then (2) re-opens a
fresh short session only to attach the downloaded file.

Two things that refactor could have broken, one test each:

* The matching logic reads ``archive.filename/started_at/completed_at/
  created_at`` and ``printer.ip_address/...`` AFTER the read session has
  closed. If any were a lazy-loaded relationship (or an expired column) that
  would raise ``DetachedInstanceError``. The not-found test drives every
  match strategy, exercising all of those detached reads.

* The attach write runs in a *fresh* ``async_session()``, which — unlike
  ``get_db`` — does NOT auto-commit on block exit. If ``attach_timelapse``
  didn't commit internally the write would be silently dropped. The attach
  test asserts the row is actually persisted.

FTP is fully mocked, so no printer is contacted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
@pytest.mark.integration
async def test_scan_timelapse_no_match_reads_detached_archive_scalars(
    async_client: AsyncClient, archive_factory, printer_factory, db_session
):
    """Two non-matching videos → 200 not_found, driving every match strategy.

    Strategies 2-4 read archive.started_at/completed_at/created_at after the
    read session closed; this fails with DetachedInstanceError if the refactor
    left one of those as a lazy load.
    """
    printer = await printer_factory()
    archive = await archive_factory(printer.id, filename="test_print.gcode.3mf")

    # Two videos, neither matching by name, no mtime, and the archive has no
    # started_at — so strategy 1 (name) misses, 2 (start time) and 3 (mtime)
    # are skipped, and 4 (single-file fallback) is disqualified by len == 2.
    listing = [
        {"name": "clip_a.mp4", "path": "/timelapse/clip_a.mp4", "is_directory": False, "size": 10, "mtime": None},
        {"name": "clip_b.mp4", "path": "/timelapse/clip_b.mp4", "is_directory": False, "size": 20, "mtime": None},
    ]

    with (
        patch("backend.app.services.bambu_ftp.list_files_async", AsyncMock(return_value=listing)),
        patch(
            "backend.app.services.bambu_ftp.get_ftp_retry_settings",
            AsyncMock(return_value=(False, 3, 2.0, 30.0)),
        ),
        patch(
            "backend.app.services.bambu_ftp.download_file_bytes_async",
            AsyncMock(return_value=b"should-not-be-called"),
        ) as mock_download,
    ):
        response = await async_client.post(f"/api/v1/archives/{archive.id}/timelapse/scan")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "not_found"
    assert {f["name"] for f in data["available_files"]} == {"clip_a.mp4", "clip_b.mp4"}
    # No match → we never download.
    mock_download.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_scan_timelapse_attaches_and_persists_via_fresh_session(
    async_client: AsyncClient, archive_factory, printer_factory, db_session, tmp_path, monkeypatch
):
    """A name-matched video is downloaded and the attach PERSISTS.

    Guards the fresh-session write boundary: attach_timelapse runs in a new
    async_session that does not auto-commit on exit, so this only passes if
    the service commits internally.
    """
    printer = await printer_factory()
    archive = await archive_factory(printer.id, filename="test_print.gcode.3mf")

    # attach_timelapse writes into settings.base_dir / archive.file_path's
    # parent, then stores a base_dir-relative timelapse_path. Point base_dir at
    # tmp and stage the archive dir so the real write succeeds (mirrors
    # test_attach_timelapse_safe_path).
    monkeypatch.setattr(
        "backend.app.services.archive.settings",
        MagicMock(base_dir=tmp_path),
    )
    archive_dir = tmp_path / "archives" / "test"
    archive_dir.mkdir(parents=True)

    # base_name = Path("test_print.gcode.3mf").stem = "test_print.gcode", so this
    # video matches by name (strategy 1). .mp4 → no background conversion task.
    matched = {
        "name": "test_print.gcode.mp4",
        "path": "/timelapse/test_print.gcode.mp4",
        "is_directory": False,
        "size": 4096,
        "mtime": None,
    }
    video_bytes = b"fake-timelapse-video-bytes"

    with (
        patch("backend.app.services.bambu_ftp.list_files_async", AsyncMock(return_value=[matched])),
        patch(
            "backend.app.services.bambu_ftp.get_ftp_retry_settings",
            AsyncMock(return_value=(False, 3, 2.0, 30.0)),
        ),
        patch(
            "backend.app.services.bambu_ftp.download_file_bytes_async",
            AsyncMock(return_value=video_bytes),
        ) as mock_download,
    ):
        response = await async_client.post(f"/api/v1/archives/{archive.id}/timelapse/scan")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "attached"
    assert data["filename"] == "test_print.gcode.mp4"
    mock_download.assert_awaited_once()

    # The write happened in the route's fresh session; confirm it was committed
    # by re-reading the row on the separate test session.
    await db_session.refresh(archive)
    assert archive.timelapse_path is not None
    assert archive.timelapse_path.endswith("test_print.gcode.mp4")
    # And the bytes actually landed on disk under the staged archive dir.
    assert (archive_dir / "test_print.gcode.mp4").read_bytes() == video_bytes
