"""The USB camera list the camera settings pick a device from.

Only devices a USB camera can stream from are listed (streaming accepts
/dev/video0 to /dev/video99), and the listing, which runs v4l2-ctl for each
device, does not hold up the server while it runs.
"""

import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from backend.app.services.external_camera import list_usb_cameras


def _devices(tmp_path: Path, *names: str) -> Path:
    for name in names:
        (tmp_path / name).touch()
    return tmp_path


def test_only_device_paths_streaming_accepts_are_listed(tmp_path):
    dev = _devices(tmp_path, "video0", "video12", "video100", "video05", "video-meta", "videoX")

    with patch("backend.app.services.external_camera.shutil.which", return_value=None):
        cameras = list_usb_cameras(dev_dir=dev)

    assert [camera["device"] for camera in cameras] == ["/dev/video0", "/dev/video12"]


def test_paths_are_listed_as_dev_paths(tmp_path):
    """The entry names the device as the camera settings store it."""
    dev = _devices(tmp_path, "video2")

    with patch("backend.app.services.external_camera.shutil.which", return_value=None):
        cameras = list_usb_cameras(dev_dir=dev)

    assert cameras == [{"device": "/dev/video2", "name": "video2", "capabilities": []}]


@pytest.mark.asyncio
async def test_the_listing_runs_off_the_event_loop(async_client: AsyncClient):
    seen: list[threading.Thread] = []

    def listing():
        seen.append(threading.current_thread())
        return [{"device": "/dev/video0", "name": "Webcam", "capabilities": ["capture"]}]

    with patch("backend.app.services.external_camera.list_usb_cameras", side_effect=listing):
        response = await async_client.get("/api/v1/printers/usb-cameras")

    assert response.status_code == 200
    assert response.json()["cameras"][0]["device"] == "/dev/video0"
    assert seen and seen[0] is not threading.main_thread()
