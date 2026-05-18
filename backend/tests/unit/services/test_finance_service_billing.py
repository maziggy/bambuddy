"""Unit tests for billing charges applied to print archives."""

import pytest
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.models.finance import BudgetReservation, CostCenter, UserWallet, WalletTransaction
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.finance_billing import apply_print_charge_for_archive


async def enable_billing(db_session):
    setting = await db_session.scalar(select(Settings).where(Settings.key == "billing_enabled"))
    if setting is None:
        db_session.add(Settings(key="billing_enabled", value="true"))
    else:
        setting.value = "true"
    await db_session.commit()


class TestFinanceBilling:
    @pytest.mark.asyncio
    async def test_apply_print_charge_uses_print_run_id_and_cost_center_override(self, db_session):
        await enable_billing(db_session)
        user = User(username="printer", role="user", is_active=True)
        archive_cost_center = CostCenter(name="Archive CC", is_active=True, is_private=False)
        override_cost_center = CostCenter(name="Override CC", is_active=True, is_private=False)
        db_session.add_all([user, archive_cost_center, override_cost_center])
        await db_session.commit()
        await db_session.refresh(user)
        await db_session.refresh(archive_cost_center)
        await db_session.refresh(override_cost_center)

        archive = PrintArchive(
            printer_id=None,
            filename="test.3mf",
            file_path="archives/test/test.3mf",
            file_size=123,
            content_hash="hash-1",
            status="completed",
            cost=7.5,
            created_by_id=user.id,
            cost_center_id=archive_cost_center.id,
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)

        changed = await apply_print_charge_for_archive(
            db_session,
            archive.id,
            cost_center_id=override_cost_center.id,
            print_run_id="run-1",
        )
        await db_session.commit()

        assert changed is True
        assert archive.cost_center_id == archive_cost_center.id

        wallet = await db_session.scalar(select(UserWallet).where(UserWallet.user_id == user.id))
        assert wallet is not None
        assert wallet.balance == -7.5

        tx = await db_session.scalar(select(WalletTransaction).where(WalletTransaction.print_run_id == "run-1"))
        assert tx is not None
        assert tx.cost_center_id == override_cost_center.id
        assert tx.print_archive_id == archive.id

        duplicate = await apply_print_charge_for_archive(
            db_session,
            archive.id,
            cost_center_id=override_cost_center.id,
            print_run_id="run-1",
        )
        assert duplicate is False

        second_run = await apply_print_charge_for_archive(
            db_session,
            archive.id,
            cost_center_id=override_cost_center.id,
            print_run_id="run-2",
        )
        await db_session.commit()

        assert second_run is True
        wallet = await db_session.scalar(select(UserWallet).where(UserWallet.user_id == user.id))
        assert wallet is not None
        assert wallet.balance == -15.0

        rows = (
            (await db_session.execute(select(WalletTransaction).where(WalletTransaction.user_id == user.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert {row.print_run_id for row in rows} == {"run-1", "run-2"}

    @pytest.mark.asyncio
    async def test_apply_print_charge_consumes_matching_budget_reservation(self, db_session):
        await enable_billing(db_session)
        user = User(username="reserved", role="user", is_active=True)
        cost_center = CostCenter(name="Reserved CC", is_active=True, is_private=False)
        db_session.add_all([user, cost_center])
        await db_session.commit()
        await db_session.refresh(user)
        await db_session.refresh(cost_center)

        archive = PrintArchive(
            printer_id=None,
            filename="reserved.3mf",
            file_path="archives/test/reserved.3mf",
            file_size=123,
            content_hash="hash-reserved",
            status="completed",
            cost=4.0,
            created_by_id=user.id,
            cost_center_id=cost_center.id,
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)

        reservation = BudgetReservation(
            cost_center_id=cost_center.id,
            amount=4.0,
            status="active",
            source_type="background_dispatch",
            source_id=42,
            print_archive_id=archive.id,
        )
        db_session.add(reservation)
        await db_session.commit()
        await db_session.refresh(reservation)

        changed = await apply_print_charge_for_archive(db_session, archive.id, print_run_id="run-reserved")
        await db_session.commit()

        assert changed is True
        await db_session.refresh(reservation)
        assert reservation.status == "consumed"
        assert reservation.released_at is not None

    @pytest.mark.asyncio
    async def test_apply_print_charge_rejects_ineligible_archive(self, db_session):
        await enable_billing(db_session)
        user = User(username="skipped", role="user", is_active=True)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Reject print with unknown status
        archive = PrintArchive(
            printer_id=None,
            filename="unknown.3mf",
            file_path="archives/test/unknown.3mf",
            file_size=123,
            content_hash="hash-2",
            status="unknown",
            cost=1.0,
            created_by_id=user.id,
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)

        changed = await apply_print_charge_for_archive(db_session, archive.id, print_run_id="run-unknown")
        assert changed is False

    @pytest.mark.asyncio
    async def test_apply_print_charge_skips_when_billing_disabled(self, db_session):
        user = User(username="billing_disabled", role="user", is_active=True)
        cost_center = CostCenter(name="Disabled Billing CC", is_active=True, is_private=False)
        db_session.add_all([user, cost_center])
        await db_session.commit()
        await db_session.refresh(user)
        await db_session.refresh(cost_center)

        archive = PrintArchive(
            printer_id=None,
            filename="billing-disabled.3mf",
            file_path="archives/test/billing-disabled.3mf",
            file_size=123,
            content_hash="hash-disabled-billing",
            status="completed",
            cost=7.5,
            created_by_id=user.id,
            cost_center_id=cost_center.id,
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)
        reservation = BudgetReservation(
            cost_center_id=cost_center.id,
            amount=7.5,
            status="active",
            source_type="background_dispatch",
            source_id=123,
            print_archive_id=archive.id,
        )
        db_session.add(reservation)
        await db_session.commit()
        await db_session.refresh(reservation)

        changed = await apply_print_charge_for_archive(db_session, archive.id, print_run_id="run-disabled")
        await db_session.commit()

        assert changed is False
        wallet = await db_session.scalar(select(UserWallet).where(UserWallet.user_id == user.id))
        tx = await db_session.scalar(select(WalletTransaction).where(WalletTransaction.print_run_id == "run-disabled"))
        assert wallet is None
        assert tx is None
        await db_session.refresh(reservation)
        assert reservation.status == "released"
        assert reservation.released_at is not None


class TestPartialPrintCharges:
    """Tests for proportional charge calculation on aborted/failed/cancelled prints."""

    @pytest.mark.asyncio
    async def test_partial_print_with_missing_planned_filament_is_skipped(self, db_session):
        await enable_billing(db_session)
        user = User(username="missing_plan", role="user", is_active=True)
        cost_center = CostCenter(name="Missing Plan CC", is_active=True, is_private=False)
        db_session.add_all([user, cost_center])
        await db_session.commit()
        await db_session.refresh(user)
        await db_session.refresh(cost_center)

        archive = PrintArchive(
            printer_id=None,
            filename="missing-plan.3mf",
            file_path="archives/test/missing-plan.3mf",
            file_size=100,
            content_hash="missing-plan-hash",
            status="aborted",
            cost=12.0,
            filament_used_grams=80.0,
            created_by_id=user.id,
            cost_center_id=cost_center.id,
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)

        changed = await apply_print_charge_for_archive(db_session, archive.id)
        await db_session.commit()

        assert changed is False
        wallet = await db_session.scalar(select(UserWallet).where(UserWallet.user_id == user.id))
        assert wallet is None

    @pytest.mark.asyncio
    async def test_invalid_transaction_type_is_rejected(self, db_session):
        user = User(username="invalid_tx", role="user", is_active=True)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        with pytest.raises(ValueError, match="Invalid transaction type"):
            WalletTransaction(
                user_id=user.id,
                transaction_type="not-a-real-type",
                amount=1.0,
            )

    @pytest.mark.asyncio
    async def test_aborted_print_with_partial_filament_charges_proportionally(self, db_session):
        """Verify aborted print charges proportionally based on filament used."""
        await enable_billing(db_session)
        user = User(username="abort_test", role="user", is_active=True)
        cost_center = CostCenter(name="Abort CC", is_active=True, is_private=False)
        db_session.add_all([user, cost_center])
        await db_session.commit()
        await db_session.refresh(user)
        await db_session.refresh(cost_center)

        # Archive with 100g planned, but only 50g used (50% filament)
        archive = PrintArchive(
            printer_id=None,
            filename="abort.3mf",
            file_path="archives/test/abort.3mf",
            file_size=100,
            content_hash="abort-hash",
            status="aborted",
            cost=10.0,  # Full cost would be 10.0
            filament_used_grams=50.0,
            extra_data={"filament_grams_total": 100.0},
            created_by_id=user.id,
            cost_center_id=cost_center.id,
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)

        changed = await apply_print_charge_for_archive(db_session, archive.id)
        await db_session.commit()

        assert changed is True

        wallet = await db_session.scalar(select(UserWallet).where(UserWallet.user_id == user.id))
        assert wallet is not None
        assert wallet.balance == -5.0  # 50% of 10.0

        tx = await db_session.scalar(
            select(WalletTransaction)
            .where(WalletTransaction.user_id == user.id)
            .where(WalletTransaction.transaction_type == "print_charge")
        )
        assert tx is not None
        assert tx.amount == -5.0
        assert "aborted" in tx.description.lower()
        assert "50.0" in tx.description  # filament used

    @pytest.mark.asyncio
    async def test_cancelled_print_with_no_filament_data_is_not_charged(self, db_session):
        """Verify cancelled print with no filament data is skipped."""
        await enable_billing(db_session)
        user = User(username="cancel_no_data", role="user", is_active=True)
        cost_center = CostCenter(name="Cancel No Data CC", is_active=True, is_private=False)
        db_session.add_all([user, cost_center])
        await db_session.commit()
        await db_session.refresh(user)
        await db_session.refresh(cost_center)

        archive = PrintArchive(
            printer_id=None,
            filename="cancel.3mf",
            file_path="archives/test/cancel.3mf",
            file_size=100,
            content_hash="cancel-hash",
            status="cancelled",
            cost=5.0,
            filament_used_grams=None,  # No data
            created_by_id=user.id,
            cost_center_id=cost_center.id,
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)
        reservation = BudgetReservation(
            cost_center_id=cost_center.id,
            amount=5.0,
            status="active",
            source_type="background_dispatch",
            source_id=99,
            print_archive_id=archive.id,
        )
        db_session.add(reservation)
        await db_session.commit()
        await db_session.refresh(reservation)

        changed = await apply_print_charge_for_archive(db_session, archive.id)
        await db_session.commit()

        assert changed is False
        wallet = await db_session.scalar(select(UserWallet).where(UserWallet.user_id == user.id))
        assert wallet is None  # No wallet created
        await db_session.refresh(reservation)
        assert reservation.status == "released"
        assert reservation.released_at is not None

    @pytest.mark.asyncio
    async def test_failed_print_with_minimal_filament_charges_small_amount(self, db_session):
        """Verify failed print with minimal filament usage charges proportionally."""
        await enable_billing(db_session)
        user = User(username="fail_min", role="user", is_active=True)
        cost_center = CostCenter(name="Fail Min CC", is_active=True, is_private=False)
        db_session.add_all([user, cost_center])
        await db_session.commit()
        await db_session.refresh(user)
        await db_session.refresh(cost_center)

        # 5% filament used out of 100g planned
        archive = PrintArchive(
            printer_id=None,
            filename="fail_min.3mf",
            file_path="archives/test/fail_min.3mf",
            file_size=100,
            content_hash="fail-min-hash",
            status="failed",
            cost=20.0,
            filament_used_grams=5.0,
            extra_data={"filament_grams_total": 100.0},
            failure_reason="Filament runout",
            created_by_id=user.id,
            cost_center_id=cost_center.id,
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)

        changed = await apply_print_charge_for_archive(db_session, archive.id)
        await db_session.commit()

        assert changed is True

        wallet = await db_session.scalar(select(UserWallet).where(UserWallet.user_id == user.id))
        assert wallet is not None
        assert wallet.balance == pytest.approx(-1.0, abs=0.01)  # 5% of 20.0

    @pytest.mark.asyncio
    async def test_completed_print_still_charges_full_cost(self, db_session):
        """Verify completed prints ignore filament ratio and charge full cost."""
        await enable_billing(db_session)
        user = User(username="completed_full", role="user", is_active=True)
        cost_center = CostCenter(name="Completed Full CC", is_active=True, is_private=False)
        db_session.add_all([user, cost_center])
        await db_session.commit()
        await db_session.refresh(user)
        await db_session.refresh(cost_center)

        archive = PrintArchive(
            printer_id=None,
            filename="complete.3mf",
            file_path="archives/test/complete.3mf",
            file_size=100,
            content_hash="complete-hash",
            status="completed",
            cost=15.0,
            filament_used_grams=100.0,
            extra_data={"filament_grams_total": 100.0},
            created_by_id=user.id,
            cost_center_id=cost_center.id,
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)

        changed = await apply_print_charge_for_archive(db_session, archive.id)
        await db_session.commit()

        assert changed is True

        wallet = await db_session.scalar(select(UserWallet).where(UserWallet.user_id == user.id))
        assert wallet.balance == -15.0  # Full cost, not proportional

    @pytest.mark.asyncio
    async def test_partial_charge_with_cost_center_override(self, db_session):
        """Verify partial charges respect cost_center_id override."""
        await enable_billing(db_session)
        user = User(username="partial_cc", role="user", is_active=True)
        default_cc = CostCenter(name="Default", is_active=True, is_private=False)
        override_cc = CostCenter(name="Override", is_active=True, is_private=False)
        db_session.add_all([user, default_cc, override_cc])
        await db_session.commit()
        await db_session.refresh(user)
        await db_session.refresh(default_cc)
        await db_session.refresh(override_cc)

        archive = PrintArchive(
            printer_id=None,
            filename="partial_cc.3mf",
            file_path="archives/test/partial_cc.3mf",
            file_size=100,
            content_hash="partial-cc-hash",
            status="aborted",
            cost=8.0,
            filament_used_grams=25.0,
            extra_data={"filament_grams_total": 100.0},
            cost_center_id=default_cc.id,
            created_by_id=user.id,
        )
        db_session.add(archive)
        await db_session.commit()
        await db_session.refresh(archive)

        changed = await apply_print_charge_for_archive(db_session, archive.id, cost_center_id=override_cc.id)
        await db_session.commit()

        assert changed is True

        tx = await db_session.scalar(
            select(WalletTransaction)
            .where(WalletTransaction.user_id == user.id)
            .where(WalletTransaction.transaction_type == "print_charge")
        )
        assert tx is not None
        assert tx.cost_center_id == override_cc.id
        assert tx.amount == -2.0  # 25% of 8.0
