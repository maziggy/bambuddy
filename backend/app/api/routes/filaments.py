from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.database import get_db
from backend.app.models.filament import Filament
from backend.app.schemas.filament import (
    FilamentCreate,
    FilamentUpdate,
    FilamentResponse,
    FilamentCostCalculation,
)


router = APIRouter(prefix="/filaments", tags=["filaments"])


@router.get("/", response_model=list[FilamentResponse])
async def list_filaments(db: AsyncSession = Depends(get_db)):
    """List all filaments."""
    result = await db.execute(
        select(Filament).order_by(Filament.type, Filament.name)
    )
    return list(result.scalars().all())


@router.post("/", response_model=FilamentResponse)
async def create_filament(
    filament_data: FilamentCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new filament entry."""
    filament = Filament(**filament_data.model_dump())
    db.add(filament)
    await db.commit()
    await db.refresh(filament)
    return filament


@router.get("/{filament_id}", response_model=FilamentResponse)
async def get_filament(filament_id: int, db: AsyncSession = Depends(get_db)):
    """Get a specific filament."""
    result = await db.execute(
        select(Filament).where(Filament.id == filament_id)
    )
    filament = result.scalar_one_or_none()
    if not filament:
        raise HTTPException(404, "Filament not found")
    return filament


@router.patch("/{filament_id}", response_model=FilamentResponse)
async def update_filament(
    filament_id: int,
    filament_data: FilamentUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a filament."""
    result = await db.execute(
        select(Filament).where(Filament.id == filament_id)
    )
    filament = result.scalar_one_or_none()
    if not filament:
        raise HTTPException(404, "Filament not found")

    for field, value in filament_data.model_dump(exclude_unset=True).items():
        setattr(filament, field, value)

    await db.commit()
    await db.refresh(filament)
    return filament


@router.delete("/{filament_id}")
async def delete_filament(filament_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a filament."""
    result = await db.execute(
        select(Filament).where(Filament.id == filament_id)
    )
    filament = result.scalar_one_or_none()
    if not filament:
        raise HTTPException(404, "Filament not found")

    await db.delete(filament)
    await db.commit()
    return {"status": "deleted"}


@router.post("/calculate-cost", response_model=FilamentCostCalculation)
async def calculate_cost(
    filament_id: int,
    weight_grams: float,
    db: AsyncSession = Depends(get_db),
):
    """Calculate the cost for a given weight of filament."""
    result = await db.execute(
        select(Filament).where(Filament.id == filament_id)
    )
    filament = result.scalar_one_or_none()
    if not filament:
        raise HTTPException(404, "Filament not found")

    cost = (weight_grams / 1000) * filament.cost_per_kg

    return FilamentCostCalculation(
        filament_id=filament.id,
        filament_name=filament.name,
        weight_grams=weight_grams,
        cost=round(cost, 2),
        currency=filament.currency,
    )


@router.get("/by-type/{filament_type}", response_model=list[FilamentResponse])
async def get_filaments_by_type(
    filament_type: str,
    db: AsyncSession = Depends(get_db),
):
    """Get all filaments of a specific type."""
    result = await db.execute(
        select(Filament)
        .where(Filament.type.ilike(f"%{filament_type}%"))
        .order_by(Filament.name)
    )
    return list(result.scalars().all())


@router.post("/seed-defaults")
async def seed_default_filaments(db: AsyncSession = Depends(get_db)):
    """Seed the database with common filament types."""
    defaults = [
        {"name": "Generic PLA", "type": "PLA", "cost_per_kg": 20.0, "print_temp_min": 190, "print_temp_max": 220, "bed_temp_min": 50, "bed_temp_max": 60, "density": 1.24},
        {"name": "Generic PETG", "type": "PETG", "cost_per_kg": 25.0, "print_temp_min": 230, "print_temp_max": 250, "bed_temp_min": 70, "bed_temp_max": 80, "density": 1.27},
        {"name": "Generic ABS", "type": "ABS", "cost_per_kg": 22.0, "print_temp_min": 230, "print_temp_max": 260, "bed_temp_min": 90, "bed_temp_max": 110, "density": 1.04},
        {"name": "Generic TPU", "type": "TPU", "cost_per_kg": 35.0, "print_temp_min": 220, "print_temp_max": 250, "bed_temp_min": 40, "bed_temp_max": 60, "density": 1.21},
        {"name": "Generic ASA", "type": "ASA", "cost_per_kg": 28.0, "print_temp_min": 240, "print_temp_max": 260, "bed_temp_min": 90, "bed_temp_max": 110, "density": 1.07},
        {"name": "Bambu PLA Basic", "type": "PLA", "brand": "Bambu Lab", "cost_per_kg": 20.0, "print_temp_min": 190, "print_temp_max": 220, "bed_temp_min": 35, "bed_temp_max": 55, "density": 1.24},
        {"name": "Bambu PLA Matte", "type": "PLA", "brand": "Bambu Lab", "cost_per_kg": 25.0, "print_temp_min": 190, "print_temp_max": 220, "bed_temp_min": 35, "bed_temp_max": 55, "density": 1.24},
        {"name": "Bambu PETG Basic", "type": "PETG", "brand": "Bambu Lab", "cost_per_kg": 25.0, "print_temp_min": 250, "print_temp_max": 270, "bed_temp_min": 70, "bed_temp_max": 80, "density": 1.27},
        {"name": "Bambu ABS", "type": "ABS", "brand": "Bambu Lab", "cost_per_kg": 30.0, "print_temp_min": 260, "print_temp_max": 280, "bed_temp_min": 90, "bed_temp_max": 100, "density": 1.04},
    ]

    created = 0
    for filament_data in defaults:
        # Check if already exists
        result = await db.execute(
            select(Filament).where(
                Filament.name == filament_data["name"],
                Filament.type == filament_data["type"],
            )
        )
        if result.scalar_one_or_none():
            continue

        filament = Filament(**filament_data)
        db.add(filament)
        created += 1

    await db.commit()
    return {"created": created, "message": f"Created {created} default filaments"}
