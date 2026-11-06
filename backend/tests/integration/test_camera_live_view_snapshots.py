"""Snapshots and the external camera test stand down for a live view (SP-C2).

A USB camera allows one V4L2 handle, so an external snapshot taken while a
viewer was watching failed with 503 on every Home Assistant or camera-wall
poll, and the Test button failed the same way. Finish photos and timelapse
frames already reuse the live frame (``live_frame_for_capture``); the
snapshot route and the Test button now do too.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

# 640x480 baseline JPEG header: SOI, then SOF0 with height 480 and width 640.
LIVE_JPEG = b"\xff\xd8\xff\xc0\x00\x11\x08\x01\xe0\x02\x80\x03" + b"\x00" * 120 + b"\xff\xd9"

USB = {"external_camera_enabled": True, "external_camera_url": "/dev/video0", "external_camera_type": "usb"}


def _live_view(printer_id: int, frame: bytes | None):
    streams = {f"{printer_id}-fanout": MagicMock()}
    frames = {printer_id: frame} if frame is not None else {}
    return (
        patch("backend.app.api.routes.camera._active_streams", streams),
        patch("backend.app.api.routes.camera._last_frames", frames),
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_external_snapshot_uses_the_live_frame(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(**USB)
    streams, frames = _live_view(printer.id, LIVE_JPEG)
    with (
        streams,
        frames,
        patch("backend.app.services.external_camera.capture_frame", new_callable=AsyncMock) as capture,
    ):
        response = await async_client.get(f"/api/v1/printers/{printer.id}/camera/snapshot")

    assert response.status_code == 200
    assert response.content == LIVE_JPEG
    capture.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_external_snapshot_waits_for_a_starting_live_view(async_client: AsyncClient, printer_factory):
    """No frame yet: opening the camera now would knock the viewer off (#1348)."""
    printer = await printer_factory(**USB)
    streams, frames = _live_view(printer.id, None)
    with (
        streams,
        frames,
        patch("backend.app.services.external_camera.capture_frame", new_callable=AsyncMock) as capture,
    ):
        response = await async_client.get(f"/api/v1/printers/{printer.id}/camera/snapshot")

    assert response.status_code == 503
    assert response.headers.get("retry-after") == "2"
    capture.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bambu_snapshot_waits_for_a_starting_live_view(async_client: AsyncClient, printer_factory):
    printer = await printer_factory()
    streams, frames = _live_view(printer.id, None)
    with (
        streams,
        frames,
        patch("backend.app.api.routes.camera.capture_camera_frame", new_callable=AsyncMock) as capture,
    ):
        response = await async_client.get(f"/api/v1/printers/{printer.id}/camera/snapshot")

    assert response.status_code == 503
    capture.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_testing_the_watched_camera_reports_it_working(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(**USB)
    streams, frames = _live_view(printer.id, LIVE_JPEG)
    with (
        streams,
        frames,
        patch("backend.app.services.external_camera.test_connection", new_callable=AsyncMock) as probe,
    ):
        response = await async_client.post(
            f"/api/v1/printers/{printer.id}/camera/external/test",
            json={"url": "/dev/video0", "camera_type": "usb"},
        )

    assert response.status_code == 200
    assert response.json() == {"success": True, "resolution": "640x480", "coalesced": True, "live_view": True}
    probe.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_testing_the_watched_camera_while_the_view_starts(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(**USB)
    streams, frames = _live_view(printer.id, None)
    with (
        streams,
        frames,
        patch("backend.app.services.external_camera.test_connection", new_callable=AsyncMock) as probe,
    ):
        response = await async_client.post(
            f"/api/v1/printers/{printer.id}/camera/external/test",
            json={"url": "/dev/video0", "camera_type": "usb"},
        )

    body = response.json()
    assert body["success"] is False
    assert body["live_view"] is True
    probe.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_testing_another_camera_still_opens_it(async_client: AsyncClient, printer_factory):
    """The form may hold a device that is not the one being watched."""
    printer = await printer_factory(**USB)
    streams, frames = _live_view(printer.id, LIVE_JPEG)
    with (
        streams,
        frames,
        patch(
            "backend.app.services.external_camera.test_connection",
            new_callable=AsyncMock,
            return_value={"success": True, "resolution": "1280x720", "coalesced": False},
        ) as probe,
    ):
        response = await async_client.post(
            f"/api/v1/printers/{printer.id}/camera/external/test",
            json={"url": "/dev/video2", "camera_type": "usb"},
        )

    assert response.json()["resolution"] == "1280x720"
    probe.assert_awaited_once_with("/dev/video2", "usb")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_dedicated_snapshot_url_is_still_used_during_a_live_view(async_client: AsyncClient, printer_factory):
    """#1177: an HTTP snapshot endpoint has no reader to compete with, and gives
    the full-quality frame the live view does not."""
    printer = await printer_factory(
        external_camera_enabled=True,
        external_camera_url="rtsp://10.0.0.9/live",
        external_camera_type="rtsp",
        external_camera_snapshot_url="http://10.0.0.9/snapshot.jpg",
    )
    streams, frames = _live_view(printer.id, None)
    with (
        streams,
        frames,
        patch(
            "backend.app.services.external_camera.capture_frame", new_callable=AsyncMock, return_value=LIVE_JPEG
        ) as capture,
    ):
        response = await async_client.get(f"/api/v1/printers/{printer.id}/camera/snapshot")

    assert response.status_code == 200
    capture.assert_awaited_once()
    assert capture.await_args.kwargs["snapshot_url"] == "http://10.0.0.9/snapshot.jpg"
