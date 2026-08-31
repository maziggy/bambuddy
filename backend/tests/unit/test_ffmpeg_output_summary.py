"""ffmpeg's diagnosis survives the log line, and its banner does not (#2968).

ffmpeg prints ~20 lines of version and build banner first and its actual error
last, so the ``stderr[:200]`` most call sites used kept the banner and dropped
the error. The reporter's H2D logged twelve capture failures that way; every
one of them was the same 200 characters of ``--prefix=/usr --extra-version=``
and none of them said why the capture failed.

#925 already solved this for the camera streaming endpoint. These tests cover
the shared module the other seven call sites now go through, and the two things
that were only ever true of the private copy: it takes bytes, and it masks
credentials for callers that never did.
"""

import inspect

import pytest

from backend.app.utils.ffmpeg_output import NO_FFMPEG_OUTPUT, summarize_ffmpeg_stderr

# Verbatim from the reporter's log, trimmed to the width the old truncation
# allowed through. The point of the fixture is that 200 characters of it carry
# no information at all.
_REAL_BANNER = """ffmpeg version 7.1.4-0+deb13u1 Copyright (c) 2000-2026 the FFmpeg developers
  built with gcc 14 (Debian 14.2.0-19)
  configuration: --prefix=/usr --extra-version=0+deb13u1 --toolchain=hardened --enable-gpl
  libavutil      59. 39.100 / 59. 39.100
  libavcodec     61. 19.101 / 61. 19.101
  libavformat    61.  7.100 / 61.  7.100
  libavdevice    61.  3.100 / 61.  3.100
  libavfilter    10.  4.100 / 10.  4.100
  libswscale      8.  3.100 /  8.  3.100
  libswresample   5.  3.100 /  5.  3.100
  libpostproc    58.  3.100 / 58.  3.100
"""


class TestTheDiagnosisSurvives:
    def test_the_error_is_kept_and_the_banner_is_not(self):
        """The whole point: the last line, not the first 200 characters."""
        stderr = _REAL_BANNER + "[rtsp @ 0x5f] method DESCRIBE failed: 401 Unauthorized\n"

        result = summarize_ffmpeg_stderr(stderr)

        assert "method DESCRIBE failed: 401 Unauthorized" in result
        assert "ffmpeg version" not in result
        assert "--prefix=/usr" not in result

    def test_the_old_truncation_would_have_kept_none_of_it(self):
        """Guards the claim the fix rests on rather than asserting it in prose:
        200 characters from the front of a real failure is banner only."""
        stderr = _REAL_BANNER + "[rtsp @ 0x5f] method DESCRIBE failed: 401 Unauthorized\n"

        assert "DESCRIBE" not in stderr[:200]

    def test_input_analysis_is_kept(self):
        """Indented, but not banner. ``Duration:`` and ``Stream #0:0`` explain
        the error above them and are the reason the match is on exact prefixes
        rather than on leading whitespace."""
        stderr = _REAL_BANNER + (
            "Input #0, rtsp, from 'rtsp://192.0.2.1:322/streaming/live/1':\n"
            "  Duration: N/A, start: 0.000000, bitrate: N/A\n"
            "    Stream #0:0: Video: h264, yuv420p, 1920x1080\n"
            "Output file is empty, nothing was encoded\n"
        )

        result = summarize_ffmpeg_stderr(stderr)

        assert "Duration: N/A" in result
        assert "Stream #0:0: Video: h264" in result
        assert "Output file is empty" in result

    def test_only_the_last_lines_are_kept(self):
        """A chatty decoder must not rotate the log file on one failure."""
        stderr = _REAL_BANNER + "\n".join(f"error line {i}" for i in range(40))

        lines = summarize_ffmpeg_stderr(stderr).splitlines()

        assert len(lines) == 10
        assert lines[-1] == "error line 39"

    def test_a_banner_only_failure_says_so(self):
        """Empty, so the caller substitutes a phrase. ``failed: `` with nothing
        after it reads like a truncation bug rather than a silent printer."""
        assert summarize_ffmpeg_stderr(_REAL_BANNER) == ""
        assert (summarize_ffmpeg_stderr(_REAL_BANNER) or NO_FFMPEG_OUTPUT) == NO_FFMPEG_OUTPUT


class TestWhatTheCallSitesUsedToGetWrong:
    def test_bytes_are_accepted(self):
        """Every call site held bytes and decoded them itself."""
        assert "Connection refused" in summarize_ffmpeg_stderr(b"rtsp://192.0.2.1: Connection refused\n")

    def test_undecodable_bytes_do_not_raise(self):
        """ffmpeg copies stream fragments into its messages, so a bare
        ``.decode()`` could raise UnicodeDecodeError while reporting an
        unrelated failure -- losing the diagnosis to a second exception."""
        result = summarize_ffmpeg_stderr(b"\xff\xfe broken input\nInvalid data found\n")

        assert "Invalid data found" in result

    def test_the_access_code_is_masked(self):
        """ffmpeg echoes its input URL back, and four of the call sites logged
        it unmasked. The mask is part of the summary so it cannot be skipped."""
        stderr = b"Error opening input file rtsp://bblp:12345678@192.0.2.1:322/streaming/live/1.\n"

        result = summarize_ffmpeg_stderr(stderr)

        assert "12345678" not in result
        assert "[REDACTED]" in result
        # Host and user survive, or the line stops being useful for diagnosis.
        assert "192.0.2.1:322" in result
        assert "bblp" in result

    def test_a_credential_masked_before_the_cut_not_after(self):
        """Truncating first would leave a URL with no ``@`` for the pattern to
        anchor on, and the secret in the log."""
        stderr = "\n".join(f"noise {i}" for i in range(30))
        stderr += "\nOpening rtsp://user:hunter2@192.0.2.1:322/live and 40 more characters of tail\n"

        result = summarize_ffmpeg_stderr(stderr)

        assert "hunter2" not in result

    @pytest.mark.parametrize("empty", ["", None, b""])
    def test_nothing_in_nothing_out(self, empty):
        assert summarize_ffmpeg_stderr(empty) == ""

    def test_a_single_enormous_line_is_bounded(self):
        """Ten lines only bounds the record if the lines are sane, and ffmpeg
        quotes back what the peer sent it. The tail is what is kept."""
        stderr = _REAL_BANNER + "x" * 50_000 + " Connection refused\n"

        result = summarize_ffmpeg_stderr(stderr)

        assert len(result) < 2_100
        assert result.endswith("Connection refused")
        assert result.startswith("...")

    def test_an_ordinary_diagnosis_is_never_trimmed(self):
        """The ceiling must not be reachable by real ffmpeg output."""
        stderr = _REAL_BANNER + "\n".join(f"[rtsp @ 0x5f] error line {i}" for i in range(10))

        assert not summarize_ffmpeg_stderr(stderr).startswith("...")


class TestEveryCallSiteGoesThroughIt:
    """The defect was seven copies of the same truncation, not one bad line.

    Asserted against the source because the alternative -- driving all seven
    subprocesses -- tests ffmpeg, and because the failure mode being guarded is
    somebody adding an eighth.
    """

    @pytest.mark.parametrize(
        "module_path",
        [
            "backend.app.services.camera",
            "backend.app.services.external_camera",
            "backend.app.services.layer_timelapse",
            "backend.app.services.timelapse_processor",
            "backend.app.services.archive",
            "backend.app.api.routes.camera",
        ],
    )
    def test_no_module_truncates_stderr_by_hand(self, module_path):
        import importlib

        source = inspect.getsource(importlib.import_module(module_path))

        for lineno, raw in enumerate(source.splitlines(), 1):
            # Comments discuss the defect by name -- this file's own fix notes
            # do -- so only what executes is checked.
            line = raw.split("#", 1)[0]
            if "stderr" not in line:
                continue
            assert "stderr.decode()[:" not in line, f"{module_path}:{lineno} truncates stderr from the front"
            assert "stderr_text[:" not in line, f"{module_path}:{lineno} truncates stderr from the front"
            assert 'stderr.decode(errors="replace")[:' not in line, (
                f"{module_path}:{lineno} truncates stderr from the front"
            )
            # A bare decode is the other half of the defect: it can raise
            # UnicodeDecodeError while reporting an unrelated failure, and it
            # leaves the input URL's credentials unmasked.
            assert "stderr.decode()" not in line, f"{module_path}:{lineno} decodes stderr by hand"
