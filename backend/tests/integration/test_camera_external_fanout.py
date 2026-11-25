"""External camera streams share one upstream per printer (RC-C6).

Every viewer of an external camera started its own upstream: its own ffmpeg,
MJPEG connection or snapshot poll. A USB camera allows one V4L2 handle, so a
second viewer (a Cam Wall tile and the camera page, two browsers) failed with
"device busy". External cameras now use the same fan-out broadcaster as the
printer's own camera (#1089).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes import camera
from backend.app.models.printer import Printer
from backend.app.services import camera_fanout

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _Proc:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None
        self.stdout = None
        self.stderr = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


class _Upstream:
    """A fake generate_mjpeg_stream that counts how often a camera is opened."""

    def __init__(self) -> None:
        self.opened: list[tuple[str, str]] = []
        self.closed = 0

    async def __call__(self, url, camera_type, fps, *, on_process=None, on_frame=None, stop_event=None):
        self.opened.append((url, camera_type))
        if on_process is not None:
            on_process(_Proc(60000 + len(self.opened)))
        try:
            while stop_event is None or not stop_event.is_set():
                if on_frame is not None:
                    on_frame(b"\xff\xd8frame\xff\xd9")
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8frame\xff\xd9\r\n"
                await asyncio.sleep(0.01)
        finally:
            self.closed += 1


@pytest.fixture
def upstream(monkeypatch):
    fake = _Upstream()
    monkeypatch.setattr(camera_fanout, "_GRACE_SECONDS", 0.05)
    with patch("backend.app.services.external_camera.generate_mjpeg_stream", fake):
        yield fake


@pytest.fixture
async def usb_printer(async_client, db_session) -> int:
    # async_client points database.async_session, which camera_stream opens, at the test DB.
    row = Printer(
        name="USB cam",
        serial_number="CAM-FANOUT",
        ip_address="192.0.2.71",
        access_code="x",
        external_camera_enabled=True,
        external_camera_url="/dev/video0",
        external_camera_type="usb",
    )
    db_session.add(row)
    await db_session.commit()
    yield row.id
    await camera.forget_printer(row.id)


def _request() -> MagicMock:
    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=False)
    return request


async def _open(printer_id: int, viewer: str | None = None):
    response = await camera.camera_stream(printer_id, _request(), fps=5, opener=None, viewer=viewer)
    stream = response.body_iterator
    await asyncio.wait_for(anext(stream), 2)
    return stream


async def _settle() -> None:
    await asyncio.sleep(0.15)


async def test_two_viewers_share_one_upstream(upstream, usb_printer):
    tile = await _open(usb_printer, "tile-a")
    page = await _open(usb_printer, "page-b")

    await asyncio.wait_for(anext(tile), 2)
    await asyncio.wait_for(anext(page), 2)

    assert upstream.opened == [("/dev/video0", "usb")]
    usb_streams = [sid for sid in camera._active_streams if sid.startswith(f"{usb_printer}-ext-")]
    assert len(usb_streams) == 1

    await tile.aclose()
    await page.aclose()


async def test_the_other_viewer_keeps_watching_when_one_leaves(upstream, usb_printer):
    tile = await _open(usb_printer)
    page = await _open(usb_printer)

    await tile.aclose()

    assert await asyncio.wait_for(anext(page), 2)
    assert (await camera.camera_status(usb_printer, None))["active"]
    assert upstream.closed == 0
    await page.aclose()


async def test_the_upstream_closes_after_the_last_viewer(upstream, usb_printer):
    page = await _open(usb_printer)
    await page.aclose()

    await _settle()

    assert upstream.closed == 1
    assert not any(sid.startswith(f"{usb_printer}-ext-") for sid in camera._active_streams)
    assert not (await camera.camera_status(usb_printer, None))["active"]


async def test_a_viewer_back_within_the_grace_window_reuses_the_upstream(upstream, usb_printer, monkeypatch):
    """A page reload: closing and reopening a USB camera costs seconds."""
    monkeypatch.setattr(camera_fanout, "_GRACE_SECONDS", 5.0)
    first = await _open(usb_printer)
    await first.aclose()

    again = await _open(usb_printer)

    assert len(upstream.opened) == 1
    status = await camera.camera_status(usb_printer, None)
    assert status["active"] and status["stream_uptime"] is not None
    await again.aclose()


async def test_a_stop_while_another_viewer_watches_leaves_the_stream(upstream, usb_printer):
    page = await _open(usb_printer, "page-b")

    result = await camera.stop_camera_stream(usb_printer, _=None, viewer="tile-a")

    assert result.get("skipped") is True
    assert await asyncio.wait_for(anext(page), 2)
    await page.aclose()


async def test_a_stop_with_nobody_watching_ends_the_upstream(upstream, usb_printer, monkeypatch):
    monkeypatch.setattr(camera_fanout, "_GRACE_SECONDS", 5.0)
    page = await _open(usb_printer)
    await page.aclose()

    await camera.stop_camera_stream(usb_printer, _=None)

    assert upstream.closed == 1
    assert not any(sid.startswith(f"{usb_printer}-ext-") for sid in camera._active_streams)


async def test_a_changed_camera_gets_its_own_upstream(upstream, usb_printer, db_session):
    old = await _open(usb_printer)

    row = await db_session.get(Printer, usb_printer)
    row.external_camera_url = "/dev/video2"
    await db_session.commit()
    new = await _open(usb_printer)

    assert upstream.opened == [("/dev/video0", "usb"), ("/dev/video2", "usb")]
    # The old camera's viewer is ended rather than left on the old device.
    with pytest.raises(StopAsyncIteration):
        for _ in range(20):
            await asyncio.wait_for(anext(old), 2)
    assert await asyncio.wait_for(anext(new), 2)
    await new.aclose()


async def test_a_deleted_printer_ends_the_shared_stream(upstream, usb_printer):
    page = await _open(usb_printer)

    await camera.forget_printer(usb_printer)

    with pytest.raises(StopAsyncIteration):
        for _ in range(20):
            await asyncio.wait_for(anext(page), 2)
    await _settle()
    assert upstream.closed == 1


async def test_the_live_frame_is_published_for_one_shot_captures(upstream, usb_printer):
    page = await _open(usb_printer)

    defer, frame = camera.live_frame_for_capture(usb_printer)

    assert defer and frame == b"\xff\xd8frame\xff\xd9"
    await page.aclose()


async def test_a_snapshot_in_the_grace_window_gets_the_live_frame(upstream, usb_printer, monkeypatch):
    """The upstream still holds the camera for a few seconds after the last viewer."""
    monkeypatch.setattr(camera_fanout, "_GRACE_SECONDS", 5.0)
    page = await _open(usb_printer)
    await page.aclose()
    # Only frames taken after the viewer left count.
    camera._last_frames.pop(usb_printer, None)
    await asyncio.sleep(0.05)

    defer, frame = camera.live_frame_for_capture(usb_printer)

    assert defer and frame == b"\xff\xd8frame\xff\xd9"
    assert not (await camera.camera_status(usb_printer, None))["stalled"]
