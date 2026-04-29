"""Pydantic schemas for the macro system."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

# ── Cfg file schemas ───────────────────────────────────────────────────────────


class MacroCfgFileCreate(BaseModel):
    name: str
    content: str = ""


class MacroCfgFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    file_path: str
    parse_error: str | None
    created_at: datetime
    updated_at: datetime


class MacroCfgFileSave(BaseModel):
    content: str


# ── Macro schemas ──────────────────────────────────────────────────────────────


class MacroResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    cfg_file_id: int | None
    trigger_type: str
    cron_expression: str | None
    printer_id: int | None
    created_at: datetime
    updated_at: datetime


# ── Run schemas ────────────────────────────────────────────────────────────────


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


# ── Function catalogue schemas ────────────────────────────────────────────────


class ArgSpecResponse(BaseModel):
    description: str
    required: bool
    default: str | None


class FunctionSpecResponse(BaseModel):
    name: str
    description: str
    args: dict[str, ArgSpecResponse]
    context_var: str | None
    requires_printer: bool
    allowed_in_embed: bool


# ── Terminal / exec schemas ────────────────────────────────────────────────────


class HMSErrorInfo(BaseModel):
    code: str
    severity: int
    message: str = ""


class ExecLineRequest(BaseModel):
    line: str
    printer_id: int | None = None


class ExecLineResponse(BaseModel):
    status: str
    log: str
    hms_errors: list[HMSErrorInfo] = []
    printer_state: str = ""
    run_id: int | None = None
