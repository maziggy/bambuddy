import json
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.database import get_db
from backend.app.models.settings import Settings
from backend.app.schemas.settings import AppSettings, AppSettingsUpdate


router = APIRouter(prefix="/settings", tags=["settings"])

# Default settings
DEFAULT_SETTINGS = AppSettings()


async def get_setting(db: AsyncSession, key: str) -> str | None:
    """Get a single setting value by key."""
    result = await db.execute(select(Settings).where(Settings.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def set_setting(db: AsyncSession, key: str, value: str) -> None:
    """Set a single setting value."""
    result = await db.execute(select(Settings).where(Settings.key == key))
    setting = result.scalar_one_or_none()

    if setting:
        setting.value = value
    else:
        setting = Settings(key=key, value=value)
        db.add(setting)


@router.get("/", response_model=AppSettings)
async def get_settings(db: AsyncSession = Depends(get_db)):
    """Get all application settings."""
    settings_dict = DEFAULT_SETTINGS.model_dump()

    # Load saved settings from database
    result = await db.execute(select(Settings))
    db_settings = result.scalars().all()

    for setting in db_settings:
        if setting.key in settings_dict:
            # Parse the value based on the expected type
            if setting.key in ["auto_archive", "save_thumbnails"]:
                settings_dict[setting.key] = setting.value.lower() == "true"
            elif setting.key == "default_filament_cost":
                settings_dict[setting.key] = float(setting.value)
            else:
                settings_dict[setting.key] = setting.value

    return AppSettings(**settings_dict)


@router.put("/", response_model=AppSettings)
async def update_settings(
    settings_update: AppSettingsUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update application settings."""
    update_data = settings_update.model_dump(exclude_unset=True)

    for key, value in update_data.items():
        # Convert value to string for storage
        if isinstance(value, bool):
            str_value = "true" if value else "false"
        else:
            str_value = str(value)
        await set_setting(db, key, str_value)

    await db.commit()

    # Return updated settings
    return await get_settings(db)


@router.post("/reset", response_model=AppSettings)
async def reset_settings(db: AsyncSession = Depends(get_db)):
    """Reset all settings to defaults."""
    # Delete all settings
    result = await db.execute(select(Settings))
    for setting in result.scalars().all():
        await db.delete(setting)

    await db.commit()

    return DEFAULT_SETTINGS
