"""Service for controlling WLED devices via JSON API."""

import json
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Printer states mapped to human labels for the UI
WLED_STATES = ["RUNNING", "IDLE", "FINISH", "FAILED", "PAUSE", "PREPARE", "offline"]

DEFAULT_STATE_MAP = {
    "RUNNING": {"color": "#0064FF", "brightness": 200, "effect_id": 0, "preset_id": None},
    "IDLE": {"color": "#FFFFFF", "brightness": 80, "effect_id": 0, "preset_id": None},
    "FINISH": {"color": "#00FF00", "brightness": 255, "effect_id": 0, "preset_id": None},
    "FAILED": {"color": "#FF0000", "brightness": 255, "effect_id": 0, "preset_id": None},
    "PAUSE": {"color": "#FFC800", "brightness": 150, "effect_id": 0, "preset_id": None},
    "PREPARE": {"color": "#0032AA", "brightness": 120, "effect_id": 0, "preset_id": None},
    "offline": {"color": "#000000", "brightness": 0, "effect_id": 0, "preset_id": None},
}


def _hex_to_rgb(hex_color: str) -> list[int]:
    """Convert #RRGGBB hex string to [R, G, B] list."""
    h = hex_color.lstrip("#")
    return [int(h[i : i + 2], 16) for i in (0, 2, 4)]


def _build_wled_payload(config: dict) -> dict:
    """Build WLED JSON API payload from a state config entry."""
    preset_id = config.get("preset_id")
    if preset_id is not None:
        return {"ps": int(preset_id)}

    brightness = config.get("brightness", 128)
    if brightness == 0:
        return {"on": False}

    color = config.get("color", "#FFFFFF")
    effect_id = config.get("effect_id", 0)

    return {
        "on": True,
        "bri": int(brightness),
        "seg": [{"col": [_hex_to_rgb(color)], "fx": int(effect_id)}],
    }


class WledService:
    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout
        self._state_map: dict = {}
        self._last_sent: dict[int, str] = {}  # printer_id → last state name sent
        self._cache_valid = False

    def invalidate_cache(self):
        """Call this when global WLED settings are updated."""
        self._cache_valid = False

    async def _load_state_map(self, db: "AsyncSession"):
        from sqlalchemy import select

        from backend.app.models.settings import Settings

        result = await db.execute(select(Settings).where(Settings.key == "wled_state_map"))
        row = result.scalar_one_or_none()
        if row and row.value:
            try:
                loaded = json.loads(row.value)
                self._state_map = {**DEFAULT_STATE_MAP, **loaded}
            except (json.JSONDecodeError, TypeError):
                self._state_map = dict(DEFAULT_STATE_MAP)
        else:
            self._state_map = dict(DEFAULT_STATE_MAP)
        self._cache_valid = True

    async def _is_globally_enabled(self, db: "AsyncSession") -> bool:
        from sqlalchemy import select

        from backend.app.models.settings import Settings

        result = await db.execute(select(Settings).where(Settings.key == "wled_enabled"))
        row = result.scalar_one_or_none()
        return bool(row and row.value and row.value.lower() == "true")

    def _wled_base_url(self, host: str, port: int) -> str:
        return f"http://{host}:{port}"

    def _headers(self, api_key: str | None) -> dict:
        if api_key:
            return {"Authorization": f"Bearer {api_key}"}
        return {}

    async def apply_state(
        self,
        printer_id: int,
        host: str,
        port: int,
        api_key: str | None,
        state: str,
        db: "AsyncSession",
    ) -> None:
        """Send the appropriate WLED state for a printer state change.

        Only fires an HTTP request when the state actually changes, avoiding
        flooding WLED on every temperature tick.
        """
        if not host:
            return

        # Dedup: only act when state changes
        last = self._last_sent.get(printer_id)
        if last == state:
            return

        if not self._cache_valid:
            await self._load_state_map(db)

        if not await self._is_globally_enabled(db):
            return

        config = self._state_map.get(state) or self._state_map.get("IDLE") or DEFAULT_STATE_MAP["IDLE"]
        payload = _build_wled_payload(config)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self._wled_base_url(host, port)}/json/state",
                    headers=self._headers(api_key),
                    json=payload,
                )
                response.raise_for_status()
                self._last_sent[printer_id] = state
                logger.debug("WLED state applied: printer=%d state=%s payload=%s", printer_id, state, payload)
        except httpx.TimeoutException:
            logger.warning("WLED timeout for printer %d at %s:%d", printer_id, host, port)
        except Exception as e:
            logger.warning("WLED apply_state failed for printer %d: %s", printer_id, e)

    def clear_printer(self, printer_id: int) -> None:
        """Remove cached state when a printer is disconnected or deleted."""
        self._last_sent.pop(printer_id, None)

    async def test_connection(self, host: str, port: int, api_key: str | None) -> dict:
        """Test connectivity to a WLED device.

        Returns dict with success, device_name, version, and error keys.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self._wled_base_url(host, port)}/json/info",
                    headers=self._headers(api_key),
                )
                response.raise_for_status()
                data = response.json()
                return {
                    "success": True,
                    "device_name": data.get("name"),
                    "version": data.get("ver"),
                    "led_count": data.get("leds", {}).get("count"),
                    "error": None,
                }
        except httpx.TimeoutException:
            return {
                "success": False,
                "device_name": None,
                "version": None,
                "led_count": None,
                "error": "Connection timeout",
            }
        except httpx.ConnectError:
            return {
                "success": False,
                "device_name": None,
                "version": None,
                "led_count": None,
                "error": f"Could not connect to {host}:{port}",
            }
        except httpx.HTTPStatusError as e:
            return {
                "success": False,
                "device_name": None,
                "version": None,
                "led_count": None,
                "error": f"HTTP {e.response.status_code}",
            }
        except Exception as e:
            return {"success": False, "device_name": None, "version": None, "led_count": None, "error": str(e)}

    async def get_presets(self, host: str, port: int, api_key: str | None) -> list[dict]:
        """Fetch presets list from a WLED device.

        Returns list of {id, name} dicts, excluding presets with empty names.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self._wled_base_url(host, port)}/json/presets",
                    headers=self._headers(api_key),
                )
                response.raise_for_status()
                data = response.json()
                presets = []
                for preset_id_str, preset_data in data.items():
                    try:
                        preset_id = int(preset_id_str)
                    except ValueError:
                        continue
                    # WLED uses preset id 0 for current state placeholder — skip it
                    if preset_id == 0:
                        continue
                    name = preset_data.get("n", "").strip()
                    if name:
                        presets.append({"id": preset_id, "name": name})
                return sorted(presets, key=lambda p: p["id"])
        except Exception as e:
            logger.warning("Failed to fetch WLED presets from %s:%d: %s", host, port, e)
            return []

    async def trigger_test_effect(self, host: str, port: int, api_key: str | None) -> dict:
        """Flash white briefly as a visual confirmation the device is reachable."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Flash white for 2 seconds
                await client.post(
                    f"{self._wled_base_url(host, port)}/json/state",
                    headers=self._headers(api_key),
                    json={
                        "on": True,
                        "bri": 255,
                        "seg": [{"col": [[255, 255, 255]], "fx": 0}],
                        "nl": {"on": True, "dur": 2, "mode": 1, "tbri": 0},
                    },
                )
                return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


wled_service = WledService()
