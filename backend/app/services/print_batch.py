"""Batch order planning: per-plate targets, progress, and staged dispatch (#342).

A batch stores *intent* in :class:`PrintBatchPlate` rows — "this order wants 3
of plate 2" — while its queue items record what was actually dispatched.
Everything here derives one from the other.

The distinction matters for exactly one reason, and it is the reason the
feature exists: a failed or cancelled run does not count towards the target, so
``remaining`` goes back up and the order still says it owes a print. A design
that only counted the items it created could not tell "the user cancelled this
deliberately" apart from "this one burned and needs reprinting".

Batches created before targets existed have no plate rows. They still report
progress — the plate breakdown is derived from their queue items and every
target simply equals the number of items dispatched, so ``remaining`` is zero
and the dispatch endpoint has nothing to do. ``has_targets`` tells callers
which kind of batch they are looking at.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.print_batch import PrintBatch, PrintBatchPlate
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.print_queue import PrintQueueItem, PrintQueueVariant

logger = logging.getLogger(__name__)

# Statuses that consume a unit of the target. "printing" counts because the
# run is in flight — re-dispatching it would double-print. "failed",
# "cancelled" and "skipped" deliberately do not.
CONSUMING_STATUSES = ("pending", "printing", "completed")

# Queue statuses the roll-up has a counter for. Anything else is ignored rather
# than crashing the page — the queue's status vocabulary is allowed to grow
# without this module having to be updated in lockstep.
COUNTED_STATUSES = ("pending", "printing", "completed", "failed", "cancelled", "skipped")

# Columns copied onto a clone when dispatching more of a plate. This is the
# print *configuration* the user already chose and the API already validated —
# copying the row is what keeps a second dispatch identical to the first
# without re-serialising twenty fields through a template blob that would drift
# from the model the first time someone adds a column.
CLONED_SETTING_COLUMNS = (
    "printer_id",
    "target_model",
    "target_location",
    "required_filament_types",
    "archive_id",
    "library_file_id",
    "project_id",
    "batch_id",
    "ams_mapping",
    "filament_overrides",
    "plate_id",
    "print_time_seconds",
    "gcode_injection",
    "nozzle_mapping",
    "nozzle_rack_choice",
    "require_previous_success",
    "auto_off_after",
    "manual_start",
    "bed_levelling",
    "flow_cali",
    "vibration_cali",
    "layer_inspect",
    "timelapse",
    "use_ams",
    "nozzle_offset_cali",
    "preheat_override",
    "preheat_chamber_target_override",
    "skip_filament_check",
)

CLONED_VARIANT_COLUMNS = (
    "position",
    "library_file_id",
    "target_model",
    "plate_id",
    "ams_mapping",
    "nozzle_mapping",
    "nozzle_rack_choice",
    "filament_overrides",
    "required_filament_types",
    "print_time_seconds",
)


class BatchDispatchError(Exception):
    """Raised when more runs are owed but nothing can be cloned to produce them."""


@dataclass
class PlateProgress:
    """Per-plate roll-up for one batch."""

    plate_id: int | None
    plate_name: str | None
    quantity_target: int
    sort_order: int = 0
    pending: int = 0
    printing: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    skipped: int = 0
    # Actual material + energy cost of this plate's finished runs. None when no
    # run has produced a cost yet — reported as "unknown", never as zero.
    actual_cost: float | None = None
    filament_used_grams: float | None = None
    print_time_seconds: int = 0

    @property
    def dispatched(self) -> int:
        return self.pending + self.printing + self.completed

    @property
    def remaining(self) -> int:
        return max(0, self.quantity_target - self.dispatched)

    @property
    def cost_per_run(self) -> float | None:
        """Observed mean cost of this plate's completed runs, or None.

        Deliberately measured rather than estimated from the file: the file's
        estimate ignores what the run actually consumed, and a plate that has
        never completed has no honest number to show.
        """
        if self.completed <= 0 or self.actual_cost is None:
            return None
        return self.actual_cost / self.completed

    @property
    def estimated_remaining_cost(self) -> float | None:
        per_run = self.cost_per_run
        if per_run is None:
            return None
        return per_run * self.remaining


@dataclass
class BatchProgress:
    """Whole-order roll-up, plus the per-plate breakdown it was derived from."""

    plates: list[PlateProgress] = field(default_factory=list)
    has_targets: bool = False

    def _sum(self, attr: str) -> int:
        return sum(getattr(p, attr) for p in self.plates)

    @property
    def pending(self) -> int:
        return self._sum("pending")

    @property
    def printing(self) -> int:
        return self._sum("printing")

    @property
    def completed(self) -> int:
        return self._sum("completed")

    @property
    def failed(self) -> int:
        return self._sum("failed")

    @property
    def cancelled(self) -> int:
        return self._sum("cancelled")

    @property
    def skipped(self) -> int:
        return self._sum("skipped")

    @property
    def target(self) -> int:
        return self._sum("quantity_target")

    @property
    def remaining(self) -> int:
        return self._sum("remaining")

    @property
    def actual_cost(self) -> float | None:
        costs = [p.actual_cost for p in self.plates if p.actual_cost is not None]
        return sum(costs) if costs else None

    @property
    def estimated_remaining_cost(self) -> float | None:
        estimates = [p.estimated_remaining_cost for p in self.plates if p.estimated_remaining_cost is not None]
        return sum(estimates) if estimates else None

    @property
    def filament_used_grams(self) -> float | None:
        grams = [p.filament_used_grams for p in self.plates if p.filament_used_grams is not None]
        return sum(grams) if grams else None

    @property
    def print_time_seconds(self) -> int:
        return self._sum("print_time_seconds")

    @property
    def is_fulfilled(self) -> bool:
        """True when every target is met and nothing is still in flight.

        A zero total target is never "fulfilled". Without that guard a legacy
        batch whose items were all cancelled one by one would report itself
        completed — its derived target counts only pending/printing/completed
        items, so cancelling the lot leaves a target of zero that trivially
        satisfies ``remaining == 0``.
        """
        return self.target > 0 and self.remaining == 0 and self.pending == 0 and self.printing == 0


async def load_progress(db: AsyncSession, batch: PrintBatch) -> BatchProgress:
    """Build the per-plate progress roll-up for *batch*.

    Two queries plus one for costs, regardless of how many plates the order
    has — this runs once per batch in the list endpoint.
    """
    plate_rows = (await db.execute(select(PrintBatchPlate).where(PrintBatchPlate.batch_id == batch.id))).scalars().all()

    # (plate_id, status) -> count, plus the time/weight actually recorded.
    item_rows = (
        await db.execute(
            select(
                PrintQueueItem.plate_id,
                PrintQueueItem.status,
                func.count(PrintQueueItem.id),
                func.sum(PrintQueueItem.print_time_seconds),
            )
            .where(PrintQueueItem.batch_id == batch.id)
            .group_by(PrintQueueItem.plate_id, PrintQueueItem.status)
        )
    ).all()

    # Per-run actuals, attributed through the queue item that produced them.
    # PrintLogEntry is the authoritative per-run record (#1378) and is already
    # scoped to the printed plate (#2614), so a multi-plate order gets each
    # plate's own cost rather than the whole file's.
    cost_rows = (
        await db.execute(
            select(
                PrintQueueItem.plate_id,
                func.sum(func.coalesce(PrintLogEntry.cost, 0.0) + func.coalesce(PrintLogEntry.energy_cost, 0.0)),
                func.sum(PrintLogEntry.filament_used_grams),
            )
            .select_from(PrintLogEntry)
            .join(PrintQueueItem, PrintLogEntry.queue_item_id == PrintQueueItem.id)
            .where(PrintQueueItem.batch_id == batch.id)
            .group_by(PrintQueueItem.plate_id)
        )
    ).all()
    costs = {row[0]: (row[1], row[2]) for row in cost_rows}

    progress = BatchProgress(has_targets=bool(plate_rows))
    by_plate: dict[int | None, PlateProgress] = {}

    for row in plate_rows:
        by_plate[row.plate_id] = PlateProgress(
            plate_id=row.plate_id,
            plate_name=row.plate_name,
            quantity_target=row.quantity_target,
            sort_order=row.sort_order,
        )

    for plate_id, status, count, time_sum in item_rows:
        plate = by_plate.get(plate_id)
        if plate is None:
            # A queue item for a plate the order has no target row for: either
            # a legacy batch, or an item grouped in by hand after the fact.
            # Its own dispatched count becomes its target so it reads as
            # complete rather than as owing work nobody asked for.
            plate = PlateProgress(plate_id=plate_id, plate_name=None, quantity_target=0, sort_order=plate_id or 0)
            by_plate[plate_id] = plate
            if status in CONSUMING_STATUSES:
                plate.quantity_target += count
        elif not progress.has_targets and status in CONSUMING_STATUSES:
            plate.quantity_target += count
        if status in COUNTED_STATUSES:
            setattr(plate, status, getattr(plate, status) + count)
        else:
            logger.debug("Batch %s: ignoring queue item status %r in progress roll-up", batch.id, status)
        plate.print_time_seconds += int(time_sum or 0)

    for plate_id, (cost_sum, gram_sum) in costs.items():
        plate = by_plate.get(plate_id)
        if plate is None:
            continue
        plate.actual_cost = float(cost_sum) if cost_sum else None
        plate.filament_used_grams = float(gram_sum) if gram_sum else None

    progress.plates = sorted(by_plate.values(), key=lambda p: (p.sort_order, p.plate_id or 0))
    return progress


async def refresh_batch_status(db: AsyncSession, batch: PrintBatch) -> bool:
    """Flip an ``active`` batch to ``completed`` once its targets are met.

    Returns True when the status changed. A ``cancelled`` batch is never
    resurrected, and a ``completed`` batch drops back to ``active`` if its
    targets grow — raising a target on a finished order reopens it rather than
    leaving a "completed" order that still owes prints.
    """
    progress = await load_progress(db, batch)

    if batch.status == "cancelled":
        return False

    if batch.status == "active" and progress.is_fulfilled:
        batch.status = "completed"
        batch.completed_at = datetime.now(timezone.utc)
        logger.info("Batch %s fulfilled — marked completed", batch.id)
        return True

    # A grouping whose every item was cancelled one at a time is finished, but
    # nothing was produced, so "completed" would be a lie and `is_fulfilled`
    # rightly refuses it (its derived target is zero). Left alone it would sit
    # on "active" forever. Cancelled is what it is, and matches what the
    # batch-level Cancel action would have set had it been used.
    #
    # Deliberately not applied to orders: an order states its intent
    # independently of its runs, so cancelling every run still leaves it owing
    # work and offering to re-queue it. A grouping has no such statement — it
    # was only ever the sum of its items.
    if batch.status == "active" and not progress.has_targets and progress.completed == 0:
        settled = progress.pending == 0 and progress.printing == 0
        if settled and progress.cancelled > 0 and progress.failed == 0 and progress.skipped == 0:
            batch.status = "cancelled"
            logger.info("Batch %s had every item cancelled — marked cancelled", batch.id)
            return True

    if batch.status == "completed" and not progress.is_fulfilled:
        batch.status = "active"
        batch.completed_at = None
        logger.info("Batch %s reopened — targets no longer met", batch.id)
        return True

    return False


async def backfill_batch_statuses(db: AsyncSession) -> int:
    """Close out ``active`` batches that finished before the status existed.

    ``completed`` only became reachable with #342. Every batch created since
    the feature shipped in April 2026 is therefore still marked ``active``,
    however long ago its last run finished — so without this pass the Batches
    tab opens on months of accumulated history.

    Runs on every startup rather than once behind a marker: it is cheap (only
    batches with nothing in flight are even considered), it is idempotent, and
    repeating it also closes out any order whose last run landed while the
    process was down.

    Returns the number of batches whose status changed.
    """
    candidates = (
        (
            await db.execute(
                select(PrintBatch)
                .where(PrintBatch.status == "active")
                # Anything still queued or printing is by definition unfinished,
                # and re-deriving its progress would change nothing.
                .where(
                    ~select(PrintQueueItem.id)
                    .where(PrintQueueItem.batch_id == PrintBatch.id)
                    .where(PrintQueueItem.status.in_(("pending", "printing")))
                    .exists()
                )
            )
        )
        .scalars()
        .all()
    )

    changed = 0
    for batch in candidates:
        if await refresh_batch_status(db, batch):
            changed += 1

    if changed:
        await db.commit()
        logger.info("Marked %d finished batch(es) as completed at startup (#342)", changed)
    return changed


async def refresh_batch_status_for_item(db: AsyncSession, queue_item_id: int) -> None:
    """Re-evaluate the batch owning *queue_item_id*, if it has one.

    Called from the print-completion path so a finished order reports itself
    complete the moment its last run lands, rather than whenever someone next
    opens the page.
    """
    batch_id = (
        await db.execute(select(PrintQueueItem.batch_id).where(PrintQueueItem.id == queue_item_id))
    ).scalar_one_or_none()
    if batch_id is None:
        return
    batch = (await db.execute(select(PrintBatch).where(PrintBatch.id == batch_id))).scalar_one_or_none()
    if batch is None:
        return
    await refresh_batch_status(db, batch)


async def _next_position(db: AsyncSession, printer_id: int | None) -> int:
    """Next free queue position in the scope a clone will land in.

    Positions are per-queue, not global: one sequence per printer plus one
    shared sequence for unassigned / model-based items, matching the scope the
    add-to-queue route uses. Taking a global MAX here would drop every clone
    at the end of whichever printer's queue happens to be longest and scramble
    the order the user sees.
    """
    # Same advisory lock the add-to-queue route takes (#1625-followup): two
    # concurrent inserts into an empty scope would otherwise both read
    # MAX(position) as 0 and land on position 1. SQLite serialises writes
    # implicitly and needs no equivalent.
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(1625, :k)"), {"k": printer_id if printer_id is not None else 0}
        )

    scope = PrintQueueItem.printer_id == printer_id if printer_id is not None else PrintQueueItem.printer_id.is_(None)
    max_pos = (
        await db.execute(
            select(func.max(PrintQueueItem.position)).where(scope).where(PrintQueueItem.status == "pending")
        )
    ).scalar() or 0
    return max_pos + 1


def _clone_queue_item(source: PrintQueueItem, *, position: int, created_by_id: int | None) -> PrintQueueItem:
    """Copy *source*'s print configuration into a fresh pending item.

    Lifecycle state (status, timestamps, retry counters, scheduler flags) is
    deliberately not copied — the clone is a new run, not a resurrection.

    ``scheduled_time`` is dropped too: dispatching more of a plate is a
    "queue this now" action, and replaying the original's scheduled time would
    either fire immediately (it is in the past) or silently park the new run
    until a moment the user chose for a different print.

    ``cleanup_library_after_dispatch`` is forced off. It only ever comes from
    the Printers-page direct-print flow, where it deletes the transient library
    row after dispatch — replaying that on a clone would delete the source file
    out from under the rest of the order.
    """
    clone = PrintQueueItem(
        status="pending",
        position=position,
        created_by_id=created_by_id if created_by_id is not None else source.created_by_id,
        cleanup_library_after_dispatch=False,
    )
    for column in CLONED_SETTING_COLUMNS:
        setattr(clone, column, getattr(source, column))
    return clone


async def dispatch_remaining(
    db: AsyncSession,
    batch: PrintBatch,
    *,
    plate_id: int | None = None,
    only_plate: bool = False,
    limit: int | None = None,
    created_by_id: int | None = None,
) -> list[PrintQueueItem]:
    """Create queue items for the runs *batch* still owes.

    ``only_plate`` restricts the dispatch to the single plate named by
    ``plate_id`` (which may legitimately be ``None`` for a single-plate file);
    otherwise every plate with work outstanding is dispatched in plate order.
    ``limit`` caps the total number of items created across all plates.

    Raises :class:`BatchDispatchError` when a plate owes runs but has no
    existing item to clone — the order can describe work it has never once
    dispatched, and there is no configuration to copy in that case.
    """
    progress = await load_progress(db, batch)
    if not progress.has_targets:
        return []

    targets = [p for p in progress.plates if p.remaining > 0]
    if only_plate:
        targets = [p for p in targets if p.plate_id == plate_id]

    created: list[PrintQueueItem] = []

    for plate in targets:
        if limit is not None and len(created) >= limit:
            break

        source = (
            await db.execute(
                select(PrintQueueItem)
                .options(selectinload(PrintQueueItem.variants))
                .where(PrintQueueItem.batch_id == batch.id)
                .where(PrintQueueItem.plate_id == plate.plate_id)
                .order_by(PrintQueueItem.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if source is None:
            raise BatchDispatchError(
                f"Plate {plate.plate_id if plate.plate_id is not None else 1} has no queued or finished run to "
                "copy settings from. Queue it once from the file, then dispatch the rest from here."
            )

        wanted = plate.remaining
        if limit is not None:
            wanted = min(wanted, limit - len(created))

        # One scope per source printer; clones for this plate all land in it,
        # appended after whatever is already queued there.
        position = await _next_position(db, source.printer_id)

        for _ in range(wanted):
            clone = _clone_queue_item(source, position=position, created_by_id=created_by_id)
            position += 1
            db.add(clone)
            await db.flush()
            for variant in source.variants:
                cloned_variant = PrintQueueVariant(queue_item_id=clone.id)
                for column in CLONED_VARIANT_COLUMNS:
                    setattr(cloned_variant, column, getattr(variant, column))
                db.add(cloned_variant)
            created.append(clone)

    if created:
        # Dispatching more work can only ever un-fulfil an order, but run the
        # check anyway so a reopened batch flips back from completed.
        await db.flush()
        await refresh_batch_status(db, batch)

    logger.info("Dispatched %d item(s) for batch %s", len(created), batch.id)
    return created
