"""Layer-based timelapse for external cameras.

Captures a frame on each layer change and stitches them into a video on print completion.
"""

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from backend.app.core.config import settings
from backend.app.services.camera import apply_camera_rotation
from backend.app.services.external_camera import capture_frame

logger = logging.getLogger(__name__)

# Active timelapse sessions: {printer_id: TimelapseSession}
_active_sessions: dict[int, "TimelapseSession"] = {}

# Sessions whose frames are being stitched right now: {printer_id: session_id}.
# on_print_complete removes the session from _active_sessions *before* handing
# frames_dir to ffmpeg, so for the length of a stitch (up to 300s) nothing in
# _active_sessions marks that directory as in use. Without this second registry
# the only thing standing between an in-progress stitch and
# cleanup_orphaned_timelapse_sessions() is the age margin — whose default is
# exactly the stitch timeout, so there is no headroom at all.
_finalizing_sessions: dict[int, str] = {}


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
    snapshot_url: str | None = None  # Optional single-frame override; #1177
    rotation: int = 0  # Printer's configured camera_rotation, degrees clockwise
    last_layer: int = -1
    frame_count: int = 0
    session_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))
    frames_dir: Path = field(init=False)

    def __post_init__(self):
        self.frames_dir = settings.base_dir / "timelapse_frames" / str(self.printer_id) / self.session_id
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created timelapse session %s for printer %s", self.session_id, self.printer_id)

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
            # Reuse the live view's frame instead of opening a second handle on
            # a single-reader device (#2707). Unguarded, a print watched from
            # start to finish recorded zero successful layer captures, and the
            # stitched video came out empty or badly truncated.
            from backend.app.api.routes.camera import live_frame_for_capture

            defer, buffered = live_frame_for_capture(self.printer_id)
            if defer:
                if not buffered:
                    # Viewer attached but nothing buffered yet: skip this layer
                    # rather than compete and kick them off (#1348).
                    logger.debug(
                        "Skipping layer %s for printer %s: viewer attached, no buffered frame yet",
                        layer_num,
                        self.printer_id,
                    )
                    return False
                frame_data = buffered
            else:
                frame_data = await capture_frame(self.camera_url, self.camera_type, snapshot_url=self.snapshot_url)
            if frame_data:
                if self.rotation:
                    frame_data = await asyncio.to_thread(apply_camera_rotation, frame_data, self.rotation, logger)
                frame_path = self.frames_dir / f"layer_{layer_num:05d}.jpg"
                await asyncio.to_thread(frame_path.write_bytes, frame_data)
                self.frame_count += 1
                logger.debug(
                    "Captured layer %s for printer %s (frame %s)", layer_num, self.printer_id, self.frame_count
                )
                return True
            else:
                logger.warning("Failed to capture frame for layer %s", layer_num)
                return False
        except Exception as e:
            logger.error("Error capturing timelapse frame: %s", e)
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
            logger.error("Failed to create concat file: %s", e)
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
                logger.error("ffmpeg timelapse stitch failed: %s", stderr.decode()[:500])
                return False

            logger.info("Created timelapse video: %s (%s frames)", output_path, self.frame_count)
            return True

        except TimeoutError:
            logger.error("Timelapse stitching timed out")
            if process:
                process.kill()
            return False
        except Exception as e:
            logger.error("Timelapse stitch failed: %s", e)
            return False

    def cleanup(self):
        """Remove temporary frames directory."""
        try:
            if self.frames_dir.exists():
                shutil.rmtree(self.frames_dir, ignore_errors=True)
                logger.info("Cleaned up timelapse frames for session %s", self.session_id)
        except Exception as e:
            logger.warning("Failed to cleanup timelapse frames: %s", e)


def start_session(
    printer_id: int,
    archive_id: int | None,
    url: str,
    cam_type: str,
    snapshot_url: str | None = None,
    rotation: int = 0,
) -> TimelapseSession:
    """Start new timelapse session for a printer.

    Args:
        printer_id: The printer ID
        archive_id: Associated print archive ID (optional)
        url: External camera URL
        cam_type: Camera type ("mjpeg", "rtsp", "snapshot")
        snapshot_url: Optional single-frame URL override; when set, layer captures
            fetch from it directly instead of opening the live stream. #1177.
        rotation: Printer's configured camera_rotation (degrees clockwise),
            applied to every captured frame before it's saved.

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
        snapshot_url=snapshot_url,
        rotation=rotation,
    )
    _active_sessions[printer_id] = session
    logger.info("Started timelapse session for printer %s", printer_id)
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
        logger.info("No timelapse frames captured for printer %s", printer_id)
        session.cleanup()
        return None

    # Create output path in parent of frames dir
    output_path = session.frames_dir.parent / f"timelapse_{session.session_id}.mp4"

    # The session is already out of _active_sessions, so mark it finalizing for
    # the length of the stitch — otherwise a sweep running now sees a frames
    # directory that matches no session and whose mtime is the last layer's
    # write, which on a tall print's final layer is easily older than the age
    # margin, and deletes ffmpeg's input from under it.
    _finalizing_sessions[printer_id] = session.session_id
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
        logger.error("Timelapse completion failed: %s", e)
        session.cleanup()
        return None
    finally:
        _finalizing_sessions.pop(printer_id, None)


def cancel_session(printer_id: int):
    """Cancel and cleanup timelapse session (on print fail/cancel).

    Args:
        printer_id: The printer ID
    """
    session = _active_sessions.pop(printer_id, None)
    if session:
        session.cleanup()
        logger.info("Cancelled timelapse session for printer %s", printer_id)


def get_active_sessions() -> dict[int, TimelapseSession]:
    """Get all active timelapse sessions."""
    return _active_sessions.copy()


def cleanup_orphaned_timelapse_sessions(min_age_seconds: float = 300) -> int:
    """Remove timelapse_frames/<printer_id>/* left behind by a crash or
    restart that happened while a session was active.

    _active_sessions is in-memory only, so a process restart loses track of
    any in-flight session without ever calling cancel_session()/cleanup() -
    the frames directory (and, if stitching had already produced output
    before the restart, a stray `timelapse_<session_id>.mp4`) are then
    orphaned on disk with nothing else to reap them (unlike the ffmpeg
    orphan janitor in routes/camera.py, there was no equivalent here).

    Safe to call once at startup: normal operation always cleans up via
    on_print_complete/cancel_session, so anything found here predates this
    process - and a restart-recovered print doesn't get a new timelapse
    session either (`_maybe_start_layer_timelapse` is only wired into fresh
    PRINT_START events, see #1353), so an orphaned directory can never be
    resumed.

    Also safe to call mid-run, which needs all three guards rather than the
    age margin alone:

    * `_active_sessions` covers a session that is still capturing.
    * `_finalizing_sessions` covers the stitch window. on_print_complete drops
      the session from `_active_sessions` before handing frames_dir to ffmpeg,
      so without this the directory matches no session for up to 300s while
      being actively read.
    * `min_age_seconds` covers the remaining gap - a session in the middle of
      being created, and the stitched `.mp4` between ffmpeg finishing it and
      the caller attaching and unlinking it. Both are freshly written, so the
      margin has real headroom there; it did NOT have any for the stitch
      window, whose length is bounded by the same 300s.

    Returns the number of orphaned directories/files removed.
    """
    base_dir = settings.base_dir / "timelapse_frames"
    if not base_dir.exists():
        return 0

    now = time.time()
    removed = 0
    for printer_dir in base_dir.iterdir():
        if not printer_dir.is_dir():
            continue
        try:
            printer_id = int(printer_dir.name)
        except ValueError:
            continue

        active_session = _active_sessions.get(printer_id)
        in_use_session_ids = {
            active_session.session_id if active_session else None,
            _finalizing_sessions.get(printer_id),
        } - {None}

        for entry in printer_dir.iterdir():
            # Frame dirs are named "<session_id>/"; stitched-but-not-yet-
            # attached output files are "timelapse_<session_id>.mp4" (see
            # on_print_complete's output_path). Anything else under here was
            # not written by this module, so leave it alone rather than
            # deleting a file on the strength of its age.
            if entry.is_dir():
                entry_session_id = entry.name
            elif entry.name.startswith("timelapse_") and entry.name.endswith(".mp4"):
                entry_session_id = entry.name[len("timelapse_") : -len(".mp4")]
            else:
                continue
            if entry_session_id in in_use_session_ids:
                continue
            try:
                if now - entry.stat().st_mtime < min_age_seconds:
                    continue
            except OSError:
                continue
            try:
                # No ignore_errors: it would swallow a failed removal while the
                # count and the log line below still claimed success, and that
                # log is the only evidence an operator has of what was deleted.
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink(missing_ok=True)
                removed += 1
                logger.info("Removed orphaned timelapse artifact: %s", entry)
            except OSError as e:
                logger.warning("Failed to remove orphaned timelapse artifact %s: %s", entry, e)

    return removed
