from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class SmartPlugBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    ip_address: str = Field(..., pattern=r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    printer_id: int | None = None
    enabled: bool = True
    auto_on: bool = True
    auto_off: bool = True
    off_delay_mode: Literal["time", "temperature"] = "time"
    off_delay_minutes: int = Field(default=5, ge=0, le=60)
    off_temp_threshold: int = Field(default=70, ge=30, le=150)
    username: str | None = None
    password: str | None = None


class SmartPlugCreate(SmartPlugBase):
    pass


class SmartPlugUpdate(BaseModel):
    name: str | None = None
    ip_address: str | None = None
    printer_id: int | None = None
    enabled: bool | None = None
    auto_on: bool | None = None
    auto_off: bool | None = None
    off_delay_mode: Literal["time", "temperature"] | None = None
    off_delay_minutes: int | None = Field(default=None, ge=0, le=60)
    off_temp_threshold: int | None = Field(default=None, ge=30, le=150)
    username: str | None = None
    password: str | None = None


class SmartPlugResponse(SmartPlugBase):
    id: int
    last_state: str | None = None
    last_checked: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SmartPlugControl(BaseModel):
    action: Literal["on", "off", "toggle"]


class SmartPlugStatus(BaseModel):
    state: str | None = None  # "ON", "OFF", or None if unreachable
    reachable: bool = True
    device_name: str | None = None


class SmartPlugTestConnection(BaseModel):
    ip_address: str = Field(..., pattern=r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    username: str | None = None
    password: str | None = None
