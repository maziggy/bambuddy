from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.app.schemas.not_null import IgnoresNullForRequiredColumns


class WalletBalanceResponse(BaseModel):
    user_id: int
    balance: float
    currency: str
    updated_at: datetime | None = None


class WalletTransactionResponse(BaseModel):
    id: int
    user_id: int | None  # None once the user is deleted
    cost_center_id: int | None = None
    transaction_type: Literal["print_charge", "deposit", "withdraw", "manual_adjustment"]
    amount: float
    balance_after: float | None = None
    description: str | None = None
    created_by_user_id: int | None = None
    print_run_id: str | None = None
    print_archive_id: int | None = None
    print_queue_id: int | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class WalletTransactionListResponse(BaseModel):
    items: list[WalletTransactionResponse]
    total: int
    limit: int
    offset: int


class CostCenterSummaryResponse(BaseModel):
    id: int
    name: str
    is_private: bool
    owner_user_id: int | None = None
    is_active: bool
    total_balance: float = 0.0
    total_budget: float | None = None
    monthly_budget: float | None = None
    budget_mode: str = "none"
    budget_limit: float | None = None
    budget_used: float | None = None
    budget_available: float | None = None
    can_print: bool = True

    class Config:
        from_attributes = True


class WalletAdjustmentRequest(BaseModel):
    amount: float = Field(..., gt=0)
    description: str | None = None
    cost_center_id: int | None = None


class WalletAdjustmentResponse(BaseModel):
    transaction: WalletTransactionResponse
    balance: WalletBalanceResponse


class TransactionEditRequest(IgnoresNullForRequiredColumns):
    __orm_table__ = "wallet_transactions"
    user_id: int | None = None
    cost_center_id: int | None = None
    amount: float | None = None
    description: str | None = None


class ManualPrintRequest(BaseModel):
    user_id: int
    cost_center_id: int
    amount: float = Field(..., gt=0)
    description: str | None = None
    created_at: datetime | None = None


class CostCenterCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    total_budget: float | None = Field(default=None, ge=0)
    monthly_budget: float | None = Field(default=None, ge=0)
    is_active: bool = True


class CostCenterUpdateRequest(IgnoresNullForRequiredColumns):
    __orm_table__ = "cost_centers"
    name: str | None = Field(default=None, min_length=1, max_length=150)
    is_active: bool | None = None


class CostCenterBudgetUpdateRequest(BaseModel):
    total_budget: float | None = Field(default=None, ge=0)
    monthly_budget: float | None = Field(default=None, ge=0)


class CostCenterMemberRequest(BaseModel):
    user_id: int
    can_print: bool = True


class CostCenterMemberResponse(BaseModel):
    id: int
    cost_center_id: int
    user_id: int
    can_print: bool
    created_at: datetime

    class Config:
        from_attributes = True


class CostCenterDetailResponse(CostCenterSummaryResponse):
    members: list[CostCenterMemberResponse] = []
