"""
Tests for the external camera service.

These tests cover pure functions and frame parsing logic.
"""

from unittest.mock import patch

import pytest

JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"


def _make_jpeg(payload: bytes = b"\x00" * 100) -> bytes:
    """Build a synthetic JPEG byte sequence (SOI + payload + EOI)."""
    return JPEG_START + payload + JPEG_END


class _FakeMjpegResponse:
    """Drop-in for aiohttp's response that drives `iter_chunked` from a fixed
    list of byte chunks. Each chunk is yielded once; if the iterator runs out
    the response is treated as closed (which is the realistic behaviour for an
    MJPEG stream the upstream server has finished). An optional `raise_after`
    raises the supplied exception after N chunks to simulate timeout / IO
    failure mid-stream."""

    def __init__(self, chunks, status=200, raise_after=None, raise_exc=None):
        self.status = status
        self._chunks = list(chunks)
        self._raise_after = raise_after
        self._raise_exc = raise_exc
        self.content = self  # the function calls `response.content.iter_chunked(...)`

    def iter_chunked(self, _size):  # noqa: ARG002 — chunk size is informational
        chunks = self._chunks
        raise_after = self._raise_after
        raise_exc = self._raise_exc

        async def _gen():
            for i, chunk in enumerate(chunks):
                if raise_after is not None and i >= raise_after:
                    raise raise_exc
                yield chunk

        return _gen()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class _FakeMjpegSession:
    """Drop-in for aiohttp.ClientSession; `get(url)` returns a pre-baked
    `_FakeMjpegResponse`."""

    def __init__(self, response):
        self._response = response

    def get(self, _url):
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


def _patch_mjpeg_session(response):
    """Patch `aiohttp.ClientSession` inside the external_camera module so the
    real `_capture_mjpeg_frame` runs against our fake stream."""

    def _factory(*_args, **_kwargs):
        return _FakeMjpegSession(response)

    return patch("backend.app.services.external_camera.aiohttp.ClientSession", _factory)


class TestCaptureMjpegFrameWarmupSkip:
    """Regression for #1177. Many MJPEG sources (notably go2rtc) emit a
    warm-up / black frame on the first byte that follows connection accept;
    `_capture_mjpeg_frame` must skip past it and return the second frame.
    Where the stream ends or times out before a second frame ever arrives the
    function falls back to the warm-up frame so callers still get *something*
    — returning None there would regress every code path that consumed the
    pre-fix behaviour (snapshot UX, plate-detection CV, finish photo,
    timelapse, Obico inference)."""

    @pytest.mark.asyncio
    async def test_skips_warmup_frame_returns_second_frame(self):
        # Two frames arriving in two chunks — typical of a steady MJPEG feed.
        # Pre-fix this returned `warm`; post-fix returns `live`.
        from backend.app.services.external_camera import _capture_mjpeg_frame

        warm = _make_jpeg(b"\x10" * 50)  # warm-up — encoder hasn't caught up
        live = _make_jpeg(b"\x20" * 200)  # representative scene
        response = _FakeMjpegResponse(chunks=[warm, live])

        with _patch_mjpeg_session(response):
            frame = await _capture_mjpeg_frame("http://camera.example/stream", timeout=15)

        assert frame == live
        assert frame != warm

    @pytest.mark.asyncio
    async def test_two_frames_in_single_chunk_returns_second(self):
        # High-FPS sources often pack multiple frames into one chunk delivered
        # in a single iteration of `iter_chunked`. The inner while-loop must
        # drain every complete frame from the buffer before reading more.
        from backend.app.services.external_camera import _capture_mjpeg_frame

        warm = _make_jpeg(b"\x10" * 50)
        live = _make_jpeg(b"\x20" * 200)
        response = _FakeMjpegResponse(chunks=[warm + live])

        with _patch_mjpeg_session(response):
            frame = await _capture_mjpeg_frame("http://camera.example/stream", timeout=15)

        assert frame == live

    @pytest.mark.asyncio
    async def test_partial_frame_split_across_chunks_assembles_correctly(self):
        # Realistic chunking: TCP doesn't respect frame boundaries, so a
        # single frame can straddle two chunks. The fix's buffer-trim path
        # must still find the SOI / EOI pair across the boundary.
        from backend.app.services.external_camera import _capture_mjpeg_frame

        warm = _make_jpeg(b"\x10" * 50)
        live = _make_jpeg(b"\x20" * 200)
        # Split `live` mid-payload
        split_at = len(JPEG_START) + 100
        chunks = [warm + live[:split_at], live[split_at:]]
        response = _FakeMjpegResponse(chunks=chunks)

        with _patch_mjpeg_session(response):
            frame = await _capture_mjpeg_frame("http://camera.example/stream", timeout=15)

        assert frame == live

    @pytest.mark.asyncio
    async def test_single_frame_stream_falls_back_to_first_frame(self):
        # Critical no-regression case. A snapshot-style endpoint that emits
        # exactly one frame and closes the connection (or a slow stream that
        # only delivers one frame within the timeout window) must still hand
        # back that one frame — not None. Pre-fix users on these sources got
        # the frame; the warm-up skip would otherwise turn that into None
        # silently.
        from backend.app.services.external_camera import _capture_mjpeg_frame

        only = _make_jpeg(b"\xab" * 80)
        response = _FakeMjpegResponse(chunks=[only])

        with _patch_mjpeg_session(response):
            frame = await _capture_mjpeg_frame("http://camera.example/stream", timeout=15)

        assert frame == only

    @pytest.mark.asyncio
    async def test_timeout_after_first_frame_falls_back_to_first(self):
        # Timeout mid-stream — the warm-up frame has already arrived but the
        # second hasn't. Same fallback: hand back what we have, never None.
        from backend.app.services.external_camera import _capture_mjpeg_frame

        warm = _make_jpeg(b"\x10" * 50)
        response = _FakeMjpegResponse(
            chunks=[warm, b""],  # second yield will raise instead
            raise_after=1,
            raise_exc=TimeoutError(),
        )

        with _patch_mjpeg_session(response):
            frame = await _capture_mjpeg_frame("http://camera.example/stream", timeout=15)

        assert frame == warm

    @pytest.mark.asyncio
    async def test_no_frames_returns_none(self):
        # Server replied 200 but emitted zero JPEG bytes before closing —
        # there's nothing to return, so None is the correct answer.
        from backend.app.services.external_camera import _capture_mjpeg_frame

        response = _FakeMjpegResponse(chunks=[b"\x00\x01\x02\x03"])

        with _patch_mjpeg_session(response):
            frame = await _capture_mjpeg_frame("http://camera.example/stream", timeout=15)

        assert frame is None

    @pytest.mark.asyncio
    async def test_non_200_status_returns_none(self):
        # Invariant: a 4xx/5xx is never a valid frame source.
        from backend.app.services.external_camera import _capture_mjpeg_frame

        response = _FakeMjpegResponse(chunks=[], status=404)

        with _patch_mjpeg_session(response):
            frame = await _capture_mjpeg_frame("http://camera.example/stream", timeout=15)

        assert frame is None


class TestFormatMjpegFrame:
    """Tests for MJPEG frame formatting."""

    def test_format_mjpeg_frame_basic(self):
        """Verify MJPEG frame is formatted correctly with boundary and headers."""
        from backend.app.services.external_camera import _format_mjpeg_frame

        # Minimal JPEG data (just SOI and EOI markers)
        jpeg_data = b"\xff\xd8\xff\xd9"

        result = _format_mjpeg_frame(jpeg_data)

        # Check boundary
        assert result.startswith(b"--frame\r\n")
        # Check content type
        assert b"Content-Type: image/jpeg\r\n" in result
        # Check content length
        assert b"Content-Length: 4\r\n" in result
        # Check frame data is included
        assert jpeg_data in result
        # Check ends with CRLF
        assert result.endswith(b"\r\n")

    def test_format_mjpeg_frame_larger_data(self):
        """Verify content length is correct for larger frames."""
        from backend.app.services.external_camera import _format_mjpeg_frame

        # Simulate a larger JPEG (1000 bytes)
        jpeg_data = b"\xff\xd8" + b"\x00" * 996 + b"\xff\xd9"

        result = _format_mjpeg_frame(jpeg_data)

        assert b"Content-Length: 1000\r\n" in result


class TestGetFfmpegPath:
    """Tests for ffmpeg path detection."""

    def test_get_ffmpeg_path_from_shutil_which(self):
        """Verify ffmpeg found via shutil.which is returned."""
        from backend.app.services.external_camera import get_ffmpeg_path

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            result = get_ffmpeg_path()
            assert result == "/usr/bin/ffmpeg"

    def test_get_ffmpeg_path_fallback_to_common_paths(self):
        """Verify common paths are checked when shutil.which fails."""
        from backend.app.services.external_camera import get_ffmpeg_path

        with patch("shutil.which", return_value=None), patch("pathlib.Path.exists") as mock_exists:
            # First common path exists
            mock_exists.return_value = True
            result = get_ffmpeg_path()
            assert result in ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"]

    def test_get_ffmpeg_path_returns_none_when_not_found(self):
        """Verify None is returned when ffmpeg not found anywhere."""
        from backend.app.services.external_camera import get_ffmpeg_path

        with patch("shutil.which", return_value=None), patch("pathlib.Path.exists", return_value=False):
            result = get_ffmpeg_path()
            assert result is None


class TestJpegFrameExtraction:
    """Tests for JPEG frame extraction from buffer."""

    def test_extract_single_frame_from_buffer(self):
        """Test extracting a complete JPEG frame from buffer."""
        # JPEG markers
        jpeg_start = b"\xff\xd8"
        jpeg_end = b"\xff\xd9"

        # Create a buffer with one complete frame
        frame_content = b"\x00" * 100
        buffer = jpeg_start + frame_content + jpeg_end

        # Find frame boundaries
        start_idx = buffer.find(jpeg_start)
        end_idx = buffer.find(jpeg_end, start_idx + 2)

        assert start_idx == 0
        assert end_idx == 102

        # Extract frame
        frame = buffer[start_idx : end_idx + 2]
        assert frame == buffer
        assert len(frame) == 104

    def test_extract_frame_with_leading_garbage(self):
        """Test extracting frame when buffer has leading garbage data."""
        jpeg_start = b"\xff\xd8"
        jpeg_end = b"\xff\xd9"

        # Buffer with garbage before the JPEG
        garbage = b"\x00\x01\x02\x03"
        frame_content = b"\xff" * 50
        buffer = garbage + jpeg_start + frame_content + jpeg_end

        start_idx = buffer.find(jpeg_start)
        assert start_idx == 4  # After garbage

        end_idx = buffer.find(jpeg_end, start_idx + 2)
        frame = buffer[start_idx : end_idx + 2]

        assert frame.startswith(jpeg_start)
        assert frame.endswith(jpeg_end)
        assert len(frame) == 54  # 2 + 50 + 2

    def test_incomplete_frame_detection(self):
        """Test detection of incomplete frame (no end marker)."""
        jpeg_start = b"\xff\xd8"

        # Incomplete buffer - no end marker
        buffer = jpeg_start + b"\x00" * 100

        start_idx = buffer.find(jpeg_start)
        end_idx = buffer.find(b"\xff\xd9", start_idx + 2)

        assert start_idx == 0
        assert end_idx == -1  # Not found

    def test_multiple_frames_in_buffer(self):
        """Test extracting first frame when buffer contains multiple frames."""
        jpeg_start = b"\xff\xd8"
        jpeg_end = b"\xff\xd9"

        # Two complete frames
        frame1 = jpeg_start + b"\x01" * 10 + jpeg_end
        frame2 = jpeg_start + b"\x02" * 20 + jpeg_end
        buffer = frame1 + frame2

        # Extract first frame
        start_idx = buffer.find(jpeg_start)
        end_idx = buffer.find(jpeg_end, start_idx + 2)
        first_frame = buffer[start_idx : end_idx + 2]

        assert first_frame == frame1
        assert len(first_frame) == 14

        # Remaining buffer should contain second frame
        remaining = buffer[end_idx + 2 :]
        assert remaining == frame2


class TestCameraTypeValidation:
    """Tests for camera type handling."""

    @pytest.mark.asyncio
    async def test_capture_frame_unknown_type_returns_none(self):
        """Verify unknown camera type returns None."""
        from backend.app.services.external_camera import capture_frame

        result = await capture_frame("http://example.com", "unknown_type")
        assert result is None

    @pytest.mark.asyncio
    async def test_capture_frame_valid_types(self):
        """Verify valid camera types are accepted (they may fail but shouldn't error on type)."""
        from backend.app.services.external_camera import capture_frame

        # These will fail to connect but shouldn't raise type errors
        for camera_type in ["mjpeg", "rtsp", "snapshot"]:
            # Use a non-routable IP to fail fast
            result = await capture_frame("http://192.0.2.1/test", camera_type, timeout=1)
            # Should return None (failed connection) not raise exception
            assert result is None


class TestSnapshotUrlOverride:
    """#1177 follow-up. When ``external_camera_snapshot_url`` is set on the
    printer, every single-frame capture (notification thumbnail, finish photo,
    timelapse, plate-detect) must route through the plain HTTP-GET path on the
    snapshot URL instead of opening the live stream and skipping a warm-up
    frame. Sources that expose a dedicated frame endpoint (e.g. go2rtc's
    ``/api/frame.jpeg``) reliably return a clean image — the warm-up dance is
    only required for sources that don't, and bypassing it removes the
    inconsistency the reporter still saw after the warm-up fix landed."""

    @pytest.mark.asyncio
    async def test_snapshot_override_routes_to_snapshot_path(self):
        from unittest.mock import AsyncMock

        with (
            patch(
                "backend.app.services.external_camera._capture_snapshot",
                new=AsyncMock(return_value=b"\xff\xd8snapshot\xff\xd9"),
            ) as mocked_snapshot,
            patch(
                "backend.app.services.external_camera._capture_mjpeg_frame",
                new=AsyncMock(return_value=b"should-not-be-called"),
            ) as mocked_mjpeg,
        ):
            from backend.app.services.external_camera import capture_frame

            result = await capture_frame(
                "http://192.168.1.61:1984/api/stream.mjpeg",
                "mjpeg",
                snapshot_url="http://192.168.1.61:1984/api/frame.jpeg",
            )

        assert result == b"\xff\xd8snapshot\xff\xd9"
        mocked_snapshot.assert_awaited_once()
        # First positional arg is the snapshot URL; the live-stream URL is ignored.
        assert mocked_snapshot.await_args.args[0] == "http://192.168.1.61:1984/api/frame.jpeg"
        mocked_mjpeg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_snapshot_override_routes_to_camera_type_handler(self):
        from unittest.mock import AsyncMock

        with (
            patch(
                "backend.app.services.external_camera._capture_snapshot",
                new=AsyncMock(return_value=b"should-not-be-called"),
            ) as mocked_snapshot,
            patch(
                "backend.app.services.external_camera._capture_mjpeg_frame",
                new=AsyncMock(return_value=b"\xff\xd8live\xff\xd9"),
            ) as mocked_mjpeg,
        ):
            from backend.app.services.external_camera import capture_frame

            result = await capture_frame("http://192.168.1.61:1984/api/stream.mjpeg", "mjpeg")

        assert result == b"\xff\xd8live\xff\xd9"
        mocked_mjpeg.assert_awaited_once()
        mocked_snapshot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_string_snapshot_url_treated_as_unset(self):
        """Falsy snapshot_url (empty string from a cleared input) must NOT
        hijack the live-stream path — the form-cleared input becomes ``None``
        in the DB, but a defence-in-depth empty-string guard means a stale
        config row still uses the live stream rather than firing GET ''."""
        from unittest.mock import AsyncMock

        with (
            patch(
                "backend.app.services.external_camera._capture_snapshot",
                new=AsyncMock(return_value=b"should-not-be-called"),
            ) as mocked_snapshot,
            patch(
                "backend.app.services.external_camera._capture_mjpeg_frame",
                new=AsyncMock(return_value=b"\xff\xd8live\xff\xd9"),
            ) as mocked_mjpeg,
        ):
            from backend.app.services.external_camera import capture_frame

            result = await capture_frame(
                "http://192.168.1.61:1984/api/stream.mjpeg",
                "mjpeg",
                snapshot_url="",
            )

        assert result == b"\xff\xd8live\xff\xd9"
        mocked_mjpeg.assert_awaited_once()
        mocked_snapshot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_snapshot_override_honours_ssrf_guard(self):
        """The override goes through ``_capture_snapshot`` which already
        sanitises the URL — link-local / metadata / blocked-host targets
        return None instead of being fetched."""
        from backend.app.services.external_camera import capture_frame

        result = await capture_frame(
            "http://192.168.1.61:1984/api/stream.mjpeg",
            "mjpeg",
            snapshot_url="http://169.254.169.254/latest/meta-data/",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_snapshot_override_works_for_rtsp_and_usb_camera_types(self):
        """The override is camera-type agnostic: a user with an RTSP or USB
        stream paired with a separate HTTP snapshot endpoint (e.g. go2rtc
        feeding a USB cam, exposing both /api/stream.mjpeg and
        /api/frame.jpeg) gets clean snapshots without spinning up ffmpeg."""
        from unittest.mock import AsyncMock

        for camera_type in ("rtsp", "usb"):
            with (
                patch(
                    "backend.app.services.external_camera._capture_snapshot",
                    new=AsyncMock(return_value=b"\xff\xd8snap\xff\xd9"),
                ) as mocked_snapshot,
                patch(
                    "backend.app.services.external_camera._capture_rtsp_frame",
                    new=AsyncMock(return_value=b"should-not-be-called"),
                ) as mocked_rtsp,
                patch(
                    "backend.app.services.external_camera._capture_usb_frame",
                    new=AsyncMock(return_value=b"should-not-be-called"),
                ) as mocked_usb,
            ):
                from backend.app.services.external_camera import capture_frame

                result = await capture_frame(
                    "rtsp://printer/stream" if camera_type == "rtsp" else "/dev/video0",
                    camera_type,
                    snapshot_url="http://192.168.1.61:1984/api/frame.jpeg",
                )

            assert result == b"\xff\xd8snap\xff\xd9", f"camera_type={camera_type}"
            mocked_snapshot.assert_awaited_once()
            mocked_rtsp.assert_not_awaited()
            mocked_usb.assert_not_awaited()


class TestRtspUrlHandling:
    """Tests for RTSP/RTSPS URL handling."""

    def test_rtsps_url_detection(self):
        """Verify rtsps:// and rtsp:// URL schemes are distinct."""
        url_rtsps = "rtsps://user:pass@192.168.1.1:554/stream"
        url_rtsp = "rtsp://user:pass@192.168.1.1:554/stream"

        assert url_rtsps.startswith("rtsps://")
        assert not url_rtsp.startswith("rtsps://")
        assert url_rtsp.startswith("rtsp://")

    def test_ffmpeg_handles_both_rtsp_and_rtsps(self):
        """Verify ffmpeg command structure handles both URL schemes identically.

        ffmpeg automatically handles TLS for rtsps:// URLs, so no special
        flags are needed - both URL schemes use the same command structure.
        """
        # Both URL types should use the same basic ffmpeg options
        base_cmd = [
            "ffmpeg",
            "-rtsp_transport",
            "tcp",
            "-i",
        ]

        rtsp_url = "rtsp://user:pass@192.168.1.1:554/stream"
        rtsps_url = "rtsps://user:pass@192.168.1.1:554/stream"

        # Command structure is identical for both
        cmd_rtsp = base_cmd + [rtsp_url]
        cmd_rtsps = base_cmd + [rtsps_url]

        # Only the URL differs
        assert cmd_rtsp[:-1] == cmd_rtsps[:-1]
        assert cmd_rtsp[-1] != cmd_rtsps[-1]


class TestUsbCameraHandling:
    """Tests for USB camera support."""

    def test_list_usb_cameras_returns_list(self):
        """Verify list_usb_cameras returns a list (may be empty if no cameras)."""
        from backend.app.services.external_camera import list_usb_cameras

        result = list_usb_cameras()
        assert isinstance(result, list)

    def test_list_usb_cameras_dict_structure(self):
        """Verify each camera entry has expected fields."""
        from backend.app.services.external_camera import list_usb_cameras

        result = list_usb_cameras()
        for camera in result:
            assert "device" in camera
            assert "name" in camera
            assert camera["device"].startswith("/dev/video")

    @pytest.mark.asyncio
    async def test_capture_frame_usb_type_accepted(self):
        """Verify 'usb' camera type is accepted."""
        from backend.app.services.external_camera import capture_frame

        # Non-existent device should fail gracefully
        result = await capture_frame("/dev/video999", "usb", timeout=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_capture_frame_usb_invalid_device_path(self):
        """Verify invalid USB device paths are rejected."""
        from backend.app.services.external_camera import capture_frame

        # Invalid device path (not /dev/video*)
        result = await capture_frame("/dev/sda1", "usb", timeout=1)
        assert result is None

        result = await capture_frame("http://example.com", "usb", timeout=1)
        assert result is None
