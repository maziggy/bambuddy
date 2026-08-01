"""Shared Intel Quick Sync device discovery and probing."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path

_RENDER_DEVICE_DIRECTORY = Path("/dev/dri")
_RENDER_DEVICE_PATTERN = "renderD*"
_QSV_PROBE_TIMEOUT_SECONDS = 10.0

_qsv_device_cache: Path | None = None
_qsv_probe_failure_cache: tuple[QsvDeviceProbe, ...] | None = None
_qsv_device_lock = asyncio.Lock()


@dataclass(frozen=True)
class QsvDeviceProbe:
    """Result of probing one DRM render device for the camera QSV pipeline."""

    device: Path
    available: bool
    duration_ms: int = 0
    code: str | None = None
    detail: str | None = None


def list_render_devices() -> list[Path]:
    """Return DRM render devices in deterministic numeric/name order."""

    if not _RENDER_DEVICE_DIRECTORY.is_dir():
        return []

    return sorted(_RENDER_DEVICE_DIRECTORY.glob(_RENDER_DEVICE_PATTERN))


def build_qsv_probe_command(ffmpeg: str, device: Path) -> tuple[str, ...]:
    """Build the smoke test matching the camera's VAAPI-derived QSV setup."""

    return (
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-init_hw_device",
        f"vaapi=va:{device}",
        "-init_hw_device",
        "qsv=qs@va",
        "-filter_hw_device",
        "qs",
        "-f",
        "lavfi",
        "-i",
        "color=size=64x64:rate=1:duration=1",
        "-vf",
        "format=nv12,hwupload=extra_hw_frames=16",
        "-frames:v",
        "1",
        "-c:v",
        "mjpeg_qsv",
        "-f",
        "null",
        "-",
    )


def _short_error(stderr: bytes, stdout: bytes = b"") -> str | None:
    text = (stderr or stdout).decode(errors="replace").strip()
    if not text:
        return None

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:500] if lines else None


def _classify_qsv_failure(stderr: bytes, stdout: bytes = b"") -> str:
    """Map known FFmpeg/QSV failures to actionable diagnostic codes."""

    text = (stderr + b"\n" + stdout).decode(errors="replace").lower()

    # oneVPL dispatcher is present, but no Intel GPU implementation can create
    # an MFX session. On current Debian/Ubuntu this commonly means that the
    # libmfx-gen1.2 runtime package is missing.
    if "mfx session" in text and "-9" in text:
        return "qsv_runtime_missing"

    return "qsv_initialization_failed"


async def probe_qsv_device(ffmpeg: str, device: Path) -> QsvDeviceProbe:
    """Test one render device using the real camera hardware-device chain."""

    started = time.monotonic()

    if not device.exists():
        return QsvDeviceProbe(
            device=device,
            available=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            code="render_device_missing",
            detail=str(device),
        )

    if not os.access(device, os.R_OK | os.W_OK):
        return QsvDeviceProbe(
            device=device,
            available=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            code="render_device_permission_denied",
            detail=str(device),
        )

    process = await asyncio.create_subprocess_exec(
        *build_qsv_probe_command(ffmpeg, device),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=_QSV_PROBE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        return QsvDeviceProbe(
            device=device,
            available=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            code="diagnostic_timeout",
        )

    if process.returncode != 0:
        return QsvDeviceProbe(
            device=device,
            available=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            code=_classify_qsv_failure(stderr, stdout),
            detail=_short_error(stderr, stdout),
        )

    return QsvDeviceProbe(
        device=device,
        available=True,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def clear_qsv_device_cache() -> None:
    """Forget the cached render device after a runtime QSV failure."""

    global _qsv_device_cache, _qsv_probe_failure_cache
    _qsv_device_cache = None
    _qsv_probe_failure_cache = None


async def find_qsv_render_device(
    ffmpeg: str,
    *,
    refresh: bool = False,
) -> tuple[Path | None, list[QsvDeviceProbe]]:
    """Return a cached QSV device or probe render nodes until one works.

    ``refresh=True`` forces a full probe and refreshes the process-local cache.
    The returned probe list is empty when a cached device was reused.
    """

    global _qsv_device_cache, _qsv_probe_failure_cache

    if not refresh and _qsv_probe_failure_cache is not None:
        return None, list(_qsv_probe_failure_cache)

    if not refresh and _qsv_device_cache is not None:
        if os.access(_qsv_device_cache, os.R_OK | os.W_OK):
            return _qsv_device_cache, []
        _qsv_device_cache = None

    async with _qsv_device_lock:
        # Another coroutine may have populated either cache while we waited.
        if not refresh and _qsv_probe_failure_cache is not None:
            return None, list(_qsv_probe_failure_cache)

        if not refresh and _qsv_device_cache is not None:
            if os.access(_qsv_device_cache, os.R_OK | os.W_OK):
                return _qsv_device_cache, []
            _qsv_device_cache = None

        probes: list[QsvDeviceProbe] = []

        for device in list_render_devices():
            probe = await probe_qsv_device(ffmpeg, device)
            probes.append(probe)

            if probe.available:
                _qsv_device_cache = device
                _qsv_probe_failure_cache = None
                return device, probes

        _qsv_device_cache = None
        _qsv_probe_failure_cache = tuple(probes)
        return None, probes
