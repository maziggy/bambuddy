"""Runtime fallback from Intel QSV to software camera processing."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import suppress
from pathlib import Path

import pytest

from backend.app.api.routes import camera
from backend.app.services.camera_profiles import CameraProfile

pytestmark = pytest.mark.asyncio


_FFMPEG_QSV_STARTUP_STDERR = b"""ffmpeg version 7.1.1 Copyright (c) 2000-2025 the FFmpeg developers
  built with gcc 14
  configuration: --enable-libvpl --enable-vaapi
Stream mapping:
  Stream #0:0 -> #0:0 (h264 (h264_qsv) -> mjpeg (mjpeg_qsv))
"""


class _FakeServer:
    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


class _BytesReader:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)

    async def read(self, _size: int = -1) -> bytes:
        return next(self._chunks, b"")


class _ImmediateFailureProcess:
    def __init__(self, stderr: bytes, pid: int) -> None:
        self.pid = pid
        self.returncode = 1
        self.stdout = _BytesReader([])
        self.stderr = _BytesReader([stderr])

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


class _StreamProcess:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        stderr: bytes = b"",
        pid: int,
    ) -> None:
        self.pid = pid
        self.returncode = None
        self.stdout = _BytesReader(chunks)
        self.stderr = _BytesReader([stderr] if stderr else [])

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


async def _fake_tls_proxy(
    _ip_address: str,
    _port: int,
) -> tuple[int, _FakeServer]:
    return 48521, _FakeServer()


async def _fake_qsv_device(
    _ffmpeg: str,
) -> tuple[Path, list[object]]:
    return Path("/dev/dri/renderD128"), []


def _configure_common_mocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(camera, "get_ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(camera, "create_tls_proxy", _fake_tls_proxy)
    monkeypatch.setattr(camera, "find_qsv_render_device", _fake_qsv_device)
    monkeypatch.setattr(
        camera,
        "get_camera_profile",
        lambda _model: CameraProfile(
            rtsp_reconnect_max=2,
            rtsp_reconnect_delay=0,
        ),
    )


def _make_stream(stream_id: str) -> AsyncGenerator[bytes, None]:
    return camera.generate_rtsp_mjpeg_stream(
        ip_address="192.0.2.17",
        access_code="test-code",
        model="H2D",
        fps=15,
        stream_id=stream_id,
        disconnect_event=asyncio.Event(),
        printer_id=None,
        video_processing="intel_qsv",
    )


async def test_immediate_qsv_failure_retries_with_software(monkeypatch):
    _configure_common_mocks(monkeypatch)

    failed_qsv = _ImmediateFailureProcess(
        b"Error initializing an MFX session: unsupported",
        pid=42001,
    )
    software = _StreamProcess(
        [b"\xff\xd8software-frame\xff\xd9"],
        pid=42002,
    )
    processes = iter((failed_qsv, software))
    commands: list[tuple[object, ...]] = []
    cache_clears = 0

    async def fake_create_subprocess_exec(*args, **_kwargs):
        commands.append(args)
        return next(processes)

    def fake_clear_cache() -> None:
        nonlocal cache_clears
        cache_clears += 1

    monkeypatch.setattr(
        camera.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(camera, "clear_qsv_device_cache", fake_clear_cache)

    stream = _make_stream("qsv-immediate-fallback")

    try:
        chunk = await asyncio.wait_for(anext(stream), timeout=2)
        assert b"software-frame" in chunk

        assert len(commands) == 2
        assert "mjpeg_qsv" in commands[0]
        assert "mjpeg_qsv" not in commands[1]
        assert "mjpeg" in commands[1]
        assert "-init_hw_device" not in commands[1]
        assert cache_clears == 1
    finally:
        with suppress(Exception):
            await stream.aclose()


async def test_qsv_eof_before_first_frame_falls_back_once(monkeypatch):
    _configure_common_mocks(monkeypatch)

    failed_qsv = _StreamProcess(
        [],
        stderr=b"Error creating a QSV device",
        pid=42011,
    )
    software = _StreamProcess(
        [b"\xff\xd8software-frame\xff\xd9"],
        pid=42012,
    )
    processes = iter((failed_qsv, software))
    commands: list[tuple[object, ...]] = []
    cache_clears = 0

    async def fake_create_subprocess_exec(*args, **_kwargs):
        commands.append(args)
        return next(processes)

    def fake_clear_cache() -> None:
        nonlocal cache_clears
        cache_clears += 1

    monkeypatch.setattr(
        camera.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(camera, "clear_qsv_device_cache", fake_clear_cache)

    stream = _make_stream("qsv-eof-fallback")

    try:
        chunk = await asyncio.wait_for(anext(stream), timeout=2)
        assert b"software-frame" in chunk

        assert len(commands) == 2
        assert "mjpeg_qsv" in commands[0]
        assert "mjpeg_qsv" not in commands[1]
        assert cache_clears == 1
    finally:
        with suppress(Exception):
            await stream.aclose()


async def test_network_disconnect_after_frame_keeps_qsv(monkeypatch):
    _configure_common_mocks(monkeypatch)

    first_qsv = _StreamProcess(
        [b"\xff\xd8first-frame\xff\xd9"],
        stderr=_FFMPEG_QSV_STARTUP_STDERR,
        pid=42021,
    )
    reconnected_qsv = _StreamProcess(
        [b"\xff\xd8second-frame\xff\xd9"],
        pid=42022,
    )
    processes = iter((first_qsv, reconnected_qsv))
    commands: list[tuple[object, ...]] = []
    cache_clears = 0

    async def fake_create_subprocess_exec(*args, **_kwargs):
        commands.append(args)
        return next(processes)

    def fake_clear_cache() -> None:
        nonlocal cache_clears
        cache_clears += 1

    monkeypatch.setattr(
        camera.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(camera, "clear_qsv_device_cache", fake_clear_cache)

    stream = _make_stream("qsv-network-reconnect")

    try:
        first = await asyncio.wait_for(anext(stream), timeout=2)
        second = await asyncio.wait_for(anext(stream), timeout=2)

        assert b"first-frame" in first
        assert b"second-frame" in second

        assert len(commands) == 2
        assert "mjpeg_qsv" in commands[0]
        assert "mjpeg_qsv" in commands[1]
        assert "-init_hw_device" in commands[1]
        assert cache_clears == 0
    finally:
        with suppress(Exception):
            await stream.aclose()
