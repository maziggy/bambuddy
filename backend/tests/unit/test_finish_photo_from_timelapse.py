"""Tests for _capture_finish_photo_from_timelapse (#1397).

The polling helper runs in parallel with _scan_for_timelapse_with_retries —
it waits for archive.timelapse_path to land in the DB, then extracts the
last frame as the finish photo. These tests exercise the four shapes the
helper has to handle correctly:

  1. timelapse never lands within timeout → return None (caller falls back)
  2. timelapse lands, extraction succeeds → return filename
  3. timelapse lands, extraction fails → return None (caller falls back)
  4. timelapse_path is set but the file doesn't exist on disk → keep polling

DB access is patched at the session-maker boundary so these tests run in
~50ms each without standing up a real engine.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app import main as main_module
from backend.app.main import _capture_finish_photo_from_timelapse


@asynccontextmanager
async def _fake_session(archive):
    """A fake session whose execute().scalar_one_or_none() returns `archive`.

    `archive` is mutated by the test mid-poll to simulate the real flow:
    the timelapse-attach background task setting `timelapse_path` after a
    few poll cycles.
    """
    result = SimpleNamespace(scalar_one_or_none=lambda: archive)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    yield session


@pytest.fixture
def fake_archive():
    """Mutable archive stand-in. Tests flip `.timelapse_path` to simulate
    the timelapse-attach task writing to the DB."""
    return SimpleNamespace(id=42, timelapse_path=None)


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    """Shrink poll interval + timeout so tests don't sleep for real."""
    monkeypatch.setattr(main_module, "_FINISH_PHOTO_TIMELAPSE_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(main_module, "_FINISH_PHOTO_TIMELAPSE_POLL_TIMEOUT_SECONDS", 0.2)


@pytest.fixture
def patched_session(fake_archive, monkeypatch):
    """Patch main.async_session so the helper reads our fake archive."""
    monkeypatch.setattr(main_module, "async_session", lambda: _fake_session(fake_archive))
    return fake_archive


async def test_returns_none_when_timelapse_never_lands(tmp_path: Path, patched_session):
    """Print finished without a timelapse — bail after timeout so the caller
    falls back to the live-camera grab."""
    result = await _capture_finish_photo_from_timelapse(
        archive_id=42,
        archive_dir=tmp_path,
    )
    assert result is None


async def test_extracts_frame_when_timelapse_lands(tmp_path: Path, patched_session, monkeypatch):
    """Simulate the timelapse landing after one poll cycle and extraction
    succeeding — should return a filename matching the finish_*.jpg pattern."""
    # Lay down a stub timelapse file relative to base_dir so the path
    # join works the way the helper expects.
    monkeypatch.setattr(main_module.app_settings, "base_dir", tmp_path)
    video_relpath = Path("archive/1/print/timelapse.mp4")
    video_abspath = tmp_path / video_relpath
    video_abspath.parent.mkdir(parents=True, exist_ok=True)
    video_abspath.write_bytes(b"x" * 100)  # non-empty so the size check passes

    # Patch extraction to succeed unconditionally — the actual ffmpeg
    # codepath has its own test file.
    async def fake_extract(src, dst):
        dst.write_bytes(b"\xff\xd8" + b"\x00" * 50)  # JPEG SOI
        return True

    monkeypatch.setattr(main_module, "_FINISH_PHOTO_TIMELAPSE_POLL_INTERVAL_SECONDS", 0.0)

    # Flip the archive into the "timelapse landed" state before the first
    # poll — the helper picks it up on its initial read.
    patched_session.timelapse_path = str(video_relpath)

    with patch(
        "backend.app.services.camera.extract_video_last_frame",
        new=fake_extract,
    ):
        result = await _capture_finish_photo_from_timelapse(
            archive_id=42,
            archive_dir=tmp_path / "archive_dir",
        )

    assert result is not None
    assert result.startswith("finish_")
    assert result.endswith(".jpg")
    assert (tmp_path / "archive_dir" / "photos" / result).exists()


async def test_returns_none_when_extraction_fails(tmp_path: Path, patched_session, monkeypatch):
    """Timelapse landed but ffmpeg said no — we don't keep retrying on the
    same broken file; return None so the caller falls back."""
    monkeypatch.setattr(main_module.app_settings, "base_dir", tmp_path)
    video_relpath = Path("archive/1/print/timelapse.mp4")
    video_abspath = tmp_path / video_relpath
    video_abspath.parent.mkdir(parents=True, exist_ok=True)
    video_abspath.write_bytes(b"x" * 100)

    async def fake_extract_fails(src, dst):
        return False

    patched_session.timelapse_path = str(video_relpath)

    with patch(
        "backend.app.services.camera.extract_video_last_frame",
        new=fake_extract_fails,
    ):
        result = await _capture_finish_photo_from_timelapse(
            archive_id=42,
            archive_dir=tmp_path / "archive_dir",
        )

    assert result is None


async def test_polls_until_file_appears(tmp_path: Path, patched_session, monkeypatch):
    """timelapse_path is set, but the file isn't on disk yet (the attach
    background task hasn't finished writing). Should keep polling — and
    succeed once the file materialises."""
    monkeypatch.setattr(main_module.app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(main_module, "_FINISH_PHOTO_TIMELAPSE_POLL_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(main_module, "_FINISH_PHOTO_TIMELAPSE_POLL_TIMEOUT_SECONDS", 1.0)

    video_relpath = Path("archive/1/print/timelapse.mp4")
    patched_session.timelapse_path = str(video_relpath)

    # File not present yet. Schedule it to land after ~150ms.
    import asyncio

    async def materialise_later():
        await asyncio.sleep(0.15)
        video_abspath = tmp_path / video_relpath
        video_abspath.parent.mkdir(parents=True, exist_ok=True)
        video_abspath.write_bytes(b"x" * 100)

    async def fake_extract(src, dst):
        dst.write_bytes(b"\xff\xd8")
        return True

    materialise = asyncio.create_task(materialise_later())
    try:
        with patch(
            "backend.app.services.camera.extract_video_last_frame",
            new=fake_extract,
        ):
            result = await _capture_finish_photo_from_timelapse(
                archive_id=42,
                archive_dir=tmp_path / "archive_dir",
            )
    finally:
        materialise.cancel()

    assert result is not None
    assert result.startswith("finish_")
