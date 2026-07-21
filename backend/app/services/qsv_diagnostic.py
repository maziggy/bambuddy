"""Intel Quick Sync capability diagnostic for camera video processing."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_RENDER_DEVICE = Path("/dev/dri/renderD128")
_COMMAND_TIMEOUT_SECONDS = 10.0


@dataclass
class QsvDiagnosticStage:
    name: str
    status: str  # "ok" | "failed" | "skipped"
    duration_ms: int = 0
    code: str | None = None
    detail: str | None = None


@dataclass
class QsvDiagnosticResult:
    available: bool
    overall_status: str  # "ok" | "failed"
    device: str
    stages: list[QsvDiagnosticStage] = field(default_factory=list)
    summary_code: str = ""

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "overall_status": self.overall_status,
            "device": self.device,
            "stages": [asdict(stage) for stage in self.stages],
            "summary_code": self.summary_code,
        }


async def _run_command(*args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise

    return (
        process.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


def _short_error(stderr: str, stdout: str = "") -> str | None:
    text = stderr.strip() or stdout.strip()
    if not text:
        return None

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:500] if lines else None


async def diagnose_qsv() -> QsvDiagnosticResult:
    device = str(_RENDER_DEVICE)
    stages: list[QsvDiagnosticStage] = []

    # Stage 1: FFmpeg executable
    started = time.monotonic()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        stages.append(
            QsvDiagnosticStage(
                name="ffmpeg",
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                code="ffmpeg_not_found",
            )
        )
        stages.extend(
            [
                QsvDiagnosticStage(name="render_device", status="skipped"),
                QsvDiagnosticStage(name="qsv_codecs", status="skipped"),
                QsvDiagnosticStage(name="qsv_initialization", status="skipped"),
            ]
        )
        return QsvDiagnosticResult(
            available=False,
            overall_status="failed",
            device=device,
            stages=stages,
            summary_code="ffmpeg_not_found",
        )

    stages.append(
        QsvDiagnosticStage(
            name="ffmpeg",
            status="ok",
            duration_ms=int((time.monotonic() - started) * 1000),
            detail=ffmpeg,
        )
    )

    # Stage 2: render device and process permissions
    started = time.monotonic()
    if not _RENDER_DEVICE.exists():
        stages.append(
            QsvDiagnosticStage(
                name="render_device",
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                code="render_device_missing",
                detail=device,
            )
        )
        stages.extend(
            [
                QsvDiagnosticStage(name="qsv_codecs", status="skipped"),
                QsvDiagnosticStage(name="qsv_initialization", status="skipped"),
            ]
        )
        return QsvDiagnosticResult(
            available=False,
            overall_status="failed",
            device=device,
            stages=stages,
            summary_code="render_device_missing",
        )

    if not os.access(_RENDER_DEVICE, os.R_OK | os.W_OK):
        stages.append(
            QsvDiagnosticStage(
                name="render_device",
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                code="render_device_permission_denied",
                detail=device,
            )
        )
        stages.extend(
            [
                QsvDiagnosticStage(name="qsv_codecs", status="skipped"),
                QsvDiagnosticStage(name="qsv_initialization", status="skipped"),
            ]
        )
        return QsvDiagnosticResult(
            available=False,
            overall_status="failed",
            device=device,
            stages=stages,
            summary_code="render_device_permission_denied",
        )

    stages.append(
        QsvDiagnosticStage(
            name="render_device",
            status="ok",
            duration_ms=int((time.monotonic() - started) * 1000),
            detail=device,
        )
    )

    # Stage 3: required FFmpeg codecs
    started = time.monotonic()
    try:
        decoder_rc, decoder_out, decoder_err = await _run_command(
            ffmpeg,
            "-hide_banner",
            "-decoders",
        )
        encoder_rc, encoder_out, encoder_err = await _run_command(
            ffmpeg,
            "-hide_banner",
            "-encoders",
        )
    except TimeoutError:
        stages.append(
            QsvDiagnosticStage(
                name="qsv_codecs",
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                code="diagnostic_timeout",
            )
        )
        stages.append(QsvDiagnosticStage(name="qsv_initialization", status="skipped"))
        return QsvDiagnosticResult(
            available=False,
            overall_status="failed",
            device=device,
            stages=stages,
            summary_code="diagnostic_timeout",
        )

    if decoder_rc != 0 or encoder_rc != 0:
        stages.append(
            QsvDiagnosticStage(
                name="qsv_codecs",
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                code="ffmpeg_codec_query_failed",
                detail=_short_error(decoder_err + encoder_err, decoder_out + encoder_out),
            )
        )
        stages.append(QsvDiagnosticStage(name="qsv_initialization", status="skipped"))
        return QsvDiagnosticResult(
            available=False,
            overall_status="failed",
            device=device,
            stages=stages,
            summary_code="ffmpeg_codec_query_failed",
        )

    missing: list[str] = []
    if "h264_qsv" not in decoder_out:
        missing.append("h264_qsv")
    if "mjpeg_qsv" not in encoder_out:
        missing.append("mjpeg_qsv")

    if missing:
        code = "h264_qsv_missing" if missing == ["h264_qsv"] else "mjpeg_qsv_missing"
        if len(missing) > 1:
            code = "qsv_codecs_missing"

        stages.append(
            QsvDiagnosticStage(
                name="qsv_codecs",
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                code=code,
                detail=", ".join(missing),
            )
        )
        stages.append(QsvDiagnosticStage(name="qsv_initialization", status="skipped"))
        return QsvDiagnosticResult(
            available=False,
            overall_status="failed",
            device=device,
            stages=stages,
            summary_code=code,
        )

    stages.append(
        QsvDiagnosticStage(
            name="qsv_codecs",
            status="ok",
            duration_ms=int((time.monotonic() - started) * 1000),
            detail="h264_qsv, mjpeg_qsv",
        )
    )

    # Stage 4: initialize the same QSV device and MJPEG encoder used by
    # the camera pipeline. This catches missing oneVPL/media-driver setups
    # even when FFmpeg lists the codecs.
    started = time.monotonic()
    command = (
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-init_hw_device",
        f"qsv=hw:{device}",
        "-filter_hw_device",
        "hw",
        "-f",
        "lavfi",
        "-i",
        "color=size=64x64:rate=1:duration=1",
        "-vf",
        "format=nv12,hwupload=extra_hw_frames=16",
        "-frames:v",
        "1",
        "-c:v",
        "mjpeg_qsv",
        "-f",
        "null",
        "-",
    )

    try:
        return_code, stdout, stderr = await _run_command(*command)
    except TimeoutError:
        stages.append(
            QsvDiagnosticStage(
                name="qsv_initialization",
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                code="diagnostic_timeout",
            )
        )
        return QsvDiagnosticResult(
            available=False,
            overall_status="failed",
            device=device,
            stages=stages,
            summary_code="diagnostic_timeout",
        )

    if return_code != 0:
        stages.append(
            QsvDiagnosticStage(
                name="qsv_initialization",
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                code="qsv_initialization_failed",
                detail=_short_error(stderr, stdout),
            )
        )
        return QsvDiagnosticResult(
            available=False,
            overall_status="failed",
            device=device,
            stages=stages,
            summary_code="qsv_initialization_failed",
        )

    stages.append(
        QsvDiagnosticStage(
            name="qsv_initialization",
            status="ok",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    )

    return QsvDiagnosticResult(
        available=True,
        overall_status="ok",
        device=device,
        stages=stages,
        summary_code="available",
    )
