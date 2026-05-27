"""Tests for _summarize_ffmpeg_stderr (#925).

The ffmpeg banner (version / build / configuration / lib*) dumps ~20 lines
before any actual error. Before this fix, every failed camera retry logged
the full banner, producing hundreds of lines per failure — see #925 where a
single click produced 555 lines across 30 retries. The helper strips the
banner so logs stay focused on the real error.
"""

import asyncio

from backend.app.api.routes.camera import _read_ffmpeg_stderr, _summarize_ffmpeg_stderr

_FAKE_BANNER = """ffmpeg version 7.1.3-0+deb13u1 Copyright (c) 2000-2025 the FFmpeg developers
  built with gcc 14 (Debian 14.2.0-19)
  configuration: --prefix=/usr --extra-version=0+deb13u1 --toolchain=hardened --enable-gpl --enable-gnutls
  libavutil      59. 39.100 / 59. 39.100
  libavcodec     61. 19.101 / 61. 19.101
  libavformat    61.  7.100 / 61.  7.100
  libavdevice    61.  3.100 / 61.  3.100
  libavfilter    10.  4.100 / 10.  4.100
  libswscale      8.  3.100 /  8.  3.100
  libswresample   5.  3.100 /  5.  3.100
  libpostproc    58.  3.100 / 58.  3.100
"""


def test_empty_input():
    assert _summarize_ffmpeg_stderr("") == ""
    assert _summarize_ffmpeg_stderr(None) == ""


def test_keeps_error_lines_drops_banner():
    stderr = _FAKE_BANNER + (
        "[in#0 @ 0x64a7cd6350c0] Error opening input: Invalid data found when processing input\n"
        "Error opening input file rtsp://[CREDENTIALS]@192.0.2.1:322/streaming/live/1.\n"
        "Error opening input files: Invalid data found when processing input\n"
    )
    result = _summarize_ffmpeg_stderr(stderr)

    # Banner gone
    assert "ffmpeg version" not in result
    assert "configuration:" not in result
    assert "libavcodec" not in result

    # Real errors preserved
    assert "Error opening input: Invalid data found when processing input" in result
    assert "Error opening input file rtsp" in result


def test_caps_at_10_lines():
    stderr = _FAKE_BANNER + "\n".join(f"error line {i}" for i in range(25))
    result = _summarize_ffmpeg_stderr(stderr)

    lines = result.splitlines()
    assert len(lines) == 10
    # Keeps the *last* 10 lines (most recent errors closest to failure)
    assert lines[-1] == "error line 24"
    assert lines[0] == "error line 15"


def test_drops_blank_lines():
    stderr = "real error\n\n\n   \nsecond error\n"
    result = _summarize_ffmpeg_stderr(stderr)
    assert result == "real error\nsecond error"


def test_banner_only_returns_empty():
    """If ffmpeg prints only the banner (no errors), the summary should be empty."""
    assert _summarize_ffmpeg_stderr(_FAKE_BANNER) == ""


# --- _read_ffmpeg_stderr (#1395) -------------------------------------------
# A stalled-but-alive ffmpeg (the P2S RTSP failure) never closes stderr, so a
# read-to-EOF discarded everything it had already printed. _read_ffmpeg_stderr
# now drains incrementally and must return that buffered output.


class _FakeProcess:
    """Minimal stand-in for asyncio.subprocess.Process — only .stderr is read."""

    def __init__(self, stderr):
        self.stderr = stderr


def _reader_with(data: bytes, *, eof: bool) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    if data:
        reader.feed_data(data)
    if eof:
        reader.feed_eof()
    return reader


async def test_read_stderr_captures_output_from_a_running_ffmpeg():
    """The #1395 regression: ffmpeg is alive and has NOT closed stderr (no EOF).
    The output it already printed must still be returned, not discarded while
    waiting for an EOF that never arrives."""
    stderr = _FAKE_BANNER + "[rtsp @ 0x5] Could not find codec parameters\n"
    proc = _FakeProcess(_reader_with(stderr.encode(), eof=False))
    result = await _read_ffmpeg_stderr(proc)
    assert result is not None
    assert "Could not find codec parameters" in result
    assert "ffmpeg version" not in result  # banner still stripped


async def test_read_stderr_captures_output_from_an_exited_ffmpeg():
    stderr = _FAKE_BANNER + "Error opening input: Connection refused\n"
    proc = _FakeProcess(_reader_with(stderr.encode(), eof=True))
    result = await _read_ffmpeg_stderr(proc)
    assert result is not None
    assert "Connection refused" in result


async def test_read_stderr_returns_none_when_no_stderr_pipe():
    assert await _read_ffmpeg_stderr(_FakeProcess(None)) is None


async def test_read_stderr_returns_none_for_banner_only_output():
    """Banner with no actionable lines summarizes to empty -> None."""
    proc = _FakeProcess(_reader_with(_FAKE_BANNER.encode(), eof=True))
    assert await _read_ffmpeg_stderr(proc) is None
