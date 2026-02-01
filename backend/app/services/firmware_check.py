"""
Firmware Check Service

Checks for firmware updates by fetching from Bambu Lab's official firmware download page.
Also provides firmware download functionality for offline updates.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from backend.app.core.config import _data_dir

logger = logging.getLogger(__name__)

# Bambu Lab firmware download page
BAMBU_FIRMWARE_BASE = "https://bambulab.com"
FIRMWARE_PAGE = "/en/support/firmware-download/all"

# Cache TTL in seconds (1 hour)
CACHE_TTL = 3600

# Map Bambuddy model names to Bambu Lab API keys
MODEL_TO_API_KEY = {
    "X1": "x1",
    "X1C": "x1",
    "X1-Carbon": "x1",
    "X1 Carbon": "x1",
    "P1P": "p1",
    "P1S": "p1",
    "A1": "a1",
    "A1 Mini": "a1-mini",
    "A1-Mini": "a1-mini",
    "A1mini": "a1-mini",
    "H2D": "h2d",
    "H2C": "h2d",  # H2C uses same firmware as H2D
    "H2S": "h2s",
    "P2S": "p2s",
    "X1E": "x1e",
    "H2D Pro": "h2d-pro",
    "H2D-Pro": "h2d-pro",
    "H2DPRO": "h2d-pro",
}

# Reverse mapping: API key to model codes
API_KEY_TO_DEV_MODEL = {
    "x1": "BL-P001",
    "p1": "C11",
    "a1": "N2S",
    "a1-mini": "N1",
    "h2d": "O1D",
    "h2s": "O1S",
    "p2s": "N7",
    "x1e": "C13",
    "h2d-pro": "O1E",
}


@dataclass
class FirmwareVersion:
    """Firmware version information."""

    version: str
    download_url: str
    release_notes: str | None = None
    release_time: str | None = None


class FirmwareCheckService:
    """Service for checking firmware updates from Bambu Lab."""

    def __init__(self):
        self._build_id: str | None = None
        self._build_id_time: float = 0
        self._version_cache: dict[str, FirmwareVersion] = {}
        self._cache_time: float = 0
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
        )

    async def _get_build_id(self) -> str | None:
        """Fetch the Next.js build ID from Bambu Lab's firmware page."""
        # Use cached build ID if still valid (cache for 1 hour)
        if self._build_id and (time.time() - self._build_id_time) < CACHE_TTL:
            return self._build_id

        try:
            response = await self._client.get(f"{BAMBU_FIRMWARE_BASE}{FIRMWARE_PAGE}")
            if response.status_code == 200:
                # Extract buildId from the page
                match = re.search(r'"buildId":"([^"]+)"', response.text)
                if match:
                    self._build_id = match.group(1)
                    self._build_id_time = time.time()
                    logger.info(f"Got Bambu Lab build ID: {self._build_id}")
                    return self._build_id
            logger.warning(f"Failed to get Bambu Lab page: {response.status_code}")
        except Exception as e:
            logger.error(f"Error fetching Bambu Lab build ID: {e}")

        return self._build_id  # Return cached value if available

    async def _fetch_firmware_versions(self, api_key: str) -> FirmwareVersion | None:
        """Fetch firmware versions for a specific printer from Bambu Lab API."""
        build_id = await self._get_build_id()
        if not build_id:
            logger.warning("No build ID available, cannot fetch firmware versions")
            return None

        try:
            url = f"{BAMBU_FIRMWARE_BASE}/_next/data/{build_id}/en/support/firmware-download/{api_key}.json"
            response = await self._client.get(url)

            if response.status_code == 200:
                data = response.json()
                page_props = data.get("pageProps", {})
                printer_map = page_props.get("printerMap", {})
                printer_data = printer_map.get(api_key, {})
                versions = printer_data.get("versions", [])

                if versions:
                    latest = versions[0]
                    return FirmwareVersion(
                        version=latest.get("version", ""),
                        download_url=latest.get("url", ""),
                        release_notes=latest.get("release_notes_en"),
                        release_time=latest.get("release_time"),
                    )
            else:
                logger.warning(f"Failed to fetch firmware for {api_key}: {response.status_code}")

        except Exception as e:
            logger.error(f"Error fetching firmware for {api_key}: {e}")

        return None

    async def get_latest_version(self, model: str) -> FirmwareVersion | None:
        """
        Get the latest firmware version for a printer model.

        Args:
            model: Bambuddy printer model name (e.g., "X1C", "P1S", "H2D")

        Returns:
            FirmwareVersion if found, None otherwise
        """
        # Normalize model name
        model_upper = model.upper().replace(" ", "").replace("-", "")

        # Find the API key for this model
        api_key = None
        for model_name, key in MODEL_TO_API_KEY.items():
            if model_name.upper().replace(" ", "").replace("-", "") == model_upper:
                api_key = key
                break

        if not api_key:
            # Try direct lookup with original model
            api_key = MODEL_TO_API_KEY.get(model)

        if not api_key:
            logger.debug(f"Unknown printer model: {model}")
            return None

        # Check cache
        cache_key = api_key
        if cache_key in self._version_cache and (time.time() - self._cache_time) < CACHE_TTL:
            return self._version_cache[cache_key]

        # Fetch from API
        version = await self._fetch_firmware_versions(api_key)
        if version:
            self._version_cache[cache_key] = version
            self._cache_time = time.time()

        return version

    async def check_for_update(self, model: str, current_version: str) -> dict:
        """
        Check if a firmware update is available for a printer.

        Args:
            model: Printer model name
            current_version: Currently installed firmware version

        Returns:
            Dict with update info:
            - update_available: bool
            - current_version: str
            - latest_version: str or None
            - download_url: str or None
            - release_notes: str or None
        """
        result = {
            "update_available": False,
            "current_version": current_version,
            "latest_version": None,
            "download_url": None,
            "release_notes": None,
        }

        if not current_version:
            return result

        latest = await self.get_latest_version(model)
        if not latest:
            return result

        result["latest_version"] = latest.version
        result["download_url"] = latest.download_url
        result["release_notes"] = latest.release_notes

        # Compare versions (format: XX.XX.XX.XX)
        try:
            current_parts = [int(x) for x in current_version.split(".")]
            latest_parts = [int(x) for x in latest.version.split(".")]

            # Pad to same length
            while len(current_parts) < 4:
                current_parts.append(0)
            while len(latest_parts) < 4:
                latest_parts.append(0)

            result["update_available"] = latest_parts > current_parts
        except (ValueError, AttributeError):
            logger.warning(f"Could not compare versions: {current_version} vs {latest.version}")

        return result

    async def get_all_latest_versions(self) -> dict[str, FirmwareVersion]:
        """
        Fetch latest firmware versions for all known printer models.

        Returns:
            Dict mapping API key to FirmwareVersion
        """
        results = {}

        for api_key in API_KEY_TO_DEV_MODEL:
            version = await self._fetch_firmware_versions(api_key)
            if version:
                results[api_key] = version

        return results

    def _get_firmware_cache_dir(self) -> Path:
        """Get the firmware cache directory, creating it if needed."""
        cache_dir = _data_dir / "firmware"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _get_cached_firmware_path(self, model: str, version: str) -> Path:
        """Get the path where a firmware file would be cached."""
        # Normalize model name for filename
        model_safe = model.upper().replace(" ", "-").replace("/", "-")
        version_safe = version.replace(".", "_")
        filename = f"{model_safe}_{version_safe}.bin"
        return self._get_firmware_cache_dir() / filename

    async def get_firmware_file_info(self, model: str) -> dict | None:
        """
        Get information about the firmware file for a model.

        Returns:
            Dict with download_url, version, filename, and estimated_size (if available)
        """
        latest = await self.get_latest_version(model)
        if not latest or not latest.download_url:
            return None

        # Extract filename from URL
        url_parts = latest.download_url.split("/")
        filename = url_parts[-1] if url_parts else f"firmware_{model}.bin"

        return {
            "download_url": latest.download_url,
            "version": latest.version,
            "filename": filename,
            "release_notes": latest.release_notes,
        }

    async def download_firmware(
        self,
        model: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> Path | None:
        """
        Download firmware file for a printer model.

        Args:
            model: Printer model name (e.g., "X1C", "P1S", "H2D")
            progress_callback: Optional callback(bytes_downloaded, total_bytes, status_message)

        Returns:
            Path to downloaded firmware file, or None on failure
        """
        latest = await self.get_latest_version(model)
        if not latest or not latest.download_url:
            logger.warning(f"No firmware download URL available for model: {model}")
            return None

        # Check if already cached
        cached_path = self._get_cached_firmware_path(model, latest.version)
        if cached_path.exists():
            logger.info(f"Using cached firmware: {cached_path}")
            return cached_path

        # Extract original filename from URL (must preserve for SD card update)
        url_parts = latest.download_url.split("/")
        original_filename = url_parts[-1] if url_parts else f"firmware_{model}.bin"

        # Download to temp file first
        temp_path = self._get_firmware_cache_dir() / f".downloading_{original_filename}"

        try:
            logger.info(f"Downloading firmware from {latest.download_url}")
            if progress_callback:
                progress_callback(0, 0, "Starting download...")

            async with self._client.stream("GET", latest.download_url) as response:
                if response.status_code != 200:
                    logger.error(f"Firmware download failed with status {response.status_code}")
                    return None

                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0

                with open(temp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total_size, "Downloading firmware...")

            # Also save a copy with the original filename for SD card
            original_path = self._get_firmware_cache_dir() / original_filename
            if original_path.exists():
                original_path.unlink()

            # Move temp to both cached path and original filename path
            import shutil

            shutil.copy2(temp_path, cached_path)
            temp_path.rename(original_path)

            logger.info(f"Firmware downloaded successfully: {original_path}")
            if progress_callback:
                progress_callback(downloaded, total_size, "Download complete")

            return original_path

        except Exception as e:
            logger.error(f"Firmware download failed: {e}")
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            return None

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()


# Singleton instance
_firmware_service: FirmwareCheckService | None = None


def get_firmware_service() -> FirmwareCheckService:
    """Get the singleton firmware check service instance."""
    global _firmware_service
    if _firmware_service is None:
        _firmware_service = FirmwareCheckService()
    return _firmware_service
