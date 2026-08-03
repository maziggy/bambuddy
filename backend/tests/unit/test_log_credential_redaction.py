"""Credentials must never reach bambuddy.log.

Subprocesses echo their input URL back at us: ffmpeg prints the RTSP input in
its ``Input #0`` line, so logging its stderr verbatim published the printer
access code (or an external camera's password) into the log file — which users
routinely attach to public GitHub issues.

These cover the shared helper plus the two funnels that carry subprocess output
into the log.
"""

import asyncio
import time

from backend.app.api.routes.camera import _read_ffmpeg_stderr, _summarize_ffmpeg_stderr
from backend.app.core.logging_filters import redact_url_credentials
from backend.app.services.log_reader import sanitize_log_content

# What ffmpeg actually prints for the camera's local TLS-proxy input. The
# access code sits in the userinfo of the URL it quotes back.
FFMPEG_INPUT_LINE = "Input #0, rtsp, from 'rtsp://bblp:38A4KQ2P@127.0.0.1:48521/streaming/live/1':"


class TestRedactUrlCredentials:
    def test_masks_the_printer_access_code(self):
        result = redact_url_credentials(FFMPEG_INPUT_LINE)
        assert "38A4KQ2P" not in result
        assert result == "Input #0, rtsp, from 'rtsp://bblp:[REDACTED]@127.0.0.1:48521/streaming/live/1':"

    def test_keeps_everything_that_is_not_the_secret(self):
        """Host, port, path and username stay — the line has to remain diagnosable."""
        result = redact_url_credentials("rtsp://admin:hunter2@192.168.1.50:554/stream1")
        assert result == "rtsp://admin:[REDACTED]@192.168.1.50:554/stream1"

    def test_masks_every_scheme_not_just_the_ones_we_use_today(self):
        for url, expected in (
            ("http://user:pw@cam.local/snapshot", "http://user:[REDACTED]@cam.local/snapshot"),
            ("https://user:pw@cam.local/snapshot", "https://user:[REDACTED]@cam.local/snapshot"),
            ("rtsps://bblp:code@printer:322/streaming/live/1", "rtsps://bblp:[REDACTED]@printer:322/streaming/live/1"),
            ("ftp://bblp:code@printer:990/", "ftp://bblp:[REDACTED]@printer:990/"),
        ):
            assert redact_url_credentials(url) == expected

    def test_masks_a_password_containing_an_at_sign(self):
        """The userinfo ends at the LAST @ before the path — no tail may survive."""
        result = redact_url_credentials("rtsp://admin:p@ssw0rd@192.168.1.50/stream")
        assert result == "rtsp://admin:[REDACTED]@192.168.1.50/stream"
        assert "ssw0rd" not in result

    def test_masks_several_urls_in_one_blob(self):
        text = "first rtsp://bblp:AAAAAAAA@10.0.0.1/live then rtsp://bblp:BBBBBBBB@10.0.0.2/live"
        result = redact_url_credentials(text)
        assert "AAAAAAAA" not in result
        assert "BBBBBBBB" not in result
        assert result.count("[REDACTED]") == 2

    def test_never_runs_past_the_authority_into_the_path(self):
        """A later @ in the path must not drag the host into the mask."""
        result = redact_url_credentials("rtsp://bblp:code@10.0.0.1/live/user@example")
        assert result == "rtsp://bblp:[REDACTED]@10.0.0.1/live/user@example"

    def test_leaves_credential_free_text_alone(self):
        for untouched in (
            "Connection refused",
            "rtsp://10.0.0.1:554/stream1",
            "mailto and user@example.com in prose",
            "Starting USB camera stream from /dev/video0 at 10 fps",
        ):
            assert redact_url_credentials(untouched) == untouched

    def test_tolerates_empty_and_none(self):
        assert redact_url_credentials("") == ""
        assert redact_url_credentials(None) is None

    def test_a_long_scheme_like_run_does_not_blow_up(self):
        """The scheme repetition is capped so the match stays linear.

        Unbounded, the engine restarted at every offset of a run of
        scheme-legal characters and consumed to the end each time before
        failing to find ``://`` — quadratic in the length of the line, and
        ffmpeg echoes the operator's camera URL into the subject. An absolute
        timing bound would be flaky, so this pins the growth rate instead:
        doubling the input must not quadruple the work. Measured against the
        unbounded pattern, these two inputs took 550ms and 2187ms (ratio 3.97,
        so the assertion fails); bounded, 2.8ms and 5.4ms (ratio 1.98).
        """
        small = "A" * 32_000 + "://@"
        large = "A" * 64_000 + "://@"

        start = time.perf_counter()
        assert redact_url_credentials(small) == small
        small_elapsed = time.perf_counter() - start

        start = time.perf_counter()
        assert redact_url_credentials(large) == large
        large_elapsed = time.perf_counter() - start

        # Linear would be ~2x. Allow generous slack for a loaded CI box while
        # still failing the ~4x of a quadratic match.
        assert large_elapsed < max(small_elapsed * 3, 0.5)

    def test_a_scheme_longer_than_the_cap_still_gets_its_secret_masked(self):
        """The cap bounds backtracking; it must not create a redaction hole.

        A pseudo-scheme longer than the cap simply matches from a later
        offset, so the password is still replaced.
        """
        result = redact_url_credentials("Z" * 100 + "://user:hunter2@host/path")
        assert "hunter2" not in result
        assert result.endswith("://user:[REDACTED]@host/path")


class TestFfmpegStderrFunnel:
    """`_summarize_ffmpeg_stderr` is the one funnel every stderr log in the
    camera route passes through, so redaction lands there."""

    def test_summary_strips_the_access_code(self):
        stderr = f"{FFMPEG_INPUT_LINE}\n[rtsp @ 0x5] Could not find codec parameters\n"
        result = _summarize_ffmpeg_stderr(stderr)
        assert "38A4KQ2P" not in result
        assert "[REDACTED]" in result
        # The actionable error is untouched.
        assert "Could not find codec parameters" in result

    def test_incremental_reader_strips_the_access_code(self):
        async def run():
            reader = asyncio.StreamReader()
            reader.feed_data(f"{FFMPEG_INPUT_LINE}\nError opening input: Connection refused\n".encode())
            reader.feed_eof()

            class _FakeProcess:
                stderr = reader

            return await _read_ffmpeg_stderr(_FakeProcess())

        result = asyncio.run(run())
        assert result is not None
        assert "38A4KQ2P" not in result
        assert "Connection refused" in result


class TestSupportBundleSanitizerUnchanged:
    """The bundle sanitizer shares the pattern but keeps its own, stricter
    replacement — it drops the username too. Guard against drift."""

    def test_bundle_still_drops_the_whole_userinfo(self):
        result = sanitize_log_content("rtsp://bblp:38A4KQ2P@10.0.0.1/live")
        assert "38A4KQ2P" not in result
        assert "bblp" not in result
        assert "[CREDENTIALS]@" in result
