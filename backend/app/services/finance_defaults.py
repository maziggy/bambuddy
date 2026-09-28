from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.finance import CostCenter, CostCenterMember, UserWallet
from backend.app.models.user import User


async def ensure_user_finance_defaults(db: AsyncSession, user: User) -> bool:
    """Ensure wallet and private cost center defaults exist for a user.

    Returns True when database objects were created or changed.
    """
    changed = False

    wallet = (await db.execute(select(UserWallet).where(UserWallet.user_id == user.id))).scalar_one_or_none()
    if wallet is None:
        db.add(UserWallet(user_id=user.id, balance=0.0))
        changed = True

    private_center = (
        (
            await db.execute(
                select(CostCenter)
                .where(
                    CostCenter.is_private.is_(True),
                    CostCenter.owner_user_id == user.id,
                )
                .order_by(CostCenter.id.asc())
            )
        )
        .scalars()
        .first()
    )

    if private_center is None:
        private_center = CostCenter(
            name=user.username,
            is_active=True,
            is_private=True,
            owner_user_id=user.id,
        )
        db.add(private_center)
        await db.flush()
        changed = True
    else:
        # A private center is the billing fallback for its owner and therefore
        # must remain active. A zero budget is the supported way to prevent
        # printing from it.
        if not private_center.is_active:
            private_center.is_active = True
            changed = True
        if private_center.name != user.username:
            private_center.name = user.username
            changed = True

    membership = (
        await db.execute(
            select(CostCenterMember).where(
                CostCenterMember.cost_center_id == private_center.id,
                CostCenterMember.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        db.add(CostCenterMember(cost_center_id=private_center.id, user_id=user.id, can_print=True))
        changed = True

    return changed
