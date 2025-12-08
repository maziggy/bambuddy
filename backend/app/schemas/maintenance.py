"""Maintenance tracking schemas."""

from datetime import datetime
from pydantic import BaseModel, Field


# Maintenance Type schemas
class MaintenanceTypeBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    default_interval_hours: float = Field(default=100.0, ge=1.0)
    # "hours" = print hours, "days" = calendar days
    interval_type: str = Field(default="hours", pattern="^(hours|days)$")
    icon: str | None = None


class MaintenanceTypeCreate(MaintenanceTypeBase):
    pass


class MaintenanceTypeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    default_interval_hours: float | None = Field(default=None, ge=1.0)
    interval_type: str | None = Field(default=None, pattern="^(hours|days)$")
    icon: str | None = None


class MaintenanceTypeResponse(MaintenanceTypeBase):
    id: int
    is_system: bool
    created_at: datetime

    class Config:
        from_attributes = True


# Printer Maintenance schemas
class PrinterMaintenanceBase(BaseModel):
    printer_id: int
    maintenance_type_id: int
    custom_interval_hours: float | None = None
    enabled: bool = True


class PrinterMaintenanceCreate(PrinterMaintenanceBase):
    pass


class PrinterMaintenanceUpdate(BaseModel):
    custom_interval_hours: float | None = None
    custom_interval_type: str | None = Field(default=None, pattern="^(hours|days)$")
    enabled: bool | None = None


class PrinterMaintenanceResponse(BaseModel):
    id: int
    printer_id: int
    maintenance_type_id: int
    maintenance_type: MaintenanceTypeResponse
    custom_interval_hours: float | None
    enabled: bool
    last_performed_at: datetime | None
    last_performed_hours: float
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Maintenance History schemas
class MaintenanceHistoryBase(BaseModel):
    notes: str | None = None


class MaintenanceHistoryCreate(MaintenanceHistoryBase):
    pass


class MaintenanceHistoryResponse(MaintenanceHistoryBase):
    id: int
    printer_maintenance_id: int
    performed_at: datetime
    hours_at_maintenance: float

    class Config:
        from_attributes = True


# Combined status response for frontend
class MaintenanceStatus(BaseModel):
    """Maintenance status for a printer with calculated values."""
    id: int
    printer_id: int
    printer_name: str
    maintenance_type_id: int
    maintenance_type_name: str
    maintenance_type_icon: str | None
    enabled: bool
    # Interval configuration
    interval_hours: float  # custom or default (hours for print-based, days for time-based)
    interval_type: str  # "hours" or "days"
    # For print-hour based maintenance
    current_hours: float  # total print hours for printer
    hours_since_maintenance: float  # current - last_performed
    hours_until_due: float  # interval - hours_since (for hours type)
    # For time-based maintenance
    days_since_maintenance: float | None  # days since last performed
    days_until_due: float | None  # for days type
    # Status flags
    is_due: bool  # hours_until_due <= 0 OR days_until_due <= 0
    is_warning: bool  # within 10% of interval
    last_performed_at: datetime | None


class PrinterMaintenanceOverview(BaseModel):
    """Overview of all maintenance items for a printer."""
    printer_id: int
    printer_name: str
    total_print_hours: float
    maintenance_items: list[MaintenanceStatus]
    due_count: int
    warning_count: int


class PerformMaintenanceRequest(BaseModel):
    """Request to mark maintenance as performed."""
    notes: str | None = None
