"""Camera capture service for Bambu Lab printers.

Supports two camera protocols:
- RTSP: Used by X1, X1C, X1E, H2C, H2D, H2DPRO, H2S, P2S (port 322)
- Chamber Image: Used by A1, A1MINI, P1P, P1S (port 6000, custom binary protocol)
"""

import asyncio
import logging
import math
import os
import platform
import shutil
import ssl
import struct
import sys
import uuid
from datetime import datetime
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

# JPEG markers
JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"

# Cache the ffmpeg path after first lookup
_ffmpeg_path: str | None = None

# Cache GPU hardware acceleration backends
_gpu_hwaccels: list[str] | None = None
_gpu_hwaccels_lock: asyncio.Lock | None = None


def _get_gpu_lock() -> asyncio.Lock:
    """Return (lazy-init) the lock guarding GPU hwaccel detection."""
    global _gpu_hwaccels_lock
    if _gpu_hwaccels_lock is None:
        _gpu_hwaccels_lock = asyncio.Lock()
    return _gpu_hwaccels_lock


# ---------------------------------------------------------------------------
# Global semaphore limiting concurrent RTSP ffmpeg processes
# Lazily initialised to avoid creating an asyncio.Semaphore before the event
# loop is running (raises RuntimeError on Python 3.12+).
# ---------------------------------------------------------------------------
_MAX_RTSP_FFMPEG: int = 20
_rtsp_semaphore: asyncio.Semaphore | None = None


def get_rtsp_semaphore() -> asyncio.Semaphore:
    """Return the global RTSP ffmpeg semaphore (lazy-init on first call)."""
    global _rtsp_semaphore
    if _rtsp_semaphore is None:
        _rtsp_semaphore = asyncio.Semaphore(_MAX_RTSP_FFMPEG)
    return _rtsp_semaphore


def get_ffmpeg_path() -> str | None:
    """Find the ffmpeg executable path.

    Uses shutil.which first, then checks common installation locations
    for systems where PATH may be limited (e.g., systemd services).
    """
    global _ffmpeg_path

    if _ffmpeg_path is not None:
        return _ffmpeg_path

    # Try PATH first
    ffmpeg_path = shutil.which("ffmpeg")

    # If not found via PATH, check common installation locations
    if ffmpeg_path is None:
        common_paths = [
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/opt/homebrew/bin/ffmpeg",  # macOS Homebrew
            "/snap/bin/ffmpeg",  # Ubuntu Snap
            "C:\\ffmpeg\\bin\\ffmpeg.exe",  # Windows common
        ]
        for path in common_paths:
            if Path(path).exists():
                ffmpeg_path = path
                break

    _ffmpeg_path = ffmpeg_path
    if ffmpeg_path:
        logger.info("Found ffmpeg at: %s", ffmpeg_path)
    else:
        logger.warning("ffmpeg not found in PATH or common locations")

    return ffmpeg_path


async def detect_gpu_hwaccels() -> list[str]:
    """Detect available GPU hardware acceleration backends via ffmpeg.

    Runs ``ffmpeg -hwaccels``, parses the output, and caches the result.
    Returns an empty list when ffmpeg is not installed or no backends found.
    Uses a lock to prevent duplicate subprocess spawns on concurrent calls.
    """
    global _gpu_hwaccels

    if _gpu_hwaccels is not None:
        return _gpu_hwaccels

    async with _get_gpu_lock():
        # Re-check after acquiring lock (another coroutine may have populated it)
        if _gpu_hwaccels is not None:
            return _gpu_hwaccels

        ffmpeg = get_ffmpeg_path()
        if not ffmpeg:
            _gpu_hwaccels = []
            return _gpu_hwaccels

        try:
            process = await asyncio.create_subprocess_exec(
                ffmpeg,
                "-hwaccels",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
            lines = stdout.decode().strip().splitlines()
            # First line is typically "Hardware acceleration methods:" — skip it
            backends = [line.strip() for line in lines[1:] if line.strip() and line.strip().lower() != "none"]
            _gpu_hwaccels = backends
            if backends:
                logger.info("GPU hwaccel backends detected: %s", ", ".join(backends))
            else:
                logger.info("No GPU hwaccel backends detected")
        except Exception as e:
            logger.warning("Failed to detect GPU hwaccels: %s", e)
            _gpu_hwaccels = []

        return _gpu_hwaccels


# ---------------------------------------------------------------------------
# Hardware capability cache for auto quality resolution
# ---------------------------------------------------------------------------
_system_hw_info: dict | None = None
_system_hw_lock: asyncio.Lock | None = None


def _get_hw_lock() -> asyncio.Lock:
    """Return (lazy-init) the lock guarding system HW info detection."""
    global _system_hw_lock
    if _system_hw_lock is None:
        _system_hw_lock = asyncio.Lock()
    return _system_hw_lock


async def _get_system_hw_info() -> dict:
    """Probe system hardware once and cache the result.

    Returns a dict with cpu_score, ram_score, gpu_score, gpu_penalty_factor,
    and base_score used by resolve_camera_quality().
    Uses a lock to prevent duplicate probing on concurrent calls.
    """
    global _system_hw_info

    if _system_hw_info is not None:
        return _system_hw_info

    async with _get_hw_lock():
        # Re-check after acquiring lock
        if _system_hw_info is not None:
            return _system_hw_info

        cpu_count = os.cpu_count() or 2

        # Platform awareness
        is_darwin = sys.platform == "darwin"
        is_arm = platform.machine().lower() in ("arm64", "aarch64")
        is_apple_silicon = is_darwin and is_arm

        if is_apple_silicon:
            core_efficiency = 1.3
        elif is_arm:
            core_efficiency = 0.5  # Raspberry Pi, etc.
        else:
            core_efficiency = 1.0  # x86_64

        cpu_score = math.sqrt(cpu_count) * core_efficiency

        # RAM consideration
        ram_gb = psutil.virtual_memory().total / (1024**3)
        ram_score = min(math.log2(max(ram_gb, 1)), 3.0)

        # GPU scoring by backend type
        gpu_backends = await detect_gpu_hwaccels()
        backend_set = {b.lower() for b in gpu_backends}

        gpu_score = 0.0
        gpu_penalty_factor = 1.0

        if "videotoolbox" in backend_set and is_apple_silicon:
            gpu_score, gpu_penalty_factor = 4.0, 0.25
        elif "videotoolbox" in backend_set:
            gpu_score, gpu_penalty_factor = 3.0, 0.4
        elif "cuda" in backend_set:
            gpu_score, gpu_penalty_factor = 3.0, 0.35
        elif "qsv" in backend_set:
            gpu_score, gpu_penalty_factor = 2.5, 0.4
        elif "vaapi" in backend_set:
            gpu_score, gpu_penalty_factor = 2.0, 0.5
        elif gpu_backends:
            gpu_score, gpu_penalty_factor = 1.0, 0.7

        base_score = (cpu_score + ram_score + gpu_score) * 2

        _system_hw_info = {
            "cpu_count": cpu_count,
            "cpu_score": cpu_score,
            "ram_gb": ram_gb,
            "ram_score": ram_score,
            "gpu_backends": gpu_backends,
            "gpu_score": gpu_score,
            "gpu_penalty_factor": gpu_penalty_factor,
            "base_score": base_score,
        }

        return _system_hw_info


def _reset_system_hw_cache() -> None:
    """Clear the cached hardware info (for testing)."""
    global _system_hw_info
    _system_hw_info = None


async def resolve_camera_quality(preset_name: str, stream_count: int = 1) -> str:
    """Resolve 'auto' to a concrete preset based on hardware capability and stream count.

    Uses a hardware capability score (CPU cores scaled by architecture efficiency,
    RAM, and GPU backend type) divided by a sqrt-based stream penalty that accounts
    for how much the GPU offloads work from the CPU.
    """
    if preset_name != "auto":
        return preset_name

    hw = await _get_system_hw_info()
    base_score = hw["base_score"]
    gpu_penalty_factor = hw["gpu_penalty_factor"]

    sc = max(stream_count, 1)
    penalty = 1 + (math.sqrt(sc) - 1) * gpu_penalty_factor
    effective = base_score / penalty

    if effective >= 12.0:
        return "high"
    elif effective >= 7.0:
        return "medium"
    else:
        return "low"


def get_gpu_hwaccels() -> list[str]:
    """Return cached GPU hwaccel backends (empty list if not yet detected)."""
    return _gpu_hwaccels or []


def supports_rtsp(model: str | None) -> bool:
    """Check if printer model supports RTSP camera streaming.

    RTSP supported: X1, X1C, X1E, H2C, H2D, H2DPRO, H2S, P2S
    Chamber image only: A1, A1MINI, P1P, P1S

    Note: Model can be either display name (e.g., "P2S") or internal code (e.g., "N7").
    Internal codes from MQTT/SSDP:
      - BL-P001: X1/X1C
      - C13: X1E
      - O1D: H2D
      - O1C, O1C2: H2C
      - O1S: H2S
      - O1E, O2D: H2D Pro
      - N7: P2S
    """
    if model:
        model_upper = model.upper()
        # Display names: X1, X1C, X1E, H2C, H2D, H2DPRO, H2S, P2S
        if model_upper.startswith(("X1", "H2", "P2")):
            return True
        # Internal codes for RTSP models
        if model_upper in ("BL-P001", "C13", "O1D", "O1C", "O1C2", "O1S", "O1E", "O2D", "N7"):
            return True
    # A1/P1 and unknown models use chamber image protocol
    return False


def get_camera_port(model: str | None) -> int:
    """Get the camera port based on printer model.

    X1/H2/P2 series use RTSP on port 322.
    A1/P1 series use chamber image protocol on port 6000.
    """
    if supports_rtsp(model):
        return 322
    return 6000


def is_chamber_image_model(model: str | None) -> bool:
    """Check if printer uses chamber image protocol instead of RTSP.

    A1, A1MINI, P1P, P1S use the chamber image protocol on port 6000.
    """
    return not supports_rtsp(model)


def build_camera_url(ip_address: str, access_code: str, model: str | None) -> str:
    """Build the RTSPS URL for the printer camera (RTSP models only)."""
    port = get_camera_port(model)
    return f"rtsps://bblp:{access_code}@{ip_address}:{port}/streaming/live/1"


def _create_chamber_auth_payload(access_code: str) -> bytes:
    """Create the 80-byte authentication payload for chamber image protocol.

    Format:
    - Bytes 0-3: 0x40 0x00 0x00 0x00 (magic)
    - Bytes 4-7: 0x00 0x30 0x00 0x00 (command)
    - Bytes 8-15: zeros (padding)
    - Bytes 16-47: username "bblp" (32 bytes, null-padded)
    - Bytes 48-79: access code (32 bytes, null-padded)
    """
    username = b"bblp"
    access_code_bytes = access_code.encode("utf-8")

    # Build the 80-byte payload
    payload = struct.pack(
        "<II8s32s32s",
        0x40,  # Magic header
        0x3000,  # Command
        b"\x00" * 8,  # Padding
        username.ljust(32, b"\x00"),  # Username padded to 32 bytes
        access_code_bytes.ljust(32, b"\x00"),  # Access code padded to 32 bytes
    )
    return payload


def _create_ssl_context() -> ssl.SSLContext:
    """Create an SSL context for chamber image connection.

    Bambu printers use self-signed certificates, so we disable verification.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def read_chamber_image_frame(
    ip_address: str,
    access_code: str,
    timeout: float = 10.0,
) -> bytes | None:
    """Read a single JPEG frame from the chamber image protocol.

    This is used by A1/P1 printers which don't support RTSP.

    Args:
        ip_address: Printer IP address
        access_code: Printer access code
        timeout: Connection timeout in seconds

    Returns:
        JPEG image data or None if failed
    """
    port = 6000
    ssl_context = _create_ssl_context()

    try:
        # Connect with SSL
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip_address, port, ssl=ssl_context),
            timeout=timeout,
        )

        try:
            # Send authentication payload
            auth_payload = _create_chamber_auth_payload(access_code)
            writer.write(auth_payload)
            await writer.drain()

            # Read the 16-byte header
            header = await asyncio.wait_for(reader.readexactly(16), timeout=timeout)
            if len(header) < 16:
                logger.error("Chamber image: incomplete header received")
                return None

            # Parse payload size from header (little-endian uint32 at offset 0)
            payload_size = struct.unpack("<I", header[0:4])[0]

            if payload_size == 0 or payload_size > 10_000_000:  # Sanity check: max 10MB
                logger.error("Chamber image: invalid payload size %s", payload_size)
                return None

            # Read the JPEG data
            jpeg_data = await asyncio.wait_for(
                reader.readexactly(payload_size),
                timeout=timeout,
            )

            # Validate JPEG markers
            if not jpeg_data.startswith(JPEG_START):
                logger.error("Chamber image: data is not a valid JPEG (missing start marker)")
                return None

            if not jpeg_data.endswith(JPEG_END):
                logger.warning("Chamber image: JPEG missing end marker, may be truncated")

            logger.debug("Chamber image: received %s bytes", len(jpeg_data))
            return jpeg_data

        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass  # Socket already closed; cleanup is best-effort

    except TimeoutError:
        logger.error("Chamber image: connection timeout to %s:%s", ip_address, port)
        return None
    except ConnectionRefusedError:
        logger.error("Chamber image: connection refused by %s:%s", ip_address, port)
        return None
    except Exception as e:
        logger.exception("Chamber image: error connecting to %s:%s: %s", ip_address, port, e)
        return None


async def generate_chamber_image_stream(
    ip_address: str,
    access_code: str,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    """Create a persistent connection for streaming chamber images.

    Returns a connected (reader, writer) tuple or None if connection failed.
    """
    port = 6000
    ssl_context = _create_ssl_context()

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip_address, port, ssl=ssl_context),
            timeout=10.0,
        )

        # Send authentication payload
        auth_payload = _create_chamber_auth_payload(access_code)
        writer.write(auth_payload)
        await writer.drain()

        logger.info("Chamber image: connected to %s:%s", ip_address, port)
        return reader, writer

    except Exception as e:
        logger.error("Chamber image: failed to connect to %s:%s: %s", ip_address, port, e)
        return None


class ChamberConnectionClosed(Exception):
    """Raised when the chamber image connection is closed by the printer."""


async def read_next_chamber_frame(reader: asyncio.StreamReader, timeout: float = 10.0) -> bytes | None:
    """Read the next JPEG frame from an established chamber image connection.

    Returns JPEG bytes on success, None on timeout (caller can retry).
    Raises ChamberConnectionClosed if the TCP connection is broken (caller should stop).
    """
    try:
        # Read the 16-byte header
        header = await asyncio.wait_for(reader.readexactly(16), timeout=timeout)

        # Parse payload size from header (little-endian uint32 at offset 0)
        payload_size = struct.unpack("<I", header[0:4])[0]

        if payload_size == 0 or payload_size > 10_000_000:
            logger.error("Chamber image: invalid payload size %s", payload_size)
            raise ChamberConnectionClosed(f"invalid payload size {payload_size}")

        # Read the JPEG data
        jpeg_data = await asyncio.wait_for(
            reader.readexactly(payload_size),
            timeout=timeout,
        )

        return jpeg_data

    except asyncio.IncompleteReadError:
        logger.warning("Chamber image: connection closed by printer")
        raise ChamberConnectionClosed("connection closed by printer")
    except TimeoutError:
        logger.warning("Chamber image: read timeout")
        return None
    except ChamberConnectionClosed:
        raise  # Don't catch our own exception in the generic handler
    except Exception as e:
        logger.error("Chamber image: error reading frame: %s", e)
        raise ChamberConnectionClosed(str(e))


async def capture_camera_frame(
    ip_address: str,
    access_code: str,
    model: str | None,
    output_path: Path,
    timeout: int = 30,
    gpu_accel: bool = False,
) -> bool:
    """Capture a single frame from the printer's camera stream and save to disk.

    Uses capture_camera_frame_bytes() internally for protocol selection,
    then writes the result to the specified output path.

    Args:
        ip_address: Printer IP address
        access_code: Printer access code
        model: Printer model (X1, H2D, P1, A1, etc.)
        output_path: Path where to save the captured image
        timeout: Timeout in seconds for the capture operation

    Returns:
        True if capture was successful, False otherwise
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    jpeg_data = await capture_camera_frame_bytes(ip_address, access_code, model, timeout, gpu_accel=gpu_accel)
    if jpeg_data:
        try:
            with open(output_path, "wb") as f:
                f.write(jpeg_data)
            logger.info("Saved camera frame to: %s", output_path)
            return True
        except OSError as e:
            logger.error("Failed to write camera frame: %s", e)
            return False
    return False


async def capture_camera_frame_bytes(
    ip_address: str,
    access_code: str,
    model: str | None,
    timeout: int = 15,
    gpu_accel: bool = False,
) -> bytes | None:
    """Capture a single frame and return as JPEG bytes (no disk write).

    Uses the same protocol selection as capture_camera_frame but returns
    bytes directly instead of writing to disk.

    Args:
        ip_address: Printer IP address
        access_code: Printer access code
        model: Printer model (X1, H2D, P1, A1, etc.)
        timeout: Timeout in seconds for the capture operation

    Returns:
        JPEG bytes if capture was successful, None otherwise
    """
    # Chamber image models: A1/P1 - returns bytes directly
    if is_chamber_image_model(model):
        logger.info("Capturing camera frame bytes from %s using chamber image protocol (model: %s)", ip_address, model)
        return await read_chamber_image_frame(ip_address, access_code, timeout=float(timeout))

    # RTSP models: X1/H2/P2 - use ffmpeg piping to stdout
    camera_url = build_camera_url(ip_address, access_code, model)

    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        logger.error("ffmpeg not found for camera frame capture")
        return None

    cmd = [ffmpeg, "-y"]
    if gpu_accel:
        cmd.extend(["-hwaccel", "auto"])
    cmd.extend(
        [
            "-rtsp_transport",
            "tcp",
            "-rtsp_flags",
            "prefer_tcp",
            "-i",
            camera_url,
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
    )

    logger.info("Capturing camera frame bytes from %s using RTSP (model: %s)", ip_address, model)

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            logger.error("Camera frame bytes capture timed out after %ss", timeout)
            return None

        if process.returncode == 0 and stdout and len(stdout) >= 100:
            logger.info("Successfully captured camera frame bytes: %s bytes", len(stdout))
            return stdout
        else:
            stderr_text = stderr.decode() if stderr else "Unknown error"
            logger.error("ffmpeg frame bytes capture failed (code %s): %s", process.returncode, stderr_text[:200])
            return None

    except FileNotFoundError:
        logger.error("ffmpeg not found for camera frame capture")
        return None
    except Exception as e:
        logger.exception("Camera frame bytes capture failed: %s", e)
        return None


async def capture_finish_photo(
    printer_id: int,
    ip_address: str,
    access_code: str,
    model: str | None,
    archive_dir: Path,
    gpu_accel: bool = False,
) -> str | None:
    """Capture a finish photo and save it to the archive's photos folder.

    Args:
        printer_id: ID of the printer
        ip_address: Printer IP address
        access_code: Printer access code
        model: Printer model
        archive_dir: Directory of the archive (where the 3MF is stored)

    Returns:
        Filename of the captured photo, or None if capture failed
    """
    # Create photos subdirectory
    photos_dir = archive_dir / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"finish_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
    output_path = photos_dir / filename

    success = await capture_camera_frame(
        ip_address=ip_address,
        access_code=access_code,
        model=model,
        output_path=output_path,
        timeout=30,
        gpu_accel=gpu_accel,
    )

    if success:
        logger.info("Finish photo saved: %s", filename)
        return filename
    else:
        logger.warning("Failed to capture finish photo for printer %s", printer_id)
        return None


async def test_camera_connection(
    ip_address: str,
    access_code: str,
    model: str | None,
) -> dict:
    """Test if the camera stream is accessible.

    Returns dict with success status and any error message.
    """
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        test_path = Path(f.name)

    try:
        success = await capture_camera_frame(
            ip_address=ip_address,
            access_code=access_code,
            model=model,
            output_path=test_path,
            timeout=15,
        )

        if success:
            return {"success": True, "message": "Camera connection successful"}
        else:
            return {
                "success": False,
                "error": (
                    "Failed to capture frame from camera. "
                    "Ensure the printer is powered on, camera is enabled, and Developer Mode is active. "
                    "If running in Docker, try 'network_mode: host' in docker-compose.yml."
                ),
            }
    finally:
        # Clean up test file
        if test_path.exists():
            test_path.unlink()
