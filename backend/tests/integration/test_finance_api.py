"""Integration tests for the finance/billing API."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core.auth import get_password_hash
from backend.app.core.database import repair_wallet_ledger_internal
from backend.app.models.archive import PrintArchive
from backend.app.models.finance import BudgetReservation, CostCenter, UserWallet, WalletTransaction
from backend.app.models.group import Group
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.finance_billing import apply_print_charge_for_archive


class TestFinanceAPI:
    @pytest.fixture
    async def admin_user(self, db_session):
        user = User(
            username="finance-admin",
            email="finance-admin@example.com",
            password_hash=get_password_hash("AdminPass1!"),
            role="admin",
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    @pytest.fixture
    async def auth_headers(self, async_client: AsyncClient, db_session, admin_user):
        db_session.add(Settings(key="auth_enabled", value="true"))
        db_session.add(Settings(key="advanced_auth_enabled", value="false"))
        # Ensure billing is enabled for finance integration tests
        existing = await db_session.scalar(select(Settings).where(Settings.key == "billing_enabled"))
        if existing is None:
            db_session.add(Settings(key="billing_enabled", value="true"))
        else:
            existing.value = "true"
        await db_session.commit()

        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": "AdminPass1!"},
        )
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    async def _enable_basic_user_creation(self, db_session):
        return None

    async def _create_user_via_api(self, async_client: AsyncClient, auth_headers: dict[str, str], username: str):
        response = await async_client.post(
            "/api/v1/users",
            json={
                "username": username,
                "password": "Regularpass1!",
                "email": f"{username}@example.com",
                "role": "user",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        return response.json()

    async def _login_user(self, async_client: AsyncClient, username: str) -> dict[str, str]:
        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "Regularpass1!"},
        )
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_balance_get_does_not_create_wallet(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        target = User(
            username="balance-without-wallet",
            email="balance-without-wallet@example.com",
            password_hash=get_password_hash("Regularpass1!"),
            role="user",
            is_active=True,
        )
        db_session.add(target)
        await db_session.commit()
        await db_session.refresh(target)
        assert await db_session.scalar(select(UserWallet).where(UserWallet.user_id == target.id)) is None

        response = await async_client.get(f"/api/v1/finance/users/{target.id}/balance", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["balance"] == 0
        assert await db_session.scalar(select(UserWallet).where(UserWallet.user_id == target.id)) is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_wallet_ledger_rebuild_processes_more_than_one_batch(
        self,
        db_session,
        admin_user: User,
    ):
        wallet = UserWallet(user_id=admin_user.id, balance=0)
        db_session.add(wallet)
        await db_session.flush()
        await db_session.execute(
            WalletTransaction.__table__.insert(),
            [{"user_id": admin_user.id, "transaction_type": "deposit", "amount": 0.01} for _ in range(1001)],
        )

        await repair_wallet_ledger_internal(db_session)
        await db_session.refresh(wallet)
        last_transaction = await db_session.scalar(
            select(WalletTransaction)
            .where(WalletTransaction.user_id == admin_user.id)
            .order_by(WalletTransaction.created_at.desc(), WalletTransaction.id.desc())
            .limit(1)
        )

        assert wallet.balance == 10.01
        assert last_transaction is not None
        assert last_transaction.balance_after == 10.01

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_cost_center_assign_member_and_list_mine(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        await self._enable_basic_user_creation(db_session)
        created_user = await self._create_user_via_api(async_client, auth_headers, "carol")
        user_headers = await self._login_user(async_client, "carol")

        negative_budget_response = await async_client.post(
            "/api/v1/finance/cost-centers",
            json={"name": "Invalid Budget", "total_budget": -1},
            headers=auth_headers,
        )
        assert negative_budget_response.status_code == 422

        create_response = await async_client.post(
            "/api/v1/finance/cost-centers",
            json={
                "name": "Shared Lab",
                "monthly_budget": 120.0,
                "total_budget": 999.0,
                "is_active": True,
            },
            headers=auth_headers,
        )

        assert create_response.status_code == 200
        shared_center = create_response.json()
        assert shared_center["name"] == "Shared Lab"
        assert shared_center["monthly_budget"] == 120.0
        assert shared_center["total_budget"] is None
        assert shared_center["budget_mode"] == "monthly"

        member_response = await async_client.post(
            f"/api/v1/finance/cost-centers/{shared_center['id']}/members",
            json={"user_id": created_user["id"], "can_print": False},
            headers=auth_headers,
        )

        assert member_response.status_code == 200
        assert member_response.json()["user_id"] == created_user["id"]
        assert member_response.json()["can_print"] is False

        mine_response = await async_client.get("/api/v1/finance/cost-centers/mine", headers=user_headers)
        assert mine_response.status_code == 200
        mine_names = {center["name"] for center in mine_response.json()}
        assert "carol" in mine_names
        assert "Shared Lab" in mine_names

        detail_response = await async_client.get(
            f"/api/v1/finance/cost-centers/{shared_center['id']}", headers=auth_headers
        )
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert len(detail["members"]) == 1
        assert detail["members"][0]["user_id"] == created_user["id"]

        remove_response = await async_client.delete(
            f"/api/v1/finance/cost-centers/{shared_center['id']}/members/{created_user['id']}",
            headers=auth_headers,
        )
        assert remove_response.status_code == 200

        mine_after_remove = await async_client.get("/api/v1/finance/cost-centers/mine", headers=user_headers)
        assert mine_after_remove.status_code == 200
        assert {center["name"] for center in mine_after_remove.json()} == {"carol"}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_private_cost_center_cannot_be_deactivated_but_can_have_zero_budget(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        created_user = await self._create_user_via_api(async_client, auth_headers, "private-budget-user")
        private_center = await db_session.scalar(
            select(CostCenter).where(
                CostCenter.owner_user_id == created_user["id"],
                CostCenter.is_private.is_(True),
            )
        )
        assert private_center is not None

        deactivate_response = await async_client.patch(
            f"/api/v1/finance/cost-centers/{private_center.id}",
            json={"is_active": False},
            headers=auth_headers,
        )

        assert deactivate_response.status_code == 400
        assert "cannot be deactivated" in deactivate_response.json()["detail"]
        await db_session.refresh(private_center)
        assert private_center.is_active is True

        rename_response = await async_client.patch(
            f"/api/v1/finance/cost-centers/{private_center.id}",
            json={"name": "Renamed private center"},
            headers=auth_headers,
        )
        assert rename_response.status_code == 400
        await db_session.refresh(private_center)
        assert private_center.name == "private-budget-user"

        negative_budget_response = await async_client.patch(
            f"/api/v1/finance/cost-centers/{private_center.id}/budgets",
            json={"monthly_budget": -0.01},
            headers=auth_headers,
        )
        assert negative_budget_response.status_code == 422

        budget_response = await async_client.patch(
            f"/api/v1/finance/cost-centers/{private_center.id}/budgets",
            json={"total_budget": 0},
            headers=auth_headers,
        )

        assert budget_response.status_code == 200
        assert budget_response.json()["total_budget"] == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cost_center_available_budget_does_not_double_count_queue_reservation(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        center_response = await async_client.post(
            "/api/v1/finance/cost-centers",
            json={"name": "Reserved Once", "total_budget": 10.0},
            headers=auth_headers,
        )
        assert center_response.status_code == 200
        center_id = center_response.json()["id"]

        reserved_item = PrintQueueItem(
            cost_center_id=center_id,
            estimated_cost=3.0,
            status="pending",
            position=1,
        )
        legacy_unreserved_item = PrintQueueItem(
            cost_center_id=center_id,
            estimated_cost=2.0,
            status="pending",
            position=2,
        )
        db_session.add_all([reserved_item, legacy_unreserved_item])
        await db_session.flush()
        db_session.add(
            BudgetReservation(
                cost_center_id=center_id,
                amount=3.0,
                status="active",
                source_type="print_queue",
                source_id=reserved_item.id,
            )
        )
        await db_session.commit()

        response = await async_client.get("/api/v1/finance/cost-centers", headers=auth_headers)

        assert response.status_code == 200
        center = next(item for item in response.json() if item["id"] == center_id)
        # 3.00 active reservation + 2.00 legacy queue estimate, not 3 + 3 + 2.
        assert center["budget_available"] == 5.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cost_center_with_balanced_transactions_cannot_be_deleted(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        admin_user,
        db_session,
    ):
        center_response = await async_client.post(
            "/api/v1/finance/cost-centers",
            json={"name": "Balanced History"},
            headers=auth_headers,
        )
        center_id = center_response.json()["id"]
        db_session.add_all(
            [
                WalletTransaction(
                    user_id=admin_user.id,
                    cost_center_id=center_id,
                    transaction_type="deposit",
                    amount=50.0,
                    balance_after=50.0,
                ),
                WalletTransaction(
                    user_id=admin_user.id,
                    cost_center_id=center_id,
                    transaction_type="withdraw",
                    amount=-50.0,
                    balance_after=0.0,
                ),
            ]
        )
        await db_session.commit()

        response = await async_client.delete(
            f"/api/v1/finance/cost-centers/{center_id}",
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert "transactions reference it" in response.json()["detail"]
        transactions = (
            (await db_session.execute(select(WalletTransaction).where(WalletTransaction.cost_center_id == center_id)))
            .scalars()
            .all()
        )
        assert len(transactions) == 2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cost_center_with_active_reservation_cannot_be_deleted(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        center_response = await async_client.post(
            "/api/v1/finance/cost-centers",
            json={"name": "Active Hold"},
            headers=auth_headers,
        )
        center_id = center_response.json()["id"]
        db_session.add(
            BudgetReservation(
                cost_center_id=center_id,
                amount=3.0,
                status="active",
                source_type="direct_print",
                source_id=123,
            )
        )
        await db_session.commit()

        response = await async_client.delete(
            f"/api/v1/finance/cost-centers/{center_id}",
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert "active budget reservations" in response.json()["detail"]
        assert await db_session.get(CostCenter, center_id) is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_wallet_adjustments_and_transaction_ledger_rebuild(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        admin_user,
        db_session,
    ):
        """A user's private cost center and unassigned entries share one wallet."""
        await self._enable_basic_user_creation(db_session)
        created_user = await self._create_user_via_api(async_client, auth_headers, "dave")

        private_center = await db_session.scalar(
            select(CostCenter).where(CostCenter.owner_user_id == created_user["id"], CostCenter.is_private.is_(True))
        )
        assert private_center is not None

        # The owner's private cost center affects both its own ledger and the wallet.
        deposit = await async_client.post(
            f"/api/v1/finance/users/{created_user['id']}/deposit",
            json={"amount": 25.0, "description": "Initial CC top-up", "cost_center_id": private_center.id},
            headers=auth_headers,
        )
        assert deposit.status_code == 200
        assert deposit.json()["transaction"]["cost_center_id"] == private_center.id
        assert deposit.json()["transaction"]["balance_after"] == 25.0  # CC balance
        assert deposit.json()["balance"]["balance"] == 25.0  # Response shows CC balance

        # A withdrawal updates both views by the same amount.
        withdraw = await async_client.post(
            f"/api/v1/finance/users/{created_user['id']}/withdraw",
            json={"amount": 5.0, "description": "CC Usage", "cost_center_id": private_center.id},
            headers=auth_headers,
        )
        assert withdraw.status_code == 200
        assert withdraw.json()["transaction"]["amount"] == -5.0
        assert withdraw.json()["transaction"]["balance_after"] == 20.0  # CC balance after withdraw
        assert withdraw.json()["balance"]["balance"] == 20.0  # Response shows CC balance

        # Personal deposit: affects user wallet
        personal_deposit = await async_client.post(
            f"/api/v1/finance/users/{created_user['id']}/deposit",
            json={"amount": 30.0, "description": "Personal top-up", "cost_center_id": None},
            headers=auth_headers,
        )
        assert personal_deposit.status_code == 200
        assert personal_deposit.json()["transaction"]["cost_center_id"] is None
        assert personal_deposit.json()["transaction"]["balance_after"] == 50.0
        assert personal_deposit.json()["balance"]["balance"] == 50.0

        transactions_response = await async_client.get(
            f"/api/v1/finance/users/{created_user['id']}/transactions", headers=auth_headers
        )
        assert transactions_response.status_code == 200
        transactions = transactions_response.json()
        assert len(transactions) == 3
        cc_txs = [tx for tx in transactions if tx["cost_center_id"] == private_center.id]
        personal_txs = [tx for tx in transactions if tx["cost_center_id"] is None]
        assert len(cc_txs) == 2
        assert len(personal_txs) == 1

        balance_response = await async_client.get(
            f"/api/v1/finance/users/{created_user['id']}/balance", headers=auth_headers
        )
        assert balance_response.status_code == 200
        assert balance_response.json()["balance"] == 50.0

        # Delete personal transaction, user wallet should decrease
        personal_tx = next(tx for tx in transactions if tx["cost_center_id"] is None)
        delete_response = await async_client.delete(
            f"/api/v1/finance/transactions/{personal_tx['id']}", headers=auth_headers
        )
        assert delete_response.status_code == 200

        balance_after_delete = await async_client.get(
            f"/api/v1/finance/users/{created_user['id']}/balance", headers=auth_headers
        )
        assert balance_after_delete.status_code == 200
        assert balance_after_delete.json()["balance"] == 20.0

        remaining = await async_client.get(
            f"/api/v1/finance/users/{created_user['id']}/transactions", headers=auth_headers
        )
        assert remaining.status_code == 200
        assert len(remaining.json()) == 2  # 2 CC transactions remain

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_admin_and_user_balances_agree_after_charge_adjustment_and_delete(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        created_user = await self._create_user_via_api(async_client, auth_headers, "balance-lifecycle")
        user = await db_session.get(User, created_user["id"])
        operators = await db_session.scalar(select(Group).where(Group.name == "Operators"))
        assert operators is not None
        user.groups.append(operators)
        await db_session.commit()
        user_headers = await self._login_user(async_client, "balance-lifecycle")
        private_center = await db_session.scalar(
            select(CostCenter).where(CostCenter.owner_user_id == user.id, CostCenter.is_private.is_(True))
        )
        assert private_center is not None

        deposit = await async_client.post(
            f"/api/v1/finance/users/{user.id}/deposit",
            json={"amount": 20.0, "cost_center_id": private_center.id},
            headers=auth_headers,
        )
        assert deposit.status_code == 200

        archive = PrintArchive(
            filename="charged.gcode.3mf",
            file_path="archives/test/charged.gcode.3mf",
            file_size=10,
            status="completed",
            cost=4.0,
            created_by_id=user.id,
            cost_center_id=private_center.id,
        )
        db_session.add(archive)
        await db_session.commit()
        assert await apply_print_charge_for_archive(db_session, archive.id) is True
        await db_session.commit()

        adjustment = await async_client.post(
            f"/api/v1/finance/users/{user.id}/deposit",
            json={"amount": 5.0, "description": "temporary adjustment"},
            headers=auth_headers,
        )
        assert adjustment.status_code == 200

        async def assert_views_agree(expected: float):
            admin_view = await async_client.get(
                f"/api/v1/finance/users/{user.id}/balance",
                headers=auth_headers,
            )
            user_view = await async_client.get("/api/v1/finance/me/balance", headers=user_headers)
            assert admin_view.status_code == 200
            assert user_view.status_code == 200
            assert admin_view.json()["balance"] == expected
            assert user_view.json()["balance"] == expected

        await assert_views_agree(21.0)

        delete_response = await async_client.delete(
            f"/api/v1/finance/transactions/{adjustment.json()['transaction']['id']}",
            headers=auth_headers,
        )
        assert delete_response.status_code == 200
        await assert_views_agree(16.0)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_cost_center_transaction_rebuilds_remaining_ledger(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        await self._enable_basic_user_creation(db_session)
        created_user = await self._create_user_via_api(async_client, auth_headers, "erin")

        shared_center = await db_session.scalar(
            select(CostCenter).where(CostCenter.owner_user_id == created_user["id"], CostCenter.is_private.is_(True))
        )
        assert shared_center is not None

        first_deposit = await async_client.post(
            f"/api/v1/finance/users/{created_user['id']}/deposit",
            json={"amount": 25.0, "description": "CC top-up", "cost_center_id": shared_center.id},
            headers=auth_headers,
        )
        assert first_deposit.status_code == 200

        cc_withdraw = await async_client.post(
            f"/api/v1/finance/users/{created_user['id']}/withdraw",
            json={"amount": 5.0, "description": "CC usage", "cost_center_id": shared_center.id},
            headers=auth_headers,
        )
        assert cc_withdraw.status_code == 200

        personal_deposit = await async_client.post(
            f"/api/v1/finance/users/{created_user['id']}/deposit",
            json={"amount": 12.0, "description": "Personal top-up", "cost_center_id": None},
            headers=auth_headers,
        )
        assert personal_deposit.status_code == 200

        delete_response = await async_client.delete(
            f"/api/v1/finance/transactions/{first_deposit.json()['transaction']['id']}",
            headers=auth_headers,
        )
        assert delete_response.status_code == 200

        transactions_response = await async_client.get(
            f"/api/v1/finance/users/{created_user['id']}/transactions", headers=auth_headers
        )
        assert transactions_response.status_code == 200
        transactions = transactions_response.json()
        assert len(transactions) == 2

        cc_transaction = next(tx for tx in transactions if tx["cost_center_id"] == shared_center.id)
        assert cc_transaction["amount"] == -5.0
        assert cc_transaction["balance_after"] == -5.0

        balance_response = await async_client.get(
            f"/api/v1/finance/users/{created_user['id']}/balance", headers=auth_headers
        )
        assert balance_response.status_code == 200
        assert balance_response.json()["balance"] == 7.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_print_charge_stays_deleted_after_recalculate(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        await self._enable_basic_user_creation(db_session)
        created_user = await self._create_user_via_api(async_client, auth_headers, "frank")
        user = await db_session.scalar(select(User).where(User.id == created_user["id"]))
        assert user is not None
        user_id = user.id

        archive = PrintArchive(
            printer_id=None,
            filename="print.gcode",
            file_path="archives/test/print.gcode",
            file_size=10,
            content_hash="hash-print",
            status="completed",
            cost=4.0,
            created_by_id=user.id,
        )
        db_session.add(archive)
        await db_session.flush()

        tx = WalletTransaction(
            user_id=user.id,
            transaction_type="print_charge",
            amount=-4.0,
            balance_after=-4.0,
            description="Print charge: print.gcode",
            created_by_user_id=None,
            print_run_id="deleted-print-run",
            print_archive_id=archive.id,
        )
        db_session.add(tx)
        await db_session.commit()
        archive_id = archive.id

        tx_rows_before = (
            (await db_session.execute(select(WalletTransaction).where(WalletTransaction.user_id == user_id)))
            .scalars()
            .all()
        )
        assert len(tx_rows_before) == 1

        delete_response = await async_client.delete(
            f"/api/v1/finance/transactions/{tx_rows_before[0].id}", headers=auth_headers
        )
        assert delete_response.status_code == 200
        db_session.expire_all()

        tx_rows_after = (
            (await db_session.execute(select(WalletTransaction).where(WalletTransaction.user_id == user_id)))
            .scalars()
            .all()
        )
        assert len(tx_rows_after) == 1
        assert tx_rows_after[0].is_voided is True

        # The voided run remains an idempotency tombstone and cannot be
        # recreated by a delayed duplicate completion callback.
        assert (
            await apply_print_charge_for_archive(
                db_session,
                archive_id,
                print_run_id="deleted-print-run",
            )
        ) is False

        # A later reprint of the same archive has a distinct run identity and
        # must still be charged normally.
        assert (
            await apply_print_charge_for_archive(
                db_session,
                archive_id,
                charged_user_id=user_id,
                print_run_id="later-reprint-run",
            )
        ) is True
        await db_session.commit()
        visible = await async_client.get(
            f"/api/v1/finance/users/{user_id}/transactions",
            headers=auth_headers,
        )
        assert visible.status_code == 200
        assert [row["print_run_id"] for row in visible.json()] == ["later-reprint-run"]

    async def test_edit_transaction_updates_ledger(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        """Test that editing a transaction (user, cost_center, amount, description) rebuilds ledger."""
        await self._enable_basic_user_creation(db_session)
        user1 = await self._create_user_via_api(async_client, auth_headers, "user1")
        user2 = await self._create_user_via_api(async_client, auth_headers, "user2")

        # Create a cost center
        cc_response = await async_client.post(
            "/api/v1/finance/cost-centers",
            json={"name": "Test Center", "is_active": True},
            headers=auth_headers,
        )
        assert cc_response.status_code == 200
        cost_center = cc_response.json()

        # Get user records from DB
        user1_db = await db_session.scalar(select(User).where(User.id == user1["id"]))
        user2_db = await db_session.scalar(select(User).where(User.id == user2["id"]))

        # Create a personal transaction for user1
        tx_response = await async_client.post(
            f"/api/v1/finance/users/{user1_db.id}/deposit",
            json={"amount": 50.0, "description": "Initial deposit"},
            headers=auth_headers,
        )
        assert tx_response.status_code == 200
        tx_data = tx_response.json()
        tx_id = tx_data["transaction"]["id"]

        # Get the original transaction
        original_tx = await db_session.scalar(select(WalletTransaction).where(WalletTransaction.id == tx_id))
        assert original_tx.user_id == user1_db.id
        assert original_tx.cost_center_id is None
        assert original_tx.amount == 50.0
        assert original_tx.balance_after == 50.0

        # Edit the transaction: change user, add cost center, change amount
        edit_response = await async_client.patch(
            f"/api/v1/finance/transactions/{tx_id}",
            json={
                "user_id": user2_db.id,
                "cost_center_id": cost_center["id"],
                "amount": 75.0,
                "description": "Updated deposit (Admin edit)",
            },
            headers=auth_headers,
        )
        assert edit_response.status_code == 200
        edited_tx_data = edit_response.json()

        # Verify transaction was updated
        assert edited_tx_data["user_id"] == user2_db.id
        assert edited_tx_data["cost_center_id"] == cost_center["id"]
        assert edited_tx_data["amount"] == 75.0
        # Description should have "(Admin edit)" appended
        assert "(Admin edit)" in edited_tx_data["description"]

        # An explicit null moves the transaction back to the personal ledger.
        clear_response = await async_client.patch(
            f"/api/v1/finance/transactions/{tx_id}",
            json={"cost_center_id": None},
            headers=auth_headers,
        )
        assert clear_response.status_code == 200
        assert clear_response.json()["cost_center_id"] is None

        invalid_user_response = await async_client.patch(
            f"/api/v1/finance/transactions/{tx_id}",
            json={"user_id": 2147483647},
            headers=auth_headers,
        )
        assert invalid_user_response.status_code == 404
        assert invalid_user_response.json()["detail"] == "User not found"

        invalid_center_response = await async_client.patch(
            f"/api/v1/finance/transactions/{tx_id}",
            json={"cost_center_id": 2147483647},
            headers=auth_headers,
        )
        assert invalid_center_response.status_code == 404
        assert invalid_center_response.json()["detail"] == "Cost center not found"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_manual_print_and_recalculates_ledger(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        """Posting a manual print (manual_adjustment) creates a transaction and rebuilds ledger."""
        await self._enable_basic_user_creation(db_session)
        created_user = await self._create_user_via_api(async_client, auth_headers, "gina")

        # Get user DB record
        user = await db_session.scalar(select(User).where(User.id == created_user["id"]))
        assert user is not None

        # Private cost center for user
        private_cc = await db_session.scalar(
            select(CostCenter).where(CostCenter.owner_user_id == created_user["id"], CostCenter.is_private.is_(True))
        )
        assert private_cc is not None

        # Post manual print affecting the cost center
        payload = {
            "user_id": user.id,
            "cost_center_id": private_cc.id,
            "amount": 4.0,
            "description": "Manual adjustment for a print",
            "created_at": "2026-05-12T12:00:00Z",
        }

        response = await async_client.post("/api/v1/finance/transactions/manual", json=payload, headers=auth_headers)
        assert response.status_code == 200
        resp_json = response.json()
        assert "transaction" in resp_json or "id" in resp_json

        # Response contains the created transaction details
        assert resp_json["transaction_type"] == "manual_adjustment"
        assert resp_json["amount"] == -4.0
        assert resp_json["cost_center_id"] == private_cc.id

        # The response includes the computed running balance for the transaction
        assert resp_json.get("balance_after") == -4.0

        negative_amount_response = await async_client.post(
            "/api/v1/finance/transactions/manual",
            json={**payload, "amount": -1},
            headers=auth_headers,
        )
        assert negative_amount_response.status_code == 422

        invalid_user_response = await async_client.post(
            "/api/v1/finance/transactions/manual",
            json={**payload, "user_id": 2147483647},
            headers=auth_headers,
        )
        assert invalid_user_response.status_code == 404
        assert invalid_user_response.json()["detail"] == "User not found"

        invalid_center_response = await async_client.post(
            "/api/v1/finance/transactions/manual",
            json={**payload, "cost_center_id": 2147483647},
            headers=auth_headers,
        )
        assert invalid_center_response.status_code == 404
        assert invalid_center_response.json()["detail"] == "Cost center not found"


class TestPartialPrintChargesIntegration:
    """Integration tests for partial print charge calculation."""

    @pytest.fixture
    async def admin_user(self, db_session):
        user = User(
            username="partial-admin",
            email="partial-admin@example.com",
            password_hash=get_password_hash("AdminPass1!"),
            role="admin",
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    @pytest.fixture
    async def auth_headers(self, async_client: AsyncClient, db_session, admin_user):
        db_session.add(Settings(key="auth_enabled", value="true"))
        db_session.add(Settings(key="advanced_auth_enabled", value="false"))
        # Ensure billing is enabled for these partial-charge integration tests
        existing = await db_session.scalar(select(Settings).where(Settings.key == "billing_enabled"))
        if existing is None:
            db_session.add(Settings(key="billing_enabled", value="true"))
        else:
            existing.value = "true"
        await db_session.commit()

        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": "AdminPass1!"},
        )
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    async def _create_user_via_api(self, async_client: AsyncClient, auth_headers: dict[str, str], username: str):
        response = await async_client.post(
            "/api/v1/users",
            json={
                "username": username,
                "password": "Regularpass1!",
                "email": f"{username}@example.com",
                "role": "user",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        return response.json()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_aborted_print_charges_proportionally_via_recalculate_endpoint(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        """Verify aborted prints are included in recalculate and charged proportionally."""
        created_user = await self._create_user_via_api(async_client, auth_headers, "frank")
        user = await db_session.scalar(select(User).where(User.id == created_user["id"]))
        assert user is not None

        # Wallet is already created by ensure_user_finance_defaults during user creation

        # Archive: completed print (100% charge)
        completed = PrintArchive(
            printer_id=None,
            filename="completed.3mf",
            file_path="archives/test/completed.3mf",
            file_size=100,
            content_hash="partial-complete",
            status="completed",
            cost=10.0,
            created_by_id=user.id,
        )

        # Archive: aborted print (50% filament used = 50% charge)
        aborted = PrintArchive(
            printer_id=None,
            filename="aborted.3mf",
            file_path="archives/test/aborted.3mf",
            file_size=100,
            content_hash="partial-aborted",
            status="aborted",
            cost=8.0,
            filament_used_grams=50.0,
            extra_data={"filament_grams_total": 100.0},
            created_by_id=user.id,
        )

        # Archive: failed print (0% filament used = no charge)
        failed = PrintArchive(
            printer_id=None,
            filename="failed.3mf",
            file_path="archives/test/failed.3mf",
            file_size=100,
            content_hash="partial-failed",
            status="failed",
            cost=5.0,
            filament_used_grams=0.0,
            created_by_id=user.id,
        )

        db_session.add_all([completed, aborted, failed])
        await db_session.commit()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_partial_charges_appear_in_transaction_ledger(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        """Verify transaction descriptions indicate partial charges."""
        created_user = await self._create_user_via_api(async_client, auth_headers, "grace")
        user = await db_session.scalar(select(User).where(User.id == created_user["id"]))
        assert user is not None

        # Wallet is already created by ensure_user_finance_defaults during user creation

        cancelled = PrintArchive(
            printer_id=None,
            filename="cancelled.3mf",
            file_path="archives/test/cancelled.3mf",
            file_size=100,
            content_hash="partial-cancel",
            status="cancelled",
            cost=12.0,
            filament_used_grams=25.0,
            extra_data={"filament_grams_total": 100.0},
            print_name="Partially Cancelled Print",
            created_by_id=user.id,
        )
        db_session.add(cancelled)
        await db_session.commit()

        from backend.app.services.finance_billing import apply_print_charge_for_archive

        changed = await apply_print_charge_for_archive(db_session, cancelled.id)
        assert changed is True
        await db_session.commit()

        tx_response = await async_client.get(
            f"/api/v1/finance/users/{created_user['id']}/transactions", headers=auth_headers
        )
        assert tx_response.status_code == 200
        transactions = tx_response.json()
        assert len(transactions) == 1

        tx = transactions[0]
        assert tx["transaction_type"] == "print_charge"
        assert tx["amount"] == -3.0  # 25% of 12.0
        assert "cancelled" in tx["description"].lower()
        assert "25.0g/100.0" in tx["description"]  # filament amounts in description

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_partial_charges_with_cost_center_override(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        """Verify partial charges respect cost_center_id when present."""
        created_user = await self._create_user_via_api(async_client, auth_headers, "henry")
        user = await db_session.scalar(select(User).where(User.id == created_user["id"]))
        assert user is not None

        # Create cost centers
        default_cc = CostCenter(name="Default CC", owner_user_id=user.id, is_active=True, is_private=False)
        lab_cc = CostCenter(name="Lab CC", owner_user_id=user.id, is_active=True, is_private=False)
        db_session.add_all([default_cc, lab_cc])
        await db_session.flush()

        # Wallet is already created by ensure_user_finance_defaults during user creation

        # Archive assigned to default_cc
        aborted = PrintArchive(
            printer_id=None,
            filename="aborted_cc.3mf",
            file_path="archives/test/aborted_cc.3mf",
            file_size=100,
            content_hash="partial-cc",
            status="aborted",
            cost=6.0,
            filament_used_grams=30.0,
            extra_data={"filament_grams_total": 100.0},
            cost_center_id=default_cc.id,
            created_by_id=user.id,
        )
        db_session.add(aborted)
        await db_session.commit()

        # Manually apply charge with override
        from backend.app.services.finance_billing import apply_print_charge_for_archive

        changed = await apply_print_charge_for_archive(
            db_session,
            aborted.id,
            cost_center_id=lab_cc.id,
        )
        await db_session.commit()

        assert changed is True

        tx_response = await async_client.get(
            f"/api/v1/finance/users/{created_user['id']}/transactions", headers=auth_headers
        )
        assert tx_response.status_code == 200
        transactions = tx_response.json()
        assert len(transactions) == 1

        tx = transactions[0]
        assert tx["cost_center_id"] == lab_cc.id  # Overridden to lab_cc
        assert tx["amount"] == pytest.approx(-1.8, abs=0.01)  # 30% of 6.0


class TestFinanceUserDefaults:
    """Tests for user creation and finance defaults initialization."""

    @pytest.fixture
    async def admin_user(self, db_session):
        user = User(
            username="billing-admin",
            email="billing-admin@example.com",
            password_hash=get_password_hash("AdminPass1!"),
            role="admin",
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    @pytest.fixture
    async def auth_headers(self, async_client: AsyncClient, db_session, admin_user):
        db_session.add(Settings(key="auth_enabled", value="true"))
        db_session.add(Settings(key="advanced_auth_enabled", value="false"))
        await db_session.commit()

        response = await async_client.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": "AdminPass1!"},
        )
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    async def _create_user_via_api(self, async_client: AsyncClient, auth_headers: dict[str, str], username: str):
        response = await async_client.post(
            "/api/v1/users",
            json={
                "username": username,
                "password": "Regularpass1!",
                "email": f"{username}@example.com",
                "role": "user",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        return response.json()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_user_initializes_wallet_and_private_cost_center(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        """Verify user creation initializes wallet, private cost center, and membership."""
        result = await async_client.post(
            "/api/v1/users",
            json={
                "username": "alice",
                "password": "Regularpass1!",
                "email": "alice@example.com",
                "role": "user",
            },
            headers=auth_headers,
        )

        assert result.status_code == 201
        created = result.json()
        assert created["username"] == "alice"

        user = await db_session.scalar(select(User).where(User.username == "alice"))
        assert user is not None

        from backend.app.models.finance import CostCenterMember

        wallet = await db_session.scalar(select(UserWallet).where(UserWallet.user_id == user.id))
        assert wallet is not None
        assert wallet.balance == 0.0

        private_center = await db_session.scalar(
            select(CostCenter).where(CostCenter.owner_user_id == user.id, CostCenter.is_private.is_(True))
        )
        assert private_center is not None
        assert private_center.name == "alice"

        membership = await db_session.scalar(
            select(CostCenterMember).where(
                CostCenterMember.cost_center_id == private_center.id,
                CostCenterMember.user_id == user.id,
            )
        )
        assert membership is not None
        assert membership.can_print is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_user_keeps_private_cost_center_in_sync(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        """Verify user updates keep private cost center name in sync."""
        created = await self._create_user_via_api(async_client, auth_headers, "bob")

        response = await async_client.patch(
            f"/api/v1/users/{created['id']}",
            json={"username": "bobby"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["username"] == "bobby"

        user = await db_session.scalar(select(User).where(User.id == created["id"]))
        assert user is not None

        private_centers = (
            (
                await db_session.execute(
                    select(CostCenter).where(CostCenter.owner_user_id == user.id, CostCenter.is_private.is_(True))
                )
            )
            .scalars()
            .all()
        )
        assert len(private_centers) == 1
        assert private_centers[0].name == "bobby"

        wallet = await db_session.scalar(select(UserWallet).where(UserWallet.user_id == user.id))
        assert wallet is not None


class TestFinanceCurrency(TestFinanceAPI):
    """#3123: every balance is reported in the install's configured currency.

    Wallets used to carry a currency of their own, which three of its four
    writers filled with a hardcoded "EUR" and the Finance page rendered as it
    found it -- so an install set to AUD showed a euro balance. The column is
    gone; these tests pin what replaced it.
    """

    @pytest.fixture
    async def aud_install(self, db_session):
        existing = await db_session.scalar(select(Settings).where(Settings.key == "currency"))
        if existing is None:
            db_session.add(Settings(key="currency", value="AUD"))
        else:
            existing.value = "AUD"
        await db_session.commit()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_balance_reports_the_configured_currency_without_a_wallet(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        admin_user,
        aud_install,
    ):
        """The read-only path used to answer a flat "EUR" when no wallet row existed."""
        assert await db_session.scalar(select(UserWallet).where(UserWallet.user_id == admin_user.id)) is None

        response = await async_client.get("/api/v1/finance/me/balance", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["currency"] == "AUD"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_existing_wallet_is_reported_in_the_configured_currency(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        admin_user,
        aud_install,
    ):
        """The reporter's case: a wallet created back when the install said EUR."""
        db_session.add(UserWallet(user_id=admin_user.id, balance=12.34))
        await db_session.commit()

        response = await async_client.get("/api/v1/finance/me/balance", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["balance"] == 12.34
        assert response.json()["currency"] == "AUD"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_adjustment_answers_in_the_configured_currency(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        admin_user,
        aud_install,
    ):
        """_get_or_create_wallet is the path that used to write EUR into the database."""
        response = await async_client.post(
            f"/api/v1/finance/users/{admin_user.id}/deposit",
            json={"amount": 5.0, "description": "currency check"},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["balance"]["currency"] == "AUD"
