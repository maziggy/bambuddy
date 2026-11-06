"""Layer-based timelapse for external cameras.

Captures a frame on each layer change and stitches them into a video on print completion.
"""

import asyncio
import contextlib
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from backend.app.core.config import settings
from backend.app.services.camera import apply_camera_rotation
from backend.app.services.external_camera import capture_frame
from backend.app.utils.ffmpeg_output import NO_FFMPEG_OUTPUT, X264_BOUNDED_ARGS, summarize_ffmpeg_stderr

logger = logging.getLogger(__name__)

# Active timelapse sessions: {printer_id: TimelapseSession}
_active_sessions: dict[int, "TimelapseSession"] = {}

# Sessions whose frames are being stitched right now: {printer_id: session_id}.
# on_print_complete removes the session from _active_sessions *before* handing
# frames_dir to ffmpeg, so for the length of a stitch (up to an hour, see
# _stitch_timeout) nothing in _active_sessions marks that directory as in use.
# Without this second registry the only thing standing between an in-progress
# stitch and cleanup_orphaned_timelapse_sessions() is the age margin, which is
# far shorter than a long stitch.
# A set per printer: a short print can finish while a long one still stitches.
_finalizing_sessions: dict[int, set[str]] = {}

# Stitch deadline: a libx264 encode of every frame takes about this long per
# frame on a Raspberry Pi, never less than 5 minutes and never more than an
# hour. A flat 300 s lost the timelapse of any print with more than a few
# hundred layers on slow hardware.
_STITCH_SECONDS_PER_FRAME = 0.5
_STITCH_MIN_SECONDS = 300.0
_STITCH_MAX_SECONDS = 3600.0

# Frames larger than 1080p (counted in pixels, so a portrait camera keeps its
# shape) are scaled down to it; both sides are made even, as yuv420p requires.
STITCH_SCALE_FILTER = "scale=w='if(gt(iw*ih,2073600),trunc(iw*sqrt(2073600/(iw*ih))/2)*2,trunc(iw/2)*2)':h=-2"

# A stitch that fails keeps its frames, marked with this file, so the video can
# still be made from them; the orphan janitor leaves them for this long.
KEPT_FRAMES_MARKER = "STITCH_FAILED"
KEEP_FAILED_FRAMES_SECONDS = 24 * 60 * 60


def _stitch_timeout(frame_count: int) -> float:
    return min(max(_STITCH_MIN_SECONDS, frame_count * _STITCH_SECONDS_PER_FRAME), _STITCH_MAX_SECONDS)


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
    # Set once ffmpeg has been started on the frames: only a stitch that got
    # that far and failed keeps them. Without ffmpeg, or without frames, there
    # is nothing a later attempt could do differently.
    ffmpeg_ran: bool = field(default=False, init=False)

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
            "-vf",
            STITCH_SCALE_FILTER,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-crf",
            "23",
            # Bounded memory; see X264_BOUNDED_ARGS (#3292).
            *X264_BOUNDED_ARGS,
            str(output_path),
        ]

        process = None
        timeout = _STITCH_MIN_SECONDS
        # Marked before ffmpeg starts, so a restart in the middle of a stitch
        # (which can now run an hour) keeps the frames too.
        self.mark_kept("The timelapse was being stitched when Bambuddy stopped.")
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.frames_dir),  # Run in frames dir so relative paths work
            )
            self.ffmpeg_ran = True

            timeout = _stitch_timeout(len(frame_files))
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)

            if process.returncode != 0:
                logger.error("ffmpeg timelapse stitch failed: %s", summarize_ffmpeg_stderr(stderr) or NO_FFMPEG_OUTPUT)
                output_path.unlink(missing_ok=True)
                return False

            logger.info("Created timelapse video: %s (%s frames)", output_path, self.frame_count)
            return True

        except TimeoutError:
            logger.error("Timelapse stitching timed out after %.0f s (%s frames)", timeout, len(frame_files))
            output_path.unlink(missing_ok=True)
            return False
        except Exception as e:
            logger.error("Timelapse stitch failed: %s", e)
            output_path.unlink(missing_ok=True)
            return False
        except asyncio.CancelledError:
            # A stitch cut short leaves a truncated file behind; not a video.
            output_path.unlink(missing_ok=True)
            raise
        finally:
            # However the stitch ends -- its deadline, an error, or the task
            # being cancelled -- an ffmpeg it started does not keep running and
            # holding memory (#3292).
            if process is not None and process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    logger.warning("ffmpeg did not exit after the timelapse stitch was killed")

    def mark_kept(self, reason: str) -> bool:
        """Mark the frames to be kept for KEEP_FAILED_FRAMES_SECONDS."""
        try:
            marker = self.frames_dir / KEPT_FRAMES_MARKER  # SEC-PATH-OK: this module's constant filename
            marker.write_text(
                f"{reason}\nThese frames are kept for 24 hours. To make the video from them, run in this folder:\n"
                f'ffmpeg -f concat -safe 0 -i frames.txt -vf "{STITCH_SCALE_FILTER}" -c:v libx264 -pix_fmt yuv420p -threads 2 timelapse.mp4\n'
            )
            return True
        except OSError as e:
            logger.warning("Could not mark timelapse frames as kept: %s", e)
            return False

    def keep_frames(self) -> None:
        """Keep the frames of a failed stitch for a day instead of deleting them."""
        if self.mark_kept("The timelapse could not be stitched."):
            logger.warning(
                "Timelapse frames for printer %s kept for 24 hours at %s (stitching failed)",
                self.printer_id,
                self.frames_dir,
            )
        else:
            self.cleanup()

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
    session = _active_sessions.get(printer_id)
    if not session:
        return None

    # Marked finalizing for the length of the stitch, and before it leaves
    # _active_sessions (the janitor runs in a thread) — otherwise a sweep sees
    # a frames directory that matches no session and whose mtime is the last
    # layer's write, which on a tall print's final layer is easily older than
    # the age margin, and deletes ffmpeg's input from under it.
    _finalizing_sessions.setdefault(printer_id, set()).add(session.session_id)
    _active_sessions.pop(printer_id, None)
    try:
        if session.frame_count == 0:
            logger.info("No timelapse frames captured for printer %s", printer_id)
            session.cleanup()
            return None

        # Create output path in parent of frames dir
        output_path = session.frames_dir.parent / f"timelapse_{session.session_id}.mp4"
        success = await session.stitch(output_path)
        if success:
            # Cleanup frames after successful stitch
            session.cleanup()
            return output_path
        _keep_or_clean(session)
        return None
    except Exception as e:
        logger.error("Timelapse completion failed: %s", e)
        _keep_or_clean(session)
        return None
    finally:
        finalizing = _finalizing_sessions.get(printer_id)
        if finalizing is not None:
            finalizing.discard(session.session_id)
            if not finalizing:
                _finalizing_sessions.pop(printer_id, None)


def _keep_or_clean(session: "TimelapseSession") -> None:
    """After a failed stitch: keep the frames if ffmpeg ran on them (a slow
    encode, an ffmpeg error), so the video can still be made; delete them if it
    never could (no ffmpeg, no frames)."""
    if session.ffmpeg_ran:
        session.keep_frames()
    else:
        session.cleanup()


def cancel_session(printer_id: int):
    """Cancel and cleanup timelapse session (on print fail/cancel).

    Args:
        printer_id: The printer ID
    """
    session = _active_sessions.pop(printer_id, None)
    if session:
        session.cleanup()
        logger.info("Cancelled timelapse session for printer %s", printer_id)


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
      so without this the directory matches no session for up to an hour
      (`_stitch_timeout`) while being actively read.
    * `min_age_seconds` covers the remaining gap - a session in the middle of
      being created, and the stitched `.mp4` between ffmpeg finishing it and
      the caller attaching and unlinking it. Both are freshly written, so the
      margin has real headroom there; it has none for the stitch window.

    Frames a failed stitch kept (`KEPT_FRAMES_MARKER`) stay for
    `KEEP_FAILED_FRAMES_SECONDS`, counted from the marker.

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
        in_use_session_ids = {active_session.session_id if active_session else None} - {None}
        in_use_session_ids |= _finalizing_sessions.get(printer_id, set())

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
                marker = entry / KEPT_FRAMES_MARKER if entry.is_dir() else None
                if marker is not None and marker.exists() and now - marker.stat().st_mtime < KEEP_FAILED_FRAMES_SECONDS:
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
