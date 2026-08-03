"""External camera service.

Supports MJPEG streams, RTSP streams (via ffmpeg), HTTP snapshot URLs, and USB cameras.

Security Note: This service intentionally makes requests to user-configured camera URLs.
This is necessary functionality for external camera integration. URLs are validated
to ensure they are well-formed before use.
"""

import asyncio
import functools
import logging
import re
import shutil
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

from backend.app.core.logging_filters import redact_url_credentials

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
            "0.0.0.0",  # nosec B104
        )
        if hostname_lower in blocked_hosts:
            logger.warning("Blocked camera URL targeting restricted host: %s", hostname)
            return None

        # Block link-local addresses (169.254.x.x)
        if hostname.startswith("169.254."):
            logger.warning("Blocked camera URL targeting link-local address: %s", hostname)
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
    except ValueError:
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
                logger.debug("v4l2-ctl failed for %s: %s", device_path, e)

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


# In-flight one-shot captures, keyed by (url, camera_type, snapshot_url) —
# the tuple that actually identifies the physical resource being contended
# (#2707 comment thread, following #2705's shape for the built-in path).
#
# V4L2 USB devices allow exactly one open handle, and is_stream_active() /
# try_get_active_buffered_frame() (#2707) only stop a one-shot capturer from
# competing with the fan-out live view. They do nothing for capturer-vs-
# capturer with no viewer attached, where every consumer correctly concludes
# it isn't competing with a viewer and then collides with the others -
# exactly the #2705 report, just for this module's callers instead of
# capture_camera_frame_bytes()'s (Obico polling, the in-print frame bank,
# the finish-photo moment, plate detection, and the notification snapshot
# all reach capture_frame() independently).
#
# snapshot_url is part of the key (not just url/camera_type) because it
# routes to a completely different endpoint (#1177) - two printers that
# share a camera_url but differ only in snapshot_url must not coalesce.
_inflight_captures: dict[tuple[str, str, str | None], asyncio.Task[bytes | None]] = {}


def capture_in_flight(url: str, camera_type: str, snapshot_url: str | None = None) -> bool:
    """Return True iff a one-shot capture for this key is running right now.

    Mirrors camera.py's capture_in_flight() for the built-in path - for a
    caller that needs to know it will JOIN someone else's capture rather
    than open its own connection. Ordinary consumers should ignore this:
    they want "a recent frame", and capture_frame() already does the right
    thing for them.
    """
    task = _inflight_captures.get((url, camera_type, snapshot_url))
    return task is not None and not task.done()


def _discard_inflight_capture(key: tuple[str, str, str | None], task: asyncio.Task) -> None:
    """Done-callback: drop the finished task from the in-flight registry.

    Guarded on identity so a slow task that finishes after a newer capture
    has registered for the same key can't evict its successor.

    Also retrieves the exception, if any: the leader normally awaits the
    task and would surface it, but a leader whose own caller was cancelled
    leaves nobody to collect it, and an unretrieved task exception is
    logged by asyncio as a warning with a traceback at an arbitrary later
    point otherwise.
    """
    if _inflight_captures.get(key) is task:
        del _inflight_captures[key]
    if not task.cancelled() and task.exception() is not None:
        logger.debug("In-flight external-camera capture for %s ended in an exception", _log_key(key))


def _log_key(key: tuple[str, str, str | None]) -> str:
    """Render an in-flight key for a log line, with credentials redacted.

    Unlike camera.py's coalescing — which is keyed by IP address and so has
    nothing to hide — these keys carry the camera URL, and an RTSP camera URL
    routinely embeds ``user:pass@``. Redact before truncating: slicing first
    can cut the URL short of the ``@`` the pattern anchors on and leave the
    password in the log, which is why every other URL log in this module does
    it in this order.
    """
    return redact_url_credentials(key[0])[:50] if key[0] else "None"


async def capture_frame(
    url: str,
    camera_type: str,
    timeout: int = 15,
    snapshot_url: str | None = None,
) -> bytes | None:
    """Capture single frame from external camera.

    Args:
        url: Live-stream URL (MJPEG stream, RTSP URL, HTTP snapshot URL, or USB device path).
        camera_type: "mjpeg", "rtsp", "snapshot", or "usb".
        timeout: Connection timeout in seconds. Applies to this caller's own
            wait, including when it joins another caller's capture - call
            sites disagree about the value, and a follower must not silently
            inherit the leader's deadline in either direction.
        snapshot_url: Optional override for single-frame capture. When set, fetched
            via plain HTTP GET regardless of `camera_type`. Bypasses MJPEG warm-up
            handling on sources that expose a dedicated frame endpoint (e.g. go2rtc's
            `/api/frame.jpeg` reliably returns a clean image while the MJPEG stream's
            first frame is often the encoder's stale keyframe). #1177.

    Returns:
        JPEG bytes or None on failure

    Concurrent callers for the same (url, camera_type, snapshot_url) share
    one capture (#2705-shape fix, filed for the external-camera path as a
    follow-up on #2707): the first opens the connection, everyone arriving
    while it's in flight awaits the same result. This coalesces; it does
    not cache - a call that arrives after the previous capture finished
    always captures fresh, since plate detection and the finish-photo path
    judge a running print from these frames and a stale one there is worse
    than a slow one (#1397).
    """
    key = (url, camera_type, snapshot_url)

    # A follower whose leader fails takes a turn of its own rather than
    # inheriting a failure it never had a chance to avoid - by then the
    # leader has finished, so there's no connection left to compete with.
    # Bounded at two rounds: if the capture we joined AND its replacement
    # both failed, a third attempt won't help, and this caller has already
    # spent its patience.
    for _ in range(2):
        leader = _inflight_captures.get(key)
        if leader is None or leader.done():
            break
        try:
            frame = await asyncio.wait_for(asyncio.shield(leader), timeout=timeout)
        except TimeoutError:
            # shield() keeps the capture running for whoever else is still
            # waiting on it - giving up is this caller's decision alone.
            logger.warning(
                "Gave up waiting %ss on the in-flight external-camera capture for %s", timeout, _log_key(key)
            )
            return None
        except asyncio.CancelledError:
            # Distinguish "the capture I joined was cancelled" from "I was
            # cancelled". Only the former is ours to recover from.
            if not leader.cancelled():
                raise
            logger.info("In-flight external-camera capture for %s was cancelled; capturing our own", _log_key(key))
            continue
        if frame is not None:
            logger.debug(
                "Reusing in-flight external-camera capture for %s: %d bytes (no second connection opened)",
                _log_key(key),
                len(frame),
            )
            return frame
        logger.debug("In-flight external-camera capture for %s failed; capturing our own", _log_key(key))
    else:
        return None

    task = asyncio.create_task(_capture_frame_uncoalesced(url, camera_type, timeout, snapshot_url))
    _inflight_captures[key] = task
    task.add_done_callback(functools.partial(_discard_inflight_capture, key))
    # No wait_for here: this caller IS the capture, and each dispatched
    # _capture_* function already enforces `timeout` internally, where it
    # can also kill the ffmpeg process - a second deadline on top would
    # abandon the subprocess instead of killing it. shield() so a cancelled
    # leader (a client navigating away mid-request is routine) doesn't take
    # the capture down with it - followers already waiting on it still get
    # their frame.
    return await asyncio.shield(task)


async def _capture_frame_uncoalesced(
    url: str,
    camera_type: str,
    timeout: int,
    snapshot_url: str | None,
) -> bytes | None:
    """Open a connection and capture one frame. See capture_frame().

    Callers want that wrapper, not this: it opens a connection
    unconditionally, which is the collision #2705/#2707 are about.

    Failure is reported as ``None``, never as an exception. That is load-
    bearing now that captures are shared: the coalescing wrapper hands one
    task's outcome to every caller waiting on it, and it can only give a
    follower its own turn for an outcome it can recognise. An exception
    escaping here would instead propagate to every follower at once —
    turning one caller's failure into N — and none of them would retry.
    The per-type helpers below each catch what they expect and return None,
    but they catch narrowly (``aiohttp.ClientError``/``OSError``/timeouts),
    so this is the structural guarantee rather than one contingent on their
    coverage. Mirrors ``_capture_camera_frame_bytes_uncoalesced`` in
    camera.py, which ends in the same blanket catch for the same reason.
    """
    try:
        if snapshot_url:
            # Redact before truncating — slicing first can cut the URL short of the
            # ``@`` the pattern anchors on and leave the password in the log.
            logger.debug("capture_frame using snapshot override url=%s...", redact_url_credentials(snapshot_url)[:50])
            return await _capture_snapshot(snapshot_url, timeout)
        logger.debug(
            "capture_frame called: type=%s, url=%s...",
            camera_type,
            redact_url_credentials(url)[:50] if url else "None",
        )
        if camera_type == "mjpeg":
            return await _capture_mjpeg_frame(url, timeout)
        elif camera_type == "rtsp":
            return await _capture_rtsp_frame(url, timeout)
        elif camera_type == "snapshot":
            return await _capture_snapshot(url, timeout)
        elif camera_type == "usb":
            return await _capture_usb_frame(url, timeout)
        else:
            logger.warning("Unknown camera type: %s", camera_type)
            return None
    except asyncio.CancelledError:
        # Cancellation is not a capture failure and must stay distinguishable:
        # the wrapper checks ``leader.cancelled()`` to decide whether a
        # follower may take its own turn.
        raise
    except Exception:
        logger.exception("External camera capture failed for %s", redact_url_credentials(url)[:50] if url else "None")
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
        logger.error("Invalid USB device path format: %s", device)
        return None

    # Convert to integer to break taint chain - integers cannot contain path traversal
    # lgtm[py/path-injection] - device_num is validated integer 0-99
    device_num = int(device_match.group(1))  # Safe: regex guarantees 1-2 digits
    if device_num > 99:
        logger.error("USB device number out of range: %s", device_num)
        return None

    # Construct safe path from validated integer (completely untainted)
    safe_device_path = Path(f"/dev/video{device_num}")  # lgtm[py/path-injection]

    if not safe_device_path.exists():
        logger.error("USB device does not exist: %s", safe_device_path)
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
        logger.debug("Running USB capture: %s", " ".join(cmd))
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)

        if process.returncode != 0:
            logger.error("ffmpeg USB capture failed: %s", stderr.decode()[:200])
            return None

        if not stdout or len(stdout) < 100:
            logger.error("ffmpeg returned empty or too small frame from USB camera")
            return None

        return stdout

    except TimeoutError:
        logger.warning("USB frame capture timed out after %ss", timeout)
        if process:
            process.kill()
        return None
    except OSError as e:
        logger.error("USB frame capture failed: %s", e)
        return None


async def _capture_mjpeg_frame(url: str, timeout: int) -> bytes | None:
    """Extract a single representative frame from an MJPEG stream.

    Many MJPEG sources — go2rtc most notably (#1177), and several IP cameras —
    emit a "warm-up" frame on the byte that follows connection accept: usually
    the last keyframe held in the encoder, which is often black or stale until
    the encoder catches up to live content. To return a frame that's actually
    representative of the scene we read past the first frame and return the
    second; if the connection closes / times out / hits the buffer cap before
    a second frame ever arrives we fall back to the first so callers still
    get *something* (better than degrading slow / single-frame streams to None,
    which would regress every code path that consumed pre-fix behaviour).

    Note: this function intentionally makes requests to user-configured URLs.
    External camera support requires connecting to user-specified camera
    endpoints. URL is sanitized and dangerous destinations are blocked.
    """
    safe_url = _sanitize_camera_url(url, ("http", "https"))
    if not safe_url:
        logger.error("Invalid MJPEG URL format: %s...", redact_url_credentials(url)[:50])
        return None

    jpeg_start = b"\xff\xd8"
    jpeg_end = b"\xff\xd9"
    first_frame: bytes | None = None  # warm-up frame; fallback if no second arrives
    buffer = b""

    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
            session.get(safe_url) as response,
        ):
            if response.status != 200:
                logger.error("MJPEG stream returned status %s", response.status)
                return None

            async for chunk in response.content.iter_chunked(8192):
                buffer += chunk

                # A single chunk can carry multiple frames (e.g. high-FPS sources)
                # or a partial frame. Drain every complete frame we already have
                # before pulling the next chunk.
                while True:
                    start_idx = buffer.find(jpeg_start)
                    if start_idx == -1:
                        # No frame start yet — drop trailing garbage, keep waiting.
                        break
                    end_idx = buffer.find(jpeg_end, start_idx + 2)
                    if end_idx == -1:
                        # Partial frame; trim already-discarded prefix so the
                        # buffer stays bounded across long-running streams.
                        if start_idx > 0:
                            buffer = buffer[start_idx:]
                        break
                    frame = buffer[start_idx : end_idx + 2]
                    buffer = buffer[end_idx + 2 :]
                    if first_frame is None:
                        first_frame = frame  # warm-up; keep but don't return yet
                        continue
                    return frame  # representative second frame

                if len(buffer) > 5 * 1024 * 1024:  # 5MB limit
                    logger.warning("MJPEG buffer exceeded 5MB without finding frame")
                    break  # exit chunk loop, fall through to first_frame fallback

    except TimeoutError:
        logger.warning("MJPEG frame capture timed out after %ss", timeout)
    except (aiohttp.ClientError, OSError) as e:
        logger.error("MJPEG frame capture failed: %s", e)

    # Stream ended / timed out / buffer cap before a second frame arrived.
    # Return whatever warm-up frame we managed to read; better an iffy frame
    # than None for callers that need *some* image (snapshot UX, plate-detect
    # CV, finish photo). None only if no frame ever arrived at all.
    return first_frame


async def _capture_rtsp_frame(url: str, timeout: int) -> bytes | None:
    """Capture frame from RTSP using ffmpeg.

    For rtsps:// URLs, a local TLS proxy is used to avoid GnuTLS issues.
    """
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        logger.error("ffmpeg not found - required for RTSP capture")
        return None

    # If rtsps://, use TLS proxy
    proxy_server = None
    effective_url = url
    if url.lower().startswith("rtsps://"):
        try:
            from urllib.parse import urlparse

            from backend.app.services.camera import create_tls_proxy

            parsed = urlparse(url)
            target_port = parsed.port or 322
            proxy_port, proxy_server = await create_tls_proxy(parsed.hostname, target_port)
            userinfo = ""
            if parsed.username:
                userinfo = parsed.username
                if parsed.password:
                    userinfo += f":{parsed.password}"
                userinfo += "@"
            effective_url = f"rtsp://{userinfo}127.0.0.1:{proxy_port}{parsed.path}"
            if parsed.query:
                effective_url += f"?{parsed.query}"
        except Exception as e:
            logger.warning("Failed to create TLS proxy for RTSP capture, falling back: %s", e)
            effective_url = url

    cmd = [
        ffmpeg,
        "-rtsp_transport",
        "tcp",
        "-i",
        effective_url,
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
        logger.debug("Running ffmpeg RTSP capture...")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        logger.debug(
            "ffmpeg returned: code=%s, stdout=%s bytes, stderr=%s bytes",
            process.returncode,
            len(stdout),
            len(stderr),
        )

        if process.returncode != 0:
            # ffmpeg echoes the RTSP input URL, which carries the camera password.
            logger.error("ffmpeg RTSP capture failed: %s", redact_url_credentials(stderr.decode())[:200])
            return None

        if not stdout or len(stdout) < 100:
            logger.error("ffmpeg returned empty or too small frame")
            return None

        return stdout

    except TimeoutError:
        logger.warning("RTSP frame capture timed out after %ss", timeout)
        if process:
            process.kill()
        return None
    except OSError as e:
        logger.error("RTSP frame capture failed: %s", e)
        return None
    finally:
        if proxy_server:
            proxy_server.close()
            await proxy_server.wait_closed()


def _transcode_to_jpeg(data: bytes) -> bytes | None:
    """Decode an arbitrary still image (PNG/WebP/BMP/GIF/...) and re-encode as JPEG.

    Some camera/proxy snapshot endpoints serve stills as PNG or WebP rather than
    JPEG. A browser opened directly at the URL renders those fine, but our MJPEG
    ``multipart/x-mixed-replace`` stream hard-labels every part
    ``Content-Type: image/jpeg`` — so a non-JPEG payload makes the browser reject
    the frame and drop the whole stream ("connection lost", #1902). Transcoding to
    JPEG keeps the stream genuinely MJPEG and also keeps the JPEG-only downstream
    (plate detection, Obico, finish photo) working.

    Returns None if the bytes are not a decodable image (e.g. an HTML error page)
    or if the imaging libraries are unavailable — callers fall back to the raw
    bytes so behaviour is never worse than before.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None
        return buf.tobytes()
    except Exception as e:  # cv2 raises cv2.error (a subclass of Exception) on bad input
        logger.debug("Snapshot transcode to JPEG failed: %s", e)
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
        logger.error("Invalid snapshot URL format: %s...", redact_url_credentials(url)[:50])
        return None

    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
            session.get(safe_url) as response,
        ):
            if response.status != 200:
                logger.error("Snapshot URL returned status %s", response.status)
                return None

            data = await response.read()
    except TimeoutError:
        logger.warning("Snapshot capture timed out after %ss", timeout)
        return None
    except (aiohttp.ClientError, OSError) as e:
        logger.error("Snapshot capture failed: %s", e)
        return None

    # Fast path: already JPEG (SOI marker), stream it as-is (no decode/re-encode).
    if data.startswith(b"\xff\xd8"):
        return data

    # Not JPEG. Many snapshot endpoints serve PNG/WebP/BMP — transcode to JPEG so
    # the browser's MJPEG stream (and JPEG-only downstream) keep working instead of
    # dropping the connection (#1902). Run off the event loop: cv2 decode/encode is
    # CPU-bound and this can be polled at up to 15 fps while a camera view is open.
    transcoded = await asyncio.to_thread(_transcode_to_jpeg, data)
    if transcoded is not None:
        logger.debug(
            "Transcoded non-JPEG snapshot (%d bytes, header %s) to JPEG",
            len(data),
            data[:4].hex(),
        )
        return transcoded

    # Couldn't decode it as an image at all — most likely not an image response
    # (HTML error page, auth redirect, wrong URL). Return the raw bytes as a last
    # resort (unchanged behaviour) but log enough to debug.
    logger.warning(
        "External camera snapshot is not a decodable image "
        "(%d bytes, header %s) — verify the camera URL returns an image",
        len(data),
        data[:4].hex(),
    )
    return data


async def test_connection(url: str, camera_type: str) -> dict:
    """Test camera connection.

    Returns:
        Dict with {success: bool, error?: str, resolution?: str, coalesced: bool}

    ``coalesced`` is True when the frame came from a capture that was already
    running rather than from a connection this test opened. Captures are shared
    (see ``capture_frame``), so a test that lands while Obico is polling — or
    while any other one-shot consumer is mid-capture — gets that frame back and
    would otherwise report a healthy connection it never made, which is the one
    answer a *connection test* must not give silently. Forcing an uncoalesced
    capture here would be worse: it would open the second handle to a
    single-reader device that this whole mechanism exists to prevent. So the
    test still shares, and says so. Mirrors the ``coalesced_capture`` code the
    built-in diagnostic reports for the same situation (camera_diagnose.py).
    """
    logger.info("Testing camera connection: type=%s, url=%s...", camera_type, redact_url_credentials(url)[:50])
    # Sampled before the call, while it can still distinguish "someone else is
    # mid-capture" from "I am the one capturing".
    coalesced = capture_in_flight(url, camera_type)
    try:
        frame = await capture_frame(url, camera_type, timeout=10)
        logger.info("Capture result: %s bytes%s", len(frame) if frame else 0, " (coalesced)" if coalesced else "")

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
            except (IndexError, ValueError):
                pass  # Resolution detection is optional; fall back to default

            return {"success": True, "resolution": resolution, "coalesced": coalesced}
        else:
            return {"success": False, "error": "Failed to capture frame from camera", "coalesced": coalesced}

    except Exception as e:
        # Sanitize error message - don't expose internal details
        error_type = type(e).__name__
        logger.error("Camera connection test failed: %s", e)
        return {"success": False, "error": f"Connection failed: {error_type}", "coalesced": coalesced}


async def generate_mjpeg_stream(
    url: str,
    camera_type: str,
    fps: int = 10,
    *,
    on_process: Callable[[asyncio.subprocess.Process], None] | None = None,
    on_frame: Callable[[bytes], None] | None = None,
    stop_event: asyncio.Event | None = None,
) -> AsyncGenerator[bytes, None]:
    """Generator yielding MJPEG frames for streaming.

    Args:
        url: Camera URL or USB device path
        camera_type: "mjpeg", "rtsp", "snapshot", or "usb"
        fps: Target frames per second
        on_process: Called with the spawned ffmpeg process for the ``usb`` and
            ``rtsp`` paths so the route layer can register it into the shared
            stream registries — that's what lets ``/camera/stop`` and the orphan
            janitor find and kill a leaked ffmpeg that's holding a USB device
            open (#2675). Without it the process is reachable only from this
            generator's own ``finally``, which an abrupt client disconnect can
            skip (same cancellation-timing class as #776).
        on_frame: Called with each RAW frame, before it is wrapped for the wire,
            so the route layer can publish it as the printer's buffered frame
            (#2707). It has to be a callback: what this generator yields is
            multipart-wrapped, so a consumer of the stream cannot recover the
            JPEG, and until now nothing populated the buffer for external
            cameras at all — leaving every one-shot consumer (layer timelapse,
            finish photo, Obico, plate check) with nothing to reuse and no
            option but to open a competing handle on a single-reader device.
            Exceptions are logged and swallowed: buffering must never be able
            to break the live stream.
        stop_event: When set, the reconnect loops stop retrying — so an explicit
            stop (which kills the current ffmpeg) doesn't immediately respawn a
            new process and reacquire the device.

    Yields:
        MJPEG frame data with HTTP multipart boundaries
    """
    frame_interval = 1.0 / max(fps, 1)
    last_frame_time = 0.0

    def _publish(frame: bytes) -> bytes:
        """Hand the raw frame to on_frame, then format it for the wire."""
        if on_frame is not None:
            try:
                on_frame(frame)
            except Exception:
                logger.exception("on_frame callback raised")
        return _format_mjpeg_frame(frame)

    if camera_type == "mjpeg":
        # Proxy MJPEG stream directly, with reconnect on timeout
        max_retries = 3
        for attempt in range(max_retries + 1):
            frame_yielded = False
            async for frame in _stream_mjpeg(url):
                frame_yielded = True
                current_time = asyncio.get_event_loop().time()
                if current_time - last_frame_time >= frame_interval:
                    last_frame_time = current_time
                    yield _publish(frame)
            if not frame_yielded or attempt == max_retries or (stop_event is not None and stop_event.is_set()):
                break
            logger.warning(
                "External MJPEG stream ended, reconnecting (attempt %d/%d)...",
                attempt + 1,
                max_retries,
            )
            await asyncio.sleep(2)

    elif camera_type == "rtsp":
        # Use ffmpeg to convert RTSP to MJPEG, with reconnect on timeout
        max_retries = 3
        for attempt in range(max_retries + 1):
            frame_yielded = False
            async for frame in _stream_rtsp(url, fps, on_process=on_process):
                frame_yielded = True
                yield _publish(frame)
            if not frame_yielded or attempt == max_retries or (stop_event is not None and stop_event.is_set()):
                break
            logger.warning(
                "External RTSP stream ended, reconnecting (attempt %d/%d)...",
                attempt + 1,
                max_retries,
            )
            await asyncio.sleep(2)

    elif camera_type == "usb":
        # Use ffmpeg to stream from USB camera
        async for frame in _stream_usb(url, fps, on_process=on_process):
            yield _publish(frame)

    elif camera_type == "snapshot":
        # Poll snapshot URL at interval
        while True:
            try:
                frame = await _capture_snapshot(url, timeout=10)
                if frame:
                    yield _publish(frame)
                await asyncio.sleep(frame_interval)
            except asyncio.CancelledError:
                break
            except (aiohttp.ClientError, OSError) as e:
                logger.warning("Snapshot poll failed: %s", e)
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
        logger.error("Invalid MJPEG stream URL: %s...", redact_url_credentials(url)[:50])
        return

    try:
        timeout = aiohttp.ClientTimeout(total=None, sock_read=30)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(safe_url) as response:
            if response.status != 200:
                logger.error("MJPEG stream returned status %s", response.status)
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
    except (aiohttp.ClientError, OSError) as e:
        logger.error("MJPEG stream error: %s", e)


async def _stream_rtsp(
    url: str,
    fps: int,
    *,
    on_process: Callable[[asyncio.subprocess.Process], None] | None = None,
) -> AsyncGenerator[bytes, None]:
    """Stream frames from RTSP URL via ffmpeg.

    For rtsps:// URLs, a local TLS proxy (Python OpenSSL) is used instead
    of relying on ffmpeg's GnuTLS backend, which has compatibility issues
    with some printer firmwares.
    """
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        logger.error("ffmpeg not found - required for RTSP streaming")
        return

    from backend.app.services.camera import rtsp_socket_timeout_flag

    # If the URL uses rtsps://, set up a TLS proxy so ffmpeg uses plain rtsp://
    proxy_server = None
    effective_url = url
    if url.lower().startswith("rtsps://"):
        try:
            from urllib.parse import urlparse

            from backend.app.services.camera import create_tls_proxy

            parsed = urlparse(url)
            target_port = parsed.port or 322
            proxy_port, proxy_server = await create_tls_proxy(parsed.hostname, target_port)
            # Rewrite URL: rtsps://user:pass@host:port/path → rtsp://user:pass@127.0.0.1:proxy/path
            userinfo = ""
            if parsed.username:
                userinfo = parsed.username
                if parsed.password:
                    userinfo += f":{parsed.password}"
                userinfo += "@"
            effective_url = f"rtsp://{userinfo}127.0.0.1:{proxy_port}{parsed.path}"
            if parsed.query:
                effective_url += f"?{parsed.query}"
        except Exception as e:
            logger.warning("Failed to create TLS proxy for RTSP, falling back to direct: %s", e)
            effective_url = url

    cmd = [
        ffmpeg,
        "-rtsp_transport",
        "tcp",
        "-rtsp_flags",
        "prefer_tcp",
        # Socket I/O timeout name varies by ffmpeg version (#1504); see
        # `rtsp_socket_timeout_flag()` in services.camera.
        f"-{rtsp_socket_timeout_flag()}",
        "30000000",
        "-buffer_size",
        "1024000",
        "-max_delay",
        "500000",
        "-probesize",
        "32",
        "-analyzeduration",
        "0",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-i",
        effective_url,
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
        # Register immediately — before the startup probe below — so a process
        # that hangs on connect (rather than exiting) is still reachable by the
        # stop endpoint / orphan janitor (#2675).
        if on_process is not None:
            on_process(process)

        # Brief check for immediate startup failures
        await asyncio.sleep(0.1)
        if process.returncode is not None:
            stderr = await process.stderr.read()
            # ffmpeg echoes the RTSP input URL, which carries the camera password.
            logger.error("ffmpeg RTSP stream failed immediately: %s", redact_url_credentials(stderr.decode())[:300])
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
    except OSError as e:
        logger.error("RTSP stream error: %s", e)
    finally:
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        if proxy_server:
            proxy_server.close()
            await proxy_server.wait_closed()


async def _stream_usb(
    device: str,
    fps: int,
    *,
    on_process: Callable[[asyncio.subprocess.Process], None] | None = None,
) -> AsyncGenerator[bytes, None]:
    """Stream frames from USB camera via ffmpeg."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        logger.error("ffmpeg not found - required for USB camera streaming")
        return

    # Validate device path
    if not device.startswith("/dev/video"):
        logger.error("Invalid USB device path: %s", device)
        return

    if not Path(device).exists():
        logger.error("USB device does not exist: %s", device)
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
        logger.info("Starting USB camera stream from %s at %s fps", device, fps)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Register immediately — before the startup probe below — so a process
        # that hangs in open()/ioctl on a still-locked device (rather than
        # exiting with a "busy" error) is still reachable by the stop endpoint /
        # orphan janitor (#2675).
        if on_process is not None:
            on_process(process)

        # Give ffmpeg a moment to start and check for immediate failures
        await asyncio.sleep(0.5)
        if process.returncode is not None:
            stderr = await process.stderr.read()
            logger.error("ffmpeg USB stream failed immediately: %s", stderr.decode()[:300])
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
    except OSError as e:
        logger.error("USB stream error: %s", e)
    finally:
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except TimeoutError:
                process.kill()
                await process.wait()
