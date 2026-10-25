from datetime import datetime

from pydantic import BaseModel


class SpoolUsageHistoryResponse(BaseModel):
    id: int
    spool_id: int | None = None  # None: the spool was deleted, its usage kept
    printer_id: int | None = None
    print_name: str | None = None
    weight_used: float
    percent_used: int
    status: str
    cost: float | None = None
    created_at: datetime

    class Config:
        from_attributes = True
