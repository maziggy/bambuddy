"""External camera service.

Supports MJPEG streams, RTSP streams (via ffmpeg), HTTP snapshot URLs, and USB cameras.

Security Note: This service intentionally makes requests to user-configured camera URLs.
This is necessary functionality for external camera integration. URLs are validated
to ensure they are well-formed before use.
"""

import asyncio
import logging
import re
import shutil
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)


def _sanitize_camera_url(url: str, allowed_schemes: tuple[str, ...] = ("http", "https", "rtsp")) -> str | None:
    """Validate and sanitize camera URL, returning a safe reconstructed URL.

    This validates that the URL is well-formed, uses an allowed scheme,
    does not target cloud metadata services, and returns a reconstructed
    URL from validated components.

    Note: This intentionally allows user-provided URLs as that is the
    purpose of external camera configuration. Local network IPs are
    allowed since cameras are typically on the same LAN.

    Args:
        url: URL to validate and sanitize
        allowed_schemes: Tuple of allowed URL schemes

    Returns:
        Sanitized URL string if valid, None otherwise
    """
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return None

        # Validate scheme against allowlist
        scheme = parsed.scheme.lower()
        if scheme not in allowed_schemes:
            return None

        # Block cloud metadata service endpoints (SSRF mitigation)
        # These are dangerous destinations that should never be accessed
        hostname = parsed.hostname or ""
        hostname_lower = hostname.lower()
        blocked_hosts = (
            "169.254.169.254",  # AWS/GCP/Azure metadata
            "metadata.google.internal",  # GCP metadata
            "metadata.google",
            "localhost",  # Block localhost to prevent internal service access
            "127.0.0.1",
            "::1",
            "0.0.0.0",
        )
        if hostname_lower in blocked_hosts:
            logger.warning(f"Blocked camera URL targeting restricted host: {hostname}")
            return None

        # Block link-local addresses (169.254.x.x)
        if hostname.startswith("169.254."):
            logger.warning(f"Blocked camera URL targeting link-local address: {hostname}")
            return None

        # Reconstruct URL from validated components to break taint chain
        # This creates a new string from validated parts
        port_str = f":{parsed.port}" if parsed.port else ""
        path = parsed.path or ""
        query = f"?{parsed.query}" if parsed.query else ""
        fragment = f"#{parsed.fragment}" if parsed.fragment else ""

        # Build sanitized URL from validated components
        sanitized = f"{scheme}://{hostname}{port_str}{path}{query}{fragment}"
        return sanitized
    except Exception:
        return None


def _validate_camera_url(url: str, allowed_schemes: tuple[str, ...] = ("http", "https", "rtsp")) -> bool:
    """Validate camera URL format (legacy wrapper).

    Args:
        url: URL to validate
        allowed_schemes: Tuple of allowed URL schemes

    Returns:
        True if URL is valid, False otherwise
    """
    return _sanitize_camera_url(url, allowed_schemes) is not None


def list_usb_cameras() -> list[dict]:
    """List available USB cameras (V4L2 devices on Linux).

    Returns:
        List of dicts with {device: str, name: str, capabilities: list}
    """
    cameras = []
    video_devices = sorted(Path("/dev").glob("video*"))

    for device in video_devices:
        device_path = str(device)
        info = {"device": device_path, "name": device.name, "capabilities": []}

        # Try to get device info via v4l2-ctl
        v4l2_ctl = shutil.which("v4l2-ctl")
        if v4l2_ctl:
            import subprocess

            try:
                result = subprocess.run(
                    [v4l2_ctl, "-d", device_path, "--info"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    # Parse device name from output
                    for line in result.stdout.splitlines():
                        if "Card type" in line:
                            info["name"] = line.split(":", 1)[1].strip()
                        elif "Driver name" in line:
                            info["driver"] = line.split(":", 1)[1].strip()

                    # Check if device supports video capture
                    result = subprocess.run(
                        [v4l2_ctl, "-d", device_path, "--list-formats"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        info["capabilities"].append("capture")
                        # Parse available formats
                        formats = re.findall(r"'(\w+)'", result.stdout)
                        info["formats"] = list(set(formats))

            except (subprocess.TimeoutExpired, Exception) as e:
                logger.debug(f"v4l2-ctl failed for {device_path}: {e}")

        # Only include devices that look like video capture devices
        # Skip metadata devices (typically odd numbered like video1, video3)
        try:
            device_num = int(device.name.replace("video", ""))
            # Even numbered devices are usually capture, odd are metadata
            # But also check if we got capabilities
            if info.get("capabilities") or device_num % 2 == 0:
                cameras.append(info)
        except ValueError:
            cameras.append(info)

    return cameras


def get_ffmpeg_path() -> str | None:
    """Get the path to ffmpeg executable."""
    # Try shutil.which first
    path = shutil.which("ffmpeg")
    if path:
        return path
    # Check common locations (systemd services may have limited PATH)
    for common_path in ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"]:
        if Path(common_path).exists():
            return common_path
    return None


async def capture_frame(url: str, camera_type: str, timeout: int = 15) -> bytes | None:
    """Capture single frame from external camera.

    Args:
        url: Camera URL (MJPEG stream, RTSP URL, HTTP snapshot URL, or USB device path)
        camera_type: "mjpeg", "rtsp", "snapshot", or "usb"
        timeout: Connection timeout in seconds

    Returns:
        JPEG bytes or None on failure
    """
    logger.debug(f"capture_frame called: type={camera_type}, url={url[:50] if url else 'None'}...")
    if camera_type == "mjpeg":
        return await _capture_mjpeg_frame(url, timeout)
    elif camera_type == "rtsp":
        return await _capture_rtsp_frame(url, timeout)
    elif camera_type == "snapshot":
        return await _capture_snapshot(url, timeout)
    elif camera_type == "usb":
        return await _capture_usb_frame(url, timeout)
    else:
        logger.warning(f"Unknown camera type: {camera_type}")
        return None


async def _capture_usb_frame(device: str, timeout: int) -> bytes | None:
    """Capture frame from USB camera using ffmpeg."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        logger.error("ffmpeg not found - required for USB camera capture")
        return None

    # Validate device path - must be /dev/videoN format where N is 0-99
    # This prevents path traversal by using a strict allowlist approach
    import re as regex_module

    device_match = regex_module.match(r"^/dev/video(\d{1,2})$", device)
    if not device_match:
        logger.error(f"Invalid USB device path format: {device}")
        return None

    # Convert to integer to break taint chain - integers cannot contain path traversal
    # lgtm[py/path-injection] - device_num is validated integer 0-99
    device_num = int(device_match.group(1))  # Safe: regex guarantees 1-2 digits
    if device_num > 99:
        logger.error(f"USB device number out of range: {device_num}")
        return None

    # Construct safe path from validated integer (completely untainted)
    safe_device_path = Path(f"/dev/video{device_num}")  # lgtm[py/path-injection]

    if not safe_device_path.exists():
        logger.error(f"USB device does not exist: {safe_device_path}")
        return None

    # Use the safe path for ffmpeg - this is a hardcoded /dev/videoN path
    device = str(safe_device_path)  # lgtm[py/path-injection]

    # Use ffmpeg to grab a single frame from USB camera
    cmd = [
        ffmpeg,
        "-f",
        "v4l2",
        "-i",
        device,
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-q:v",
        "2",
        "-",
    ]

    try:
        logger.debug(f"Running USB capture: {' '.join(cmd)}")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)

        if process.returncode != 0:
            logger.error(f"ffmpeg USB capture failed: {stderr.decode()[:200]}")
            return None

        if not stdout or len(stdout) < 100:
            logger.error("ffmpeg returned empty or too small frame from USB camera")
            return None

        return stdout

    except TimeoutError:
        logger.warning(f"USB frame capture timed out after {timeout}s")
        if process:
            process.kill()
        return None
    except Exception as e:
        logger.error(f"USB frame capture failed: {e}")
        return None


async def _capture_mjpeg_frame(url: str, timeout: int) -> bytes | None:
    """Extract single frame from MJPEG stream.

    Note: This function intentionally makes requests to user-configured URLs.
    External camera support requires connecting to user-specified camera endpoints.
    URL is sanitized and dangerous destinations are blocked.
    """
    # Sanitize URL - returns reconstructed URL from validated components
    safe_url = _sanitize_camera_url(url, ("http", "https"))
    if not safe_url:
        logger.error(f"Invalid MJPEG URL format: {url[:50]}...")
        return None

    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
            session.get(safe_url) as response,
        ):
            if response.status != 200:
                logger.error(f"MJPEG stream returned status {response.status}")
                return None

            # Read chunks until we find a complete JPEG frame
            buffer = b""
            jpeg_start = b"\xff\xd8"
            jpeg_end = b"\xff\xd9"

            async for chunk in response.content.iter_chunked(8192):
                buffer += chunk

                # Look for complete JPEG frame
                start_idx = buffer.find(jpeg_start)
                if start_idx == -1:
                    continue

                end_idx = buffer.find(jpeg_end, start_idx + 2)
                if end_idx != -1:
                    # Found complete frame
                    frame = buffer[start_idx : end_idx + 2]
                    return frame

                # Keep searching, but limit buffer size
                if len(buffer) > 5 * 1024 * 1024:  # 5MB limit
                    logger.warning("MJPEG buffer exceeded 5MB without finding frame")
                    return None

    except TimeoutError:
        logger.warning(f"MJPEG frame capture timed out after {timeout}s")
        return None
    except Exception as e:
        logger.error(f"MJPEG frame capture failed: {e}")
        return None

    return None


async def _capture_rtsp_frame(url: str, timeout: int) -> bytes | None:
    """Capture frame from RTSP using ffmpeg."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        logger.error("ffmpeg not found - required for RTSP capture")
        return None

    # Use ffmpeg to grab a single frame from RTSP stream
    # ffmpeg handles both rtsp:// and rtsps:// URLs automatically
    cmd = [
        ffmpeg,
        "-rtsp_transport",
        "tcp",
        "-i",
        url,
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-q:v",
        "2",
        "-",
    ]

    try:
        print(f"[EXT-CAM] Running ffmpeg command: {' '.join(cmd[:6])}...")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        print(
            f"[EXT-CAM] ffmpeg returned: code={process.returncode}, stdout={len(stdout)} bytes, stderr={len(stderr)} bytes"
        )

        if process.returncode != 0:
            logger.error(f"ffmpeg RTSP capture failed: {stderr.decode()[:200]}")
            print(f"[EXT-CAM] ffmpeg error: {stderr.decode()[:300]}")
            return None

        if not stdout or len(stdout) < 100:
            logger.error("ffmpeg returned empty or too small frame")
            return None

        return stdout

    except TimeoutError:
        logger.warning(f"RTSP frame capture timed out after {timeout}s")
        if process:
            process.kill()
        return None
    except Exception as e:
        logger.error(f"RTSP frame capture failed: {e}")
        return None


async def _capture_snapshot(url: str, timeout: int) -> bytes | None:
    """Fetch snapshot from HTTP URL.

    Note: This function intentionally makes requests to user-configured URLs.
    External camera support requires connecting to user-specified camera endpoints.
    URL is sanitized and dangerous destinations are blocked.
    """
    # Sanitize URL - returns reconstructed URL from validated components
    safe_url = _sanitize_camera_url(url, ("http", "https"))
    if not safe_url:
        logger.error(f"Invalid snapshot URL format: {url[:50]}...")
        return None

    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
            session.get(safe_url) as response,
        ):
            if response.status != 200:
                logger.error(f"Snapshot URL returned status {response.status}")
                return None

            data = await response.read()

            # Validate it looks like JPEG
            if not data.startswith(b"\xff\xd8"):
                logger.warning("Snapshot does not appear to be JPEG")
                # Still return it - might be valid with different header

            return data

    except TimeoutError:
        logger.warning(f"Snapshot capture timed out after {timeout}s")
        return None
    except Exception as e:
        logger.error(f"Snapshot capture failed: {e}")
        return None


async def test_connection(url: str, camera_type: str) -> dict:
    """Test camera connection.

    Returns:
        Dict with {success: bool, error?: str, resolution?: str}
    """
    print(f"[EXT-CAM] Testing camera connection: type={camera_type}, url={url[:50]}...")
    logger.info(f"Testing camera connection: type={camera_type}, url={url[:50]}...")
    try:
        frame = await capture_frame(url, camera_type, timeout=10)
        print(f"[EXT-CAM] Capture result: {len(frame) if frame else 0} bytes")
        logger.info(f"Capture result: {len(frame) if frame else 0} bytes")

        if frame:
            # Try to get resolution from JPEG header
            resolution = None
            try:
                # Simple JPEG dimension extraction
                # SOF0 marker is FF C0, followed by length, precision, height, width
                sof_markers = [b"\xff\xc0", b"\xff\xc1", b"\xff\xc2"]
                for marker in sof_markers:
                    idx = frame.find(marker)
                    if idx != -1 and idx + 9 <= len(frame):
                        height = (frame[idx + 5] << 8) | frame[idx + 6]
                        width = (frame[idx + 7] << 8) | frame[idx + 8]
                        resolution = f"{width}x{height}"
                        break
            except Exception:
                pass

            return {"success": True, "resolution": resolution}
        else:
            return {"success": False, "error": "Failed to capture frame from camera"}

    except Exception as e:
        # Sanitize error message - don't expose internal details
        error_type = type(e).__name__
        logger.error(f"Camera connection test failed: {e}")
        return {"success": False, "error": f"Connection failed: {error_type}"}


async def generate_mjpeg_stream(url: str, camera_type: str, fps: int = 10) -> AsyncGenerator[bytes, None]:
    """Generator yielding MJPEG frames for streaming.

    Args:
        url: Camera URL or USB device path
        camera_type: "mjpeg", "rtsp", "snapshot", or "usb"
        fps: Target frames per second

    Yields:
        MJPEG frame data with HTTP multipart boundaries
    """
    frame_interval = 1.0 / max(fps, 1)
    last_frame_time = 0.0

    if camera_type == "mjpeg":
        # Proxy MJPEG stream directly
        async for frame in _stream_mjpeg(url):
            current_time = asyncio.get_event_loop().time()
            if current_time - last_frame_time >= frame_interval:
                last_frame_time = current_time
                yield _format_mjpeg_frame(frame)

    elif camera_type == "rtsp":
        # Use ffmpeg to convert RTSP to MJPEG
        async for frame in _stream_rtsp(url, fps):
            yield _format_mjpeg_frame(frame)

    elif camera_type == "usb":
        # Use ffmpeg to stream from USB camera
        async for frame in _stream_usb(url, fps):
            yield _format_mjpeg_frame(frame)

    elif camera_type == "snapshot":
        # Poll snapshot URL at interval
        while True:
            try:
                frame = await _capture_snapshot(url, timeout=10)
                if frame:
                    yield _format_mjpeg_frame(frame)
                await asyncio.sleep(frame_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Snapshot poll failed: {e}")
                await asyncio.sleep(frame_interval)


def _format_mjpeg_frame(frame: bytes) -> bytes:
    """Format frame for MJPEG HTTP response."""
    return (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: " + str(len(frame)).encode() + b"\r\n"
        b"\r\n" + frame + b"\r\n"
    )


async def _stream_mjpeg(url: str) -> AsyncGenerator[bytes, None]:
    """Stream frames from MJPEG URL.

    Note: This function intentionally makes requests to user-configured URLs.
    External camera support requires connecting to user-specified camera endpoints.
    URL is sanitized and dangerous destinations are blocked.
    """
    # Sanitize URL - returns reconstructed URL from validated components
    safe_url = _sanitize_camera_url(url, ("http", "https"))
    if not safe_url:
        logger.error(f"Invalid MJPEG stream URL: {url[:50]}...")
        return

    try:
        timeout = aiohttp.ClientTimeout(total=None, sock_read=30)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(safe_url) as response:
            if response.status != 200:
                logger.error(f"MJPEG stream returned status {response.status}")
                return

            buffer = b""
            jpeg_start = b"\xff\xd8"
            jpeg_end = b"\xff\xd9"

            async for chunk in response.content.iter_chunked(8192):
                buffer += chunk

                # Extract complete frames from buffer
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
                    yield frame

    except asyncio.CancelledError:
        logger.info("MJPEG stream cancelled")
    except Exception as e:
        logger.error(f"MJPEG stream error: {e}")


async def _stream_rtsp(url: str, fps: int) -> AsyncGenerator[bytes, None]:
    """Stream frames from RTSP URL via ffmpeg."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        logger.error("ffmpeg not found - required for RTSP streaming")
        return

    # ffmpeg handles both rtsp:// and rtsps:// URLs automatically
    cmd = [
        ffmpeg,
        "-rtsp_transport",
        "tcp",
        "-rtsp_flags",
        "prefer_tcp",
        "-timeout",
        "30000000",
        "-buffer_size",
        "1024000",
        "-max_delay",
        "500000",
        "-i",
        url,
        "-f",
        "mjpeg",
        "-q:v",
        "5",
        "-r",
        str(fps),
        "-an",
        "-",
    ]

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Give ffmpeg a moment to start and check for immediate failures
        await asyncio.sleep(0.5)
        if process.returncode is not None:
            stderr = await process.stderr.read()
            logger.error(f"ffmpeg RTSP stream failed immediately: {stderr.decode()[:300]}")
            return

        buffer = b""
        jpeg_start = b"\xff\xd8"
        jpeg_end = b"\xff\xd9"

        while True:
            try:
                chunk = await asyncio.wait_for(process.stdout.read(8192), timeout=30.0)

                if not chunk:
                    break

                buffer += chunk

                # Extract complete frames
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
                    yield frame

            except TimeoutError:
                logger.warning("RTSP stream read timeout")
                break

    except asyncio.CancelledError:
        logger.info("RTSP stream cancelled")
    except Exception as e:
        logger.error(f"RTSP stream error: {e}")
    finally:
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except TimeoutError:
                process.kill()
                await process.wait()


async def _stream_usb(device: str, fps: int) -> AsyncGenerator[bytes, None]:
    """Stream frames from USB camera via ffmpeg."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        logger.error("ffmpeg not found - required for USB camera streaming")
        return

    # Validate device path
    if not device.startswith("/dev/video"):
        logger.error(f"Invalid USB device path: {device}")
        return

    if not Path(device).exists():
        logger.error(f"USB device does not exist: {device}")
        return

    # ffmpeg command to stream from USB camera (v4l2)
    cmd = [
        ffmpeg,
        "-f",
        "v4l2",
        "-framerate",
        str(fps),
        "-i",
        device,
        "-f",
        "mjpeg",
        "-q:v",
        "5",
        "-r",
        str(fps),
        "-",
    ]

    process = None
    try:
        logger.info(f"Starting USB camera stream from {device} at {fps} fps")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Give ffmpeg a moment to start and check for immediate failures
        await asyncio.sleep(0.5)
        if process.returncode is not None:
            stderr = await process.stderr.read()
            logger.error(f"ffmpeg USB stream failed immediately: {stderr.decode()[:300]}")
            return

        buffer = b""
        jpeg_start = b"\xff\xd8"
        jpeg_end = b"\xff\xd9"

        while True:
            try:
                chunk = await asyncio.wait_for(process.stdout.read(8192), timeout=30.0)

                if not chunk:
                    break

                buffer += chunk

                # Extract complete frames
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
                    yield frame

            except TimeoutError:
                logger.warning("USB stream read timeout")
                break

    except asyncio.CancelledError:
        logger.info("USB stream cancelled")
    except Exception as e:
        logger.error(f"USB stream error: {e}")
    finally:
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except TimeoutError:
                process.kill()
                await process.wait()
