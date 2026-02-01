"""Layer-based timelapse for external cameras.

Captures a frame on each layer change and stitches them into a video on print completion.
"""

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from backend.app.core.config import settings
from backend.app.services.external_camera import capture_frame

logger = logging.getLogger(__name__)

# Active timelapse sessions: {printer_id: TimelapseSession}
_active_sessions: dict[int, "TimelapseSession"] = {}


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


@dataclass
class TimelapseSession:
    """Active timelapse recording session."""

    printer_id: int
    archive_id: int | None
    camera_url: str
    camera_type: str
    last_layer: int = -1
    frame_count: int = 0
    session_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))
    frames_dir: Path = field(init=False)

    def __post_init__(self):
        self.frames_dir = settings.base_dir / "timelapse_frames" / str(self.printer_id) / self.session_id
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created timelapse session {self.session_id} for printer {self.printer_id}")

    async def capture_layer(self, layer_num: int) -> bool:
        """Capture frame if layer changed.

        Args:
            layer_num: Current layer number from printer

        Returns:
            True if frame was captured, False otherwise
        """
        # Only capture if layer increased
        if layer_num <= self.last_layer:
            return False

        self.last_layer = layer_num

        try:
            frame_data = await capture_frame(self.camera_url, self.camera_type)
            if frame_data:
                frame_path = self.frames_dir / f"layer_{layer_num:05d}.jpg"
                await asyncio.to_thread(frame_path.write_bytes, frame_data)
                self.frame_count += 1
                logger.debug(f"Captured layer {layer_num} for printer {self.printer_id} (frame {self.frame_count})")
                return True
            else:
                logger.warning(f"Failed to capture frame for layer {layer_num}")
                return False
        except Exception as e:
            logger.error(f"Error capturing timelapse frame: {e}")
            return False

    async def stitch(self, output_path: Path, fps: int = 30) -> bool:
        """Create MP4 from captured frames using ffmpeg.

        Args:
            output_path: Path for output video file
            fps: Frames per second for output video

        Returns:
            True if stitching succeeded, False otherwise
        """
        if self.frame_count == 0:
            logger.warning("No frames to stitch")
            return False

        ffmpeg = get_ffmpeg_path()
        if not ffmpeg:
            logger.error("ffmpeg not found - required for timelapse stitching")
            return False

        # Find all frame files and create a sequential list
        # This handles gaps in layer numbers (e.g., if some captures failed)
        frame_files = sorted(self.frames_dir.glob("layer_*.jpg"))
        if not frame_files:
            logger.warning("No frame files found in timelapse directory")
            return False

        # Create a concat file listing all frames
        concat_file = self.frames_dir / "frames.txt"
        try:
            with open(concat_file, "w") as f:
                for frame in frame_files:
                    # Each frame shown for 1/fps duration
                    f.write(f"file '{frame.name}'\n")
                    f.write(f"duration {1.0 / fps}\n")
                # Add last frame again (required by concat demuxer)
                if frame_files:
                    f.write(f"file '{frame_files[-1].name}'\n")
        except Exception as e:
            logger.error(f"Failed to create concat file: {e}")
            return False

        # Use ffmpeg concat demuxer for variable-gap frame sequences
        cmd = [
            ffmpeg,
            "-y",  # Overwrite output
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-crf",
            "23",
            str(output_path),
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.frames_dir),  # Run in frames dir so relative paths work
            )

            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)

            if process.returncode != 0:
                logger.error(f"ffmpeg timelapse stitch failed: {stderr.decode()[:500]}")
                return False

            logger.info(f"Created timelapse video: {output_path} ({self.frame_count} frames)")
            return True

        except TimeoutError:
            logger.error("Timelapse stitching timed out")
            if process:
                process.kill()
            return False
        except Exception as e:
            logger.error(f"Timelapse stitch failed: {e}")
            return False

    def cleanup(self):
        """Remove temporary frames directory."""
        try:
            if self.frames_dir.exists():
                shutil.rmtree(self.frames_dir, ignore_errors=True)
                logger.info(f"Cleaned up timelapse frames for session {self.session_id}")
        except Exception as e:
            logger.warning(f"Failed to cleanup timelapse frames: {e}")


def start_session(printer_id: int, archive_id: int | None, url: str, cam_type: str) -> TimelapseSession:
    """Start new timelapse session for a printer.

    Args:
        printer_id: The printer ID
        archive_id: Associated print archive ID (optional)
        url: External camera URL
        cam_type: Camera type ("mjpeg", "rtsp", "snapshot")

    Returns:
        The new TimelapseSession
    """
    # Cancel any existing session
    cancel_session(printer_id)

    session = TimelapseSession(
        printer_id=printer_id,
        archive_id=archive_id,
        camera_url=url,
        camera_type=cam_type,
    )
    _active_sessions[printer_id] = session
    logger.info(f"Started timelapse session for printer {printer_id}")
    return session


def get_session(printer_id: int) -> TimelapseSession | None:
    """Get active timelapse session for a printer."""
    return _active_sessions.get(printer_id)


async def on_layer_change(printer_id: int, layer_num: int):
    """Called on layer change - captures frame if session active.

    Args:
        printer_id: The printer ID
        layer_num: Current layer number
    """
    session = get_session(printer_id)
    if session:
        await session.capture_layer(layer_num)


async def on_print_complete(printer_id: int) -> Path | None:
    """Stitch timelapse and return path. Cleans up session.

    Args:
        printer_id: The printer ID

    Returns:
        Path to stitched video, or None if no session or stitching failed
    """
    session = _active_sessions.pop(printer_id, None)
    if not session:
        return None

    if session.frame_count == 0:
        logger.info(f"No timelapse frames captured for printer {printer_id}")
        session.cleanup()
        return None

    # Create output path in parent of frames dir
    output_path = session.frames_dir.parent / f"timelapse_{session.session_id}.mp4"

    try:
        success = await session.stitch(output_path)
        if success:
            # Cleanup frames after successful stitch
            session.cleanup()
            return output_path
        else:
            session.cleanup()
            return None
    except Exception as e:
        logger.error(f"Timelapse completion failed: {e}")
        session.cleanup()
        return None


def cancel_session(printer_id: int):
    """Cancel and cleanup timelapse session (on print fail/cancel).

    Args:
        printer_id: The printer ID
    """
    session = _active_sessions.pop(printer_id, None)
    if session:
        session.cleanup()
        logger.info(f"Cancelled timelapse session for printer {printer_id}")


def get_active_sessions() -> dict[int, TimelapseSession]:
    """Get all active timelapse sessions."""
    return _active_sessions.copy()
