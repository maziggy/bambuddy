"""FlashForge local API monitoring client.

This is intentionally monitoring-first. It maps the FlashForge HTTP `/detail`
response onto Bambuddy's existing PrinterState shape so the dashboard,
WebSocket updates, and notification plumbing can observe non-Bambu printers
without enabling print dispatch or control paths yet.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from backend.app.services.bambu_mqtt import PrinterState

logger = logging.getLogger(__name__)

DEFAULT_FLASHFORGE_PORT = 8898
FLASHFORGE_POLL_INTERVAL_SECONDS = 10.0
FLASHFORGE_STALE_AFTER_SECONDS = 45.0


def is_flashforge_model(model: str | None) -> bool:
    """Return True when a printer model should use the FlashForge local API."""
    if not model:
        return False
    normalized = model.strip().upper()
    return "FLASHFORGE" in normalized or "CREATOR 5 PRO" in normalized


def _first_number(values: Any, default: float = 0.0) -> float:
    if isinstance(values, list) and values:
        return _number(values[0], default)
    return _number(values, default)


def _max_number(values: Any, default: float = 0.0) -> float:
    if not isinstance(values, list) or not values:
        return _number(values, default)
    parsed = [_number(value, default) for value in values]
    return max(parsed) if parsed else default


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalize_state(status: Any) -> str:
    """Map FlashForge status strings onto Bambuddy's broad state buckets."""
    raw = str(status or "").strip().lower()
    if raw in {"building", "printing", "print", "running"}:
        return "RUNNING"
    if raw in {"pause", "paused", "pausing"}:
        return "PAUSE"
    if raw in {"finish", "finished", "complete", "completed", "done"}:
        return "FINISH"
    if raw in {"error", "failed", "failure", "fault"}:
        return "FAILED"
    if raw in {"ready", "idle", "standby", "completed"}:
        return "IDLE"
    if raw in {"loading", "heating", "preheating", "preparing"}:
        return "PREPARE"
    return raw.upper() if raw else "unknown"


def _slot_to_tray(slot: dict[str, Any], index: int) -> dict[str, Any]:
    color = str(slot.get("materialColor") or slot.get("color") or "808080").replace("#", "")
    if len(color) == 6:
        color = f"{color}FF"
    return {
        "id": index,
        "tray_color": color,
        "tray_type": slot.get("materialName") or slot.get("materialType") or slot.get("type") or "",
        "tray_sub_brands": "",
        "tray_id_name": "",
        "tray_info_idx": "",
        "remain": 0,
        "state": 10 if slot.get("hasFilament", bool(slot)) else 9,
    }


@dataclass
class FlashForgeLocalClient:
    """Small polling client for FlashForge's LAN-only HTTP API."""

    ip_address: str
    serial_number: str
    access_code: str
    model: str | None = None
    on_state_change: Callable[[PrinterState], None] | None = None
    on_print_start: Callable[[dict], None] | None = None
    on_print_complete: Callable[[dict], None] | None = None

    def __post_init__(self) -> None:
        self.state = PrinterState(connected=False, state="unknown")
        self.logging_enabled = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_seen = 0.0
        self._last_state = "unknown"

    def connect(self) -> None:
        """Start polling the printer."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name=f"flashforge-{self.serial_number}",
            daemon=True,
        )
        self._thread.start()

    def disconnect(self, timeout: float = 0) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout or 2.0)
        self.state.connected = False

    def check_staleness(self) -> bool:
        if self._last_seen and time.time() - self._last_seen > FLASHFORGE_STALE_AFTER_SECONDS:
            self.state.connected = False
        return self.state.connected

    def request_status_update(self) -> bool:
        detail = self._fetch_detail()
        if detail is None:
            return False
        self._apply_detail(detail)
        return True

    def start_print(self, *args: Any, **kwargs: Any) -> bool:
        logger.warning("FlashForge start_print is not implemented yet")
        return False

    def stop_print(self) -> bool:
        logger.warning("FlashForge stop_print is not implemented yet")
        return False

    def pause_print(self) -> bool:
        logger.warning("FlashForge pause_print is not implemented yet")
        return False

    def resume_print(self) -> bool:
        logger.warning("FlashForge resume_print is not implemented yet")
        return False

    def clear_hms_errors(self) -> bool:
        return False

    def enable_logging(self, enabled: bool = True) -> None:
        self.logging_enabled = enabled

    def get_logs(self) -> list:
        return []

    def clear_logs(self) -> None:
        return None

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            detail = self._fetch_detail()
            if detail is not None:
                self._apply_detail(detail)
            else:
                self.check_staleness()
                if self.on_state_change:
                    self.on_state_change(self.state)
            self._stop_event.wait(FLASHFORGE_POLL_INTERVAL_SECONDS)

    def _fetch_detail(self) -> dict[str, Any] | None:
        body = json.dumps(
            {"serialNumber": self.serial_number, "checkCode": self.access_code}
        ).encode()
        request = Request(
            f"http://{self.ip_address}:{DEFAULT_FLASHFORGE_PORT}/detail",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode())
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            logger.debug("FlashForge detail request failed for %s: %s", self.ip_address, exc)
            return None
        if payload.get("code") != 0:
            logger.warning("FlashForge detail request returned non-zero code for %s: %s", self.ip_address, payload)
            return None
        detail = payload.get("detail")
        return detail if isinstance(detail, dict) else None

    def _apply_detail(self, detail: dict[str, Any]) -> None:
        now = time.time()
        raw_status = detail.get("status")
        mapped_state = _normalize_state(raw_status)
        previous_state = self.state.state

        self.state.connected = True
        self.state.state = mapped_state
        self.state.current_print = (
            detail.get("printFileName")
            or detail.get("currentPrintFile")
            or detail.get("fileName")
        )
        self.state.subtask_name = self.state.current_print
        self.state.gcode_file = self.state.current_print
        progress = _number(detail.get("printProgress", detail.get("progress")), 0.0)
        self.state.progress = progress * 100 if 0 <= progress <= 1 else progress
        self.state.remaining_time = _int(detail.get("estimatedTime", detail.get("remainingTime")), 0)
        self.state.layer_num = _int(detail.get("printLayer", detail.get("currentLayer")), 0)
        self.state.total_layers = _int(detail.get("targetPrintLayer", detail.get("totalLayer")), 0)
        self.state.firmware_version = str(detail.get("firmwareVersion") or "")
        self.state.ipcam = bool(detail.get("camera") or detail.get("cameraStreamUrl"))
        self.state.cooling_fan_speed = _int(detail.get("coolingFanSpeed"), 0)
        self.state.big_fan2_speed = _int(detail.get("chamberFanSpeed"), 0)
        self.state.chamber_light = str(detail.get("lightStatus") or "").lower() == "open"
        self.state.door_open = str(detail.get("doorStatus") or "").lower() == "open"
        self.state.raw_data = {
            **detail,
            "device_model": detail.get("model") or self.model or "FlashForge",
            "vendor": "flashforge",
        }

        nozzle_temp = _max_number(detail.get("nozzleTemps"), _number(detail.get("nozzleTemp"), 0.0))
        nozzle_target = _max_number(
            detail.get("nozzleTargetTemps"),
            _number(detail.get("nozzleTargetTemp"), 0.0),
        )
        bed_temp = _number(detail.get("platTemp"), _number(detail.get("bedTemp"), 0.0))
        bed_target = _number(detail.get("platTargetTemp"), _number(detail.get("bedTargetTemp"), 0.0))
        chamber_temp = _number(detail.get("chamberTemp"), 0.0)

        self.state.temperatures = {
            "nozzle": nozzle_temp,
            "nozzle_target": nozzle_target,
            "bed": bed_temp,
            "bed_target": bed_target,
            "chamber": chamber_temp,
        }

        station = detail.get("matlStationInfo") if isinstance(detail.get("matlStationInfo"), dict) else {}
        slots = station.get("slotInfos") if isinstance(station.get("slotInfos"), list) else []
        if slots:
            self.state.raw_data["ams"] = [
                {
                    "id": 0,
                    "tray": [_slot_to_tray(slot, idx) for idx, slot in enumerate(slots)],
                    "sn": "",
                    "module_type": "flashforge_ifs",
                }
            ]
        else:
            self.state.raw_data["ams"] = []

        self._last_seen = now
        if previous_state != mapped_state:
            if self.on_print_start and mapped_state == "RUNNING" and previous_state not in {"RUNNING", "PAUSE"}:
                self.on_print_start({"filename": self.state.gcode_file})
            if self.on_print_complete and mapped_state in {"FINISH", "FAILED"} and previous_state in {"RUNNING", "PAUSE"}:
                self.on_print_complete(
                    {
                        "filename": self.state.gcode_file,
                        "status": "completed" if mapped_state == "FINISH" else "failed",
                    }
                )
            if self.on_state_change:
                self.on_state_change(self.state)
        elif self.on_state_change:
            self.on_state_change(self.state)


async def probe_flashforge_connection(ip_address: str, serial_number: str, access_code: str) -> dict:
    """Probe a FlashForge printer once using the LAN HTTP API."""
    client = FlashForgeLocalClient(ip_address, serial_number, access_code)
    detail = await asyncio.to_thread(client._fetch_detail)
    if detail is None:
        return {"success": False, "state": None, "model": None}
    return {
        "success": True,
        "state": _normalize_state(detail.get("status")),
        "model": detail.get("model") or detail.get("name") or "FlashForge",
    }
