"""Regression for #1504: ffmpeg RTSP socket-I/O timeout flag.

The RTSP demuxer's client-side socket I/O timeout option name varies by
ffmpeg version (full chronology in
`backend/app/services/camera.rtsp_socket_timeout_flag`). Hard-coding
either ``-timeout`` or ``-stimeout`` regresses one half of the install
base. The flag is therefore probed at runtime; this module tests that
probe and guards against either RTSP ffmpeg argv re-hard-coding the
wrong literal.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

import backend.app.services.camera as camera_svc
from backend.app.services.camera import rtsp_socket_timeout_flag


@pytest.fixture(autouse=True)
def _reset_cache():
    """The probe caches its result in a module-level global. Reset it
    before every test so each one sees a fresh probe."""
    camera_svc._rtsp_socket_timeout_flag = None
    yield
    camera_svc._rtsp_socket_timeout_flag = None


class TestRtspSocketTimeoutFlagProbe:
    def test_prefers_stimeout_when_ffmpeg_advertises_it(self):
        """Transitional ffmpeg (~late-4.x): both options are listed and
        ``-timeout`` is the broken listen-mode option — pick ``-stimeout``."""
        transitional_help = (
            "  -listen_timeout    <int>  ... incoming connections ...\n"
            "  -stimeout          <int64> ... socket TCP I/O ...\n"
            "  -timeout           <int>  ... DEPRECATED ...\n"
        )
        with (
            patch.object(camera_svc, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("backend.app.services.camera.subprocess.run") as mock_run,
        ):
            mock_run.return_value.stdout = transitional_help
            mock_run.return_value.stderr = ""
            assert rtsp_socket_timeout_flag() == "stimeout"

    def test_falls_back_to_timeout_on_modern_ffmpeg(self):
        """Modern ffmpeg (5+/6+/7+): ``-stimeout`` no longer exists and
        ``-timeout`` is back to meaning socket I/O — pick ``-timeout``."""
        modern_help = (
            "  -listen_timeout    <int>  ... incoming connections ...\n"
            "  -timeout           <int64> ... socket I/O ...\n"
            "  -reorder_queue_size <int> ... reordered packets ...\n"
        )
        with (
            patch.object(camera_svc, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("backend.app.services.camera.subprocess.run") as mock_run,
        ):
            mock_run.return_value.stdout = modern_help
            mock_run.return_value.stderr = ""
            assert rtsp_socket_timeout_flag() == "timeout"

    def test_defaults_to_timeout_when_ffmpeg_missing(self):
        """No ffmpeg available — return the modern default so we don't
        wedge ffmpeg-less unit tests trying to import camera.py."""
        with patch.object(camera_svc, "get_ffmpeg_path", return_value=None):
            assert rtsp_socket_timeout_flag() == "timeout"

    def test_defaults_to_timeout_when_probe_raises(self):
        """If subprocess probe blows up, prefer the modern default —
        breaking the transitional-ffmpeg case is preferable to crashing
        every live-view start."""
        with (
            patch.object(camera_svc, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("backend.app.services.camera.subprocess.run", side_effect=OSError("boom")),
        ):
            assert rtsp_socket_timeout_flag() == "timeout"

    def test_result_is_cached_across_calls(self):
        """Probing ffmpeg is a subprocess spawn; cache it for the
        process lifetime (ffmpeg won't swap mid-run)."""
        with (
            patch.object(camera_svc, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("backend.app.services.camera.subprocess.run") as mock_run,
        ):
            mock_run.return_value.stdout = "  -timeout  <int64>\n"
            mock_run.return_value.stderr = ""
            rtsp_socket_timeout_flag()
            rtsp_socket_timeout_flag()
            rtsp_socket_timeout_flag()
            assert mock_run.call_count == 1

    def test_substring_match_does_not_false_positive(self):
        """Match the option as ``-stimeout `` (trailing space) so an
        unrelated mention like ``-listen_timeout`` or a fragment in
        another section doesn't trick us into picking the missing flag."""
        only_listen_help = (
            "  -listen_timeout    <int>  ... incoming connections ...\n"
            "  -timeout           <int64> ... socket I/O ...\n"
        )
        with (
            patch.object(camera_svc, "get_ffmpeg_path", return_value="/usr/bin/ffmpeg"),
            patch("backend.app.services.camera.subprocess.run") as mock_run,
        ):
            mock_run.return_value.stdout = only_listen_help
            mock_run.return_value.stderr = ""
            assert rtsp_socket_timeout_flag() == "timeout"


class TestRtspArgvUsesProbe:
    """The two RTSP ffmpeg callers must not hard-code either flag literal —
    they must consume the probe so version-dependent correctness is
    preserved. Guards #1504 from being half-fixed again."""

    # Anchor on this file so the assertion is CWD-independent (pytest can
    # be invoked from the project root OR from backend/, depending on who
    # runs it). __file__ lives at backend/tests/unit/, so the repo root
    # is three parents up.
    _REPO_ROOT = Path(__file__).resolve().parents[3]
    _RTSP_FFMPEG_CALLERS = (
        "backend/app/api/routes/camera.py",
        "backend/app/services/external_camera.py",
    )

    @pytest.mark.parametrize("rel", _RTSP_FFMPEG_CALLERS)
    def test_no_hard_coded_timeout_literal(self, rel):
        """Neither RTSP ffmpeg argv may pass a hard-coded ``-timeout``
        or ``-stimeout`` literal — both must come from the probe."""
        src = (self._REPO_ROOT / rel).read_text()
        assert '"-timeout"' not in src, (
            f"{rel} hard-codes `-timeout` — this is the listen-mode option on "
            f"transitional ffmpeg (EADDRINUSE, #1504). Use rtsp_socket_timeout_flag()."
        )
        assert '"-stimeout"' not in src, (
            f"{rel} hard-codes `-stimeout` — this option was removed in ffmpeg 7. Use rtsp_socket_timeout_flag()."
        )
        assert "rtsp_socket_timeout_flag()" in src, (
            f"{rel} should derive its RTSP socket timeout flag from rtsp_socket_timeout_flag() — see #1504."
        )
