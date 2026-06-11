"""Env-flagged wire-payload dump for VP MQTT debug (gated; off by default).

Set ``BAMBUDDY_VP_DUMP_WIRE=1`` to write the most recent inbound (bridge
cache input) and outbound (slicer-facing 1Hz push) MQTT payloads to disk,
one file per VP per direction, overwritten each tick.

Used to triage shape-of-payload bugs (e.g. #1622) where the question is
"is the bridge missing fields in the cache, or is something else stripping
them on the way out to the slicer?" Compare ``*_in.json`` and ``*_out.json``
for the failing VP against a known-good VP (e.g. H2D vs P1S).

Layout: ``<log_dir>/vp_wire/<sanitized_vp_name>_<direction>.json``

Failure modes are swallowed at debug level — debug instrumentation must
never break the bridge or slicer-facing 1Hz loop. Disable by unsetting the
env var; the in-progress files stay on disk and can be deleted manually.
"""

from __future__ import annotations

import json
import logging
import os
import re

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
