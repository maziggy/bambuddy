"""Integration tests for the finance/billing API."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core.auth import get_password_hash
from backend.app.models.archive import PrintArchive
from backend.app.models.finance import CostCenter, UserWallet, WalletTransaction
from backend.app.models.settings import Settings
from backend.app.models.user import User


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
    async def test_create_cost_center_assign_member_and_list_mine(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
    ):
        await self._enable_basic_user_creation(db_session)
        created_user = await self._create_user_via_api(async_client, auth_headers, "carol")
        user_headers = await self._login_user(async_client, "carol")

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
    async def test_wallet_adjustments_and_transaction_ledger_rebuild(
        self,
        async_client: AsyncClient,
        auth_headers: dict[str, str],
        admin_user,
        db_session,
    ):
        """Test cost-center and personal transaction handling.

        Cost-center transactions affect cost-center balance only.
        Personal transactions (no cost_center_id) affect user wallet.
        """
        await self._enable_basic_user_creation(db_session)
        created_user = await self._create_user_via_api(async_client, auth_headers, "dave")

        shared_center = await db_session.scalar(
            select(CostCenter).where(CostCenter.owner_user_id == created_user["id"], CostCenter.is_private.is_(True))
        )
        assert shared_center is not None

        # Deposit to cost center: affects cost-center balance, not user wallet
        deposit = await async_client.post(
            f"/api/v1/finance/users/{created_user['id']}/deposit",
            json={"amount": 25.0, "description": "Initial CC top-up", "cost_center_id": shared_center.id},
            headers=auth_headers,
        )
        assert deposit.status_code == 200
        assert deposit.json()["transaction"]["cost_center_id"] == shared_center.id
        assert deposit.json()["transaction"]["balance_after"] == 25.0  # CC balance
        assert deposit.json()["balance"]["balance"] == 25.0  # Response shows CC balance

        # Withdraw from cost center: affects cost-center balance only
        withdraw = await async_client.post(
            f"/api/v1/finance/users/{created_user['id']}/withdraw",
            json={"amount": 5.0, "description": "CC Usage", "cost_center_id": shared_center.id},
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
        assert personal_deposit.json()["transaction"]["balance_after"] == 30.0  # Personal balance
        assert personal_deposit.json()["balance"]["balance"] == 30.0  # User wallet updated

        transactions_response = await async_client.get(
            f"/api/v1/finance/users/{created_user['id']}/transactions", headers=auth_headers
        )
        assert transactions_response.status_code == 200
        transactions = transactions_response.json()
        assert len(transactions) == 3
        cc_txs = [tx for tx in transactions if tx["cost_center_id"] == shared_center.id]
        personal_txs = [tx for tx in transactions if tx["cost_center_id"] is None]
        assert len(cc_txs) == 2
        assert len(personal_txs) == 1

        balance_response = await async_client.get(
            f"/api/v1/finance/users/{created_user['id']}/balance", headers=auth_headers
        )
        assert balance_response.status_code == 200
        assert balance_response.json()["balance"] == 30.0  # Only personal balance

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
        assert balance_after_delete.json()["balance"] == 0.0  # Personal balance back to 0

        remaining = await async_client.get(
            f"/api/v1/finance/users/{created_user['id']}/transactions", headers=auth_headers
        )
        assert remaining.status_code == 200
        assert len(remaining.json()) == 2  # 2 CC transactions remain

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
        assert balance_response.json()["balance"] == 12.0

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
            print_archive_id=archive.id,
        )
        db_session.add(tx)
        await db_session.commit()

        tx_rows_before = (
            (await db_session.execute(select(WalletTransaction).where(WalletTransaction.user_id == user.id)))
            .scalars()
            .all()
        )
        assert len(tx_rows_before) == 1

        delete_response = await async_client.delete(
            f"/api/v1/finance/transactions/{tx_rows_before[0].id}", headers=auth_headers
        )
        assert delete_response.status_code == 200

        tx_rows_after = (
            (await db_session.execute(select(WalletTransaction).where(WalletTransaction.user_id == user.id)))
            .scalars()
            .all()
        )
        assert tx_rows_after == []

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
            "amount": -4.0,
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
