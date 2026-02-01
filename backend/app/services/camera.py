"""Camera capture service for Bambu Lab printers.

Supports two camera protocols:
- RTSP: Used by X1, X1C, X1E, H2C, H2D, H2DPRO, H2S, P2S (port 322)
- Chamber Image: Used by A1, A1MINI, P1P, P1S (port 6000, custom binary protocol)
"""

import asyncio
import logging
import shutil
import ssl
import struct
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# JPEG markers
JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"

# Cache the ffmpeg path after first lookup
_ffmpeg_path: str | None = None


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
        logger.info(f"Found ffmpeg at: {ffmpeg_path}")
    else:
        logger.warning("ffmpeg not found in PATH or common locations")

    return ffmpeg_path


def supports_rtsp(model: str | None) -> bool:
    """Check if printer model supports RTSP camera streaming.

    RTSP supported: X1, X1C, X1E, H2C, H2D, H2DPRO, H2S, P2S
    Chamber image only: A1, A1MINI, P1P, P1S

    Note: Model can be either display name (e.g., "P2S") or internal code (e.g., "N7").
    Internal codes from MQTT/SSDP:
      - BL-P001: X1/X1C
      - C13: X1E
      - O1D: H2D
      - O1C: H2C
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
        if model_upper in ("BL-P001", "C13", "O1D", "O1C", "O1S", "O1E", "O2D", "N7"):
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
                logger.error(f"Chamber image: invalid payload size {payload_size}")
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

            logger.debug(f"Chamber image: received {len(jpeg_data)} bytes")
            return jpeg_data

        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    except TimeoutError:
        logger.error(f"Chamber image: connection timeout to {ip_address}:{port}")
        return None
    except ConnectionRefusedError:
        logger.error(f"Chamber image: connection refused by {ip_address}:{port}")
        return None
    except Exception as e:
        logger.exception(f"Chamber image: error connecting to {ip_address}:{port}: {e}")
        return None


async def generate_chamber_image_stream(
    ip_address: str,
    access_code: str,
    fps: int = 5,
) -> asyncio.StreamReader | None:
    """Create a persistent connection for streaming chamber images.

    Returns a connected reader or None if connection failed.
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

        logger.info(f"Chamber image: connected to {ip_address}:{port}")
        return reader, writer

    except Exception as e:
        logger.error(f"Chamber image: failed to connect to {ip_address}:{port}: {e}")
        return None


async def read_next_chamber_frame(reader: asyncio.StreamReader, timeout: float = 10.0) -> bytes | None:
    """Read the next JPEG frame from an established chamber image connection."""
    try:
        # Read the 16-byte header
        header = await asyncio.wait_for(reader.readexactly(16), timeout=timeout)

        # Parse payload size from header (little-endian uint32 at offset 0)
        payload_size = struct.unpack("<I", header[0:4])[0]

        if payload_size == 0 or payload_size > 10_000_000:
            logger.error(f"Chamber image: invalid payload size {payload_size}")
            return None

        # Read the JPEG data
        jpeg_data = await asyncio.wait_for(
            reader.readexactly(payload_size),
            timeout=timeout,
        )

        return jpeg_data

    except asyncio.IncompleteReadError:
        logger.warning("Chamber image: connection closed by printer")
        return None
    except TimeoutError:
        logger.warning("Chamber image: read timeout")
        return None
    except Exception as e:
        logger.error(f"Chamber image: error reading frame: {e}")
        return None


async def capture_camera_frame(
    ip_address: str,
    access_code: str,
    model: str | None,
    output_path: Path,
    timeout: int = 30,
) -> bool:
    """Capture a single frame from the printer's camera stream.

    Uses the appropriate protocol based on printer model:
    - A1/P1: Chamber image protocol (port 6000)
    - X1/H2/P2: RTSP via ffmpeg (port 322)

    Args:
        ip_address: Printer IP address
        access_code: Printer access code
        model: Printer model (X1, H2D, P1, A1, etc.)
        output_path: Path where to save the captured image
        timeout: Timeout in seconds for the capture operation

    Returns:
        True if capture was successful, False otherwise
    """
    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Use chamber image protocol for A1/P1 models
    if is_chamber_image_model(model):
        logger.info(f"Capturing camera frame from {ip_address} using chamber image protocol (model: {model})")
        jpeg_data = await read_chamber_image_frame(ip_address, access_code, timeout=float(timeout))
        if jpeg_data:
            try:
                with open(output_path, "wb") as f:
                    f.write(jpeg_data)
                logger.info(f"Successfully captured camera frame: {output_path}")
                return True
            except Exception as e:
                logger.error(f"Failed to write camera frame: {e}")
                return False
        return False

    # Use RTSP/ffmpeg for X1/H2/P2 models
    camera_url = build_camera_url(ip_address, access_code, model)

    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        logger.error("ffmpeg not found. Please install ffmpeg to enable camera capture.")
        return False

    # ffmpeg command to capture a single frame from RTSPS stream
    cmd = [
        ffmpeg,
        "-y",  # Overwrite output
        "-rtsp_transport",
        "tcp",
        "-rtsp_flags",
        "prefer_tcp",
        "-i",
        camera_url,
        "-frames:v",
        "1",
        "-update",
        "1",
        "-q:v",
        "2",
        str(output_path),
    ]

    logger.info(f"Capturing camera frame from {ip_address} using RTSP (model: {model})")

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
            logger.error(f"Camera capture timed out after {timeout}s")
            return False

        if process.returncode != 0:
            stderr_text = stderr.decode() if stderr else "Unknown error"
            logger.error(f"ffmpeg failed with code {process.returncode}: {stderr_text}")
            return False

        if output_path.exists() and output_path.stat().st_size > 0:
            logger.info(f"Successfully captured camera frame: {output_path}")
            return True
        else:
            logger.error("Camera capture produced no output file")
            return False

    except FileNotFoundError:
        logger.error("ffmpeg not found. Please install ffmpeg to enable camera capture.")
        return False
    except Exception as e:
        logger.exception(f"Camera capture failed: {e}")
        return False


async def capture_finish_photo(
    printer_id: int,
    ip_address: str,
    access_code: str,
    model: str | None,
    archive_dir: Path,
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
    )

    if success:
        logger.info(f"Finish photo saved: {filename}")
        return filename
    else:
        logger.warning(f"Failed to capture finish photo for printer {printer_id}")
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
