"""Camera streaming API endpoints for Bambu Lab printers."""

import asyncio
import logging
import math
import os
import re
import struct
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.user import User
from backend.app.services.camera import (
    ChamberConnectionClosed,
    capture_camera_frame,
    generate_chamber_image_stream,
    get_camera_port,
    get_ffmpeg_path,
    get_rtsp_semaphore,
    is_chamber_image_model,
    read_next_chamber_frame,
    resolve_camera_quality,
    test_camera_connection,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/printers", tags=["camera"])

CAMERA_QUALITY_PRESETS = {
    "low": {"grid": {"fps": 2, "quality": 20, "scale": 0.25}, "single": {"fps": 10, "quality": 10, "scale": 0.5}, "threads": 1},
    "medium": {"grid": {"fps": 5, "quality": 15, "scale": 0.5}, "single": {"fps": 15, "quality": 5, "scale": 1.0}, "threads": 0},
    "high": {"grid": {"fps": 10, "quality": 5, "scale": 0.75}, "single": {"fps": 30, "quality": 2, "scale": 1.0}, "threads": 0},
}

# Track active ffmpeg processes for cleanup
_active_streams: dict[str, asyncio.subprocess.Process] = {}

# Track active chamber image connections for cleanup
_active_chamber_streams: dict[str, tuple] = {}

# Store last frame for each printer (for photo capture from active stream)
_last_frames: dict[int, bytes] = {}

# Track last frame timestamp for each printer (for stall detection)
_last_frame_times: dict[int, float] = {}

# Track stream start times for each printer
_stream_start_times: dict[int, float] = {}

# Track active external camera streams by printer ID
_active_external_streams: set[int] = set()

# Track ALL spawned ffmpeg PIDs (persists even if _active_streams entries are removed)
# Maps PID -> spawn timestamp — used by cleanup to find truly orphaned OS processes
_spawned_ffmpeg_pids: dict[int, float] = {}
# Max age for stale frame buffer entries (5 minutes)
_FRAME_BUFFER_MAX_AGE = 300.0
_CLEANUP_INTERVAL = 60.0  # seconds between periodic cleanup runs
_RTSP_BUFFER_LIMIT = 3 * 1024 * 1024  # 3 MB — enough for 2-3 large JPEG frames
_cleanup_task: asyncio.Task | None = None


def _scan_dead_pids() -> list[int]:
    """Check which tracked ffmpeg PIDs no longer exist (sync, safe for executor)."""
    dead = []
    for pid in list(_spawned_ffmpeg_pids):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            dead.append(pid)
        except PermissionError:
            pass  # Process exists but we can't signal it
    return dead


async def _cleanup_stale_frame_buffers() -> None:
    """Remove stale entries from module-level frame dicts.

    Called periodically to prevent unbounded growth if stream generators
    crash without running their finally blocks.
    """
    now = time.monotonic()
    stale_ids = [pid for pid, ts in _last_frame_times.items() if now - ts > _FRAME_BUFFER_MAX_AGE]
    for pid in stale_ids:
        _last_frames.pop(pid, None)
        _last_frame_times.pop(pid, None)
        _stream_start_times.pop(pid, None)
    if stale_ids:
        logger.info("Cleaned up stale frame buffers for printers: %s", stale_ids)

    # Clean up PIDs for processes that no longer exist — offload to thread pool
    # to avoid blocking the event loop with os.kill() syscalls
    if _spawned_ffmpeg_pids:
        dead_pids = await asyncio.get_running_loop().run_in_executor(None, _scan_dead_pids)
        for pid in dead_pids:
            _spawned_ffmpeg_pids.pop(pid, None)
        if dead_pids:
            logger.info("Cleaned up %d dead ffmpeg PIDs from tracking", len(dead_pids))


async def _periodic_cleanup_loop() -> None:
    """Background task that runs stale frame buffer cleanup on a fixed interval."""
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL)
        await _cleanup_stale_frame_buffers()


def start_frame_buffer_cleanup() -> None:
    """Start the periodic cleanup background task."""
    global _cleanup_task
    if _cleanup_task is None:
        _cleanup_task = asyncio.create_task(_periodic_cleanup_loop())
        logger.info("Started periodic frame buffer cleanup (every %.0fs)", _CLEANUP_INTERVAL)


def stop_frame_buffer_cleanup() -> None:
    """Stop the periodic cleanup background task."""
    global _cleanup_task
    if _cleanup_task is not None:
        _cleanup_task.cancel()
        _cleanup_task = None
        logger.info("Stopped periodic frame buffer cleanup")


def get_buffered_frame(printer_id: int) -> bytes | None:
    """Get the last buffered frame for a printer from an active stream.

    Checks the shared hub first (zero-copy), then falls back to _last_frames
    which is populated by non-hub streams (chamber, RTSP).
    """
    hub_frame = _hub.get_last_frame(printer_id)
    if hub_frame is not None:
        return hub_frame
    return _last_frames.get(printer_id)


async def get_printer_or_404(printer_id: int, db: AsyncSession) -> Printer:
    """Get printer by ID or raise 404."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")
    return printer


class _SharedStream:
    """State for a single shared camera stream.

    Viewers poll frame_seq to detect new frames.  No ref counting — viewers
    are independent polling loops with zero cleanup requirements.  The producer
    self-stops after IDLE_TIMEOUT seconds without any viewer activity.

    Note: viewer_count is approximate — incremented/decremented without locks.
    Used only for logging and idle-timeout heuristics, not for correctness.
    """

    __slots__ = (
        "frame",
        "frame_seq",
        "task",
        "error",
        "alive",
        "last_accessed",
        "params_key",
        "viewer_count",
        "frame_event",
    )

    def __init__(self, params_key: str = "") -> None:
        self.frame: bytes | None = None
        self.frame_seq: int = 0
        self.task: asyncio.Task | None = None
        self.error: str | None = None
        self.alive: bool = True
        self.last_accessed: float = time.monotonic()
        self.params_key: str = params_key  # e.g. "5-15-0.5" for fps-quality-scale
        self.viewer_count: int = 0
        self.frame_event: asyncio.Event = asyncio.Event()


class SharedStreamHub:
    """One camera source per printer, shared across multiple viewers.

    Design: producers and viewers are fully decoupled.
    - Producer: a background task that reads raw frames from ffmpeg/chamber
      and writes them into _SharedStream.frame.  Self-stops after
      IDLE_TIMEOUT seconds with no viewer activity.
    - Viewer: a simple async-generator polling loop returned by make_viewer().
      It has NO cleanup — when the HTTP connection drops, the generator is
      just abandoned.  No locks, no ref counting, no aclose() chains.

    This eliminates all async-generator lifecycle bugs that caused the
    "toggle off then on → black screen / backend stuck" issue.
    """

    IDLE_TIMEOUT = 30.0  # seconds without a viewer before producer auto-stops

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._streams: dict[int, _SharedStream] = {}

    async def get_or_start(self, printer_id: int, starter_fn, params_key: str = "") -> _SharedStream:
        """Return the shared stream for a printer, starting a producer if needed.

        Always reuses an existing alive producer regardless of params — this
        prevents different clients (grid vs single camera) from fighting over
        quality settings.  Use restart() to explicitly change quality.
        """
        async with self._lock:
            entry = self._streams.get(printer_id)
            if entry is not None and entry.alive:
                entry.last_accessed = time.monotonic()
                return entry
            # Start a new producer
            entry = _SharedStream(params_key=params_key)
            self._streams[printer_id] = entry
            entry.task = asyncio.create_task(self._run_producer(printer_id, starter_fn, entry))
            logger.info(
                "Started new producer for printer %s (params=%s, total_producers=%s)",
                printer_id,
                params_key,
                len(self._streams),
            )
            return entry

    async def restart(self, printer_id: int, starter_fn, params_key: str) -> _SharedStream:
        """Stop the existing producer and start a new one with different params.

        Called when a client explicitly changes quality settings.

        Three-phase approach avoids racing on the camera resource:
        1. Under lock: mark old entry dead, cancel its task, remove from registry
        2. No lock: await old task so its finally block (ffmpeg kill) completes
        3. Re-acquire lock: guard against concurrent get_or_start(), create new entry
        """
        old_task: asyncio.Task | None = None

        # Phase 1 — cancel under lock
        async with self._lock:
            old = self._streams.get(printer_id)
            if old is not None and old.alive:
                if old.params_key == params_key:
                    # Same params — no need to restart
                    old.last_accessed = time.monotonic()
                    return old
                logger.info(
                    "Restarting producer for printer %s (%s → %s)",
                    printer_id,
                    old.params_key,
                    params_key,
                )
                old.alive = False
                old_task = old.task
                if old_task:
                    old_task.cancel()
                # Remove so the old producer's finally block identity check won't match
                del self._streams[printer_id]

        # Phase 2 — await old task outside lock (lets ffmpeg terminate fully)
        if old_task is not None:
            try:
                await asyncio.wait_for(old_task, timeout=3.0)
            except (asyncio.CancelledError, TimeoutError, Exception):
                pass  # Best effort — task will clean up on its own eventually

        # Phase 3 — re-acquire lock to create new entry
        async with self._lock:
            # Guard: another caller may have started a producer during our gap
            existing = self._streams.get(printer_id)
            if existing is not None and existing.alive:
                existing.last_accessed = time.monotonic()
                return existing
            entry = _SharedStream(params_key=params_key)
            self._streams[printer_id] = entry
            entry.task = asyncio.create_task(self._run_producer(printer_id, starter_fn, entry))
            logger.info("Started new producer for printer %s (params=%s)", printer_id, params_key)
            return entry

    def make_viewer(self, entry: _SharedStream, fps: int) -> AsyncGenerator[bytes, None]:
        """Create a viewer generator that polls the shared frame buffer.

        This generator has lightweight cleanup: it increments viewer_count
        on start and decrements on exit.  When the HTTP response ends
        (client disconnect, CancelledError, GC), the finally block runs
        automatically.  No locks, no unsubscribe, no aclose() needed.
        """

        async def _viewer():
            entry.viewer_count += 1
            try:
                frame_interval = 1.0 / fps if fps > 0 else 0.1
                seen_seq = 0
                last_yield = 0.0

                while entry.alive:
                    # Touch last_accessed so the producer knows someone is watching
                    entry.last_accessed = time.monotonic()

                    # Snapshot both seq and frame together so they stay consistent
                    # (producer may update both between our reads otherwise)
                    seq = entry.frame_seq
                    frame = entry.frame

                    if seq <= seen_seq:
                        # Wait for the producer to post a new frame instead of polling
                        try:
                            await asyncio.wait_for(entry.frame_event.wait(), timeout=frame_interval)
                        except TimeoutError:
                            pass
                        continue

                    if frame is None:
                        break

                    # Per-viewer rate limiting — sleep until the next frame interval
                    now = time.monotonic()
                    remaining = frame_interval - (now - last_yield)
                    if remaining > 0:
                        await asyncio.sleep(remaining)

                    seen_seq = seq
                    last_yield = time.monotonic()

                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(frame)).encode() + b"\r\n"
                        b"\r\n"
                    )
                    yield frame
                    yield b"\r\n"
            finally:
                entry.viewer_count -= 1
                if entry.viewer_count < 0:
                    logger.warning("viewer_count underflow for entry params=%s, resetting to 0", entry.params_key)
                    entry.viewer_count = 0

        return _viewer()

    async def _run_producer(self, printer_id: int, starter_fn, entry: _SharedStream) -> None:
        """Background task: read raw frames from ffmpeg/chamber into the shared buffer.

        Self-cleans when done: sets alive=False and removes itself from _streams.
        """
        source = None
        try:
            source = starter_fn()
            async for frame in source:
                if not entry.alive:
                    logger.info("Producer for printer %s marked dead, exiting", printer_id)
                    break
                entry.frame = frame
                entry.frame_seq += 1
                entry.frame_event.set()
                entry.frame_event.clear()
                # Auto-stop if no viewer has polled recently
                if time.monotonic() - entry.last_accessed > self.IDLE_TIMEOUT:
                    logger.info(
                        "Producer idle for printer %s (%.0fs, viewers=%s), auto-stopping",
                        printer_id,
                        self.IDLE_TIMEOUT,
                        entry.viewer_count,
                    )
                    # Mark dead before breaking so get_or_start() won't hand out
                    # this dying entry to a new viewer during cleanup
                    entry.alive = False
                    break
        except asyncio.CancelledError:
            logger.info("Producer cancelled for printer %s", printer_id)
            raise
        except Exception as e:
            logger.exception("Producer error for printer %s: %s", printer_id, e)
            entry.error = str(e)
        finally:
            # 1. Mark dead so viewers stop polling immediately
            entry.alive = False
            entry.frame = None
            # 2. Close the source generator (terminates ffmpeg / closes SSL)
            if source is not None:
                try:
                    await source.aclose()
                except Exception:
                    pass
            # 3. Remove from registry — identity check ensures we don't remove
            #    a replacement entry created by get_or_start() during our cleanup
            async with self._lock:
                if self._streams.get(printer_id) is entry:
                    del self._streams[printer_id]
            logger.info(
                "Producer for printer %s stopped and cleaned up (remaining_producers=%s)",
                printer_id,
                len(self._streams),
            )

    async def stop(self, printer_id: int) -> bool:
        """Force-stop the shared stream for a printer."""
        async with self._lock:
            entry = self._streams.pop(printer_id, None)
        if entry is None:
            return False
        # Mark dead — viewers will stop on next poll
        entry.alive = False
        entry.frame = None
        # Cancel and await the producer so its finally block runs (closes ffmpeg/SSL)
        if entry.task and not entry.task.done():
            entry.task.cancel()
            try:
                await asyncio.wait_for(entry.task, timeout=5.0)
            except (asyncio.CancelledError, TimeoutError, Exception):
                pass  # Best effort — task will clean up on its own eventually
        return True

    def is_active(self, printer_id: int) -> bool:
        entry = self._streams.get(printer_id)
        return entry is not None and entry.alive

    def get_last_frame(self, printer_id: int) -> bytes | None:
        """Return the current frame from the shared producer, or None."""
        entry = self._streams.get(printer_id)
        if entry is not None and entry.alive and entry.frame is not None:
            return entry.frame
        return None

    async def get_existing(self, printer_id: int) -> "_SharedStream | None":
        """Return an alive producer for *printer_id*, or ``None``."""
        async with self._lock:
            entry = self._streams.get(printer_id)
            if entry is not None and entry.alive:
                entry.last_accessed = time.monotonic()
                return entry
        return None

    async def get_existing_batch(self, printer_ids: list[int]) -> tuple[dict[int, "_SharedStream"], list[int]]:
        """Return ``(found, missing)`` for a batch of printer IDs in one pass."""
        found: dict[int, _SharedStream] = {}
        missing: list[int] = []
        now = time.monotonic()
        async with self._lock:
            for pid in printer_ids:
                entry = self._streams.get(pid)
                if entry is not None and entry.alive:
                    entry.last_accessed = now
                    found[pid] = entry
                else:
                    missing.append(pid)
        return found, missing

    def status(self) -> dict:
        """Return a snapshot of all active producers for debugging."""
        now = time.monotonic()
        producers = {}
        for pid, entry in self._streams.items():
            producers[pid] = {
                "alive": entry.alive,
                "viewers": entry.viewer_count,
                "params": entry.params_key,
                "idle_seconds": round(now - entry.last_accessed, 1),
                "frames_produced": entry.frame_seq,
            }
        return {"producer_count": len(self._streams), "producers": producers}


# Shared stream hub instance — one source per printer, many viewers
_hub = SharedStreamHub()


async def generate_chamber_mjpeg_stream(
    ip_address: str,
    access_code: str,
    model: str | None,
    fps: int = 5,
    stream_id: str | None = None,
    printer_id: int | None = None,
    raw: bool = False,
) -> AsyncGenerator[bytes, None]:
    """Generate MJPEG stream from A1/P1 printer using chamber image protocol.

    This connects to port 6000 and reads JPEG frames using the Bambu binary protocol.
    """
    logger.info("Starting chamber image stream for %s (stream_id=%s, model=%s)", ip_address, stream_id, model)

    connection = await generate_chamber_image_stream(ip_address, access_code)
    if connection is None:
        logger.error("Failed to connect to chamber image stream for %s", ip_address)
        if not raw:
            yield (
                b"--frame\r\n"
                b"Content-Type: text/plain\r\n\r\n"
                b"Error: Camera connection failed. Check printer is on and camera is enabled.\r\n"
            )
        return

    reader, writer = connection

    # Track active connection for cleanup
    if stream_id:
        _active_chamber_streams[stream_id] = (reader, writer)

    try:
        frame_interval = 1.0 / fps if fps > 0 else 0.2
        last_frame_time = 0.0
        consecutive_timeouts = 0

        while True:
            # Read next frame
            try:
                frame = await read_next_chamber_frame(reader, timeout=30.0)
            except ChamberConnectionClosed as e:
                logger.warning("Chamber image stream broken for %s: %s", stream_id, e)
                break

            if frame is None:
                # Timeout — retry a few times before giving up
                consecutive_timeouts += 1
                if consecutive_timeouts >= 3:
                    logger.warning("Chamber image stream stalled for %s (%d timeouts)", stream_id, consecutive_timeouts)
                    break
                continue
            consecutive_timeouts = 0

            # Track timestamp for stall detection; frame is served via hub for snapshots
            if printer_id is not None:
                _last_frame_times[printer_id] = time.monotonic()

            # Rate limiting - skip frames if needed to maintain target FPS
            current_time = time.monotonic()
            if current_time - last_frame_time < frame_interval:
                continue
            last_frame_time = current_time

            if raw:
                yield frame
            else:
                # Yield frame in MJPEG format — separate chunks to avoid copying frame
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode() + b"\r\n"
                    b"\r\n"
                )
                yield frame
                yield b"\r\n"

    except asyncio.CancelledError:
        logger.info("Chamber image stream cancelled (stream_id=%s)", stream_id)
        raise
    except GeneratorExit:
        logger.info("Chamber image stream generator exit (stream_id=%s)", stream_id)
        raise
    except Exception as e:
        logger.exception("Chamber image stream error: %s", e)
    finally:
        # Remove from active streams
        if stream_id and stream_id in _active_chamber_streams:
            del _active_chamber_streams[stream_id]

        # Clean up frame buffer and timestamps
        if printer_id is not None:
            _last_frames.pop(printer_id, None)
            _last_frame_times.pop(printer_id, None)
            _stream_start_times.pop(printer_id, None)

        # Close the connection
        try:
            writer.close()
            await writer.wait_closed()
        except OSError:
            pass  # Connection already closed or broken; cleanup is best-effort
        logger.info("Chamber image stream stopped for %s (stream_id=%s)", ip_address, stream_id)


async def generate_rtsp_mjpeg_stream(
    ip_address: str,
    access_code: str,
    model: str | None,
    fps: int = 10,
    stream_id: str | None = None,
    printer_id: int | None = None,
    raw: bool = False,
    quality: int = 5,
    scale: float = 1.0,
    threads: int = 0,
    gpu_accel: bool = False,
) -> AsyncGenerator[bytes, None]:
    """Generate MJPEG stream from printer camera using ffmpeg/RTSP.

    This is for X1/H2/P2 models that support RTSP streaming.
    """
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        logger.error("ffmpeg not found - camera streaming requires ffmpeg")
        if not raw:
            yield (b"--frame\r\nContent-Type: text/plain\r\n\r\nError: ffmpeg not installed\r\n")
        return

    port = get_camera_port(model)
    camera_url = f"rtsps://bblp:{access_code}@{ip_address}:{port}/streaming/live/1"

    # ffmpeg command to output MJPEG stream to stdout
    # -rtsp_transport tcp: Use TCP for reliability
    # -rtsp_flags prefer_tcp: Prefer TCP for RTSP
    # -timeout: Connection timeout in microseconds (30 seconds)
    # -buffer_size: Larger buffer for network jitter
    # -max_delay: Maximum demuxing delay
    # -f mjpeg: Output as MJPEG
    # -q:v: Quality (2=best, 31=worst). Default 5 for full view, 15+ for grid thumbnails
    # -r: Output framerate
    # -vf scale: Downscale for grid view to save bandwidth
    if not math.isfinite(quality) or not math.isfinite(scale) or not math.isfinite(fps):
        logger.warning("Non-finite stream parameter: fps=%s quality=%s scale=%s", fps, quality, scale)
        if not raw:
            yield (b"--frame\r\nContent-Type: text/plain\r\n\r\nError: invalid parameters\r\n")
        return
    quality = max(2, min(quality, 31))
    scale = max(0.1, min(scale, 1.0))

    vf_filters = []
    if scale < 1.0:
        vf_filters.append(f"scale=iw*{scale}:ih*{scale}")

    cmd = [ffmpeg]
    if gpu_accel:
        cmd.extend(["-hwaccel", "auto"])
    cmd.extend([
        "-rtsp_transport",
        "tcp",
        "-rtsp_flags",
        "prefer_tcp",
        "-timeout",
        "30000000",  # 30 seconds in microseconds
        "-buffer_size",
        "1024000",  # 1MB buffer
        "-max_delay",
        "500000",  # 0.5 seconds max delay
        "-i",
        camera_url,
    ])
    if vf_filters:
        cmd.extend(["-vf", ",".join(vf_filters)])
    output_args = [
        "-f",
        "mjpeg",
        "-q:v",
        str(quality),
        "-r",
        str(fps),
        "-an",  # No audio
    ]
    if threads > 0:
        output_args.extend(["-threads", str(threads)])
    output_args.append("-")  # Output to stdout
    cmd.extend(output_args)

    logger.info(
        "Starting RTSP camera stream for %s (stream_id=%s, model=%s, fps=%s)", ip_address, stream_id, model, fps
    )
    logger.debug("ffmpeg command: %s ... (url hidden)", ffmpeg)

    semaphore = get_rtsp_semaphore()
    process = None

    # Acquire semaphore only for process creation + liveness check
    logger.debug("Waiting for RTSP semaphore (stream_id=%s)", stream_id)
    async with semaphore:
        logger.debug("Acquired RTSP semaphore (stream_id=%s)", stream_id)
        try:
            kwargs: dict = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs,
            )

            # Track active process for cleanup
            if stream_id:
                _active_streams[stream_id] = process
                _spawned_ffmpeg_pids[process.pid] = time.monotonic()

            # Give ffmpeg a moment to start and check for immediate failures
            await asyncio.sleep(0.5)
            if process.returncode is not None:
                stderr = await process.stderr.read()
                logger.error("ffmpeg failed immediately: %s", re.sub(r"bblp:[^@]*@", "bblp:***@", stderr.decode()))
                if stream_id:
                    _active_streams.pop(stream_id, None)
                    _spawned_ffmpeg_pids.pop(process.pid, None)
                if not raw:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: text/plain\r\n\r\n"
                        b"Error: Camera connection failed. Check printer is on and camera is enabled.\r\n"
                    )
                return
        except FileNotFoundError:
            logger.error("ffmpeg not found - camera streaming requires ffmpeg")
            if not raw:
                yield (b"--frame\r\nContent-Type: text/plain\r\n\r\nError: ffmpeg not installed\r\n")
            return

    # Semaphore released — streaming loop runs without holding it
    try:
        # Read JPEG frames from ffmpeg output
        # JPEG images start with 0xFFD8 and end with 0xFFD9
        buffer = bytearray()
        jpeg_start = b"\xff\xd8"
        jpeg_end = b"\xff\xd9"

        while True:
            try:
                # Read chunk from ffmpeg — larger reads reduce syscalls
                chunk = await asyncio.wait_for(process.stdout.read(65536), timeout=30.0)

                if not chunk:
                    logger.warning("Camera stream ended (no more data)")
                    break

                buffer.extend(chunk)

                if len(buffer) > _RTSP_BUFFER_LIMIT:
                    logger.error(
                        "RTSP buffer exceeded %d bytes — dropping (stream_id=%s)", _RTSP_BUFFER_LIMIT, stream_id
                    )
                    break

                # Find complete JPEG frames in buffer
                while True:
                    start_idx = buffer.find(jpeg_start)
                    if start_idx == -1:
                        # No start marker, keep last byte (could be 0xFF)
                        del buffer[: max(0, len(buffer) - 1)]
                        break

                    # Trim anything before the start marker
                    if start_idx > 0:
                        del buffer[:start_idx]

                    end_idx = buffer.find(jpeg_end, 2)  # Skip first 2 bytes
                    if end_idx == -1:
                        break

                    # Extract complete frame as immutable bytes
                    frame = bytes(buffer[: end_idx + 2])
                    del buffer[: end_idx + 2]

                    # Track timestamp for stall detection
                    if printer_id is not None:
                        _last_frame_times[printer_id] = time.monotonic()

                    if raw:
                        yield frame
                    else:
                        yield (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            b"Content-Length: " + str(len(frame)).encode() + b"\r\n"
                            b"\r\n"
                        )
                        yield frame
                        yield b"\r\n"

            except TimeoutError:
                logger.warning("Camera stream read timeout")
                break
            except asyncio.CancelledError:
                logger.info("Camera stream cancelled (stream_id=%s)", stream_id)
                raise
            except GeneratorExit:
                logger.info("Camera stream generator exit (stream_id=%s)", stream_id)
                raise

    except asyncio.CancelledError:
        logger.info("Camera stream task cancelled (stream_id=%s)", stream_id)
        raise
    except GeneratorExit:
        logger.info("Camera stream generator closed (stream_id=%s)", stream_id)
        raise
    except Exception as e:
        logger.exception("Camera stream error: %s", e)
    finally:
        # Remove from active streams
        if stream_id and stream_id in _active_streams:
            del _active_streams[stream_id]

        # Clean up frame buffer and timestamps
        if printer_id is not None:
            _last_frames.pop(printer_id, None)
            _last_frame_times.pop(printer_id, None)
            _stream_start_times.pop(printer_id, None)

        if process and process.returncode is None:
            logger.info("Terminating ffmpeg process for stream %s", stream_id)
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except TimeoutError:
                logger.warning("ffmpeg didn't terminate gracefully, killing (stream_id=%s)", stream_id)
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass  # Process already dead
            except OSError as e:
                logger.warning("Error terminating ffmpeg: %s", e)

        if process:
            _spawned_ffmpeg_pids.pop(process.pid, None)
        logger.info("Camera stream stopped for %s (stream_id=%s)", ip_address, stream_id)


async def _ensure_producer(
    printer_id: int,
    db: AsyncSession,
    fps: int,
    quality: int,
    scale: float,
    printer: Printer | None = None,
    force_quality: bool = False,
    threads: int = 0,
    gpu_accel: bool = False,
) -> _SharedStream | None:
    """Start or reuse a shared producer for a single printer.

    Returns the _SharedStream entry, or None if the printer doesn't exist
    or has an external camera (not supported via the hub).

    Pass an already-fetched ``printer`` to skip the DB lookup.
    Set ``force_quality=True`` to restart the producer if params changed
    (used when a client explicitly switches quality).
    """
    # Fast path: if a producer is already alive and we're not forcing a
    # quality change, grab it directly (no DB query).  This is the common case
    # when multiple clients connect to the same camera grid.
    if not force_quality:
        existing = await _hub.get_existing(printer_id)
        if existing is not None:
            return existing

    if printer is None:
        result = await db.execute(select(Printer).where(Printer.id == printer_id))
        printer = result.scalar_one_or_none()
    if not printer:
        return None

    # External cameras are not supported in the multiplexed stream
    if printer.external_camera_enabled and printer.external_camera_url:
        return None

    if is_chamber_image_model(printer.model):
        fps_clamped = min(max(fps, 1), 5)
        stream_generator = generate_chamber_mjpeg_stream
    else:
        fps_clamped = min(max(fps, 1), 30)
        stream_generator = generate_rtsp_mjpeg_stream

    stream_id = f"{printer_id}-{uuid.uuid4().hex[:8]}"
    gen_kwargs: dict = {
        "ip_address": printer.ip_address,
        "access_code": printer.access_code,
        "model": printer.model,
        "fps": fps_clamped,
        "stream_id": stream_id,
        "printer_id": printer_id,
        "raw": True,
    }
    if stream_generator is generate_rtsp_mjpeg_stream:
        gen_kwargs["quality"] = quality
        gen_kwargs["scale"] = scale
        gen_kwargs["threads"] = threads
        gen_kwargs["gpu_accel"] = gpu_accel

    def starter_fn():
        return stream_generator(**gen_kwargs)

    params_key = f"{fps_clamped}-{quality}-{scale}-{threads}-{gpu_accel}"
    _stream_start_times[printer_id] = time.monotonic()
    if force_quality:
        return await _hub.restart(printer_id, starter_fn, params_key=params_key)
    return await _hub.get_or_start(printer_id, starter_fn, params_key=params_key)


@router.get("/camera/grid-stream")
async def camera_grid_stream(
    request: Request,
    ids: str = Query(..., description="Comma-separated printer IDs"),
    fps: int | None = Query(default=None, ge=1, le=30),
    quality: int | None = Query(default=None, ge=2, le=31),
    scale: float | None = Query(default=None, ge=0.1, le=1.0),
    force: bool = Query(False, description="Force restart producers with new quality settings"),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Multiplexed camera stream for the camera grid.

    Streams JPEG frames for multiple printers over a SINGLE HTTP connection
    using a binary framing protocol.  This avoids the browser's
    6-connection-per-origin limit that makes the page unresponsive when many
    cameras are open.

    Binary frame format (little-endian):
        [4 bytes: printer_id][4 bytes: jpeg_length][jpeg_data]

    The frontend reads this stream with one fetch() and demuxes frames to
    the correct <canvas> element by printer ID.
    """
    # Resolve quality preset from DB when no explicit params provided
    threads = 0
    gpu_accel = False
    if fps is None and quality is None and scale is None:
        from backend.app.api.routes.settings import get_setting

        raw_preset = await get_setting(db, "camera_quality") or "auto"
        printer_count = (await db.execute(select(func.count()).select_from(Printer).where(Printer.is_active == True))).scalar() or 1  # noqa: E712
        preset_name = await resolve_camera_quality(raw_preset, printer_count)
        preset = CAMERA_QUALITY_PRESETS.get(preset_name, CAMERA_QUALITY_PRESETS["medium"])
        fps = preset["grid"]["fps"]
        quality = preset["grid"]["quality"]
        scale = preset["grid"]["scale"]
        threads = preset["threads"]
        gpu_accel = (await get_setting(db, "camera_gpu_accel") or "false").lower() == "true"
        force = True  # Ensure producers match preset params
    else:
        fps = fps or 5
        quality = quality or 15
        scale = scale or 0.5

    # Parse printer IDs
    try:
        printer_ids = [int(x.strip()) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "ids must be comma-separated integers")

    if not printer_ids:
        raise HTTPException(400, "No printer IDs provided")

    # Deduplicate while preserving order
    printer_ids = list(dict.fromkeys(printer_ids))

    if len(printer_ids) > 30:
        raise HTTPException(400, "Maximum 30 printers per grid stream")

    # Start producers for all requested printers.
    # First, collect IDs that already have a live producer (fast path — no DB).
    entries, need_db = await _hub.get_existing_batch(printer_ids)

    # Single batch DB query for printers that need a new producer.
    if need_db:
        result = await db.execute(select(Printer).where(Printer.id.in_(need_db)))
        printers_by_id = {p.id: p for p in result.scalars().all()}
        for pid in need_db:
            printer = printers_by_id.get(pid)
            if printer is None:
                continue
            entry = await _ensure_producer(pid, db, fps, quality, scale, printer=printer, force_quality=force, threads=threads, gpu_accel=gpu_accel)
            if entry is not None:
                entries[pid] = entry

    if not entries:
        raise HTTPException(404, "No valid printers found")

    async def generate():
        """Round-robin across all printers, yielding binary-framed JPEG data."""
        frame_interval = 1.0 / fps
        # Track last seen sequence per printer to avoid sending duplicates
        seen_seqs: dict[int, int] = dict.fromkeys(entries, 0)

        # Register as viewer on all entries
        for entry in entries.values():
            entry.viewer_count += 1
        # Snapshot for finally — exactly one decrement per entry we incremented,
        # regardless of entries popped mid-loop or CancelledError timing.
        registered_entries = list(entries.values())

        try:
            last_disconnect_check = 0.0
            while True:
                now = time.monotonic()
                # Throttle disconnect check to once per second
                if now - last_disconnect_check > 1.0:
                    if await request.is_disconnected():
                        break
                    last_disconnect_check = now

                sent_any = False
                for pid, entry in list(entries.items()):
                    if not entry.alive:
                        # Producer died — remove from rotation (finally handles decrement)
                        entries.pop(pid, None)
                        continue

                    # Touch last_accessed so the producer stays alive
                    entry.last_accessed = now

                    seq = entry.frame_seq
                    frame = entry.frame
                    if seq <= seen_seqs.get(pid, 0):
                        continue

                    if frame is None:
                        continue

                    seen_seqs[pid] = seq

                    # Binary header: [printer_id u32 LE][length u32 LE]
                    header = struct.pack("<II", pid, len(frame))
                    yield header + frame
                    sent_any = True

                if not entries:
                    break

                if sent_any:
                    await asyncio.sleep(0.001)
                else:
                    wait_tasks = [asyncio.ensure_future(e.frame_event.wait()) for e in entries.values()]
                    try:
                        _, pending = await asyncio.wait(
                            wait_tasks, timeout=frame_interval, return_when=asyncio.FIRST_COMPLETED
                        )
                        for t in pending:
                            t.cancel()
                    except asyncio.CancelledError:
                        for t in wait_tasks:
                            t.cancel()
                        raise
        finally:
            # Decrement viewer count on all entries we registered with
            for entry in registered_entries:
                entry.viewer_count -= 1
                if entry.viewer_count < 0:
                    entry.viewer_count = 0

    return StreamingResponse(
        generate(),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{printer_id}/camera/stream")
async def camera_stream(
    printer_id: int,
    request: Request,
    fps: int | None = Query(default=None, ge=1, le=30),
    quality: int | None = Query(default=None, ge=2, le=31),
    scale: float | None = Query(default=None, ge=0.1, le=1.0),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Stream live video from printer camera as MJPEG.

    This endpoint returns a multipart MJPEG stream consumed via fetch() + auth
    headers and rendered to a canvas element.

    Uses external camera if configured, otherwise uses built-in camera:
    - External: MJPEG, RTSP, or HTTP snapshot
    - A1/P1: Chamber image protocol (port 6000)
    - X1/H2/P2: RTSP via ffmpeg (port 322)

    Args:
        printer_id: Printer ID
        fps: Target frames per second (default: 10, max: 30)
    """
    # Resolve quality preset from DB when no explicit params provided
    threads = 0
    gpu_accel = False
    if fps is None and quality is None and scale is None:
        from backend.app.api.routes.settings import get_setting

        raw_preset = await get_setting(db, "camera_quality") or "auto"
        printer_count = (await db.execute(select(func.count()).select_from(Printer).where(Printer.is_active == True))).scalar() or 1  # noqa: E712
        preset_name = await resolve_camera_quality(raw_preset, printer_count)
        preset = CAMERA_QUALITY_PRESETS.get(preset_name, CAMERA_QUALITY_PRESETS["medium"])
        fps = preset["single"]["fps"]
        quality = preset["single"]["quality"]
        scale = preset["single"]["scale"]
        threads = preset["threads"]
        gpu_accel = (await get_setting(db, "camera_gpu_accel") or "false").lower() == "true"
    else:
        fps = fps or 10
        quality = quality or 5
        scale = scale or 1.0

    printer = await get_printer_or_404(printer_id, db)

    # Check for external camera first
    if printer.external_camera_enabled and printer.external_camera_url:
        from backend.app.services.external_camera import generate_mjpeg_stream

        # Limit external camera FPS to reduce browser load
        fps = min(max(fps, 1), 15)
        logger.info(
            "Using external camera (%s) for printer %s at %s fps", printer.external_camera_type, printer_id, fps
        )

        # Track stream start
        _stream_start_times[printer_id] = time.monotonic()
        _active_external_streams.add(printer_id)

        async def external_stream_wrapper():
            """Wrap external stream to track start/stop and update frame times."""
            frame_interval = 1.0 / fps
            last_yield_time = 0.0
            try:
                async for frame in generate_mjpeg_stream(
                    printer.external_camera_url, printer.external_camera_type, fps
                ):
                    # Rate limit to prevent overwhelming browser
                    current_time = time.monotonic()
                    elapsed = current_time - last_yield_time
                    if elapsed < frame_interval:
                        await asyncio.sleep(frame_interval - elapsed)
                    last_yield_time = time.monotonic()
                    _last_frame_times[printer_id] = last_yield_time
                    yield frame
            finally:
                _active_external_streams.discard(printer_id)
                logger.info("External camera stream ended for printer %s", printer_id)

        return StreamingResponse(
            external_stream_wrapper(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    # Clamp fps for viewer rate limiting (producer uses its own clamped value)
    if is_chamber_image_model(printer.model):
        fps = min(max(fps, 1), 5)
    else:
        fps = min(max(fps, 1), 30)

    # Reuse the shared producer start logic (skips external-camera check
    # since we already handled it above).
    entry = await _ensure_producer(printer_id, db, fps, quality, scale, printer=printer, threads=threads, gpu_accel=gpu_accel)
    if entry is None:
        raise HTTPException(503, "Failed to start camera stream")
    viewer = _hub.make_viewer(entry, fps)

    # Wrap with disconnect detection so the response stops promptly when
    # the client goes away (page refresh, tab close).  Without this,
    # Starlette keeps iterating the viewer and buffering frames for a dead
    # connection, blocking the event loop and exhausting connection slots.
    async def with_disconnect_check():
        async for chunk in viewer:
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        with_disconnect_check(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.post("/{printer_id}/camera/stop")
async def stop_camera_stream(
    printer_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Hint that a single viewer has disconnected.

    The shared producer (SharedStreamHub) is NOT stopped here — it auto-stops
    after 30 s with no active viewers.  Killing the shared producer would
    break other viewers (grid on another computer, embedded viewer, etc.).

    Only non-shared resources (chamber image TCP connections) are cleaned up
    explicitly.

    POST only (sendBeacon compatibility).
    """
    stopped = 0

    # Stop ffmpeg/RTSP streams — skip hub-owned processes (hub manages its own lifecycle)
    hub_owns_printer = _hub.is_active(printer_id)
    to_remove = []
    for stream_id, process in list(_active_streams.items()):
        if stream_id.startswith(f"{printer_id}-"):
            if hub_owns_printer:
                continue
            to_remove.append(stream_id)
            if process.returncode is None:
                try:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=2.0)
                    except TimeoutError:
                        logger.warning("ffmpeg didn't terminate gracefully, killing (stream_id=%s)", stream_id)
                        process.kill()
                        await process.wait()
                    stopped += 1
                    logger.info("Terminated ffmpeg process for stream %s", stream_id)
                except ProcessLookupError:
                    pass  # Process already dead
                except OSError as e:
                    logger.warning("Error stopping stream %s: %s", stream_id, e)
            _spawned_ffmpeg_pids.pop(process.pid, None)

    for stream_id in to_remove:
        _active_streams.pop(stream_id, None)

    # Stop chamber image streams
    # Clean up chamber image TCP connections (these are per-client, not shared)
    to_remove_chamber = []
    for stream_id, (_reader, writer) in list(_active_chamber_streams.items()):
        if stream_id.startswith(f"{printer_id}-"):
            to_remove_chamber.append(stream_id)
            try:
                writer.close()
                stopped += 1
                logger.info("Closed chamber image connection for stream %s", stream_id)
            except OSError as e:
                logger.warning("Error stopping chamber stream %s: %s", stream_id, e)

    for stream_id in to_remove_chamber:
        _active_chamber_streams.pop(stream_id, None)

    # NOTE: We intentionally do NOT call _hub.stop() here.  The shared
    # producer manages its own lifecycle via an idle timeout (30 s with
    # no viewer polling).  Killing it on a single viewer disconnect would
    # tear down the ffmpeg process that other viewers are still using,
    # causing unnecessary process churn and visual glitches.

    logger.info(
        "Camera stop hint for printer %s (cleaned %s chamber conn, hub active=%s)",
        printer_id,
        stopped,
        _hub.is_active(printer_id),
    )
    return {"stopped": stopped}


@router.get("/{printer_id}/camera/snapshot")
async def camera_snapshot(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Capture a single frame from the printer camera.

    Returns a JPEG image.
    """
    import tempfile
    from pathlib import Path

    printer = await get_printer_or_404(printer_id, db)

    # Check for external camera first
    if printer.external_camera_enabled and printer.external_camera_url:
        from backend.app.services.external_camera import capture_frame

        frame_data = await capture_frame(printer.external_camera_url, printer.external_camera_type, timeout=15)
        if not frame_data:
            raise HTTPException(
                status_code=503,
                detail="Failed to capture frame from external camera.",
            )
        return Response(
            content=frame_data,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Content-Disposition": f'inline; filename="snapshot_{printer_id}.jpg"',
            },
        )

    # Create temporary file for the snapshot
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        temp_path = Path(f.name)

    try:
        success = await capture_camera_frame(
            ip_address=printer.ip_address,
            access_code=printer.access_code,
            model=printer.model,
            output_path=temp_path,
            timeout=15,
        )

        if not success:
            raise HTTPException(
                status_code=503,
                detail="Failed to capture camera frame. Ensure printer is on and camera is enabled.",
            )

        # Read and return the image
        with open(temp_path, "rb") as f:
            image_data = f.read()

        return Response(
            content=image_data,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Content-Disposition": f'inline; filename="snapshot_{printer_id}.jpg"',
            },
        )
    finally:
        # Clean up temp file
        if temp_path.exists():
            temp_path.unlink()


@router.get("/{printer_id}/camera/test")
async def test_camera(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Test camera connection for a printer.

    Returns success status and any error message.
    """
    printer = await get_printer_or_404(printer_id, db)

    result = await test_camera_connection(
        ip_address=printer.ip_address,
        access_code=printer.access_code,
        model=printer.model,
    )

    return result


@router.get("/{printer_id}/camera/status")
async def camera_status(
    printer_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Get the status of an active camera stream.

    Returns whether a stream is active and when the last frame was received.
    Used by the frontend to detect stalled streams and auto-reconnect.
    """
    # Check if there's an active stream for this printer
    # Check shared hub first (O(1) lookup) before falling back to linear scans
    has_active_stream = _hub.is_active(printer_id)

    # Check external camera streams
    if not has_active_stream and printer_id in _active_external_streams:
        has_active_stream = True

    # Check ffmpeg/RTSP streams
    if not has_active_stream:
        for stream_id in _active_streams:
            if stream_id.startswith(f"{printer_id}-"):
                process = _active_streams[stream_id]
                if process.returncode is None:
                    has_active_stream = True
                    break

    # Check chamber image streams
    if not has_active_stream:
        for stream_id in _active_chamber_streams:
            if stream_id.startswith(f"{printer_id}-"):
                has_active_stream = True
                break

    # Get timing information (all timestamps use time.monotonic())
    current_time = time.monotonic()
    last_frame_time = _last_frame_times.get(printer_id)
    stream_start_time = _stream_start_times.get(printer_id)

    # Calculate seconds since last frame
    seconds_since_frame = None
    if last_frame_time is not None:
        seconds_since_frame = current_time - last_frame_time

    # Calculate stream uptime
    stream_uptime = None
    if stream_start_time is not None:
        stream_uptime = current_time - stream_start_time

    return {
        "active": has_active_stream,
        "has_frames": printer_id in _last_frames or _hub.get_last_frame(printer_id) is not None,
        "seconds_since_frame": seconds_since_frame,
        "stream_uptime": stream_uptime,
        # Consider stalled if no frame for more than 10 seconds after stream started
        "stalled": (
            has_active_stream
            and stream_uptime is not None
            and stream_uptime > 5  # Give 5 seconds for stream to start
            and (seconds_since_frame is None or seconds_since_frame > 10)
        ),
    }


@router.get("/camera/hub-status")
async def camera_hub_status(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Debug endpoint: return the state of all shared camera producers.

    Shows how many ffmpeg/chamber producers are running, their viewer
    counts, idle times, and frame counters.
    """
    return _hub.status()


class ExternalCameraTestRequest(BaseModel):
    url: str
    camera_type: Literal["mjpeg", "rtsp", "snapshot", "usb"]


@router.post("/{printer_id}/camera/external/test")
async def test_external_camera(
    printer_id: int,
    body: ExternalCameraTestRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
):
    """Test external camera connection.

    Args:
        printer_id: Printer ID (for authorization)
        body: Request body with url and camera_type

    Returns:
        Dict with {success: bool, error?: str, resolution?: str}
    """
    # Verify printer exists (for authorization)
    await get_printer_or_404(printer_id, db)

    from backend.app.services.external_camera import test_connection

    return await test_connection(body.url, body.camera_type)


@router.get("/{printer_id}/camera/check-plate")
async def check_plate_empty(
    printer_id: int,
    plate_type: str | None = None,
    use_external: bool = False,
    include_debug_image: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Check if the build plate is empty using camera vision.

    Uses calibration-based difference detection - compares current frame
    to a reference image of the empty plate.

    IMPORTANT: Chamber light must be ON for reliable detection.

    Args:
        printer_id: Printer ID
        plate_type: Type of build plate (e.g., "High Temp Plate") for calibration lookup
        use_external: If True, prefer external camera over built-in
        include_debug_image: If True, return URL to annotated debug image

    Returns:
        Dict with detection results:
        - is_empty: bool - Whether plate appears empty
        - confidence: float - Confidence level (0.0 to 1.0)
        - difference_percent: float - How different from calibration reference
        - message: str - Human-readable result message
        - needs_calibration: bool - True if calibration is required
        - light_warning: bool - True if chamber light is off
    """
    from backend.app.services.plate_detection import (
        check_plate_empty as do_check,
        is_plate_detection_available,
    )
    from backend.app.services.printer_manager import printer_manager

    # Check printer exists first (before OpenCV check)
    printer = await get_printer_or_404(printer_id, db)

    if not is_plate_detection_available():
        raise HTTPException(
            status_code=503,
            detail="Plate detection not available. Install opencv-python-headless to enable.",
        )

    # Check chamber light status
    light_warning = False
    state = printer_manager.get_status(printer_id)
    if state and not state.chamber_light:
        light_warning = True

    from backend.app.services.plate_detection import PlateDetector

    # Build ROI tuple from printer settings if available
    roi = None
    if all(
        [
            printer.plate_detection_roi_x is not None,
            printer.plate_detection_roi_y is not None,
            printer.plate_detection_roi_w is not None,
            printer.plate_detection_roi_h is not None,
        ]
    ):
        roi = (
            printer.plate_detection_roi_x,
            printer.plate_detection_roi_y,
            printer.plate_detection_roi_w,
            printer.plate_detection_roi_h,
        )

    result = await do_check(
        printer_id=printer.id,
        ip_address=printer.ip_address,
        access_code=printer.access_code,
        model=printer.model,
        plate_type=plate_type,
        include_debug_image=include_debug_image,
        external_camera_url=printer.external_camera_url if printer.external_camera_enabled else None,
        external_camera_type=printer.external_camera_type if printer.external_camera_enabled else None,
        use_external=use_external,
        roi=roi,
    )

    # Get reference count for the response
    detector = PlateDetector()
    ref_count = detector.get_calibration_count(printer.id)

    response = result.to_dict()
    response["light_warning"] = light_warning
    response["reference_count"] = ref_count
    response["max_references"] = detector.MAX_REFERENCES
    # Include current ROI in response
    if roi:
        response["roi"] = {"x": roi[0], "y": roi[1], "w": roi[2], "h": roi[3]}
    else:
        # Return default ROI
        response["roi"] = {"x": 0.15, "y": 0.35, "w": 0.70, "h": 0.55}

    # If debug image requested and available, encode as base64 data URL
    if include_debug_image and result.debug_image:
        import base64

        b64_image = base64.b64encode(result.debug_image).decode("utf-8")
        response["debug_image_url"] = f"data:image/jpeg;base64,{b64_image}"

    return response


@router.post("/{printer_id}/camera/plate-detection/calibrate")
async def calibrate_plate_detection(
    printer_id: int,
    label: str | None = Query(default=None, max_length=200),
    use_external: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Calibrate plate detection by capturing a reference image of the empty plate.

    The plate MUST be empty when calling this endpoint. The captured image
    will be used as the reference for future detection comparisons.

    Supports up to 5 reference images per printer. When adding a 6th, the oldest
    is automatically removed.

    IMPORTANT: Chamber light should be ON for calibration.

    Args:
        printer_id: Printer ID
        label: Optional label for this reference (e.g., "High Temp Plate", "Wham Bam")
        use_external: If True, prefer external camera over built-in

    Returns:
        Dict with:
        - success: bool - Whether calibration succeeded
        - message: str - Status message
        - index: int - The reference slot used (0-4)
    """
    from backend.app.services.plate_detection import (
        calibrate_plate,
        is_plate_detection_available,
    )
    from backend.app.services.printer_manager import printer_manager

    # Check printer exists first (before OpenCV check)
    printer = await get_printer_or_404(printer_id, db)

    if not is_plate_detection_available():
        raise HTTPException(
            status_code=503,
            detail="Plate detection not available. Install opencv-python-headless to enable.",
        )

    # Check chamber light - warn but don't block
    state = printer_manager.get_status(printer_id)
    light_warning = state and not state.chamber_light

    success, message, index = await calibrate_plate(
        printer_id=printer.id,
        ip_address=printer.ip_address,
        access_code=printer.access_code,
        model=printer.model,
        label=label,
        external_camera_url=printer.external_camera_url if printer.external_camera_enabled else None,
        external_camera_type=printer.external_camera_type if printer.external_camera_enabled else None,
        use_external=use_external,
    )

    if light_warning and success:
        message += " (Warning: Chamber light was off)"

    return {"success": success, "message": message, "index": index}


@router.delete("/{printer_id}/camera/plate-detection/calibrate")
async def delete_plate_calibration(
    printer_id: int,
    plate_type: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Delete the plate detection calibration for a printer and plate type.

    Args:
        printer_id: Printer ID
        plate_type: Type of build plate (if None, deletes legacy non-plate-specific calibration)

    Returns:
        Dict with:
        - success: bool - Whether deletion succeeded
        - message: str - Status message
    """
    from backend.app.services.plate_detection import (
        delete_calibration,
        is_plate_detection_available,
    )

    # Verify printer exists first (before OpenCV check)
    await get_printer_or_404(printer_id, db)

    if not is_plate_detection_available():
        raise HTTPException(
            status_code=503,
            detail="Plate detection not available. Install opencv-python-headless to enable.",
        )

    deleted = delete_calibration(printer_id, plate_type)
    plate_msg = f" for '{plate_type}'" if plate_type else ""

    return {
        "success": deleted,
        "message": f"Calibration deleted{plate_msg}" if deleted else f"No calibration found{plate_msg}",
    }


@router.get("/{printer_id}/camera/plate-detection/status")
async def get_plate_detection_status(
    printer_id: int,
    plate_type: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Check plate detection status for a printer and plate type.

    Returns:
        Dict with:
        - available: bool - Whether OpenCV is installed
        - calibrated: bool - Whether printer has calibration for this plate type
        - plate_type: str - The plate type queried
        - chamber_light: bool - Whether chamber light is on
        - message: str - Status message
    """
    from backend.app.services.plate_detection import (
        get_calibration_status,
        is_plate_detection_available,
    )
    from backend.app.services.printer_manager import printer_manager

    # Verify printer exists first (before OpenCV check)
    await get_printer_or_404(printer_id, db)

    if not is_plate_detection_available():
        return {
            "available": False,
            "calibrated": False,
            "plate_type": plate_type,
            "chamber_light": False,
            "message": "OpenCV not installed",
        }

    # Get chamber light status
    state = printer_manager.get_status(printer_id)
    chamber_light = state.chamber_light if state else False

    status = get_calibration_status(printer_id, plate_type)
    status["chamber_light"] = chamber_light

    return status


@router.get("/{printer_id}/camera/plate-detection/references")
async def get_plate_references(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Get all calibration references for a printer with metadata.

    Returns list of references with index, label, timestamp, and thumbnail URL.
    """
    from backend.app.services.plate_detection import PlateDetector, is_plate_detection_available

    # Verify printer exists first (before OpenCV check)
    await get_printer_or_404(printer_id, db)

    if not is_plate_detection_available():
        raise HTTPException(503, "Plate detection not available")

    detector = PlateDetector()
    references = detector.get_references(printer_id)

    # Add thumbnail URLs
    for ref in references:
        ref["thumbnail_url"] = (
            f"/api/v1/printers/{printer_id}/camera/plate-detection/references/{ref['index']}/thumbnail"
        )

    return {
        "references": references,
        "max_references": detector.MAX_REFERENCES,
    }


@router.get("/{printer_id}/camera/plate-detection/references/{index}/thumbnail")
async def get_reference_thumbnail(
    printer_id: int,
    index: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Get thumbnail image for a calibration reference."""
    from fastapi.responses import Response

    from backend.app.services.plate_detection import PlateDetector, is_plate_detection_available

    # Verify printer exists first (before OpenCV check)
    await get_printer_or_404(printer_id, db)

    if not is_plate_detection_available():
        raise HTTPException(503, "Plate detection not available")

    detector = PlateDetector()
    thumbnail = detector.get_reference_thumbnail(printer_id, index)

    if thumbnail is None:
        raise HTTPException(404, "Reference not found")

    return Response(content=thumbnail, media_type="image/jpeg")


@router.put("/{printer_id}/camera/plate-detection/references/{index}")
async def update_reference_label(
    printer_id: int,
    index: int,
    label: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Update the label for a calibration reference."""
    from backend.app.services.plate_detection import PlateDetector, is_plate_detection_available

    # Verify printer exists first (before OpenCV check)
    await get_printer_or_404(printer_id, db)

    if not is_plate_detection_available():
        raise HTTPException(503, "Plate detection not available")

    detector = PlateDetector()
    success = detector.update_reference_label(printer_id, index, label)

    if not success:
        raise HTTPException(404, "Reference not found")

    return {"success": True, "index": index, "label": label}


@router.delete("/{printer_id}/camera/plate-detection/references/{index}")
async def delete_reference(
    printer_id: int,
    index: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Delete a specific calibration reference."""
    from backend.app.services.plate_detection import PlateDetector, is_plate_detection_available

    # Verify printer exists first (before OpenCV check)
    await get_printer_or_404(printer_id, db)

    if not is_plate_detection_available():
        raise HTTPException(503, "Plate detection not available")

    detector = PlateDetector()
    success = detector.delete_reference(printer_id, index)

    if not success:
        raise HTTPException(404, "Reference not found")

    return {"success": True, "message": "Reference deleted"}


def _scan_bambu_ffmpeg_pids() -> list[int]:
    """Scan /proc for ffmpeg processes with Bambu RTSP URLs.

    These are definitely ours — no other software connects to rtsps://bblp:.
    This catches orphans that survive app restarts and are not in any tracking dict.
    """
    import os

    pids = []
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as f:
                    cmdline = f.read()
                if b"ffmpeg" in cmdline and b"rtsps://bblp:" in cmdline:
                    pids.append(int(entry))
            except (OSError, PermissionError, ValueError):
                continue
    except OSError:
        pass
    return pids


def _sync_cleanup_orphaned_pids(active_pids: set[int], tracked_pids: list[int]) -> tuple[list[int], list[int]]:
    """Scan /proc and check tracked PIDs — runs in thread pool to avoid blocking the event loop.

    Returns (killed_pids, dead_pids) so the caller can update module-level dicts back on the event loop.
    """
    import os
    import signal

    killed: list[int] = []
    # Layer 1: /proc scan — kill orphaned Bambu ffmpeg processes
    for pid in _scan_bambu_ffmpeg_pids():
        if pid in active_pids:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except (ProcessLookupError, OSError):
            pass

    # Layer 2: check tracked PIDs for dead processes
    dead: list[int] = []
    for pid in tracked_pids:
        try:
            os.kill(pid, 0)  # existence check
        except ProcessLookupError:
            dead.append(pid)
        except PermissionError:
            pass  # Process exists, owned by different user

    return killed, dead


async def cleanup_orphaned_streams():
    """Clean up orphaned ffmpeg processes and stale stream entries.

    Called periodically from the background task loop in main.py.

    Five-layer cleanup:
    1. /proc scan — finds ALL Bambu ffmpeg processes on the system, even those
       from previous app sessions. This is the nuclear safety net.
    2. _spawned_ffmpeg_pids — tracks PIDs spawned this session, catches orphans
       that were removed from _active_streams but not killed.
    3. _active_streams — kills stale entries with no recent frames.

    Layers 1-2 run in a thread pool to avoid blocking the event loop with
    /proc reads and os.kill() syscalls.
    """
    import time

    cleaned = 0
    now = time.monotonic()

    # Collect PIDs that are legitimately in-use (active stream, process alive)
    active_pids = {proc.pid for proc in _active_streams.values() if proc.returncode is None}

    # Layers 1-2: offload blocking /proc scan + os.kill() to thread pool
    loop = asyncio.get_running_loop()
    killed_pids, dead_pids = await loop.run_in_executor(
        None, _sync_cleanup_orphaned_pids, active_pids, list(_spawned_ffmpeg_pids)
    )

    for pid in killed_pids:
        logger.info("Killing orphaned ffmpeg process found via /proc (pid=%d)", pid)
        _spawned_ffmpeg_pids.pop(pid, None)
        cleaned += 1

    for pid in dead_pids:
        _spawned_ffmpeg_pids.pop(pid, None)

    # 3. Clean up _active_streams entries with dead processes
    dead_streams = [sid for sid, proc in _active_streams.items() if proc.returncode is not None]
    for sid in dead_streams:
        proc = _active_streams.pop(sid, None)
        if proc:
            _spawned_ffmpeg_pids.pop(proc.pid, None)
        cleaned += 1

    # 4. Kill stale active streams (alive but no frames for >60s)
    for sid, proc in list(_active_streams.items()):
        if proc.returncode is not None:
            continue
        try:
            printer_id = int(sid.split("-", 1)[0])
        except (ValueError, IndexError):
            continue
        start_time = _stream_start_times.get(printer_id, now)
        last_frame = _last_frame_times.get(printer_id, start_time)
        if now - start_time > 120 and now - last_frame > 60:
            logger.info("Killing stale ffmpeg stream %s (no frames for %.0fs)", sid, now - last_frame)
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
            _active_streams.pop(sid, None)
            _spawned_ffmpeg_pids.pop(proc.pid, None)
            cleaned += 1

    # 5. Clean stale chamber stream entries
    dead_chamber = [sid for sid, (_reader, writer) in _active_chamber_streams.items() if writer.is_closing()]
    for sid in dead_chamber:
        _active_chamber_streams.pop(sid, None)
        cleaned += 1

    if cleaned:
        logger.info("Cleaned up %d orphaned camera stream(s)", cleaned)
