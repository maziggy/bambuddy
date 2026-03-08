"""Timelapse video processing service using FFmpeg."""

import asyncio
import json
import logging
import tempfile
from pathlib import Path

from backend.app.services.camera import get_ffmpeg_path

logger = logging.getLogger(__name__)


class TimelapseProcessor:
    """Service for processing timelapse videos with FFmpeg."""

    def __init__(self, input_path: Path):
        self.input_path = input_path
        self.ffmpeg = get_ffmpeg_path()
        if not self.ffmpeg:
            raise RuntimeError("FFmpeg not found")
        # Derive ffprobe path from ffmpeg path
        self.ffprobe = self.ffmpeg.replace("ffmpeg", "ffprobe")

    async def get_info(self) -> dict:
        """Get video metadata using ffprobe."""
        cmd = [
            self.ffprobe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(self.input_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            logger.error("ffprobe failed: %s", stderr.decode())
            raise RuntimeError(f"ffprobe failed: {stderr.decode()}")

        data = json.loads(stdout.decode())
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            {},
        )
        audio_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
            None,
        )

        # Parse frame rate (can be "30/1" or "29.97")
        fps = 30.0
        r_frame_rate = video_stream.get("r_frame_rate", "30/1")
        try:
            if "/" in r_frame_rate:
                num, den = r_frame_rate.split("/")
                fps = float(num) / float(den)
            else:
                fps = float(r_frame_rate)
        except (ValueError, ZeroDivisionError):
            pass  # Keep default fps if frame rate string is unparseable

        return {
            "duration": float(data.get("format", {}).get("duration", 0)),
            "width": video_stream.get("width", 0),
            "height": video_stream.get("height", 0),
            "fps": fps,
            "codec": video_stream.get("codec_name", "unknown"),
            "file_size": int(data.get("format", {}).get("size", 0)),
            "has_audio": audio_stream is not None,
        }

    async def generate_thumbnails(
        self,
        count: int = 10,
        width: int = 160,
    ) -> list[tuple[float, bytes]]:
        """Generate evenly-spaced thumbnail frames.

        Runs up to 4 FFmpeg extractions concurrently for faster processing.
        """
        info = await self.get_info()
        duration = info["duration"]

        if duration <= 0:
            return []

        interval = duration / max(count, 1)
        semaphore = asyncio.Semaphore(4)

        with tempfile.TemporaryDirectory() as tmpdir:

            async def _extract_one(i: int) -> tuple[float, bytes] | None:
                timestamp = i * interval
                output_path = Path(tmpdir) / f"thumb_{i:03d}.jpg"

                cmd = [
                    self.ffmpeg,
                    "-y",
                    "-ss",
                    str(timestamp),
                    "-i",
                    str(self.input_path),
                    "-vframes",
                    "1",
                    "-vf",
                    f"scale={width}:-1",
                    "-q:v",
                    "5",
                    str(output_path),
                ]

                async with semaphore:
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await process.communicate()

                if output_path.exists():
                    return (timestamp, output_path.read_bytes())
                return None

            results = await asyncio.gather(*(_extract_one(i) for i in range(count)))

        return [r for r in results if r is not None]

    async def process(
        self,
        output_path: Path,
        trim_start: float = 0,
        trim_end: float | None = None,
        speed: float = 1.0,
        audio_path: Path | None = None,
        audio_volume: float = 1.0,
    ) -> bool:
        """Process video with trim, speed, and optional audio overlay.

        Args:
            output_path: Where to save the processed video
            trim_start: Start time in seconds
            trim_end: End time in seconds (None = full duration)
            speed: Speed multiplier (0.25 to 4.0)
            audio_path: Optional music file to overlay
            audio_volume: Volume for audio overlay (0.0 to 1.0)

        Returns:
            True if processing succeeded, False otherwise
        """
        # Build FFmpeg command
        cmd = [self.ffmpeg, "-y"]

        # Input seeking (fast seek before input)
        if trim_start > 0:
            cmd.extend(["-ss", str(trim_start)])

        cmd.extend(["-i", str(self.input_path)])

        # Add audio input if provided
        if audio_path:
            cmd.extend(["-i", str(audio_path)])

        # Duration limit
        if trim_end is not None and trim_end > trim_start:
            duration = trim_end - trim_start
            cmd.extend(["-t", str(duration)])

        # Build filters - use filter_complex when we have audio overlay
        video_filter = ""
        if speed != 1.0:
            # setpts changes video speed: PTS/speed = faster, PTS*speed = slower
            setpts_value = 1.0 / speed
            video_filter = f"setpts={setpts_value}*PTS"

        if audio_path:
            # Use filter_complex for audio overlay (can't mix with -vf/-af)
            filter_parts = []

            # Video filter
            if video_filter:
                filter_parts.append(f"[0:v]{video_filter}[v]")
                video_out = "[v]"
            else:
                video_out = "0:v"

            # Audio filter with volume
            filter_parts.append(f"[1:a]volume={audio_volume}[a]")

            cmd.extend(["-filter_complex", ";".join(filter_parts)])
            cmd.extend(["-map", video_out, "-map", "[a]"])
            cmd.extend(["-shortest"])
        elif speed != 1.0:
            # No audio overlay - use simple -vf and -af
            if video_filter:
                cmd.extend(["-vf", video_filter])
            # Adjust original audio speed with atempo
            atempo_chain = self._build_atempo_chain(speed)
            if atempo_chain:
                cmd.extend(["-af", atempo_chain])

        # Output settings
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",  # Enable streaming
                str(output_path),
            ]
        )

        logger.info("Processing timelapse: %s", " ".join(cmd))

        # Run FFmpeg
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        _, stderr = await process.communicate()

        if process.returncode != 0:
            logger.error("FFmpeg processing failed: %s", stderr.decode())
            return False

        return output_path.exists()

    def _build_atempo_chain(self, speed: float) -> str:
        """Build atempo filter chain.

        atempo filter only supports values between 0.5 and 2.0,
        so we chain multiple filters for extreme speeds.
        """
        if speed == 1.0:
            return ""

        filters = []
        remaining_speed = speed

        # Handle speeds > 2.0 by chaining atempo=2.0
        while remaining_speed > 2.0:
            filters.append("atempo=2.0")
            remaining_speed /= 2.0

        # Handle speeds < 0.5 by chaining atempo=0.5
        while remaining_speed < 0.5:
            filters.append("atempo=0.5")
            remaining_speed *= 2.0

        # Add final atempo for remaining adjustment
        # After the while loops above, remaining_speed is guaranteed to be in [0.5, 2.0]
        if remaining_speed != 1.0:
            filters.append(f"atempo={remaining_speed:.4f}")

        return ",".join(filters)
