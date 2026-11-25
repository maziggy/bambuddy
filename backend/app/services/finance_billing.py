import json
import logging
import uuid
from types import SimpleNamespace
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.archive import PrintArchive
from backend.app.models.finance import TransactionType, UserWallet, WalletTransaction
from backend.app.models.print_log import INCLUDE_OPEN_RUNS, PrintLogEntry
from backend.app.models.settings import Settings
from backend.app.services.finance_balance import sync_personal_wallet_balance
from backend.app.services.finance_budget import is_billing_enabled, release_budget_reservation

logger = logging.getLogger(__name__)


class BillingRunIdCollisionError(RuntimeError):
    """A billing idempotency key points at a different physical print run."""


async def _get_balance_after_for_transaction(
    db: AsyncSession,
    user_id: int | None,
    cost_center_id: int | None,
    amount: float,
) -> float:
    """Calculate balance_after for a transaction.

    For cost-center transactions: sum of ALL transactions for that cost center (global).
    For personal transactions (cost_center_id=None): user's wallet balance (personal).

    Args:
        user_id: The user making the transaction
        cost_center_id: The cost center (None for personal)
        amount: The transaction amount (positive/negative)

    Returns:
        The balance after this transaction would be applied
    """
    try:
        if cost_center_id is None:
            # Personal transaction: use user wallet balance
            wallet = (await db.execute(select(UserWallet).where(UserWallet.user_id == user_id))).scalar_one_or_none()
            if wallet is None:
                return float(amount)
            return float(wallet.balance) + amount
        else:
            # Cost-center transaction: sum of ALL transactions for this cost center (global, not per-user)
            result = await db.execute(
                select(func.coalesce(func.sum(WalletTransaction.amount), 0.0)).where(
                    WalletTransaction.cost_center_id == cost_center_id,
                    WalletTransaction.is_voided.is_(False),
                )
            )
            current_balance = float(result.scalar() or 0.0)
            return current_balance + amount
    except SQLAlchemyError as e:
        logger.error(f"Database error in _get_balance_after_for_transaction: {e}", exc_info=True)
        raise


def _calculate_partial_charge(
    archive: PrintArchive,
    base_cost: float,
    *,
    filament_usage: tuple[float | None, float | None] | None = None,
    status: str | None = None,
    partial_filament: str = "proportional",
) -> tuple[float, str]:
    """Calculate proportional charge for partial prints based on filament usage.

    ``status`` is how the charged run ended; without it, the archive's.
    ``partial_filament`` is the policy for a run that did not finish (#3581):
    its share of the filament as below, the whole print (``full``) or nothing
    (``none``).

    Returns (charge_amount, description_suffix) where:
    - charge_amount: absolute cost to charge (0 if insufficient data)
    - description_suffix: reason/details for transaction description
    """
    try:
        status = status or archive.status
        # Only apply proportional calculation for non-completed prints
        if status == "completed":
            return round(float(base_cost), 2), ""

        if partial_filament == "full":
            return round(float(base_cost), 2), f"[{status}: full filament]"
        if partial_filament == "none":
            return 0.0, f"[{status}: filament not charged]"

        if filament_usage is not None:
            actual_grams, planned_grams = filament_usage
            filament_used = float(actual_grams or 0.0)
            filament_planned = float(planned_grams) if planned_grams is not None else None
        else:
            # Backwards-compatible fallback for recalculation and callers that
            # do not have per-run telemetry. At print completion main.py passes
            # the measured/progress-scaled run usage explicitly: the archive
            # field is the slicer's planned amount and must not be mistaken for
            # the amount consumed by an aborted run.
            filament_used = float(archive.filament_used_grams or 0.0)
            filament_planned = None

            if archive.extra_data and isinstance(archive.extra_data, dict):
                filament_planned = archive.extra_data.get("filament_grams_total")
                if filament_planned is not None:
                    filament_planned = float(filament_planned)

        # If we don't have reliable planned filament data, do not guess a partial charge.
        # Charging a failed/aborted print without an estimated baseline can overcharge users.
        if filament_planned is None or filament_planned <= 0:
            return 0.0, f"[{status}: insufficient filament data]"

        # Calculate proportional cost
        filament_ratio = min(1.0, max(0.0, filament_used / filament_planned))  # Clamp to [0, 1]
        charge = float(base_cost) * filament_ratio

        # Round charges to 2 decimals for consistent persistence
        charge = round(charge, 2)

        suffix = f"[{status}: {filament_ratio:.1%} filament ({filament_used:.1f}g/{filament_planned:.1f}g)]"
        return charge, suffix
    except ValueError as e:
        logger.error(f"Value error in _calculate_partial_charge: {e}", exc_info=True)
        raise


PARTIAL_FILAMENT_MODES = ("proportional", "full", "none")


class ChargePolicy(NamedTuple):
    """What the wallet charge of one run is made of (#3581).

    ``partial_filament`` is how a run that did not finish is charged for its
    filament: its share of the planned filament (``proportional``), the whole
    print (``full``) or nothing (``none``). A completed run is always charged
    its filament. ``energy`` and ``wear`` say whether the run's energy and
    printer wear are added.

    The default is the charge as it was before the policy: the filament alone,
    proportional for a run that did not finish.
    """

    partial_filament: str = "proportional"
    energy: bool = False
    wear: bool = False


# The policy's settings: one set for completed runs, one for failed and
# cancelled ones. Global; a cost centre's own would be resolved here too.
_COMPLETED_KEYS = ("billing_completed_energy", "billing_completed_wear")
_PARTIAL_KEYS = ("billing_failed_energy", "billing_failed_wear")


async def print_charge_policy(db: AsyncSession, run_status: str) -> ChargePolicy:
    """The charging policy for a run that ended as ``run_status``, read when
    it ends. The default policy while billing is off, and for any setting
    never saved or holding a value it does not know."""
    completed = run_status == "completed"
    energy_key, wear_key = _COMPLETED_KEYS if completed else _PARTIAL_KEYS
    rows = await db.execute(
        select(Settings.key, Settings.value).where(
            Settings.key.in_(("billing_enabled", "billing_failed_filament", energy_key, wear_key))
        )
    )
    values = {key: (value or "").strip().lower() for key, value in rows.all()}
    if values.get("billing_enabled") != "true":
        return ChargePolicy()
    mode = values.get("billing_failed_filament")
    return ChargePolicy(
        partial_filament=mode if not completed and mode in PARTIAL_FILAMENT_MODES else "proportional",
        energy=values.get(energy_key) == "true",
        wear=values.get(wear_key) == "true",
    )


def defer_print_charge(run: PrintLogEntry, archive_id: int, charge: dict) -> None:
    """Keep a run's charge on the run until its energy is known (#3581).

    ``charge`` is what ``apply_print_charge_for_archive`` takes for it, apart
    from the energy. On the run row, so the charge outlives a restart: the
    task that reads the plug writes it (``apply_pending_print_charge``), and
    startup writes the ones a restart interrupted
    (``recover_pending_print_charges``). ``archive_id`` is kept for the log
    only: the charge is written for the archive the run points at when it is
    written. Does not commit.

    A charge already waiting on the run stays as it is: a second end of the
    same run (its real end, then a reconnect closing it again) knows less of
    it, not who queued it nor its cost centre, and would charge someone else.
    """
    if run.pending_charge:
        logger.info("Run %s: a print charge is already waiting on it and is kept.", run.id)
        return
    run.pending_charge = json.dumps({"archive_id": archive_id, **charge})


# Notes kept with a waiting charge, beside what the charge takes.
_ENERGY_NOT_TRUSTED = "energy_not_trusted"
_FAILURE_REPORTED = "failure_reported"


def _pending_charge_has(run, note: str) -> bool:
    try:
        return bool(dict(json.loads(run.pending_charge or "")).get(note))
    except (TypeError, ValueError):
        return False


def _note_on_pending_charge(run: PrintLogEntry, note: str) -> None:
    """Set ``note`` on the run's waiting charge, if it has one."""
    try:
        charge = dict(json.loads(run.pending_charge or ""))
    except (TypeError, ValueError):
        return
    charge[note] = True
    run.pending_charge = json.dumps(charge)


def distrust_pending_charge_energy(run: PrintLogEntry) -> None:
    """The energy just worked out for the run is not one to charge (its end
    reading is not from the plug that gave its start reading): its waiting
    charge is written without energy, also by a startup that finds the figure
    saved on the run. Does not commit."""
    _note_on_pending_charge(run, _ENERGY_NOT_TRUSTED)


async def report_print_charge_failed(run, error: Exception | str) -> None:
    """The billing-failed alert for a run's charge that could not be written
    after its print's end (#3581): to the interface now, and to the
    notification providers in the background, in a session of their own (the
    caller's transaction may still be open). ``run`` is the run or what it
    was. Never raises."""
    from backend.app.core.tasks import spawn_background_task

    printer_id, archive_id = run.printer_id, run.archive_id
    printer_name = run.printer_name or f"Printer {printer_id}"
    filename = run.print_name or "Unknown"
    reason = str(error)

    async def _notify() -> None:
        try:
            from backend.app.core.database import async_session
            from backend.app.services.notification_service import notification_service

            async with async_session() as db:
                await notification_service.on_billing_charge_failed(
                    printer_id, printer_name, filename, archive_id, reason, db
                )
        except Exception:  # noqa: BLE001 -- an alert that fails has nobody left to tell
            logger.error("Run %s: could not send the alert for its failed print charge", run.id, exc_info=True)

    try:
        from backend.app.core.websocket import ws_manager

        await ws_manager.broadcast(
            {
                "type": "billing_charge_failed",
                "printer_id": printer_id,
                "printer_name": printer_name,
                "filename": filename,
                "archive_id": archive_id,
            }
        )
    except Exception:  # noqa: BLE001 -- the providers are still told
        logger.error("Run %s: could not broadcast its failed print charge", run.id, exc_info=True)
    spawn_background_task(_notify(), name=f"billing-charge-failed-run-{run.id}")


async def _drop_missing_charge_targets(db: AsyncSession, run: PrintLogEntry, charge: dict) -> None:
    """Drop the queue item and the cost centre a waiting charge was kept
    with when they were deleted while it waited: the wallet transaction
    points at both, and PostgreSQL refused it for either (foreign key), at
    every retry and every startup. The run's own cost centre too, which the
    charge falls back on. Without its queue item the charge settles the
    archive's reservation; who pays stays who paid with it
    (``apply_pending_print_charge``)."""
    from backend.app.models.finance import CostCenter
    from backend.app.models.print_queue import PrintQueueItem

    queue_id = charge.get("print_queue_id")
    if queue_id is not None and await db.scalar(select(PrintQueueItem.id).where(PrintQueueItem.id == queue_id)) is None:
        logger.info("Run %s: queue item %s of its waiting charge is gone; charged without it.", run.id, queue_id)
        charge["print_queue_id"] = None
    for cost_center_id in {charge.get("cost_center_id"), run.cost_center_id} - {None}:
        if await db.scalar(select(CostCenter.id).where(CostCenter.id == cost_center_id)) is None:
            logger.info(
                "Run %s: cost centre %s of its waiting charge is gone; charged without it.", run.id, cost_center_id
            )
            if charge.get("cost_center_id") == cost_center_id:
                charge["cost_center_id"] = None
            if run.cost_center_id == cost_center_id:
                run.cost_center_id = None


async def apply_pending_print_charge(db: AsyncSession, run_id: int, *, energy_cost: float | None = None) -> bool:
    """Write the charge a run kept while waiting for its energy (#3581).

    ``energy_cost`` is the run's, just read. Without it, the one already
    saved on the run (a restart after the energy was saved and before the
    charge), and with none the charge is written without energy and says so.
    So is one whose energy was marked as not to be charged
    (``distrust_pending_charge_energy``).
    Cleared in the caller's transaction, with the charge: an error leaves it
    in place for the next startup. Returns True when a wallet transaction was
    created. Does not commit.

    The charge is for the archive the run points at now (a merge moves a run
    to another archive). With that archive gone there is nothing left to
    charge: the print's reservation is released, the charge stops waiting and
    the billing-failed alert goes out (``report_print_charge_failed``).
    """
    run = (
        await db.execute(
            select(PrintLogEntry)
            .where(PrintLogEntry.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True, **{INCLUDE_OPEN_RUNS: True})
        )
    ).scalar_one_or_none()
    if run is None:
        # Deleted meanwhile. The paths that delete runs settle a waiting
        # charge first (``settle_pending_print_charges``); one that reaches
        # here was lost with its row.
        logger.warning("Run %s is gone: a print charge that may have been waiting on it was not written.", run_id)
        return False
    if not run.pending_charge:
        return False
    try:
        charge = dict(json.loads(run.pending_charge))
        kept_archive_id = charge.pop("archive_id", None)
        energy_not_trusted = bool(charge.pop(_ENERGY_NOT_TRUSTED, False))
        charge.pop(_FAILURE_REPORTED, None)
    except (TypeError, ValueError):
        logger.error("Run %s: its pending charge cannot be read and is dropped: %r", run_id, run.pending_charge)
        run.pending_charge = None
        return False
    archive_id = run.archive_id
    if archive_id is not None:
        archive_id = await db.scalar(select(PrintArchive.id).where(PrintArchive.id == archive_id))
    if archive_id is None:
        # No archive, no charge: what the print reserved is given back rather
        # than held for good. By its queue item: the reservation lost its
        # archive with the archive.
        for queue_id in {charge.get("print_queue_id"), run.queue_item_id} - {None}:
            await release_budget_reservation(db, source_type="print_queue", source_id=queue_id)
        run.pending_charge = None
        logger.error(
            "Run %s: archive %s is gone, so its waiting print charge was not written.", run_id, kept_archive_id
        )
        await report_print_charge_failed(
            run, f"The print's archive was deleted before its charge was written (run {run_id})."
        )
        return False
    # Kept with the charge, the queue item's id also says the print was a
    # queued one once the item is deleted (by hand, or with its archive: a
    # soft delete removes the archive's queue items). Read before the id is
    # dropped: such a print stays charged as a queued one.
    queued = charge.get("print_queue_id") is not None
    await _drop_missing_charge_targets(db, run, charge)
    usage = charge.pop("filament_usage", None)
    if energy_not_trusted:
        energy_cost = None
    elif energy_cost is None:
        energy_cost = run.energy_cost
    charged = await apply_print_charge_for_archive(
        db,
        archive_id,
        **charge,
        queued=queued,
        filament_usage=(usage[0], usage[1]) if usage else None,
        energy_cost=energy_cost,
        energy_unavailable=energy_cost is None,
    )
    run.pending_charge = None
    return charged


async def settle_pending_print_charges(db: AsyncSession, *conditions) -> int:
    """Write the waiting charges of the runs matching ``conditions``, which
    are about to be deleted or to lose their archive: a charge kept on a run
    would go with its row, and has nothing to charge without its archive.
    With the energy already saved on the run, without it otherwise. One that
    fails is logged and reported (the billing-failed alert) and does not stop
    the delete; what its print reserved is released, since nothing is left to
    consume it once the run is gone. In the caller's transaction; does not
    commit. Returns how many were waiting."""
    runs = list(
        await db.scalars(
            select(PrintLogEntry)
            .where(PrintLogEntry.pending_charge.is_not(None), *conditions)
            .order_by(PrintLogEntry.id)
            .execution_options(**{INCLUDE_OPEN_RUNS: True})
        )
    )
    # What they were: a failed settle rolls back to its savepoint, and the
    # session expires the rows with it.
    waiting = [
        SimpleNamespace(
            id=run.id,
            archive_id=run.archive_id,
            printer_id=run.printer_id,
            printer_name=run.printer_name,
            print_name=run.print_name,
            queue_ids=_pending_charge_queue_ids(run),
        )
        for run in runs
    ]
    for run in waiting:
        try:
            # A savepoint each: one that fails takes back only its own
            # writes, not the charges settled before it or the caller's work.
            async with db.begin_nested():
                await apply_pending_print_charge(db, run.id)
        except Exception as error:  # noqa: BLE001 -- the delete was asked for; the lost charge is reported
            logger.error(
                "Run %s is being deleted and its waiting print charge could not be written", run.id, exc_info=True
            )
            await report_print_charge_failed(run, error)
            try:
                async with db.begin_nested():
                    await _release_unsettled_reservation(db, run)
            except Exception:  # noqa: BLE001 -- as above: the delete goes on
                logger.error("Run %s: could not release what its print reserved", run.id, exc_info=True)
    return len(waiting)


def _pending_charge_queue_ids(run: PrintLogEntry) -> set[int]:
    """The queue items a run's waiting charge may hold a reservation by."""
    try:
        kept = dict(json.loads(run.pending_charge or "")).get("print_queue_id")
    except (TypeError, ValueError):
        kept = None
    return {queue_id for queue_id in (kept, run.queue_item_id) if isinstance(queue_id, int)}


async def _release_unsettled_reservation(db: AsyncSession, run) -> None:
    """Give back what a print reserved when its waiting charge could not be
    written and its run is going: held on, it would count against the cost
    centre's budget for good and keep the cost centre from being deleted.
    As a charge releases it (``_settle_reservation``): by the print's queue
    item, without one by its archive."""
    if run.queue_ids:
        for queue_id in run.queue_ids:
            await release_budget_reservation(db, source_type="print_queue", source_id=queue_id)
    elif run.archive_id is not None:
        await release_budget_reservation(db, print_archive_id=run.archive_id)


async def recover_pending_print_charges(session_factory, on_failure=None) -> int:
    """Write the charges a restart interrupted: the print had ended, and the
    backend stopped before its energy was read and its charge written
    (#3581). Each in its own transaction; one that fails stays for the next
    startup and is passed to ``on_failure(run, error)``, the billing-failed
    alert. Once: a charge that fails at every start (its error is permanent)
    is tried and logged each time, and reported the first. Returns how many
    were settled."""
    async with session_factory() as db:
        runs = list(
            await db.scalars(
                select(PrintLogEntry)
                .where(PrintLogEntry.pending_charge.is_not(None))
                .order_by(PrintLogEntry.id)
                .execution_options(**{INCLUDE_OPEN_RUNS: True})
            )
        )
        db.expunge_all()
    settled = 0
    for run in runs:
        try:
            async with session_factory() as db:
                charged = await apply_pending_print_charge(db, run.id)
                await db.commit()
            settled += 1
            logger.info("Run %s: settled the print charge a restart interrupted (charged: %s).", run.id, charged)
        except Exception as error:  # noqa: BLE001 -- one run's failure must not keep the others, or startup
            logger.error(
                "Run %s: could not write its pending print charge; kept for the next start", run.id, exc_info=True
            )
            if on_failure is None or _pending_charge_has(run, _FAILURE_REPORTED):
                continue
            try:
                await on_failure(run, error)
                async with session_factory() as db:
                    kept = await db.scalar(
                        select(PrintLogEntry)
                        .where(PrintLogEntry.id == run.id)
                        .execution_options(**{INCLUDE_OPEN_RUNS: True})
                    )
                    if kept is not None:
                        _note_on_pending_charge(kept, _FAILURE_REPORTED)
                        await db.commit()
            except Exception:  # noqa: BLE001 -- an alert that fails must not stop startup
                logger.error("Run %s: could not report its failed print charge", run.id, exc_info=True)
    return settled


def _charge_breakdown(
    filament_charge: float,
    wear_cost: float | None,
    energy_cost: float | None,
    energy_unavailable: bool,
    filament_note: str | None = None,
) -> tuple[float, str | None]:
    """The amount charged and how it is made up, for the description (#3581):
    ``(filament 1.20 + energy 0.08 + wear 0.25)``.

    The filament's own charge with no text when nothing else is charged or
    said: the charge and its description are then as before. A part that is
    nothing is left out. ``filament_note`` says what the policy did with a
    run's filament when that is not its proportional share
    (``filament charged in full``, ``filament not charged``), ``energy_unavailable``
    that the energy was to be included and could not be read.

    Each part is rounded to cents before it is added, so the parts listed add
    up to the amount charged.
    """
    parts = [("filament", round(float(filament_charge), 2))] if filament_charge > 0 else []
    extras = 0
    for name, value in (("energy", energy_cost), ("wear", wear_cost)):
        if isinstance(value, int | float) and not isinstance(value, bool) and round(float(value), 2) > 0:
            parts.append((name, round(float(value), 2)))
            extras += 1
    notes = [note for note in (filament_note, "energy not available" if energy_unavailable else None) if note]
    total = round(sum(value for _, value in parts), 2)
    if not extras and not notes:
        return total, None
    text = "; ".join([" + ".join(f"{name} {value:.2f}" for name, value in parts), *notes] if parts else notes)
    return total, f"({text})"


async def _settle_reservation(db: AsyncSession, status: str, *, archive_id: int, print_queue_id: int | None) -> None:
    """Release or consume what the charged print reserved. Reservations are
    made per queue item. Without the print's own (no queue item, or its item
    closed already, e.g. cancelled by a stop before the printer reported the
    start) the archive's whose items are over, never those of another plate
    of it still pending or printing."""
    if print_queue_id is not None:
        await release_budget_reservation(db, source_type="print_queue", source_id=print_queue_id, status=status)
    else:
        await release_budget_reservation(db, print_archive_id=archive_id, status=status)


async def apply_print_charge_for_archive(
    db: AsyncSession,
    archive_id: int,
    *,
    charged_user_id: int | None = None,
    cost_center_id: int | None = None,
    print_queue_id: int | None = None,
    print_run_id: str | None = None,
    base_cost_override: float | None = None,
    filament_usage: tuple[float | None, float | None] | None = None,
    run_entry_id: int | None = None,
    run_status: str | None = None,
    wear_cost: float | None = None,
    energy_cost: float | None = None,
    energy_unavailable: bool = False,
    partial_filament: str = "proportional",
    queued: bool = False,
) -> bool:
    """Apply an idempotent wallet charge for a print archive.

    Charges completed prints at full cost, and partial/failed prints proportionally
    based on actual filament used vs. planned filament.

    ``wear_cost`` and ``energy_cost`` are the run's printer wear and energy,
    passed only when the charging policy includes them (#3581). They are the
    run's actual values and are added as they are, also to a partial charge,
    and listed in the description. ``energy_unavailable`` says the energy was
    to be included and could not be read: the charge is written without it
    and says so. ``partial_filament`` is the policy for the filament of a run
    that did not finish (``_calculate_partial_charge``).

    ``queued`` says the print came from a queue item, for one whose
    ``print_queue_id`` is no longer passed: the item was deleted while the
    charge waited for its energy. It is then charged to whom it was with the
    item, not to the archive's creator and cost centre.

    A run that is charged nothing for its filament (a print with no filament
    cost, on free spools, or one that did not finish under a policy that does
    not charge it or without filament data) is still charged the energy and
    wear the policy includes, however it ended. With neither, nothing is
    charged and its reservation is released, as before.

    Returns True when a new wallet transaction was created.
    """
    try:
        if not await is_billing_enabled(db):
            await _settle_reservation(db, "released", archive_id=archive_id, print_queue_id=print_queue_id)
            logger.info("Billing is disabled; skipping print charge for archive ID %s.", archive_id)
            return False

        run = None
        if run_entry_id is not None:
            # Locked, and read again under the lock (the caller loaded it):
            # two ends of the same run (a repeated completion) mint and charge
            # its billing run id once.
            run = (
                await db.execute(
                    select(PrintLogEntry)
                    .where(PrintLogEntry.id == run_entry_id)
                    .with_for_update()
                    .execution_options(populate_existing=True, **{INCLUDE_OPEN_RUNS: True})
                )
            ).scalar_one_or_none()

        archive_query = select(PrintArchive).where(PrintArchive.id == archive_id)
        if run is None:
            archive_query = archive_query.with_for_update()
        archive = (await db.execute(archive_query)).scalar_one_or_none()
        if archive is None:
            logger.warning(f"Archive with ID {archive_id} not found.")
            return False

        if run is not None:
            # A run opened at its start has its own id. One written only at
            # its end (no start seen) has none, and is charged as before by the
            # archive's, which a repeated end then finds charged.
            effective_run_id = print_run_id or run.billing_run_id or archive.billing_run_id
            skipped = bool(run.wallet_charge_skipped)
        else:
            effective_run_id = print_run_id or archive.billing_run_id
            # The archive-level flag is retained only for legacy deleted charges.
            # A new scheduler dispatch clears it while persisting its new run UUID;
            # current deletions are represented by a voided transaction instead.
            skipped = archive.wallet_charge_skipped
        if skipped:
            logger.info(f"Wallet charge skipped for archive ID {archive_id}.")
            return False

        # Accept completed, aborted, cancelled, and failed prints. A run's own
        # end: with another plate of the archive ending at the same time, the
        # archive's status may be that one's.
        charged_status = run_status or archive.status
        if charged_status not in ("completed", "aborted", "cancelled", "failed"):
            logger.info(f"Archive ID {archive_id} has status {charged_status}, which is not chargeable.")
            return False

        # A queue print's user is the one who queued it, also when deleted
        # since: a reprint reuses the source archive, whose creator did not
        # print it. The archive's creator only for a print without a queue row.
        if charged_user_id is not None:
            # Read by the caller seconds before (the completion does usage and
            # archive work first): a user deleted since is no user. Charged by
            # id, PostgreSQL refused the wallet (FK) and the reservation stayed
            # active; SQLite wrote a wallet and a charge for a missing user.
            from backend.app.models.user import User

            actual_user_id = await db.scalar(select(User.id).where(User.id == charged_user_id))
        elif print_queue_id is not None or queued:
            actual_user_id = None
        else:
            actual_user_id = archive.created_by_id
        if cost_center_id is not None:
            actual_cost_center_id = cost_center_id
        elif run is not None and (run.cost_center_id is not None or run.queue_item_id is not None or queued):
            # A queued print's is its queue item's, none included: the
            # archive's is another print's when the same file prints on
            # several printers at once (archive redesign).
            actual_cost_center_id = run.cost_center_id
        else:
            actual_cost_center_id = archive.cost_center_id
        # No user: deleted while the print ran, or never had one (login turned
        # off). A cost centre's print is still its cost: charged to the cost
        # centre without a user (shown as "No user"), which also consumes its
        # reservation. That includes
        # the user's own private cost centre, which their personal prints go
        # through with billing on and which outlives them. Only a print with no
        # cost centre at all (started while billing was off) has nobody left to
        # charge, and holds no reservation (budgets exist per cost centre).
        if actual_user_id is None and actual_cost_center_id is None:
            logger.info("Archive ID %s has no user to charge (deleted) and no cost centre.", archive_id)
            return False

        base_cost = float(base_cost_override if base_cost_override is not None else (archive.cost or 0.0))
        # Energy and wear are a charge on their own, however the run ended:
        # a print on free filament still used the printer and the power.
        extras_only = _charge_breakdown(0.0, wear_cost, energy_cost, False)[0] > 0
        if base_cost <= 0 and not extras_only:
            # Nothing to charge, but the print is over: settle what it held
            # (reserved from an estimate, e.g. an older archive with grams but
            # no stored cost), as the zero-charge case below does.
            await _settle_reservation(db, "released", archive_id=archive.id, print_queue_id=print_queue_id)
            logger.info(f"Base cost for archive ID {archive_id} is zero or negative.")
            return False

        # New dispatches persist a UUID before sending the printer command,
        # and a run gets its own when it opens. Generate one here only for
        # legacy/in-flight rows created before that; the locked row makes this
        # fallback durable.
        if not effective_run_id:
            effective_run_id = str(uuid.uuid4())
            if run is None:
                archive.billing_run_id = effective_run_id
        if run is not None and run.billing_run_id != effective_run_id:
            run.billing_run_id = effective_run_id

        tx_conditions = [
            WalletTransaction.transaction_type == TransactionType.PRINT_CHARGE.value,
            WalletTransaction.print_run_id == effective_run_id,
        ]

        existing_tx = (await db.execute(select(WalletTransaction).where(*tx_conditions))).scalar_one_or_none()
        if existing_tx is not None:
            if existing_tx.print_archive_id != archive.id:
                logger.critical(
                    "BILLING RUN ID COLLISION: run %s belongs to archive %s, not archive %s; charge aborted",
                    effective_run_id,
                    existing_tx.print_archive_id,
                    archive.id,
                )
                raise BillingRunIdCollisionError(
                    f"Billing run ID {effective_run_id} is already assigned to another archive"
                )
            logger.info(f"Transaction already exists for archive ID {archive_id}.")
            if existing_tx.is_voided:
                logger.info("Print charge for run %s was voided by an administrator.", effective_run_id)
            return False

        # Calculate charge (full for completed, partial for others)
        charge, reason_suffix = _calculate_partial_charge(
            archive,
            max(base_cost, 0.0),
            filament_usage=filament_usage,
            status=charged_status,
            partial_filament=partial_filament,
        )
        filament_note = None
        if charged_status != "completed" and partial_filament != "proportional":
            filament_note = "filament charged in full" if partial_filament == "full" else "filament not charged"
        breakdown_text = None
        if charge > 0 or extras_only:
            charge, breakdown_text = _charge_breakdown(
                charge, wear_cost, energy_cost, energy_unavailable, filament_note
            )
        if charge <= 0:
            await _settle_reservation(db, "released", archive_id=archive.id, print_queue_id=print_queue_id)
            logger.info(f"Calculated charge for archive ID {archive_id} is zero or negative.")
            return False

        wallet = None
        if actual_user_id is not None:
            wallet = (
                await db.execute(select(UserWallet).where(UserWallet.user_id == actual_user_id))
            ).scalar_one_or_none()
            if wallet is None:
                wallet = UserWallet(user_id=actual_user_id, balance=0.0)
                db.add(wallet)
                await db.flush()
                logger.info("Created new wallet for user ID %s.", actual_user_id)

        label = archive.print_name or archive.filename or f"Archive {archive.id}"
        if breakdown_text:
            label = f"{label} {breakdown_text}"
        description = f"Print charge: {label}{' ' + reason_suffix if reason_suffix else ''}"

        balance_after = await _get_balance_after_for_transaction(db, actual_user_id, actual_cost_center_id, -charge)
        if balance_after is not None:
            balance_after = round(float(balance_after), 2)

        tx = WalletTransaction(
            user_id=actual_user_id,
            cost_center_id=actual_cost_center_id,
            transaction_type=TransactionType.PRINT_CHARGE.value,
            amount=-charge,
            balance_after=balance_after,
            description=description,
            created_by_user_id=None,
            print_run_id=effective_run_id,
            print_archive_id=archive.id,
            print_queue_id=print_queue_id,
        )
        # Limit a concurrent deduplication conflict to a savepoint. The caller
        # owns the outer transaction, which may already contain archive-owner
        # backfills and other completion updates that must survive this race.
        try:
            async with db.begin_nested():
                db.add(tx)
                # Flush inside the savepoint to detect unique/index conflicts.
                await db.flush()
        except IntegrityError as e:
            # Distinguish a legitimate concurrent retry of this exact run from
            # a collision or an unrelated constraint failure. Only the former
            # is an idempotent no-op; everything else must remain loud so the
            # caller rolls back and the budget reservation stays active.
            concurrent_tx = (await db.execute(select(WalletTransaction).where(*tx_conditions))).scalar_one_or_none()
            if concurrent_tx is not None and concurrent_tx.print_archive_id == archive.id:
                logger.info("Transaction already exists for archive ID %s (concurrent), skipping", archive_id)
                return False
            logger.critical(
                "Failed to persist billing charge for archive %s and run %s: %s",
                archive_id,
                effective_run_id,
                e,
                exc_info=True,
            )
            if concurrent_tx is not None:
                raise BillingRunIdCollisionError(
                    f"Billing run ID {effective_run_id} is already assigned to another archive"
                ) from e
            raise

        # Rebuild from the canonical personal-ledger definition. A shared cost
        # center charge must not debit the user's personal wallet.
        new_wallet_balance = await sync_personal_wallet_balance(db, wallet) if wallet is not None else None

        # Consume matching budget reservations after the transaction is persisted
        await _settle_reservation(db, "consumed", archive_id=archive.id, print_queue_id=print_queue_id)
        logger.info(f"Applied print charge for archive ID {archive_id}. New balance: {new_wallet_balance}.")
        return True
    except SQLAlchemyError as e:
        logger.error(f"Database error in apply_print_charge_for_archive: {e}", exc_info=True)
        raise
    except ValueError as e:
        logger.error(f"Value error in apply_print_charge_for_archive: {e}", exc_info=True)
        return False
