"""Camera streaming API endpoints for Bambu Lab printers."""

import asyncio
import logging
import struct
import time
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
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
    is_chamber_image_model,
    read_next_chamber_frame,
    test_camera_connection,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/printers", tags=["camera"])

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

# Max age for stale frame buffer entries (5 minutes)
_FRAME_BUFFER_MAX_AGE = 300.0
_CLEANUP_INTERVAL = 60.0  # seconds between periodic cleanup runs
_cleanup_task: asyncio.Task | None = None


def _cleanup_stale_frame_buffers() -> None:
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


async def _periodic_cleanup_loop() -> None:
    """Background task that runs stale frame buffer cleanup on a fixed interval."""
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL)
        _cleanup_stale_frame_buffers()


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

    Returns the JPEG frame data if available, or None if no active stream.
    """
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
    """

    __slots__ = ("frame", "frame_seq", "task", "error", "alive", "last_accessed", "params_key")

    def __init__(self, params_key: str = "") -> None:
        self.frame: bytes | None = None
        self.frame_seq: int = 0
        self.task: asyncio.Task | None = None
        self.error: str | None = None
        self.alive: bool = True
        self.last_accessed: float = time.monotonic()
        self.params_key: str = params_key  # e.g. "5-15-0.5" for fps-quality-scale


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
            logger.info("Started new producer for printer %s (params=%s)", printer_id, params_key)
            return entry

    async def restart(self, printer_id: int, starter_fn, params_key: str) -> _SharedStream:
        """Stop the existing producer and start a new one with different params.

        Called when a client explicitly changes quality settings.
        """
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
                if old.task:
                    old.task.cancel()
            entry = _SharedStream(params_key=params_key)
            self._streams[printer_id] = entry
            entry.task = asyncio.create_task(self._run_producer(printer_id, starter_fn, entry))
            logger.info("Started new producer for printer %s (params=%s)", printer_id, params_key)
            return entry

    def make_viewer(self, entry: _SharedStream, fps: int) -> AsyncGenerator[bytes, None]:
        """Create a viewer generator that polls the shared frame buffer.

        This generator has NO cleanup requirements.  When the HTTP response
        ends (client disconnect, CancelledError, GC), it simply stops
        iterating.  No locks, no unsubscribe, no aclose() needed.
        """

        async def _viewer():
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
                    await asyncio.sleep(0.05)
                    continue

                if frame is None:
                    break

                seen_seq = seq

                # Per-viewer rate limiting
                now = time.monotonic()
                if now - last_yield < frame_interval:
                    continue
                last_yield = now

                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode() + b"\r\n"
                    b"\r\n" + frame + b"\r\n"
                )

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
                # Auto-stop if no viewer has polled recently
                if time.monotonic() - entry.last_accessed > self.IDLE_TIMEOUT:
                    logger.info(
                        "Producer idle for printer %s (%.0fs), auto-stopping",
                        printer_id,
                        self.IDLE_TIMEOUT,
                    )
                    # Mark dead before breaking so get_or_start() won't hand out
                    # this dying entry to a new viewer during cleanup
                    entry.alive = False
                    break
        except asyncio.CancelledError:
            logger.info("Producer cancelled for printer %s", printer_id)
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
            logger.info("Producer for printer %s stopped and cleaned up", printer_id)

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
                await asyncio.wait_for(asyncio.shield(entry.task), timeout=5.0)
            except (asyncio.CancelledError, TimeoutError, Exception):
                pass  # Best effort — task will clean up on its own eventually
        return True

    def is_active(self, printer_id: int) -> bool:
        entry = self._streams.get(printer_id)
        return entry is not None and entry.alive


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

    connection = await generate_chamber_image_stream(ip_address, access_code, fps)
    if connection is None:
        logger.error("Failed to connect to chamber image stream for %s", ip_address)
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

            # Save frame to buffer for photo capture and track timestamp
            if printer_id is not None:
                _last_frames[printer_id] = frame
                _last_frame_times[printer_id] = time.monotonic()

            # Rate limiting - skip frames if needed to maintain target FPS
            current_time = time.monotonic()
            if current_time - last_frame_time < frame_interval:
                continue
            last_frame_time = current_time

            if raw:
                yield frame
            else:
                # Yield frame in MJPEG format
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode() + b"\r\n"
                    b"\r\n" + frame + b"\r\n"
                )

    except asyncio.CancelledError:
        logger.info("Chamber image stream cancelled (stream_id=%s)", stream_id)
    except GeneratorExit:
        logger.info("Chamber image stream generator exit (stream_id=%s)", stream_id)
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
) -> AsyncGenerator[bytes, None]:
    """Generate MJPEG stream from printer camera using ffmpeg/RTSP.

    This is for X1/H2/P2 models that support RTSP streaming.
    """
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        logger.error("ffmpeg not found - camera streaming requires ffmpeg")
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
    quality = max(2, min(quality, 31))
    scale = max(0.1, min(scale, 1.0))

    vf_filters = []
    if scale < 1.0:
        vf_filters.append(f"scale=iw*{scale}:ih*{scale}")

    cmd = [
        ffmpeg,
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
    ]
    if vf_filters:
        cmd.extend(["-vf", ",".join(vf_filters)])
    cmd.extend(
        [
            "-f",
            "mjpeg",
            "-q:v",
            str(quality),
            "-r",
            str(fps),
            "-an",  # No audio
            "-",  # Output to stdout
        ]
    )

    logger.info(
        "Starting RTSP camera stream for %s (stream_id=%s, model=%s, fps=%s)", ip_address, stream_id, model, fps
    )
    logger.debug("ffmpeg command: %s ... (url hidden)", ffmpeg)

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Track active process for cleanup
        if stream_id:
            _active_streams[stream_id] = process

        # Give ffmpeg a moment to start and check for immediate failures
        await asyncio.sleep(0.5)
        if process.returncode is not None:
            stderr = await process.stderr.read()
            logger.error("ffmpeg failed immediately: %s", stderr.decode())
            yield (
                b"--frame\r\n"
                b"Content-Type: text/plain\r\n\r\n"
                b"Error: Camera connection failed. Check printer is on and camera is enabled.\r\n"
            )
            return

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

                    # Save frame to buffer for photo capture and track timestamp
                    if printer_id is not None:
                        _last_frames[printer_id] = frame
                        _last_frame_times[printer_id] = time.monotonic()

                    if raw:
                        yield frame
                    else:
                        yield (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            b"Content-Length: " + str(len(frame)).encode() + b"\r\n"
                            b"\r\n" + frame + b"\r\n"
                        )

            except TimeoutError:
                logger.warning("Camera stream read timeout")
                break
            except asyncio.CancelledError:
                logger.info("Camera stream cancelled (stream_id=%s)", stream_id)
                break
            except GeneratorExit:
                logger.info("Camera stream generator exit (stream_id=%s)", stream_id)
                break

    except FileNotFoundError:
        logger.error("ffmpeg not found - camera streaming requires ffmpeg")
        yield (b"--frame\r\nContent-Type: text/plain\r\n\r\nError: ffmpeg not installed\r\n")
    except asyncio.CancelledError:
        logger.info("Camera stream task cancelled (stream_id=%s)", stream_id)
    except GeneratorExit:
        logger.info("Camera stream generator closed (stream_id=%s)", stream_id)
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
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except TimeoutError:
                    logger.warning("ffmpeg didn't terminate gracefully, killing (stream_id=%s)", stream_id)
                    process.kill()
                    await process.wait()
            except ProcessLookupError:
                pass  # Process already dead
            except OSError as e:
                logger.warning("Error terminating ffmpeg: %s", e)
            logger.info("Camera stream stopped for %s (stream_id=%s)", ip_address, stream_id)


async def _ensure_producer(
    printer_id: int,
    db: AsyncSession,
    fps: int,
    quality: int,
    scale: float,
    printer: Printer | None = None,
    force_quality: bool = False,
) -> _SharedStream | None:
    """Start or reuse a shared producer for a single printer.

    Returns the _SharedStream entry, or None if the printer doesn't exist
    or has an external camera (not supported via the hub).

    Pass an already-fetched ``printer`` to skip the DB lookup.
    Set ``force_quality=True`` to restart the producer if params changed
    (used when a client explicitly switches quality).
    """
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

    def starter_fn():
        return stream_generator(**gen_kwargs)

    params_key = f"{fps_clamped}-{quality}-{scale}"
    _stream_start_times[printer_id] = time.monotonic()
    if force_quality:
        return await _hub.restart(printer_id, starter_fn, params_key=params_key)
    return await _hub.get_or_start(printer_id, starter_fn, params_key=params_key)


@router.get("/camera/grid-stream")
async def camera_grid_stream(
    request: Request,
    ids: str = Query(..., description="Comma-separated printer IDs"),
    fps: int = 5,
    quality: int = 15,
    scale: float = 0.5,
    force: bool = Query(False, description="Force restart producers with new quality settings"),
    db: AsyncSession = Depends(get_db),
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

    Note: Unauthenticated - loaded via fetch which may not send auth headers
    from the camera grid.
    """
    # Parse printer IDs
    try:
        printer_ids = [int(x.strip()) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "ids must be comma-separated integers")

    if not printer_ids:
        raise HTTPException(400, "No printer IDs provided")

    if len(printer_ids) > 20:
        raise HTTPException(400, "Maximum 20 printers per grid stream")

    # Start producers for all requested printers
    entries: dict[int, _SharedStream] = {}
    for pid in printer_ids:
        entry = await _ensure_producer(pid, db, fps, quality, scale, force_quality=force)
        if entry is not None:
            entries[pid] = entry

    if not entries:
        raise HTTPException(404, "No valid printers found")

    fps = max(1, min(fps, 30))

    async def generate():
        """Round-robin across all printers, yielding binary-framed JPEG data."""
        frame_interval = 1.0 / fps
        # Track last seen sequence per printer to avoid sending duplicates
        seen_seqs: dict[int, int] = dict.fromkeys(entries, 0)

        while True:
            if await request.is_disconnected():
                break

            sent_any = False
            now = time.monotonic()
            for pid, entry in list(entries.items()):
                if not entry.alive:
                    # Producer died — remove from rotation
                    entries.pop(pid, None)
                    continue

                # Touch last_accessed so the producer stays alive
                entry.last_accessed = now

                seq = entry.frame_seq
                if seq <= seen_seqs.get(pid, 0):
                    continue

                frame = entry.frame
                if frame is None:
                    continue

                seen_seqs[pid] = seq

                # Binary header: [printer_id u32 LE][length u32 LE]
                header = struct.pack("<II", pid, len(frame))
                yield header + frame
                sent_any = True

            if not entries:
                break

            # Sleep to avoid busy-looping; shorter than frame_interval
            # so we're responsive across multiple printers
            await asyncio.sleep(frame_interval * 0.5 if not sent_any else 0.01)

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
    fps: int = 10,
    quality: int = 5,
    scale: float = 1.0,
    db: AsyncSession = Depends(get_db),
):
    """Stream live video from printer camera as MJPEG.

    This endpoint returns a multipart MJPEG stream that can be used directly
    in an <img> tag or video player.

    Note: Unauthenticated - loaded via <img> tags which can't send auth headers.

    Uses external camera if configured, otherwise uses built-in camera:
    - External: MJPEG, RTSP, or HTTP snapshot
    - A1/P1: Chamber image protocol (port 6000)
    - X1/H2/P2: RTSP via ffmpeg (port 322)

    Args:
        printer_id: Printer ID
        fps: Target frames per second (default: 10, max: 30)
    """
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
    entry = await _ensure_producer(printer_id, db, fps, quality, scale, printer=printer)
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


@router.api_route("/{printer_id}/camera/stop", methods=["GET", "POST"])
async def stop_camera_stream(
    printer_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Stop all active camera streams for a printer.

    This can be called by the frontend when the camera window is closed.
    Accepts both GET and POST (POST for sendBeacon compatibility).
    """
    stopped = 0

    # Stop ffmpeg/RTSP streams
    to_remove = []
    for stream_id, process in list(_active_streams.items()):
        if stream_id.startswith(f"{printer_id}-"):
            to_remove.append(stream_id)
            if process.returncode is None:
                try:
                    process.terminate()
                    stopped += 1
                    logger.info("Terminated ffmpeg process for stream %s", stream_id)
                except OSError as e:
                    logger.warning("Error stopping stream %s: %s", stream_id, e)

    for stream_id in to_remove:
        _active_streams.pop(stream_id, None)

    # Stop chamber image streams
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

    # Stop shared hub stream (covers both RTSP and chamber)
    if await _hub.stop(printer_id):
        stopped += 1

    logger.info("Stopped %s camera stream(s) for printer %s", stopped, printer_id)
    return {"stopped": stopped}


@router.get("/{printer_id}/camera/snapshot")
async def camera_snapshot(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Capture a single frame from the printer camera.

    Returns a JPEG image.

    Note: Unauthenticated - loaded via <img> tags which can't send auth headers.
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
    has_active_stream = False

    # Check external camera streams
    if printer_id in _active_external_streams:
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

    # Check shared hub streams
    if not has_active_stream:
        has_active_stream = _hub.is_active(printer_id)

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
        "has_frames": printer_id in _last_frames,
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


@router.post("/{printer_id}/camera/external/test")
async def test_external_camera(
    printer_id: int,
    url: str,
    camera_type: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Test external camera connection.

    Args:
        printer_id: Printer ID (for authorization)
        url: Camera URL or USB device path to test
        camera_type: Camera type ("mjpeg", "rtsp", "snapshot", "usb")

    Returns:
        Dict with {success: bool, error?: str, resolution?: str}
    """
    # Verify printer exists (for authorization)
    await get_printer_or_404(printer_id, db)

    from backend.app.services.external_camera import test_connection

    return await test_connection(url, camera_type)


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
    label: str | None = None,
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
):
    """Get thumbnail image for a calibration reference.

    Note: Unauthenticated - loaded via <img> tags which can't send auth headers.
    """
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
    label: str,
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
