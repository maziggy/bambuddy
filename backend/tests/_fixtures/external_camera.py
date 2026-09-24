"""Stand-ins for the ffmpeg subprocess the external-camera paths spawn.

Both RTSP paths in ``backend.app.services.external_camera`` build an argv and
hand it to ``asyncio.create_subprocess_exec``. Tests that care about *what we
asked ffmpeg to do* — the SSRF guards, the probe settings — need to see that
argv without an ffmpeg binary being involved, so these patch the lookup and the
spawn and record the call.
"""

from unittest.mock import AsyncMock, MagicMock, patch


def fake_ffmpeg():
    """Pretend ffmpeg is installed, so the paths get as far as building argv."""
    return patch("backend.app.services.external_camera.get_ffmpeg_path", return_value="/usr/bin/ffmpeg")


def spawn_spy(returncode: int | None = 0, stdout: bytes = b"\xff\xd8" + b"\x00" * 200):
    """Stand in for the ffmpeg subprocess, recording the argv it was handed.

    The streaming path reads until EOF, so stdout.read returns b"" and the
    generator finishes immediately — these tests are about whether ffmpeg was
    launched and with what, not about frame extraction.
    """
    process = MagicMock()
    process.returncode = returncode
    process.communicate = AsyncMock(return_value=(stdout, b""))
    process.stdout.read = AsyncMock(return_value=b"")
    process.stderr.read = AsyncMock(return_value=b"")
    process.wait = AsyncMock(return_value=returncode)
    process.kill = MagicMock()
    process.terminate = MagicMock()
    return patch(
        "backend.app.services.external_camera.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=process),
    )
