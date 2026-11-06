"""A spool swapped on one tray while a print runs (#3325).

The tray-change log (``utils.tray_split``) splits a print across trays when the
AMS switches to a backup on a runout. A spool replaced by hand in the *same*
tray leaves no trace there: the external spool on a printer without an AMS is
tray 254 before and after, and so is an AMS slot refilled with a new roll. The
only record of the swap is the operator assigning the new spool, and without a
layer to put it at, completion charged the whole print to whichever spool the
tray held at the end.

So a print keeps a second log: the spool on every assigned tray at print start,
and every later assignment of a different spool to a tray, with the layer the
printer was on. At completion a tray's grams are divided between its spools in
proportion to the filament the G-code feeds between those layers (by layer
count without G-code data). Proportional, so the shares always add up to the
tray's total and the last spool is never left with nothing.

A tray with no spool at print start is not split by its first assignment:
that is the operator catching up on a forgotten one, and that spool takes the
print from its start, as before. Only a later, different spool splits it.

The log lives on ``PrinterState`` (Spoolman reports after the print session
row is gone) and is mirrored to ``active_print_sessions`` for a restart.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import event

from backend.app.utils import threemf_tools

logger = logging.getLogger(__name__)

INVENTORY = "inventory"
SPOOLMAN = "spoolman"


def entry(backend: str, ams_id: int, tray_id: int, spool_id: int, layer: int, start: bool = False) -> dict:
    return {"backend": backend, "ams": ams_id, "tray": tray_id, "spool": spool_id, "layer": layer, "start": start}


def start_entries(backend: str, assignments: dict[tuple[int, int], int]) -> list[dict]:
    """The spool on every assigned tray as the print starts."""
    return [
        entry(backend, ams_id, tray_id, spool_id, 0, start=True) for (ams_id, tray_id), spool_id in assignments.items()
    ]


def _valid(item) -> bool:
    return isinstance(item, dict) and {"backend", "ams", "tray", "spool", "layer"} <= item.keys()


def segments(log: list | None, backend: str, ams_id: int, tray_id: int) -> list[tuple[int, int]]:
    """``[(spool_id, from_layer), ...]`` for one tray, or ``[]`` when it was not swapped.

    On a tray that had no spool at print start, the first assignment is a
    forgotten one caught up: that spool covers the print from its start, and
    only a later, different spool is a swap. Repeats of the same spool
    collapse; a swap back to an earlier spool is its own segment.
    """
    items = [
        item
        for item in (log or [])
        if _valid(item) and item["backend"] == backend and item["ams"] == ams_id and item["tray"] == tray_id
    ]
    result: list[tuple[int, int]] = []
    for item in items:
        if result and result[-1][0] == item["spool"]:
            continue
        result.append((item["spool"], max(0, int(item["layer"] or 0)) if result else 0))
    return result if len(result) > 1 else []


def split(
    segs: list[tuple[int, int]],
    grams: float,
    filament_id: int | None,
    layer_usage: dict[int, dict[int, float]] | None,
    end_layer: int,
    from_layer: int = 0,
) -> list[tuple[int, float]]:
    """Divide ``grams`` fed between ``from_layer`` and ``end_layer`` among the spools of ``segs``.

    Returns ``[(spool_id, grams), ...]`` in order, one entry per spool, the
    shares summing to ``grams``. With nothing to weigh by (no G-code data, no
    layer range) everything goes to the spool loaded at ``end_layer``.
    """
    end_layer = max(end_layer, from_layer)
    bounds: list[tuple[int, int]] = []  # (spool_id, from_layer) restricted to the range
    for spool_id, layer in segs:
        if layer <= from_layer:
            bounds = [(spool_id, from_layer)]
        elif layer < end_layer:
            bounds.append((spool_id, layer))
    if not bounds:
        bounds = [(segs[-1][0], from_layer)]

    edges = [layer for _, layer in bounds] + [end_layer]
    weights: list[float] = []
    if layer_usage and filament_id is not None:

        def mm_at(layer: int) -> float:
            return threemf_tools.get_cumulative_usage_at_layer(layer_usage, layer).get(filament_id, 0.0)

        weights = [max(0.0, mm_at(edges[i + 1]) - mm_at(edges[i])) for i in range(len(bounds))]
    if sum(weights) <= 0:
        weights = [float(edges[i + 1] - edges[i]) for i in range(len(bounds))]
    total = sum(weights)

    shares: dict[int, float] = {}
    if total <= 0:
        shares[bounds[-1][0]] = grams
    else:
        for (spool_id, _), weight in zip(bounds, weights, strict=True):
            shares[spool_id] = shares.get(spool_id, 0.0) + grams * weight / total
    return list(shares.items())


class Splitter:
    """One print's swap log, ready to share a tray's grams out (one per backend).

    ``layer_usage`` is the G-code's per-layer usage, or a callable returning it,
    read only when a tray was actually swapped. ``end_layer`` is where the
    print stopped; for a completed print the G-code's last layer wins over it.
    """

    def __init__(self, log: list | None, backend: str, layer_usage, end_layer: int, completed: bool) -> None:
        self.log = list(log or [])
        self.backend = backend
        # The per-layer usage, or until first read, the callable that returns it.
        self._layer_usage: Any = layer_usage
        self.end_layer = end_layer
        self.completed = completed

    def _usage(self) -> dict[int, dict[int, float]] | None:
        if callable(self._layer_usage):
            try:
                self._layer_usage = self._layer_usage()
            except Exception:  # noqa: BLE001 -- the split falls back to layer counts
                logger.debug("[SPOOL-SWAP] No per-layer usage for the split", exc_info=True)
                self._layer_usage = None
        return self._layer_usage

    def swapped(self, ams_id: int, tray_id: int) -> bool:
        return bool(segments(self.log, self.backend, ams_id, tray_id))

    def shares(
        self,
        ams_id: int,
        tray_id: int,
        slot_id: int,
        grams: float,
        from_layer: int = 0,
        to_layer: int | None = None,
    ) -> list[tuple[int, float]] | None:
        """``[(spool_id, grams), ...]`` for a swapped tray, None for one that kept its spool."""
        segs = segments(self.log, self.backend, ams_id, tray_id)
        if not segs:
            return None
        usage = self._usage()
        if to_layer is not None:
            end = to_layer
        elif self.completed and usage:
            end = max(usage)
        else:
            end = self.end_layer
        result = split(segs, grams, slot_id - 1, usage, end, from_layer)
        logger.info(
            "[SPOOL-SWAP] %s AMS%d-T%d swapped during the print %s: %.2fg split %s",
            self.backend,
            ams_id,
            tray_id,
            segs,
            grams,
            [(spool, round(share, 2)) for spool, share in result],
        )
        return result


def _on_commit(db, apply) -> None:
    """Run ``apply`` once ``db`` commits; never if it rolls back first."""
    sync = getattr(db, "sync_session", None)
    if sync is None:
        apply()
        return
    settled: list[bool] = []

    def committed(_session) -> None:
        if not settled:
            settled.append(True)
            apply()

    def rolled_back(_session) -> None:
        settled.append(False)

    event.listen(sync, "after_commit", committed, once=True)
    event.listen(sync, "after_rollback", rolled_back, once=True)


async def record_assignment(db, printer_id: int, ams_id: int, tray_id: int, spool_id: int, backend: str) -> None:
    """Note that ``spool_id`` was assigned to a tray, if that printer is printing.

    Runs on the caller's session, before the caller commits the assignment:
    a second session would wait on the SQLite write lock the assignment holds.
    The log is the persisted row's merged with ``PrinterState``'s: after a
    restart the state is empty until the printer reports RUNNING again, and a
    runout leaves it paused, so the row alone holds the print's start spools.
    ``PrinterState`` takes the new entry only once the assignment commits; one
    that fails must not split the print. Best-effort: an assignment must never
    fail because of this.
    """
    try:
        from backend.app.models.active_print_session import ActivePrintSession
        from backend.app.services.printer_manager import printer_manager

        state = printer_manager.get_status(printer_id)
        if state is None:
            return
        # The row exists from print start to completion: a print Bambuddy is
        # tracking is running.
        row = await db.get(ActivePrintSession, printer_id)
        if row is None:
            return
        log = [item for item in (row.spool_change_log or []) if _valid(item)]
        log += [item for item in (getattr(state, "spool_change_log", None) or []) if _valid(item) and item not in log]
        same_tray = [i for i in log if _valid(i) and (i["backend"], i["ams"], i["tray"]) == (backend, ams_id, tray_id)]
        if same_tray and same_tray[-1]["spool"] == spool_id:
            return
        layer = int(getattr(state, "layer_num", 0) or 0)
        log.append(entry(backend, ams_id, tray_id, spool_id, layer))
        row.spool_change_log = list(log)
        _on_commit(db, lambda: setattr(state, "spool_change_log", log))
        logger.info(
            "[SPOOL-SWAP] Printer %d %s AMS%d-T%d: spool %d assigned at layer %d during the print",
            printer_id,
            backend,
            ams_id,
            tray_id,
            spool_id,
            layer,
        )
    except Exception:  # noqa: BLE001 -- never fail an assignment over its usage note
        logger.warning("[SPOOL-SWAP] Could not record a mid-print assignment on printer %d", printer_id, exc_info=True)
