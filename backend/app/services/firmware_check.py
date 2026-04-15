"""
Firmware Check Service

Checks for firmware updates by fetching from Bambu Lab's official wiki and firmware
download page. The wiki is used as the primary version source (always up-to-date),
while the download page provides firmware file URLs for offline updates.
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

# Bambu Lab firmware download page (for download URLs)
BAMBU_FIRMWARE_BASE = "https://bambulab.com"
FIRMWARE_PAGE = "/en/support/firmware-download/all"

# Bambu Lab wiki (primary source for latest version detection)
BAMBU_WIKI_BASE = "https://wiki.bambulab.com"

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
    "H2C": "h2c",
    "H2S": "h2s",
    "P2S": "p2s",
    "X1E": "x1e",
    "X2D": "x2d",
    "H2D Pro": "h2d-pro",
    "H2D-Pro": "h2d-pro",
    "H2DPRO": "h2d-pro",
    # SSDP model codes (DevModel header) — in case raw codes are stored
    "O1D": "h2d",
    "O1E": "h2d-pro",
    "O2D": "h2d-pro",
    "O1C": "h2c",
    "O1C2": "h2c",
    "O1S": "h2s",
    "BL-P001": "x1",
    "BL-P002": "x1",
    "BL-P003": "x1e",
    "C11": "p1",
    "C12": "p1",
    "C13": "p2s",
    "N2S": "a1",
    "N1": "a1-mini",
    "N6": "x2d",
    "N7": "p2s",
}

# Reverse mapping: API key to model codes
API_KEY_TO_DEV_MODEL = {
    "x1": "BL-P001",
    "p1": "C11",
    "a1": "N2S",
    "a1-mini": "N1",
    "h2d": "O1D",
    "h2c": "O1C",
    "h2s": "O1S",
    "p2s": "N7",
    "x1e": "C13",
    "x2d": "N6",
    "h2d-pro": "O1E",
}

# Wiki firmware release history pages (primary version source)
API_KEY_TO_WIKI_PATH = {
    "x1": "/en/x1/manual/X1-X1C-firmware-release-history",
    "x1e": "/en/x1/manual/X1E-firmware-release-history",
    "x2d": "/en/x2d/manual/x2d-firmware-release-history",
    "p1": "/en/p1/manual/p1p-firmware-release-history",
    "a1": "/en/a1/manual/a1-firmware-release-history",
    "a1-mini": "/en/a1-mini/manual/a1-mini-firmware-release-history",
    "h2d": "/en/h2d/manual/h2d-firmware-release-history",
    "h2c": "/en/h2c/manual/h2c-firmware-release-history",
    "h2s": "/en/h2s/manual/h2s-firmware-release-history",
    "p2s": "/en/p2s/manual/p2s-firmware-release-history",
    "h2d-pro": "/en/h2d-pro/manual/firmware-release-history",
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
        self._versions_list_cache: dict[str, list[FirmwareVersion]] = {}
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
                    logger.info("Got Bambu Lab build ID: %s", self._build_id)
                    return self._build_id
            logger.warning("Failed to get Bambu Lab page: %s", response.status_code)
        except Exception as e:
            logger.error("Error fetching Bambu Lab build ID: %s", e)

        return self._build_id  # Return cached value if available

    async def _fetch_version_from_wiki(self, api_key: str) -> str | None:
        """Fetch the latest firmware version from Bambu Lab's wiki release history page."""
        versions = await self._fetch_all_versions_from_wiki(api_key)
        if versions:
            logger.debug("Wiki firmware for %s: %s", api_key, versions[0][0])
            return versions[0][0]
        return None

    async def _fetch_all_versions_from_wiki(self, api_key: str) -> list[tuple[str, str | None]]:
        """
        Fetch all firmware versions from the wiki release history page.

        Only extracts versions that appear in section-heading anchors
        (e.g. `id="h-01030000-20260303"`) — this excludes version-like
        numbers mentioned incidentally in release-note text.

        Returns list of (version, release_date_YYYYMMDD | None) tuples, newest first.
        """
        wiki_path = API_KEY_TO_WIKI_PATH.get(api_key)
        if not wiki_path:
            return []

        try:
            url = f"{BAMBU_WIKI_BASE}{wiki_path}"
            response = await self._client.get(url, follow_redirects=True)
            if response.status_code != 200:
                return []

            # Primary: heading anchor ids like id="h-01030000-20260303"
            anchor_matches = re.findall(r'id="h-(\d{2})(\d{2})(\d{2})(\d{2})-(\d{8})"', response.text)
            seen: set[str] = set()
            versions: list[tuple[str, str | None]] = []
            for a, b, c, d, date in anchor_matches:
                v = f"{a}.{b}.{c}.{d}"
                if v in seen:
                    continue
                seen.add(v)
                versions.append((v, date))

            if versions:
                return versions

            # Fallback: heading text with "XX.XX.XX.XX (YYYYMMDD)"
            text_matches = re.findall(r"(\d{2}\.\d{2}\.\d{2}\.\d{2})\s*\((\d{8})\)", response.text)
            for v, date in text_matches:
                if v in seen:
                    continue
                seen.add(v)
                versions.append((v, date))
            return versions
        except Exception as e:
            logger.debug("Error fetching wiki firmware list for %s: %s", api_key, e)
        return []

    async def _fetch_all_versions_from_download_page(self, api_key: str) -> list[FirmwareVersion]:
        """Fetch all firmware versions from Bambu Lab's download page (newest first)."""
        build_id = await self._get_build_id()
        if not build_id:
            return []

        try:
            url = f"{BAMBU_FIRMWARE_BASE}/_next/data/{build_id}/en/support/firmware-download/{api_key}.json"
            response = await self._client.get(url)

            if response.status_code == 200:
                data = response.json()
                page_props = data.get("pageProps", {})
                printer_map = page_props.get("printerMap", {})
                printer_data = printer_map.get(api_key, {})
                versions = printer_data.get("versions", [])
                return [
                    FirmwareVersion(
                        version=v.get("version", ""),
                        download_url=v.get("url", ""),
                        release_notes=v.get("release_notes_en"),
                        release_time=v.get("release_time"),
                    )
                    for v in versions
                    if v.get("version")
                ]

        except Exception as e:
            logger.debug("Error fetching download page firmware for %s: %s", api_key, e)

        return []

    async def _fetch_from_download_page(self, api_key: str) -> FirmwareVersion | None:
        """Fetch the latest firmware info from Bambu Lab's download page (has download URLs)."""
        versions = await self._fetch_all_versions_from_download_page(api_key)
        return versions[0] if versions else None

    async def _fetch_firmware_versions(self, api_key: str) -> FirmwareVersion | None:
        """Fetch firmware version info, using wiki as primary source and download page as fallback."""
        # Try wiki first (always has the latest version)
        wiki_version = await self._fetch_version_from_wiki(api_key)

        # Try download page (has download URLs, may lag behind wiki)
        download_info = await self._fetch_from_download_page(api_key)

        if wiki_version:
            # Wiki has the latest version — use it, attach download URL if available
            download_url = ""
            release_notes = None
            if download_info and download_info.version == wiki_version:
                download_url = download_info.download_url
                release_notes = download_info.release_notes
            return FirmwareVersion(
                version=wiki_version,
                download_url=download_url,
                release_notes=release_notes,
            )

        if download_info:
            return download_info

        logger.warning("Could not fetch firmware info for %s from wiki or download page", api_key)
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
            logger.debug("Unknown printer model: %s", model)
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

    def _resolve_api_key(self, model: str) -> str | None:
        """Resolve a model name to its Bambu API key."""
        model_upper = model.upper().replace(" ", "").replace("-", "")
        for name, key in MODEL_TO_API_KEY.items():
            if name.upper().replace(" ", "").replace("-", "") == model_upper:
                return key
        return MODEL_TO_API_KEY.get(model)

    @staticmethod
    def _version_tuple(v: str) -> tuple[int, ...]:
        parts = [int(x) for x in v.split(".")]
        while len(parts) < 4:
            parts.append(0)
        return tuple(parts)

    async def get_available_versions(self, model: str) -> list[FirmwareVersion]:
        """
        Get all announced firmware versions for a model, newest first.

        Merges the wiki release history (list of version strings) with the
        download page JSON (which provides download URLs + release notes).
        Versions present only on the wiki have an empty download_url and
        should be treated as "unavailable" for file-based installation.
        """
        api_key = self._resolve_api_key(model)
        if not api_key:
            return []

        if api_key in self._versions_list_cache and (time.time() - self._cache_time) < CACHE_TTL:
            return self._versions_list_cache[api_key]

        wiki_versions = await self._fetch_all_versions_from_wiki(api_key)
        download_versions = await self._fetch_all_versions_from_download_page(api_key)
        by_version: dict[str, FirmwareVersion] = {d.version: d for d in download_versions if d.version}

        merged: list[FirmwareVersion] = []
        seen: set[str] = set()
        for v, wiki_date in wiki_versions:
            if v in seen:
                continue
            seen.add(v)
            if v in by_version:
                merged.append(by_version[v])
            else:
                merged.append(FirmwareVersion(version=v, download_url="", release_time=wiki_date))
        for d in download_versions:
            if d.version and d.version not in seen:
                seen.add(d.version)
                merged.append(d)

        try:
            merged.sort(key=lambda fv: self._version_tuple(fv.version), reverse=True)
        except (ValueError, AttributeError):
            pass

        self._versions_list_cache[api_key] = merged
        self._cache_time = time.time()
        return merged

    async def get_version_info(self, model: str, version: str) -> FirmwareVersion | None:
        """Find a specific version's info (including download URL) for a model."""
        for v in await self.get_available_versions(model):
            if v.version == version:
                return v
        return None

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
            "available_versions": [],
        }

        available = await self.get_available_versions(model)
        result["available_versions"] = [
            {
                "version": v.version,
                "download_url": v.download_url or None,
                "file_available": bool(v.download_url),
                "release_notes": v.release_notes,
                "release_time": v.release_time,
            }
            for v in available
        ]

        if not current_version:
            return result

        latest = available[0] if available else await self.get_latest_version(model)
        if not latest:
            return result

        result["latest_version"] = latest.version
        result["download_url"] = latest.download_url or None
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
            logger.warning("Could not compare versions: %s vs %s", current_version, latest.version)

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

    async def get_firmware_file_info(self, model: str, version: str | None = None) -> dict | None:
        """
        Get information about a firmware file for a model.

        If `version` is provided, returns info for that specific version (must be
        available on the download page). Otherwise returns info for the latest version.
        """
        if version:
            target = await self.get_version_info(model, version)
        else:
            target = await self.get_latest_version(model)
        if not target or not target.download_url:
            return None

        url_parts = target.download_url.split("/")
        filename = url_parts[-1] if url_parts else f"firmware_{model}.bin"

        return {
            "download_url": target.download_url,
            "version": target.version,
            "filename": filename,
            "release_notes": target.release_notes,
        }

    async def download_firmware(
        self,
        model: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
        version: str | None = None,
    ) -> Path | None:
        """
        Download firmware file for a printer model.

        Args:
            model: Printer model name (e.g., "X1C", "P1S", "H2D")
            progress_callback: Optional callback(bytes_downloaded, total_bytes, status_message)

        Returns:
            Path to downloaded firmware file, or None on failure
        """
        if version:
            latest = await self.get_version_info(model, version)
        else:
            latest = await self.get_latest_version(model)
        if not latest or not latest.download_url:
            logger.warning("No firmware download URL available for model %s version %s", model, version)
            return None

        # Extract original filename from URL (must preserve for SD card update)
        url_parts = latest.download_url.split("/")
        original_filename = url_parts[-1] if url_parts else f"firmware_{model}.bin"

        # Check if already cached (using original filename so SD card gets the right name)
        cached_path = self._get_firmware_cache_dir() / original_filename
        if cached_path.exists():
            logger.info("Using cached firmware: %s", cached_path)
            return cached_path

        # Download to temp file first
        temp_path = self._get_firmware_cache_dir() / f".downloading_{original_filename}"

        try:
            logger.info("Downloading firmware from %s", latest.download_url)
            if progress_callback:
                progress_callback(0, 0, "Starting download...")

            async with self._client.stream("GET", latest.download_url) as response:
                if response.status_code != 200:
                    logger.error("Firmware download failed with status %s", response.status_code)
                    return None

                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0

                with open(temp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total_size, "Downloading firmware...")

            # Move temp to final path, preserving original filename
            temp_path.rename(cached_path)

            logger.info("Firmware downloaded successfully: %s", cached_path)
            if progress_callback:
                progress_callback(downloaded, total_size, "Download complete")

            return cached_path

        except Exception as e:
            logger.error("Firmware download failed: %s", e)
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass  # Best-effort cleanup of failed download temp file
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
