"""A departing camera stream must not clean up its successor's state.

The fan-out stream id used to be ``f"{printer_id}-fanout"`` — constant per
printer, so every successive stream shared one registry key — and the
generator's ``finally`` popped the per-printer frame buffer unconditionally.
Teardown taking ~4s (the undrained-pipe deadlock, fixed separately) made the
overlap wide enough to hit by closing and reopening the camera:

    12.221  stream A cancelled, begins teardown
    12.324  new viewer attaches
    16.223  A finishes killing
    16.224  new generator registers _active_streams["1-fanout"]
            ...then A's finally pops that very entry

The damage is not cosmetic. ``is_stream_active()`` is what the #1348 / #1271
guards consult before deciding whether opening a second camera connection is
safe, so a printer with a viewer attached looked idle; the janitor's /proc scan
reaps any ffmpeg missing from ``_active_streams``, so it killed the live stream;
and ``/camera/stop`` reported ``Stopped 0``.

The external-camera path already solved this with a per-instance id (#2675).
These tests pin the same property for the fan-out path.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress

import pytest

from backend.app.api.routes import camera

pytestmark = pytest.mark.asyncio

PRINTER_ID = 7701


@pytest.fixture(autouse=True)
def _clean_registries():
    """These registries are module-global; leave them as we found them."""

    def _purge():
        for sid in [k for k in camera._active_streams if k.startswith(f"{PRINTER_ID}-")]:
            camera._active_streams.pop(sid, None)
        for sid in [k for k in camera._active_chamber_streams if k.startswith(f"{PRINTER_ID}-")]:
            camera._active_chamber_streams.pop(sid, None)
        for sid in [k for k in camera._stream_last_frame_times if k.startswith(f"{PRINTER_ID}-")]:
            camera._stream_last_frame_times.pop(sid, None)
        for sid in [k for k in camera._disconnect_events if k.startswith(f"{PRINTER_ID}-")]:
            camera._disconnect_events.pop(sid, None)
        camera._last_frames.pop(PRINTER_ID, None)
        camera._last_frame_times.pop(PRINTER_ID, None)
        camera._stream_start_times.pop(PRINTER_ID, None)

    _purge()
    yield
    _purge()


def _seed_frame_state() -> None:
    camera._last_frames[PRINTER_ID] = b"\xff\xd8live\xff\xd9"
    camera._last_frame_times[PRINTER_ID] = time.time()
    camera._stream_start_times[PRINTER_ID] = time.time()


# ---------------------------------------------------------------------------
# _new_fanout_stream_id — one key per stream, not per printer
# ---------------------------------------------------------------------------


async def test_fanout_stream_ids_are_unique_per_stream():
    """Two streams for one printer must never collide in the registries."""
    ids = {camera._new_fanout_stream_id(PRINTER_ID) for _ in range(50)}

    assert len(ids) == 50, "ids collide, so one stream can clean up another's entry"


async def test_camera_stream_has_no_function_local_module_imports():
    """A local ``import x`` anywhere in camera_stream shadows x for the WHOLE
    function, including branches that never reach the import.

    This is not hypothetical: an ``import uuid`` inside the external-camera
    branch meant building the fan-out id on the RTSP path raised
    UnboundLocalError, so the camera would not start on any printer without an
    external camera configured. ``time`` and ``uuid`` are module-level now;
    keep them that way.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(camera.camera_stream))
    local_imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]

    assert local_imports == [], f"function-local imports shadow the whole function: {local_imports}"


async def test_fanout_stream_id_keeps_the_printer_prefix():
    """is_stream_active / stop_camera_stream / camera-status all scan for it."""
    stream_id = camera._new_fanout_stream_id(PRINTER_ID)

    assert stream_id.startswith(f"{PRINTER_ID}-")
    camera._active_streams[stream_id] = object()
    assert camera.is_stream_active(PRINTER_ID) is True


# ---------------------------------------------------------------------------
# _release_printer_frame_state — the ownership check itself
# ---------------------------------------------------------------------------


async def test_frame_state_survives_when_another_rtsp_stream_is_live():
    _seed_frame_state()
    camera._active_streams[f"{PRINTER_ID}-fanout-successor"] = object()

    camera._release_printer_frame_state(PRINTER_ID)

    assert PRINTER_ID in camera._last_frames, "successor's buffered frame was wiped"
    assert PRINTER_ID in camera._last_frame_times
    assert PRINTER_ID in camera._stream_start_times


async def test_frame_state_survives_when_a_chamber_stream_is_live():
    """A1/P1 models register in a different dict; ownership spans both."""
    _seed_frame_state()
    camera._active_chamber_streams[f"{PRINTER_ID}-fanout-successor"] = (None, None)

    camera._release_printer_frame_state(PRINTER_ID)

    assert PRINTER_ID in camera._last_frames


async def test_last_stream_out_releases_the_frame_state():
    """The other half: with nothing left running, stale state must not linger."""
    _seed_frame_state()

    camera._release_printer_frame_state(PRINTER_ID)

    assert PRINTER_ID not in camera._last_frames
    assert PRINTER_ID not in camera._last_frame_times
    assert PRINTER_ID not in camera._stream_start_times


async def test_release_is_a_noop_without_a_printer_id():
    _seed_frame_state()

    camera._release_printer_frame_state(None)

    assert PRINTER_ID in camera._last_frames


# ---------------------------------------------------------------------------
# The whole generator cleanup path, with a successor already registered
# ---------------------------------------------------------------------------


class _FakeServer:
    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


class _OneFrameThenEOF:
    def __init__(self) -> None:
        self._sent = False

    async def read(self, _size: int = -1) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return b"\xff\xd8predecessor\xff\xd9"


class _Proc:
    def __init__(self, pid: int = 77010) -> None:
        self.pid = pid
        self.returncode = None
        self.stdout = _OneFrameThenEOF()
        self.stderr = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


async def test_departing_generator_leaves_its_successors_registry_entry_alone(monkeypatch):
    """End of the real cleanup path, with a second stream already registered."""

    async def _fake_exec(*_args, **_kwargs):
        return _Proc()

    async def _fake_proxy(_ip: str, _port: int):
        return 48999, _FakeServer()

    monkeypatch.setattr(camera, "get_ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(camera, "create_tls_proxy", _fake_proxy)
    monkeypatch.setattr(camera.asyncio, "create_subprocess_exec", _fake_exec)

    predecessor_id = f"{PRINTER_ID}-fanout-aaaaaaaa"
    successor_id = f"{PRINTER_ID}-fanout-bbbbbbbb"

    stream = camera.generate_rtsp_mjpeg_stream(
        ip_address="192.0.2.31",
        access_code="test-code",
        model="P2S",
        fps=15,
        stream_id=predecessor_id,
        disconnect_event=asyncio.Event(),
        printer_id=PRINTER_ID,
    )

    # Drive it far enough to buffer a frame, as a real viewer would.
    chunk = await asyncio.wait_for(anext(stream), timeout=5.0)
    assert b"predecessor" in chunk
    assert camera._last_frames[PRINTER_ID].endswith(b"predecessor\xff\xd9")

    # A viewer reopens the camera mid-teardown: a fresh stream registers under
    # its own id and republishes the buffered frame.
    camera._active_streams[successor_id] = object()
    camera._last_frames[PRINTER_ID] = b"\xff\xd8successor\xff\xd9"

    with suppress(Exception):
        await asyncio.wait_for(stream.aclose(), timeout=5.0)

    assert successor_id in camera._active_streams, "predecessor removed its successor's entry"
    assert camera.is_stream_active(PRINTER_ID) is True, "a viewer is attached; guards must see it"
    assert camera._last_frames[PRINTER_ID].endswith(b"successor\xff\xd9"), "successor's frame was wiped"
    assert predecessor_id not in camera._active_streams, "predecessor must still clean up after itself"
