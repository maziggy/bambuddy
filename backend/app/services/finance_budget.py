"""Budget validation helpers for finance-aware print dispatch."""

import calendar
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.finance import BudgetReservation, CostCenter, CostCenterMember, WalletTransaction
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings
from backend.app.models.user import User


async def is_billing_enabled(db: AsyncSession) -> bool:
    # Consider any 'billing_enabled' setting with a true-ish value as enabling billing.
    result = await db.execute(
        select(func.count())
        .select_from(Settings)
        .where(Settings.key == "billing_enabled", func.lower(func.coalesce(Settings.value, "")) == "true")
    )
    count = int(result.scalar_one() or 0)
    return count > 0


async def is_printer_kill_switch_enabled(db: AsyncSession) -> bool:
    """Return True when billing and the printer kill-switch are both enabled."""

    result = await db.execute(
        select(Settings.key, Settings.value).where(Settings.key.in_(("billing_enabled", "printer_kill_switch_enabled")))
    )
    values = {key: (value or "").strip().lower() for key, value in result.all()}
    return values.get("billing_enabled") == "true" and values.get("printer_kill_switch_enabled") == "true"


async def _get_budget_window_start_utc(db: AsyncSession) -> datetime:
    result = await db.execute(
        select(Settings).where(Settings.key.in_(["finance_budget_reset_day", "finance_budget_reset_timezone"]))
    )
    values = {setting.key: setting.value for setting in result.scalars().all()}

    desired_day = 1
    try:
        parsed = int(values.get("finance_budget_reset_day") or 1)
        if 1 <= parsed <= 31:
            desired_day = parsed
    except (TypeError, ValueError):
        pass

    timezone_name = values.get("finance_budget_reset_timezone") or "UTC"
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    now = datetime.now(tz)
    current_month_reset_day = min(desired_day, calendar.monthrange(now.year, now.month)[1])
    if now.day < current_month_reset_day:
        month = now.month - 1
        year = now.year
        if month == 0:
            month = 12
            year -= 1
    else:
        month = now.month
        year = now.year

    reset_day = min(desired_day, calendar.monthrange(year, month)[1])
    return datetime(year, month, reset_day, tzinfo=tz).astimezone(timezone.utc)


async def _cost_center_spend(db: AsyncSession, cost_center_id: int, *, monthly: bool) -> float:
    spend_expr = case((WalletTransaction.amount < 0, -WalletTransaction.amount), else_=0.0)
    conditions = [
        WalletTransaction.cost_center_id == cost_center_id,
        WalletTransaction.cost_center_id.is_not(None),
    ]
    if monthly:
        conditions.append(WalletTransaction.created_at >= await _get_budget_window_start_utc(db))

    result = await db.execute(select(func.coalesce(func.sum(spend_expr), 0.0)).where(*conditions))
    return float(result.scalar() or 0.0)


async def _cost_center_open_queue_reservations(
    db: AsyncSession,
    cost_center_id: int,
    *,
    exclude_queue_item_id: int | None = None,
) -> float:
    conditions = [
        PrintQueueItem.cost_center_id == cost_center_id,
        PrintQueueItem.status.in_(("pending", "printing")),
    ]
    if exclude_queue_item_id is not None:
        conditions.append(PrintQueueItem.id != exclude_queue_item_id)

    result = await db.execute(select(func.coalesce(func.sum(PrintQueueItem.estimated_cost), 0.0)).where(*conditions))
    return float(result.scalar() or 0.0)


async def _cost_center_active_budget_reservations(db: AsyncSession, cost_center_id: int) -> float:
    result = await db.execute(
        select(func.coalesce(func.sum(BudgetReservation.amount), 0.0)).where(
            BudgetReservation.cost_center_id == cost_center_id,
            BudgetReservation.status == "active",
        )
    )
    return float(result.scalar() or 0.0)


async def validate_print_budget(
    db: AsyncSession,
    *,
    cost_center_id: int | None,
    estimated_cost: float | None,
    current_user: User | None,
    quantity: int = 1,
    exclude_queue_item_id: int | None = None,
) -> None:
    """Validate that a print can be assigned to a cost center budget."""
    if not await is_billing_enabled(db):
        return

    if cost_center_id is None:
        raise HTTPException(status_code=400, detail="Cost center is required when billing is enabled")

    if estimated_cost is None or estimated_cost <= 0:
        raise HTTPException(status_code=400, detail="Estimated cost is required for cost center prints")

    center = await db.scalar(select(CostCenter).where(CostCenter.id == cost_center_id).with_for_update())
    if not center:
        raise HTTPException(status_code=404, detail="Cost center not found")
    if not center.is_active:
        raise HTTPException(status_code=400, detail="Cost center is inactive")

    if current_user is not None and not current_user.is_admin:
        if center.is_private:
            if center.owner_user_id != current_user.id:
                raise HTTPException(status_code=403, detail="You cannot print with this private cost center")
        else:
            member = await db.scalar(
                select(CostCenterMember).where(
                    CostCenterMember.cost_center_id == cost_center_id,
                    CostCenterMember.user_id == current_user.id,
                )
            )
            if not member or not member.can_print:
                raise HTTPException(status_code=403, detail="You cannot print with this cost center")

    budget_limit = center.monthly_budget if center.monthly_budget is not None else center.total_budget
    if budget_limit is None:
        return

    used = await _cost_center_spend(db, cost_center_id, monthly=center.monthly_budget is not None)
    reserved = await _cost_center_open_queue_reservations(
        db,
        cost_center_id,
        exclude_queue_item_id=exclude_queue_item_id,
    )
    reserved += await _cost_center_active_budget_reservations(db, cost_center_id)
    requested = estimated_cost * max(1, quantity)
    available = float(budget_limit) - used - reserved
    if requested > available:
        raise HTTPException(
            status_code=400,
            detail=f"Estimated print cost exceeds available cost center budget ({requested:.2f} > {available:.2f})",
        )


async def create_budget_reservation(
    db: AsyncSession,
    *,
    cost_center_id: int | None,
    estimated_cost: float | None,
    current_user: User | None,
    source_type: str,
    source_id: int | None,
    print_archive_id: int | None = None,
) -> BudgetReservation | None:
    if not await is_billing_enabled(db):
        return None

    if cost_center_id is None:
        raise HTTPException(status_code=400, detail="Cost center is required when billing is enabled")

    await validate_print_budget(
        db,
        cost_center_id=cost_center_id,
        estimated_cost=estimated_cost,
        current_user=current_user,
    )
    reservation = BudgetReservation(
        cost_center_id=cost_center_id,
        amount=float(estimated_cost or 0.0),
        status="active",
        source_type=source_type,
        source_id=source_id,
        print_archive_id=print_archive_id,
    )
    db.add(reservation)
    await db.flush()
    return reservation


async def release_budget_reservation(
    db: AsyncSession,
    *,
    source_type: str | None = None,
    source_id: int | None = None,
    print_archive_id: int | None = None,
    status: str = "released",
) -> int:
    conditions = [BudgetReservation.status == "active"]
    if print_archive_id is not None:
        conditions.append(BudgetReservation.print_archive_id == print_archive_id)
    else:
        conditions.extend(
            [
                BudgetReservation.source_type == source_type,
                BudgetReservation.source_id == source_id,
            ]
        )

    result = await db.execute(select(BudgetReservation).where(*conditions))
    reservations = result.scalars().all()
    for reservation in reservations:
        reservation.status = status
        reservation.released_at = datetime.now(timezone.utc)
    if reservations:
        await db.flush()
    return len(reservations)
