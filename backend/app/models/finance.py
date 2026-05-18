from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from backend.app.core.database import Base

if TYPE_CHECKING:
    from backend.app.models.archive import PrintArchive
    from backend.app.models.print_queue import PrintQueueItem
    from backend.app.models.user import User


class TransactionType(str, PyEnum):
    PRINT_CHARGE = "print_charge"
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"
    MANUAL_ADJUSTMENT = "manual_adjustment"


VALID_TRANSACTION_TYPES = {item.value for item in TransactionType}


def normalize_transaction_type(value: str | TransactionType) -> str:
    if isinstance(value, TransactionType):
        return value.value
    if value not in VALID_TRANSACTION_TYPES:
        raise ValueError(f"Invalid transaction type: {value}")
    return value


class UserWallet(Base):
    """Per-user wallet balance.

    Balance updates are driven by wallet transactions.
    """

    __tablename__ = "user_wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship()


class CostCenter(Base):
    """Cost center for assigning print costs and budgets."""

    __tablename__ = "cost_centers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, default=lambda: uuid.uuid4().hex[:12])
    name: Mapped[str] = mapped_column(String(150), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    total_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    monthly_budget: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    owner: Mapped[User | None] = relationship()
    members: Mapped[list[CostCenterMember]] = relationship(
        "CostCenterMember",
        back_populates="cost_center",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class CostCenterMember(Base):
    """User-to-cost-center assignment with print permission."""

    __tablename__ = "cost_center_members"
    __table_args__ = (UniqueConstraint("cost_center_id", "user_id", name="uq_cost_center_members_cc_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cost_center_id: Mapped[int] = mapped_column(ForeignKey("cost_centers.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    can_print: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    cost_center: Mapped[CostCenter] = relationship("CostCenter", back_populates="members")
    user: Mapped[User] = relationship()


class BudgetReservation(Base):
    """Persisted budget hold for accepted print work that has not been charged yet."""

    __tablename__ = "budget_reservations"

    id: Mapped[int] = mapped_column(primary_key=True)
    cost_center_id: Mapped[int] = mapped_column(ForeignKey("cost_centers.id", ondelete="CASCADE"), index=True)
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    source_type: Mapped[str] = mapped_column(String(50), index=True)
    source_id: Mapped[int | None] = mapped_column(index=True)
    print_archive_id: Mapped[int | None] = mapped_column(
        ForeignKey("print_archives.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    cost_center: Mapped[CostCenter] = relationship()
    print_archive: Mapped[PrintArchive | None] = relationship()


class WalletTransaction(Base):
    """Immutable wallet ledger entry."""

    __tablename__ = "wallet_transactions"
    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('print_charge', 'deposit', 'withdraw', 'manual_adjustment')",
            name="ck_wallet_transactions_transaction_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    cost_center_id: Mapped[int | None] = mapped_column(
        ForeignKey("cost_centers.id", ondelete="SET NULL"), nullable=True, index=True
    )

    transaction_type: Mapped[str] = mapped_column(String(40), index=True)
    amount: Mapped[float] = mapped_column(Float)
    balance_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    print_run_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    print_archive_id: Mapped[int | None] = mapped_column(
        ForeignKey("print_archives.id", ondelete="SET NULL"), nullable=True, index=True
    )
    print_queue_id: Mapped[int | None] = mapped_column(
        ForeignKey("print_queue.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    cost_center: Mapped[CostCenter | None] = relationship()
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id])
    print_archive: Mapped[PrintArchive | None] = relationship()
    print_queue: Mapped[PrintQueueItem | None] = relationship()

    @validates("transaction_type")
    def _validate_transaction_type(self, key: str, value: str | TransactionType) -> str:
        return normalize_transaction_type(value)
