"""
Bambu Lab Cloud API Service

Handles authentication and profile management with Bambu Lab's cloud services.
"""

import httpx
import json
import logging
from typing import Optional
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

BAMBU_API_BASE = "https://api.bambulab.com"
BAMBU_API_BASE_CN = "https://api.bambulab.cn"


class BambuCloudError(Exception):
    """Base exception for Bambu Cloud errors."""
    pass


class BambuCloudAuthError(BambuCloudError):
    """Authentication related errors."""
    pass


class BambuCloudService:
    """Service for interacting with Bambu Lab Cloud API."""

    def __init__(self, region: str = "global"):
        self.base_url = BAMBU_API_BASE if region == "global" else BAMBU_API_BASE_CN
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self._client = httpx.AsyncClient(timeout=30.0)

    @property
    def is_authenticated(self) -> bool:
        """Check if we have a valid token."""
        if not self.access_token:
            return False
        if self.token_expiry and datetime.now() > self.token_expiry:
            return False
        return True

    def _get_headers(self) -> dict:
        """Get headers for authenticated requests."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Bambuddy/1.0",
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    async def login_request(self, email: str, password: str) -> dict:
        """
        Initiate login - this will trigger a verification code email.

        Returns dict with login status and whether verification is needed.
        """
        try:
            response = await self._client.post(
                f"{self.base_url}/v1/user-service/user/login",
                headers={"Content-Type": "application/json"},
                json={
                    "account": email,
                    "password": password,
                }
            )

            data = response.json()

            if response.status_code == 200:
                # Check if we need verification code
                # Bambu API returns loginType or may require tfaKey
                if data.get("loginType") == "verifyCode" or "tfaKey" in data:
                    return {
                        "success": False,
                        "needs_verification": True,
                        "message": "Verification code sent to email"
                    }

                # Direct login success (rare, usually needs 2FA)
                if "accessToken" in data:
                    self._set_tokens(data)
                    return {
                        "success": True,
                        "needs_verification": False,
                        "message": "Login successful"
                    }

            # Handle specific error codes
            error_msg = data.get("message") or data.get("error") or "Login failed"
            return {
                "success": False,
                "needs_verification": False,
                "message": error_msg
            }

        except Exception as e:
            logger.error(f"Login request failed: {e}")
            raise BambuCloudAuthError(f"Login request failed: {e}")

    async def verify_code(self, email: str, code: str) -> dict:
        """
        Complete login with verification code.
        """
        try:
            response = await self._client.post(
                f"{self.base_url}/v1/user-service/user/login",
                headers={"Content-Type": "application/json"},
                json={
                    "account": email,
                    "code": code,
                }
            )

            data = response.json()

            if response.status_code == 200 and "accessToken" in data:
                self._set_tokens(data)
                return {
                    "success": True,
                    "message": "Login successful"
                }

            return {
                "success": False,
                "message": data.get("message", "Verification failed")
            }

        except Exception as e:
            logger.error(f"Verification failed: {e}")
            raise BambuCloudAuthError(f"Verification failed: {e}")

    def _set_tokens(self, data: dict):
        """Set tokens from login response."""
        self.access_token = data.get("accessToken")
        self.refresh_token = data.get("refreshToken")
        # Token typically valid for ~3 months, but we'll refresh more often
        self.token_expiry = datetime.now() + timedelta(days=30)

    def set_token(self, access_token: str):
        """Set access token directly (for stored tokens)."""
        self.access_token = access_token
        self.token_expiry = datetime.now() + timedelta(days=30)

    def logout(self):
        """Clear authentication state."""
        self.access_token = None
        self.refresh_token = None
        self.token_expiry = None

    async def get_user_profile(self) -> dict:
        """Get user profile information."""
        if not self.is_authenticated:
            raise BambuCloudAuthError("Not authenticated")

        try:
            response = await self._client.get(
                f"{self.base_url}/v1/design-user-service/my/preference",
                headers=self._get_headers()
            )

            if response.status_code == 200:
                return response.json()

            raise BambuCloudError(f"Failed to get profile: {response.status_code}")

        except httpx.RequestError as e:
            raise BambuCloudError(f"Request failed: {e}")

    async def get_slicer_settings(self, version: str = "02.04.00.70") -> dict:
        """
        Get all slicer settings (filament, printer, process presets).

        Args:
            version: Slicer version string
        """
        if not self.is_authenticated:
            raise BambuCloudAuthError("Not authenticated")

        try:
            response = await self._client.get(
                f"{self.base_url}/v1/iot-service/api/slicer/setting",
                headers=self._get_headers(),
                params={"version": version}
            )

            data = response.json()

            if response.status_code == 200:
                return data

            raise BambuCloudError(f"Failed to get settings: {response.status_code}")

        except httpx.RequestError as e:
            raise BambuCloudError(f"Request failed: {e}")

    async def get_setting_detail(self, setting_id: str) -> dict:
        """Get detailed information for a specific setting/preset."""
        if not self.is_authenticated:
            raise BambuCloudAuthError("Not authenticated")

        try:
            response = await self._client.get(
                f"{self.base_url}/v1/iot-service/api/slicer/setting/{setting_id}",
                headers=self._get_headers()
            )

            if response.status_code == 200:
                return response.json()

            raise BambuCloudError(f"Failed to get setting detail: {response.status_code}")

        except httpx.RequestError as e:
            raise BambuCloudError(f"Request failed: {e}")

    async def create_setting(self, preset_type: str, name: str, base_id: str, setting: dict, version: str = "2.0.0.0") -> dict:
        """
        Create a new slicer preset/setting.

        Args:
            preset_type: Type of preset - "filament", "print", or "printer"
            name: Display name for the preset
            base_id: Base preset ID to inherit from (e.g., "GFSA00")
            setting: Dict of setting key-value pairs (only modified values from base)
            version: Version string for the preset (default: "2.0.0.0")

        Returns:
            Created preset data including the new setting_id
        """
        if not self.is_authenticated:
            raise BambuCloudAuthError("Not authenticated")

        try:
            # Add timestamp if not present
            import time
            if "updated_time" not in setting:
                setting["updated_time"] = str(int(time.time()))

            payload = {
                "type": preset_type,
                "name": name,
                "version": version,
                "base_id": base_id,
                "setting": setting,
            }

            response = await self._client.post(
                f"{self.base_url}/v1/iot-service/api/slicer/setting",
                headers=self._get_headers(),
                json=payload
            )

            data = response.json()

            if response.status_code in (200, 201):
                return data

            error_msg = data.get("message") or data.get("error") or f"HTTP {response.status_code}"
            raise BambuCloudError(f"Failed to create setting: {error_msg}")

        except httpx.RequestError as e:
            raise BambuCloudError(f"Request failed: {e}")

    async def update_setting(self, setting_id: str, name: str | None = None, setting: dict | None = None) -> dict:
        """
        Update an existing slicer preset/setting.

        Note: Bambu Cloud API doesn't support true updates. Instead, we:
        1. Fetch the current setting metadata (type, base_id, version)
        2. Use the provided settings as the new complete settings (NOT merged)
        3. Delete the old setting first (to avoid name conflicts)
        4. Create a new setting via POST

        Args:
            setting_id: ID of the preset to update
            name: New display name (optional)
            setting: Dict of setting key-value pairs - this REPLACES the old settings entirely

        Returns:
            Updated preset data with new setting_id
        """
        if not self.is_authenticated:
            raise BambuCloudAuthError("Not authenticated")

        try:
            # Fetch current setting to get metadata (type, base_id, version)
            current = await self.get_setting_detail(setting_id)
            preset_type = current.get("type", "filament")

            # Use provided settings directly (complete replacement, not merge)
            # This allows the frontend to edit the full settings JSON
            if setting is not None:
                updated_setting = setting.copy()
            else:
                updated_setting = current.get("setting", {}).copy()

            # Extract name from settings_id field in the JSON, or use provided name, or fall back to current
            # The settings_id field contains the name in quotes, e.g., '"My Preset Name"'
            settings_id_key = {
                "filament": "filament_settings_id",
                "print": "print_settings_id",
                "printer": "printer_settings_id",
            }.get(preset_type, "filament_settings_id")

            settings_id_value = updated_setting.get(settings_id_key, "")
            if settings_id_value:
                # Remove surrounding quotes if present (e.g., '"foo"' -> 'foo')
                updated_name = settings_id_value.strip('"')
            elif name is not None:
                updated_name = name
            else:
                updated_name = current.get("name", "Untitled")

            # Update the timestamp
            import time
            updated_setting["updated_time"] = str(int(time.time()))

            # Ensure settings_id field matches the name
            updated_setting[settings_id_key] = f'"{updated_name}"'

            # Delete the old setting FIRST to avoid name conflicts
            await self.delete_setting(setting_id)

            # Create new setting via POST
            payload = {
                "type": preset_type,
                "name": updated_name,
                "version": current.get("version", "2.0.0.0"),
                "base_id": current.get("base_id", ""),
                "setting": updated_setting,
            }

            response = await self._client.post(
                f"{self.base_url}/v1/iot-service/api/slicer/setting",
                headers=self._get_headers(),
                json=payload
            )

            data = response.json()

            if response.status_code == 200:
                return data

            error_msg = data.get("message") or data.get("error") or f"HTTP {response.status_code}"
            raise BambuCloudError(f"Failed to update setting: {error_msg}")

        except httpx.RequestError as e:
            raise BambuCloudError(f"Request failed: {e}")

    async def delete_setting(self, setting_id: str) -> dict:
        """
        Delete a slicer preset/setting.

        Args:
            setting_id: ID of the preset to delete

        Returns:
            Deletion confirmation
        """
        if not self.is_authenticated:
            raise BambuCloudAuthError("Not authenticated")

        try:
            response = await self._client.delete(
                f"{self.base_url}/v1/iot-service/api/slicer/setting/{setting_id}",
                headers=self._get_headers()
            )

            if response.status_code in (200, 204):
                return {"success": True, "message": "Setting deleted"}

            data = response.json() if response.content else {}
            error_msg = data.get("message") or data.get("error") or f"HTTP {response.status_code}"
            raise BambuCloudError(f"Failed to delete setting: {error_msg}")

        except httpx.RequestError as e:
            raise BambuCloudError(f"Request failed: {e}")

    async def get_devices(self) -> dict:
        """Get list of bound devices."""
        if not self.is_authenticated:
            raise BambuCloudAuthError("Not authenticated")

        try:
            response = await self._client.get(
                f"{self.base_url}/v1/iot-service/api/user/bind",
                headers=self._get_headers()
            )

            if response.status_code == 200:
                return response.json()

            raise BambuCloudError(f"Failed to get devices: {response.status_code}")

        except httpx.RequestError as e:
            raise BambuCloudError(f"Request failed: {e}")

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()


# Singleton instance
_cloud_service: Optional[BambuCloudService] = None


def get_cloud_service() -> BambuCloudService:
    """Get the singleton cloud service instance."""
    global _cloud_service
    if _cloud_service is None:
        _cloud_service = BambuCloudService()
    return _cloud_service
