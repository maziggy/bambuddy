"""AI-backed build-plate occupancy check (selectable alternative to the OpenCV backend).

Sends one downscaled camera snapshot to a configured OpenAI-compatible vision
model (chat/completions, structured JSON output) and maps the verdict back
onto PlateDetectionResult. Off by default (bedcheck_backend='opencv'); fails
open (is_empty=True) on any error -- see check_bed_ai().
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
from base64 import b64encode

import httpx
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from backend.app.core.database import async_session
from backend.app.models.settings import Settings
from backend.app.services.plate_detection import PlateDetectionResult

logger = logging.getLogger(__name__)

DOWNSCALE_MAX_EDGE = 768
# Module constant, not a user setting. 27.84s measured cold-start (model
# evicted from VRAM) on the pinned Ollama target justifies ~2x headroom; warm
# calls measure 0.8-1.5s at 768px, so this costs nothing on the common path.
DEFAULT_TIMEOUT = 60.0

VERDICT_JSON_SCHEMA = {
    "name": "bed_occupancy_verdict",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "is_empty": {
                "type": "boolean",
                "description": (
                    "true if the build plate is bare and empty; false if anything "
                    "(a print, filament, tool, debris) is on it"
                ),
            },
            "confidence": {
                "type": "number",
                "description": (
                    "Confidence in is_empty, as a decimal fraction from 0.0 (pure guess) "
                    "to 1.0 (certain). Always between 0.0 and 1.0 -- never a percentage, "
                    "never greater than 1.0."
                ),
            },
            "reason": {
                "type": "string",
                "description": "One short sentence (under 20 words) describing what is on the plate, or confirming it is bare",
            },
        },
        "required": ["is_empty", "confidence", "reason"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = (
    "You are inspecting a 3D printer's build plate through its camera to decide whether "
    "it is safe to start a new print. Look only at the build plate surface, not the "
    "printer frame, gantry, or background. Respond with nothing but the JSON object "
    "described by the response schema -- no other text, no markdown code fences, no "
    'explanation outside the "reason" field. The "confidence" field must always be a '
    "decimal fraction between 0.0 and 1.0 (for example 0.87) -- never a percentage, "
    "never a value above 1.0."
)

USER_PROMPT_INTRO_NO_REFS = (
    "Here is a live snapshot of a 3D printer's build plate. Decide: is the build plate "
    "empty and ready for a new print, or is there a finished/failed print, loose "
    "filament, a tool, debris, or anything else on it?"
)


class AiBedCheckError(Exception):
    """Internal failure signal for the AI bed-check pipeline.

    Always raised with an already-generic, user-safe message (see the raise
    sites below and _generic_fail_open_reason) -- never wraps a raw upstream
    string. Never escapes check_bed_ai(), which catches it (and everything
    else) and fails open.
    """


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _downscale_jpeg(image_data: bytes) -> bytes:
    """Downscale to DOWNSCALE_MAX_EDGE on the longest edge, re-encode as JPEG.

    Pillow, not OpenCV -- bedcheck_ai.py never touches cv2, so it works
    identically whether or not OPENCV_AVAILABLE. Raises AiBedCheckError on a
    truncated/non-image payload -- capture_camera_image can hand back a
    partial MJPEG frame grab, and that must fail closed here rather than be
    sent to the vision model as garbage.
    """
    try:
        img = Image.open(io.BytesIO(image_data))
        img.load()
        img = img.convert("RGB")
    except (UnidentifiedImageError, OSError) as e:
        raise AiBedCheckError("camera frame could not be processed") from e

    longest_edge = max(img.size)
    if longest_edge > DOWNSCALE_MAX_EDGE:
        scale = DOWNSCALE_MAX_EDGE / longest_edge
        new_size = (round(img.width * scale), round(img.height * scale))
        img = img.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _data_uri(jpeg_bytes: bytes) -> str:
    return f"data:image/jpeg;base64,{b64encode(jpeg_bytes).decode('ascii')}"


def _build_messages(image_data: bytes) -> list[dict]:
    """Build the chat/completions `messages` list for one verdict request.

    Zero-shot only: a single user-prompt text part plus the live snapshot.
    (A reference-photo prompt variant was benched against this and measured
    no accuracy gain, so it was cut rather than shipped as dead code -- see
    the bed-check A/B bench notes in fork history if a fuller comparison ever
    warrants resurrecting it.)
    """
    live_uri = _data_uri(_downscale_jpeg(image_data))

    user_content = [
        {"type": "text", "text": USER_PROMPT_INTRO_NO_REFS},
        {"type": "image_url", "image_url": {"url": live_uri}},
    ]

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _has_required_keys(data: object) -> bool:
    return isinstance(data, dict) and isinstance(data.get("is_empty"), bool) and "confidence" in data


def _parse_verdict_json(raw: str) -> dict:
    """Parse a verdict JSON object out of a raw model response.

    Tries a clean json.loads first, then falls back to extracting the first
    {...} block (handles models that wrap JSON in markdown fences despite
    instructions). Raises AiBedCheckError("invalid response from AI backend")
    if neither parses, or the parsed object is missing required keys / has a
    non-bool is_empty -- the caller treats this identically to a parse
    failure and retries once.
    """
    try:
        data = json.loads(raw.strip())
    except (json.JSONDecodeError, AttributeError):
        data = None

    if not _has_required_keys(data):
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None

    if not _has_required_keys(data):
        raise AiBedCheckError("invalid response from AI backend")

    return data


def _clamp_confidence(value) -> float:
    """Guards against a schema-valid but semantically-wrong percentage-style
    value (e.g. 95 instead of 0.95) reaching main.py's `:.0%` format.
    """
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.0
    if c > 1.0:
        c = c / 100.0
    return max(0.0, min(1.0, c))


async def _post_chat(base_url: str, model: str, api_key: str, messages: list[dict]) -> str:
    """POST one chat/completions request, return the raw message content string.

    Network/timeout/non-2xx failures are raised unwrapped (httpx exception
    types) -- the caller does not retry these. A malformed 200 envelope
    (missing choices, content-less message, an {"error": ...} body returned
    with status 200) is raised as AiBedCheckError("invalid response from AI
    backend") -- treated as a parse failure by the caller, so the one retry
    still applies.
    """
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 300,
        "messages": messages,
        "response_format": {"type": "json_schema", "json_schema": VERDICT_JSON_SCHEMA},
    }
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers=_auth_headers(api_key),
        )
        resp.raise_for_status()
        body = resp.json()

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise AiBedCheckError("invalid response from AI backend") from e

    if not isinstance(content, str):
        # A tool-call-only message returns "content": null (or a list-shaped
        # content) alongside "tool_calls" on some OpenAI-compatible backends --
        # the lookup above succeeds without raising, so this needs its own
        # check to actually cover the content-less-message case this
        # function's docstring claims to handle.
        raise AiBedCheckError("invalid response from AI backend")

    return content


async def _load_ai_settings() -> dict:
    """Narrow, independently-committed read of the 3 AI-backend connection keys.

    Mirrors obico_detection.py's own _load_settings() (module :123-156): a
    plain `async with async_session()` block that opens, reads, and closes
    before any outbound I/O, so no pooled connection is held across the
    vision-model call.
    """
    keys = ["bedcheck_ai_base_url", "bedcheck_ai_model", "bedcheck_ai_api_key"]
    async with async_session() as db:
        result = await db.execute(select(Settings).where(Settings.key.in_(keys)))
        rows = {r.key: r.value for r in result.scalars().all()}

    return {
        "base_url": (rows.get("bedcheck_ai_base_url") or "").rstrip("/"),
        "model": (rows.get("bedcheck_ai_model") or "").strip(),
        "api_key": (rows.get("bedcheck_ai_api_key") or "").strip(),
    }


async def _analyze_frame_ai(image_data: bytes, printer_id: int) -> tuple[bool, float, str]:
    """The raising primitive: (is_empty, confidence, reason), or raises AiBedCheckError.

    printer_id is accepted but currently unused -- kept on the signature so
    callers don't need to change if a per-printer variant of this check is
    ever added.
    """
    cfg = await _load_ai_settings()
    if not cfg["base_url"] or not cfg["model"]:
        # Both empty base_url and empty model short-circuit with no network
        # call attempted.
        raise AiBedCheckError("AI backend not configured")

    messages = _build_messages(image_data)

    try:
        raw = await _post_chat(cfg["base_url"], cfg["model"], cfg["api_key"], messages)
        data = _parse_verdict_json(raw)
    except AiBedCheckError:
        # One retry only, on parse/schema failure (malformed JSON, missing
        # keys, or a malformed-but-200 envelope) -- never on timeout/non-2xx,
        # which raise as bare httpx exceptions and propagate unwrapped.
        retry_messages = [
            *messages,
            {
                "role": "user",
                "content": "Respond with a single JSON object only, matching the schema. No prose, no markdown fences.",
            },
        ]
        raw = await _post_chat(cfg["base_url"], cfg["model"], cfg["api_key"], retry_messages)
        data = _parse_verdict_json(raw)

    is_empty = bool(data["is_empty"])
    confidence = _clamp_confidence(data.get("confidence"))
    reason = str(data.get("reason") or "").strip()
    return is_empty, confidence, reason


def build_ai_result(is_empty: bool, confidence: float, reason: str, camera_source: str) -> PlateDetectionResult:
    """Pure formatter, no I/O -- maps a verdict onto the shared PlateDetectionResult shape."""
    difference_percent = round(confidence * 100, 1) if not is_empty else 0.0
    status = "Plate appears empty" if is_empty else "Objects detected on plate"
    suffix = f": {reason}" if reason else ""
    message = f"[{camera_source}] {status} (AI, confidence {confidence:.0%}){suffix}"
    return PlateDetectionResult(
        is_empty=is_empty,
        confidence=confidence,
        difference_percent=difference_percent,
        message=message,
        # Always False -- the AI backend has no calibration reference to be
        # missing, and this preserves main.py's existing needs_calibration
        # pause-gate behavior unchanged.
        needs_calibration=False,
        backend="ai",
        ai_reason=reason or None,
    )


def _generic_fail_open_reason(e: Exception) -> str:
    """Map any check_bed_ai() failure to a short, generic, user-facing reason.

    Never includes a URL, hostname, or raw exception text: httpx.HTTPStatusError's
    str() embeds the full configured request URL, and this string reaches
    camera.py's manual-check route, CAMERA_VIEW-gated -- a lower bar than
    settings:read, which routes/obico.py's get_printer_status (dev :52-57)
    deliberately holds error strings behind for exactly this reason. Full
    detail always goes to the logger, never to this string.
    """
    if isinstance(e, httpx.TimeoutException):
        return "request timed out"
    if isinstance(e, httpx.ConnectError):
        return "connection failed"
    if isinstance(e, httpx.HTTPStatusError):
        return "AI backend returned an error"
    if isinstance(e, httpx.HTTPError):
        # Any other httpx transport failure -- still network-shaped, str(e)
        # may still carry the URL, so it gets the same generic treatment.
        return "connection failed"
    if isinstance(e, AiBedCheckError):
        # Raised internally with an already-generic, pre-selected message
        # (see the raise sites above) -- safe to return directly, never a raw
        # upstream string.
        return str(e)
    # Anything else (e.g. a DB error surfaced from _load_ai_settings) -- never
    # echo str(e); it may contain a connection string or file path.
    return "AI backend unavailable"


async def check_bed_ai(printer_id: int, image_data: bytes, camera_source: str) -> PlateDetectionResult:
    """Public entry for backend='ai'. Catches every failure and fails open (is_empty=True)."""
    try:
        is_empty, confidence, reason = await _analyze_frame_ai(image_data, printer_id)
    except Exception as e:
        logger.warning("AI bed-check failed for printer %s: %s", printer_id, e)
        return PlateDetectionResult(
            is_empty=True,
            confidence=0.0,
            difference_percent=0.0,
            needs_calibration=False,
            message=f"[{camera_source}] AI bed-check unavailable: {_generic_fail_open_reason(e)}",
            backend="ai",
        )
    return build_ai_result(is_empty, confidence, reason, camera_source)


def _synthetic_test_frame() -> bytes:
    """64x64 flat-gray JPEG, generated in-memory -- no real printer, no calibration refs."""
    img = Image.new("RGB", (64, 64), (128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def test_connection(base_url: str, model: str, api_key: str = "") -> dict:
    """Send one synthetic frame through the real verdict pipeline. Never raises.

    Returns {ok, error, latency_ms, verdict}. No `timeout` parameter -- always
    DEFAULT_TIMEOUT. Applies assert_safe_lan_service_url before any
    request, mirroring obico_detection.py:420-425's own re-assertion at
    request time -- the stored setting is validated at save time by the
    Pydantic field_validator, but a URL accepted fresh in a request body needs
    the same guard applied again or it's trivially bypassed. `error` is a
    short, generic message, never a raw echo of the upstream response body
    (matching test_opaque_failure_does_not_return_the_response_body's norm).
    """
    from backend.app.api.routes._url_safety import assert_safe_lan_service_url

    if not base_url or not model:
        return {"ok": False, "error": "Base URL and model are required", "latency_ms": None, "verdict": None}

    try:
        assert_safe_lan_service_url(base_url, label="AI Bed-Check Base URL")
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "latency_ms": None, "verdict": None}

    start = time.monotonic()
    try:
        image_data = _synthetic_test_frame()
        messages = _build_messages(image_data)
        raw = await _post_chat(base_url, model, api_key, messages)
        data = _parse_verdict_json(raw)
    except Exception as e:
        logger.warning("AI bed-check test-connection failed: %s", e)
        return {"ok": False, "error": _generic_fail_open_reason(e), "latency_ms": None, "verdict": None}

    latency_ms = round((time.monotonic() - start) * 1000)
    verdict = {
        "is_empty": bool(data["is_empty"]),
        "confidence": _clamp_confidence(data.get("confidence")),
        "reason": str(data.get("reason") or "").strip(),
    }
    return {"ok": True, "error": None, "latency_ms": latency_ms, "verdict": verdict}
