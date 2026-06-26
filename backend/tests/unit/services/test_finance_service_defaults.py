"""Unit tests for finance defaults applied during user creation/update."""

import pytest
from sqlalchemy import select

from backend.app.models.finance import CostCenter, CostCenterMember, UserWallet
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.finance_defaults import ensure_user_finance_defaults


class TestFinanceDefaults:
    @pytest.mark.asyncio
    async def test_creates_wallet_private_center_and_membership(self, db_session):
        db_session.add(Settings(key="currency", value="USD"))

        user = User(username="alice", role="user", is_active=True)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        changed = await ensure_user_finance_defaults(db_session, user)
        await db_session.commit()

        assert changed is True

        wallet = await db_session.scalar(select(UserWallet).where(UserWallet.user_id == user.id))
        assert wallet is not None
        assert wallet.balance == 0.0
        assert wallet.currency == "USD"

        center = await db_session.scalar(
            select(CostCenter).where(CostCenter.owner_user_id == user.id, CostCenter.is_private.is_(True))
        )
        assert center is not None
        assert center.name == "alice"

        membership = await db_session.scalar(
            select(CostCenterMember).where(
                CostCenterMember.cost_center_id == center.id,
                CostCenterMember.user_id == user.id,
            )
        )
        assert membership is not None
        assert membership.can_print is True

    @pytest.mark.asyncio
    async def test_updates_private_center_name_and_is_idempotent(self, db_session):
        user = User(username="bob", role="user", is_active=True)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        initial_changed = await ensure_user_finance_defaults(db_session, user)
        await db_session.commit()

        assert initial_changed is True

        user.username = "bobby"
        renamed_changed = await ensure_user_finance_defaults(db_session, user)
        await db_session.commit()

        assert renamed_changed is True

        center = await db_session.scalar(
            select(CostCenter).where(CostCenter.owner_user_id == user.id, CostCenter.is_private.is_(True))
        )
        assert center is not None
        assert center.name == "bobby"

        idempotent_changed = await ensure_user_finance_defaults(db_session, user)
        assert idempotent_changed is False
