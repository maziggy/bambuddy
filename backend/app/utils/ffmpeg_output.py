"""Turning an ffmpeg subprocess's stderr into a log line worth reading (#2968).

ffmpeg opens every run with ~20 lines of version, build and library banner and
prints its diagnosis *last*. Truncating that from the front -- ``stderr[:200]``,
which is what most call sites did -- keeps the banner and throws the diagnosis
away. A reporter's H2D produced twelve of these, and every one of them read

    ffmpeg frame bytes capture failed (code 183): ffmpeg version 7.1.4-0+deb13u1
    Copyright (c) 2000-2026 the FFmpeg developers  built with gcc 14 (Debian
    14.2.0-19)  configuration: --prefix=/usr --extra-version=0+deb13u1 --toolch

-- 200 characters that are identical on every install and say nothing about why
the capture failed. The exit code was the only usable byte in the whole line.

The banner-stripping summariser this module holds was written for #925 and
lived as a private helper in ``api/routes/camera.py``, where the streaming
endpoint used it. Ten other places log ffmpeg or ffprobe stderr -- snapshot
capture, last-frame extraction, the layer-timelapse stitch, the archive's MP4
conversion, external USB and RTSP capture and streaming, and timelapse
post-processing. Seven of them truncated from the front, two logged the whole
banner, and one already kept the tail. They all come here now, so they cannot
drift again.

Redaction is part of the summary rather than each caller's job. ffmpeg echoes
its input URL back in the ``Input #0`` line, so a camera password or a printer
access code reaches stderr on any failure; seven of those ten logged it
unmasked. A helper that redacts is one that cannot be called wrong.

Kept as a leaf module -- stdlib plus :mod:`core.logging_filters`, which is
itself stdlib-only -- so the services and the route can all reach it without
pulling a startup graph behind them.
"""

from __future__ import annotations

from backend.app.core.logging_filters import redact_url_credentials

# What ffmpeg prints before it has anything to say. Every line of the banner is
# either the version line or an indented continuation, and a real diagnostic is
# never indented this way, so the match is on the exact prefixes rather than on
# indentation alone -- ``  Duration: ...`` and ``    Stream #0:0 ...`` are
# indented too and are worth keeping.
_BANNER_PREFIXES = (
    "ffmpeg version ",
    "ffprobe version ",
    "  built with ",
    "  configuration:",
    "  libavutil ",
    "  libavcodec ",
    "  libavformat ",
    "  libavdevice ",
    "  libavfilter ",
    "  libswscale ",
    "  libswresample ",
    "  libpostproc ",
)

# How much of the tail to keep. ffmpeg's diagnosis is the last thing it writes,
# and ten lines is enough to carry the error plus the input analysis that
# explains it without letting a chatty decoder rotate the log file.
_MAX_LINES = 10

# And a ceiling on the whole thing. Ten lines is only a bound on the log record
# if the lines are a sane length, and ffmpeg quotes what the peer sent it back
# at us -- a printer's RTSP response is not something Bambuddy controls. Well
# above any real diagnosis, so this only ever trims a line that was already not
# going to be read.
_MAX_CHARACTERS = 2000

# What to log when the summary is empty. A failure whose stderr held nothing but
# the banner still deserves a line saying so -- ``failed: `` with an empty tail
# reads like a truncation bug rather than a printer that closed the connection.
NO_FFMPEG_OUTPUT = "no diagnostic output"


def summarize_ffmpeg_stderr(text: str | bytes | None) -> str:
    """Strip ffmpeg's boilerplate banner and keep the last lines that matter.

    Accepts raw ``bytes`` as well as ``str`` and decodes with ``errors=
    "replace"``: ffmpeg copies fragments of the stream into its error messages,
    so a bare ``.decode()`` at the call site can raise ``UnicodeDecodeError``
    while reporting an unrelated failure. Losing the diagnosis to a second
    exception is the one outcome worse than logging the banner.

    Returns ``""`` when there is nothing left after the banner, which is the
    signal the streaming endpoint uses to stay quiet. One-shot callers that log
    unconditionally should fall back to :data:`NO_FFMPEG_OUTPUT`.
    """
    if not text:
        return ""
    if isinstance(text, (bytes, bytearray)):
        text = text.decode(errors="replace")
    # Redaction runs on the whole string before anything is dropped: a
    # credentialed URL that straddles the cut would otherwise leave its tail in
    # the log with no ``@`` left for the pattern to anchor on.
    text = redact_url_credentials(text) or ""
    meaningful = [line for line in text.splitlines() if line.strip() and not line.startswith(_BANNER_PREFIXES)]
    summary = "\n".join(meaningful[-_MAX_LINES:])
    if len(summary) > _MAX_CHARACTERS:
        # From the end, for the same reason the whole module exists.
        summary = "..." + summary[-_MAX_CHARACTERS:]
    return summary
