"""Regression tests for ArchiveService.attach_timelapse path-traversal guard.

``filename`` ultimately comes from a printer's FTP listing or a query
parameter on ``POST /archives/{id}/timelapse/select``. A compromised printer
that returns a malicious filename (e.g. ``"../../etc/passwd"``) used to land
the write outside the archive directory. The safe-join helper now rejects
such names; this test locks the behaviour in.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.archive import ArchiveService


@pytest.mark.asyncio
async def test_attach_timelapse_rejects_dotdot_filename(tmp_path: Path, monkeypatch):
    """A ``..`` traversal in filename must not land bytes outside archive_dir."""
    # Stage an archive directory that the service thinks is owned.
    archive_dir = tmp_path / "archive" / "1" / "20260101_test"
    archive_dir.mkdir(parents=True)
    # Repoint settings.base_dir so attach_timelapse's archive_dir = file_path.parent
    # resolves to our tmp directory.
    monkeypatch.setattr(
        "backend.app.services.archive.settings",
        MagicMock(base_dir=tmp_path),
    )

    db = MagicMock()
    db.commit = AsyncMock()
    service = ArchiveService(db)

    # Mock the archive lookup to return a row whose file_path resolves under tmp_path.
    fake_archive = MagicMock()
    fake_archive.file_path = "archive/1/20260101_test/file.3mf"
    service.get_archive = AsyncMock(return_value=fake_archive)

    # The attacker-controlled filename in the threat model.
    malicious = "../../etc/passwd_pwned"

    result = await service.attach_timelapse(
        archive_id=1,
        timelapse_data=b"would-be-attacker-payload",
        filename=malicious,
    )

    # The helper rejected the join → service returns False.
    assert result is False
    # And no payload landed at the target outside archive_dir.
    target_outside = tmp_path / "etc" / "passwd_pwned"
    assert not target_outside.exists(), "Attacker payload landed outside archive_dir"
    # And no payload landed under archive_dir either (since we rejected before write).
    assert not list(archive_dir.glob("*"))


@pytest.mark.asyncio
async def test_attach_timelapse_rejects_absolute_filename(tmp_path: Path, monkeypatch):
    """An absolute path in filename must not collapse the join."""
    archive_dir = tmp_path / "archive" / "1" / "20260101_test"
    archive_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "backend.app.services.archive.settings",
        MagicMock(base_dir=tmp_path),
    )

    db = MagicMock()
    db.commit = AsyncMock()
    service = ArchiveService(db)

    fake_archive = MagicMock()
    fake_archive.file_path = "archive/1/20260101_test/file.3mf"
    service.get_archive = AsyncMock(return_value=fake_archive)

    result = await service.attach_timelapse(
        archive_id=1,
        timelapse_data=b"x",
        filename="/tmp/owned_via_absolute",
    )

    assert result is False
    assert not Path("/tmp/owned_via_absolute").exists()


@pytest.mark.asyncio
async def test_attach_timelapse_accepts_legit_filename(tmp_path: Path, monkeypatch):
    """The legitimate happy path must still work — the fix isn't over-strict."""
    archive_dir = tmp_path / "archive" / "1" / "20260101_test"
    archive_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "backend.app.services.archive.settings",
        MagicMock(base_dir=tmp_path),
    )

    db = MagicMock()
    db.commit = AsyncMock()
    service = ArchiveService(db)

    fake_archive = MagicMock()
    fake_archive.file_path = "archive/1/20260101_test/file.3mf"
    fake_archive.timelapse_path = None
    service.get_archive = AsyncMock(return_value=fake_archive)

    result = await service.attach_timelapse(
        archive_id=1,
        timelapse_data=b"hello-timelapse",
        filename="timelapse_2026-01-01_12-00-00.mp4",
    )

    assert result is True
    landed = archive_dir / "timelapse_2026-01-01_12-00-00.mp4"
    assert landed.exists()
    assert landed.read_bytes() == b"hello-timelapse"
