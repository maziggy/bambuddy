"""Spoolman integration service for syncing AMS filament data."""

import asyncio
import logging
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

BAMBU_RFID_TAG_LENGTH = 32


@dataclass
class SpoolmanSpool:
    """Represents a spool in Spoolman."""

    id: int
    filament_id: int | None
    remaining_weight: float | None
    used_weight: float
    first_used: str | None
    last_used: str | None
    location: str | None
    lot_nr: str | None
    comment: str | None
    extra: dict | None  # Contains tag_uid in extra.tag


@dataclass
class SpoolmanFilament:
    """Represents a filament type in Spoolman."""

    id: int
    name: str
    vendor_id: int | None
    material: str | None
    color_hex: str | None
    weight: float | None  # Net weight in grams


@dataclass
class AMSTray:
    """Represents an AMS tray with filament data from Bambu printer."""

    ams_id: int  # 0-3 for regular AMS, 128-135 for AMS-HT, 254+ for external spool
    tray_id: int  # 0-3
    tray_type: str  # PLA, PETG, ABS, etc.
    tray_sub_brands: str  # Full name like "PLA Basic", "PETG HF"
    tray_color: str  # Hex color like "FEC600FF"
    remain: int  # Remaining percentage (0-100)
    tag_uid: str  # RFID tag UID
    tray_uuid: str  # Spool UUID
    tray_info_idx: str  # Bambu filament preset ID like "GFA00"
    tray_weight: int  # Spool weight in grams (usually 1000)


class SpoolmanNotFoundError(Exception):
    """Raised when a spool ID does not exist in Spoolman (HTTP 404)."""


class SpoolmanUnavailableError(Exception):
    """Raised when Spoolman is unreachable or returns a server/network error."""


class SpoolmanClientError(Exception):
    """Raised when Spoolman returns a 4xx client error (not 404)."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class SpoolmanClient:
    """Client for interacting with Spoolman API."""

    def __init__(self, base_url: str):
        """Initialize the Spoolman client."""
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api/v1"
        self._client: httpx.AsyncClient | None = None
        self._connected = False
        # Per-spool locks for atomic read-modify-write in merge_spool_extra.
        # WeakValueDictionary: locks are GC'd once no coroutine holds a reference.
        self._extra_locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client with connection pooling limits."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
                follow_redirects=False,
                verify=True,
                limits=httpx.Limits(
                    max_keepalive_connections=5,
                    max_connections=10,
                    keepalive_expiry=30.0,
                ),
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> bool:
        """Check if Spoolman server is reachable; returns True if healthy."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.api_url}/health")
            self._connected = response.status_code == 200
            return self._connected
        except Exception as e:
            logger.warning("Spoolman health check failed: %s", e)
            self._connected = False
            return False

    @property
    def is_connected(self) -> bool:
        """Check if client is connected to Spoolman."""
        return self._connected

    async def get_spools(self) -> list[dict]:
        """Fetch all spools from Spoolman with up to 3 retries on connection errors."""
        max_attempts = 3
        retry_delay = 0.5  # 500ms

        for attempt in range(1, max_attempts + 1):
            try:
                client = await self._get_client()
                response = await client.get(f"{self.api_url}/spool")
                response.raise_for_status()
                spools = response.json()
                if attempt > 1:
                    logger.info("Successfully fetched %d spools on attempt %d", len(spools), attempt)
                return spools
            except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError) as e:
                # Connection-related errors - close and recreate client for next attempt
                if attempt < max_attempts:
                    logger.warning(
                        "Connection error getting spools (attempt %d/%d): %s. Recreating client and retrying in %dms...",
                        attempt,
                        max_attempts,
                        e,
                        int(retry_delay * 1000),
                    )
                    # Close the stale client and recreate it
                    await self.close()
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error("Failed to get spools from Spoolman after %d attempts: %s", max_attempts, e)
                    raise
            except Exception as e:
                # Other errors (HTTP errors, JSON decode errors, etc.)
                if attempt < max_attempts:
                    logger.warning(
                        "Failed to get spools from Spoolman (attempt %d/%d): %s. Retrying in %dms...",
                        attempt,
                        max_attempts,
                        e,
                        int(retry_delay * 1000),
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error("Failed to get spools from Spoolman after %d attempts: %s", max_attempts, e)
                    raise SpoolmanUnavailableError("Cannot reach Spoolman") from e

    async def get_filaments(self) -> list[dict]:
        """Fetch all internal filaments from Spoolman."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.api_url}/filament")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to get filaments from Spoolman: %s", e)
            raise SpoolmanUnavailableError("Cannot reach Spoolman") from e

    async def get_external_filaments(self) -> list[dict]:
        """Fetch external/library filaments from Spoolman."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.api_url}/external/filament")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to get external filaments from Spoolman: %s", e)
            raise SpoolmanUnavailableError("Cannot reach Spoolman") from e

    async def get_vendors(self) -> list[dict]:
        """Fetch all vendors from Spoolman."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.api_url}/vendor")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to get vendors from Spoolman: %s", e)
            raise SpoolmanUnavailableError("Cannot reach Spoolman") from e

    async def create_vendor(self, name: str) -> dict:
        """Create a new vendor in Spoolman."""
        try:
            client = await self._get_client()
            response = await client.post(f"{self.api_url}/vendor", json={"name": name})
            if 400 <= response.status_code < 500:
                raise SpoolmanClientError(
                    f"Spoolman rejected vendor creation (HTTP {response.status_code})",
                    response.status_code,
                )
            response.raise_for_status()
            return response.json()
        except SpoolmanClientError:
            raise
        except Exception as e:
            logger.error("Failed to create vendor in Spoolman: %s", e)
            raise SpoolmanUnavailableError("Cannot reach Spoolman") from e

    def _get_material_density(self, material: str | None) -> float:
        """Return typical density (g/cm³) for the given filament material; defaults to PLA (1.24)."""
        # Typical densities for common filament materials
        densities = {
            "PLA": 1.24,
            "PLA-CF": 1.29,
            "PLA-S": 1.24,
            "PETG": 1.27,
            "ABS": 1.04,
            "ASA": 1.07,
            "TPU": 1.21,
            "PA": 1.14,  # Nylon
            "PA-CF": 1.20,
            "PC": 1.20,
            "PVA": 1.23,
            "HIPS": 1.04,
            "PP": 0.90,
            "PET": 1.38,
        }
        if material:
            # Try exact match first, then uppercase
            mat_upper = material.upper()
            for key, density in densities.items():
                if key.upper() == mat_upper or mat_upper.startswith(key.upper()):
                    return density
        return 1.24  # Default to PLA density

    async def create_filament(
        self,
        name: str,
        vendor_id: int | None = None,
        material: str | None = None,
        color_hex: str | None = None,
        color_name: str | None = None,
        weight: float | None = None,
        diameter: float = 1.75,
        density: float | None = None,
    ) -> dict:
        """Create a new filament in Spoolman."""
        if not name or not name.strip():
            raise ValueError("Filament name is required")

        if density is None:
            density = self._get_material_density(material)

        data: dict = {
            "name": name.strip(),
            "diameter": diameter,
            "density": density,
        }
        if vendor_id:
            data["vendor_id"] = vendor_id
        if material:
            data["material"] = material
        if color_hex:
            # Strip alpha channel if present (RRGGBBAA -> RRGGBB)
            color_hex = color_hex[:6] if len(color_hex) >= 6 else color_hex
            data["color_hex"] = color_hex
        if color_name:
            data["color_name"] = color_name
        if weight:
            data["weight"] = weight

        logger.debug("Creating filament in Spoolman: %s", data)
        try:
            client = await self._get_client()
            response = await client.post(f"{self.api_url}/filament", json=data)
            if 400 <= response.status_code < 500:
                raise SpoolmanClientError(
                    f"Spoolman rejected filament creation (HTTP {response.status_code})",
                    response.status_code,
                )
            response.raise_for_status()
            return response.json()
        except SpoolmanClientError:
            raise
        except Exception as e:
            logger.error("Failed to create filament in Spoolman: %s", e)
            raise SpoolmanUnavailableError("Cannot reach Spoolman") from e

    async def create_spool(
        self,
        filament_id: int,
        remaining_weight: float | None = None,
        location: str | None = None,
        lot_nr: str | None = None,
        comment: str | None = None,
        extra: dict | None = None,
    ) -> dict:
        """Create a new spool in Spoolman."""
        data: dict = {"filament_id": filament_id}
        if remaining_weight is not None:
            data["remaining_weight"] = remaining_weight
        if location:
            data["location"] = location
        if lot_nr:
            data["lot_nr"] = lot_nr
        if comment:
            data["comment"] = comment
        if extra:
            data["extra"] = extra

        logger.debug("Creating spool in Spoolman: %s", data)
        try:
            client = await self._get_client()
            response = await client.post(f"{self.api_url}/spool", json=data)
            if response.status_code == 404:
                raise SpoolmanNotFoundError(f"Filament {filament_id} not found in Spoolman")
            if 400 <= response.status_code < 500:
                raise SpoolmanClientError(
                    f"Spoolman rejected spool creation (HTTP {response.status_code})",
                    response.status_code,
                )
            response.raise_for_status()
            result = response.json()
            logger.info("Created spool %s in Spoolman", result.get("id"))
            return result
        except (SpoolmanNotFoundError, SpoolmanClientError):
            raise
        except Exception as e:
            logger.error("Failed to create spool in Spoolman: %s", e)
            raise SpoolmanUnavailableError("Cannot reach Spoolman") from e

    async def update_spool(
        self,
        spool_id: int,
        remaining_weight: float | None = None,
        location: str | None = None,
        clear_location: bool = False,
        extra: dict | None = None,
    ) -> dict:
        """Update an existing spool in Spoolman, always setting last_used."""
        data: dict = {}
        if remaining_weight is not None:
            data["remaining_weight"] = remaining_weight
        if clear_location:
            data["location"] = None
        elif location:
            data["location"] = location
        if extra:
            data["extra"] = extra
        data["last_used"] = datetime.now(timezone.utc).isoformat()

        response = await self._request_spool("PATCH", spool_id, json_body=data, operation="update")
        return response.json()

    async def _request_spool(
        self,
        method: Literal["GET", "PATCH", "DELETE"],
        spool_id: int,
        *,
        json_body: dict | None = None,
        operation: str,
    ) -> httpx.Response:
        """Perform a spool-scoped HTTP request, translating 404 and errors to named exceptions."""
        try:
            client = await self._get_client()
            response = await client.request(
                method,
                f"{self.api_url}/spool/{spool_id}",
                json=json_body,
            )
            if response.status_code == 404:
                raise SpoolmanNotFoundError(f"Spool {spool_id} not found in Spoolman")
            response.raise_for_status()
            return response
        except SpoolmanNotFoundError:
            raise
        except httpx.HTTPStatusError as e:
            if 400 <= e.response.status_code < 500:
                logger.warning(
                    "Spoolman returned %d for %s spool %s",
                    e.response.status_code,
                    operation,
                    spool_id,
                )
                raise SpoolmanClientError(
                    f"Spoolman rejected {operation} for spool {spool_id} (HTTP {e.response.status_code})",
                    e.response.status_code,
                ) from e
            else:
                logger.error("Failed to %s spool %s in Spoolman: %s", operation, spool_id, e)
                raise SpoolmanUnavailableError(f"Failed to {operation} spool {spool_id}") from e
        except Exception as e:
            logger.error("Failed to %s spool %s in Spoolman: %s", operation, spool_id, e)
            raise SpoolmanUnavailableError(f"Failed to {operation} spool {spool_id}") from e

    async def get_spool(self, spool_id: int) -> dict:
        """Fetch a single spool by ID from Spoolman."""
        response = await self._request_spool("GET", spool_id, operation="get")
        return response.json()

    async def get_all_spools(self, allow_archived: bool = False) -> list[dict]:
        """Fetch all spools from Spoolman, optionally including archived ones."""
        try:
            client = await self._get_client()
            params: dict = {}
            if allow_archived:
                params["allow_archived"] = "true"
            response = await client.get(f"{self.api_url}/spool", params=params or None)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to get all spools from Spoolman: %s", e)
            raise SpoolmanUnavailableError("Cannot reach Spoolman") from e

    async def delete_spool(self, spool_id: int) -> None:
        """Delete a spool from Spoolman."""
        await self._request_spool("DELETE", spool_id, operation="delete")

    async def set_spool_archived(self, spool_id: int, archived: bool) -> dict:
        """Archive or restore a spool in Spoolman."""
        response = await self._request_spool(
            "PATCH",
            spool_id,
            json_body={"archived": archived},
            operation="archive/restore",
        )
        return response.json()

    async def update_spool_full(
        self,
        spool_id: int,
        *,
        filament_id: int | None = None,
        remaining_weight: float | None = None,
        comment: str | None = None,
        price: float | None = None,
        location: str | None = None,
        clear_location: bool = False,
        extra: dict | None = None,
    ) -> dict:
        """Update a spool with full field support; unlike update_spool, does not auto-set last_used."""
        data: dict = {}
        if filament_id is not None:
            data["filament_id"] = filament_id
        if remaining_weight is not None:
            data["remaining_weight"] = remaining_weight
        if comment is not None:
            data["comment"] = comment if comment else None
        if price is not None:
            data["price"] = price
        if clear_location:
            data["location"] = None
        elif location is not None:
            data["location"] = location
        if extra is not None:
            data["extra"] = extra

        response = await self._request_spool("PATCH", spool_id, json_body=data, operation="update")
        return response.json()

    def extra_lock(self, spool_id: int) -> asyncio.Lock:
        """Return (creating if needed) the per-spool asyncio.Lock used by merge_spool_extra."""
        lock = self._extra_locks.get(spool_id)
        if lock is None:
            lock = asyncio.Lock()
            self._extra_locks[spool_id] = lock
        return lock

    async def merge_spool_extra(self, spool_id: int, new_fields: dict) -> dict:
        """Fetch the spool's extra dict, merge new_fields into it, then PATCH back — serialised per spool."""
        async with self.extra_lock(spool_id):
            current = await self.get_spool(spool_id)  # raises on error
            current_extra: dict = current.get("extra") or {}
            merged = {**current_extra, **new_fields}
            return await self.update_spool_full(spool_id=spool_id, extra=merged)

    async def find_or_create_vendor(self, name: str) -> int:
        """Return the Spoolman vendor ID for the given name, creating the vendor if absent."""
        vendors = await self.get_vendors()
        name_lower = name.strip().lower()
        for vendor in vendors:
            if vendor.get("name", "").strip().lower() == name_lower:
                return vendor["id"]
        created = await self.create_vendor(name.strip())
        return created["id"]

    async def find_or_create_filament(
        self,
        material: str,
        subtype: str,
        brand: str | None,
        color_hex: str,
        label_weight: int,
        color_name: str | None = None,
    ) -> int:
        """Return the filament ID matching material/name/brand/color, creating it if absent."""
        name = f"{material} {subtype}".strip() if subtype else material
        color = color_hex[:6].upper() if len(color_hex) >= 6 else color_hex.upper()

        vendor_id: int | None = None
        if brand:
            vendor_id = await self.find_or_create_vendor(brand)

        filaments = await self.get_filaments()
        for f in filaments:
            f_material = (f.get("material") or "").upper()
            f_name = (f.get("name") or "").strip()
            f_color = (f.get("color_hex") or "").upper()[:6]
            f_vendor = f.get("vendor") or {}
            f_vendor_name = (f_vendor.get("name") or "").strip().lower()

            material_match = f_material == material.upper()
            name_match = f_name.lower() == name.lower()
            color_match = f_color == color
            vendor_match = (not brand) or f_vendor_name == (brand or "").strip().lower()

            if material_match and name_match and color_match and vendor_match:
                return f["id"]

        filament = await self.create_filament(
            name=name,
            vendor_id=vendor_id,
            material=material,
            color_hex=color,
            color_name=color_name,
            weight=float(label_weight),
        )
        return filament["id"]

    async def use_spool(self, spool_id: int, used_weight: float) -> dict:
        """Record filament usage for a spool via the Spoolman /use endpoint."""
        try:
            client = await self._get_client()
            response = await client.put(
                f"{self.api_url}/spool/{spool_id}/use",
                json={"use_weight": used_weight},
            )
            if response.status_code == 404:
                raise SpoolmanNotFoundError(f"Spool {spool_id} not found in Spoolman")
            if 400 <= response.status_code < 500:
                raise SpoolmanClientError(
                    f"Spoolman rejected use_spool for spool {spool_id} (HTTP {response.status_code})",
                    response.status_code,
                )
            response.raise_for_status()
            return response.json()
        except (SpoolmanNotFoundError, SpoolmanClientError):
            raise
        except Exception as e:
            logger.error("Failed to record spool usage in Spoolman: %s", e)
            raise SpoolmanUnavailableError(f"Failed to record usage for spool {spool_id}") from e

    async def find_spool_by_tag(self, tag_uid: str, cached_spools: list[dict] | None = None) -> dict | None:
        """Return the spool matching the given RFID tag UID, or None if not found."""
        # Use cached spools if provided, otherwise fetch from API
        spools = cached_spools if cached_spools is not None else await self.get_spools()
        # Normalize tag_uid for comparison (uppercase, strip quotes)
        search_tag = tag_uid.strip('"').upper()

        for spool in spools:
            extra = spool.get("extra", {})
            if extra:
                stored_tag = extra.get("tag", "")
                # Normalize stored tag (strip quotes, uppercase)
                if stored_tag:
                    normalized_tag = stored_tag.strip('"').upper()
                    if normalized_tag == search_tag:
                        logger.debug("Found spool %s matching tag %s", spool["id"], tag_uid)
                        return spool
        return None

    def _find_spool_by_location(self, location: str, cached_spools: list[dict] | None) -> dict | None:
        """Return the spool at the exact location string, or None; fallback when RFID is unavailable."""
        if not cached_spools:
            return None
        for spool in cached_spools:
            if spool.get("location") == location:
                return spool
        return None

    async def find_spools_by_location_prefix(
        self, location_prefix: str, cached_spools: list[dict] | None = None
    ) -> list[dict]:
        """Return all spools whose location starts with location_prefix."""
        # Use cached spools if provided, otherwise fetch from API
        spools = cached_spools if cached_spools is not None else await self.get_spools()
        matching = []
        for spool in spools:
            location = spool.get("location", "")
            if location and location.startswith(location_prefix):
                matching.append(spool)
        return matching

    async def clear_location_for_removed_spools(
        self,
        printer_name: str,
        current_tray_uuids: set[str],
        cached_spools: list[dict] | None = None,
        synced_spool_ids: set[int] | None = None,
    ) -> int:
        """Clear location for Bambu Lab spools at this printer whose tray_uuid is no longer in the AMS."""
        location_prefix = f"{printer_name} - "
        spools_at_printer = await self.find_spools_by_location_prefix(location_prefix, cached_spools=cached_spools)
        cleared_count = 0

        for spool in spools_at_printer:
            spool_id = spool.get("id")

            # Skip spools that were just synced (matched by location or tag)
            if synced_spool_ids and spool_id in synced_spool_ids:
                continue

            # Get the tray_uuid (stored as "tag" in extra field)
            extra = spool.get("extra", {}) or {}
            stored_tag = extra.get("tag", "")
            if stored_tag:
                # Normalize: strip quotes and uppercase
                spool_uuid = stored_tag.strip('"').upper()
            else:
                spool_uuid = ""

            # Only clear location for Bambu Lab spools (those with a stored 32-character RFID tag).
            if len(spool_uuid) != BAMBU_RFID_TAG_LENGTH:
                continue

            # If this spool's UUID is not in the current AMS, clear its location
            if spool_uuid not in current_tray_uuids:
                logger.info(
                    f"Clearing location for spool {spool_id} "
                    f"(was: {spool.get('location')}, uuid: {spool_uuid[:16] if spool_uuid else 'none'}...)"
                )
                result = await self.update_spool(spool_id=spool_id, clear_location=True)
                if result:
                    cleared_count += 1

        return cleared_count

    async def ensure_bambu_vendor(self) -> int | None:
        """Return the Bambu Lab vendor ID in Spoolman, creating the vendor if absent."""
        vendors = await self.get_vendors()
        for vendor in vendors:
            if vendor.get("name", "").lower() == "bambu lab":
                return vendor["id"]

        # Create Bambu Lab vendor if not exists
        vendor = await self.create_vendor("Bambu Lab")
        return vendor["id"] if vendor else None

    async def ensure_tag_extra_field(self) -> bool:
        """Register the 'tag' extra field in Spoolman if not present; returns True on success."""
        try:
            client = await self._get_client()

            # Check if field already exists
            response = await client.get(f"{self.api_url}/field/spool/tag")
            if response.status_code == 200:
                logger.debug("Spoolman 'tag' extra field already exists")
                return True

            # Field doesn't exist - create it
            field_data = {
                "name": "tag",
                "field_type": "text",
                "default_value": None,
            }
            response = await client.post(f"{self.api_url}/field/spool/tag", json=field_data)
            if response.status_code in (200, 201):
                logger.info("Created 'tag' extra field in Spoolman")
                return True

            logger.warning("Failed to create 'tag' extra field: %s - %s", response.status_code, response.text)
            return False

        except Exception as e:
            logger.warning("Failed to ensure 'tag' extra field exists: %s", e)
            return False

    def parse_ams_tray(self, ams_id: int, tray_data: dict) -> AMSTray | None:
        """Parse raw MQTT tray data into an AMSTray; returns None for empty or invalid trays."""
        # Skip empty trays - check for valid tray_type
        tray_type = tray_data.get("tray_type", "")
        if not tray_type or tray_type.strip() == "":
            return None

        # Need valid color to create filament
        tray_color = tray_data.get("tray_color", "")
        if not tray_color or tray_color.strip() == "":
            logger.debug("Skipping tray with empty color")
            return None

        # Handle transparent/natural filament (RRGGBBAA with alpha=00)
        # Replace with cream color that represents how natural PLA actually looks
        if tray_color == "00000000":
            tray_color = "F5E6D3FF"  # Light cream/natural color

        # Get sub_brands, falling back to tray_type
        tray_sub_brands = tray_data.get("tray_sub_brands", "")
        if not tray_sub_brands or tray_sub_brands.strip() == "":
            tray_sub_brands = tray_type

        # Get tag_uid and tray_uuid, filtering out empty/invalid values
        tag_uid = tray_data.get("tag_uid", "")
        if tag_uid in ("", "0000000000000000"):
            tag_uid = ""
        tray_uuid = tray_data.get("tray_uuid", "")
        if tray_uuid in ("", "00000000000000000000000000000000"):
            tray_uuid = ""

        # Get tray_info_idx (Bambu filament preset ID like "GFA00")
        tray_info_idx = tray_data.get("tray_info_idx", "") or ""

        # Get remaining percentage (-1 means unknown/not read by AMS)
        remain = int(tray_data.get("remain", -1))

        return AMSTray(
            ams_id=ams_id,
            tray_id=int(tray_data.get("id", 0)),
            tray_type=tray_type.strip(),
            tray_sub_brands=tray_sub_brands.strip(),
            tray_color=tray_color,
            remain=remain,
            tag_uid=tag_uid,
            tray_uuid=tray_uuid,
            tray_info_idx=tray_info_idx.strip(),
            tray_weight=int(tray_data.get("tray_weight", 1000)),
        )

    def convert_ams_slot_to_location(self, ams_id: int, tray_id: int) -> str:
        """Return a human-readable location string (e.g. "AMS A1") for the given AMS slot."""
        if ams_id >= 254:
            return "External Spool"

        if 128 <= ams_id <= 135:
            # AMS-HT units use IDs 128-135
            ht_letter = chr(ord("A") + (ams_id - 128))
            return f"AMS-HT {ht_letter}{tray_id + 1}"

        ams_letter = chr(ord("A") + ams_id)
        return f"AMS {ams_letter}{tray_id + 1}"

    def is_bambu_lab_spool(self, tray_uuid: str, tag_uid: str = "", tray_info_idx: str = "") -> bool:
        """Return True if tray_uuid or tag_uid identifies a Bambu Lab spool; tray_info_idx is ignored."""
        # Check tray_uuid (preferred - consistent across printer models)
        if tray_uuid:
            uuid = tray_uuid.strip()
            if len(uuid) == 32 and uuid != "00000000000000000000000000000000":
                try:
                    int(uuid, 16)
                    return True
                except ValueError:
                    pass

        # Fallback: check tag_uid (RFID tag - varies between printer readers)
        # Bambu Lab RFID tags are 16 hex characters (8 bytes)
        if tag_uid:
            tag = tag_uid.strip()
            if len(tag) == 16 and tag != "0000000000000000":
                try:
                    int(tag, 16)
                    logger.debug("Identified Bambu Lab spool via tag_uid fallback: %s", tag)
                    return True
                except ValueError:
                    pass

        return False

    def calculate_remaining_weight(self, remain_percent: int, spool_weight: int) -> float:
        """Return remaining filament weight in grams given a percentage and total spool weight."""
        return (remain_percent / 100.0) * spool_weight

    async def sync_ams_tray(
        self,
        tray: AMSTray,
        printer_name: str,
        disable_weight_sync: bool = False,
        cached_spools: list[dict] | None = None,
        inventory_remaining: float | None = None,
        spoolman_spool_id_hint: int | None = None,
    ) -> dict | None:
        """Sync one AMS tray to Spoolman; creates the spool on first sight, updates weight otherwise."""
        logger.debug(
            f"Processing {printer_name} AMS {tray.ams_id} tray {tray.tray_id}: "
            f"type={tray.tray_type}, idx={tray.tray_info_idx or 'none'}, "
            f"uuid={tray.tray_uuid[:16] if tray.tray_uuid else 'none'}, "
            f"tag={tray.tag_uid[:8] if tray.tag_uid else 'none'}..."
        )

        # Determine which identifier to use for Spoolman (prefer tray_uuid, fallback to tag_uid)
        # Zero-filled values mean the AMS hasn't read the RFID tag — treat as no tag
        zero_uuid = "00000000000000000000000000000000"
        zero_tag = "0000000000000000"
        spool_tag = None
        if tray.tray_uuid and tray.tray_uuid != zero_uuid:
            spool_tag = tray.tray_uuid
        elif tray.tag_uid and tray.tag_uid != zero_tag:
            spool_tag = tray.tag_uid

        # Calculate remaining weight
        # Primary: AMS MQTT data (remain percentage + tray_weight)
        # Fallback: Built-in inventory tracked weight (when firmware sends invalid remain/tray_weight)
        if tray.remain >= 0 and tray.tray_weight > 0:
            remaining = self.calculate_remaining_weight(tray.remain, tray.tray_weight)
        elif inventory_remaining is not None:
            remaining = inventory_remaining
            logger.debug(
                "Using inventory weight fallback for %s AMS %s tray %s: %.1fg",
                printer_name,
                tray.ams_id,
                tray.tray_id,
                remaining,
            )
        else:
            remaining = None

        if spool_tag:
            # Primary path: match by RFID tag
            existing = await self.find_spool_by_tag(spool_tag, cached_spools=cached_spools)
            if existing:
                logger.info("Updating existing spool %s for tag %s...", existing["id"], spool_tag[:16])
                return await self.update_spool(
                    spool_id=existing["id"],
                    remaining_weight=None if disable_weight_sync else remaining,
                )

            # Spool not found by tag - auto-create it
            logger.info("Creating new spool in Spoolman for %s (tag: %s...)", tray.tray_sub_brands, spool_tag[:16])
            if self.is_bambu_lab_spool(tray.tray_uuid, tray.tag_uid, tray.tray_info_idx):
                filament = await self._find_or_create_filament(tray)
                filament_id = filament["id"] if filament else None
            else:
                # Non-BL spool with custom RFID: use generic vendor lookup
                brand = tray.tray_sub_brands if tray.tray_sub_brands != tray.tray_type else None
                try:
                    filament_id = await self.find_or_create_filament(
                        material=tray.tray_type,
                        subtype="",
                        brand=brand,
                        color_hex=tray.tray_color[:6],
                        label_weight=tray.tray_weight,
                    )
                except (SpoolmanNotFoundError, SpoolmanUnavailableError, SpoolmanClientError):
                    logger.warning("Could not find or create filament for non-BL spool %s", tray.tray_sub_brands)
                    return None

            if not filament_id:
                logger.error("Failed to find or create filament for %s", tray.tray_sub_brands)
                return None

            import json

            return await self.create_spool(
                filament_id=filament_id,
                remaining_weight=remaining,
                comment="Created by Bambuddy",
                extra={"tag": json.dumps(spool_tag)},
            )

        # No-RFID fallback: use the spool ID resolved from the local slot-assignment table.
        # Never create new spools without a tag to avoid duplicates.
        if spoolman_spool_id_hint is not None:
            existing = next((s for s in (cached_spools or []) if s.get("id") == spoolman_spool_id_hint), None)
            if existing is None:
                try:
                    existing = await self.get_spool(spoolman_spool_id_hint)
                except (SpoolmanNotFoundError, SpoolmanUnavailableError):
                    existing = None
            if existing:
                logger.info(
                    "Updating spool %s by slot-assignment hint (no RFID tag available)",
                    existing["id"],
                )
                return await self.update_spool(
                    spool_id=existing["id"],
                    remaining_weight=None if disable_weight_sync else remaining,
                )

        logger.info(
            "%s AMS %s tray %s — skipping (no RFID tag and no slot-assignment hint)",
            printer_name,
            tray.ams_id,
            tray.tray_id,
        )
        return None

    async def _find_or_create_filament(self, tray: AMSTray) -> dict | None:
        """Return a Bambu Lab filament matching the tray's material/color, creating it if absent."""
        # Get Bambu Lab vendor ID for filtering
        bambu_vendor_id = await self.ensure_bambu_vendor()
        color_hex = tray.tray_color[:6]  # Strip alpha channel

        # Search internal filaments - only match Bambu Lab vendor
        filaments = await self.get_filaments()
        for filament in filaments:
            # Only match filaments from Bambu Lab vendor
            fil_vendor_id = filament.get("vendor_id") or filament.get("vendor", {}).get("id")
            if fil_vendor_id != bambu_vendor_id:
                continue

            # Match by material and color (handle None values)
            fil_material = filament.get("material") or ""
            fil_color = filament.get("color_hex") or ""
            if fil_material.upper() == tray.tray_type.upper() and fil_color.upper() == color_hex.upper():
                return filament

        # Search external filaments (Bambu library)
        external = await self.get_external_filaments()
        for filament in external:
            fil_material = filament.get("material") or ""
            fil_color = filament.get("color_hex") or ""
            if fil_material.upper() == tray.tray_type.upper() and fil_color.upper() == color_hex.upper():
                # Found in external library - need to create internal copy
                return await self._create_filament_from_external(filament, tray)

        # Not found - create new Bambu Lab filament
        return await self.create_filament(
            name=tray.tray_sub_brands or tray.tray_type,
            vendor_id=bambu_vendor_id,
            material=tray.tray_type,
            color_hex=color_hex,
            weight=tray.tray_weight,
        )

    async def _create_filament_from_external(self, external: dict, tray: AMSTray) -> dict | None:
        """Create an internal Spoolman filament from an external library entry."""
        vendor_id = await self.ensure_bambu_vendor()
        return await self.create_filament(
            name=external.get("name", tray.tray_sub_brands),
            vendor_id=vendor_id,
            material=external.get("material", tray.tray_type),
            color_hex=external.get("color_hex", tray.tray_color[:6]),
            weight=external.get("weight", tray.tray_weight),
        )


# Global client instance (initialized when settings are loaded)
_spoolman_client: SpoolmanClient | None = None


async def get_spoolman_client() -> SpoolmanClient | None:
    """Return the global SpoolmanClient, or None if not configured."""
    return _spoolman_client


async def init_spoolman_client(url: str) -> SpoolmanClient:
    """Initialise (or reinitialise) the global SpoolmanClient; raises ValueError if url fails SSRF guard."""
    from backend.app.api.routes._spoolman_helpers import assert_safe_spoolman_url

    assert_safe_spoolman_url(url)

    global _spoolman_client
    if _spoolman_client:
        await _spoolman_client.close()

    _spoolman_client = SpoolmanClient(url)
    return _spoolman_client


async def close_spoolman_client():
    """Close the global Spoolman client."""
    global _spoolman_client
    if _spoolman_client:
        await _spoolman_client.close()
        _spoolman_client = None
