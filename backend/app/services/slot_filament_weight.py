"""A slot's remaining weight on the printer, from the inventory (``set_filament_weight``).

Bambu Studio sets it from a slot's material dialog on the models whose printer
profile declares ``support_filament_remain_edit`` (so far only the H3C): a total
and a remaining weight in grams, which the printer then reports as the tray's
``remain`` percentage. Bambuddy sends a spool's figures the same way when it is
assigned to a slot, from the built-in inventory or from Spoolman, instead of
leaving the tray at ``remain: -1``.

The printer doesn't always keep the weight when the spool comes out: an
untagged tray has come back as ``remain: -1``, and a Bambu Lab spool with the
printer's own estimate. So each slot is remembered here: one seen empty, or
just linked to a spool by the AMS sync, gets its spool's weight on its next
fill, and so does one that reports no weight at all. Each fill gets it once, so
a printer that refuses the command is not asked again on every push.

A slot keeps its spool after it reads empty, for a while (slot_unlink_grace)
and for the rest of a running print, so the spool that goes back in may not be
the one assigned. A tray with an RFID tag gets the weight only when the tag is
the assigned spool's (tray_tags, spool_tags).
"""

import math
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

from backend.app.utils.tag_normalization import is_bambu_tray_uuid

# Bambu Studio's limits (DevFilamentWeight.h): both weights 1-10000 g, the
# remaining weight no more than the total, and at most 1000 g remaining for a
# Bambu Lab RFID spool.
MIN_GRAMS = 1
MAX_GRAMS = 10000
MAX_RFID_REMAIN_GRAMS = 1000


class FilamentWeight(NamedTuple):
    """What ``set_filament_weight`` sends for a slot, in grams."""

    total: int
    remain: int
    rfid: bool = False

    @property
    def percent(self) -> int:
        """The ``remain`` the printer reports for this weight; the firmware truncates it."""
        return self.remain * 100 // self.total

    def is_valid(self) -> bool:
        cap = MAX_RFID_REMAIN_GRAMS if self.rfid else MAX_GRAMS
        return MIN_GRAMS <= self.remain <= min(self.total, cap) and self.total <= MAX_GRAMS


def _grams(value: object) -> int | None:
    # A bool is an int in Python; True must not read as 1 g.
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return round(value)


def filament_weight(total: object, remain: object, *, rfid: bool = False) -> FilamentWeight | None:
    """The weight to send for a spool of ``total`` g net with ``remain`` g left, or None.

    None when either is missing, the total is outside Studio's limits, or less
    than a gram is left. A remaining weight above the total counts as a full
    spool, and a Bambu Lab RFID spool's is capped at 1000 g, as in Studio.
    """
    total_g, remain_g = _grams(total), _grams(remain)
    if total_g is None or remain_g is None or not MIN_GRAMS <= total_g <= MAX_GRAMS:
        return None
    remain_g = min(remain_g, total_g, MAX_RFID_REMAIN_GRAMS if rfid else MAX_GRAMS)
    if remain_g < MIN_GRAMS:
        return None
    return FilamentWeight(total_g, remain_g, rfid)


def inventory_weight(label_weight: object, weight_used: object, *, rfid: bool = False) -> FilamentWeight | None:
    """The weight to send for a built-in inventory spool: its label weight, less what it has used."""
    if isinstance(label_weight, bool) or not isinstance(label_weight, int | float):
        return None
    used = weight_used if isinstance(weight_used, int | float) and not isinstance(weight_used, bool) else 0
    return filament_weight(label_weight, label_weight - used, rfid=rfid)


def reads_as(total: object, remaining: object, remain_pct: int, *, rfid: bool = False) -> bool:
    """Whether a spool of ``total`` g with ``remaining`` g left already shows as ``remain_pct`` on the printer.

    The printer reports a slot's weight as a percentage it rounds down: 965 g of
    a 1000 g spool, sent as the slot's weight, comes back as 96 %. A figure that
    comes to the printer's percentage, as it stands or as Bambuddy sends it (in
    whole grams, at most 1000 g for a Bambu Lab spool), is the precise one, and
    replacing it with the percentage's grams would only lose the difference.
    """
    if (
        not isinstance(total, int | float)
        or not isinstance(remaining, int | float)
        or isinstance(total, bool)
        or isinstance(remaining, bool)
        or not math.isfinite(total)
        or not math.isfinite(remaining)
    ):
        return False
    if total > 0 and 0 <= remaining <= total and int(remaining * 100 // total) == remain_pct:
        return True
    sent = filament_weight(total, remaining, rfid=rfid)
    return sent is not None and sent.percent == remain_pct


def is_rfid_tray(tray: Mapping[str, Any] | None) -> bool:
    """True when the AMS read a Bambu Lab RFID tag in this tray."""
    return isinstance(tray, Mapping) and is_bambu_tray_uuid(tray.get("tray_uuid"))


def _tag(value: object) -> str:
    """A tag as Spoolman stores it: no quotes (extra.tag is JSON), uppercase."""
    return value.strip().strip('"').upper() if isinstance(value, str) else ""


def tray_tags(tray: Mapping[str, Any] | None) -> set[str]:
    """The RFID identifiers the AMS reports for a tray, in every form Spoolman may hold them; empty without a tag."""
    if not isinstance(tray, Mapping):
        return set()
    tags = {tag for tag in (_tag(tray.get("tray_uuid")), _tag(tray.get("tag_uid"))) if tag.strip("0")}
    uid = _tag(tray.get("tag_uid"))
    # The AMS reports a Bambu chip's 4-byte UID padded to 8 bytes ("D3E68F32" as
    # "D3E68F3200000100"); a native tag holds the chip's own 4.
    if len(uid) == 16 and uid[:8].strip("0"):
        tags.add(uid[:8])
    return tags


def spool_tags(spool: Mapping[str, Any]) -> set[str]:
    """The tags a Spoolman spool carries: its native tags (Spoolman 0.27+) and extra.tag."""
    tags = {_tag(t.get("uid")) for t in spool.get("tags") or [] if isinstance(t, Mapping)}
    extra = spool.get("extra")
    if isinstance(extra, Mapping):
        tags.add(_tag(extra.get("tag")))
    return tags - {""}


def tag_holder(spools: Iterable[Mapping[str, Any]], tags: set[str]) -> Mapping[str, Any] | None:
    """The spool among ``spools`` that carries one of ``tags``, or None."""
    return next((spool for spool in spools if spool_tags(spool) & tags), None)


def tray_remain(tray: Mapping[str, Any] | None) -> int | None:
    """The tray's ``remain`` percentage as the printer reports it (-1: no weight), or None."""
    if not isinstance(tray, Mapping):
        return None
    try:
        return int(tray.get("remain"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


Slot = tuple[int, int, int]  # (printer_id, ams_id, tray_id)

# Which inventory a spool id belongs to: the two number their spools separately.
INVENTORY = "inventory"
SPOOLMAN = "spoolman"
SpoolKey = tuple[str, int]  # (INVENTORY or SPOOLMAN, the spool's id there)

# Slots whose next fill gets its spool's weight: seen empty, or just linked to
# a spool by the AMS sync.
_pending: set[Slot] = set()
# The spool each slot's current fill has been given a weight for (or found to
# have none to give).
_handled: dict[Slot, SpoolKey] = {}
# Spoolman could not be read for a slot weight, and has not answered since.
_spoolman_down = False


def mark_pending(slot: Slot) -> None:
    """The slot was seen empty, or was just linked to a spool: its next fill needs the weight."""
    _handled.pop(slot, None)
    _pending.add(slot)


def weight_handled(slot: Slot, spool: SpoolKey) -> None:
    """The slot's current fill has had ``spool``'s weight (or has none to get)."""
    _handled[slot] = spool
    _pending.discard(slot)


def needs_weight(slot: Slot, spool: SpoolKey, tray: Mapping[str, Any]) -> bool:
    """Whether the filled ``slot``, assigned ``spool``, should be sent its weight now."""
    if _handled.get(slot) == spool:
        return False
    if slot in _pending:
        return True
    remain = tray_remain(tray)
    return remain is not None and remain < 0


def spoolman_failed() -> bool:
    """Spoolman could not be read for a slot weight. True the first time, until it answers again."""
    global _spoolman_down
    first, _spoolman_down = not _spoolman_down, True
    return first


def spoolman_answered() -> bool:
    """Spoolman answered a slot-weight read. True when it had failed before."""
    global _spoolman_down
    was_down, _spoolman_down = _spoolman_down, False
    return was_down


def reset() -> None:
    """Forget every slot, and any Spoolman outage (tests)."""
    global _spoolman_down
    _pending.clear()
    _handled.clear()
    _spoolman_down = False
