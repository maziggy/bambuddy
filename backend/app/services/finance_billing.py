import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.archive import PrintArchive
from backend.app.models.finance import TransactionType, UserWallet, WalletTransaction
from backend.app.models.print_log import INCLUDE_OPEN_RUNS, PrintLogEntry
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
) -> tuple[float, str]:
    """Calculate proportional charge for partial prints based on filament usage.

    ``status`` is how the charged run ended; without it, the archive's.

    Returns (charge_amount, description_suffix) where:
    - charge_amount: absolute cost to charge (0 if insufficient data)
    - description_suffix: reason/details for transaction description
    """
    try:
        status = status or archive.status
        # Only apply proportional calculation for non-completed prints
        if status == "completed":
            return round(float(base_cost), 2), ""

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
) -> bool:
    """Apply an idempotent wallet charge for a print archive.

    Charges completed prints at full cost, and partial/failed prints proportionally
    based on actual filament used vs. planned filament.

    ``run_entry_id`` is the run being charged (its print log entry) and
    ``run_status`` how it ended. The charge is then the run's: its billing run
    id and cost centre, under a lock on the run row, so plates of
    one archive printing on two printers are each charged once. Without a run
    (a print started before runs were written at the start) the archive's.

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
        elif print_queue_id is not None:
            actual_user_id = None
        else:
            actual_user_id = archive.created_by_id
        if cost_center_id is not None:
            actual_cost_center_id = cost_center_id
        elif run is not None and (run.cost_center_id is not None or run.queue_item_id is not None):
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
        if base_cost <= 0:
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
            base_cost,
            filament_usage=filament_usage,
            status=charged_status,
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
