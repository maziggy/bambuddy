"""External-camera captures must reuse the live view's frame (#2707).

A USB camera allows exactly one V4L2 handle, so a one-shot capture taken while
somebody is watching the live view doesn't degrade — it fails. The reporter
measured 0 of 87 and 0 of 105 layer-timelapse captures on prints watched from
start to finish, and finish-photo notifications going out with no image.

The guards for the built-in camera (#1348, #1271) were never extended to the
external paths, and the deeper reason they couldn't be: ``_last_frames`` was
only ever populated by the built-in paths. ``generate_mjpeg_stream`` yields
multipart-wrapped chunks, so the route layer had no way to recover the JPEG —
hence the ``on_frame`` callback, and hence a guard alone would have found an
empty buffer and skipped every time.

These tests cover the plumbing (raw frames reach the callback) and each consumer
that used to compete: layer timelapse, Obico polling, and plate detection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes import camera
from backend.app.services import external_camera, layer_timelapse
from backend.app.services.obico_detection import ObicoDetectionService

pytestmark = pytest.mark.asyncio

LIVE_FRAME = b"\xff\xd8live-viewer-frame\xff\xd9"
FRESH_FRAME = b"\xff\xd8fresh-capture\xff\xd9"
PRINTER_ID = 9310


@pytest.fixture(autouse=True)
def _clean_registries():
    def _purge():
        for sid in [k for k in camera._active_streams if k.startswith(f"{PRINTER_ID}-")]:
            camera._active_streams.pop(sid, None)
        camera._last_frames.pop(PRINTER_ID, None)
        camera._last_frame_times.pop(PRINTER_ID, None)
        camera._stream_start_times.pop(PRINTER_ID, None)

    _purge()
    yield
    _purge()


def _attach_viewer(frame: bytes | None = LIVE_FRAME) -> None:
    """Register a live external stream, as the stream route does."""
    camera._active_streams[f"{PRINTER_ID}-ext-deadbeef"] = object()
    if frame is not None:
        camera._last_frames[PRINTER_ID] = frame


# ---------------------------------------------------------------------------
# live_frame_for_capture — the shared decision
# ---------------------------------------------------------------------------


async def test_no_viewer_means_capture_normally():
    defer, frame = camera.live_frame_for_capture(PRINTER_ID)

    assert defer is False
    assert frame is None


async def test_viewer_with_a_buffered_frame_is_reused():
    _attach_viewer()

    defer, frame = camera.live_frame_for_capture(PRINTER_ID)

    assert defer is True
    assert frame == LIVE_FRAME


async def test_viewer_with_an_empty_buffer_means_skip_not_capture():
    """#1348: competing for the device is worse than missing one frame."""
    _attach_viewer(frame=None)

    defer, frame = camera.live_frame_for_capture(PRINTER_ID)

    assert defer is True
    assert frame is None


# ---------------------------------------------------------------------------
# on_frame plumbing — without this the buffer is always empty
# ---------------------------------------------------------------------------


async def test_on_frame_receives_the_raw_jpeg_not_the_multipart_chunk():
    """The consumers want a JPEG; the stream yields multipart. Hence a callback."""
    captured: list[bytes] = []

    async def _fake_usb(_url, _fps, on_process=None):
        yield FRESH_FRAME

    with patch.object(external_camera, "_stream_usb", _fake_usb):
        chunks = [
            chunk
            async for chunk in external_camera.generate_mjpeg_stream(
                "/dev/video0", "usb", fps=15, on_frame=captured.append
            )
        ]

    assert captured == [FRESH_FRAME], "callback did not get the raw frame"
    assert b"--frame" in chunks[0], "wire format should still be multipart"
    assert b"--frame" not in captured[0]


async def test_a_raising_on_frame_callback_cannot_break_the_stream():
    """Buffering is a side effect; it must never take the live view down."""

    async def _fake_usb(_url, _fps, on_process=None):
        yield FRESH_FRAME
        yield FRESH_FRAME

    def _boom(_frame: bytes) -> None:
        raise RuntimeError("buffering blew up")

    with patch.object(external_camera, "_stream_usb", _fake_usb):
        chunks = [
            chunk async for chunk in external_camera.generate_mjpeg_stream("/dev/video0", "usb", fps=15, on_frame=_boom)
        ]

    assert len(chunks) == 2, "stream stopped because the callback raised"


# ---------------------------------------------------------------------------
# Layer timelapse — the 0-of-87 case
# ---------------------------------------------------------------------------


def _session(tmp_path) -> layer_timelapse.TimelapseSession:
    with patch.object(layer_timelapse.settings, "base_dir", tmp_path):
        return layer_timelapse.TimelapseSession(
            printer_id=PRINTER_ID,
            archive_id=None,
            camera_url="/dev/video0",
            camera_type="usb",
        )


async def test_timelapse_uses_the_live_frame_instead_of_competing(tmp_path):
    session = _session(tmp_path)
    _attach_viewer()

    with patch.object(layer_timelapse, "capture_frame", new=AsyncMock(return_value=FRESH_FRAME)) as mock_capture:
        captured = await session.capture_layer(1)

    assert captured is True, "layer capture failed with a viewer attached"
    # Would have opened a competing handle on a single-reader device.
    mock_capture.assert_not_called()
    written = sorted(session.frames_dir.glob("layer_*.jpg"))
    assert len(written) == 1
    assert written[0].read_bytes() == LIVE_FRAME


async def test_timelapse_skips_a_layer_rather_than_competing_on_an_empty_buffer(tmp_path):
    session = _session(tmp_path)
    _attach_viewer(frame=None)

    with patch.object(layer_timelapse, "capture_frame", new=AsyncMock(return_value=FRESH_FRAME)) as mock_capture:
        captured = await session.capture_layer(1)

    assert captured is False
    mock_capture.assert_not_called()
    assert sorted(session.frames_dir.glob("layer_*.jpg")) == []


async def test_timelapse_captures_normally_with_no_viewer(tmp_path):
    """The unwatched path must be untouched — this is the common case."""
    session = _session(tmp_path)

    with patch.object(layer_timelapse, "capture_frame", new=AsyncMock(return_value=FRESH_FRAME)) as mock_capture:
        captured = await session.capture_layer(1)

    assert captured is True
    mock_capture.assert_awaited_once()
    written = sorted(session.frames_dir.glob("layer_*.jpg"))
    assert written[0].read_bytes() == FRESH_FRAME


# ---------------------------------------------------------------------------
# Obico polling — external branch, mirroring the built-in one
# ---------------------------------------------------------------------------


def _external_printer() -> MagicMock:
    return MagicMock(
        external_camera_enabled=True,
        external_camera_url="/dev/video0",
        external_camera_type="usb",
        external_camera_snapshot_url=None,
        ip_address="192.168.1.10",
        access_code="12345678",
        model="A1",
    )


def _db_returning(printer) -> MagicMock:
    session = MagicMock()
    session.get = AsyncMock(return_value=printer)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


async def test_obico_reuses_the_live_external_frame():
    _attach_viewer()
    svc = ObicoDetectionService()

    with (
        patch(
            "backend.app.services.obico_detection.async_session",
            return_value=_db_returning(_external_printer()),
        ),
        patch(
            "backend.app.services.external_camera.capture_frame",
            new=AsyncMock(return_value=FRESH_FRAME),
        ) as mock_capture,
    ):
        result = await svc._capture_frame(printer_id=PRINTER_ID)

    assert result == LIVE_FRAME
    mock_capture.assert_not_called()


async def test_obico_skips_the_poll_when_the_external_buffer_is_empty():
    _attach_viewer(frame=None)
    svc = ObicoDetectionService()

    with (
        patch(
            "backend.app.services.obico_detection.async_session",
            return_value=_db_returning(_external_printer()),
        ),
        patch(
            "backend.app.services.external_camera.capture_frame",
            new=AsyncMock(return_value=FRESH_FRAME),
        ) as mock_capture,
    ):
        result = await svc._capture_frame(printer_id=PRINTER_ID)

    assert result is None
    mock_capture.assert_not_called()


async def test_obico_still_captures_when_nobody_is_watching():
    svc = ObicoDetectionService()

    with (
        patch(
            "backend.app.services.obico_detection.async_session",
            return_value=_db_returning(_external_printer()),
        ),
        patch(
            "backend.app.services.external_camera.capture_frame",
            new=AsyncMock(return_value=FRESH_FRAME),
        ) as mock_capture,
    ):
        result = await svc._capture_frame(printer_id=PRINTER_ID)

    assert result == FRESH_FRAME
    mock_capture.assert_awaited_once()


# ---------------------------------------------------------------------------
# Plate detection — its docstring already promised this
# ---------------------------------------------------------------------------


async def test_plate_detection_reuses_the_live_external_frame():
    from backend.app.services import plate_detection

    _attach_viewer()

    with patch(
        "backend.app.services.external_camera.capture_frame",
        new=AsyncMock(return_value=FRESH_FRAME),
    ) as mock_capture:
        image, source = await plate_detection.capture_camera_image(
            printer_id=PRINTER_ID,
            ip_address="192.168.1.10",
            access_code="12345678",
            model="A1",
            external_camera_url="/dev/video0",
            external_camera_type="usb",
            use_external=True,
        )

    assert image == LIVE_FRAME
    assert source == "external (buffered)"
    mock_capture.assert_not_called()
