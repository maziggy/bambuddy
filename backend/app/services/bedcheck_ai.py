"""AI-backed build-plate occupancy check (selectable alternative to the OpenCV backend).

Sends one downscaled camera snapshot to a configured OpenAI-compatible vision
model (chat/completions, structured JSON output) and maps the verdict back
onto PlateDetectionResult. Off by default (bedcheck_backend='opencv'); fails
open (is_empty=True) on any error -- see check_bed_ai().
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import re
import time
from base64 import b64encode
from datetime import datetime, timezone

import httpx
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from backend.app.core.database import async_session
from backend.app.models.settings import Settings
from backend.app.services.plate_detection import PlateDetectionResult

logger = logging.getLogger(__name__)

DOWNSCALE_MAX_EDGE = 768
# Module constant, not a user setting. Sized for a cold local-model case
# with margin. This ceiling only governs the uncapped manual-check and
# test-connection paths -- the print-start path never waits this long
# regardless, since it's bounded by PRINT_START_DEADLINE_SECONDS below and
# primed ahead of time by warmup().
DEFAULT_TIMEOUT = 60.0

# Upper bound on the TCP-connect phase of any request this module makes (see
# _post_chat). Connecting to a reachable OpenAI-compatible service on the LAN
# is sub-millisecond; anything slower is a wrong/black-holed address, and
# waiting the full read budget for it only delays the fail-open.
CONNECT_TIMEOUT_CAP = 5.0

# Per-request ceiling for test_connection()'s 2nd and 3rd (shape-discovery
# retry) requests. The first probe gets the full DEFAULT_TIMEOUT because it is
# the one that may pay a model cold start; by the time a retry is issued the
# backend has already answered once, so a hung retry is a fault, not a warmup.
# Without this a wedged endpoint could hold the settings UI for 3 x 60s.
DISCOVERY_RETRY_TIMEOUT = 20.0

# Total wall-clock budget (camera capture + downscale + request, no parse
# retry) for the AI part of the print-start safety check -- see
# plate_detection.check_plate_empty(deadline_seconds=...). Kept
# well under DEFAULT_TIMEOUT so a stuck AI backend can't stall print start.
# Sized from measured cold starts of qwen2.5vl:7b on an RTX 3090 via Ollama
# (2026-09-10, full-size frame): 8.8-12.4s from an empty GPU, 11.1s when a
# 24GB model had to be evicted first; warm calls 2.9-3.8s. ~1.6x the worst
# observed cold start. A slower or busier backend that misses this budget
# fails open with outcome="unavailable" (visible in the UI + notified), not
# silently.
PRINT_START_DEADLINE_SECONDS = 20.0

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

# Default chat/completions request shape -- max_tokens + a json_schema
# response_format. Some OpenAI-compatible backends (notably certain reasoning
# models) reject one or both of these; test_connection() discovers the
# working shape for a given (base_url, model, api_key) and caches it here so
# the print/manual paths never have to probe (see _post_chat + item 4 in the
# module's PR review response).
_DEFAULT_SHAPE = {"token_param": "max_tokens", "response_mode": "json_schema"}

# Keyed on (base_url, model, sha256(api_key)[:12]) -- the key hash avoids
# storing the raw key in a long-lived in-process cache while still keying on
# it (a key rotation on the same URL/model must not reuse a stale shape).
_shape_cache: dict[tuple[str, str, str], dict] = {}

# Per-printer health snapshot from the most recent check_bed_ai() call (both
# success and fail-open update this) -- see get_health() and
# routes/bedcheck_ai.py's GET /health.
_last_outcome: dict[int, dict] = {}

# Monotonic timestamp of the last "AI bed-check unavailable" notification
# sent per printer -- see _maybe_notify_unavailable_transition().
_last_unavailable_notified_at: dict[int, float] = {}
_UNAVAILABLE_NOTIFY_COOLDOWN_SECONDS = 3600.0


def _shape_cache_key(base_url: str, model: str, api_key: str) -> tuple[str, str, str]:
    """Normalise here, not only at the _load_ai_settings() read site.

    routes/bedcheck_ai.py's POST /test-connection passes the RAW request body
    straight through to test_connection(), while the print/manual path reads
    the *stored* settings back through _load_ai_settings(), which strips and
    rstrips them. A saved base_url of "http://host/v1/" (perfectly legal per
    the URL validator) would otherwise be keyed as ".../v1/" by discovery and
    looked up as ".../v1" by the print path -- discovery silently lost, every
    real check falling back to the default shape and 400ing while
    test-connection cheerfully reported ok. Normalising in the key function
    itself makes the two paths agree no matter which spelling reaches it.
    """
    normalized_url = (base_url or "").strip().rstrip("/")
    normalized_model = (model or "").strip()
    key_hash = hashlib.sha256((api_key or "").strip().encode("utf-8")).hexdigest()[:12]
    return (normalized_url, normalized_model, key_hash)


def _get_shape(base_url: str, model: str, api_key: str) -> dict:
    """Cached request shape for this (base_url, model, api_key), or the
    default (max_tokens + json_schema) if discovery has never run for it."""
    return _shape_cache.get(_shape_cache_key(base_url, model, api_key), _DEFAULT_SHAPE)


def get_health() -> dict[int, dict]:
    """Public accessor for the per-printer health snapshot -- routes/bedcheck_ai.py's
    GET /health reads this.

    Returns a copy at both levels (the mapping and each per-printer entry), so
    a caller that mutates what it gets back -- a route enriching the payload,
    a test -- cannot corrupt the registry that drives the ok/degraded ->
    unavailable transition logic.
    """
    return {printer_id: dict(entry) for printer_id, entry in _last_outcome.items()}


def reset_health_state() -> None:
    """Drop every per-printer health entry and its notification rate-limit
    state. Called from routes/settings.py when the AI bed-check connection
    settings actually change: the recorded outcomes describe the *old*
    backend, so leaving them in place would show a stale "unavailable" badge
    for a URL that is no longer configured, and -- worse -- leave
    _last_unavailable_notified_at armed, suppressing the first genuine
    failure notification from the new backend for up to an hour.
    """
    _last_outcome.clear()
    _last_unavailable_notified_at.clear()


def _record_outcome(printer_id: int, outcome: str, reason: str | None, request_mode: str) -> None:
    _last_outcome[printer_id] = {
        "outcome": outcome,
        "reason": reason,
        "at": datetime.now(timezone.utc).isoformat(),
        "request_mode": request_mode,
    }


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
    if not isinstance(data, dict) or not isinstance(data.get("is_empty"), bool):
        return False
    confidence = data.get("confidence")
    return (
        isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and math.isfinite(confidence)
        and isinstance(data.get("reason"), str)
    )


def _parse_verdict_json(raw: str) -> dict:
    """Parse a verdict JSON object out of a raw model response.

    Tries a clean json.loads first, then falls back to extracting the first
    {...} block (handles models that wrap JSON in markdown fences despite
    instructions). Raises AiBedCheckError("invalid response from AI backend")
    if neither parses, or the parsed object has an invalid required field
    (boolean verdict, finite numeric confidence, string reason). The caller
    treats this identically to a parse failure and retries once.
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


async def _post_chat(
    base_url: str,
    model: str,
    api_key: str,
    messages: list[dict],
    timeout: float = DEFAULT_TIMEOUT,
    shape: dict | None = None,
) -> str:
    """POST one chat/completions request, return the raw message content string.

    Network/timeout/non-2xx failures are raised unwrapped (httpx exception
    types) -- the caller does not retry these. A malformed 200 envelope
    (missing choices, content-less message, an {"error": ...} body returned
    with status 200) is raised as AiBedCheckError("invalid response from AI
    backend") -- treated as a parse failure by the caller, so the one retry
    still applies.

    `shape` selects the token-limit param name and response_format mode
    ({"token_param": "max_tokens"|"max_completion_tokens", "response_mode":
    "json_schema"|"json_object"}). Callers on the print/manual path pass
    nothing -- the cached shape for (base_url, model, api_key) is looked up
    here (defaulting to max_tokens + json_schema). test_connection()'s
    discovery loop passes an explicit candidate shape instead, so probing
    never mutates the cache until it finds one that works (see _shape_cache).
    """
    if shape is None:
        shape = _get_shape(base_url, model, api_key)
    payload = {
        "model": model,
        "temperature": 0,
        shape["token_param"]: 300,
        "messages": messages,
        "response_format": (
            {"type": "json_schema", "json_schema": VERDICT_JSON_SCHEMA}
            if shape["response_mode"] == "json_schema"
            # SYSTEM_PROMPT already demands "nothing but the JSON object" and
            # spells out the exact schema in prose -- that satisfies OpenAI's
            # json_object-mode rule that the prompt must mention JSON, so no
            # separate schema text needs to be injected here.
            else {"type": "json_object"}
        ),
    }
    # httpx applies a bare float timeout to EACH of connect/read/write/pool
    # independently, so a `timeout` meant as a wall-clock budget is really a
    # 4x worst case. Spell the phases out so no single phase may exceed it,
    # and connect -- which on a reachable LAN service is sub-millisecond --
    # is capped far tighter so a black-holed host fails fast instead of
    # burning the whole budget before a byte is sent.
    #
    # This alone is still NOT a total-call bound: httpx's read timeout is
    # idle-time-since-the-last-byte, not a ceiling on the call as a whole, so
    # a backend that trickles a byte or two just often enough to keep
    # resetting it could run this request for multiples of `timeout` before
    # a plain httpx.Timeout ever fires. The deadline path (see
    # _analyze_frame_ai) additionally wraps its call to this function in
    # asyncio.wait_for(), which enforces a real wall-clock ceiling regardless
    # of read-idle resets -- that is what makes deadline_seconds an actual
    # TOTAL budget rather than merely a per-phase one.
    timeout_config = httpx.Timeout(timeout, connect=min(timeout, CONNECT_TIMEOUT_CAP))
    async with httpx.AsyncClient(timeout=timeout_config) as client:
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
        "base_url": (rows.get("bedcheck_ai_base_url") or "").strip().rstrip("/"),
        "model": (rows.get("bedcheck_ai_model") or "").strip(),
        "api_key": (rows.get("bedcheck_ai_api_key") or "").strip(),
    }


async def load_ai_settings() -> dict:
    """Public accessor for the 3 AI-backend connection keys.

    Thin wrapper around _load_ai_settings() -- routes/bedcheck_ai.py used to
    reach into that name directly despite the leading underscore; this gives
    it a real public entry point instead. _load_ai_settings() keeps its name
    and stays the actual implementation so the existing unit tests that patch
    it (test_ai_bed_check.py's _patch_settings()) keep working unmodified.
    """
    return await _load_ai_settings()


async def _analyze_frame_ai(
    image_data: bytes, printer_id: int, deadline_seconds: float | None = None, shape_out: dict | None = None
) -> tuple[bool, float, str, str]:
    """The raising primitive: (is_empty, confidence, reason, request_mode), or
    raises AiBedCheckError.

    printer_id is accepted but currently unused -- kept on the signature so
    callers don't need to change if a per-printer variant of this check is
    ever added.

    deadline_seconds, when given, is a TOTAL wall-clock budget for this whole
    call (downscale + request), measured from a time.monotonic() start here --
    NOT a per-request timeout. httpx's timeout is derived as the remaining
    budget at the moment each request is issued, AND (on this path only) the
    request is additionally wrapped in asyncio.wait_for() against that same
    remaining budget -- httpx's read timeout only measures idle time between
    bytes, so a backend that drip-feeds data fast enough to keep resetting it
    could otherwise run well past the promised total; wait_for is what
    actually makes "TOTAL" true regardless of that. The one parse/schema
    retry is also skipped entirely on this path: doubling latency on an
    already tight safety-path budget is the wrong trade, even though the
    retry is worth keeping on the diagnostic (manual-check/test-connection)
    path where deadline_seconds is None.

    shape_out, when given, is filled in with {"response_mode": ...} as soon as
    the request shape is resolved -- before any network I/O. It exists so
    check_bed_ai()'s failure path can label the health entry with the shape
    that was actually in play without a second settings read (an unbounded DB
    round trip) on a deadline-bounded print-start path.
    """
    start = time.monotonic()
    cfg = await _load_ai_settings()
    if not cfg["base_url"] or not cfg["model"]:
        # Both empty base_url and empty model short-circuit with no network
        # call attempted.
        raise AiBedCheckError("AI backend not configured")

    shape = _get_shape(cfg["base_url"], cfg["model"], cfg["api_key"])
    if shape_out is not None:
        shape_out["response_mode"] = shape["response_mode"]
    messages = _build_messages(image_data)

    def _timeout() -> float:
        if deadline_seconds is None:
            return DEFAULT_TIMEOUT
        remaining = deadline_seconds - (time.monotonic() - start)
        if remaining <= 0:
            # Budget already spent (a slow Pillow downscale of a large frame,
            # or a first request that ate all of it). httpx would happily
            # accept 0.0 here -- it is not a ValueError -- but issuing a
            # request that cannot possibly complete still costs a socket, a
            # connection attempt and a guaranteed timeout, all of it past the
            # deadline the caller was promised. Fail the deadline explicitly.
            raise AiBedCheckError("deadline exceeded")
        return remaining

    try:
        if deadline_seconds is not None:
            # Resolve the remaining budget exactly once and reuse it for both
            # the httpx per-phase config and the wait_for ceiling below -- two
            # separate _timeout() calls would each re-measure elapsed time
            # (and re-raise "deadline exceeded" independently), needlessly
            # letting them disagree by however long falls between them.
            #
            # See _post_chat's timeout_config comment: a bare httpx.Timeout
            # bounds each phase but not the call as a whole (read timeout is
            # idle-time, not total). wait_for against that same budget is the
            # actual aggregate wall-clock bound -- if it fires,
            # asyncio.TimeoutError propagates unwrapped and is classified by
            # _generic_fail_open_reason exactly like an httpx timeout.
            request_timeout = _timeout()
            raw = await asyncio.wait_for(
                _post_chat(
                    cfg["base_url"], cfg["model"], cfg["api_key"], messages, timeout=request_timeout, shape=shape
                ),
                timeout=request_timeout,
            )
        else:
            raw = await _post_chat(
                cfg["base_url"], cfg["model"], cfg["api_key"], messages, timeout=_timeout(), shape=shape
            )
        data = _parse_verdict_json(raw)
    except AiBedCheckError:
        if deadline_seconds is not None:
            raise
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
        raw = await _post_chat(
            cfg["base_url"], cfg["model"], cfg["api_key"], retry_messages, timeout=_timeout(), shape=shape
        )
        data = _parse_verdict_json(raw)

    is_empty = bool(data["is_empty"])
    confidence = _clamp_confidence(data.get("confidence"))
    reason = str(data.get("reason") or "").strip()
    return is_empty, confidence, reason, shape["response_mode"]


def build_ai_result(
    is_empty: bool, confidence: float, reason: str, camera_source: str, outcome: str = "ok"
) -> PlateDetectionResult:
    """Pure formatter, no I/O -- maps a verdict onto the shared PlateDetectionResult shape.

    difference_percent is a pixel-diff concept the AI backend has no
    equivalent of -- always None here. ai_confidence carries the model's own
    confidence instead (confidence also still carries it, for the
    already-existing callers that read that field regardless of backend).
    """
    status = "Plate appears empty" if is_empty else "Objects detected on plate"
    suffix = f": {reason}" if reason else ""
    message = f"[{camera_source}] {status} (AI, confidence {confidence:.0%}){suffix}"
    return PlateDetectionResult(
        is_empty=is_empty,
        confidence=confidence,
        difference_percent=None,
        ai_confidence=confidence,
        message=message,
        # Always False -- the AI backend has no calibration reference to be
        # missing, and this preserves main.py's existing needs_calibration
        # pause-gate behavior unchanged.
        needs_calibration=False,
        backend="ai",
        ai_reason=reason or None,
        outcome=outcome,
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
    if isinstance(e, (httpx.TimeoutException, asyncio.TimeoutError)):
        # asyncio.TimeoutError is what asyncio.wait_for() raises when the
        # deadline path's aggregate wall-clock bound (see
        # _analyze_frame_ai) fires -- classified identically to a plain
        # httpx per-phase timeout so the user-facing reason doesn't depend
        # on which of the two guards actually caught the slow backend.
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


# Strong references to in-flight notification tasks. asyncio only holds a weak
# reference to a running task, so a create_task() result that nobody keeps can
# be garbage-collected mid-await and the notification silently vanishes; the
# done-callback discards the entry again so this never grows.
_pending_notify_tasks: set[asyncio.Task] = set()


def _dispatch_unavailable_transition(printer_id: int, previous_outcome: str | None, reason: str) -> None:
    """Schedule the unavailable-transition notification WITHOUT awaiting it.

    check_bed_ai()'s failure path runs inside the print-start deadline budget,
    and _maybe_notify_unavailable_transition() opens a DB session and then
    awaits every configured provider's outbound HTTP send -- an unbounded
    amount of work that must not sit between a failed bed check and the print
    that is waiting on its verdict. Scheduling it detaches that cost onto the
    event loop.
    """
    # Bound to a name before the try so the RuntimeError branch can close it:
    # asyncio.create_task() constructs this coroutine object as its argument
    # BEFORE calling asyncio.get_running_loop() to schedule it, so a
    # RuntimeError there (no running loop) still leaves a real,
    # never-awaited coroutine behind. Left unclosed, Python warns
    # "coroutine '...' was never awaited" the next time the GC collects it.
    coro = _maybe_notify_unavailable_transition(printer_id, previous_outcome, reason)
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:
        # No running loop (only reachable from sync test/CLI contexts) -- the
        # notification is a nicety, never a reason to fail the check.
        coro.close()
        logger.debug("No running event loop; skipping AI bed-check unavailable notification for %s", printer_id)
        return
    _pending_notify_tasks.add(task)
    task.add_done_callback(_pending_notify_tasks.discard)


async def _maybe_notify_unavailable_transition(printer_id: int, previous_outcome: str | None, reason: str) -> None:
    """Send an "AI bed-check unavailable" notification on any transition INTO
    "unavailable" (from ok, degraded, or no prior entry -- only an
    already-"unavailable" previous outcome suppresses), rate-limited to at
    most one per printer per
    _UNAVAILABLE_NOTIFY_COOLDOWN_SECONDS. Reuses the printer_error event
    (same notification_service mechanism main.py's plate-check pause path
    uses) instead of introducing a new event type: a new event needs a new
    per-provider toggle column + migration + settings-UI checkbox, none of
    which exist yet, so providers would never see it; printer_error's toggle
    already exists and is already the general "something's wrong with this
    printer" channel. Never raises into check_bed_ai -- a notification bug
    must not turn a fail-open plate check into a hard failure.
    """
    try:
        # Suppress only a repeat of an already-unavailable state. "degraded"
        # must NOT suppress: a backend that needs the json_object fallback
        # records "degraded" on every single successful check, so treating it
        # as a non-transition would mean such an install never gets notified
        # when its AI bed-check actually dies.
        if previous_outcome == "unavailable":
            return
        now = time.monotonic()
        last_sent = _last_unavailable_notified_at.get(printer_id)
        if last_sent is not None and (now - last_sent) < _UNAVAILABLE_NOTIFY_COOLDOWN_SECONDS:
            return

        from backend.app.models.printer import Printer
        from backend.app.services.notification_service import notification_service

        async with async_session() as db:
            result = await db.execute(select(Printer).where(Printer.id == printer_id))
            printer = result.scalar_one_or_none()
            printer_name = printer.name if printer else f"Printer {printer_id}"
            await notification_service.on_printer_error(
                printer_id=printer_id,
                printer_name=printer_name,
                error_type="AI Bed-Check Unavailable",
                db=db,
                error_detail=(
                    f"AI bed-check unavailable: {reason}. Plate checks fall back to reporting an "
                    "empty plate until it recovers."
                ),
            )
        _last_unavailable_notified_at[printer_id] = now
    except Exception:
        logger.warning("Failed to send AI bed-check unavailable notification for printer %s", printer_id, exc_info=True)


async def check_bed_ai(
    printer_id: int, image_data: bytes, camera_source: str, deadline_seconds: float | None = None
) -> PlateDetectionResult:
    """Public entry for backend='ai'. Catches every failure and fails open (is_empty=True).

    deadline_seconds is forwarded to _analyze_frame_ai() unchanged -- see its
    docstring. Every call (success or fail-open) records a health-registry
    entry for printer_id (get_health()) and, on any transition INTO
    "unavailable" (from ok, degraded, or no prior entry), schedules a
    rate-limited notification without awaiting it.
    """
    previous_outcome = _last_outcome.get(printer_id, {}).get("outcome")
    # Filled in by _analyze_frame_ai the moment the request shape is resolved,
    # so the failure path can label the health entry without a second (and on
    # the print path, out-of-budget) settings read.
    observed_shape: dict = {}
    try:
        is_empty, confidence, reason, request_mode = await _analyze_frame_ai(
            image_data, printer_id, deadline_seconds, shape_out=observed_shape
        )
    except Exception as e:
        logger.warning("AI bed-check failed for printer %s: %s", printer_id, e)
        reason_str = _generic_fail_open_reason(e)
        # Missing only when the failure happened before the settings load
        # finished, in which case no shape was ever chosen and the default is
        # exactly what the next attempt will use.
        request_mode = observed_shape.get("response_mode", _DEFAULT_SHAPE["response_mode"])
        return unavailable_result(printer_id, camera_source, reason_str, request_mode, previous_outcome)
    # A successful verdict obtained via the json_object fallback shape is
    # real but was reached via a reduced request shape -- surfaced as
    # "degraded" rather than "ok" so the health UI can flag it even though
    # nothing failed open.
    outcome = "degraded" if request_mode == "json_object" else "ok"
    _record_outcome(printer_id, outcome, None, request_mode)
    return build_ai_result(is_empty, confidence, reason, camera_source, outcome=outcome)


def unavailable_result(
    printer_id: int,
    camera_source: str,
    reason: str,
    request_mode: str = "json_schema",
    previous_outcome: str | None = None,
) -> PlateDetectionResult:
    """Record a fail-open result, including failures before model inference.

    Camera capture and the outer print-start deadline can fail before
    check_bed_ai() gets control. They still need the same visible health state
    and unavailable-transition notification as a model or network failure.
    ``reason`` must be a fixed, user-safe message, never raw exception text.
    """
    if previous_outcome is None:
        previous_outcome = _last_outcome.get(printer_id, {}).get("outcome")
    _record_outcome(printer_id, "unavailable", reason, request_mode)
    _dispatch_unavailable_transition(printer_id, previous_outcome, reason)
    return PlateDetectionResult(
        is_empty=True,
        confidence=0.0,
        difference_percent=None,
        ai_confidence=None,
        needs_calibration=False,
        message=f"[{camera_source}] AI bed-check unavailable: {reason}",
        backend="ai",
        outcome="unavailable",
    )


async def warmup() -> None:
    """Fire-and-forget priming request -- sends one synthetic frame through
    the normal message-building/request pipeline (_build_messages +
    _post_chat) so a locally-hosted model is already loaded into VRAM before
    the next print-start check needs it under PRINT_START_DEADLINE_SECONDS.

    Deliberately does NOT go through check_bed_ai(): that would write a
    health-registry entry and could fire the ok->unavailable notification for
    a synthetic, no-real-printer check, which would be actively misleading.
    Swallows every error -- this is best-effort only; a failed warmup just
    means the next real check pays the cold-start cost instead of this one.
    """
    try:
        cfg = await _load_ai_settings()
        if not cfg["base_url"] or not cfg["model"]:
            return
        messages = _build_messages(_synthetic_test_frame())
        await _post_chat(cfg["base_url"], cfg["model"], cfg["api_key"], messages, timeout=DEFAULT_TIMEOUT)
    except Exception as e:
        logger.debug("AI bed-check warmup failed (non-fatal): %s", e)


def _synthetic_test_frame() -> bytes:
    """64x64 flat-gray JPEG, generated in-memory -- no real printer, no calibration refs."""
    img = Image.new("RGB", (64, 64), (128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _classify_shape_error(response: httpx.Response) -> str | None:
    """Classify a 400/422 error body as a request-shape problem, or None if it
    doesn't look like one (a real config/auth/model-name error should not be
    retried with a different shape).

    Prefers the structured `error.param` / `error.code` fields (OpenAI's own
    error shape); falls back to a substring match on `error.message` for
    backends that don't populate those. Returns "token_param" if the error
    names max_tokens, "response_mode" if it names response_format/json_schema,
    else None.

    When one haystack names both dimensions, the one mentioned FIRST by
    position wins rather than a fixed dimension order: real bodies lead with
    the parameter they actually rejected ("response_format json_schema is not
    supported; try max_tokens>=N") and a fixed order would retry the wrong
    dimension first, burning one of the at-most-two discovery retries.
    Applies to 422 exactly as to 400 -- some backends validate the request
    shape as a schema violation rather than a bad request.
    """
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        return None
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None

    structured = f"{error.get('param') or ''} {error.get('code') or ''}".lower()
    message = str(error.get("message") or "").lower()

    needles = (
        ("max_tokens", "token_param"),
        ("response_format", "response_mode"),
        ("json_schema", "response_mode"),
    )
    for haystack in (structured, message):
        hits = [(haystack.find(needle), dimension) for needle, dimension in needles if needle in haystack]
        if hits:
            return min(hits)[1]
    return None


def _adjust_shape(shape: dict, dimension: str) -> dict:
    adjusted = dict(shape)
    if dimension == "token_param":
        adjusted["token_param"] = "max_completion_tokens"
    elif dimension == "response_mode":
        adjusted["response_mode"] = "json_object"
    return adjusted


async def test_connection(base_url: str, model: str, api_key: str = "") -> dict:
    """Send one synthetic frame through the real verdict pipeline. Never raises.

    Returns {ok, error, latency_ms, verdict, request_mode}. No `timeout`
    parameter -- always DEFAULT_TIMEOUT. Applies assert_safe_lan_service_url
    before any request, mirroring obico_detection.py:420-425's own
    re-assertion at request time -- the stored setting is validated at save
    time by the Pydantic field_validator, but a URL accepted fresh in a
    request body needs the same guard applied again or it's trivially
    bypassed. `error` is a short, generic message, never a raw echo of the
    upstream response body (matching
    test_opaque_failure_does_not_return_the_response_body's norm).

    Request-shape discovery (item 4 of the review response) runs ONLY here,
    never on the print/manual path: on a 400/422, _classify_shape_error()
    inspects the body and, if it names a shape dimension not already retried,
    this retries once with the adjusted shape (_adjust_shape()). At most one
    retry per dimension (token_param, response_mode) -- so at most 3 requests
    total. On overall success the shape actually used is written into
    _shape_cache, unconditionally -- this doubles as the recovery path for a
    stale "degraded" cache entry from a backend that has since been fixed.
    """
    from backend.app.api.routes._url_safety import assert_safe_lan_service_url

    if not base_url or not model:
        return {"ok": False, "error": "Base URL and model are required", "latency_ms": None, "verdict": None}

    try:
        assert_safe_lan_service_url(base_url, label="AI Bed-Check Base URL")
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "latency_ms": None, "verdict": None}

    start = time.monotonic()
    cache_key = _shape_cache_key(base_url, model, api_key)
    shape = dict(_shape_cache.get(cache_key, _DEFAULT_SHAPE))
    retried_dimensions: set[str] = set()
    try:
        image_data = _synthetic_test_frame()
        messages = _build_messages(image_data)
        while True:
            try:
                # First probe may pay a model cold start, so it gets the full
                # ceiling; the retries cannot (the backend already answered)
                # and are capped so a wedged endpoint can't hold this for 3x60s.
                request_timeout = (
                    DEFAULT_TIMEOUT if not retried_dimensions else min(DEFAULT_TIMEOUT, DISCOVERY_RETRY_TIMEOUT)
                )
                raw = await _post_chat(base_url, model, api_key, messages, timeout=request_timeout, shape=shape)
                data = _parse_verdict_json(raw)
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in (400, 422):
                    raise
                dimension = _classify_shape_error(e.response)
                if dimension is None or dimension in retried_dimensions:
                    raise
                retried_dimensions.add(dimension)
                shape = _adjust_shape(shape, dimension)
    except Exception as e:
        logger.warning("AI bed-check test-connection failed: %s", e)
        return {"ok": False, "error": _generic_fail_open_reason(e), "latency_ms": None, "verdict": None}

    _shape_cache[cache_key] = shape
    latency_ms = round((time.monotonic() - start) * 1000)
    verdict = {
        "is_empty": bool(data["is_empty"]),
        "confidence": _clamp_confidence(data.get("confidence")),
        "reason": str(data.get("reason") or "").strip(),
    }
    return {
        "ok": True,
        "error": None,
        "latency_ms": latency_ms,
        "verdict": verdict,
        "request_mode": shape["response_mode"],
    }
