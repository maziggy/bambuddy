"""Tests for _summarize_ffmpeg_stderr (#925).

The ffmpeg banner (version / build / configuration / lib*) dumps ~20 lines
before any actual error. Before this fix, every failed camera retry logged
the full banner, producing hundreds of lines per failure — see #925 where a
single click produced 555 lines across 30 retries. The helper strips the
banner so logs stay focused on the real error.
"""

from backend.app.api.routes.camera import _summarize_ffmpeg_stderr

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
