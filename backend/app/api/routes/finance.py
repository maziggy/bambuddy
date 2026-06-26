import calendar
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import RequirePermissionIfAuthEnabled, require_auth_if_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.archive import PrintArchive
from backend.app.models.finance import (
    BudgetReservation,
    CostCenter,
    CostCenterMember,
    TransactionType,
    UserWallet,
    WalletTransaction,
    normalize_transaction_type,
)
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.schemas.finance import (
    CostCenterBudgetUpdateRequest,
    CostCenterCreateRequest,
    CostCenterDetailResponse,
    CostCenterMemberRequest,
    CostCenterMemberResponse,
    CostCenterSummaryResponse,
    CostCenterUpdateRequest,
    ManualPrintRequest,
    TransactionEditRequest,
    WalletAdjustmentRequest,
    WalletAdjustmentResponse,
    WalletBalanceResponse,
    WalletTransactionListResponse,
    WalletTransactionResponse,
)

router = APIRouter(prefix="/finance", tags=["finance"])


def _serialize_wallet_transaction(tx: WalletTransaction) -> WalletTransactionResponse:
    transaction_type = (
        tx.transaction_type.value if isinstance(tx.transaction_type, TransactionType) else tx.transaction_type
    )
    return WalletTransactionResponse.model_construct(
        id=tx.id,
        user_id=tx.user_id,
        cost_center_id=tx.cost_center_id,
        transaction_type=transaction_type,
        amount=tx.amount,
        balance_after=tx.balance_after,
        description=tx.description,
        created_by_user_id=tx.created_by_user_id,
        print_run_id=tx.print_run_id,
        print_archive_id=tx.print_archive_id,
        print_queue_id=tx.print_queue_id,
        created_at=tx.created_at,
    )


def _clamp_day(year: int, month: int, desired_day: int) -> int:
    return min(max(1, desired_day), calendar.monthrange(year, month)[1])


async def _get_budget_window_start_utc(db: AsyncSession) -> datetime:
    """Resolve monthly budget window start in UTC using configurable reset day/timezone.

    Defaults preserve current behavior: day=1, timezone=UTC.
    """
    desired_day = 1
    tz_name = "UTC"

    result = await db.execute(
        select(Settings).where(Settings.key.in_(["finance_budget_reset_day", "finance_budget_reset_timezone"]))
    )
    for setting in result.scalars().all():
        if setting.key == "finance_budget_reset_day":
            try:
                parsed = int(setting.value)
                if 1 <= parsed <= 31:
                    desired_day = parsed
            except (TypeError, ValueError):
                pass
        elif setting.key == "finance_budget_reset_timezone":
            value = (setting.value or "").strip()
            if value:
                tz_name = value

    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = timezone.utc

    now_local = datetime.now(tz)
    current_month_reset_day = _clamp_day(now_local.year, now_local.month, desired_day)

    if now_local.day >= current_month_reset_day:
        start_local = datetime(now_local.year, now_local.month, current_month_reset_day, tzinfo=tz)
    else:
        prev_year = now_local.year
        prev_month = now_local.month - 1
        if prev_month == 0:
            prev_month = 12
            prev_year -= 1
        prev_month_reset_day = _clamp_day(prev_year, prev_month, desired_day)
        start_local = datetime(prev_year, prev_month, prev_month_reset_day, tzinfo=tz)

    return start_local.astimezone(timezone.utc)


async def _get_cost_center_usage_maps(
    db: AsyncSession,
    cost_center_ids: list[int],
) -> tuple[dict[int, float], dict[int, float]]:
    if not cost_center_ids:
        return {}, {}

    spend_expr = case((WalletTransaction.amount < 0, -WalletTransaction.amount), else_=0.0)

    total_rows = await db.execute(
        select(WalletTransaction.cost_center_id, func.coalesce(func.sum(spend_expr), 0.0))
        .where(
            WalletTransaction.cost_center_id.in_(cost_center_ids),
            WalletTransaction.cost_center_id.is_not(None),
        )
        .group_by(WalletTransaction.cost_center_id)
    )

    budget_window_start_utc = await _get_budget_window_start_utc(db)

    month_rows = await db.execute(
        select(WalletTransaction.cost_center_id, func.coalesce(func.sum(spend_expr), 0.0))
        .where(
            WalletTransaction.cost_center_id.in_(cost_center_ids),
            WalletTransaction.cost_center_id.is_not(None),
            WalletTransaction.created_at >= budget_window_start_utc,
        )
        .group_by(WalletTransaction.cost_center_id)
    )

    total_map = {int(center_id): float(value) for center_id, value in total_rows.all() if center_id is not None}
    month_map = {int(center_id): float(value) for center_id, value in month_rows.all() if center_id is not None}
    return total_map, month_map


async def _get_cost_center_balance_map(
    db: AsyncSession,
    cost_center_ids: list[int],
) -> dict[int, float]:
    if not cost_center_ids:
        return {}

    rows = await db.execute(
        select(WalletTransaction.cost_center_id, func.coalesce(func.sum(WalletTransaction.amount), 0.0))
        .where(
            WalletTransaction.cost_center_id.in_(cost_center_ids),
            WalletTransaction.cost_center_id.is_not(None),
        )
        .group_by(WalletTransaction.cost_center_id)
    )
    return {int(center_id): float(value) for center_id, value in rows.all() if center_id is not None}


async def _get_cost_center_reserved_map(
    db: AsyncSession,
    cost_center_ids: list[int],
) -> dict[int, float]:
    if not cost_center_ids:
        return {}

    budget_rows = await db.execute(
        select(BudgetReservation.cost_center_id, func.coalesce(func.sum(BudgetReservation.amount), 0.0))
        .where(
            BudgetReservation.cost_center_id.in_(cost_center_ids),
            BudgetReservation.status == "active",
        )
        .group_by(BudgetReservation.cost_center_id)
    )
    reserved_map = {int(center_id): float(value) for center_id, value in budget_rows.all() if center_id is not None}

    queue_rows = await db.execute(
        select(PrintQueueItem.cost_center_id, func.coalesce(func.sum(PrintQueueItem.estimated_cost), 0.0))
        .where(
            PrintQueueItem.cost_center_id.in_(cost_center_ids),
            PrintQueueItem.status.in_(("pending", "printing")),
        )
        .group_by(PrintQueueItem.cost_center_id)
    )
    for center_id, value in queue_rows.all():
        if center_id is not None:
            reserved_map[int(center_id)] = reserved_map.get(int(center_id), 0.0) + float(value or 0.0)
    return reserved_map


def _budget_mode_and_limit(center: CostCenter) -> tuple[str, float | None]:
    # Monthly takes precedence if legacy data still has both set.
    if center.monthly_budget is not None:
        return "monthly", float(center.monthly_budget)
    if center.total_budget is not None:
        return "total", float(center.total_budget)
    return "none", None


def _to_cost_center_summary(
    center: CostCenter,
    *,
    can_print: bool,
    total_usage: float,
    month_usage: float,
    total_balance: float,
    reserved: float = 0.0,
) -> CostCenterSummaryResponse:
    budget_mode, budget_limit = _budget_mode_and_limit(center)
    budget_used = month_usage if budget_mode == "monthly" else total_usage if budget_mode == "total" else None
    budget_available = (
        max(0.0, budget_limit - budget_used - reserved)
        if budget_limit is not None and budget_used is not None
        else None
    )

    return CostCenterSummaryResponse(
        id=center.id,
        name=center.name,
        is_private=center.is_private,
        owner_user_id=center.owner_user_id,
        is_active=center.is_active,
        total_balance=total_balance,
        total_budget=center.total_budget,
        monthly_budget=center.monthly_budget,
        budget_mode=budget_mode,
        budget_limit=budget_limit,
        budget_used=budget_used,
        budget_available=budget_available,
        can_print=can_print,
    )


async def _require_authenticated_user(current_user: User | None) -> User:
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return current_user


def _has_cost_center_admin_access(user: User) -> bool:
    return user.has_any_permission(
        Permission.COST_CENTERS_READ_ALL.value,
        Permission.COST_CENTERS_MODIFY.value,
        Permission.COST_CENTERS_CREATE.value,
    )


async def _require_cost_center_admin_access(current_user: User | None) -> User:
    user = await _require_authenticated_user(current_user)
    if not _has_cost_center_admin_access(user):
        raise HTTPException(status_code=403, detail="Missing required permissions for cost center administration")
    return user


async def _get_or_create_wallet(db: AsyncSession, user_id: int) -> UserWallet:
    result = await db.execute(select(UserWallet).where(UserWallet.user_id == user_id))
    wallet = result.scalar_one_or_none()
    if wallet:
        return wallet

    wallet = UserWallet(user_id=user_id, balance=0.0, currency="EUR")
    db.add(wallet)
    await db.flush()
    await db.refresh(wallet)
    return wallet


async def _get_user_or_404(db: AsyncSession, user_id: int) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _get_cost_center_or_404(db: AsyncSession, cost_center_id: int) -> CostCenter:
    result = await db.execute(
        select(CostCenter).options(selectinload(CostCenter.members)).where(CostCenter.id == cost_center_id)
    )
    center = result.scalar_one_or_none()
    if center is None:
        raise HTTPException(status_code=404, detail="Cost center not found")
    return center


def _to_balance_response(wallet: UserWallet) -> WalletBalanceResponse:
    return WalletBalanceResponse(
        user_id=wallet.user_id,
        balance=wallet.balance,
        currency=wallet.currency,
        updated_at=wallet.updated_at,
    )


async def _build_personal_balance_map(db: AsyncSession, user_id: int) -> dict[int, float]:
    result = await db.execute(
        select(
            WalletTransaction.id,
            WalletTransaction.amount,
            WalletTransaction.cost_center_id,
            CostCenter.is_private,
            CostCenter.owner_user_id,
        )
        .where(
            WalletTransaction.user_id == user_id,
        )
        .outerjoin(CostCenter, WalletTransaction.cost_center_id == CostCenter.id)
        .order_by(WalletTransaction.created_at.asc(), WalletTransaction.id.asc())
    )

    running_balance = 0.0
    balance_map: dict[int, float] = {}
    for transaction_id, amount, cost_center_id, is_private, owner_user_id in result.all():
        is_personal_cost_center = bool(cost_center_id is not None and is_private and owner_user_id == user_id)
        if cost_center_id is None or is_personal_cost_center:
            running_balance += float(amount)
            balance_map[int(transaction_id)] = running_balance

    return balance_map


def _personal_balance_condition(user_id: int):
    return or_(
        WalletTransaction.cost_center_id.is_(None),
        and_(CostCenter.is_private.is_(True), CostCenter.owner_user_id == user_id),
    )


async def _create_wallet_adjustment(
    db: AsyncSession,
    *,
    target_user_id: int,
    actor_user_id: int,
    amount: float,
    transaction_type: str,
    description: str | None,
    cost_center_id: int | None,
) -> WalletAdjustmentResponse:
    transaction_type = normalize_transaction_type(transaction_type)

    if cost_center_id is not None:
        await _get_cost_center_or_404(db, cost_center_id)

    wallet = await _get_or_create_wallet(db, target_user_id)

    # Calculate balance_after for this specific transaction context
    if cost_center_id is None:
        # Personal transaction: validate and update user wallet
        new_balance = wallet.balance + amount
        if new_balance < 0:
            raise HTTPException(status_code=400, detail="Insufficient balance for withdrawal")
        wallet.balance = new_balance
        balance_after = new_balance
    else:
        # Cost-center transaction: validate against cost center balance only (global, not per-user)
        result = await db.execute(
            select(func.coalesce(func.sum(WalletTransaction.amount), 0.0)).where(
                WalletTransaction.cost_center_id == cost_center_id,
            )
        )
        current_cc_balance = float(result.scalar() or 0.0)
        new_cc_balance = current_cc_balance + amount
        if new_cc_balance < 0:
            raise HTTPException(status_code=400, detail="Insufficient cost center balance for withdrawal")
        balance_after = new_cc_balance
        # Do NOT update wallet.balance for cost-center transactions

    tx = WalletTransaction(
        user_id=target_user_id,
        cost_center_id=cost_center_id,
        transaction_type=transaction_type,
        amount=amount,
        balance_after=balance_after,
        description=description,
        created_by_user_id=actor_user_id,
    )
    db.add(tx)
    await db.flush()
    await db.commit()
    await db.refresh(wallet)
    await db.refresh(tx)

    # Return appropriate balance based on transaction type
    if cost_center_id is None:
        # Personal transaction: return user wallet balance
        response_balance = _to_balance_response(wallet)
    else:
        # Cost-center transaction: return cost-center balance as if it were a wallet
        response_balance = WalletBalanceResponse(
            user_id=target_user_id,
            balance=balance_after,
            currency=wallet.currency,
            updated_at=tx.created_at,
        )

    return WalletAdjustmentResponse(
        transaction=_serialize_wallet_transaction(tx),
        balance=response_balance,
    )


@router.get("/me/balance", response_model=WalletBalanceResponse)
async def get_my_balance(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_READ_OWN),
):
    """Return the current user's wallet balance."""
    user = await _require_authenticated_user(current_user)
    wallet = await _get_or_create_wallet(db, user.id)
    personal_balance_result = await db.execute(
        select(func.coalesce(func.sum(WalletTransaction.amount), 0.0))
        .select_from(WalletTransaction)
        .outerjoin(CostCenter, WalletTransaction.cost_center_id == CostCenter.id)
        .where(WalletTransaction.user_id == user.id, _personal_balance_condition(user.id))
    )
    personal_balance = float(personal_balance_result.scalar_one() or 0.0)
    return WalletBalanceResponse(
        user_id=user.id,
        balance=personal_balance,
        currency=wallet.currency,
        updated_at=wallet.updated_at,
    )


@router.get("/me/transactions", response_model=WalletTransactionListResponse)
async def get_my_transactions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_READ_OWN),
):
    """Return wallet ledger entries for the current user."""
    user = await _require_authenticated_user(current_user)

    total_result = await db.execute(
        select(func.count(WalletTransaction.id)).where(WalletTransaction.user_id == user.id)
    )
    total = int(total_result.scalar_one() or 0)

    result = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.user_id == user.id)
        .order_by(WalletTransaction.created_at.desc(), WalletTransaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    transactions = result.scalars().all()
    personal_balance_map = await _build_personal_balance_map(db, user.id)
    return WalletTransactionListResponse(
        items=[
            _serialize_wallet_transaction(tx).model_copy(
                update={"balance_after": personal_balance_map.get(tx.id, tx.balance_after)}
            )
            for tx in transactions
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/transactions", response_model=WalletTransactionListResponse)
async def get_all_transactions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user_id: int | None = Query(None, description="Optional filter by user id"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_READ_ALL),
):
    """Return wallet ledger entries across users for admin finance view."""
    await _require_authenticated_user(current_user)

    conditions = []
    if user_id is not None:
        await _get_user_or_404(db, user_id)
        conditions.append(WalletTransaction.user_id == user_id)

    total_result = await db.execute(select(func.count(WalletTransaction.id)).where(*conditions))
    total = int(total_result.scalar_one() or 0)

    result = await db.execute(
        select(WalletTransaction)
        .where(*conditions)
        .order_by(WalletTransaction.created_at.desc(), WalletTransaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    transactions = result.scalars().all()
    return WalletTransactionListResponse(
        items=[_serialize_wallet_transaction(tx) for tx in transactions],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _rebuild_wallet_ledger_for_user(db: AsyncSession, user_id: int) -> None:
    """Recompute `balance_after` for all wallet transactions of a user.

    - Personal transactions (cost_center_id=None): running balance per user
    - Cost-center transactions: running balance GLOBAL for entire cost center (not per-user)
    - Also updates the user's wallet balance (sum of personal transactions only)
    """
    result = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.user_id == user_id)
        .order_by(WalletTransaction.created_at.asc(), WalletTransaction.id.asc())
    )
    user_transactions = result.scalars().all()

    # Handle personal transactions (cost_center_id=None)
    personal_balance = 0.0
    for tx in user_transactions:
        if tx.cost_center_id is None:
            personal_balance += float(tx.amount)
            tx.balance_after = personal_balance
            db.add(tx)

    for cc_id in {tx.cost_center_id for tx in user_transactions if tx.cost_center_id is not None}:
        # Get all transactions for this cost center (all users, all time)
        result_all_cc = await db.execute(
            select(WalletTransaction)
            .where(WalletTransaction.cost_center_id == cc_id)
            .order_by(WalletTransaction.created_at.asc(), WalletTransaction.id.asc())
        )
        all_cc_transactions = result_all_cc.scalars().all()

        running = 0.0
        for tx in all_cc_transactions:
            running += float(tx.amount)
            tx.balance_after = running
            db.add(tx)

    # Update user wallet balance (sum of all personal transactions only)
    wallet = await _get_or_create_wallet(db, user_id)
    wallet.balance = personal_balance
    await db.flush()
    await db.commit()


@router.delete("/transactions/{transaction_id}")
async def delete_transaction(
    transaction_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_MODIFY),
):
    """Delete a wallet transaction and rebuild the user's ledger to keep balances consistent."""
    await _require_authenticated_user(current_user)

    result = await db.execute(select(WalletTransaction).where(WalletTransaction.id == transaction_id))
    tx = result.scalar_one_or_none()
    if tx is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    user_id = tx.user_id

    if tx.transaction_type == "print_charge" and tx.print_archive_id is not None:
        archive_result = await db.execute(select(PrintArchive).where(PrintArchive.id == tx.print_archive_id))
        archive = archive_result.scalar_one_or_none()
        if archive is not None:
            archive.wallet_charge_skipped = True
            db.add(archive)

    await db.delete(tx)
    await db.flush()

    await _rebuild_wallet_ledger_for_user(db, user_id)

    return {"status": "success"}


@router.patch("/transactions/{transaction_id}", response_model=WalletTransactionResponse)
async def edit_transaction(
    transaction_id: int,
    request: TransactionEditRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_MODIFY),
):
    """Edit a wallet transaction (user_id, cost_center_id, amount, description) and rebuild ledger."""
    await _require_authenticated_user(current_user)

    result = await db.execute(select(WalletTransaction).where(WalletTransaction.id == transaction_id))
    tx = result.scalar_one_or_none()
    if tx is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # Apply edits
    if request.user_id is not None:
        tx.user_id = request.user_id

    if request.cost_center_id is not None:
        tx.cost_center_id = request.cost_center_id

    if request.amount is not None:
        tx.amount = request.amount

    if request.description is not None:
        # Append "(Admin edit)" marker if not already present
        new_desc = request.description
        if not new_desc.endswith("(Admin edit)"):
            new_desc = f"{new_desc} (Admin edit)"
        tx.description = new_desc

    db.add(tx)
    await db.flush()

    # Rebuild full ledger using the current session
    from backend.app.core.database import repair_wallet_ledger_internal

    await repair_wallet_ledger_internal(db)

    return tx


@router.post("/transactions/manual", response_model=WalletTransactionResponse)
async def create_manual_print(
    request: ManualPrintRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_MODIFY),
):
    """Create a manual print charge transaction (for admin purposes)."""
    await _require_authenticated_user(current_user)

    from datetime import timezone

    # Use provided created_at or current time
    created_at = request.created_at or datetime.now(timezone.utc)

    # Ensure manual print charges are negative amounts (charges reduce wallet)
    amount = request.amount
    if amount > 0:
        amount = -abs(amount)

    # Create transaction
    tx = WalletTransaction(
        user_id=request.user_id,
        cost_center_id=request.cost_center_id,
        transaction_type=TransactionType.MANUAL_ADJUSTMENT.value,
        amount=amount,
        balance_after=None,  # Will be set by repair_wallet_ledger_internal
        description=request.description or "Manual print charge",
        created_by_user_id=current_user.id if current_user else None,
        created_at=created_at,
    )
    db.add(tx)
    await db.flush()

    # Rebuild full ledger using the current session
    from backend.app.core.database import repair_wallet_ledger_internal

    await repair_wallet_ledger_internal(db)

    return tx


@router.get("/cost-centers/mine", response_model=list[CostCenterSummaryResponse])
async def get_my_cost_centers(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(require_auth_if_enabled),
):
    """Return private and assigned cost centers for the current user."""
    user = await _require_authenticated_user(current_user)

    result = await db.execute(
        select(CostCenter, CostCenterMember.can_print)
        .outerjoin(
            CostCenterMember,
            (CostCenterMember.cost_center_id == CostCenter.id) & (CostCenterMember.user_id == user.id),
        )
        .where(
            CostCenter.is_active.is_(True),
            or_(
                (CostCenter.is_private.is_(True) & (CostCenter.owner_user_id == user.id)),
                (CostCenterMember.user_id == user.id),
            ),
        )
        .order_by(CostCenter.is_private.desc(), CostCenter.name.asc())
    )

    rows = result.all()
    centers_only = [center for center, _ in rows]
    center_ids = [center.id for center in centers_only]
    total_usage_map, month_usage_map = await _get_cost_center_usage_maps(db, center_ids)
    total_balance_map = await _get_cost_center_balance_map(db, center_ids)
    reserved_map = await _get_cost_center_reserved_map(db, center_ids)

    centers: list[CostCenterSummaryResponse] = []
    for center, can_print in rows:
        centers.append(
            _to_cost_center_summary(
                center,
                can_print=True if center.is_private and center.owner_user_id == user.id else bool(can_print),
                total_usage=total_usage_map.get(center.id, 0.0),
                month_usage=month_usage_map.get(center.id, 0.0),
                total_balance=total_balance_map.get(center.id, 0.0),
                reserved=reserved_map.get(center.id, 0.0),
            )
        )

    return centers


@router.get("/users/{user_id}/balance", response_model=WalletBalanceResponse)
async def get_user_balance(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_READ_ALL),
):
    """Return a specific user's wallet balance."""
    await _require_authenticated_user(current_user)
    user = await _get_user_or_404(db, user_id)
    wallet = await _get_or_create_wallet(db, user.id)
    return _to_balance_response(wallet)


@router.get("/users/{user_id}/transactions", response_model=list[WalletTransactionResponse])
async def get_user_transactions(
    user_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_READ_ALL),
):
    """Return wallet ledger entries for a specific user."""
    await _require_authenticated_user(current_user)
    await _get_user_or_404(db, user_id)

    result = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.user_id == user_id)
        .order_by(WalletTransaction.created_at.desc(), WalletTransaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [_serialize_wallet_transaction(tx) for tx in result.scalars().all()]


@router.post("/users/{user_id}/deposit", response_model=WalletAdjustmentResponse)
async def deposit_user_balance(
    user_id: int,
    body: WalletAdjustmentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_MODIFY),
):
    """Add funds to a user's wallet."""
    actor = await _require_authenticated_user(current_user)
    await _get_user_or_404(db, user_id)
    return await _create_wallet_adjustment(
        db,
        target_user_id=user_id,
        actor_user_id=actor.id,
        amount=body.amount,
        transaction_type=TransactionType.DEPOSIT.value,
        description=body.description,
        cost_center_id=body.cost_center_id,
    )


@router.post("/users/{user_id}/withdraw", response_model=WalletAdjustmentResponse)
async def withdraw_user_balance(
    user_id: int,
    body: WalletAdjustmentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_MODIFY),
):
    """Withdraw funds from a user's wallet."""
    actor = await _require_authenticated_user(current_user)
    await _get_user_or_404(db, user_id)
    return await _create_wallet_adjustment(
        db,
        target_user_id=user_id,
        actor_user_id=actor.id,
        amount=-body.amount,
        transaction_type=TransactionType.WITHDRAW.value,
        description=body.description,
        cost_center_id=body.cost_center_id,
    )


@router.post("/rebuild-balance-ledger")
async def rebuild_balance_ledger(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_MODIFY),
):
    """Recompute balance_after for all wallet transactions.

    This rebuilds the running balance for all users and cost centers.
    - Personal transactions: per-user running balance
    - Cost-center transactions: global running balance for the entire cost center
    """
    await _require_authenticated_user(current_user)

    # Get ALL transactions sorted by timestamp
    result = await db.execute(
        select(WalletTransaction).order_by(WalletTransaction.created_at.asc(), WalletTransaction.id.asc())
    )
    all_transactions = result.scalars().all()

    # Build running balances per (user, cost_center_id) pair
    # For each cost center, track its global running balance
    # For each user's personal balance, track that separately
    cc_running_balances: dict[int, float] = {}  # cost_center_id -> running balance
    user_personal_balances: dict[int, float] = {}  # user_id -> personal running balance

    tx_updates: list[tuple[WalletTransaction, float]] = []

    for tx in all_transactions:
        if tx.cost_center_id is None:
            # Personal transaction: per-user running balance
            current = user_personal_balances.get(tx.user_id, 0.0)
            new_balance = current + float(tx.amount)
            user_personal_balances[tx.user_id] = new_balance
            tx_updates.append((tx, new_balance))
        else:
            # Cost-center transaction: global running balance for this cost center
            current = cc_running_balances.get(tx.cost_center_id, 0.0)
            new_balance = current + float(tx.amount)
            cc_running_balances[tx.cost_center_id] = new_balance
            tx_updates.append((tx, new_balance))

    # Update all transactions with the new balance_after values
    for tx, new_balance in tx_updates:
        tx.balance_after = new_balance
        db.add(tx)

    await db.flush()
    await db.commit()

    return {
        "status": "success",
        "transactions_rebuilt": len(all_transactions),
        "message": f"Rebuilt balance_after for {len(all_transactions)} transactions",
    }


@router.get("/cost-centers", response_model=list[CostCenterSummaryResponse])
async def list_cost_centers(
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(require_auth_if_enabled),
):
    """List all cost centers.

    Requires admin-level finance permissions.
    """
    await _require_cost_center_admin_access(current_user)
    query = select(CostCenter).order_by(CostCenter.is_private.desc(), CostCenter.name.asc())
    if not include_inactive:
        query = query.where(CostCenter.is_active.is_(True))

    result = await db.execute(query)
    centers = result.scalars().all()
    center_ids = [center.id for center in centers]
    total_usage_map, month_usage_map = await _get_cost_center_usage_maps(db, center_ids)
    total_balance_map = await _get_cost_center_balance_map(db, center_ids)
    reserved_map = await _get_cost_center_reserved_map(db, center_ids)

    return [
        _to_cost_center_summary(
            center,
            can_print=True,
            total_usage=total_usage_map.get(center.id, 0.0),
            month_usage=month_usage_map.get(center.id, 0.0),
            total_balance=total_balance_map.get(center.id, 0.0),
            reserved=reserved_map.get(center.id, 0.0),
        )
        for center in centers
    ]


@router.post("/cost-centers", response_model=CostCenterSummaryResponse)
async def create_cost_center(
    body: CostCenterCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_CREATE),
):
    """Create a shared cost center."""
    await _require_authenticated_user(current_user)
    total_budget = body.total_budget
    monthly_budget = body.monthly_budget
    if monthly_budget is not None:
        total_budget = None
    elif total_budget is not None:
        monthly_budget = None

    center = CostCenter(
        name=body.name.strip(),
        is_active=body.is_active,
        is_private=False,
        owner_user_id=None,
        total_budget=total_budget,
        monthly_budget=monthly_budget,
    )
    db.add(center)
    await db.flush()
    await db.commit()
    await db.refresh(center)

    return _to_cost_center_summary(center, can_print=True, total_usage=0.0, month_usage=0.0, total_balance=0.0)


@router.get("/cost-centers/{cost_center_id}", response_model=CostCenterDetailResponse)
async def get_cost_center(
    cost_center_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(require_auth_if_enabled),
):
    """Get one cost center with its memberships."""
    await _require_cost_center_admin_access(current_user)
    center = await _get_cost_center_or_404(db, cost_center_id)
    total_usage_map, month_usage_map = await _get_cost_center_usage_maps(db, [center.id])
    total_balance_map = await _get_cost_center_balance_map(db, [center.id])
    reserved_map = await _get_cost_center_reserved_map(db, [center.id])
    summary = _to_cost_center_summary(
        center,
        can_print=True,
        total_usage=total_usage_map.get(center.id, 0.0),
        month_usage=month_usage_map.get(center.id, 0.0),
        total_balance=total_balance_map.get(center.id, 0.0),
        reserved=reserved_map.get(center.id, 0.0),
    )
    return CostCenterDetailResponse(
        **summary.model_dump(),
        members=[CostCenterMemberResponse.model_validate(m) for m in center.members],
    )


@router.patch("/cost-centers/{cost_center_id}", response_model=CostCenterSummaryResponse)
async def update_cost_center(
    cost_center_id: int,
    body: CostCenterUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_MODIFY),
):
    """Update name or active-state of a cost center."""
    await _require_authenticated_user(current_user)
    center = await _get_cost_center_or_404(db, cost_center_id)

    if body.name is not None:
        center.name = body.name.strip()
    if body.is_active is not None:
        center.is_active = body.is_active

    await db.flush()

    total_usage_map, month_usage_map = await _get_cost_center_usage_maps(db, [center.id])
    total_balance_map = await _get_cost_center_balance_map(db, [center.id])
    reserved_map = await _get_cost_center_reserved_map(db, [center.id])
    return _to_cost_center_summary(
        center,
        can_print=True,
        total_usage=total_usage_map.get(center.id, 0.0),
        month_usage=month_usage_map.get(center.id, 0.0),
        total_balance=total_balance_map.get(center.id, 0.0),
        reserved=reserved_map.get(center.id, 0.0),
    )


@router.patch("/cost-centers/{cost_center_id}/budgets", response_model=CostCenterSummaryResponse)
async def update_cost_center_budgets(
    cost_center_id: int,
    body: CostCenterBudgetUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_MODIFY),
):
    """Update budget values of a cost center."""
    await _require_authenticated_user(current_user)
    center = await _get_cost_center_or_404(db, cost_center_id)

    if body.monthly_budget is not None:
        center.monthly_budget = body.monthly_budget
        center.total_budget = None
    elif body.total_budget is not None:
        center.total_budget = body.total_budget
        center.monthly_budget = None
    else:
        center.total_budget = None
        center.monthly_budget = None
    await db.flush()

    total_usage_map, month_usage_map = await _get_cost_center_usage_maps(db, [center.id])
    total_balance_map = await _get_cost_center_balance_map(db, [center.id])
    reserved_map = await _get_cost_center_reserved_map(db, [center.id])
    return _to_cost_center_summary(
        center,
        can_print=True,
        total_usage=total_usage_map.get(center.id, 0.0),
        month_usage=month_usage_map.get(center.id, 0.0),
        total_balance=total_balance_map.get(center.id, 0.0),
        reserved=reserved_map.get(center.id, 0.0),
    )


@router.post("/cost-centers/{cost_center_id}/members", response_model=CostCenterMemberResponse)
async def upsert_cost_center_member(
    cost_center_id: int,
    body: CostCenterMemberRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_MODIFY),
):
    """Assign or update a user's membership on a cost center."""
    await _require_authenticated_user(current_user)
    center = await _get_cost_center_or_404(db, cost_center_id)

    if center.is_private:
        raise HTTPException(status_code=400, detail="Private cost center memberships cannot be modified")

    await _get_user_or_404(db, body.user_id)

    existing = await db.execute(
        select(CostCenterMember).where(
            CostCenterMember.cost_center_id == cost_center_id,
            CostCenterMember.user_id == body.user_id,
        )
    )
    member = existing.scalar_one_or_none()
    if member is None:
        member = CostCenterMember(cost_center_id=cost_center_id, user_id=body.user_id, can_print=body.can_print)
        db.add(member)
    else:
        member.can_print = body.can_print

    await db.flush()
    await db.commit()
    return CostCenterMemberResponse.model_validate(member)


@router.delete("/cost-centers/{cost_center_id}")
async def delete_cost_center(
    cost_center_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_MODIFY),
):
    """Delete a shared cost center."""
    await _require_authenticated_user(current_user)
    center = await _get_cost_center_or_404(db, cost_center_id)

    if center.is_private:
        raise HTTPException(status_code=400, detail="Private cost centers cannot be deleted")

    balance_map = await _get_cost_center_balance_map(db, [center.id])
    total_balance = balance_map.get(center.id, 0.0)
    if abs(total_balance) > 1e-9:
        raise HTTPException(status_code=400, detail="Cost center can only be deleted when balance is 0")

    await db.delete(center)
    await db.commit()
    return {"status": "success"}


@router.delete("/cost-centers/{cost_center_id}/members/{user_id}")
async def remove_cost_center_member(
    cost_center_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.COST_CENTERS_MODIFY),
):
    """Remove a user from a shared cost center."""
    await _require_authenticated_user(current_user)
    center = await _get_cost_center_or_404(db, cost_center_id)
    if center.is_private:
        raise HTTPException(status_code=400, detail="Private cost center memberships cannot be modified")

    result = await db.execute(
        select(CostCenterMember).where(
            CostCenterMember.cost_center_id == cost_center_id,
            CostCenterMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Membership not found")

    await db.delete(member)
    await db.commit()
    return {"status": "success"}
