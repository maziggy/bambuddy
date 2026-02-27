from datetime import datetime

from pydantic import BaseModel


class AutomationBase(BaseModel):
    start_code: str | None = ""
    start_code_detect: str | None = ""
    start_code_after: str | None = ""
    end_code: str | None = ""
    end_code_detect: str | None = ""
    end_code_after: str | None = ""


class AutomationCreate(AutomationBase):
    pass


class AutomationUpdate(BaseModel):
    start_code: str | None = None
    start_code_detect: str | None = None
    start_code_after: str | None = None
    end_code: str | None = None
    end_code_detect: str | None = None
    end_code_after: str | None = None


class AutomationResponse(AutomationBase):
    id: int
    printer_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
