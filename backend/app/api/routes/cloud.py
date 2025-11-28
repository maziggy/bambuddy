"""
Bambu Lab Cloud API Routes

Handles authentication and profile management with Bambu Cloud.
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.database import get_db
from backend.app.models.settings import Settings
from backend.app.services.bambu_cloud import (
    get_cloud_service,
    BambuCloudError,
    BambuCloudAuthError,
)
from backend.app.schemas.cloud import (
    CloudLoginRequest,
    CloudVerifyRequest,
    CloudLoginResponse,
    CloudAuthStatus,
    CloudTokenRequest,
    SlicerSettingsResponse,
    SlicerSetting,
    CloudDevice,
)

router = APIRouter(prefix="/cloud", tags=["cloud"])


# Keys for storing cloud credentials in settings
CLOUD_TOKEN_KEY = "bambu_cloud_token"
CLOUD_EMAIL_KEY = "bambu_cloud_email"


async def get_stored_token(db: AsyncSession) -> tuple[str | None, str | None]:
    """Get stored cloud token and email from database."""
    result = await db.execute(
        select(Settings).where(Settings.key.in_([CLOUD_TOKEN_KEY, CLOUD_EMAIL_KEY]))
    )
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
    result = await db.execute(
        select(Settings).where(Settings.key.in_([CLOUD_TOKEN_KEY, CLOUD_EMAIL_KEY]))
    )
    for setting in result.scalars().all():
        await db.delete(setting)
    await db.commit()


@router.get("/status", response_model=CloudAuthStatus)
async def get_auth_status(db: AsyncSession = Depends(get_db)):
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
async def login(request: CloudLoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Initiate login to Bambu Cloud.

    This will typically trigger a verification code to be sent to the user's email.
    After receiving the code, call /cloud/verify to complete the login.
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
        )
    except BambuCloudAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except BambuCloudError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify", response_model=CloudLoginResponse)
async def verify_code(request: CloudVerifyRequest, db: AsyncSession = Depends(get_db)):
    """
    Complete login with verification code.

    After calling /cloud/login, the user will receive an email with a 6-digit code.
    Submit that code here to complete authentication.
    """
    cloud = get_cloud_service()

    try:
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
async def set_token(request: CloudTokenRequest, db: AsyncSession = Depends(get_db)):
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
async def logout(db: AsyncSession = Depends(get_db)):
    """Log out of Bambu Cloud."""
    cloud = get_cloud_service()
    cloud.logout()
    await clear_token(db)
    return {"success": True}


@router.get("/settings", response_model=SlicerSettingsResponse)
async def get_slicer_settings(
    version: str = "01.09.00.00",
    db: AsyncSession = Depends(get_db),
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
                parsed.append(SlicerSetting(
                    setting_id=s.get("setting_id", s.get("id", "")),
                    name=s.get("name", "Unknown"),
                    type=our_type,
                    version=s.get("version"),
                    user_id=s.get("user_id"),
                    updated_time=s.get("updated_time"),
                ))
            setattr(result, our_type, parsed)

        return result
    except BambuCloudAuthError:
        await clear_token(db)
        raise HTTPException(status_code=401, detail="Authentication expired")
    except BambuCloudError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings/{setting_id}")
async def get_setting_detail(setting_id: str, db: AsyncSession = Depends(get_db)):
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


@router.get("/devices", response_model=list[CloudDevice])
async def get_devices(db: AsyncSession = Depends(get_db)):
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
