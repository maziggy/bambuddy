"""Spoolman integration service for syncing AMS filament data."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

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
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
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
            logger.warning(f"Spoolman health check failed: {e}")
            self._connected = False
            return False

    @property
    def is_connected(self) -> bool:
        """Check if client is connected to Spoolman."""
        return self._connected

    async def get_spools(self) -> list[dict]:
        """Get all spools from Spoolman.

        Returns:
            List of spool dictionaries.
        """
        try:
            client = await self._get_client()
            response = await client.get(f"{self.api_url}/spool")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get spools from Spoolman: {e}")
            return []

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
            logger.error(f"Failed to get filaments from Spoolman: {e}")
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
            logger.error(f"Failed to get external filaments from Spoolman: {e}")
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
            logger.error(f"Failed to get vendors from Spoolman: {e}")
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
            logger.error(f"Failed to create vendor in Spoolman: {e}")
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

            logger.debug(f"Creating filament in Spoolman: {data}")
            client = await self._get_client()
            response = await client.post(f"{self.api_url}/filament", json=data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create filament in Spoolman: {e}, response: {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Failed to create filament in Spoolman: {e}")
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

            logger.debug(f"Creating spool in Spoolman: {data}")
            client = await self._get_client()
            response = await client.post(f"{self.api_url}/spool", json=data)
            response.raise_for_status()
            result = response.json()
            logger.info(f"Created spool {result.get('id')} in Spoolman")
            return result
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create spool in Spoolman: {e}, response: {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Failed to create spool in Spoolman: {e}")
            return None

    async def update_spool(
        self,
        spool_id: int,
        remaining_weight: float | None = None,
        location: str | None = None,
        extra: dict | None = None,
    ) -> dict | None:
        """Update an existing spool in Spoolman.

        Args:
            spool_id: ID of the spool to update
            remaining_weight: New remaining weight in grams
            location: New location
            extra: Extra fields to update

        Returns:
            Updated spool dictionary or None on failure.
        """
        try:
            data = {}
            if remaining_weight is not None:
                data["remaining_weight"] = remaining_weight
            if location:
                data["location"] = location
            if extra:
                data["extra"] = extra

            # Always update last_used
            data["last_used"] = datetime.now(UTC).isoformat()

            client = await self._get_client()
            response = await client.patch(f"{self.api_url}/spool/{spool_id}", json=data)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to update spool in Spoolman: {e}")
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
            logger.error(f"Failed to record spool usage in Spoolman: {e}")
            return None

    async def find_spool_by_tag(self, tag_uid: str) -> dict | None:
        """Find a spool by its RFID tag UID.

        Args:
            tag_uid: The RFID tag UID to search for

        Returns:
            Spool dictionary or None if not found.
        """
        spools = await self.get_spools()
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
                        logger.debug(f"Found spool {spool['id']} matching tag {tag_uid}")
                        return spool
        return None

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
        if not tray_color or tray_color in ("", "00000000"):
            logger.debug(f"Skipping tray with invalid color: {tray_color}")
            return None

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

        # Get remaining percentage, ensure non-negative
        remain = max(0, int(tray_data.get("remain", 0)))

        return AMSTray(
            ams_id=ams_id,
            tray_id=int(tray_data.get("id", 0)),
            tray_type=tray_type.strip(),
            tray_sub_brands=tray_sub_brands.strip(),
            tray_color=tray_color,
            remain=remain,
            tag_uid=tag_uid,
            tray_uuid=tray_uuid,
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

    def is_bambu_lab_spool(self, tray_uuid: str) -> bool:
        """Check if a tray has a valid Bambu Lab spool UUID.

        Bambu Lab spools have a tray_uuid which is a 32-character hex string.
        This UUID is consistent across all printer models (unlike tag_uid which
        varies between X1C/H2D readers).

        Non-Bambu Lab spools (SpoolEase, third-party) won't have a valid tray_uuid.

        Args:
            tray_uuid: The tray UUID to check

        Returns:
            True if the spool has a valid Bambu Lab UUID, False otherwise.
        """
        if not tray_uuid:
            return False
        # Bambu Lab tray_uuid is always 32 hex characters
        uuid = tray_uuid.strip()
        if len(uuid) != 32:
            return False
        # Verify it's all hex characters and not empty/zero
        if uuid == "00000000000000000000000000000000":
            return False
        try:
            int(uuid, 16)
            return True
        except ValueError:
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

    async def sync_ams_tray(self, tray: AMSTray, printer_name: str) -> dict | None:
        """Sync a single AMS tray to Spoolman.

        Only syncs trays with valid Bambu Lab tray_uuid (32 hex characters).
        Non-Bambu Lab spools (SpoolEase/third-party) are skipped.

        Uses tray_uuid for matching, as it's consistent across all printer models
        (unlike tag_uid which varies between X1C/H2D readers).

        Args:
            tray: The AMSTray to sync
            printer_name: Name of the printer for location

        Returns:
            Synced spool dictionary or None if skipped or failed.
        """
        logger.debug(
            f"Processing {printer_name} AMS {tray.ams_id} tray {tray.tray_id}: "
            f"type={tray.tray_type}, uuid={tray.tray_uuid[:16] if tray.tray_uuid else 'none'}..."
        )

        # Only sync trays with valid Bambu Lab tray_uuid
        if not self.is_bambu_lab_spool(tray.tray_uuid):
            if tray.tray_uuid or tray.tag_uid:
                logger.info(
                    f"Skipping non-Bambu Lab spool: {printer_name} AMS {tray.ams_id} tray {tray.tray_id} "
                    f"(tray_uuid={tray.tray_uuid}, tag_uid={tray.tag_uid})"
                )
            else:
                logger.debug(f"Skipping tray without RFID tag: AMS {tray.ams_id} tray {tray.tray_id}")
            return None

        # Calculate remaining weight
        remaining = self.calculate_remaining_weight(tray.remain, tray.tray_weight)
        location = f"{printer_name} - {self.convert_ams_slot_to_location(tray.ams_id, tray.tray_id)}"

        # Find existing spool by tray_uuid (stored as "tag" in Spoolman)
        existing = await self.find_spool_by_tag(tray.tray_uuid)
        if existing:
            # Update existing spool
            logger.info(f"Updating existing spool {existing['id']} for tray_uuid {tray.tray_uuid}")
            return await self.update_spool(
                spool_id=existing["id"],
                remaining_weight=remaining,
                location=location,
            )

        # Spool not found - auto-create it
        logger.info(
            f"Creating new spool in Spoolman for {tray.tray_sub_brands} " f"(tray_uuid: {tray.tray_uuid[:16]}...)"
        )

        # First find or create the filament type
        filament = await self._find_or_create_filament(tray)
        if not filament:
            logger.error(f"Failed to find or create filament for {tray.tray_sub_brands}")
            return None

        # Create the spool with tray_uuid stored as "tag" in extra field
        # Note: Spoolman extra field values must be valid JSON, so we encode the string
        import json

        return await self.create_spool(
            filament_id=filament["id"],
            remaining_weight=remaining,
            location=location,
            comment=f"Auto-created from {printer_name} AMS",
            extra={"tag": json.dumps(tray.tray_uuid)},
        )

    async def _find_or_create_filament(self, tray: AMSTray) -> dict | None:
        """Find existing filament or create new one.

        Args:
            tray: The AMSTray containing filament info

        Returns:
            Filament dictionary or None on failure.
        """
        # Search internal filaments first
        filaments = await self.get_filaments()
        color_hex = tray.tray_color[:6]  # Strip alpha channel

        for filament in filaments:
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

        # Not found - create new filament
        vendor_id = await self.ensure_bambu_vendor()
        return await self.create_filament(
            name=tray.tray_sub_brands or tray.tray_type,
            vendor_id=vendor_id,
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
