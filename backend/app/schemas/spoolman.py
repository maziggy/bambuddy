from pydantic import BaseModel, Field


class SpoolmanFilamentPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    spool_weight: float | None = Field(None, ge=0.0, le=10_000.0)
    keep_existing_spools: bool = False
