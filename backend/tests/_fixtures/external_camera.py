"""Stand-ins for the ffmpeg subprocess the external-camera paths spawn.

Both RTSP paths in ``backend.app.services.external_camera`` build an argv and
hand it to ``asyncio.create_subprocess_exec``. Tests that care about *what we
asked ffmpeg to do* — the SSRF guards, the probe settings — need to see that
argv without an ffmpeg binary being involved, so these patch the lookup and the
spawn and record the call.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# What ffmpeg logs once an RTSP input is open; the RTSP paths read no frame
# before it.
INPUT_OPEN_LOG = b"Input #0, rtsp, from 'rtsp://192.168.1.50:554/live':\n"


def stream_of(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


def fake_ffmpeg():
    """Pretend ffmpeg is installed, so the paths get as far as building argv."""
    return patch("backend.app.services.external_camera.get_ffmpeg_path", return_value="/usr/bin/ffmpeg")


def spawn_spy(returncode: int | None = 0, stdout: bytes = b"\xff\xd8" + b"\x00" * 200, stderr: bytes = INPUT_OPEN_LOG):
    """Stand in for the ffmpeg subprocess, recording the argv it was handed.

    The streaming path reads until EOF, so stdout.read returns b"" and the
    generator finishes immediately — these tests are about whether ffmpeg was
    launched and with what, not about frame extraction.
    """
    process = MagicMock()
    process.returncode = returncode
    process.communicate = AsyncMock(return_value=(stdout, b""))
    process.stdout.read = AsyncMock(return_value=b"")
    process.stderr = stream_of(stderr)
    process.wait = AsyncMock(return_value=returncode)
    process.kill = MagicMock()
    process.terminate = MagicMock()
    return patch(
        "backend.app.services.external_camera.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=process),
    )
