"""Spoolman integration service for syncing AMS filament data."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)


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

    ams_id: int  # 0-3 for regular AMS, 128-135 for external spool
    tray_id: int  # 0-3
    tray_type: str  # PLA, PETG, ABS, etc.
    tray_sub_brands: str  # Full name like "PLA Basic", "PETG HF"
    tray_color: str  # Hex color like "FEC600FF"
    remain: int  # Remaining percentage (0-100)
    tag_uid: str  # RFID tag UID
    tray_uuid: str  # Spool UUID
    tray_info_idx: str  # Bambu filament preset ID like "GFA00"
    tray_weight: int  # Spool weight in grams (usually 1000)


class SpoolmanClient:
    """Client for interacting with Spoolman API."""

    def __init__(self, base_url: str):
        """Initialize the Spoolman client.

        Args:
            base_url: The base URL of the Spoolman server (e.g., http://localhost:7912)
        """
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api/v1"
        self._client: httpx.AsyncClient | None = None
        self._connected = False

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client with connection pooling limits.

        Configures the client to prevent idle connection issues:
        - max_keepalive_connections=5: Limit number of persistent connections
        - keepalive_expiry=30: Close idle connections after 30 seconds
        - max_connections=10: Limit total connections to prevent resource exhaustion
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=10.0,
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
        """Check if Spoolman server is reachable.

        Returns:
            True if server is healthy, False otherwise.
        """
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
        """Get all spools from Spoolman with retry logic.

        Attempts to fetch spools up to 3 times with 500ms delay between attempts.
        This handles transient network errors like closed connections.

        Returns:
            List of spool dictionaries.

        Raises:
            Exception: If all 3 retry attempts fail.
        """
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
                    raise

    async def get_filaments(self) -> list[dict]:
        """Get all internal filaments from Spoolman.

        Returns:
            List of filament dictionaries.
        """
        try:
            client = await self._get_client()
            response = await client.get(f"{self.api_url}/filament")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to get filaments from Spoolman: %s", e)
            return []

    async def get_external_filaments(self) -> list[dict]:
        """Get external/library filaments from Spoolman.

        Returns:
            List of external filament dictionaries.
        """
        try:
            client = await self._get_client()
            response = await client.get(f"{self.api_url}/external/filament")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to get external filaments from Spoolman: %s", e)
            return []

    async def get_vendors(self) -> list[dict]:
        """Get all vendors from Spoolman.

        Returns:
            List of vendor dictionaries.
        """
        try:
            client = await self._get_client()
            response = await client.get(f"{self.api_url}/vendor")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to get vendors from Spoolman: %s", e)
            return []

    async def create_vendor(self, name: str) -> dict | None:
        """Create a new vendor in Spoolman.

        Args:
            name: Vendor name (e.g., "Bambu Lab")

        Returns:
            Created vendor dictionary or None on failure.
        """
        try:
            client = await self._get_client()
            response = await client.post(f"{self.api_url}/vendor", json={"name": name})
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to create vendor in Spoolman: %s", e)
            return None

    def _get_material_density(self, material: str | None) -> float:
        """Get typical density for a filament material type.

        Args:
            material: Material type (PLA, PETG, ABS, etc.)

        Returns:
            Density in g/cm³
        """
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
        weight: float | None = None,
        diameter: float = 1.75,
        density: float | None = None,
    ) -> dict | None:
        """Create a new filament in Spoolman.

        Args:
            name: Filament name
            vendor_id: Vendor ID
            material: Material type (PLA, PETG, etc.)
            color_hex: Color in hex format (without #)
            weight: Net weight in grams
            diameter: Filament diameter in mm (default 1.75)
            density: Filament density in g/cm³ (auto-calculated if not provided)

        Returns:
            Created filament dictionary or None on failure.
        """
        # Validate required fields
        if not name or not name.strip():
            logger.error("Cannot create filament: name is required")
            return None

        try:
            # Calculate density from material if not provided
            if density is None:
                density = self._get_material_density(material)

            data = {
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
            if weight:
                data["weight"] = weight

            logger.debug("Creating filament in Spoolman: %s", data)
            client = await self._get_client()
            response = await client.post(f"{self.api_url}/filament", json=data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("Failed to create filament in Spoolman: %s, response: %s", e, e.response.text)
            return None
        except Exception as e:
            logger.error("Failed to create filament in Spoolman: %s", e)
            return None

    async def create_spool(
        self,
        filament_id: int,
        remaining_weight: float | None = None,
        location: str | None = None,
        lot_nr: str | None = None,
        comment: str | None = None,
        extra: dict | None = None,
    ) -> dict | None:
        """Create a new spool in Spoolman.

        Args:
            filament_id: ID of the filament type
            remaining_weight: Remaining weight in grams
            location: Physical location description
            lot_nr: Lot/batch number
            comment: Optional comment
            extra: Extra fields (e.g., {"tag": "RFID_TAG_UID"})

        Returns:
            Created spool dictionary or None on failure.
        """
        try:
            data = {"filament_id": filament_id}
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
            client = await self._get_client()
            response = await client.post(f"{self.api_url}/spool", json=data)
            response.raise_for_status()
            result = response.json()
            logger.info("Created spool %s in Spoolman", result.get("id"))
            return result
        except httpx.HTTPStatusError as e:
            logger.error("Failed to create spool in Spoolman: %s, response: %s", e, e.response.text)
            return None
        except Exception as e:
            logger.error("Failed to create spool in Spoolman: %s", e)
            return None

    async def update_spool(
        self,
        spool_id: int,
        remaining_weight: float | None = None,
        location: str | None = None,
        clear_location: bool = False,
        extra: dict | None = None,
    ) -> dict | None:
        """Update an existing spool in Spoolman.

        Args:
            spool_id: ID of the spool to update
            remaining_weight: New remaining weight in grams
            location: New location (ignored if clear_location is True)
            clear_location: If True, clears the location field
            extra: Extra fields to update

        Returns:
            Updated spool dictionary or None on failure.
        """
        try:
            data = {}
            if remaining_weight is not None:
                data["remaining_weight"] = remaining_weight
            if clear_location:
                data["location"] = None
            elif location:
                data["location"] = location
            if extra:
                data["extra"] = extra

            # Always update last_used
            data["last_used"] = datetime.now(timezone.utc).isoformat()

            client = await self._get_client()
            response = await client.patch(f"{self.api_url}/spool/{spool_id}", json=data)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to update spool in Spoolman: %s", e)
            return None

    async def use_spool(self, spool_id: int, used_weight: float) -> dict | None:
        """Record filament usage for a spool.

        Args:
            spool_id: ID of the spool
            used_weight: Amount of filament used in grams

        Returns:
            Updated spool dictionary or None on failure.
        """
        try:
            client = await self._get_client()
            response = await client.put(
                f"{self.api_url}/spool/{spool_id}/use",
                json={"use_weight": used_weight},
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to record spool usage in Spoolman: %s", e)
            return None

    async def find_spool_by_tag(self, tag_uid: str, cached_spools: list[dict] | None = None) -> dict | None:
        """Find a spool by its RFID tag UID.

        Args:
            tag_uid: The RFID tag UID to search for
            cached_spools: Optional pre-fetched list of spools to search (avoids API call)

        Returns:
            Spool dictionary or None if not found.
        """
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
        """Find a spool by exact location match.

        Used as fallback when RFID tag data is unavailable (e.g., newer firmware
        that doesn't expose tray_uuid/tag_uid via MQTT).

        Args:
            location: Exact location string (e.g., "H2D-1 - AMS A1")
            cached_spools: Pre-fetched list of spools to search

        Returns:
            Spool dictionary or None if not found.
        """
        if not cached_spools:
            return None
        for spool in cached_spools:
            if spool.get("location") == location:
                return spool
        return None

    async def find_spools_by_location_prefix(
        self, location_prefix: str, cached_spools: list[dict] | None = None
    ) -> list[dict]:
        """Find all spools with locations starting with a given prefix.

        Args:
            location_prefix: The location prefix to search for (e.g., "PrinterName - ")
            cached_spools: Optional pre-fetched list of spools to search (avoids API call)

        Returns:
            List of spool dictionaries with matching locations.
        """
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
        """Clear location for spools that are no longer in the AMS.

        When a spool is removed from the AMS, its location should be cleared
        in Spoolman. This method finds all spools with locations for this printer
        and clears the location for any that are not in the current_tray_uuids set
        and were not synced in this cycle (synced_spool_ids).

        Args:
            printer_name: The printer name used as location prefix
            current_tray_uuids: Set of tray_uuids currently in the AMS
            cached_spools: Optional pre-fetched list of spools to search (avoids API call)
            synced_spool_ids: Set of spool IDs that were synced in this cycle
                (protects location-matched spools when RFID data is unavailable)

        Returns:
            Number of spools whose location was cleared.
        """
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
        """Ensure Bambu Lab vendor exists and return its ID.

        Returns:
            Vendor ID or None on failure.
        """
        vendors = await self.get_vendors()
        for vendor in vendors:
            if vendor.get("name", "").lower() == "bambu lab":
                return vendor["id"]

        # Create Bambu Lab vendor if not exists
        vendor = await self.create_vendor("Bambu Lab")
        return vendor["id"] if vendor else None

    async def ensure_tag_extra_field(self) -> bool:
        """Ensure the 'tag' extra field exists for spools.

        Spoolman requires extra fields to be registered before use.
        This creates the 'tag' field used to store RFID/UUID identifiers.

        Returns:
            True if field exists or was created, False on failure.
        """
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
        """Parse AMS tray data into AMSTray object.

        Args:
            ams_id: The AMS unit ID (0-3 for regular, 128-135 for external)
            tray_data: Raw tray data from MQTT

        Returns:
            AMSTray object or None if tray is empty or invalid.
        """
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
        """Convert AMS ID and tray ID to human-readable location.

        Args:
            ams_id: AMS unit ID (0-3 for regular AMS, 128-135 for external)
            tray_id: Tray ID within the AMS (0-3)

        Returns:
            Location string like "AMS A1", "AMS B2", "External"
        """
        if ams_id >= 128:
            return "External Spool"

        ams_letter = chr(ord("A") + ams_id)
        return f"AMS {ams_letter}{tray_id + 1}"

    def is_bambu_lab_spool(self, tray_uuid: str, tag_uid: str = "", tray_info_idx: str = "") -> bool:
        """Check if a tray has a valid Bambu Lab spool.

        Bambu Lab spools can be identified by:
        1. tray_uuid: 32-character hex string (preferred, consistent across printers)
        2. tag_uid: 16-character hex string (RFID tag, varies between readers)
        3. tray_info_idx: Bambu filament preset ID like "GFA00" (most reliable)

        Non-Bambu Lab spools (SpoolEase, third-party) won't have these identifiers.

        Args:
            tray_uuid: The tray UUID to check (32 hex chars)
            tag_uid: The RFID tag UID to check as fallback (16 hex chars)
            tray_info_idx: Bambu filament preset ID like "GFA00", "GFB00"

        Returns:
            True if the spool has valid Bambu Lab identifiers, False otherwise.
        """
        # Check tray_info_idx first - Bambu filament preset IDs like "GFA00", "GFB00", etc.
        # This is the most reliable indicator as it's set when the spool is recognized
        if tray_info_idx:
            idx = tray_info_idx.strip()
            # Bambu Lab preset IDs start with "GF" followed by letter and digits
            # e.g., GFA00, GFB00, GFL00, GFN00, GFG00, GFS00, GFU00
            if idx and len(idx) >= 3 and idx.startswith("GF"):
                logger.debug("Identified Bambu Lab spool via tray_info_idx: %s", idx)
                return True

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
        """Calculate remaining weight from percentage.

        Args:
            remain_percent: Remaining percentage (0-100)
            spool_weight: Total spool weight in grams

        Returns:
            Remaining weight in grams.
        """
        return (remain_percent / 100.0) * spool_weight

    async def sync_ams_tray(
        self,
        tray: AMSTray,
        printer_name: str,
        disable_weight_sync: bool = False,
        cached_spools: list[dict] | None = None,
        inventory_remaining: float | None = None,
    ) -> dict | None:
        """Sync a single AMS tray to Spoolman.

        Only syncs trays with valid Bambu Lab tray_uuid (32 hex characters).
        Non-Bambu Lab spools (SpoolEase/third-party) are skipped.

        Uses tray_uuid for matching, as it's consistent across all printer models
        (unlike tag_uid which varies between X1C/H2D readers).

        Args:
            tray: The AMSTray to sync
            printer_name: Name of the printer for location
            disable_weight_sync: If True, skip updating remaining_weight for existing spools.
                This allows Spoolman's granular usage tracking to maintain accurate weights.
            cached_spools: Optional pre-fetched list of spools to search (avoids API calls).
                When provided, this cache is passed to find_spool_by_tag to avoid redundant
                API calls during batch sync operations.
            inventory_remaining: Optional fallback remaining weight (grams) from the built-in
                inventory when AMS MQTT data has invalid remain/tray_weight values.

        Returns:
            Synced spool dictionary or None if skipped or failed.
        """
        logger.debug(
            f"Processing {printer_name} AMS {tray.ams_id} tray {tray.tray_id}: "
            f"type={tray.tray_type}, idx={tray.tray_info_idx or 'none'}, "
            f"uuid={tray.tray_uuid[:16] if tray.tray_uuid else 'none'}, "
            f"tag={tray.tag_uid[:8] if tray.tag_uid else 'none'}..."
        )

        # Only sync trays with valid Bambu Lab identifiers
        if not self.is_bambu_lab_spool(tray.tray_uuid, tray.tag_uid, tray.tray_info_idx):
            if tray.tray_uuid or tray.tag_uid or tray.tray_info_idx:
                logger.info(
                    f"Skipping non-Bambu Lab spool: {printer_name} AMS {tray.ams_id} tray {tray.tray_id} "
                    f"(tray_info_idx={tray.tray_info_idx}, tray_uuid={tray.tray_uuid}, tag_uid={tray.tag_uid})"
                )
            else:
                logger.debug("Skipping tray without RFID tag: AMS %s tray %s", tray.ams_id, tray.tray_id)
            return None

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
        location = f"{printer_name} - {self.convert_ams_slot_to_location(tray.ams_id, tray.tray_id)}"

        if spool_tag:
            # Primary path: match by RFID tag
            existing = await self.find_spool_by_tag(spool_tag, cached_spools=cached_spools)
            if existing:
                logger.info("Updating existing spool %s for tag %s...", existing["id"], spool_tag[:16])
                return await self.update_spool(
                    spool_id=existing["id"],
                    remaining_weight=None if disable_weight_sync else remaining,
                    location=location,
                )

            # Spool not found by tag - auto-create it
            logger.info("Creating new spool in Spoolman for %s (tag: %s...)", tray.tray_sub_brands, spool_tag[:16])
            filament = await self._find_or_create_filament(tray)
            if not filament:
                logger.error("Failed to find or create filament for %s", tray.tray_sub_brands)
                return None

            import json

            return await self.create_spool(
                filament_id=filament["id"],
                remaining_weight=remaining,
                location=location,
                comment="Created by Bambuddy",
                extra={"tag": json.dumps(spool_tag)},
            )

        # Fallback path: no RFID tag available (newer firmware may not expose UUIDs)
        # Only update existing spools matched by location — never create new ones without a tag
        # to avoid duplicates when old spools exist from previous RFID-based syncs
        existing = self._find_spool_by_location(location, cached_spools)
        if existing:
            logger.info(
                "Updating spool %s by location match '%s' (no RFID tag available)",
                existing["id"],
                location,
            )
            return await self.update_spool(
                spool_id=existing["id"],
                remaining_weight=None if disable_weight_sync else remaining,
                location=location,
            )

        logger.info(
            "No existing spool found at '%s' — skipping (no RFID tag to create with)",
            location,
        )
        return None

    async def _find_or_create_filament(self, tray: AMSTray) -> dict | None:
        """Find existing filament or create new one.

        Only matches Bambu Lab vendor filaments since this is called for
        Bambu Lab spools. Third-party filaments (like 3DJAKE) are ignored
        to prevent incorrect matching by color alone.

        Args:
            tray: The AMSTray containing filament info

        Returns:
            Filament dictionary or None on failure.
        """
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
        """Create internal filament from external library entry.

        Args:
            external: External filament dictionary
            tray: The AMSTray for additional info

        Returns:
            Created filament dictionary or None on failure.
        """
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
    """Get the global Spoolman client instance.

    Returns:
        SpoolmanClient instance or None if not configured.
    """
    return _spoolman_client


async def init_spoolman_client(url: str) -> SpoolmanClient:
    """Initialize the global Spoolman client.

    Args:
        url: Spoolman server URL

    Returns:
        Initialized SpoolmanClient instance.
    """
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
