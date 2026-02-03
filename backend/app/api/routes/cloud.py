"""
Bambu Lab Cloud API Routes

Handles authentication and profile management with Bambu Cloud.
"""

import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.schemas.cloud import (
    CloudAuthStatus,
    CloudDevice,
    CloudLoginRequest,
    CloudLoginResponse,
    CloudTokenRequest,
    CloudVerifyRequest,
    FirmwareUpdateInfo,
    FirmwareUpdatesResponse,
    SlicerSetting,
    SlicerSettingCreate,
    SlicerSettingDeleteResponse,
    SlicerSettingsResponse,
    SlicerSettingUpdate,
)
from backend.app.services.bambu_cloud import (
    BambuCloudAuthError,
    BambuCloudError,
    get_cloud_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cloud", tags=["cloud"])


# Keys for storing cloud credentials in settings
CLOUD_TOKEN_KEY = "bambu_cloud_token"
CLOUD_EMAIL_KEY = "bambu_cloud_email"


async def get_stored_token(db: AsyncSession) -> tuple[str | None, str | None]:
    """Get stored cloud token and email from database."""
    result = await db.execute(select(Settings).where(Settings.key.in_([CLOUD_TOKEN_KEY, CLOUD_EMAIL_KEY])))
    settings = {s.key: s.value for s in result.scalars().all()}
    return settings.get(CLOUD_TOKEN_KEY), settings.get(CLOUD_EMAIL_KEY)


async def store_token(db: AsyncSession, token: str, email: str) -> None:
    """Store cloud token and email in database."""
    for key, value in [(CLOUD_TOKEN_KEY, token), (CLOUD_EMAIL_KEY, email)]:
        result = await db.execute(select(Settings).where(Settings.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = value
        else:
            db.add(Settings(key=key, value=value))
    await db.commit()


async def clear_token(db: AsyncSession) -> None:
    """Clear stored cloud token and email."""
    result = await db.execute(select(Settings).where(Settings.key.in_([CLOUD_TOKEN_KEY, CLOUD_EMAIL_KEY])))
    for setting in result.scalars().all():
        await db.delete(setting)
    await db.commit()


@router.get("/status", response_model=CloudAuthStatus)
async def get_auth_status(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CLOUD_AUTH),
):
    """Get current cloud authentication status."""
    token, email = await get_stored_token(db)
    cloud = get_cloud_service()

    if token:
        cloud.set_token(token)

    return CloudAuthStatus(
        is_authenticated=cloud.is_authenticated,
        email=email if cloud.is_authenticated else None,
    )


@router.post("/login", response_model=CloudLoginResponse)
async def login(
    request: CloudLoginRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CLOUD_AUTH),
):
    """
    Initiate login to Bambu Cloud.

    This will trigger either:
    - Email verification: A code is sent to the user's email
    - TOTP verification: User enters code from their authenticator app

    After receiving/generating the code, call /cloud/verify to complete the login.
    For TOTP, include the tfa_key from this response in the verify request.
    """
    cloud = get_cloud_service()

    # Store email temporarily for verification step
    await store_token(db, "", request.email)

    try:
        result = await cloud.login_request(request.email, request.password)

        if result.get("success") and cloud.access_token:
            # Direct login succeeded (rare)
            await store_token(db, cloud.access_token, request.email)

        return CloudLoginResponse(
            success=result.get("success", False),
            needs_verification=result.get("needs_verification", False),
            message=result.get("message", "Unknown error"),
            verification_type=result.get("verification_type"),
            tfa_key=result.get("tfa_key"),
        )
    except BambuCloudAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except BambuCloudError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify", response_model=CloudLoginResponse)
async def verify_code(
    request: CloudVerifyRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CLOUD_AUTH),
):
    """
    Complete login with verification code (email or TOTP).

    For email verification:
    - After calling /cloud/login, the user receives an email with a 6-digit code
    - Submit the code with email address

    For TOTP verification:
    - The user enters the 6-digit code from their authenticator app
    - Include the tfa_key from the /cloud/login response
    """
    cloud = get_cloud_service()

    try:
        # Use TOTP verification if tfa_key is provided
        if request.tfa_key:
            result = await cloud.verify_totp(request.tfa_key, request.code)
        else:
            result = await cloud.verify_code(request.email, request.code)

        if result.get("success") and cloud.access_token:
            await store_token(db, cloud.access_token, request.email)

        return CloudLoginResponse(
            success=result.get("success", False),
            needs_verification=False,
            message=result.get("message", "Unknown error"),
        )
    except BambuCloudAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except BambuCloudError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/token", response_model=CloudAuthStatus)
async def set_token(
    request: CloudTokenRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CLOUD_AUTH),
):
    """
    Set access token directly.

    For users who already have a token (e.g., from Bambu Studio).
    """
    cloud = get_cloud_service()
    cloud.set_token(request.access_token)

    # Verify token works by trying to get profile
    try:
        await cloud.get_user_profile()
        await store_token(db, request.access_token, "token-auth")
        return CloudAuthStatus(is_authenticated=True, email="token-auth")
    except BambuCloudError:
        cloud.logout()
        raise HTTPException(status_code=401, detail="Invalid token")


@router.post("/logout")
async def logout(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.CLOUD_AUTH),
):
    """Log out of Bambu Cloud."""
    cloud = get_cloud_service()
    cloud.logout()
    await clear_token(db)
    return {"success": True}


@router.get("/settings", response_model=SlicerSettingsResponse)
async def get_slicer_settings(
    version: str = "02.04.00.70",
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """
    Get all slicer settings (filament, printer, process presets).

    Requires authentication.
    """
    token, _ = await get_stored_token(db)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    cloud = get_cloud_service()
    cloud.set_token(token)

    if not cloud.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        data = await cloud.get_slicer_settings(version)

        result = SlicerSettingsResponse()

        # Map API keys to our types (API uses 'print' for process presets)
        type_mapping = {
            "filament": "filament",
            "printer": "printer",
            "print": "process",  # API calls it 'print', we call it 'process'
        }

        for api_key, our_type in type_mapping.items():
            type_data = data.get(api_key, {})
            # Combine public and private presets, private (user's own) first
            all_settings = type_data.get("private", []) + type_data.get("public", [])

            parsed = []
            for s in all_settings:
                parsed.append(
                    SlicerSetting(
                        setting_id=s.get("setting_id", s.get("id", "")),
                        name=s.get("name", "Unknown"),
                        type=our_type,
                        version=s.get("version"),
                        user_id=s.get("user_id"),
                        updated_time=s.get("updated_time"),
                    )
                )
            setattr(result, our_type, parsed)

        return result
    except BambuCloudAuthError:
        await clear_token(db)
        raise HTTPException(status_code=401, detail="Authentication expired")
    except BambuCloudError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings/{setting_id}")
async def get_setting_detail(
    setting_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """
    Get detailed information for a specific setting/preset.

    Returns the full preset configuration.
    """
    token, _ = await get_stored_token(db)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    cloud = get_cloud_service()
    cloud.set_token(token)

    if not cloud.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        data = await cloud.get_setting_detail(setting_id)
        return data
    except BambuCloudAuthError:
        await clear_token(db)
        raise HTTPException(status_code=401, detail="Authentication expired")
    except BambuCloudError as e:
        raise HTTPException(status_code=500, detail=str(e))


# Cache for filament preset info (setting_id -> {name, k})
_filament_cache: dict[str, dict] = {}
_filament_cache_time: float = 0
FILAMENT_CACHE_TTL = 300  # 5 minutes


def _filament_id_to_setting_id(filament_id: str) -> str:
    """
    Convert filament_id to setting_id format for Bambu Cloud API.

    Printers report filament_id (e.g., GFA00, GFG02) but the API expects
    setting_id format which has an "S" inserted after "GF" (e.g., GFSA00, GFSG02).

    User presets (starting with "P") and already-correct IDs are returned unchanged.
    """
    if not filament_id:
        return filament_id

    # User presets start with "P" - leave unchanged
    if filament_id.startswith("P"):
        return filament_id

    # Official Bambu presets: GFx## -> GFSx##
    # Check if it matches the filament_id pattern (GF followed by letter and digits)
    if filament_id.startswith("GF") and len(filament_id) >= 4:
        # Check if it's already a setting_id (has S after GF)
        if filament_id[2] == "S":
            return filament_id
        # Insert "S" after "GF": GFA00 -> GFSA00
        return f"GFS{filament_id[2:]}"

    return filament_id


@router.post("/filament-info")
async def get_filament_info(
    setting_ids: list[str] = Body(...),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FILAMENTS_READ),
):
    """
    Get filament preset info (name and K value) for multiple setting IDs.

    Used to enrich AMS tray tooltips with cloud preset data.
    """
    import time

    logger.info(f"get_filament_info called with {len(setting_ids)} IDs: {setting_ids}")

    global _filament_cache, _filament_cache_time

    # Clear stale cache
    if time.time() - _filament_cache_time > FILAMENT_CACHE_TTL:
        _filament_cache = {}
        _filament_cache_time = time.time()

    token, _ = await get_stored_token(db)
    if not token:
        logger.info("get_filament_info: Not authenticated, returning empty")
        # Return empty results if not authenticated (graceful degradation)
        return {}

    cloud = get_cloud_service()
    cloud.set_token(token)

    if not cloud.is_authenticated:
        return {}

    result = {}
    for setting_id in setting_ids:
        if not setting_id:
            continue

        # Check cache first
        if setting_id in _filament_cache:
            result[setting_id] = _filament_cache[setting_id]
            continue

        try:
            # Transform filament_id to setting_id format (GFA00 -> GFSA00)
            api_setting_id = _filament_id_to_setting_id(setting_id)

            data = await cloud.get_setting_detail(api_setting_id)
            setting = data.get("setting", {})

            # Extract name (e.g., "Bambu PLA Basic Jade White")
            name = data.get("name", "")

            # Extract K value (pressure_advance)
            k_value = setting.get("pressure_advance")
            if k_value is not None:
                try:
                    k_value = float(k_value)
                except (ValueError, TypeError):
                    k_value = None

            info = {"name": name, "k": k_value}
            # Cache using original ID so frontend gets expected response
            _filament_cache[setting_id] = info
            result[setting_id] = info

        except Exception as e:
            logger.warning(
                f"Failed to get cloud preset {setting_id} (API ID: {_filament_id_to_setting_id(setting_id)}): {e}"
            )
            # Cache the failure to avoid repeated requests
            _filament_cache[setting_id] = {"name": "", "k": None}
            result[setting_id] = {"name": "", "k": None}

    return result


@router.get("/devices", response_model=list[CloudDevice])
async def get_devices(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
):
    """
    Get list of bound printer devices.

    Returns printers registered to the user's Bambu account.
    """
    token, _ = await get_stored_token(db)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    cloud = get_cloud_service()
    cloud.set_token(token)

    if not cloud.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        data = await cloud.get_devices()
        devices = data.get("devices", [])

        return [
            CloudDevice(
                dev_id=d.get("dev_id", ""),
                name=d.get("name", "Unknown"),
                dev_model_name=d.get("dev_model_name"),
                dev_product_name=d.get("dev_product_name"),
                online=d.get("online", False),
            )
            for d in devices
        ]
    except BambuCloudAuthError:
        await clear_token(db)
        raise HTTPException(status_code=401, detail="Authentication expired")
    except BambuCloudError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/firmware-updates", response_model=FirmwareUpdatesResponse)
async def get_firmware_updates(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.FIRMWARE_READ),
):
    """
    Check for firmware updates for all bound devices.

    Returns firmware version info for each device including:
    - Current installed version
    - Latest available version
    - Whether an update is available
    - Release notes for the latest version

    Requires cloud authentication.
    """
    token, _ = await get_stored_token(db)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    cloud = get_cloud_service()
    cloud.set_token(token)

    if not cloud.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        # First get list of bound devices
        devices_data = await cloud.get_devices()
        devices = devices_data.get("devices", [])

        updates = []
        updates_available = 0

        # Check firmware for each device
        for device in devices:
            device_id = device.get("dev_id", "")
            device_name = device.get("name", "Unknown")

            try:
                firmware_info = await cloud.get_firmware_version(device_id)
                update_available = firmware_info.get("update_available", False)

                if update_available:
                    updates_available += 1

                updates.append(
                    FirmwareUpdateInfo(
                        device_id=device_id,
                        device_name=device_name,
                        current_version=firmware_info.get("current_version"),
                        latest_version=firmware_info.get("latest_version"),
                        update_available=update_available,
                        release_notes=firmware_info.get("release_notes"),
                    )
                )
            except BambuCloudError as e:
                logger.warning(f"Failed to get firmware info for {device_name}: {e}")
                # Still include device but with unknown firmware status
                updates.append(
                    FirmwareUpdateInfo(
                        device_id=device_id,
                        device_name=device_name,
                        current_version=None,
                        latest_version=None,
                        update_available=False,
                        release_notes=None,
                    )
                )

        return FirmwareUpdatesResponse(updates=updates, updates_available=updates_available)

    except BambuCloudAuthError:
        await clear_token(db)
        raise HTTPException(status_code=401, detail="Authentication expired")
    except BambuCloudError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings")
async def create_setting(
    request: SlicerSettingCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """
    Create a new slicer preset/setting.

    Creates a new preset on Bambu Cloud. The preset inherits from a base preset
    and only stores the delta (modified values).

    Type should be: 'filament', 'print', or 'printer'
    """
    token, _ = await get_stored_token(db)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    cloud = get_cloud_service()
    cloud.set_token(token)

    if not cloud.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        data = await cloud.create_setting(
            preset_type=request.type,
            name=request.name,
            base_id=request.base_id,
            setting=request.setting,
            version=request.version,
        )
        return data
    except BambuCloudAuthError:
        await clear_token(db)
        raise HTTPException(status_code=401, detail="Authentication expired")
    except BambuCloudError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/settings/{setting_id}")
async def update_setting(
    setting_id: str,
    request: SlicerSettingUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """
    Update an existing slicer preset/setting.

    Updates the preset's name and/or settings on Bambu Cloud.
    """
    token, _ = await get_stored_token(db)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    cloud = get_cloud_service()
    cloud.set_token(token)

    if not cloud.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        data = await cloud.update_setting(
            setting_id=setting_id,
            name=request.name,
            setting=request.setting,
        )
        return data
    except BambuCloudAuthError:
        await clear_token(db)
        raise HTTPException(status_code=401, detail="Authentication expired")
    except BambuCloudError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/settings/{setting_id}", response_model=SlicerSettingDeleteResponse)
async def delete_setting(
    setting_id: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """
    Delete a slicer preset/setting.

    Removes the preset from Bambu Cloud. This cannot be undone.
    """
    token, _ = await get_stored_token(db)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    cloud = get_cloud_service()
    cloud.set_token(token)

    if not cloud.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        result = await cloud.delete_setting(setting_id)
        return SlicerSettingDeleteResponse(
            success=result.get("success", True),
            message=result.get("message", "Setting deleted"),
        )
    except BambuCloudAuthError:
        await clear_token(db)
        raise HTTPException(status_code=401, detail="Authentication expired")
    except BambuCloudError as e:
        raise HTTPException(status_code=500, detail=str(e))


# Path to field definition files
FIELDS_DATA_DIR = Path(__file__).parent.parent.parent / "data"

# Cache for field definitions (loaded once)
_fields_cache: dict[str, dict] = {}


def _load_fields(preset_type: str) -> dict:
    """Load field definitions from JSON file."""
    if preset_type in _fields_cache:
        return _fields_cache[preset_type]

    # Map API type names to file names
    file_map = {
        "filament": "filament_fields.json",
        "print": "process_fields.json",
        "process": "process_fields.json",
        "printer": "printer_fields.json",
    }

    filename = file_map.get(preset_type)
    if not filename:
        raise HTTPException(status_code=400, detail=f"Unknown preset type: {preset_type}")

    file_path = FIELDS_DATA_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Field definitions not found for: {preset_type}")

    with open(file_path) as f:
        data = json.load(f)

    _fields_cache[preset_type] = data
    return data


@router.get("/fields/{preset_type}")
async def get_preset_fields(
    preset_type: Literal["filament", "print", "process", "printer"],
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """
    Get field definitions for a preset type.

    Returns a list of field definitions including:
    - key: The setting key name
    - label: Human-readable label
    - type: Field type (text, number, boolean, select)
    - category: Grouping category
    - description: Field description
    - options: For select fields, available options
    - unit: Unit of measurement (if applicable)
    - min/max/step: For number fields, validation constraints
    """
    data = _load_fields(preset_type)
    return data


@router.get("/fields")
async def get_all_preset_fields(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_READ),
):
    """
    Get all field definitions for all preset types.

    Returns field definitions organized by type.
    """
    return {
        "filament": _load_fields("filament"),
        "process": _load_fields("process"),
        "printer": _load_fields("printer"),
    }
