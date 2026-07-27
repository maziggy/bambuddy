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
