"""
Small readers for appliance-set state files.

Two distinct surfaces, same shape (defensive, silent on missing files,
side-effect-free):

- ``read_local_toml`` reads ``/etc/bambuddy/local.toml`` (the file the
  appliance setup wizard writes during firstboot with the user's hostname,
  timezone, and locale).
- ``read_ntp_gate`` reads ``/run/bambuddy/time-synced`` (the appliance's
  ntp-gate.sh signals time-sync state here once chrony reports sync, or
  when the 3-minute timeout elapses with a "warning" marker).

Universal across install shapes:

- On the Bambuddy Appliance: both files exist by the time bambuddy.service
  starts; we surface their values to the frontend.
- On Docker / manual installs: both files are absent; we degrade silently.

These readers are read-only and side-effect-free. They do NOT call
hostnamectl / timedatectl / chronyc — system-state changes are the
appliance's firstboot.sh responsibility (root, runs before this process
exists). Here we just expose state so the frontend can render accordingly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, TypedDict

import tomllib

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("/etc/bambuddy/local.toml")
DEFAULT_NTP_GATE_PATH = Path("/run/bambuddy/time-synced")

# Three states: synced ("ok"), gated-and-timed-out ("warning"), or unknown (None).
TimeSyncState = Literal["ok", "warning"] | None


class LocalConfig(TypedDict, total=False):
    hostname: str
    timezone: str
    locale: str


def read_local_toml(path: Path = DEFAULT_PATH) -> LocalConfig:
    """Read the appliance local.toml. Missing / invalid file returns empty dict.

    Only the keys actually present in the file are returned — the caller checks
    `if "locale" in config:` rather than relying on defaults. Non-string values
    are dropped with a warning to keep this defensive on a hand-edited file.
    """
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("local.toml at %s could not be parsed: %s", path, exc)
        return {}

    result: LocalConfig = {}
    for key in ("hostname", "timezone", "locale"):
        value = data.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            log.warning("local.toml: %r is %s, expected str — ignoring", key, type(value).__name__)
            continue
        result[key] = value  # type: ignore[literal-required]
    return result


def read_ntp_gate(path: Path = DEFAULT_NTP_GATE_PATH) -> TimeSyncState:
    """Read the appliance NTP gate file. Returns "ok", "warning", or None.

    Wire contract with bambuddy-appliance/firstboot/ntp-gate.sh:
      - File absent: gate hasn't been evaluated yet, or this isn't an appliance
        install. Caller should treat as "unknown / don't gate."
      - File content starts with "ok": chrony reported sync within 3 minutes.
      - File content starts with "warning": 3-minute timeout elapsed without
        sync. The user has already waited and the wizard proceeded with a
        degraded clock — auth tokens may have incorrect expiry, TLS certs may
        fail validation. UI should surface this.
      - Anything else: defensive fall-through to None.
    """
    try:
        body = path.read_text(errors="replace").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("ntp-gate file at %s could not be read: %s", path, exc)
        return None

    if body.startswith("ok"):
        return "ok"
    if body.startswith("warning"):
        return "warning"
    return None
