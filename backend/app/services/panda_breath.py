"""BIQU Panda Breath chamber heater over MQTT (#1466).

Firmware 1.0.4 added a Home Assistant binding: the device connects to an MQTT
broker and, for a device id ``<id>``, uses

    panda_breath/<id>/state         JSON document with every value
    panda_breath/<id>/availability  "online" / "offline"
    panda_breath/<id>/command       JSON object with the one value to change

The keys and values below are the ones the firmware's own Home Assistant
discovery messages declare (read from the 1.0.4 image): ``work_on`` and
``drying_running`` are "ON"/"OFF", ``mode`` is one of MODES, ``target_temp``
is 0-60 °C. The device is bound to one printer and reports that printer's
serial number as ``printer_sn``, which is how it is matched to a Bambuddy
printer.

Bambuddy reuses the broker of the MQTT relay settings, like MQTT smart plugs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from backend.app.utils.paho_teardown import retire_paho_client

logger = logging.getLogger(__name__)

TOPIC_PREFIX = "panda_breath"
MODES = ("auto mode", "power on", "filament drying")
DRYING_MODES = ("pla", "petg", "custom")
MAX_TARGET_TEMP_C = 60


@dataclass
class PandaBreathDevice:
    """The last values one device reported."""

    device_id: str
    online: bool | None = None
    chamber_temp: float | None = None
    target_temp: float | None = None
    work_on: bool | None = None
    mode: str | None = None
    filament_drying_mode: str | None = None
    drying_running: bool | None = None
    drying_remaining_min: float | None = None
    printer_sn: str | None = None
    last_seen: datetime | None = None

    def to_dict(self, broker_up: bool = True) -> dict[str, Any]:
        """The values the printer card shows (no printer serial number).

        Without a broker connection nothing can reach the device and its last
        values are stale, so it counts as offline then.
        """
        return {
            "device_id": self.device_id,
            "online": self.online if broker_up else False,
            "chamber_temp": self.chamber_temp,
            "target_temp": self.target_temp,
            "work_on": self.work_on,
            "mode": self.mode,
            "filament_drying_mode": self.filament_drying_mode,
            "drying_running": self.drying_running,
            "drying_remaining_min": self.drying_remaining_min,
        }


def _on_off(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in ("ON", "TRUE", "1"):
        return True
    if text in ("OFF", "FALSE", "0"):
        return False
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _same_serial(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a.strip().upper() == b.strip().upper()


class PandaBreathService:
    """Follows Panda Breath devices on the shared broker and sends them commands."""

    def __init__(self) -> None:
        self.client: mqtt.Client | None = None
        self.connected = False
        self.enabled = False
        self.devices: dict[str, PandaBreathDevice] = {}
        self._lock = threading.Lock()
        self._disconnection_event: threading.Event | None = None
        self._broker = ""
        self._port = 1883
        self._username = ""
        self._password = ""
        self._use_tls = False

    # -- connection -------------------------------------------------------

    async def configure(self, settings: dict) -> None:
        """Follow the relay's broker while both MQTT and Panda Breath are on."""
        enabled = bool(settings.get("mqtt_enabled")) and bool(settings.get("panda_breath_enabled"))
        broker = settings.get("mqtt_broker") or ""
        if not enabled or not broker:
            self.enabled = False
            await self.disconnect()
            self._forget_devices()
            return

        port = int(settings.get("mqtt_port") or 1883)
        username = settings.get("mqtt_username") or ""
        password = settings.get("mqtt_password") or ""
        use_tls = bool(settings.get("mqtt_use_tls"))
        changed = (broker, port, username, password, use_tls) != (
            self._broker,
            self._port,
            self._username,
            self._password,
            self._use_tls,
        )
        self._broker, self._port, self._username, self._password, self._use_tls = (
            broker,
            port,
            username,
            password,
            use_tls,
        )
        self.enabled = True
        if changed and self.client:
            await self.disconnect()
            self._forget_devices()
        if not self.client:
            await self._connect()

    def _forget_devices(self) -> None:
        """Drop every device and refresh the cards that showed one."""
        with self._lock:
            serials = {d.printer_sn for d in self.devices.values() if d.printer_sn}
            self.devices.clear()
        self._broadcast(serials)

    def _broadcast(self, serials: set[str]) -> None:
        if not serials:
            return
        from backend.app.services.printer_manager import printer_manager

        for serial in serials:
            printer_id = printer_manager.printer_id_for_serial(serial)
            if printer_id is not None:
                printer_manager.schedule_status_broadcast(printer_id)

    def _bound_serials(self) -> set[str]:
        with self._lock:
            return {d.printer_sn for d in self.devices.values() if d.printer_sn}

    async def _connect(self) -> None:
        try:
            client = mqtt.Client(
                callback_api_version=CallbackAPIVersion.VERSION2,
                client_id=f"bambuddy-pandabreath-{id(self)}",
                protocol=mqtt.MQTTv311,
            )
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            if self._username:
                client.username_pw_set(self._username, self._password)
            if self._use_tls:
                client.tls_set(cert_reqs=ssl.CERT_NONE)
                client.tls_insecure_set(True)
            self.client = client
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(client.connect_async, self._broker, self._port, 60),
                    timeout=3.0,
                )
            except TimeoutError:
                logger.warning("Panda Breath: connection to %s:%s timed out", self._broker, self._port)
            client.loop_start()
        except Exception as e:
            logger.error("Panda Breath: MQTT connection failed: %s", e)
            self.connected = False

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        rc = reason_code if isinstance(reason_code, int) else reason_code.value
        self.connected = rc == 0
        if self.connected:
            client.subscribe(f"{TOPIC_PREFIX}/+/state", qos=1)
            client.subscribe(f"{TOPIC_PREFIX}/+/availability", qos=1)
            logger.info("Panda Breath: listening on %s:%s", self._broker, self._port)
        else:
            logger.warning("Panda Breath: broker refused the connection: %s", reason_code)

    def _on_disconnect(self, client, userdata, flags_or_rc, reason_code=None, properties=None) -> None:
        was_connected = self.connected
        self.connected = False
        if self._disconnection_event:
            self._disconnection_event.set()
        if was_connected:
            # The cards now show their devices offline.
            self._broadcast(self._bound_serials())

    async def disconnect(self, timeout: float = 0) -> None:
        if not self.client:
            return
        try:
            self._disconnection_event = threading.Event()
            self.client.disconnect()
            await asyncio.to_thread(self._disconnection_event.wait, timeout=timeout)
            retire_paho_client(self.client, "panda-breath")
        except Exception as e:
            logger.debug("Panda Breath disconnect error (ignored): %s", e)
        finally:
            self.client = None
            self.connected = False

    # -- incoming ---------------------------------------------------------

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            return
        printer_ids = self.apply_message(msg.topic or "", payload)
        if printer_ids:
            from backend.app.services.printer_manager import printer_manager

            for printer_id in printer_ids:
                printer_manager.schedule_status_broadcast(printer_id)

    def apply_message(self, topic: str, payload: str) -> set[int]:
        """Take one message in; return the printers whose card changed."""
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != TOPIC_PREFIX or not parts[1]:
            return set()
        device_id, kind = parts[1], parts[2]
        with self._lock:
            device = self.devices.get(device_id) or PandaBreathDevice(device_id=device_id)
            before = (device.to_dict(), device.printer_sn)
            if kind == "availability":
                device.online = payload.strip().lower() == "online"
            elif kind == "state":
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    return set()
                if not isinstance(data, dict):
                    return set()
                self._apply_state(device, data)
                device.online = True if device.online is None else device.online
            else:
                return set()
            device.last_seen = datetime.now(timezone.utc)
            self.devices[device_id] = device
            if (device.to_dict(), device.printer_sn) == before:
                return set()
            serials = {s for s in (before[1], device.printer_sn) if s}
        return {pid for pid in (self._printer_id_for_serial(s) for s in serials) if pid is not None}

    @staticmethod
    def _apply_state(device: PandaBreathDevice, data: dict) -> None:
        if "chamber_temp" in data:
            device.chamber_temp = _number(data["chamber_temp"])
        if "target_temp" in data:
            device.target_temp = _number(data["target_temp"])
        if "work_on" in data:
            device.work_on = _on_off(data["work_on"])
        if "mode" in data:
            device.mode = _text(data["mode"])
        if "filament_drying_mode" in data:
            device.filament_drying_mode = _text(data["filament_drying_mode"])
        if "drying_running" in data:
            device.drying_running = _on_off(data["drying_running"])
        if "drying_remaining_min" in data:
            device.drying_remaining_min = _number(data["drying_remaining_min"])
        if "printer_sn" in data:
            device.printer_sn = _text(data["printer_sn"])

    @staticmethod
    def _printer_id_for_serial(serial: str) -> int | None:
        from backend.app.services.printer_manager import printer_manager

        return printer_manager.printer_id_for_serial(serial)

    # -- lookups ----------------------------------------------------------

    def device_for_serial(self, serial: str | None) -> PandaBreathDevice | None:
        """The device bound to the printer with this serial number."""
        if not self.enabled or not serial:
            return None
        with self._lock:
            matches = [d for d in self.devices.values() if _same_serial(d.printer_sn, serial)]
        if not matches:
            return None
        # Two devices claiming one printer: the one heard from last.
        return max(matches, key=lambda d: d.last_seen or datetime.min.replace(tzinfo=timezone.utc))

    def state_for_serial(self, serial: str | None) -> dict[str, Any] | None:
        """What the card of the printer with this serial shows, or None."""
        device = self.device_for_serial(serial)
        return device.to_dict(self.connected) if device else None

    def state_for_printer(self, printer_id: int | None) -> dict[str, Any] | None:
        """The same for a connected printer, known by id (WebSocket status)."""
        if printer_id is None or not self.enabled:
            return None
        from backend.app.services.printer_manager import printer_manager

        info = printer_manager.get_printer(printer_id)
        return self.state_for_serial(info.serial_number if info else None)

    def get_status(self, printers: dict[str, tuple[int, str]]) -> dict[str, Any]:
        """For Settings: the connection and every device heard from.

        ``printers`` maps an upper-case serial number to (id, name) for the
        printers the caller may see; a device bound to any other printer is
        listed without naming it.
        """
        with self._lock:
            devices = list(self.devices.values())
        rows = []
        for device in sorted(devices, key=lambda d: d.device_id):
            printer = printers.get((device.printer_sn or "").strip().upper())
            rows.append(
                {
                    **device.to_dict(self.connected),
                    "bound": bool(device.printer_sn),
                    "printer_id": printer[0] if printer else None,
                    "printer_name": printer[1] if printer else None,
                }
            )
        return {"enabled": self.enabled, "connected": self.connected, "devices": rows}

    # -- outgoing ---------------------------------------------------------

    def send_command(self, device_id: str, key: str, value: Any) -> bool:
        """Publish one value change; False when the broker is not reachable."""
        if not self.client or not self.connected:
            return False
        topic = f"{TOPIC_PREFIX}/{device_id}/command"
        info = self.client.publish(topic, json.dumps({key: value}), qos=1, retain=False)
        return info.rc == mqtt.MQTT_ERR_SUCCESS


panda_breath_service = PandaBreathService()
