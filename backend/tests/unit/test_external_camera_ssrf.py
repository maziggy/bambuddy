"""The RTSP camera paths must not become a request generator for arbitrary hosts.

`_sanitize_camera_url` is the SSRF boundary for user-configured camera URLs. It
was applied to the MJPEG and snapshot paths but not to the two RTSP ones, which
handed the URL to `ffmpeg -i` unchecked — and ffmpeg's `-i` speaks http, tcp,
file and everything else it was built with, so `camera_type=rtsp` was a way to
name any destination and any protocol.

Wiring the guard in is only half of it. The guard rebuilt URLs from
`parsed.hostname`, which drops credentials and unbrackets IPv6 literals, and it
recognised loopback by comparing against four spellings of it. So these tests
pin three things at once: the RTSP paths refuse what they should, the guard
recognises a destination however it is written, and a real camera — which
usually means an authenticated one — still works.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.external_camera import (
    _blocked_host_reason,
    _capture_rtsp_frame,
    _safe_usb_device_path,
    _sanitize_camera_url,
    _stream_rtsp,
)

RTSP_SCHEMES = ("rtsp", "rtsps")
HTTP_SCHEMES = ("http", "https")


class TestTheHostsWeRefuse:
    """Loopback, the unspecified address and link-local, however they are spelled."""

    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",
            "127.0.0.2",  # the whole 127/8 range, not just .1
            "127.1",  # short form
            "2130706433",  # decimal
            "0177.0.0.1",  # octal
            "0x7f.0.0.1",  # hex
            "[::1]",
            "[::ffff:127.0.0.1]",  # loopback wearing an IPv6 spelling
            "localhost",
            "sub.localhost",
        ],
    )
    def test_loopback_is_refused(self, host):
        assert _sanitize_camera_url(f"rtsp://{host}:554/live", RTSP_SCHEMES) is None

    @pytest.mark.parametrize("host", ["0.0.0.0", "[::]"])  # nosec B104
    def test_the_unspecified_address_is_refused(self, host):
        assert _sanitize_camera_url(f"rtsp://{host}:554/live", RTSP_SCHEMES) is None

    @pytest.mark.parametrize(
        "host",
        [
            "169.254.169.254",  # AWS/GCP/Azure metadata
            "169.254.1.1",  # the rest of the range, not just the metadata IP
            "[fe80::1]",
            "metadata.google.internal",
            "metadata.google",
        ],
    )
    def test_link_local_and_metadata_are_refused(self, host):
        assert _sanitize_camera_url(f"rtsp://{host}/live", RTSP_SCHEMES) is None

    def test_the_reason_is_reported_for_logging(self):
        assert _blocked_host_reason("2130706433") == "loopback"
        assert _blocked_host_reason("169.254.169.254") is not None
        assert _blocked_host_reason("192.168.1.50") is None


class TestTheCamerasWeAllow:
    """LAN is allowed on purpose — that is where cameras are."""

    @pytest.mark.parametrize(
        "url",
        [
            "rtsp://192.168.1.50:554/live",
            "rtsp://10.0.0.5/stream1",
            "rtsp://172.16.4.9:8554/cam",
            "rtsp://[fd00::1]:554/live",  # unique-local IPv6
            "rtsp://cam.lan/live",
            "rtsps://camera.example.com:322/stream",
        ],
    )
    def test_a_camera_url_survives(self, url):
        assert _sanitize_camera_url(url, RTSP_SCHEMES) is not None

    def test_a_hostname_is_not_resolved(self):
        """A name that would resolve to loopback still passes.

        Not an oversight: aiohttp and ffmpeg resolve independently afterwards,
        so a lookup here decides nothing (DNS rebinding) while costing a DNS
        round trip on every capture. Pinned so the omission stays deliberate.
        """
        assert _sanitize_camera_url("rtsp://localtest.me/live", RTSP_SCHEMES) is not None


class TestWhatTheGuardMustNotDestroy:
    """Most RTSP cameras carry their login in the URL. Stripping it would turn
    every one of them into an authentication failure — a worse outage than the
    hole being closed."""

    def test_credentials_survive(self):
        url = "rtsp://admin:hunter2@192.168.1.50:554/live"
        assert _sanitize_camera_url(url, RTSP_SCHEMES) == url

    def test_percent_encoded_credentials_survive_byte_for_byte(self):
        """urlparse's .username/.password are already decoded, so rebuilding
        from them would corrupt any password containing an @ or a :."""
        url = "rtsp://ad%40min:p%3Ass%40word@192.168.1.50:554/live"
        assert _sanitize_camera_url(url, RTSP_SCHEMES) == url

    def test_an_ipv6_literal_keeps_its_brackets(self):
        """Without them the result is not a URL any client can parse."""
        assert _sanitize_camera_url("rtsp://[fd00::1]:554/live", RTSP_SCHEMES) == "rtsp://[fd00::1]:554/live"

    def test_http_cameras_keep_their_basic_auth_too(self):
        url = "http://admin:hunter2@192.168.1.50/stream.mjpg"
        assert _sanitize_camera_url(url, HTTP_SCHEMES) == url

    def test_port_query_and_fragment_survive(self):
        url = "rtsp://192.168.1.50:8554/live?channel=2&subtype=1#frag"
        assert _sanitize_camera_url(url, RTSP_SCHEMES) == url


class TestSchemeAllowlist:
    """What keeps an ffmpeg input a camera fetch rather than a fetch."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://192.168.1.50:8080/internal",
            "https://192.168.1.50/internal",
            "tcp://192.168.1.50:22",
            "file:///etc/passwd",
            "concat:/etc/passwd",
            "udp://192.168.1.50:1234",
            "ftp://192.168.1.50/x",
        ],
    )
    def test_only_rtsp_reaches_the_rtsp_paths(self, url):
        assert _sanitize_camera_url(url, RTSP_SCHEMES) is None

    def test_rtsp_does_not_reach_the_http_paths(self):
        assert _sanitize_camera_url("rtsp://192.168.1.50/live", HTTP_SCHEMES) is None

    @pytest.mark.parametrize("url", ["", "not a url", "rtsp://", "://192.168.1.50/x"])
    def test_malformed_input_is_refused(self, url):
        assert _sanitize_camera_url(url, RTSP_SCHEMES) is None


def _fake_ffmpeg():
    return patch("backend.app.services.external_camera.get_ffmpeg_path", return_value="/usr/bin/ffmpeg")


def _spawn_spy(returncode: int | None = 0, stdout: bytes = b"\xff\xd8" + b"\x00" * 200):
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


class TestRtspCaptureRefusesUnsafeUrls:
    """`_capture_rtsp_frame` — the one-shot path behind the test-connection
    endpoint, which takes url and camera_type straight off the query string."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8080/internal-service",  # the reported PoC
            "http://192.168.1.100:8080/any-image.jpg",
            "file:///etc/passwd",
            "rtsp://127.0.0.1:554/live",
            "rtsp://2130706433:554/live",
            "rtsp://169.254.169.254/live",
        ],
    )
    async def test_no_process_is_spawned(self, url):
        with _fake_ffmpeg(), _spawn_spy() as spawn:
            assert await _capture_rtsp_frame(url, timeout=5) is None
        spawn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_real_camera_still_captures(self):
        with _fake_ffmpeg(), _spawn_spy() as spawn:
            frame = await _capture_rtsp_frame("rtsp://admin:hunter2@192.168.1.50:554/live", timeout=5)

        assert frame is not None
        cmd = spawn.await_args.args
        assert "rtsp://admin:hunter2@192.168.1.50:554/live" in cmd, (
            "the camera's credentials must reach ffmpeg or every authenticated camera breaks"
        )

    @pytest.mark.asyncio
    async def test_ffmpeg_is_confined_to_rtsp_protocols(self):
        """Belt and braces behind the scheme check: a stream that references
        something outside itself must not be able to pull it in."""
        with _fake_ffmpeg(), _spawn_spy() as spawn:
            await _capture_rtsp_frame("rtsp://192.168.1.50:554/live", timeout=5)

        cmd = spawn.await_args.args
        whitelist = cmd[cmd.index("-protocol_whitelist") + 1].split(",")
        assert "rtsp" in whitelist
        assert "file" not in whitelist
        assert "http" not in whitelist


class TestRtspStreamRefusesUnsafeUrls:
    """`_stream_rtsp` — the live-view path, and the one the report missed."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8080/internal-service",
            "rtsp://127.0.0.1:554/live",
            "rtsp://[::ffff:127.0.0.1]:554/live",
            "file:///etc/passwd",
        ],
    )
    async def test_no_process_is_spawned(self, url):
        with _fake_ffmpeg(), _spawn_spy() as spawn:
            frames = [frame async for frame in _stream_rtsp(url, fps=5)]

        assert frames == []
        spawn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_real_camera_still_reaches_ffmpeg(self):
        with _fake_ffmpeg(), _spawn_spy(returncode=None) as spawn:
            [frame async for frame in _stream_rtsp("rtsp://admin:hunter2@192.168.1.50:554/live", fps=5)]

        spawn.assert_awaited_once()
        cmd = spawn.await_args.args
        assert "rtsp://admin:hunter2@192.168.1.50:554/live" in cmd
        assert "-protocol_whitelist" in cmd


class TestUsbDevicePaths:
    """The USB paths take a device path from the same request field, and the
    streaming one used to check only that it started with /dev/video."""

    @pytest.mark.parametrize(
        "device",
        [
            "/dev/video/../../etc/passwd",
            "/dev/videos/../../etc/shadow",
            "/dev/video0; rm -rf /",
            "/etc/passwd",
            "/dev/video100",  # three digits is not a device number
            "",
        ],
    )
    def test_a_path_that_is_not_a_device_node_is_refused(self, device):
        assert _safe_usb_device_path(device) is None

    def test_a_missing_device_is_refused(self):
        """Existence is part of the check — ffmpeg must never be pointed at a
        path just because it is shaped like one."""
        with patch("backend.app.services.external_camera.Path") as path_cls:
            path_cls.return_value.exists.return_value = False
            assert _safe_usb_device_path("/dev/video0") is None

    def test_the_path_is_rebuilt_from_the_device_number(self):
        with patch("backend.app.services.external_camera.Path") as path_cls:
            path_cls.return_value.exists.return_value = True
            path_cls.return_value.__str__.return_value = "/dev/video7"
            assert _safe_usb_device_path("/dev/video7") == "/dev/video7"
        path_cls.assert_called_once_with("/dev/video7")
