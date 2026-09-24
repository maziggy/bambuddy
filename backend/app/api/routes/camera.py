"""Camera streaming API endpoints for Bambu Lab printers."""

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core import database
from backend.app.core.auth import (
    RequireCameraStreamTokenIfAuthEnabled,
    RequirePermissionIfAuthEnabled,
    create_camera_stream_token,
)
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.user import User
from backend.app.services.camera import (
    capture_camera_frame,
    close_tls_proxy,
    create_tls_proxy,
    generate_chamber_image_stream,
    get_camera_port,
    get_ffmpeg_path,
    is_chamber_image_model,
    read_next_chamber_frame,
    rtsp_socket_timeout_flag,
    test_camera_connection,
)
from backend.app.services.camera_fanout import (
    MjpegBroadcaster,
    get_or_create_broadcaster,
    get_subscriber_count,
    iter_subscriber,
    shutdown_broadcaster,
)
from backend.app.services.camera_profiles import get_camera_profile
from backend.app.utils.ffmpeg_output import summarize_ffmpeg_stderr

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/printers", tags=["camera"])

# Grace period for a SIGTERMed ffmpeg to shut down before we SIGKILL it. Only
# reachable when ffmpeg genuinely ignores SIGTERM: _terminate_ffmpeg drains the
# pipes first, and a drained ffmpeg exits in ~0.15s.
_FFMPEG_TERM_TIMEOUT = 2.0

# Upper bound on waiting for a SIGKILLed ffmpeg to be reaped (#2580).
#
# The original diagnosis — "a killed ffmpeg stuck in uninterruptible I/O on a
# dead RTSP socket" — was wrong, and this bound was capping a deadlock of our
# own making rather than waiting out a stuck process. A process that survives
# SIGKILL would have to be in uninterruptible sleep (state D); the ffmpeg seen
# doing this was in state S, and its returncode was already set to -9 while
# wait() was still blocked. The real cause was undrained pipes (see
# _terminate_ffmpeg), which made this timeout fire on *every* camera close.
#
# Kept as a backstop now that the cause is fixed: it should no longer be
# reachable, and if it ever is, abandoning the wait is still safe because
# cleanup_orphaned_streams' /proc scan reaps any Bambu ffmpeg not attached to
# an active stream on its next pass.
_FFMPEG_KILL_TIMEOUT = 2.0

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

# Track disconnect events per stream_id — allows stop endpoint and cleanup
# to signal generators to stop reconnecting instead of just killing the process
_disconnect_events: dict[str, asyncio.Event] = {}

# Track last frame time per stream_id (not just per printer_id) for stale detection
_stream_last_frame_times: dict[str, float] = {}

# How much of a streaming ffmpeg's stderr to retain: enough for the input
# analysis plus a burst of errors, capped so a long-running stream can't grow it.
_FFMPEG_STDERR_TAIL_BYTES = 16384

# Live stderr collectors by pid — see _FfmpegStderrTail. Present means "this
# process's stderr already has a reader; do not open a second one".
_stderr_tails: dict[int, "_FfmpegStderrTail"] = {}


def get_buffered_frame(printer_id: int) -> bytes | None:
    """Get the last buffered frame for a printer from an active stream.

    Returns the JPEG frame data if available, or None if no active stream.
    """
    return _last_frames.get(printer_id)


def is_stream_active(printer_id: int) -> bool:
    """Return True iff a fan-out camera stream is currently registered for this printer.

    Snapshot callers (Obico polling, manual /camera/snapshot) MUST NOT open a
    second concurrent RTSP/chamber-image socket while a viewer is attached:
    most Bambu firmwares allow only one camera connection, so the competing
    socket either kicks the live viewer off or gets refused itself, and the
    resulting reconnect storm tears down the fan-out broadcaster (see #1348).

    Callers should consult this BEFORE trying to open a fresh socket and skip
    the capture cycle when it returns True — even if try_get_active_buffered_frame
    returns None (the stream may be running but the first frame hasn't landed
    in the buffer yet, or the upstream is mid-reconnect).
    """
    return any(k.startswith(f"{printer_id}-") for k in _active_streams) or any(
        k.startswith(f"{printer_id}-") for k in _active_chamber_streams
    )


def try_get_active_buffered_frame(printer_id: int) -> bytes | None:
    """Return a buffered frame iff a stream is currently running for this printer.

    Snapshot callers (Obico polling, manual /camera/snapshot) tap the fan-out
    broadcaster's running upstream instead of opening a second concurrent
    RTSP/chamber-image socket. Critical for printers that allow only one
    camera connection (e.g. X2D firmware 01.01.00.00; see #1271).

    Returns None when no broadcaster is active for this printer, so callers
    fall through to their existing fresh-socket path unchanged.

    NB: returning None does NOT mean "safe to open a fresh socket" — it also
    fires when the stream is registered but no frame has been buffered yet
    (startup race, mid-reconnect). Callers that must avoid competing sockets
    should consult is_stream_active() first; see #1348.
    """
    if not is_stream_active(printer_id):
        return None
    return _last_frames.get(printer_id)


async def get_printer_or_404(printer_id: int, db: AsyncSession) -> Printer:
    """Get printer by ID or raise 404."""
    result = await db.execute(select(Printer).where(Printer.id == printer_id))
    printer = result.scalar_one_or_none()
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")
    return printer


async def generate_chamber_mjpeg_stream(
    ip_address: str,
    access_code: str,
    model: str | None,
    fps: int = 5,
    stream_id: str | None = None,
    disconnect_event: asyncio.Event | None = None,
    printer_id: int | None = None,
) -> AsyncGenerator[bytes, None]:
    """Generate MJPEG stream from A1/P1 printer using chamber image protocol.

    This connects to port 6000 and reads JPEG frames using the Bambu binary protocol.
    """
    logger.info("Starting chamber image stream for %s (stream_id=%s, model=%s)", ip_address, stream_id, model)

    # Register disconnect event so stop endpoint can signal us
    if stream_id and disconnect_event:
        _disconnect_events[stream_id] = disconnect_event

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

        while True:
            # Check if client disconnected
            if disconnect_event and disconnect_event.is_set():
                logger.info("Client disconnected, stopping chamber stream %s", stream_id)
                break

            # Read next frame
            frame = await read_next_chamber_frame(reader, timeout=30.0)
            if frame is None:
                logger.warning("Chamber image stream ended for %s", stream_id)
                break

            # Save frame to buffer for photo capture and track timestamp
            if printer_id is not None:
                _last_frames[printer_id] = frame
                _last_frame_times[printer_id] = time.time()

            # Rate limiting - skip frames if needed to maintain target FPS
            current_time = asyncio.get_event_loop().time()
            if current_time - last_frame_time < frame_interval:
                continue
            last_frame_time = current_time

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
        # Remove from active streams and disconnect events
        if stream_id:
            _active_chamber_streams.pop(stream_id, None)
            _disconnect_events.pop(stream_id, None)
            _stream_last_frame_times.pop(stream_id, None)

        # Clean up frame buffer and timestamps
        _release_printer_frame_state(printer_id)

        # Close the connection
        try:
            writer.close()
            await writer.wait_closed()
        except OSError:
            pass  # Connection already closed or broken; cleanup is best-effort
        logger.info("Chamber image stream stopped for %s (stream_id=%s)", ip_address, stream_id)


def _new_fanout_stream_id(printer_id: int) -> str:
    """Registry key for one fan-out stream INSTANCE, not for the printer.

    A plain ``f"{printer_id}-fanout"`` meant every successive stream for a
    printer shared one key, so a departing generator's cleanup removed the entry
    its successor had just registered. The external-camera path already carries a
    per-instance suffix for exactly this reason (#2675); this gives the fan-out
    path the same property.

    The ``f"{printer_id}-"`` prefix is load-bearing — ``is_stream_active``,
    ``stop_camera_stream`` and ``/camera/status`` all find a printer's streams by
    scanning for it — so the suffix goes on the end.
    """
    return f"{printer_id}-fanout-{uuid.uuid4().hex[:8]}"


def live_frame_for_capture(printer_id: int) -> tuple[bool, bytes | None]:
    """Should a one-shot capture stand down for the live view, and to what frame?

    Returns ``(defer, frame)``. ``defer`` True means DO NOT open a capture of
    your own: use ``frame`` when it isn't None, and otherwise skip this attempt
    rather than competing.

    Both camera kinds allow exactly one reader — Bambu firmware permits one
    connection, and a USB camera permits one V4L2 handle — so a capture that
    races the live view doesn't degrade, it fails outright. #2707 measured 0 of
    87 and 0 of 105 layer-timelapse captures on prints watched throughout, and
    finish photos going out with no image attached.

    Skipping when the buffer is momentarily empty (stream starting, mid-
    reconnect) rather than falling through to a capture is the #1348 rule:
    opening a competing handle kicks the viewer off, which is a worse outcome
    than missing one frame.
    """
    if not is_stream_active(printer_id):
        return False, None
    return True, _last_frames.get(printer_id)


def _release_printer_frame_state(printer_id: int | None) -> None:
    """Drop a printer's buffered frame and timings — unless a stream still owns them.

    These three dicts are keyed by printer, not by stream, so a departing
    generator must not clear them while a newer stream for the same printer is
    running. That used to happen routinely: stream ids were per-printer, so a
    predecessor's cleanup wiped its successor's state, leaving
    ``is_stream_active()`` False with a viewer attached (which is exactly what
    the #1348 / #1271 guards read before deciding whether it is safe to open a
    second camera connection), the janitor free to reap the live ffmpeg as an
    orphan, and snapshots without a frame to reuse.

    Call this AFTER removing the departing stream's own key, so the check
    reports on other streams rather than on the caller.
    """
    if printer_id is None or is_stream_active(printer_id):
        return
    _last_frames.pop(printer_id, None)
    _last_frame_times.pop(printer_id, None)
    _stream_start_times.pop(printer_id, None)


async def _drain_pipe(reader) -> None:
    """Read a subprocess pipe to EOF and discard, so it can never block.

    Best-effort by design: any read failure means we cannot drain further, and
    the caller is tearing the process down regardless.
    """
    if reader is None:
        return
    try:
        while await reader.read(65536):
            pass
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — teardown must not fail on a dying pipe
        return


async def _terminate_ffmpeg(process: asyncio.subprocess.Process, stream_id: str | None = None) -> None:
    """Terminate an ffmpeg process gracefully, then kill if needed.

    Drains stdout/stderr throughout, which is load-bearing rather than hygiene.
    ffmpeg is spawned with both as pipes, and every caller of this has already
    stopped reading stdout — so by the time we get here ffmpeg is typically
    blocked in write() on a full 64 KiB pipe. Two things then go wrong:

    * SIGTERM cannot be acted on. ffmpeg's handler only sets a flag that its
      main loop polls, and a loop blocked in write() never reaches the check,
      so the whole grace period is dead time.
    * SIGKILL does kill it, but wait() cannot observe that. asyncio resolves
      Process.wait()'s waiter through BaseSubprocessTransport._try_finish(),
      which requires every pipe transport to report disconnected; paused,
      unread pipes never reach EOF, so wait() blocks with returncode already
      set. That is what made the "did not exit within Ns of SIGKILL" error
      fire on every single camera close, and unbounded it was the 12-hour
      hang in #2580.

    Draining fixes both: SIGTERM becomes actionable and the exit observable.
    Measured on an H2D: 4.0s of dead time per close before, ~0.15s after —
    which matters because the printer allows exactly one camera connection,
    so every one of those seconds was a connection nobody could use.

    Discarding what we drain is deliberate. The stream loop already reads
    stderr on its error paths (_read_ffmpeg_stderr), and it does so before
    calling this, so nothing diagnostic is lost.
    """
    if process.returncode is not None:
        _spawned_ffmpeg_pids.pop(process.pid, None)
        return  # Already dead

    drainers = [asyncio.create_task(_drain_pipe(process.stdout))]
    # A streaming ffmpeg's stderr already has a reader (_FfmpegStderrTail), and
    # it keeps draining right through teardown, which is all we need here. Adding
    # a second reader would race it — asyncio rejects concurrent reads on one
    # StreamReader — so only drain stderr when nobody else owns it.
    if process.pid not in _stderr_tails:
        drainers.append(asyncio.create_task(_drain_pipe(process.stderr)))
    try:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=_FFMPEG_TERM_TIMEOUT)
        except TimeoutError:
            logger.warning("ffmpeg didn't terminate gracefully, killing (stream_id=%s)", stream_id)
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=_FFMPEG_KILL_TIMEOUT)
            except TimeoutError:
                # Do NOT keep waiting (#2580): the caller is the stream
                # generator, and blocking here pins the fan-out pump forever.
                # The orphan janitor reaps the process later. With the pipes
                # drained this should be unreachable — see _FFMPEG_KILL_TIMEOUT.
                logger.error(
                    "ffmpeg did not exit within %.1fs of SIGKILL; abandoning wait (stream_id=%s)",
                    _FFMPEG_KILL_TIMEOUT,
                    stream_id,
                )
    except ProcessLookupError:
        pass  # Already dead
    except OSError as e:
        logger.warning("Error terminating ffmpeg: %s", e)
    finally:
        for drainer in drainers:
            drainer.cancel()
        await asyncio.gather(*drainers, return_exceptions=True)
        _spawned_ffmpeg_pids.pop(process.pid, None)


# The banner-stripping summariser moved to backend.app.utils.ffmpeg_output so
# the seven other places that log ffmpeg stderr could stop truncating it from
# the front (#2968). Imported under the private name this module has always
# used: _FfmpegStderrTail and the tests both reach for it by that name.
_summarize_ffmpeg_stderr = summarize_ffmpeg_stderr


class _FfmpegStderrTail:
    """Owns a long-lived ffmpeg's stderr: drains it continuously, keeps the tail.

    Reading stderr only when something has already gone wrong leaves a pipe
    nobody reads for the whole life of the stream. ffmpeg writes its banner, the
    input analysis and then a progress line at a steady rate, so a 64 KiB pipe
    fills eventually and ffmpeg blocks writing to it — at which point it stops
    producing frames, the stream's own read timeout fires, and the log says
    "RTSP read timeout" with no hint that we starved it ourselves.

    How long that takes is unmeasured and may be a long time: one H2D upstream
    ran 21m36s continuously without stalling, so this is a bounded resource
    being treated as unbounded rather than an observed failure. Draining removes
    the ceiling either way, and the tail is *better* diagnostic material than
    the old on-demand read: it holds ffmpeg's most recent output at the moment
    things went wrong, where reading the buffered pipe returned whatever was
    printed first (usually the startup banner, which the summariser then strips).

    Registers itself in ``_stderr_tails`` so the two other readers of this pipe
    can defer to it — asyncio raises if two coroutines read one StreamReader
    concurrently. See ``_read_ffmpeg_stderr`` and ``_terminate_ffmpeg``.
    """

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self._buffer = bytearray()
        self._task: asyncio.Task | None = None
        if process.stderr is None:
            return
        self._task = asyncio.create_task(self._pump())
        _stderr_tails[process.pid] = self

    async def _pump(self) -> None:
        reader = self._process.stderr
        try:
            while True:
                chunk = await reader.read(8192)
                if not chunk:
                    return  # EOF — ffmpeg has exited
                self._buffer.extend(chunk)
                excess = len(self._buffer) - _FFMPEG_STDERR_TAIL_BYTES
                if excess > 0:
                    del self._buffer[:excess]
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a broken pipe just ends the tail
            return

    def text(self) -> str | None:
        """The retained tail, summarised. None when nothing was captured.

        Goes through _summarize_ffmpeg_stderr like every other stderr log in
        this module: ffmpeg echoes its input URL, which carries the access code.
        """
        if not self._buffer:
            return None
        return _summarize_ffmpeg_stderr(self._buffer.decode(errors="replace")) or None

    async def aclose(self) -> None:
        """Stop draining and release ownership of the pipe. Idempotent.

        Awaits the cancelled pump rather than firing and forgetting, so the task
        is finished before the caller moves on — an abandoned pending task
        becomes an "unraisable exception" warning at an arbitrary later point,
        usually during interpreter or loop teardown.
        """
        task, self._task = self._task, None
        if _stderr_tails.get(self._process.pid) is self:
            del _stderr_tails[self._process.pid]
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _read_ffmpeg_stderr(process: asyncio.subprocess.Process) -> str | None:
    """Read whatever ffmpeg has written to stderr so far (best-effort).

    ffmpeg's stderr must be drained *incrementally*. A stalled-but-still-alive
    ffmpeg — the typical P2S RTSP failure, where it connects but never produces
    a frame — never closes stderr, so a plain ``stderr.read()`` (read-to-EOF)
    blocks until the wait_for timeout and returns nothing, discarding the
    banner + stream-analysis lines ffmpeg already printed. Reading in bounded
    chunks returns the buffered output promptly whether or not ffmpeg has
    exited. Returns the content with ffmpeg's boilerplate banner stripped.

    When a _FfmpegStderrTail owns this process's stderr — every streaming
    ffmpeg — its retained tail is returned instead. Reading the pipe here as
    well would race that collector, and asyncio refuses two concurrent readers
    on one StreamReader outright.
    """
    if not process:
        return None
    tail = _stderr_tails.get(getattr(process, "pid", None))
    if tail is not None:
        return tail.text()
    if not process.stderr:
        return None
    chunks: list[bytes] = []
    total = 0
    cap = 65536
    try:
        while total < cap:
            chunk = await asyncio.wait_for(process.stderr.read(8192), timeout=2.0)
            if not chunk:
                break  # EOF — ffmpeg has exited
            chunks.append(chunk)
            total += len(chunk)
    except Exception:
        # Timed out waiting for more data — ffmpeg is alive but quiet now.
        # Fall through and return whatever it already printed.
        pass
    if not chunks:
        return None
    return _summarize_ffmpeg_stderr(b"".join(chunks).decode(errors="replace")) or None


async def generate_rtsp_mjpeg_stream(
    ip_address: str,
    access_code: str,
    model: str | None,
    fps: int = 10,
    stream_id: str | None = None,
    disconnect_event: asyncio.Event | None = None,
    printer_id: int | None = None,
) -> AsyncGenerator[bytes, None]:
    """Generate MJPEG stream from printer camera using ffmpeg/RTSP.

    This is for X1/H2/P2 models that support RTSP streaming.
    Auto-reconnects when the printer drops the RTSP session (common on P2S).
    Per-model knobs (probesize, analyzeduration, reconnect cadence) come from
    :func:`camera_profiles.get_camera_profile` so quirky firmwares can be
    handled by adding a profile entry rather than tuning a global constant.
    """
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        logger.error("ffmpeg not found - camera streaming requires ffmpeg")
        yield (b"--frame\r\nContent-Type: text/plain\r\n\r\nError: ffmpeg not installed\r\n")
        return

    profile = get_camera_profile(model)

    port = get_camera_port(model)

    # Use a local TLS proxy so Python's OpenSSL handles TLS instead of
    # ffmpeg's GnuTLS.  This fixes P2S (and potentially other models)
    # dropping the RTSP session after a few seconds due to GnuTLS's
    # hardened Debian defaults rejecting TLS renegotiation.
    proxy_port, proxy_server = await create_tls_proxy(ip_address, port)
    camera_url = f"rtsp://bblp:{access_code}@127.0.0.1:{proxy_port}/streaming/live/1"

    # ffmpeg command to output MJPEG stream to stdout
    cmd = [
        ffmpeg,
        "-rtsp_transport",
        "tcp",
        "-rtsp_flags",
        "prefer_tcp",
        # Socket I/O timeout name varies by ffmpeg version (#1504); see
        # rtsp_socket_timeout_flag(). The 30s value is microseconds for
        # both names.
        f"-{rtsp_socket_timeout_flag()}",
        "30000000",
        "-buffer_size",
        "1024000",  # 1MB buffer
        "-max_delay",
        "500000",  # 0.5 seconds max delay
        "-probesize",
        str(profile.probesize),
        "-analyzeduration",
        str(profile.analyzeduration),
        "-fflags",
        "nobuffer",  # Reduce internal buffering
        "-flags",
        "low_delay",  # Minimize decode latency
        *profile.extra_ffmpeg_input_args,
        "-i",
        camera_url,
        "-f",
        "mjpeg",
        "-q:v",
        "5",
        "-r",
        str(fps),
        "-an",  # No audio
        "-",  # Output to stdout
    ]

    # Register disconnect event so stop endpoint can signal us
    if stream_id and disconnect_event:
        _disconnect_events[stream_id] = disconnect_event

    logger.info(
        "Starting RTSP camera stream for %s (stream_id=%s, model=%s, fps=%s, probesize=%s, analyzeduration=%s)",
        ip_address,
        stream_id,
        model,
        fps,
        profile.probesize,
        profile.analyzeduration,
    )
    # Log the full argv so a support bundle shows the actual ffmpeg flags
    # (probesize, analyzeduration, transport, ...). Only camera_url carries a
    # secret (the access code), so redact just that one element.
    _redacted_cmd = ["rtsp://<redacted>/streaming/live/1" if a == camera_url else a for a in cmd]
    logger.debug("ffmpeg command: %s", " ".join(_redacted_cmd))

    # On Windows, spawn ffmpeg in its own process group so that
    # terminate() doesn't broadcast CTRL_C_EVENT to uvicorn (#605).
    spawn_kwargs: dict = {}
    if sys.platform == "win32":
        spawn_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    jpeg_start = b"\xff\xd8"
    jpeg_end = b"\xff\xd9"
    reconnect_count = 0
    process = None
    stderr_tail: _FfmpegStderrTail | None = None
    got_any_frames = False

    try:
        while reconnect_count <= profile.rtsp_reconnect_max:
            # Check for client disconnect before (re)connecting
            if disconnect_event and disconnect_event.is_set():
                break

            if reconnect_count > 0:
                logger.info(
                    "RTSP reconnecting (%d/%d) for %s (stream_id=%s)",
                    reconnect_count,
                    profile.rtsp_reconnect_max,
                    ip_address,
                    stream_id,
                )
                await asyncio.sleep(profile.rtsp_reconnect_delay)
                if disconnect_event and disconnect_event.is_set():
                    break

            # Spawn ffmpeg
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **spawn_kwargs,
            )

            if stream_id:
                _active_streams[stream_id] = process
            import time as _time

            _spawned_ffmpeg_pids[process.pid] = _time.time()

            # Brief check for immediate startup failures
            await asyncio.sleep(0.1)
            if process.returncode is not None:
                stderr = await process.stderr.read()
                stderr_text = _summarize_ffmpeg_stderr(stderr.decode(errors="replace"))
                logger.error("ffmpeg failed immediately (attempt %d): %s", reconnect_count + 1, stderr_text)
                _spawned_ffmpeg_pids.pop(process.pid, None)
                if not got_any_frames and reconnect_count == 0:
                    # First attempt failed immediately — camera is likely unreachable
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: text/plain\r\n\r\n"
                        b"Error: Camera connection failed. Check printer is on and camera is enabled.\r\n"
                    )
                    return
                reconnect_count += 1
                continue

            # Take ownership of stderr for the life of this process. Started
            # only after the immediate-failure check above, which reads the pipe
            # directly (correct there: the process is already dead, so
            # read-to-EOF returns at once and cannot be raced by a collector).
            # Nothing is lost by starting late — the banner ffmpeg printed in the
            # meantime is still sitting in the pipe.
            stderr_tail = _FfmpegStderrTail(process)

            # Read JPEG frames from ffmpeg stdout
            buffer = b""
            stream_ended = False
            client_gone = False

            while True:
                if disconnect_event and disconnect_event.is_set():
                    client_gone = True
                    break

                try:
                    chunk = await asyncio.wait_for(process.stdout.read(8192), timeout=30.0)

                    if not chunk:
                        # ffmpeg exited — log stderr and break to reconnect
                        stderr_text = await _read_ffmpeg_stderr(process)
                        if stderr_text:
                            logger.warning("ffmpeg stderr (stream_id=%s): %s", stream_id, stderr_text)
                        logger.warning("RTSP stream ended for %s (stream_id=%s), will reconnect", ip_address, stream_id)
                        stream_ended = True
                        break

                    buffer += chunk

                    # Extract complete JPEG frames from buffer
                    while True:
                        start_idx = buffer.find(jpeg_start)
                        if start_idx == -1:
                            buffer = buffer[-2:] if len(buffer) > 2 else buffer
                            break

                        if start_idx > 0:
                            buffer = buffer[start_idx:]

                        end_idx = buffer.find(jpeg_end, 2)
                        if end_idx == -1:
                            break

                        frame = buffer[: end_idx + 2]
                        buffer = buffer[end_idx + 2 :]
                        got_any_frames = True

                        if printer_id is not None:
                            _last_frames[printer_id] = frame
                            _last_frame_times[printer_id] = time.time()
                            if stream_id:
                                _stream_last_frame_times[stream_id] = time.time()

                        yield (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            b"Content-Length: " + str(len(frame)).encode() + b"\r\n"
                            b"\r\n" + frame + b"\r\n"
                        )

                except TimeoutError:
                    stderr_text = await _read_ffmpeg_stderr(process)
                    if stderr_text:
                        logger.warning("ffmpeg stderr on timeout: %s", stderr_text)
                    logger.warning("RTSP read timeout for %s (stream_id=%s)", ip_address, stream_id)
                    stream_ended = True
                    break
                except asyncio.CancelledError:
                    logger.info("Camera stream cancelled (stream_id=%s)", stream_id)
                    client_gone = True
                    break
                except GeneratorExit:
                    logger.info("Camera stream generator exit (stream_id=%s)", stream_id)
                    client_gone = True
                    break

            # Clean up this ffmpeg process before reconnecting or exiting
            await _terminate_ffmpeg(process, stream_id)
            # Released after teardown, not before: _terminate_ffmpeg deliberately
            # leaves stderr to this collector, which has to keep draining while
            # the process is stopped or wait() can't observe the exit.
            if stderr_tail is not None:
                await stderr_tail.aclose()
                stderr_tail = None
            process = None

            if client_gone:
                break

            # Check if stream was explicitly stopped (e.g., by stop endpoint)
            if stream_id and stream_id not in _active_streams:
                logger.info("Stream %s removed from active streams, stopping reconnect", stream_id)
                break

            if stream_ended:
                reconnect_count += 1
                continue

            # Normal exit (shouldn't reach here, but be safe)
            break

        if reconnect_count > profile.rtsp_reconnect_max:
            logger.error(
                "RTSP max reconnects (%d) reached for %s (stream_id=%s)",
                profile.rtsp_reconnect_max,
                ip_address,
                stream_id,
            )

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
        # Remove from active streams and disconnect events
        if stream_id:
            _active_streams.pop(stream_id, None)
            _disconnect_events.pop(stream_id, None)
            _stream_last_frame_times.pop(stream_id, None)

        # Clean up frame buffer and timestamps
        _release_printer_frame_state(printer_id)

        if process:
            await _terminate_ffmpeg(process, stream_id)
            logger.info("Camera stream stopped for %s (stream_id=%s)", ip_address, stream_id)

        # Same order as in the loop: terminate first, then release stderr.
        if stderr_tail is not None:
            await stderr_tail.aclose()

        # Shut down the TLS proxy
        await close_tls_proxy(proxy_server)


@router.post("/camera/stream-token")
async def create_stream_token(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Create a reusable token for camera stream/snapshot access.

    Returns a token valid for 60 minutes that can be appended as ?token=xxx
    to camera stream/snapshot URLs loaded via <img> tags.
    """
    return {"token": await create_camera_stream_token()}


@router.get("/{printer_id}/camera/stream")
async def camera_stream(
    printer_id: int,
    request: Request,
    fps: int = 10,
    _: None = RequireCameraStreamTokenIfAuthEnabled,
):
    """Stream live video from printer camera as MJPEG.

    This endpoint returns a multipart MJPEG stream that can be used directly
    in an <img> tag or video player.

    Requires a stream token query param (?token=xxx) when auth is enabled.

    Uses external camera if configured, otherwise uses built-in camera:
    - External: MJPEG, RTSP, or HTTP snapshot
    - A1/P1: Chamber image protocol (port 6000)
    - X1/H2/P2: RTSP via ffmpeg (port 322)

    Args:
        printer_id: Printer ID
        fps: Target frames per second (default: 10, max: 30)
    """
    # Fetch the printer in a short-lived session so the pooled DB connection is
    # released BEFORE we start streaming. A live MJPEG stream runs for as long
    # as the browser tab stays open (potentially hours); holding the
    # Depends(get_db) session across it pinned one pooled connection per open
    # camera tab per printer — a top contributor to pool exhaustion on large
    # farms (issue #2572). expire_on_commit=False keeps the printer's already-
    # loaded columns readable after the session closes, and everything below
    # reads only scalar attributes (model, ip_address, access_code,
    # external_camera_*) — no lazy loads.
    #
    # Reference async_session via the module (not a top-level import binding) so
    # the session maker is looked up at call time — that keeps it in sync with
    # reinitialize_database() and lets the test harness's patch of
    # backend.app.core.database.async_session take effect here.
    async with database.async_session() as db:
        printer = await get_printer_or_404(printer_id, db)

    # Check for external camera first
    if printer.external_camera_enabled and printer.external_camera_url:
        # NB: no `import time` / `import uuid` here, and don't reintroduce them.
        # A local import anywhere in this function makes the name function-local
        # for the WHOLE function, so the RTSP/chamber path below — which never
        # executes this branch — would raise UnboundLocalError on any printer
        # without an external camera. Both are imported at module level.
        from backend.app.services.external_camera import generate_mjpeg_stream

        # Limit external camera FPS to reduce browser load
        fps = min(max(fps, 1), 15)
        logger.info(
            "Using external camera (%s) for printer %s at %s fps", printer.external_camera_type, printer_id, fps
        )

        # Register the stream into the SAME registries the RTSP/chamber paths use
        # (#2675) so `/camera/stop` and cleanup_orphaned_streams can find and kill
        # a leaked ffmpeg holding a USB device open. Before this, external streams
        # only tracked _active_external_streams and were structurally invisible to
        # both the stop endpoint and the janitor. The stream_id keeps the
        # `{printer_id}-` prefix both scanners key on, plus a unique suffix so two
        # concurrent viewers of one printer don't clobber each other's entry.
        stream_id = f"{printer_id}-ext-{uuid.uuid4().hex[:8]}"
        stop_event = asyncio.Event()
        _disconnect_events[stream_id] = stop_event
        # Track stream start
        _stream_start_times[printer_id] = time.time()
        _active_external_streams.add(printer_id)

        # Mutable holder so the wrapper's finally can unregister whatever process
        # is currently registered (the RTSP path may respawn across reconnects).
        current_proc: dict[str, asyncio.subprocess.Process] = {}

        def _register_external_process(proc: asyncio.subprocess.Process) -> None:
            prev = current_proc.get("proc")
            if prev is not None and prev.pid != proc.pid:
                _spawned_ffmpeg_pids.pop(prev.pid, None)
            current_proc["proc"] = proc
            _active_streams[stream_id] = proc
            _spawned_ffmpeg_pids[proc.pid] = time.time()
            _stream_last_frame_times[stream_id] = time.time()

        def _publish_external_frame(frame: bytes) -> None:
            """Make the live frame reusable by one-shot consumers (#2707).

            Only the built-in camera paths populated _last_frames, so every
            external-camera consumer — layer timelapse, finish photo, Obico,
            plate check — found an empty buffer and opened its own handle on a
            device that allows exactly one reader, which simply failed while a
            viewer was attached. Raw frame, not the multipart-wrapped chunk the
            generator yields, because that is what those consumers expect.
            """
            _last_frames[printer_id] = frame

        async def external_stream_wrapper():
            """Wrap external stream to track start/stop and update frame times."""
            try:
                async for frame in generate_mjpeg_stream(
                    printer.external_camera_url,
                    printer.external_camera_type,
                    fps,
                    on_process=_register_external_process,
                    on_frame=_publish_external_frame,
                    stop_event=stop_event,
                ):
                    # generate_mjpeg_stream already handles rate limiting;
                    # track frame times (per-printer + per-stream) for stall detection
                    now = time.time()
                    _last_frame_times[printer_id] = now
                    _stream_last_frame_times[stream_id] = now
                    yield frame
            finally:
                # Best-effort unregister. If an abrupt disconnect skips this
                # finally, the registry entries persist — which is exactly what
                # lets the stop endpoint / janitor reap the leaked process.
                stop_event.set()
                proc = current_proc.get("proc")
                if proc is not None:
                    _spawned_ffmpeg_pids.pop(proc.pid, None)
                _active_streams.pop(stream_id, None)
                _disconnect_events.pop(stream_id, None)
                _stream_last_frame_times.pop(stream_id, None)
                _active_external_streams.discard(printer_id)
                # Now that this path publishes a buffered frame, it has to
                # retract it too — ownership-checked, so a concurrent viewer of
                # the same printer keeps its own. Also clears the per-printer
                # timings this path used to leave behind.
                _release_printer_frame_state(printer_id)
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

    # Validate FPS - A1/P1 models max out at ~5 FPS
    if is_chamber_image_model(printer.model):
        fps = min(max(fps, 1), 5)
    else:
        fps = min(max(fps, 1), 30)

    # Choose the appropriate stream generator based on model
    if is_chamber_image_model(printer.model):
        stream_generator = generate_chamber_mjpeg_stream
        logger.info("Using chamber image protocol for %s", printer.model)
    else:
        stream_generator = generate_rtsp_mjpeg_stream
        logger.info("Using RTSP protocol for %s", printer.model)

    # Track stream start time. Set only if absent so the value reflects when
    # the SHARED upstream first started streaming, not when each new viewer
    # attached — otherwise /camera/status would report stream_uptime jumping
    # backward whenever a second viewer joins. The upstream generator's
    # finally clears this entry when the upstream actually ends.
    _stream_start_times.setdefault(printer_id, time.time())

    # Fan-out broadcaster (#1089): one upstream connection per printer, shared
    # across all viewers. Most Bambu printers only allow a single concurrent
    # camera connection, so opening the same printer in two tabs would
    # otherwise kick the first viewer off. The broadcaster owns the single
    # upstream and the per-viewer disconnect handling.
    #
    # Note: the upstream's fps is fixed by the first viewer who creates the
    # broadcaster. Concurrent viewers share that rate; new viewers after
    # teardown create a fresh broadcaster at their requested fps.
    fanout_key = f"printer-{printer_id}"
    upstream_stream_id = _new_fanout_stream_id(printer_id)

    def _factory(disconnect_event: asyncio.Event):
        # Re-bind locals into the closure so the async generator below sees
        # them — disconnect_event is owned by the broadcaster and signalled
        # when the last subscriber leaves (after the grace window).
        return stream_generator(
            ip_address=printer.ip_address,
            access_code=printer.access_code,
            model=printer.model,
            fps=fps,
            stream_id=upstream_stream_id,
            disconnect_event=disconnect_event,
            printer_id=printer_id,
        )

    # Subscribe with a one-shot retry to close a tiny race: the grace-window
    # teardown can flip the broadcaster to `stopped=True` between the registry
    # lookup and our subscribe call. The retry forces the registry to mint a
    # fresh broadcaster (since the now-stopped one is replaced), and the second
    # subscribe is guaranteed to land on it before any teardown can fire.
    broadcaster: MjpegBroadcaster = await get_or_create_broadcaster(fanout_key, _factory)
    try:
        queue = await broadcaster.subscribe()
    except RuntimeError:
        broadcaster = await get_or_create_broadcaster(fanout_key, _factory)
        queue = await broadcaster.subscribe()
    logger.info(
        "Camera viewer attached to %s (subscribers=%d)",
        fanout_key,
        broadcaster.subscriber_count,
    )

    async def _is_disconnected() -> bool:
        try:
            return await request.is_disconnected()
        except Exception:
            # Older starlette/uvicorn can raise during teardown — treat that
            # as "client gone" so the subscriber cleanly unsubscribes.
            return True

    def _log_detach(remaining: int) -> None:
        logger.info("Camera viewer detached from %s (subscribers=%d)", fanout_key, remaining)

    async def _generate():
        async for chunk in iter_subscriber(
            broadcaster,
            queue,
            is_disconnected=_is_disconnected,
            on_unsubscribe=_log_detach,
        ):
            yield chunk

    return StreamingResponse(
        _generate(),
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
    """Stop active camera streams for a printer.

    Called by the frontend on viewer unmount (cam-wall tile, embedded viewer,
    popup window). Accepts both GET and POST (POST for sendBeacon compatibility).

    Reference-count guard: every viewer of a printer subscribes to the same
    fan-out broadcaster, so a force-shutdown triggered by ONE leaving viewer
    used to kill the others' streams (cam-wall tile froze when a user opened
    then closed the embedded viewer). If any subscriber is still attached,
    skip the force-teardown — the broadcaster's natural grace-shutdown (5 s
    after subscribers drop to 0) handles cleanup when the leaving viewer's
    HTTP connection actually closes.
    """
    broadcaster_key = f"printer-{printer_id}"
    remaining_subscribers = get_subscriber_count(broadcaster_key)
    if remaining_subscribers >= 1:
        logger.info(
            "Skipping force-shutdown for printer %s: %d subscriber(s) still attached; "
            "natural cleanup will tear down when last viewer disconnects",
            printer_id,
            remaining_subscribers,
        )
        return {"stopped": 0, "skipped": True}

    stopped = 0

    # Tear down the fan-out broadcaster first (#1089). This cleanly notifies
    # all subscribed viewers and asks the upstream generator to stop
    # reconnecting before we fall back to forcefully killing the process below.
    if await shutdown_broadcaster(broadcaster_key):
        logger.info("Shut down camera fan-out broadcaster for printer %s", printer_id)

    # Stop ffmpeg/RTSP streams
    to_remove = []
    for stream_id, process in list(_active_streams.items()):
        if stream_id.startswith(f"{printer_id}-"):
            to_remove.append(stream_id)
            # Signal the generator to stop reconnecting BEFORE killing the process
            event = _disconnect_events.get(stream_id)
            if event:
                event.set()
            if process.returncode is None:
                # Shared helper, not an inline copy: it bounds the post-kill
                # wait (#2580) — a killed-but-unreaped ffmpeg used to hang this
                # request forever, exactly when the user hit Stop to recover a
                # stuck stream.
                await _terminate_ffmpeg(process, stream_id)
                stopped += 1
                logger.info("Terminated ffmpeg process for stream %s", stream_id)
            _spawned_ffmpeg_pids.pop(process.pid, None)

    for stream_id in to_remove:
        _active_streams.pop(stream_id, None)
        _disconnect_events.pop(stream_id, None)
        _stream_last_frame_times.pop(stream_id, None)

    # Stop chamber image streams
    to_remove_chamber = []
    for stream_id, (_reader, writer) in list(_active_chamber_streams.items()):
        if stream_id.startswith(f"{printer_id}-"):
            to_remove_chamber.append(stream_id)
            # Signal the generator to stop
            event = _disconnect_events.get(stream_id)
            if event:
                event.set()
            try:
                writer.close()
                stopped += 1
                logger.info("Closed chamber image connection for stream %s", stream_id)
            except OSError as e:
                logger.warning("Error stopping chamber stream %s: %s", stream_id, e)

    for stream_id in to_remove_chamber:
        _active_chamber_streams.pop(stream_id, None)
        _disconnect_events.pop(stream_id, None)
        _stream_last_frame_times.pop(stream_id, None)

    logger.info("Stopped %s camera stream(s) for printer %s", stopped, printer_id)
    return {"stopped": stopped}


@router.get("/{printer_id}/camera/snapshot")
async def camera_snapshot(
    printer_id: int,
    _: None = RequireCameraStreamTokenIfAuthEnabled,
):
    """Capture a single frame from the printer camera.

    Returns a JPEG image.

    Requires a stream token query param (?token=xxx) when auth is enabled.
    """
    import tempfile
    from pathlib import Path

    # Fetch the printer in a short-lived session and release the pooled DB
    # connection BEFORE the camera capture below (up to 15s, longer under a
    # saturated FTP/camera pool). Holding a Depends(get_db) session across the
    # grab pinned one connection per snapshot — and the cam wall polls this
    # per tile every 8s — so overlapping captures could pile up connections on
    # a large farm (issue #2572, sibling of the camera_stream fix). Everything
    # below reads only already-loaded scalar columns (expire_on_commit=False).
    async with database.async_session() as db:
        printer = await get_printer_or_404(printer_id, db)

    # Check for external camera first
    if printer.external_camera_enabled and printer.external_camera_url:
        from backend.app.services.external_camera import capture_frame

        frame_data = await capture_frame(
            printer.external_camera_url,
            printer.external_camera_type,
            timeout=15,
            snapshot_url=printer.external_camera_snapshot_url,
        )
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

    # Reuse the fan-out broadcaster's buffered frame when a viewer is already
    # watching — avoids opening a second concurrent RTSP socket on printers
    # that allow only one camera connection (e.g. X2D firmware 01.01.00.00;
    # see #1271). Buffered frame is <1s old while a viewer is connected.
    buffered = try_get_active_buffered_frame(printer_id)
    if buffered:
        return Response(
            content=buffered,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Content-Disposition": f'inline; filename="snapshot_{printer_id}.jpg"',
            },
        )

    # Create temporary file for the snapshot (0600 so only the app user can read it)
    fd, tmp_name = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    temp_path = Path(tmp_name)
    temp_path.chmod(0o600)

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


@router.post("/{printer_id}/camera/diagnose")
async def diagnose_camera_route(
    printer_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Run staged diagnostics for a printer's camera path.

    Returns a structured result the frontend renders inline so users can
    self-diagnose "connection lost" before opening a ticket. See
    ``camera_diagnose`` for stage details and the live-stream shortcut.
    """
    import time

    from backend.app.services.camera_diagnose import diagnose_camera

    printer = await get_printer_or_404(printer_id, db)

    # Look up live-stream evidence so the diagnostic can short-circuit
    # instead of fighting a viewer for the printer's single camera slot.
    has_live = is_stream_active(printer_id)
    last_ts = _last_frame_times.get(printer_id) if has_live else None
    live_age = (time.time() - last_ts) if (has_live and last_ts) else None

    result = await diagnose_camera(
        ip_address=printer.ip_address,
        access_code=printer.access_code,
        model=printer.model,
        printer_id=printer_id,
        has_live_stream=has_live,
        live_frame_age_seconds=live_age,
    )
    return result.to_dict()


@router.get("/{printer_id}/camera/status")
async def camera_status(
    printer_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CAMERA_VIEW),
):
    """Get the status of an active camera stream.

    Returns whether a stream is active and when the last frame was received.
    Used by the frontend to detect stalled streams and auto-reconnect.
    """
    import time

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

    # Get timing information
    current_time = time.time()
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
    use_external: bool | None = None,
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
        use_external: If True, prefer external camera over built-in. When omitted
            (None), defaults to the printer's external_camera_enabled setting —
            mirroring the runtime auto-check at print start (main.py). Without
            this default the UI's manual check would always use the built-in
            camera, mismatching the reference saved during calibration (#1359).
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

    if use_external is None:
        use_external = bool(
            printer.external_camera_enabled and printer.external_camera_url and printer.external_camera_type
        )

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
        external_camera_snapshot_url=printer.external_camera_snapshot_url if printer.external_camera_enabled else None,
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
    use_external: bool | None = None,
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
        use_external: If True, prefer external camera over built-in. When omitted
            (None), defaults to the printer's external_camera_enabled setting so
            calibration captures from the same source the runtime auto-check
            uses at print start (#1359).

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

    if use_external is None:
        use_external = bool(
            printer.external_camera_enabled and printer.external_camera_url and printer.external_camera_type
        )

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
        external_camera_snapshot_url=printer.external_camera_snapshot_url if printer.external_camera_enabled else None,
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
    _: None = RequireCameraStreamTokenIfAuthEnabled,
):
    """Get thumbnail image for a calibration reference.

    Requires a stream token query param (?token=xxx) when auth is enabled.
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


def _scan_bambu_ffmpeg_pids() -> list[int]:
    """Scan /proc for ffmpeg processes that are ours.

    Two shapes are matched, both unambiguously Bambuddy's:
    - Bambu RTSP: no other software connects to ``rtsp(s)://bblp:``.
    - External USB (V4L2): an ffmpeg spawned with ``-f v4l2`` is our USB camera
      stream (#2675). Only orphans are killed — the caller excludes PIDs still in
      ``_active_streams``, so a live USB stream (now registered there) is spared.

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
                if b"ffmpeg" not in cmdline:
                    continue
                # Match both rtsp:// (via TLS proxy) and rtsps:// (direct), plus
                # the `-f v4l2` input flag our USB camera command always carries.
                if b"rtsp://bblp:" in cmdline or b"rtsps://bblp:" in cmdline or b"v4l2" in cmdline:
                    pids.append(int(entry))
            except (OSError, PermissionError, ValueError):
                continue
    except OSError:
        pass
    return pids


async def cleanup_orphaned_streams():
    """Clean up orphaned ffmpeg processes and stale stream entries.

    Called periodically from the background task loop in main.py.

    Three-layer cleanup:
    1. /proc scan — finds ALL Bambu ffmpeg processes on the system, even those
       from previous app sessions. This is the nuclear safety net.
    2. _spawned_ffmpeg_pids — tracks PIDs spawned this session, catches orphans
       that were removed from _active_streams but not killed.
    3. _active_streams — kills stale entries with no recent frames.
    """
    import os
    import signal
    import time

    cleaned = 0
    now = time.time()

    # Collect PIDs that are legitimately in-use (active stream, process alive)
    active_pids = {proc.pid for proc in _active_streams.values() if proc.returncode is None}

    # Also exclude PIDs from one-shot snapshot captures (Obico detection, finish photos, etc.)
    from backend.app.services.camera import _active_capture_pids

    active_pids |= _active_capture_pids

    # 1. /proc scan — catch ALL orphaned Bambu ffmpeg processes on the system.
    #    Any ffmpeg with rtsp(s)://bblp: that is NOT in an active stream is orphaned.
    for pid in _scan_bambu_ffmpeg_pids():
        if pid in active_pids:
            continue
        logger.info("Killing orphaned ffmpeg process found via /proc (pid=%d)", pid)
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        _spawned_ffmpeg_pids.pop(pid, None)
        cleaned += 1

    # 2. Clean up _spawned_ffmpeg_pids entries for dead processes
    for pid in list(_spawned_ffmpeg_pids):
        try:
            os.kill(pid, 0)  # existence check
        except (ProcessLookupError, OSError):
            _spawned_ffmpeg_pids.pop(pid, None)

    # 3. Clean up _active_streams entries with dead processes
    dead_streams = [sid for sid, proc in _active_streams.items() if proc.returncode is not None]
    for sid in dead_streams:
        proc = _active_streams.pop(sid, None)
        if proc:
            _spawned_ffmpeg_pids.pop(proc.pid, None)
        cleaned += 1

    # 4. Kill stale active streams (alive but no frames for >30s)
    # Uses per-stream timestamps to avoid false "fresh" readings from newer streams
    for sid, proc in list(_active_streams.items()):
        if proc.returncode is not None:
            continue
        # Per-stream frame time is authoritative; fall back to per-printer
        stream_last_frame = _stream_last_frame_times.get(sid)
        if stream_last_frame is None:
            try:
                printer_id = int(sid.split("-", 1)[0])
            except (ValueError, IndexError):
                continue
            stream_last_frame = _last_frame_times.get(printer_id)
        spawn_time = _spawned_ffmpeg_pids.get(proc.pid, now)
        if stream_last_frame is None:
            stream_last_frame = spawn_time
        if now - spawn_time > 60 and now - stream_last_frame > 30:
            logger.info("Killing stale ffmpeg stream %s (no frames for %.0fs)", sid, now - stream_last_frame)
            # Signal the generator to stop reconnecting
            event = _disconnect_events.get(sid)
            if event:
                event.set()
            try:
                proc.kill()
                # Bounded (#2580): an unreaped SIGKILLed ffmpeg must not hang
                # the periodic cleanup loop — this janitor is the safety net
                # that recovers stalled streams, so it can least afford to
                # block. The /proc scan above retries the kill next pass.
                await asyncio.wait_for(proc.wait(), timeout=_FFMPEG_KILL_TIMEOUT)
            except (ProcessLookupError, OSError):
                pass
            except TimeoutError:
                logger.error(
                    "ffmpeg (pid=%d) did not exit within %.1fs of SIGKILL; abandoning wait (stream_id=%s)",
                    proc.pid,
                    _FFMPEG_KILL_TIMEOUT,
                    sid,
                )
            _active_streams.pop(sid, None)
            _disconnect_events.pop(sid, None)
            _stream_last_frame_times.pop(sid, None)
            _spawned_ffmpeg_pids.pop(proc.pid, None)
            cleaned += 1

    # 4. Clean stale chamber stream entries
    dead_chamber = [sid for sid, (_reader, writer) in _active_chamber_streams.items() if writer.is_closing()]
    for sid in dead_chamber:
        _active_chamber_streams.pop(sid, None)
        cleaned += 1

    if cleaned:
        logger.info("Cleaned up %d orphaned camera stream(s)", cleaned)
