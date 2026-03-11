"""FilaMan integration service for syncing AMS filament data."""

import logging

import httpx

logger = logging.getLogger(__name__)


class FilaManClient:
    """Client for interacting with FilaMan API."""

    def __init__(self, base_url: str, api_key: str):
        """Initialize the FilaMan client.

        Args:
            base_url: The base URL of the FilaMan server (e.g., http://192.168.1.x:8000)
            api_key: FilaMan API key for authentication (X-API-Key header)
        """
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api/v1"
        self._client: httpx.AsyncClient | None = None
        self._api_key = api_key

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"X-API-Key": self._api_key},
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
        """Check if FilaMan server is reachable.

        Returns:
            True if server is healthy, False otherwise.
        """
        try:
            client = await self._get_client()
            # FilaMan has a system health endpoint
            response = await client.get(f"{self.api_url}/system/health")
            if response.status_code == 200:
                return True
            # Fallback: try to fetch filaments with page_size=1
            response = await client.get(f"{self.api_url}/filaments", params={"page_size": 1})
            return response.status_code == 200
        except Exception as e:
            logger.warning("FilaMan health check failed: %s", e)
            return False

    async def get_spools(self) -> list[dict]:
        """Get all spools from FilaMan (non-archived).

        Returns:
            List of spool dictionaries.
        """
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.api_url}/spools",
                params={"include_archived": "false", "page_size": 200},
            )
            response.raise_for_status()
            data = response.json()
            # FilaMan returns PaginatedResponse with "items" key
            if isinstance(data, dict):
                return data.get("items", [])
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error("Failed to get spools from FilaMan: %s", e)
            raise

    async def get_filaments(self) -> list[dict]:
        """Get all filaments from FilaMan.

        Returns:
            List of filament dictionaries.
        """
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.api_url}/filaments",
                params={"page_size": 200},
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data.get("items", [])
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error("Failed to get filaments from FilaMan: %s", e)
            return []

    async def get_manufacturers(self) -> list[dict]:
        """Get all manufacturers from FilaMan.

        Returns:
            List of manufacturer dictionaries.
        """
        try:
            client = await self._get_client()
            response = await client.get(f"{self.api_url}/manufacturers")
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data.get("items", [])
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error("Failed to get manufacturers from FilaMan: %s", e)
            return []

    async def update_spool_rfid(self, spool_id: int, rfid_uid: str) -> dict | None:
        """Update a spool's rfid_uid field (used for tray linking).

        Stores the AMS tray_uuid as the spool's rfid_uid so that
        FilaMan can be queried to find which spool is in which AMS tray.

        Args:
            spool_id: FilaMan spool ID
            rfid_uid: The AMS tray_uuid to store as the spool's RFID UID

        Returns:
            Updated spool dictionary or None on failure.
        """
        try:
            client = await self._get_client()
            response = await client.patch(
                f"{self.api_url}/spools/{spool_id}",
                json={"rfid_uid": rfid_uid},
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to update spool %s rfid_uid in FilaMan: %s", spool_id, e)
            return None

    async def report_consumption(self, spool_id: int, delta_weight_g: float) -> dict | None:
        """Report filament consumption for a spool.

        Args:
            spool_id: FilaMan spool ID
            delta_weight_g: Amount of filament consumed in grams (positive value)

        Returns:
            Response dictionary or None on failure.
        """
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self.api_url}/spools/{spool_id}/consumptions",
                json={"delta_weight_g": delta_weight_g, "source": "bambuddy"},
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to report consumption for spool %s in FilaMan: %s", spool_id, e)
            return None

    async def find_spool_by_tray_uuid(self, tray_uuid: str) -> dict | None:
        """Find a spool by its rfid_uid matching the given tray_uuid.

        Fetches all spools and performs a local filter, as FilaMan does not
        provide a direct query-by-rfid_uid endpoint.

        Args:
            tray_uuid: The AMS tray UUID to search for

        Returns:
            Spool dictionary or None if not found.
        """
        try:
            spools = await self.get_spools()
            search_uuid = tray_uuid.strip().upper()
            for spool in spools:
                rfid_uid = spool.get("rfid_uid") or ""
                if rfid_uid.strip().upper() == search_uuid:
                    return spool
            return None
        except Exception as e:
            logger.error("Failed to find spool by tray_uuid in FilaMan: %s", e)
            return None

    def map_spool_to_bambuddy(self, spool: dict) -> dict:
        """Map a FilaMan spool to Bambuddy's internal format.

        FilaMan spool structure:
          spool.id, spool.rfid_uid, spool.remaining_weight_g,
          spool.initial_total_weight_g, spool.empty_spool_weight_g,
          spool.filament → {material_type, designation, manufacturer, colors}

        Args:
            spool: FilaMan spool dictionary

        Returns:
            Bambuddy-compatible spool dictionary.
        """
        filament = spool.get("filament") or {}
        manufacturer = filament.get("manufacturer") or {}
        colors = filament.get("colors") or []
        first_color = colors[0] if colors else {}

        # Extract hex color without '#'
        hex_code = first_color.get("hex_code") or ""
        color_hex = hex_code.lstrip("#")[:6] if hex_code else "FFFFFF"

        # Determine name: designation + manufacturer as prefix
        designation = filament.get("designation") or ""
        manufacturer_name = manufacturer.get("name") or ""
        if manufacturer_name and designation:
            name = f"{manufacturer_name} {designation}"
        else:
            name = designation or manufacturer_name or "Unknown"

        # Determine weight: prefer initial_total_weight_g, fallback to label_weight
        initial_weight = (
            spool.get("initial_total_weight_g")
            or spool.get("label_weight")
            or 0
        )

        return {
            "id": spool.get("id"),
            "material": filament.get("material_type") or "",
            "name": name,
            "brand": manufacturer_name,
            "color_hex": color_hex,
            "remaining_weight": spool.get("remaining_weight_g"),
            "initial_weight": initial_weight,
            "spool_weight": spool.get("empty_spool_weight_g"),
            "tag_uid": spool.get("rfid_uid") or "",
            "filament_id": filament.get("id"),
        }


# Global client instance (initialized when settings are loaded)
_filaman_client: FilaManClient | None = None


def get_filaman_client() -> FilaManClient | None:
    """Get the global FilaMan client instance.

    Returns:
        FilaManClient instance or None if not configured.
    """
    return _filaman_client


def init_filaman_client(url: str, api_key: str) -> FilaManClient:
    """Initialize the global FilaMan client.

    Args:
        url: FilaMan server URL
        api_key: FilaMan API key

    Returns:
        Initialized FilaManClient instance.
    """
    global _filaman_client
    if _filaman_client:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_filaman_client.close())
            else:
                loop.run_until_complete(_filaman_client.close())
        except Exception:
            pass

    _filaman_client = FilaManClient(url, api_key)
    return _filaman_client


async def close_filaman_client():
    """Close the global FilaMan client."""
    global _filaman_client
    if _filaman_client:
        await _filaman_client.close()
        _filaman_client = None
