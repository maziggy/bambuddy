"""Camera streaming API endpoints for Bambu Lab printers."""

import asyncio
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.user import User
from backend.app.services.camera import (
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
                import time

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
    disconnect_event: asyncio.Event | None = None,
    printer_id: int | None = None,
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
    # -q:v 5: Quality (lower = better, 2-10 is good range)
    # -r: Output framerate
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
        "-f",
        "mjpeg",
        "-q:v",
        "5",
        "-r",
        str(fps),
        "-an",  # No audio
        "-",  # Output to stdout
    ]

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
        buffer = b""
        jpeg_start = b"\xff\xd8"
        jpeg_end = b"\xff\xd9"

        while True:
            # Check if client disconnected
            if disconnect_event and disconnect_event.is_set():
                logger.info("Client disconnected, stopping stream %s", stream_id)
                break

            try:
                # Read chunk from ffmpeg - use longer timeout for network hiccups
                chunk = await asyncio.wait_for(process.stdout.read(8192), timeout=30.0)

                if not chunk:
                    logger.warning("Camera stream ended (no more data)")
                    break

                buffer += chunk

                # Find complete JPEG frames in buffer
                while True:
                    start_idx = buffer.find(jpeg_start)
                    if start_idx == -1:
                        # No start marker, clear buffer up to last 2 bytes
                        buffer = buffer[-2:] if len(buffer) > 2 else buffer
                        break

                    # Trim anything before the start marker
                    if start_idx > 0:
                        buffer = buffer[start_idx:]

                    end_idx = buffer.find(jpeg_end, 2)  # Skip first 2 bytes
                    if end_idx == -1:
                        # No end marker yet, wait for more data
                        break

                    # Extract complete frame
                    frame = buffer[: end_idx + 2]
                    buffer = buffer[end_idx + 2 :]

                    # Save frame to buffer for photo capture and track timestamp
                    if printer_id is not None:
                        import time

                        _last_frames[printer_id] = frame
                        _last_frame_times[printer_id] = time.time()

                    # Yield frame in MJPEG format
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


@router.get("/{printer_id}/camera/stream")
async def camera_stream(
    printer_id: int,
    request: Request,
    fps: int = 10,
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
    import uuid

    printer = await get_printer_or_404(printer_id, db)

    # Check for external camera first
    if printer.external_camera_enabled and printer.external_camera_url:
        import time

        from backend.app.services.external_camera import generate_mjpeg_stream

        # Limit external camera FPS to reduce browser load
        fps = min(max(fps, 1), 15)
        logger.info(
            "Using external camera (%s) for printer %s at %s fps", printer.external_camera_type, printer_id, fps
        )

        # Track stream start
        _stream_start_times[printer_id] = time.time()
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
                    current_time = time.time()
                    elapsed = current_time - last_yield_time
                    if elapsed < frame_interval:
                        await asyncio.sleep(frame_interval - elapsed)
                    last_yield_time = time.time()
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

    # Validate FPS - A1/P1 models max out at ~5 FPS
    if is_chamber_image_model(printer.model):
        fps = min(max(fps, 1), 5)
    else:
        fps = min(max(fps, 1), 30)

    # Generate unique stream ID for tracking
    stream_id = f"{printer_id}-{uuid.uuid4().hex[:8]}"

    # Create disconnect event that will be set when client disconnects
    disconnect_event = asyncio.Event()

    # Choose the appropriate stream generator based on model
    if is_chamber_image_model(printer.model):
        stream_generator = generate_chamber_mjpeg_stream
        logger.info("Using chamber image protocol for %s", printer.model)
    else:
        stream_generator = generate_rtsp_mjpeg_stream
        logger.info("Using RTSP protocol for %s", printer.model)

    # Track stream start time
    import time

    _stream_start_times[printer_id] = time.time()

    async def stream_with_disconnect_check():
        """Wrapper generator that monitors for client disconnect."""
        try:
            async for chunk in stream_generator(
                ip_address=printer.ip_address,
                access_code=printer.access_code,
                model=printer.model,
                fps=fps,
                stream_id=stream_id,
                disconnect_event=disconnect_event,
                printer_id=printer_id,
            ):
                # Check if client is still connected
                if await request.is_disconnected():
                    logger.info("Client disconnected detected for stream %s", stream_id)
                    disconnect_event.set()
                    break
                yield chunk
        except asyncio.CancelledError:
            logger.info("Stream %s cancelled", stream_id)
            disconnect_event.set()
        except GeneratorExit:
            logger.info("Stream %s generator closed", stream_id)
            disconnect_event.set()
        finally:
            disconnect_event.set()
            # Give a moment for the inner generator to clean up
            await asyncio.sleep(0.1)

    return StreamingResponse(
        stream_with_disconnect_check(),
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
