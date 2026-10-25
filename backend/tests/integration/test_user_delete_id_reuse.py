"""A new user must not inherit what belonged to a deleted user.

SQLite runs with foreign keys off and gives the highest freed id to the next
row, so deleting the newest user and creating another one handed the new user
everything still attached to that id: wallet balance, transactions, cost centre
memberships, a private cost centre, private library folders.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from backend.app.core import sqlite_fk
from backend.app.core.database import load_all_models
from backend.app.models.finance import CostCenter, CostCenterMember, UserWallet, WalletTransaction
from backend.app.models.library import LibraryFolder
from backend.app.models.settings import Settings
from backend.app.models.user import User


async def _user(db, name: str, user_id: int | None = None) -> int:
    user = User(id=user_id, username=name, password_hash="x", role="user")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user.id


async def _belongings(db, user_id: int) -> tuple[int, int]:
    """A wallet, a deposit, a can_print membership, a private cost centre and a private folder."""
    shared = CostCenter(name="Lab", is_private=False)
    private = CostCenter(name="Private", is_private=True, owner_user_id=user_id)
    db.add_all([shared, private])
    await db.commit()
    folder = LibraryFolder(name="Secret", created_by_id=user_id)
    db.add_all(
        [
            UserWallet(user_id=user_id, balance=42.0),
            CostCenterMember(cost_center_id=shared.id, user_id=user_id, can_print=True),
            WalletTransaction(user_id=user_id, amount=42.0, transaction_type="deposit"),
            folder,
        ]
    )
    await db.commit()
    return private.id, folder.id


async def _inherited(db, user_id: int) -> dict[str, list]:
    checks = {
        "wallet": select(UserWallet.balance).where(UserWallet.user_id == user_id),
        "transactions": select(WalletTransaction.amount).where(WalletTransaction.user_id == user_id),
        "memberships": select(CostCenterMember.can_print).where(CostCenterMember.user_id == user_id),
        "private cost centres": select(CostCenter.name).where(CostCenter.owner_user_id == user_id),
        "folders": select(LibraryFolder.name).where(LibraryFolder.created_by_id == user_id),
    }
    return {name: list((await db.execute(query)).scalars().all()) for name, query in checks.items()}


NOTHING = {"wallet": [], "transactions": [], "memberships": [], "private cost centres": [], "folders": []}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_user_with_the_deleted_users_id_inherits_nothing(async_client: AsyncClient, db_session):
    alice = await _user(db_session, "alice")
    private_id, folder_id = await _belongings(db_session, alice)

    response = await async_client.delete(f"/api/v1/users/{alice}")
    assert response.status_code == 204, response.text
    # AUTOINCREMENT (core/database) now stops SQLite reusing ids; the new user is
    # put on the freed id by hand, as on databases from before, to keep the
    # delete's own cleanup covered.
    bob = await _user(db_session, "bob", alice)

    assert bob == alice
    assert await _inherited(db_session, bob) == NOTHING
    # What was alice's private cost centre and folder now has no owner, which
    # leaves them to admins (not to everyone).
    owner = (await db_session.execute(select(CostCenter.owner_user_id).where(CostCenter.id == private_id))).scalar()
    folder = (await db_session.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))).scalar_one()
    assert owner is None
    assert folder.created_by_id is None and not folder.shared


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_startup_repair_clears_what_an_earlier_delete_left(db_session):
    """Installs that already deleted a user before this fix are repaired at start."""
    alice = await _user(db_session, "alice")
    await _belongings(db_session, alice)
    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": alice})  # the old way
    await db_session.commit()

    await sqlite_fk.sweep_orphans(await db_session.connection())
    await db_session.commit()

    assert await _inherited(db_session, alice) == NOTHING


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_ledger_keeps_a_deleted_users_transactions(async_client: AsyncClient, db_session):
    """Their charges to a shared cost centre stay spent, and the ledger stays whole."""
    from backend.app.core.database import repair_wallet_ledger_internal

    alice = await _user(db_session, "alice")
    lab = CostCenter(name="Lab", is_private=False, total_budget=100.0)
    db_session.add(lab)
    await db_session.commit()
    db_session.add_all(
        [
            WalletTransaction(user_id=alice, amount=42.0, balance_after=42.0, transaction_type="deposit"),
            WalletTransaction(
                user_id=alice, cost_center_id=lab.id, amount=-30.0, balance_after=-30.0, transaction_type="print_charge"
            ),
        ]
    )
    await db_session.commit()

    response = await async_client.delete(f"/api/v1/users/{alice}")
    assert response.status_code == 204, response.text
    await repair_wallet_ledger_internal(db_session)
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(WalletTransaction.user_id, WalletTransaction.cost_center_id, WalletTransaction.balance_after)
            .order_by(WalletTransaction.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    assert [tuple(row) for row in rows] == [(None, None, 42.0), (None, lab.id, -30.0)]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_startup_repair_keeps_the_transactions_of_an_earlier_delete(db_session):
    alice = await _user(db_session, "alice")
    db_session.add(WalletTransaction(user_id=alice, amount=-5.0, transaction_type="print_charge"))
    await db_session.commit()
    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": alice})
    await db_session.commit()

    await sqlite_fk.sweep_orphans(await db_session.connection())
    await db_session.commit()

    amounts = (await db_session.execute(select(WalletTransaction.amount, WalletTransaction.user_id))).all()
    assert [tuple(row) for row in amounts] == [(-5.0, None)]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_next_user_with_the_id_is_not_asked_for_an_email_code(async_client: AsyncClient, db_session):
    """Email 2FA is a settings key named after the user id, which no foreign key covers."""
    from backend.app.api.routes.mfa import _get_email_2fa_enabled, _set_email_2fa_enabled

    alice = await _user(db_session, "alice")
    await _set_email_2fa_enabled(db_session, alice, True)
    await db_session.commit()

    response = await async_client.delete(f"/api/v1/users/{alice}")
    assert response.status_code == 204, response.text
    bob = await _user(db_session, "bob", alice)  # on the freed id, as before AUTOINCREMENT

    assert bob == alice
    assert await _get_email_2fa_enabled(db_session, bob) is False


@pytest.mark.sqlite_only  # the startup repair of SQLite leftovers
@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_startup_repair_drops_email_2fa_of_users_already_deleted(db_session):
    from backend.app.api.routes.mfa import _get_email_2fa_enabled, _set_email_2fa_enabled
    from backend.app.core.database import _repair_sqlite_orphans

    alice, keep = await _user(db_session, "alice"), await _user(db_session, "keep")
    await _set_email_2fa_enabled(db_session, alice, True)
    await _set_email_2fa_enabled(db_session, keep, True)
    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": alice})  # the old way
    await db_session.commit()

    await _repair_sqlite_orphans(await db_session.connection())
    await db_session.commit()

    keys = (await db_session.execute(select(Settings.key).where(Settings.key.like("user_%_email_2fa_enabled")))).all()
    assert [key for (key,) in keys] == [f"user_{keep}_email_2fa_enabled"]
    assert await _get_email_2fa_enabled(db_session, keep) is True


def test_every_reference_to_users_is_applied():
    """A new table that points at users must not bring the inheritance back."""
    to_users = {
        (table.name, fk.parent.name)
        for table in load_all_models().sorted_tables
        for fk in table.foreign_keys
        if fk.column.table.name == "users" and fk.ondelete in ("CASCADE", "SET NULL")
    }
    assert to_users
    assert to_users - sqlite_fk.APPLIED == set()
