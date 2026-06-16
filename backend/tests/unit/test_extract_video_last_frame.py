"""Tests for extract_video_last_frame (#1397).

Sources the finish photo from the per-print Bambu timelapse's last frame —
captured by firmware after the toolhead parks but before the bed-drop
end-gcode runs, so the print is framed correctly. A live camera grab at
gcode_state=FINISH would capture the bed already lowered.

We can't ship a real Bambu timelapse fixture in the repo (~7-11 MB each),
so the happy-path test builds a tiny synthetic MP4 with ffmpeg at runtime.
Failure paths (missing ffmpeg, missing source, subprocess failure, timeout)
are exercised with monkeypatching so the suite stays hermetic and fast.
"""

import asyncio
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.services.camera import extract_video_last_frame

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _make_synthetic_mp4(dest: Path, duration_seconds: float = 1.0) -> None:
    """Create a tiny test MP4 via ffmpeg's testsrc generator.

    Smallest valid MP4 we can construct without committing binary fixtures —
    one second of 32x32 testsrc, ultrafast encode, ~3-5 KB.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration_seconds}:size=32x32:rate=10",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        pytest.fail(f"ffmpeg fixture build failed (exit {result.returncode}): {result.stderr.decode()[:300]}")


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not on PATH")
async def test_extracts_jpeg_from_real_mp4(tmp_path: Path):
    src = tmp_path / "synthetic.mp4"
    _make_synthetic_mp4(src)
    out = tmp_path / "out.jpg"

    ok = await extract_video_last_frame(src, out)

    assert ok is True
    assert out.exists()
    assert out.stat().st_size > 0
    # JPEG starts with the SOI marker (FFD8). Lightweight sanity check —
    # we'd otherwise depend on Pillow just to decode.
    assert out.read_bytes()[:2] == b"\xff\xd8"


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not on PATH")
async def test_extracts_correctly_from_sub_second_video(tmp_path: Path):
    """Regression for #1397 round 1: small prints (few layers) produce
    sub-second Bambu timelapses (~0.6s / 16 frames). The earlier
    ``-sseof -1.0`` approach seeked 1 second before end → before the
    start of the file → ffmpeg silently returned frame 0. Verify the
    write-every-frame-overwrite approach grabs a real frame regardless
    of duration."""
    src = tmp_path / "short.mp4"
    _make_synthetic_mp4(src, duration_seconds=0.5)  # 5 frames at 10fps
    out = tmp_path / "out.jpg"

    ok = await extract_video_last_frame(src, out)

    assert ok is True
    assert out.exists()
    assert out.stat().st_size > 0
    assert out.read_bytes()[:2] == b"\xff\xd8"


async def test_returns_false_when_source_missing(tmp_path: Path):
    src = tmp_path / "does_not_exist.mp4"
    out = tmp_path / "out.jpg"

    ok = await extract_video_last_frame(src, out)

    assert ok is False
    assert not out.exists()


async def test_returns_false_when_source_empty(tmp_path: Path):
    src = tmp_path / "empty.mp4"
    src.touch()
    out = tmp_path / "out.jpg"

    ok = await extract_video_last_frame(src, out)

    assert ok is False
    assert not out.exists()


async def test_returns_false_when_ffmpeg_unavailable(tmp_path: Path):
    src = tmp_path / "any.mp4"
    src.write_bytes(b"\x00" * 100)
    out = tmp_path / "out.jpg"

    # Force the lookup path to return None — same shape as a host without
    # ffmpeg installed. We don't want to be skipped on CI here; the
    # not-installed path is a real production fallback and must be tested.
    with patch("backend.app.services.camera.get_ffmpeg_path", return_value=None):
        ok = await extract_video_last_frame(src, out)

    assert ok is False
    assert not out.exists()


async def test_returns_false_when_ffmpeg_exits_nonzero(tmp_path: Path):
    """ffmpeg failures (corrupt file, codec issue, etc.) return False, not
    raise. The caller falls through to the existing live-camera path."""
    src = tmp_path / "garbage.mp4"
    src.write_bytes(b"not actually an mp4" * 100)
    out = tmp_path / "out.jpg"

    # Use a real ffmpeg invocation on garbage — guaranteed to fail with a
    # non-zero exit code without us monkey-patching subprocess.
    if not _HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH; cannot exercise real failure path")

    ok = await extract_video_last_frame(src, out)

    assert ok is False
    # ffmpeg may briefly touch the output file before failing; we don't
    # require the file to be absent, only that the function reported failure
    # so the caller falls back.


async def test_returns_false_on_subprocess_timeout(tmp_path: Path, monkeypatch):
    """A hung ffmpeg (network FS, bad codec, kernel bug) must not block the
    finish-photo task forever. Patch ffmpeg to a sleep command that never
    finishes — confirms the timeout path kills the subprocess."""
    src = tmp_path / "stub.mp4"
    src.write_bytes(b"\x00" * 100)
    out = tmp_path / "out.jpg"

    sleep_path = shutil.which("sleep")
    if not sleep_path:
        pytest.skip("sleep binary not available")

    # Point get_ffmpeg_path at a real binary that never exits in 15s.
    monkeypatch.setattr("backend.app.services.camera.get_ffmpeg_path", lambda: sleep_path)
    # Tighten the timeout via monkeypatch on asyncio.wait_for to keep the
    # test fast — patch only inside the call so we don't affect the harness.
    real_wait_for = asyncio.wait_for

    async def short_wait_for(awaitable, timeout):
        return await real_wait_for(awaitable, timeout=0.5)

    monkeypatch.setattr("backend.app.services.camera.asyncio.wait_for", short_wait_for)

    ok = await extract_video_last_frame(src, out)

    assert ok is False
