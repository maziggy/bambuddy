import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.archive import PrintArchive
from backend.app.models.finance import TransactionType, UserWallet, WalletTransaction
from backend.app.services.finance_budget import is_billing_enabled, release_budget_reservation

logger = logging.getLogger(__name__)


async def _get_balance_after_for_transaction(
    db: AsyncSession,
    user_id: int,
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
) -> tuple[float, str]:
    """Calculate proportional charge for partial prints based on filament usage.

    Returns (charge_amount, description_suffix) where:
    - charge_amount: absolute cost to charge (0 if insufficient data)
    - description_suffix: reason/details for transaction description
    """
    try:
        # Only apply proportional calculation for non-completed prints
        if archive.status == "completed":
            return round(float(base_cost), 2), ""

        filament_used = float(archive.filament_used_grams or 0.0)
        filament_planned = None

        if archive.extra_data and isinstance(archive.extra_data, dict):
            filament_planned = archive.extra_data.get("filament_grams_total")
            if filament_planned is not None:
                filament_planned = float(filament_planned)

        # If we don't have reliable planned filament data, do not guess a partial charge.
        # Charging a failed/aborted print without an estimated baseline can overcharge users.
        if filament_planned is None or filament_planned <= 0:
            return 0.0, f"[{archive.status}: insufficient filament data]"

        # Calculate proportional cost
        filament_ratio = min(1.0, max(0.0, filament_used / filament_planned))  # Clamp to [0, 1]
        charge = float(base_cost) * filament_ratio

        # Round charges to 2 decimals for consistent persistence
        charge = round(charge, 2)

        suffix = f"[{archive.status}: {filament_ratio:.1%} filament ({filament_used:.1f}g/{filament_planned:.1f}g)]"
        return charge, suffix
    except ValueError as e:
        logger.error(f"Value error in _calculate_partial_charge: {e}", exc_info=True)
        raise


async def apply_print_charge_for_archive(
    db: AsyncSession,
    archive_id: int,
    *,
    cost_center_id: int | None = None,
    print_run_id: str | None = None,
) -> bool:
    """Apply an idempotent wallet charge for a print archive.

    Charges completed prints at full cost, and partial/failed prints proportionally
    based on actual filament used vs. planned filament.

    Returns True when a new wallet transaction was created.
    """
    try:
        if not await is_billing_enabled(db):
            await release_budget_reservation(db, print_archive_id=archive_id, status="released")
            logger.info("Billing is disabled; skipping print charge for archive ID %s.", archive_id)
            return False

        archive = (
            await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id).with_for_update())
        ).scalar_one_or_none()
        if archive is None:
            logger.warning(f"Archive with ID {archive_id} not found.")
            return False

        if archive.wallet_charge_skipped:
            logger.info(f"Wallet charge skipped for archive ID {archive_id}.")
            return False

        # Accept completed, aborted, cancelled, and failed prints
        if archive.status not in ("completed", "aborted", "cancelled", "failed"):
            logger.info(f"Archive ID {archive_id} has status {archive.status}, which is not chargeable.")
            return False

        if archive.created_by_id is None:
            logger.warning(f"Archive ID {archive_id} has no creator ID.")
            return False

        base_cost = float(archive.cost or 0.0)
        if base_cost <= 0:
            logger.info(f"Base cost for archive ID {archive_id} is zero or negative.")
            return False

        tx_conditions = [WalletTransaction.transaction_type == TransactionType.PRINT_CHARGE.value]
        if print_run_id:
            tx_conditions.append(WalletTransaction.print_run_id == print_run_id)
        else:
            tx_conditions.append(WalletTransaction.print_archive_id == archive.id)

        existing_tx = (await db.execute(select(WalletTransaction).where(*tx_conditions))).scalar_one_or_none()
        if existing_tx is not None:
            logger.info(f"Transaction already exists for archive ID {archive_id}.")
            return False

        # Calculate charge (full for completed, partial for others)
        charge, reason_suffix = _calculate_partial_charge(archive, base_cost)
        if charge <= 0:
            await release_budget_reservation(db, print_archive_id=archive.id, status="released")
            logger.info(f"Calculated charge for archive ID {archive_id} is zero or negative.")
            return False

        actual_cost_center_id = cost_center_id if cost_center_id is not None else archive.cost_center_id

        wallet = (
            await db.execute(select(UserWallet).where(UserWallet.user_id == archive.created_by_id))
        ).scalar_one_or_none()
        if wallet is None:
            wallet = UserWallet(user_id=archive.created_by_id, balance=0.0, currency="EUR")
            db.add(wallet)
            await db.flush()
            logger.info(f"Created new wallet for user ID {archive.created_by_id}.")

        # Persist wallet balances rounded to cents
        new_wallet_balance = round(float(wallet.balance) - charge, 2)
        wallet.balance = new_wallet_balance

        label = archive.print_name or archive.filename or f"Archive {archive.id}"
        description = f"Print charge: {label}{' ' + reason_suffix if reason_suffix else ''}"

        balance_after = await _get_balance_after_for_transaction(
            db, archive.created_by_id, actual_cost_center_id, -charge
        )
        if balance_after is not None:
            balance_after = round(float(balance_after), 2)

        tx = WalletTransaction(
            user_id=archive.created_by_id,
            cost_center_id=actual_cost_center_id,
            transaction_type=TransactionType.PRINT_CHARGE.value,
            amount=-charge,
            balance_after=balance_after,
            description=description,
            created_by_user_id=None,
            print_run_id=print_run_id or archive.subtask_id,
            print_archive_id=archive.id,
        )
        db.add(tx)
        # Ensure the transaction is flushed to detect unique/index constraint violations
        try:
            await db.flush()
        except IntegrityError as e:
            # Another concurrent worker likely created the same transaction
            logger.info("Transaction already exists for archive ID %s (concurrent), skipping: %s", archive_id, e)
            await db.rollback()
            return False

        # Consume matching budget reservations after the transaction is persisted
        await release_budget_reservation(db, print_archive_id=archive.id, status="consumed")
        logger.info(f"Applied print charge for archive ID {archive_id}. New balance: {new_wallet_balance}.")
        return True
    except SQLAlchemyError as e:
        logger.error(f"Database error in apply_print_charge_for_archive: {e}", exc_info=True)
        return False
    except ValueError as e:
        logger.error(f"Value error in apply_print_charge_for_archive: {e}", exc_info=True)
        return False
