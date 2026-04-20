"""Pydantic schemas for the macro system."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MacroCreate(BaseModel):
    name: str
    description: str | None = None
    script: str
    trigger_type: str = "manual"  # manual|webhook|schedule
    cron_expression: str | None = None
    printer_id: int | None = None


class MacroUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    script: str | None = None
    trigger_type: str | None = None
    cron_expression: str | None = None
    printer_id: int | None = None


class MacroResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    script: str  # content read from disk at response time
    file_path: str
    trigger_type: str
    cron_expression: str | None
    printer_id: int | None
    created_at: datetime
    updated_at: datetime


class MacroRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    macro_id: int
    printer_id: int | None
    status: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    log: str


class RunMacroRequest(BaseModel):
    printer_id: int | None = None
