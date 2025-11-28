from datetime import datetime
from pydantic import BaseModel, Field


class FilamentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    type: str = Field(..., min_length=1, max_length=50)
    brand: str | None = None
    color: str | None = None
    color_hex: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    cost_per_kg: float = 25.0
    spool_weight_g: float = 1000.0
    currency: str = "USD"
    density: float | None = None
    print_temp_min: int | None = None
    print_temp_max: int | None = None
    bed_temp_min: int | None = None
    bed_temp_max: int | None = None


class FilamentCreate(FilamentBase):
    pass


class FilamentUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    brand: str | None = None
    color: str | None = None
    color_hex: str | None = None
    cost_per_kg: float | None = None
    spool_weight_g: float | None = None
    currency: str | None = None
    density: float | None = None
    print_temp_min: int | None = None
    print_temp_max: int | None = None
    bed_temp_min: int | None = None
    bed_temp_max: int | None = None


class FilamentResponse(FilamentBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FilamentCostCalculation(BaseModel):
    filament_id: int
    filament_name: str
    weight_grams: float
    cost: float
    currency: str
