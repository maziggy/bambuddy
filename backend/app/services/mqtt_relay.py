"""MQTT Relay Service for publishing BamBuddy events to external MQTT brokers.

This service enables integration with external automation systems like
Node-RED, Home Assistant, and other MQTT-based platforms.
"""

import asyncio
import json
import logging
import ssl
import threading
import time
from datetime import datetime
from typing import Any

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MQTTRelayService:
    """Publishes BamBuddy events to an external MQTT broker."""

    # Minimum interval between status updates per printer (seconds)
    STATUS_THROTTLE_SECONDS = 1.0

    def __init__(self):
        self.client: mqtt.Client | None = None
        self.enabled = False
        self.connected = False
        self.topic_prefix = "bambuddy"
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._broker = ""
        self._port = 1883
        self._last_printer_status: dict[int, float] = {}  # printer_id -> last publish timestamp
        self._smart_plug_service = None  # Lazy import to avoid circular dependency
        self._settings: dict = {}  # Store settings for smart plug service
        self._disconnection_event: threading.Event | None = None

    async def configure(self, settings: dict) -> bool:
        """Configure MQTT connection from settings.

        Returns True if connection was successful or MQTT is disabled.
        """
        self.enabled = settings.get("mqtt_enabled", False)
        self._settings = settings  # Store for smart plug service

        if not self.enabled:
            await self.disconnect()
            # Also configure smart plug service (will disable it)
            await self._configure_smart_plug_service(settings)
            logger.info("MQTT relay disabled")
            return True

        broker = settings.get("mqtt_broker", "")
        port = settings.get("mqtt_port", 1883)
        username = settings.get("mqtt_username", "")
        password = settings.get("mqtt_password", "")
        self.topic_prefix = settings.get("mqtt_topic_prefix", "bambuddy")
        use_tls = settings.get("mqtt_use_tls", False)

        if not broker:
            logger.warning("MQTT enabled but no broker configured")
            return False

        # Store for status endpoint
        self._broker = broker
        self._port = port

        # Disconnect existing connection if settings changed
        if self.client:
            await self.disconnect()

        # Create and connect client
        result = await self._connect(broker, port, username, password, use_tls)

        # Configure smart plug service with same settings
        await self._configure_smart_plug_service(settings)

        return result

    async def _configure_smart_plug_service(self, settings: dict):
        """Configure the MQTT smart plug service with the same broker settings."""
        try:
            if self._smart_plug_service is None:
                from backend.app.services.mqtt_smart_plug import mqtt_smart_plug_service

                self._smart_plug_service = mqtt_smart_plug_service

            await self._smart_plug_service.configure(settings)
        except Exception as e:
            logger.error("Failed to configure MQTT smart plug service: %s", e)

    @property
    def smart_plug_service(self):
        """Get the MQTT smart plug service instance."""
        if self._smart_plug_service is None:
            from backend.app.services.mqtt_smart_plug import mqtt_smart_plug_service

            self._smart_plug_service = mqtt_smart_plug_service
        return self._smart_plug_service

    async def _connect(self, broker: str, port: int, username: str, password: str, use_tls: bool) -> bool:
        """Establish MQTT connection."""
        try:
            # Create client with callback API version 2 (use MQTTv311 for broader compatibility)
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"bambuddy-{id(self)}",
                protocol=mqtt.MQTTv311,
            )

            # Set up callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect

            # Configure authentication
            if username:
                self.client.username_pw_set(username, password)

            # Configure TLS (allow self-signed certs for testing)
            if use_tls:
                self.client.tls_set(cert_reqs=ssl.CERT_NONE)
                self.client.tls_insecure_set(True)  # Allow self-signed certs

            # Run connect_async in thread pool with timeout to avoid blocking
            # on unreachable brokers (connect_async does synchronous socket creation)
            try:
                await asyncio.wait_for(asyncio.to_thread(self.client.connect_async, broker, port, 60), timeout=3.0)
            except TimeoutError:
                logger.warning("MQTT relay connection to %s:%s timed out", broker, port)
                return False

            self.client.loop_start()

            # Wait briefly for connection callback
            await asyncio.sleep(1.0)

            if self.connected:
                logger.info("MQTT relay connected to %s:%s", broker, port)
                # Publish online status
                self._publish_status("online")
                return True
            else:
                logger.warning("MQTT relay connection pending to %s:%s", broker, port)
                return True  # Connection is async, may succeed later

        except Exception as e:
            logger.error("MQTT relay connection failed: %s", e)
            self.connected = False
            return False

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict,
        reason_code: int | mqtt.ReasonCode,
        properties: mqtt.Properties | None = None,
    ):
        """Callback when connected to broker."""
        # Handle both MQTTv311 (int) and MQTTv5 (ReasonCode) return codes
        rc = reason_code if isinstance(reason_code, int) else reason_code.value
        if rc == 0:
            self.connected = True
            logger.info("MQTT relay connected successfully")
            # Publish online status
            self._publish_status("online")
        else:
            self.connected = False
            logger.error("MQTT relay connection failed: %s", reason_code)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags_or_rc: dict | int | mqtt.ReasonCode,
        reason_code: int | mqtt.ReasonCode | None = None,
        properties: mqtt.Properties | None = None,
    ):
        """Callback when disconnected from broker."""
        self.connected = False
        # Handle both MQTTv311 (rc as 3rd param) and MQTTv5 (flags, rc, props)
        rc = reason_code if reason_code is not None else flags_or_rc
        rc_val = rc if isinstance(rc, int) else getattr(rc, "value", 0)
        if rc_val != 0:
            logger.warning("MQTT relay disconnected: %s", rc)
        else:
            logger.info("MQTT relay disconnected cleanly")
        if self._disconnection_event:
            self._disconnection_event.set()

    async def disconnect(self, timeout: float = 0):
        """Disconnect from MQTT broker."""
        if self.client:
            try:
                # Publish offline status before disconnecting
                self._publish_status("offline")
                self._disconnection_event = threading.Event()
                self.client.disconnect()
                await asyncio.to_thread(self._disconnection_event.wait, timeout=timeout)
                self.client.loop_stop()
            except Exception as e:
                logger.debug("MQTT disconnect error (ignored): %s", e)
            finally:
                self.client = None
                self.connected = False

    def _publish_status(self, status: str):
        """Publish BamBuddy status (online/offline)."""
        self._publish(
            f"{self.topic_prefix}/status",
            {"status": status, "timestamp": datetime.utcnow().isoformat()},
            retain=True,
        )

    def _publish(self, topic: str, payload: dict, retain: bool = False):
        """Publish message to MQTT broker."""
        if not self.client or not self.connected:
            return

        try:
            with self._lock:
                self.client.publish(topic, json.dumps(payload, default=str), qos=1, retain=retain)
        except Exception as e:
            logger.debug("MQTT publish error: %s", e)

    def get_status(self) -> dict:
        """Get current MQTT relay status for API."""
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "broker": self._broker if self.enabled else "",
            "port": self._port if self.enabled else 0,
            "topic_prefix": self.topic_prefix,
        }

    # =========================================================================
    # Printer Events
    # =========================================================================

    async def on_printer_status(self, printer_id: int, state: Any, printer_name: str, printer_serial: str):
        """Publish printer status change (throttled to 1 update/sec per printer)."""
        if not self.enabled or not self.connected:
            return

        # Throttle status updates to avoid flooding MQTT broker
        now = time.time()
        last_publish = self._last_printer_status.get(printer_id, 0)
        if now - last_publish < self.STATUS_THROTTLE_SECONDS:
            return  # Skip this update, too soon since last publish
        self._last_printer_status[printer_id] = now

        # Build status payload from PrinterState
        payload = {
            "printer_id": printer_id,
            "printer_name": printer_name,
            "printer_serial": printer_serial,
            "timestamp": datetime.utcnow().isoformat(),
            "connected": state.connected,
            "state": state.state,
            "progress": state.progress,
            "remaining_time": state.remaining_time,
            "layer_num": state.layer_num,
            "total_layers": state.total_layers,
            "current_print": state.current_print,
            "subtask_name": state.subtask_name,
            "gcode_file": state.gcode_file,
            "temperatures": state.temperatures,
            "wifi_signal": state.wifi_signal,
            "chamber_light": state.chamber_light,
            "speed_level": state.speed_level,
            "cooling_fan_speed": state.cooling_fan_speed,
            "big_fan1_speed": state.big_fan1_speed,
            "big_fan2_speed": state.big_fan2_speed,
            "heatbreak_fan_speed": state.heatbreak_fan_speed,
        }

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/status",
            payload,
            retain=True,
        )

    async def on_printer_online(self, printer_id: int, printer_name: str, printer_serial: str):
        """Publish printer came online event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/online",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    async def on_printer_offline(self, printer_id: int, printer_name: str, printer_serial: str):
        """Publish printer went offline event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/offline",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    async def on_print_start(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        filename: str,
        subtask_name: str,
    ):
        """Publish print started event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/print/started",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "filename": filename,
                "subtask_name": subtask_name,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    async def on_print_complete(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        filename: str,
        subtask_name: str,
        status: str,
    ):
        """Publish print completed event."""
        if not self.enabled or not self.connected:
            return

        # Determine topic based on status
        if status == "completed":
            topic = f"{self.topic_prefix}/printers/{printer_serial}/print/completed"
        else:
            topic = f"{self.topic_prefix}/printers/{printer_serial}/print/failed"

        self._publish(
            topic,
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "filename": filename,
                "subtask_name": subtask_name,
                "status": status,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    async def on_ams_change(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        ams_data: list,
    ):
        """Publish AMS filament change event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/ams/changed",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "ams_units": ams_data,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    async def on_printer_error(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        errors: list,
    ):
        """Publish printer HMS error event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/error",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "errors": errors,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    # =========================================================================
    # Print Queue Events
    # =========================================================================

    async def on_queue_job_added(
        self,
        job_id: int,
        filename: str,
        printer_id: int | None,
        printer_name: str | None,
    ):
        """Publish job added to queue event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/queue/job_added",
            {
                "job_id": job_id,
                "filename": filename,
                "printer_id": printer_id,
                "printer_name": printer_name,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    async def on_queue_job_started(
        self,
        job_id: int,
        filename: str,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
    ):
        """Publish queued job started printing event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/queue/job_started",
            {
                "job_id": job_id,
                "filename": filename,
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    async def on_queue_job_completed(
        self,
        job_id: int,
        filename: str,
        printer_id: int,
        printer_name: str,
        status: str,
    ):
        """Publish queued job finished event."""
        if not self.enabled or not self.connected:
            return

        topic = (
            f"{self.topic_prefix}/queue/job_completed"
            if status == "completed"
            else f"{self.topic_prefix}/queue/job_failed"
        )

        self._publish(
            topic,
            {
                "job_id": job_id,
                "filename": filename,
                "printer_id": printer_id,
                "printer_name": printer_name,
                "status": status,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    # =========================================================================
    # Maintenance Events
    # =========================================================================

    async def on_maintenance_alert(
        self,
        printer_id: int,
        printer_name: str,
        maintenance_type: str,
        current_value: float,
        threshold: float,
    ):
        """Publish maintenance alert triggered event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/maintenance/alert",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "maintenance_type": maintenance_type,
                "current_value": current_value,
                "threshold": threshold,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    async def on_maintenance_acknowledged(
        self,
        printer_id: int,
        printer_name: str,
        maintenance_type: str,
    ):
        """Publish maintenance alert acknowledged event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/maintenance/acknowledged",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "maintenance_type": maintenance_type,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    async def on_maintenance_reset(
        self,
        printer_id: int,
        printer_name: str,
        maintenance_type: str,
    ):
        """Publish maintenance counter reset event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/maintenance/reset",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "maintenance_type": maintenance_type,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    # =========================================================================
    # Archive Events
    # =========================================================================

    async def on_archive_created(
        self,
        archive_id: int,
        print_name: str,
        printer_name: str,
        status: str,
    ):
        """Publish print archived event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/archive/created",
            {
                "archive_id": archive_id,
                "print_name": print_name,
                "printer_name": printer_name,
                "status": status,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    async def on_archive_updated(
        self,
        archive_id: int,
        print_name: str,
        status: str,
    ):
        """Publish archive record updated event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/archive/updated",
            {
                "archive_id": archive_id,
                "print_name": print_name,
                "status": status,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    # =========================================================================
    # Filament/Spoolman Events
    # =========================================================================

    async def on_filament_low(
        self,
        spool_id: int,
        spool_name: str,
        remaining_weight: float,
        remaining_percent: float,
    ):
        """Publish filament inventory low event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/filament/low",
            {
                "spool_id": spool_id,
                "spool_name": spool_name,
                "remaining_weight": remaining_weight,
                "remaining_percent": remaining_percent,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    # =========================================================================
    # Smart Plug Events
    # =========================================================================

    async def on_smart_plug_state(
        self,
        plug_id: int,
        plug_name: str,
        state: str,
        printer_id: int | None = None,
        printer_name: str | None = None,
    ):
        """Publish smart plug state change event."""
        if not self.enabled or not self.connected:
            return

        topic = f"{self.topic_prefix}/smart_plugs/on" if state == "on" else f"{self.topic_prefix}/smart_plugs/off"

        self._publish(
            topic,
            {
                "plug_id": plug_id,
                "plug_name": plug_name,
                "state": state,
                "printer_id": printer_id,
                "printer_name": printer_name,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    async def on_smart_plug_energy(
        self,
        plug_id: int,
        plug_name: str,
        power: float,
        energy_today: float,
        energy_total: float,
    ):
        """Publish smart plug energy update event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/smart_plugs/energy",
            {
                "plug_id": plug_id,
                "plug_name": plug_name,
                "power_watts": power,
                "energy_today_kwh": energy_today,
                "energy_total_kwh": energy_total,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )


# Global instance
mqtt_relay = MQTTRelayService()
