"""HTTP client for communicating with Bambuddy backend."""

import asyncio
import logging
from collections import deque

import httpx

logger = logging.getLogger(__name__)

MAX_BUFFER_SIZE = 100


class APIClient:
    def __init__(self, backend_url: str, api_key: str):
        self._base = backend_url.rstrip("/") + "/api/v1/spoolbuddy"
        self._headers = {"X-API-Key": api_key} if api_key else {}
        self._client = httpx.AsyncClient(timeout=10.0, headers=self._headers)
        self._backoff = 1.0
        self._max_backoff = 30.0
        self._buffer: deque[dict] = deque(maxlen=MAX_BUFFER_SIZE)
        self._connected = False

    async def close(self):
        await self._client.aclose()

    async def _post(self, path: str, data: dict) -> dict | None:
        try:
            resp = await self._client.post(f"{self._base}{path}", json=data)
            resp.raise_for_status()
            self._backoff = 1.0
            self._connected = True
            return resp.json()
        except Exception as e:
            if self._connected:
                logger.warning("Backend connection lost: %s", e)
                self._connected = False
            self._buffer.append({"path": path, "data": data})
            return None

    async def _get(self, path: str) -> dict | None:
        try:
            resp = await self._client.get(f"{self._base}{path}")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("GET %s failed: %s", path, e)
            return None

    async def _flush_buffer(self):
        while self._buffer:
            item = self._buffer[0]
            try:
                resp = await self._client.post(f"{self._base}{item['path']}", json=item["data"])
                resp.raise_for_status()
                self._buffer.popleft()
            except Exception:
                break

    async def register_device(
        self,
        device_id: str,
        hostname: str,
        ip_address: str,
        firmware_version: str | None = None,
        has_nfc: bool = True,
        has_scale: bool = True,
        tare_offset: int = 0,
        calibration_factor: float = 1.0,
    ) -> dict | None:
        while True:
            result = await self._post(
                "/devices/register",
                {
                    "device_id": device_id,
                    "hostname": hostname,
                    "ip_address": ip_address,
                    "firmware_version": firmware_version,
                    "has_nfc": has_nfc,
                    "has_scale": has_scale,
                    "tare_offset": tare_offset,
                    "calibration_factor": calibration_factor,
                },
            )
            if result is not None:
                logger.info("Registered with backend as %s", device_id)
                return result
            logger.warning("Registration failed, retrying in %.0fs...", self._backoff)
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, self._max_backoff)

    async def heartbeat(
        self, device_id: str, nfc_ok: bool, scale_ok: bool, uptime_s: int, ip_address: str | None = None
    ) -> dict | None:
        result = await self._post(
            f"/devices/{device_id}/heartbeat",
            {
                "nfc_ok": nfc_ok,
                "scale_ok": scale_ok,
                "uptime_s": uptime_s,
                "ip_address": ip_address,
            },
        )
        if result and self._buffer:
            await self._flush_buffer()
        return result

    async def tag_scanned(
        self,
        device_id: str,
        tag_uid: str,
        tray_uuid: str | None = None,
        sak: int | None = None,
        tag_type: str | None = None,
    ) -> dict | None:
        return await self._post(
            "/nfc/tag-scanned",
            {
                "device_id": device_id,
                "tag_uid": tag_uid,
                "tray_uuid": tray_uuid,
                "sak": sak,
                "tag_type": tag_type,
            },
        )

    async def tag_removed(self, device_id: str, tag_uid: str) -> dict | None:
        return await self._post(
            "/nfc/tag-removed",
            {
                "device_id": device_id,
                "tag_uid": tag_uid,
            },
        )

    async def scale_reading(
        self, device_id: str, weight_grams: float, stable: bool, raw_adc: int | None = None
    ) -> dict | None:
        return await self._post(
            "/scale/reading",
            {
                "device_id": device_id,
                "weight_grams": weight_grams,
                "stable": stable,
                "raw_adc": raw_adc,
            },
        )
