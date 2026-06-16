"""Env-flagged wire-payload dump for VP MQTT debug (gated; off by default).

Set ``BAMBUDDY_VP_DUMP_WIRE=1`` to enable two complementary capture modes:

1. ``dump_wire``: most recent inbound (bridge cache input) and outbound
   (slicer-facing 1Hz push) MQTT payloads, one file per VP per direction,
   overwritten each tick. Triages shape-of-payload bugs (e.g. #1622 round 1)
   where the question is "is the bridge missing fields in the cache, or is
   something else stripping them on the way out to the slicer?" Compare
   ``*_in.json`` and ``*_out.json`` for the failing VP against a known-good
   one (e.g. H2D vs P1S).

2. ``append_event``: time-ordered JSONL log of every slicer↔bridge↔printer
   command payload that flows through the VP (excludes the cached-as-base
   1Hz push, which dump_wire already covers). Triages command-flow bugs
   (e.g. #1622 round 2 / round 3) where the cached state looks right but a
   slicer-initiated write (ams_filament_setting / extrusion_cali_set /
   xcam / system) ends up corrupting state, or where the slicer's choice
   of command flow depends on what the bridge replies to its initial
   info.get_version / pushall probe. One line per event with wall-clock
   timestamp.

Layout:
- snapshot: ``<log_dir>/vp_wire/<sanitized_vp_name>_<direction>.json``
- events:   ``<log_dir>/vp_wire/<sanitized_vp_name>_cmd.jsonl``

Failure modes are swallowed at debug level — debug instrumentation must
never break the bridge or slicer-facing 1Hz loop. Disable by unsetting the
env var; the in-progress files stay on disk and can be deleted manually.
``_cmd.jsonl`` appends forever while enabled; for long debug sessions,
delete between captures rather than relying on rotation.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

from backend.app.core.config import settings as app_settings

logger = logging.getLogger(__name__)

_ENV_FLAG = "BAMBUDDY_VP_DUMP_WIRE"
_NAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _enabled() -> bool:
    return os.environ.get(_ENV_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def _sanitize(name: str) -> str:
    safe = _NAME_SAFE.sub("_", name or "vp").strip("_")
    return safe or "vp"


def dump_wire(vp_name: str, direction: str, payload: dict | bytes | str) -> None:
    """Write ``payload`` to ``<log_dir>/vp_wire/<vp_name>_<direction>.json``.

    No-op when the env flag is unset. Accepts dict (json-encoded with
    ``indent=2``), bytes (decoded as utf-8 with errors='replace'), or
    str (written verbatim).
    """
    if not _enabled():
        return
    try:
        target_dir = app_settings.log_dir / "vp_wire"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{_sanitize(vp_name)}_{_sanitize(direction)}.json"
        if isinstance(payload, dict):
            text = json.dumps(payload, indent=2, default=str)
        elif isinstance(payload, bytes):
            text = payload.decode("utf-8", errors="replace")
        else:
            text = str(payload)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        logger.debug("[%s] vp_wire dump (%s) failed: %s", vp_name, direction, e)


def _command_label(payload: dict) -> str:
    """Best-effort one-word label for the command, used as a grep handle in the JSONL.

    Bambu's MQTT request/response shape is ``{"<channel>": {"command": "<name>", ...}}``
    where channel is ``print``/``pushing``/``info``/``system``/``xcam``/etc.
    Returns ``"<channel>.<command>"`` when we can find it, ``"?"`` otherwise.
    """
    if not isinstance(payload, dict):
        return "?"
    for channel, body in payload.items():
        if isinstance(body, dict):
            cmd = body.get("command")
            if isinstance(cmd, str) and cmd:
                return f"{channel}.{cmd}"
    return "?"


def append_event(vp_name: str, direction: str, topic: str, payload: dict | bytes | str) -> None:
    """Append one event line to ``<log_dir>/vp_wire/<vp_name>_cmd.jsonl``.

    No-op when the env flag is unset. ``direction`` should be one of
    ``"slicer_to_bridge"`` (slicer-originated publish reaching the bridge),
    ``"printer_to_slicer"`` (real-printer response fanned out to the slicer),
    or ``"bridge_to_slicer"`` (bridge-synthesised reply: info.get_version
    answer, project_file ack, on-demand pushall response). A diff between
    a working VP and a broken VP can then be read top-to-bottom in causal
    order. Bytes payloads are utf-8 decoded then json-parsed best-effort;
    un-parseable payloads are logged as ``{"raw": "<text>"}`` so the line
    is still valid JSON.
    """
    if not _enabled():
        return
    try:
        target_dir = app_settings.log_dir / "vp_wire"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{_sanitize(vp_name)}_cmd.jsonl"

        if isinstance(payload, bytes):
            try:
                parsed: dict | str = json.loads(payload.decode("utf-8", errors="replace").rstrip("\x00 \r\n\t"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                parsed = {"raw": payload.decode("utf-8", errors="replace")}
        elif isinstance(payload, str):
            try:
                parsed = json.loads(payload.rstrip("\x00 \r\n\t"))
            except json.JSONDecodeError:
                parsed = {"raw": payload}
        else:
            parsed = payload

        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "dir": direction,
            "topic": topic,
            "cmd": _command_label(parsed) if isinstance(parsed, dict) else "?",
            "payload": parsed,
        }
        line = json.dumps(record, default=str) + "\n"
        with path.open("a", encoding="utf-8") as fp:
            fp.write(line)
    except OSError as e:
        logger.debug("[%s] vp_wire append (%s) failed: %s", vp_name, direction, e)
