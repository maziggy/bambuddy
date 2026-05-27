"""End-to-end camera diagnostic, surfaced via ``POST /printers/{id}/camera/diagnose``.

Cuts off the "camera broken" support-ticket loop at the user's screen by
running the printer-side camera path through staged checks (TCP, end-
to-end frame capture) and reporting WHICH stage failed plus a
remediation key the frontend can render translated.

The goal isn't to be a perfect protocol analyser — it's to be the diff
between "user opens a ticket with 'connection lost'" and "user sees
'Printer not reachable; check IP and LAN-only mode'" before they ever
write a message.

Stages
------

1. **tcp_reachable** — open a TCP socket to the camera port (322 for
   RTSPS models, 6000 for the chamber-image-protocol A1 / P1 family).
   Distinguishes "printer down" / "firewall" / "LAN-only off" from
   stream-content problems.
2. **first_frame** — call the existing ``capture_camera_frame_bytes``
   pipeline (same code that powers /camera/snapshot) and verify at
   least one JPEG comes back within the model's profile-derived
   timeout. Combines auth + protocol handshake + first keyframe into
   one stage because splitting RTSP's ``ffmpeg`` invocation is heavy
   and the user-facing answer is the same either way: "the camera
   itself isn't producing frames".

Shortcut
--------

Most Bambu firmwares allow exactly one concurrent camera connection.
Opening a fresh socket while a viewer is attached would kick them off
(and trigger the same #1348 reconnect-storm pattern we built the fan-
out broadcaster to prevent). When ``is_stream_active`` reports True
AND a buffered frame is fresh (last 10 s), we short-circuit the test
with ``live_stream_active`` and report success — the user is
literally watching the camera right now, no test needed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from backend.app.services.camera import (
    capture_camera_frame_bytes,
    get_camera_port,
    is_chamber_image_model,
)
from backend.app.services.camera_profiles import DEFAULT_PROFILE, get_camera_profile

logger = logging.getLogger(__name__)


# How long a live-stream buffered frame stays "fresh enough" to count as
# proof that the camera works. Tuned conservatively — if the active
# stream hasn't produced a frame in this window, run the real test
# instead of trusting a possibly-stale buffer.
_LIVE_FRAME_FRESHNESS_SECONDS = 10.0


@dataclass
class CameraDiagnoseStage:
    """One step of the diagnostic. Status drives the green/red icon
    the frontend renders next to the stage name."""

    name: str  # "tcp_reachable" | "first_frame" | "live_stream_active"
    status: str  # "ok" | "failed" | "skipped"
    duration_ms: int = 0
    # Optional machine-readable code for failures so the frontend can
    # render a stage-specific hint without parsing free-text errors.
    code: str | None = None


@dataclass
class CameraDiagnoseResult:
    printer_id: int
    protocol: str  # "rtsp" | "chamber_image"
    port: int
    # Whether this model's camera path uses the default profile or has
    # an override entry in ``camera_profiles._PROFILES``. Useful for
    # triage: tells us instantly whether the user is on a tuned model.
    profile: str
    overall_status: str  # "ok" | "failed"
    stages: list[CameraDiagnoseStage] = field(default_factory=list)
    # i18n key. Frontend maps to a translated remediation hint.
    summary_code: str = ""

    def to_dict(self) -> dict:
        return {
            "printer_id": self.printer_id,
            "protocol": self.protocol,
            "port": self.port,
            "profile": self.profile,
            "overall_status": self.overall_status,
            "stages": [
                {"name": s.name, "status": s.status, "duration_ms": s.duration_ms, "code": s.code} for s in self.stages
            ],
            "summary_code": self.summary_code,
        }


def _profile_label(model: str | None) -> str:
    """Return ``"default"`` or the resolved model name when this model
    has an override entry in :data:`camera_profiles._PROFILES`."""
    profile = get_camera_profile(model)
    if profile is DEFAULT_PROFILE:
        return "default"
    # Normalise via the same alias map the lookup uses. If the model
    # resolves to a profile but the lookup is by alias (e.g. N7 → P2S),
    # report the canonical display name.
    from backend.app.services.camera_profiles import _MODEL_ALIASES, _PROFILES

    key = (model or "").upper().strip()
    key = _MODEL_ALIASES.get(key, key)
    return key if key in _PROFILES else "default"


async def _check_tcp_reachable(ip_address: str, port: int, timeout: float) -> CameraDiagnoseStage:
    """Stage 1 — open a TCP socket to the camera port."""
    started = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip_address, port),
            timeout=timeout,
        )
        try:
            writer.close()
            await writer.wait_closed()
        except OSError:
            pass
        return CameraDiagnoseStage(
            name="tcp_reachable",
            status="ok",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except asyncio.TimeoutError:
        return CameraDiagnoseStage(
            name="tcp_reachable",
            status="failed",
            duration_ms=int((time.monotonic() - started) * 1000),
            code="tcp_timeout",
        )
    except (ConnectionRefusedError, OSError) as exc:
        # ConnectionRefusedError = printer up, camera port closed (likely
        # LAN-only off or developer mode off). Other OSError = host
        # unreachable. We keep these separate codes so the frontend can
        # surface a precise remediation hint.
        is_refused = isinstance(exc, ConnectionRefusedError)
        return CameraDiagnoseStage(
            name="tcp_reachable",
            status="failed",
            duration_ms=int((time.monotonic() - started) * 1000),
            code="tcp_refused" if is_refused else "tcp_unreachable",
        )


async def _check_first_frame(
    ip_address: str,
    access_code: str,
    model: str | None,
    timeout: int,
) -> CameraDiagnoseStage:
    """Stage 2 — capture one frame end-to-end. Combines auth + protocol
    handshake + first keyframe; either it works or it doesn't."""
    started = time.monotonic()
    try:
        jpeg = await capture_camera_frame_bytes(
            ip_address=ip_address,
            access_code=access_code,
            model=model,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — see camera_profiles.py rationale
        # capture_camera_frame_bytes can raise from many layers (ffmpeg
        # spawn, TLS proxy startup, asyncio.open_connection). For the
        # user-facing answer, any exception during the capture path is
        # "first frame failed" — drilling down is for the support log.
        logger.warning("Camera diagnose first-frame capture raised: %s", exc)
        return CameraDiagnoseStage(
            name="first_frame",
            status="failed",
            duration_ms=int((time.monotonic() - started) * 1000),
            code="capture_exception",
        )
    if jpeg:
        return CameraDiagnoseStage(
            name="first_frame",
            status="ok",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    return CameraDiagnoseStage(
        name="first_frame",
        status="failed",
        duration_ms=int((time.monotonic() - started) * 1000),
        code="no_frame",
    )


def _summary_for_stages(stages: list[CameraDiagnoseStage]) -> str:
    """Pick the remediation key from the first failing stage's ``code``,
    or ``all_ok`` when every stage passed."""
    for stage in stages:
        if stage.status != "failed":
            continue
        if stage.code == "tcp_timeout":
            return "printer_unreachable"
        if stage.code == "tcp_refused":
            return "camera_port_closed"
        if stage.code == "tcp_unreachable":
            return "printer_unreachable"
        if stage.code in ("no_frame", "capture_exception"):
            return "no_frame"
        return "unknown_failure"
    return "all_ok"


async def diagnose_camera(
    ip_address: str,
    access_code: str,
    model: str | None,
    printer_id: int,
    *,
    has_live_stream: bool = False,
    live_frame_age_seconds: float | None = None,
    tcp_timeout: float = 3.0,
    capture_timeout: int = 15,
) -> CameraDiagnoseResult:
    """Run the camera diagnostic and return a structured result.

    ``has_live_stream`` and ``live_frame_age_seconds`` are looked up
    by the route handler from the active-stream registry (see the
    docstring at the top of this file for why). When they indicate a
    fresh frame is already buffered, the diagnostic short-circuits with
    a ``live_stream_active`` stage and ``all_ok`` summary — real-world
    proof of a working camera beats any synthetic test.
    """
    is_chamber = is_chamber_image_model(model)
    protocol = "chamber_image" if is_chamber else "rtsp"
    port = get_camera_port(model)

    result = CameraDiagnoseResult(
        printer_id=printer_id,
        protocol=protocol,
        port=port,
        profile=_profile_label(model),
        overall_status="ok",
        stages=[],
    )

    # Shortcut: the camera is currently streaming with a fresh frame.
    # Running the real diagnostic here would either kick the live
    # viewer off (single-camera-connection printers) or block on the
    # second-socket-refused timeout (#1348). Trust the live evidence.
    if (
        has_live_stream
        and live_frame_age_seconds is not None
        and 0 <= live_frame_age_seconds < _LIVE_FRAME_FRESHNESS_SECONDS
    ):
        result.stages.append(
            CameraDiagnoseStage(
                name="live_stream_active",
                status="ok",
                duration_ms=0,
            )
        )
        result.summary_code = "live_stream_active_healthy"
        return result

    # Stage 1
    tcp_stage = await _check_tcp_reachable(ip_address, port, tcp_timeout)
    result.stages.append(tcp_stage)
    if tcp_stage.status != "ok":
        result.overall_status = "failed"
        # Skip first_frame — without TCP there's no point spawning ffmpeg.
        result.stages.append(CameraDiagnoseStage(name="first_frame", status="skipped", duration_ms=0))
        result.summary_code = _summary_for_stages(result.stages)
        return result

    # Stage 2
    frame_stage = await _check_first_frame(ip_address, access_code, model, capture_timeout)
    result.stages.append(frame_stage)
    if frame_stage.status != "ok":
        result.overall_status = "failed"
    result.summary_code = _summary_for_stages(result.stages)
    return result
