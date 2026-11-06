"""One-shot captures wait for a live view of an external MJPEG camera (SP-C7).

An ESP32-style MJPEG camera serves one client. Its live view ran no process
the stream registry tracks, so ``is_stream_active`` said no viewer was
attached, and timelapse frames, finish photos, Obico and the plate check
opened a second connection the camera refused. A camera with a separate
snapshot URL is left out: that URL serves captures beside the live view, at
full quality, and the plate check's references come from it (#1177).
"""

import asyncio
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from backend.app.api.routes import camera

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

FRAME = b"\xff\xd8frame\xff\xd9"


@pytest.fixture(autouse=True)
def _forget_frames():
    yield
    for state in (camera._last_frames, camera._last_frame_times, camera._stream_start_times):
        state.clear()


async def _watch(async_client: AsyncClient, printer_id: int) -> dict:
    seen: dict = {}

    async def _stream(*_args, on_frame=None, **_kwargs):
        on_frame(FRAME)
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8\xff\xd9\r\n"
        await asyncio.sleep(0.05)
        seen["active"] = camera.is_stream_active(printer_id)
        seen["defer"] = camera.live_frame_for_capture(printer_id)[0]
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8\xff\xd9\r\n"

    with patch("backend.app.services.external_camera.generate_mjpeg_stream", _stream):
        response = await async_client.get(f"/api/v1/printers/{printer_id}/camera/stream")
    assert response.status_code == 200
    await asyncio.sleep(0.1)
    seen["after"] = camera.is_stream_active(printer_id)
    seen["frame_after"] = camera._last_frames.get(printer_id)
    return seen


async def test_a_watched_mjpeg_camera_is_not_dialled_again(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(
        external_camera_enabled=True,
        external_camera_url="http://192.0.2.50:81/stream",
        external_camera_type="mjpeg",
    )

    seen = await _watch(async_client, printer.id)

    assert seen["active"] is True
    assert seen["defer"] is True
    assert seen["after"] is False
    # The camera's upstream ended while the viewer was still counted: its last
    # frame must not outlive it, or a plate check or the next viewer's start
    # takes an old picture for a current one.
    assert seen["frame_after"] is None


async def test_a_camera_with_a_snapshot_url_keeps_using_it(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(
        external_camera_enabled=True,
        # Its own URL: a fan-out key of its own, not the first test's upstream
        # still in its grace.
        external_camera_url="http://192.0.2.51:81/stream",
        external_camera_type="mjpeg",
        external_camera_snapshot_url="http://192.0.2.51/capture",
    )

    seen = await _watch(async_client, printer.id)

    assert seen["active"] is False
