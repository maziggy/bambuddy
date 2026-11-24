"""Weight-split math for prints that traversed >1 AMS tray mid-print.

`state.tray_change_log` records `(global_tray_id, layer_num)` tuples every
time `tray_now` changes during a print (see `bambu_mqtt.py:1861`). At
completion, both the internal Spool inventory (`usage_tracker.py`) and the
Spoolman writer (`spoolman_tracking.py`) need to split a slot's total
weight across the segments those changes define — one call site per
inventory backend, one identical splitting algorithm.

The algorithm lives here so the two callers cannot drift: #1793 came from
`spoolman_tracking` never carrying the split at all, while `usage_tracker`
had shipped it since #957 and refined it in #1771. Sharing the helper is
the structural fix; each caller wraps its own "resolve segment tray →
charge N grams" side effect.
"""

from __future__ import annotations

# Qualified-name access (``threemf_tools.mm_to_grams(...)`` rather than
# ``from … import mm_to_grams``) so ``unittest.mock.patch`` on the
# threemf_tools module lands in the helper too — the pre-refactor
# ``usage_tracker`` call site imported at call-time inside a try block,
# which had the same testability property.
from backend.app.utils import threemf_tools


def compute_tray_split_grams(
    tray_changes: list[tuple[int, int]],
    total_weight: float,
    slot_id: int,
    layer_usage: dict[int, dict[int, float]] | None,
    density: float,
    diameter: float,
    total_layers: int,
    last_layer_num: int,
) -> list[tuple[int, int, float]]:
    """Split ``total_weight`` for a single slice slot across tray segments.

    ``tray_changes`` is the ordered list ``[(global_tray_id, seg_start_layer), ...]``
    exactly as it appears in ``state.tray_change_log``. The last segment
    runs to the end of the print; every other segment ends at the next
    entry's ``seg_start_layer``.

    Preference order for per-segment grams — matches ``usage_tracker`` so
    both inventory backends split identically:

    1. **G-code cumulative extrusion** (``layer_usage``, indexed by 0-based
       filament id). Precise: uses the mm actually consumed between
       ``seg_start_layer`` and the next segment's start, then converts via
       Spoolman-authoritative ``density`` / ``diameter``.
    2. **Linear layer-ratio** — ``total_weight * segment_layers / denom``,
       with ``denom = total_layers or last_layer_num``. Firmware on P1S
       (observed) resets ``total_layer_num`` to 0 at print end, so the
       captured ``last_layer_num`` is the durable denominator (see
       ``usage_tracker.py:1132``). #1771 addressed the pre-fix behaviour
       of dumping everything onto the last segment.
    3. **Equal-split** — when neither denominator is available (server
       restart mid-print, missing state). Wrong but bounded — the last
       segment absorbs any rounding drift via the ``is_last`` branch.

    Returns ``[(seg_idx, global_tray_id, segment_grams)]``. Empty when
    ``tray_changes`` is empty; the caller decides whether to fall through
    to single-tray attribution (``len(tray_changes) <= 1``).
    """
    if not tray_changes:
        return []

    filament_id = slot_id - 1
    n_segments = len(tray_changes)
    denom = total_layers or last_layer_num
    results: list[tuple[int, int, float]] = []
    sum_previous = 0.0

    for seg_idx, (tray_global, seg_start_layer) in enumerate(tray_changes):
        is_last = seg_idx + 1 >= n_segments

        if is_last:
            segment_grams = total_weight - sum_previous
        elif layer_usage:
            seg_end_layer = tray_changes[seg_idx + 1][1]
            mm_at_start = threemf_tools.get_cumulative_usage_at_layer(layer_usage, seg_start_layer).get(filament_id, 0)
            mm_at_end = threemf_tools.get_cumulative_usage_at_layer(layer_usage, seg_end_layer).get(filament_id, 0)
            segment_grams = threemf_tools.mm_to_grams(mm_at_end - mm_at_start, diameter, density)
        else:
            seg_end_layer = tray_changes[seg_idx + 1][1]
            if denom > 0:
                segment_grams = total_weight * (seg_end_layer - seg_start_layer) / denom
            else:
                segment_grams = total_weight / n_segments

        sum_previous += segment_grams
        results.append((seg_idx, tray_global, segment_grams))

    return results


def _norm_colour(raw: object) -> str:
    """``RRGGBB`` upper-case: no ``#``, no alpha. Anything else reads as unknown."""
    if not isinstance(raw, str):
        return ""
    colour = raw.strip().lstrip("#").upper()
    return colour[:6] if len(colour) == 8 else colour


def tray_filaments_from_raw(raw_data: object, layout) -> dict[int, tuple[str, str]]:
    """``{global_tray_id: (TRAY_TYPE, RRGGBB)}`` from a printer's MQTT state.

    Feeds the material and colour check of :func:`slot_tray_changes`. An empty
    tray, or one the payload names badly, reads as ``("", "")`` or is left out:
    unknown, which never blocks a split. Free-form MQTT data, so a malformed
    unit or tray costs only that entry.
    """
    if not isinstance(raw_data, dict):
        return {}
    filaments: dict[int, tuple[str, str]] = {}

    def _add(global_tray_id: int, tray: dict) -> None:
        tray_type = tray.get("tray_type")
        tray_type = tray_type.strip().upper() if isinstance(tray_type, str) else ""
        filaments[global_tray_id] = (tray_type, _norm_colour(tray.get("tray_color")))

    units = raw_data.get("ams") or []
    if isinstance(units, dict):  # also stored as the push's {"ams": [...]}
        units = units.get("ams") or []
    for unit in units if isinstance(units, list) else []:
        if not isinstance(unit, dict):
            continue
        for tray in unit.get("tray") or []:
            if not isinstance(tray, dict):
                continue
            try:
                ams_id, tray_id = int(unit.get("id", "")), int(tray.get("id", ""))
            except (TypeError, ValueError):
                continue
            _add(layout.tray_from_slot_key(ams_id, tray_id), tray)
    vt_trays = raw_data.get("vt_tray") or []
    for tray in [vt_trays] if isinstance(vt_trays, dict) else vt_trays if isinstance(vt_trays, list) else []:
        if not isinstance(tray, dict):
            continue
        try:
            _add(int(tray.get("id", 254)), tray)
        except (TypeError, ValueError):
            continue
    return filaments


_SHARED = -1  # owner of a tray two used slots are mapped to


def slot_tray_changes(
    tray_changes: list,
    slot_to_tray: list | None,
    used_slot_ids: list[int],
    tray_filaments: dict[int, tuple[str, str]],
) -> tuple[dict[int, list[tuple[int, int]]], str | None]:
    """Each slot's own tray changes in a multi-filament print (#3650).

    Root: in a print with several filaments ``tray_now`` moves on every colour
    change, so ``tray_change_log`` cannot be split per slot the way the
    single-filament path splits it (#957, #1793) — every slot would be charged
    for the other slots' segments. An AMS backup switch still shows up in that
    log, though, as the one kind of entry that loads a tray **outside** the
    print's mapping: the backup tray was never assigned a slot.

    Logic: a mapped tray belongs to its slot. An unmapped tray, the first time
    it is loaded, continues the slot of the entry right before it — the slot
    whose spool ran out — and stays with that slot for the rest of the print,
    because every later colour change back to that slot loads it directly (so
    "the entry before it" is only meaningful on its first load). A second
    runout onto a third tray chains the same way. As a cheap check, the backup
    tray must not report a different material or colour than the slot's mapped
    tray: AMS backup only switches between identical filaments. Unknown values
    (an emptied tray may report nothing) do not block.

    Entries before the first mapped tray are what was still loaded from the
    previous print; at the layer the print's own first tray was loaded they
    consumed nothing and are skipped.

    Returns ``(per_slot, reason)``. ``per_slot`` holds only the slots that
    changed trays, each list starting with ``(mapped_tray, 0)`` and shaped
    like ``tray_change_log`` so :func:`compute_tray_split_grams` takes it
    as is. ``reason`` is set when the log does not fit this pattern (a used
    slot the mapping names no tray for, an unmapped tray with no slot before
    it, a tray shared by two slots, a filament mismatch, a leftover tray that
    ran past layer 0, a slot alternating between two trays); the caller then
    attributes by mapping alone, as before #3650, and ``per_slot`` is empty.

    Rejected: matching the backup to a slot by material and colour alone. Two
    slots of a print may hold the same filament, and the order of the log is
    the evidence of which one ran out.
    """
    if not slot_to_tray or len(used_slot_ids) < 2:
        return {}, None

    owner: dict[int, int] = {}
    mapped: dict[int, int] = {}
    for slot_id in used_slot_ids:
        tray = slot_to_tray[slot_id - 1] if 0 < slot_id <= len(slot_to_tray) else None
        if not isinstance(tray, int) or isinstance(tray, bool) or tray < 0:
            # The slot fed from somewhere the mapping does not name (an
            # external spool as -1, #3166): that tray would read as a backup.
            return {}, f"slot {slot_id} has no tray in the mapping {slot_to_tray}"
        mapped[slot_id] = tray
        owner[tray] = _SHARED if tray in owner else slot_id

    current = dict(mapped)
    per_slot: dict[int, list[tuple[int, int]]] = {slot_id: [(tray, 0)] for slot_id, tray in mapped.items()}
    # Times a slot went back to a tray it had left. Once is a refilled tray
    # loaded again; a slot alternating between two trays is a misread colour
    # change (a runout caught while loading, the backup credited to the slot
    # printing before it), not a backup chain.
    returns: dict[int, int] = {}
    prev_slot: int | None = None
    leading_layer: int | None = None
    started = False

    for entry in tray_changes or []:
        try:
            tray, layer = int(entry[0]), int(entry[1])
        except (TypeError, ValueError, IndexError):
            return {}, f"unreadable tray_change_log entry {entry!r}"
        slot = owner.get(tray)
        if slot is None:
            if not started:
                leading_layer = layer
                continue
            if prev_slot is None or prev_slot == _SHARED:
                return {}, f"tray {tray} is in no slot and follows no single slot"
            new_fil = tray_filaments.get(tray, ("", ""))
            old_fil = tray_filaments.get(mapped[prev_slot], ("", ""))
            for new, old, what in ((new_fil[0], old_fil[0], "material"), (new_fil[1], old_fil[1], "colour")):
                if new and old and new != old:
                    return (
                        {},
                        f"tray {tray} {what} {new} differs from slot {prev_slot}'s tray {mapped[prev_slot]} ({old})",
                    )
            owner[tray] = prev_slot
            slot = prev_slot
        if not started:
            if leading_layer is not None and leading_layer != layer:
                return {}, f"a tray outside the mapping fed until layer {layer} before the print's first tray"
            started = True
        if slot == _SHARED:
            prev_slot = _SHARED
            continue
        if tray != current[slot]:
            if any(earlier == tray for earlier, _ in per_slot[slot]):
                returns[slot] = returns.get(slot, 0) + 1
                if returns[slot] > 1:
                    return {}, f"slot {slot} keeps alternating between trays {sorted({t for t, _ in per_slot[slot]})}"
            per_slot[slot].append((tray, layer))
            current[slot] = tray
        prev_slot = slot

    return {slot_id: changes for slot_id, changes in per_slot.items() if len(changes) > 1}, None
