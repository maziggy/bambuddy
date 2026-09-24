"""The external live view must not cap ffmpeg's stream probing (#3082).

An external camera passed the connection test, played in VLC, and showed a
black live view that gave up after a few seconds. The two RTSP paths in
``external_camera`` were not asking ffmpeg for the same thing: the one-shot
``_capture_rtsp_frame`` passed no probe settings and got ffmpeg's defaults,
while ``_stream_rtsp`` hard-coded ``-probesize 32 -analyzeduration 0``.

32 bytes is enough for a camera that puts SPS/PPS in its SDP. It is not enough
for one that sends them in-band a moment later — a WebRTC source republished
through go2rtc, in @M1XZG's report — and without them ffmpeg never starts an
H.264 decoder, so the stream yields no frames at all. Those settings were never
chosen for external cameras: they came in with the P2S TLS proxy (#661) as
fast-start tuning for the *printer* camera path, where the source is a known
Bambu model, and were copied across to this one in the same commit. The printer
path keeps its per-model tuning in ``camera_profiles.py``; this path has no
model to tune against and belongs on the defaults.
"""

import pytest

from backend.app.services.external_camera import _capture_rtsp_frame, _stream_rtsp
from backend.tests._fixtures.external_camera import fake_ffmpeg, spawn_spy

CAMERA = "rtsp://admin:hunter2@192.168.1.50:554/live"


async def _stream_argv() -> tuple[str, ...]:
    with fake_ffmpeg(), spawn_spy(returncode=None) as spawn:
        [frame async for frame in _stream_rtsp(CAMERA, fps=5)]
    return spawn.await_args.args


async def _capture_argv() -> tuple[str, ...]:
    with fake_ffmpeg(), spawn_spy() as spawn:
        await _capture_rtsp_frame(CAMERA, timeout=5)
    return spawn.await_args.args


class TestTheLiveStreamDoesNotCapProbing:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("flag", ["-probesize", "-analyzeduration"])
    async def test_no_probe_ceiling_is_imposed(self, flag):
        """Re-adding either of these is the regression, and it is silent.

        Nothing fails, no error is logged, the connection test still passes —
        the live view just stops producing frames on the subset of cameras
        that need longer than a 32-byte probe to describe themselves.
        """
        argv = await _stream_argv()
        assert flag not in argv, f"{flag} is back in the external live stream: {argv!r}"

    @pytest.mark.asyncio
    async def test_the_low_latency_flags_are_kept(self):
        """The probe cap went; the rest of the fast-start tuning did not.

        ``-fflags nobuffer`` and ``-flags low_delay`` ask ffmpeg not to sit on
        frames it already has, which is a different question from how long it
        may look before it has any. @M1XZG re-ran the A/B with both retained
        and the stream still came up, so latency is no reason to reach for
        ``-probesize`` again.
        """
        argv = await _stream_argv()
        assert argv[argv.index("-fflags") + 1] == "nobuffer"
        assert argv[argv.index("-flags") + 1] == "low_delay"

    @pytest.mark.asyncio
    async def test_both_rtsp_paths_probe_alike(self):
        """The asymmetry itself is the bug, whichever way it is reintroduced.

        A camera that answers the test button has demonstrated nothing about
        the live view unless both paths ask ffmpeg to look at the stream the
        same way.
        """
        stream, capture = await _stream_argv(), await _capture_argv()
        probe_flags = ("-probesize", "-analyzeduration")
        assert [f for f in probe_flags if f in stream] == [f for f in probe_flags if f in capture]
