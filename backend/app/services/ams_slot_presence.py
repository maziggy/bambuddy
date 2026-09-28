"""Is there a spool in this AMS slot?

Three backend decisions turn on the answer -- whether to push
``ams_filament_setting`` when a spool is assigned, whether a pre-assigned slot
has just been filled, and whether an assignment has gone stale -- and all three
used to read it off the tray's ``state`` field. That field cannot carry it.

## Why ``state`` is the wrong source

``state`` is firmware-variant. The A1 Mini BMCU and the P1S Standard AMS report
3 for a loaded slot and never emit 11; the AMS-HT's codes differ again (#2670).
Bambuddy already works around that on the printer card, where
``getEmptySlotKind`` (``PrintersPage.tsx``) reads the presence bit first and
only falls back to the 9/10 heuristic when there is no bit to read.

Worse, ``state`` is partly Bambuddy's own writing. ``apply_tray_exist_bits``
sets ``state = 9`` on every slot whose presence bit is 0 -- and when the bit
comes back it leaves the 9 exactly where it was, because the "slot occupied"
branch only annotates ``exists`` and moves on. So a slot the firmware says is
full can sit in the cache reading ``exists=True, state=9`` indefinitely. That
is #3084: a non-Bambu spool is swapped in, Assign Spool reads the stale 9,
calls the slot empty, sends no MQTT, and the printer keeps showing ``?``. The
deferred-configuration replay could not rescue it either, because its own
"loaded" test was the same 9/10 heuristic -- the exact deadlock #1322 removed
elsewhere. #3100 is the same stale 9 one step further on: the replay does not
fire, the assignment keeps the empty fingerprint it was stored with, and the
first real tray report is read as a spool swap and deleted.

## What this module answers

``tray_exist_bits`` is the firmware's own "which slots have a spool" bitmask --
the one BambuStudio draws its ``?`` from -- and ``apply_tray_exist_bits``
records it per tray as ``exists``. That bit is authoritative where it exists,
and absent otherwise; it is never a guess. Callers that want a decision for a
payload carrying no bit at all keep their own fallback, because the right
fallback differs per caller: the assign path wants to know whether the push is
doomed, the unlink pass wants to know whether a spool was removed, and a state
of 26 ("unloaded", mid-runout) answers those two questions differently.
"""

from collections.abc import Mapping
from typing import Any


def spool_present(tray: Mapping[str, Any] | None) -> bool | None:
    """Does firmware's presence bit say a spool is in this slot?

    ``True`` / ``False`` straight from ``tray_exist_bits``; ``None`` when the
    tray carries no presence annotation, which means the caller has to decide
    on its own terms rather than assume either way.

    Only the internal AMS path annotates ``exists`` (``apply_tray_exist_bits``
    is called with ``annotate_exists=True`` there and False for the VP bridge),
    so the external spool's ``vt_tray`` entries answer ``None`` -- they have no
    bit in the mask.
    """
    if not isinstance(tray, Mapping):
        return None
    exists = tray.get("exists")
    return exists if isinstance(exists, bool) else None
