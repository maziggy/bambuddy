"""Shared reading of the firmware's own AMS drying state.

Kept as a leaf module on purpose. ``drying_preflight`` would be the natural
home, but it imports ``printer_manager``, which imports ``bambu_mqtt`` — and
``bambu_mqtt`` is one of the callers here, so putting these there would close an
import cycle. Nothing in this module imports from the app.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

# ``dry_status`` is bits 4-7 of the per-AMS ``info`` hex string (BambuStudio
# DevFilaSystem.cpp): 0=Off, 1=Checking, 2=Drying, 3=Cooling, 4=Stopping,
# 5=Error, 6=HeatOutOfControl, 7=PrdTesting. Only the first three mean a cycle
# is still live.
#
# 4 (Stopping) and 5 (Error) are excluded because the cycle is over or ending.
# 6 (HeatOutOfControl) is excluded deliberately and for a different reason: an
# AMS that has lost thermal control is exactly when a high-temperature alarm
# should still reach the user, so it must never read as "expected heat".
ACTIVE_DRY_STATUSES = frozenset({1, 2, 3})  # Checking, Drying, Cooling


def is_drying_active(ams_data: Any) -> bool:
    """True when this AMS unit reports a drying cycle in progress.

    Two independent signals, because neither alone is sufficient. ``dry_time``
    is minutes remaining and reads 0 through the cooling phase that closes a
    cycle; ``dry_status`` covers that phase but is only present when the
    firmware sent a parseable ``info`` field.
    """
    if not isinstance(ams_data, Mapping):
        return False
    try:
        if int(ams_data.get("dry_time") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass  # Unparseable countdown — fall through to the phase field
    try:
        return int(ams_data["dry_status"]) in ACTIVE_DRY_STATUSES
    except (KeyError, TypeError, ValueError):
        return False


def temperature_alarm_suppressed(
    *,
    drying_active: bool,
    temperature: float | None,
    threshold: float,
    latched_at: datetime | None,
    now: datetime,
    grace_minutes: int,
) -> tuple[bool, datetime | None]:
    """Decide whether to hold back the AMS high-temperature alarm (#1802).

    Drying heats an AMS far past the alarm threshold by design — 45 C for PLA,
    65 C for PETG, up to 85 C on an AMS-HT, against a default threshold of
    35 C — so without this the alarm fires once an hour for the length of the
    cycle and keeps going while the unit cools back down.

    Returns ``(suppress, latched_at)``. The second element is the latch to
    persist: a timestamp while suppression is in force, ``None`` to clear it.

    Suppression is released as soon as the unit reads back at or below the
    threshold rather than after a fixed delay, so a 65 C cycle in a cold
    basement and a 45 C one in a warm room each get exactly the cool-down they
    need. ``grace_minutes`` only bounds the case where the unit never returns
    below the threshold at all — and a unit that stays that hot would have been
    alarming with no drying involved, so releasing there restores the ordinary
    behaviour instead of inventing a new alert.
    """
    if drying_active:
        return True, now
    if latched_at is None:
        return False, None
    # Back at a normal storage temperature: the cool-down is over. Note this is
    # also the only path that can clear the latch promptly, so it is checked
    # before the cap.
    if temperature is not None and temperature <= threshold:
        return False, None
    # ``latched_at`` is never in the future: the caller either just stamped it
    # with this ``now`` or read it back through a loader that clamps. A future
    # stamp would make this difference negative and hold suppression for the
    # skew on top of the cap, which is why the clamp lives at the read.
    if now - latched_at >= timedelta(minutes=grace_minutes):
        return False, None
    return True, latched_at
